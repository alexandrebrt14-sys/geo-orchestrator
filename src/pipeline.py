"""Execution engine — runs tasks in dependency order with parallelism.

Performs topological sorting, launches independent tasks concurrently,
handles full fallback chain iteration, quality gates, context optimization,
connection pooling, timeout tiers, early termination, and robust checkpointing.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    CONTEXT_SUMMARIZE_THRESHOLD,
    DEFAULT_TIMEOUT,
    LLM_CONFIGS,
    LLMConfig,
    OUTPUT_DIR,
    Provider,
    TIMEOUT_BY_TASK_TYPE,
)
from .connection_pool import ConnectionPool
from .cost_tracker import CostTracker
from .finops import BudgetExceededError, get_finops
from .llm_client import LLMClient
from .models import Plan, Task, TaskResult, TaskStatus
from .rate_limiter import RateLimiter
from .router import Router
from .sanitize import sanitize_path
from .tracer import TraceManager

logger = logging.getLogger(__name__)


class Pipeline:
    """Execute a Plan respecting task dependencies and maximizing parallelism.

    Features:
    - Wave-based parallel execution (groups of independent tasks)
    - Connection pooling (one httpx.AsyncClient per provider, reused)
    - Timeout tiers (per task type: research 45s, writing 60s, analysis 20s)
    - Full fallback chain iteration (not just primary + one fallback)
    - Early termination (stop chain when quality gate passes)
    - Checkpoint/resume for interrupted executions
    - Quality gates with automatic retry on next LLM in chain
    - Context window optimization (summarize long dependency outputs)
    - Per-task result persistence
    """

    def __init__(self, plan: Plan, router: Router) -> None:
        self.plan = plan
        self.router = router
        self.cost_tracker = CostTracker()
        self._results: dict[str, TaskResult] = {}
        self._task_map: dict[str, Task] = {t.id: t for t in plan.tasks}
        self._execution_start_ms: int = 0
        self._wave_timings: list[dict] = []
        self._pool = ConnectionPool.get_instance()

        # Directories for persistence
        self._results_dir = OUTPUT_DIR / ".results"
        self._checkpoint_path = OUTPUT_DIR / ".checkpoint.json"
        self._results_dir.mkdir(parents=True, exist_ok=True)

        # Estatísticas por LLM para exibição em tempo real
        # Inclui todos os LLMs em LLM_CONFIGS (5 canonicos + tiers internos Claude)
        self._llm_stats: dict[str, dict] = {
            name: {"assigned": 0, "completed": 0, "tokens": 0, "cost": 0.0, "status": "idle"}
            for name in LLM_CONFIGS
        }

    # ==================================================================
    # Status em tempo real
    # ==================================================================

    def _print_status(self) -> None:
        """Imprime tabela de status dos LLMs em tempo real."""
        col_llm = 12
        col_tarefas = 7
        col_comp = 9
        col_tokens = 6
        col_custo = 7
        col_status = 6

        def fmt_tokens(n: int) -> str:
            if n >= 1000:
                return f"{n / 1000:.1f}k"
            return str(n)

        def fmt_bar(completed: int, assigned: int, width: int = 4) -> str:
            if assigned == 0:
                return "░" * width
            filled = round(completed / assigned * width)
            return "█" * filled + "░" * (width - filled)

        sep_top    = "┌" + "─" * (col_llm + 2) + "┬" + "─" * (col_tarefas + 2) + "┬" + "─" * (col_comp + 2) + "┬" + "─" * (col_tokens + 2) + "┬" + "─" * (col_custo + 2) + "┬" + "─" * (col_status + 2) + "┐"
        sep_header = "├" + "─" * (col_llm + 2) + "┼" + "─" * (col_tarefas + 2) + "┼" + "─" * (col_comp + 2) + "┼" + "─" * (col_tokens + 2) + "┼" + "─" * (col_custo + 2) + "┼" + "─" * (col_status + 2) + "┤"
        sep_bot    = "└" + "─" * (col_llm + 2) + "┴" + "─" * (col_tarefas + 2) + "┴" + "─" * (col_comp + 2) + "┴" + "─" * (col_tokens + 2) + "┴" + "─" * (col_custo + 2) + "┴" + "─" * (col_status + 2) + "┘"

        title = " Status dos LLMs "
        total_width = len(sep_top) - 2
        title_padded = title.center(total_width, "─")
        title_line = "┌" + title_padded + "┐"

        header = (
            "│ "
            + "LLM".ljust(col_llm)
            + " │ "
            + "Tarefas".center(col_tarefas)
            + " │ "
            + "Completas".center(col_comp)
            + " │ "
            + "Tokens".center(col_tokens)
            + " │ "
            + "Custo".center(col_custo)
            + " │ "
            + "Status".center(col_status)
            + " │"
        )

        print(title_line)
        print(header)
        print(sep_header)

        for name, stats in self._llm_stats.items():
            assigned  = stats["assigned"]
            completed = stats["completed"]
            tokens    = stats["tokens"]
            cost      = stats["cost"]
            bar       = fmt_bar(completed, assigned)

            row = (
                "│ "
                + name.ljust(col_llm)
                + " │ "
                + str(assigned).center(col_tarefas)
                + " │ "
                + str(completed).center(col_comp)
                + " │ "
                + fmt_tokens(tokens).center(col_tokens)
                + " │ "
                + f"${cost:.3f}".center(col_custo)
                + " │ "
                + bar.center(col_status)
                + " │"
            )
            print(row)

        print(sep_bot)

    # ==================================================================
    # Main execution
    # ==================================================================

    async def execute(self) -> dict[str, TaskResult]:
        """Run all tasks in wave-based topological order, parallelizing
        independent tasks within each wave.

        Returns a dict mapping task_id -> TaskResult.
        """
        tracer = TraceManager.get_instance()
        pipeline_span = tracer.start_span(
            "pipeline.execute",
            demand=self.plan.demand,
            task_count=len(self.plan.tasks),
        )

        self._execution_start_ms = int(time.perf_counter_ns() / 1_000_000)

        # FinOps: pre-execution budget check
        finops = get_finops()
        llm_names = []
        for task in self.plan.tasks:
            try:
                # v2.0: use smart_route if available, fallback to force_all_models_route
                if hasattr(self.router, 'smart_route') and hasattr(self.router, '_demand_tier'):
                    cfg = self.router.smart_route(task, self.router._demand_tier)
                else:
                    cfg = self.router.force_all_models_route(task)
                llm_names.append(cfg.name)
            except RuntimeError:
                pass
        try:
            finops.pre_execution_check(len(self.plan.tasks), llm_names or None)
        except BudgetExceededError as exc:
            logger.error("FinOps bloqueou a execucao: %s", exc)
            pipeline_span.set_error(exc)
            tracer.finish_span(pipeline_span)
            raise

        # Sprint 3 (2026-04-07): zerar contadores APOS pre_execution_check.
        # O pre_check chama smart_route/route para estimar custo, e essas
        # chamadas registravam assignment. Agora a contagem real comeca aqui,
        # quando a execucao das waves de fato vai acontecer.
        for k in self.router._session_usage:
            self.router._session_usage[k] = 0

        # Check for existing checkpoint
        resumed_ids = self._load_checkpoint()

        # Compute execution waves
        waves = self._compute_waves(self.plan.tasks)

        for wave_idx, wave_tasks in enumerate(waves):
            wave_ids = [t.id for t in wave_tasks]
            # Filter out already-completed tasks (from checkpoint resume)
            pending_ids = [tid for tid in wave_ids if tid not in resumed_ids]

            if not pending_ids:
                logger.info("Wave %d: all tasks already completed (resumed).", wave_idx + 1)
                continue

            # Start wave span
            wave_span = tracer.start_span(
                f"wave.{wave_idx + 1}",
                task_count=len(pending_ids),
                task_ids=pending_ids,
                task_types=[self._task_map[tid].type for tid in pending_ids],
            )

            wave_start = time.perf_counter_ns()
            logger.info(
                "Wave %d (%d tasks): %s",
                wave_idx + 1,
                len(pending_ids),
                ", ".join(f"{self._task_map[tid].type}" for tid in pending_ids),
            )
            # Bridge status: report model usage between waves
            logger.info(
                "Status dos modelos:\n%s", self.router.get_model_status_table()
            )
            unused = self.router.get_unused_models()
            if unused:
                logger.info(
                    "BRIDGE: %d modelos ainda nao usados: %s — priorizando.",
                    len(unused), ", ".join(unused),
                )

            # Save checkpoint before wave execution
            self._save_checkpoint(wave_idx, pending_ids)

            # Separate Gemini tasks from others to stagger them
            # (Gemini billing ativo = 30 RPM, stagger 2s gaps)
            gemini_task_ids: list[str] = []
            parallel_task_ids: list[str] = []

            for tid in pending_ids:
                task = self._task_map[tid]
                routing = self.router.route(task)
                if routing.provider == Provider.GOOGLE:
                    gemini_task_ids.append(tid)
                else:
                    parallel_task_ids.append(tid)

            # Run non-Gemini tasks in parallel + Gemini tasks staggered
            tasks_to_run: list[asyncio.Task] = []

            for tid in parallel_task_ids:
                tasks_to_run.append(
                    asyncio.create_task(self._run_task(tid, wave_idx))
                )

            if gemini_task_ids:
                tasks_to_run.append(
                    asyncio.create_task(
                        self._run_gemini_staggered(gemini_task_ids, wave_idx)
                    )
                )

            await asyncio.gather(*tasks_to_run)

            wave_duration_ms = int((time.perf_counter_ns() - wave_start) / 1_000_000)
            wave_info = {
                "wave": wave_idx + 1,
                "tasks": pending_ids,
                "task_types": [self._task_map[tid].type for tid in pending_ids],
                "duration_ms": wave_duration_ms,
            }
            self._wave_timings.append(wave_info)
            logger.info(
                "Wave %d (%d tasks): %s -> %.1fs",
                wave_idx + 1,
                len(pending_ids),
                ", ".join(wave_info["task_types"]),
                wave_duration_ms / 1000,
            )

            # Finish wave span
            tracer.finish_span(wave_span, duration_ms=wave_duration_ms)

            # Exibe status dos LLMs ao final de cada wave
            self._print_status()

            # v2.0: Early stopping check between waves
            if hasattr(self.router, 'should_early_stop'):
                remaining = sum(1 for w in waves[wave_idx + 1:] for _ in w)
                if remaining > 0:
                    completed_outputs = [
                        r.output for r in self._results.values()
                        if r.success and r.output
                    ]
                    if self.router.should_early_stop(
                        self.plan.demand, completed_outputs, remaining
                    ):
                        logger.info(
                            "EARLY STOP: demand sufficiently covered after wave %d "
                            "(%d remaining tasks skipped)",
                            wave_idx + 1, remaining,
                        )
                        break

        # Clean up checkpoint on successful completion
        self._clear_checkpoint()

        # Finish pipeline span
        total_cost = sum(r.cost for r in self._results.values())
        tracer.finish_span(
            pipeline_span,
            status="ok",
            total_cost=total_cost,
            tasks_completed=sum(1 for r in self._results.values() if r.success),
            tasks_failed=sum(1 for r in self._results.values() if not r.success),
        )

        return self._results

    @classmethod
    async def resume(cls, checkpoint_path: str | Path, router: Router) -> dict[str, TaskResult]:
        """Resume execution from a checkpoint file.

        Loads the saved plan and completed results, then continues
        from where execution was interrupted.
        """
        path = Path(checkpoint_path)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {path}")

        data = json.loads(path.read_text(encoding="utf-8"))
        plan = Plan.model_validate(data["plan"])
        pipeline = cls(plan, router)

        # Load already-completed results
        for tid, result_data in data.get("completed_results", {}).items():
            pipeline._results[tid] = TaskResult.model_validate(result_data)

        return await pipeline.execute()

    # ==================================================================
    # Wave computation (parallel execution groups)
    # ==================================================================

    def _compute_waves(self, tasks: list[Task]) -> list[list[Task]]:
        """Compute execution waves: groups of tasks that can run simultaneously.

        Wave 1: tasks with no dependencies
        Wave 2: tasks depending only on wave 1 tasks
        Wave N: tasks depending only on tasks in waves 1..N-1
        """
        task_map = {t.id: t for t in tasks}
        completed: set[str] = set()
        waves: list[list[Task]] = []
        remaining = set(task_map.keys())

        while remaining:
            # Find tasks whose dependencies are all in completed
            wave = [
                task_map[tid]
                for tid in remaining
                if all(dep in completed for dep in task_map[tid].dependencies)
            ]

            if not wave:
                # Remaining tasks have circular or broken dependencies
                logger.warning(
                    "Unresolvable dependencies detected for tasks: %s",
                    remaining,
                )
                # Add them as a final wave (they'll fail gracefully)
                wave = [task_map[tid] for tid in remaining]
                waves.append(wave)
                break

            waves.append(wave)
            for t in wave:
                completed.add(t.id)
                remaining.discard(t.id)

        return waves

    # ==================================================================
    # Checkpoint system
    # ==================================================================

    def _save_checkpoint(self, current_wave: int, pending_task_ids: list[str]) -> None:
        """Save current pipeline state for potential resume."""
        checkpoint = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "demand": self.plan.demand,
            "plan": self.plan.model_dump(mode="json"),
            "current_wave": current_wave,
            "pending_task_ids": pending_task_ids,
            "completed_results": {
                tid: result.model_dump(mode="json")
                for tid, result in self._results.items()
            },
        }
        self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._checkpoint_path.write_text(
            json.dumps(checkpoint, indent=2, default=str), encoding="utf-8"
        )

    def _load_checkpoint(self) -> set[str]:
        """Load checkpoint if it exists and matches the current demand.

        Returns a set of already-completed task IDs.
        """
        if not self._checkpoint_path.exists():
            return set()

        try:
            data = json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return set()

        # Only resume if it's the same demand
        if data.get("demand") != self.plan.demand:
            return set()

        completed_ids: set[str] = set()
        for tid, result_data in data.get("completed_results", {}).items():
            if tid in self._task_map:
                self._results[tid] = TaskResult.model_validate(result_data)
                task = self._task_map[tid]
                task.status = TaskStatus.COMPLETED if self._results[tid].success else TaskStatus.FAILED
                completed_ids.add(tid)
                logger.info("Checkpoint: restored result for task '%s'.", tid)

        if completed_ids:
            logger.info(
                "Resuming from checkpoint: %d tasks already completed.", len(completed_ids)
            )
        return completed_ids

    def _clear_checkpoint(self) -> None:
        """Remove checkpoint file after successful completion."""
        if self._checkpoint_path.exists():
            self._checkpoint_path.unlink()

    def _save_task_result(self, task_id: str, result: TaskResult) -> None:
        """Persist a single task result to disk immediately.

        Sprint 3 (2026-04-07): task_id passa por sanitize_path() porque
        decomposers podem produzir IDs com acentos/path traversal. Reusa
        licao do incidente 2026-03-27 (55 hrefs corrompidos por acentos).
        """
        # task_id pode vir com acentos ou caracteres perigosos do decomposer
        result_path = sanitize_path(self._results_dir, f"{task_id}.json")
        result_path.write_text(
            json.dumps(result.model_dump(mode="json"), indent=2, default=str),
            encoding="utf-8",
        )

    # ==================================================================
    # Quality gate
    # ==================================================================

    def _quality_check(self, task: Task, result: TaskResult) -> bool:
        """Run a quality check on a completed task's output.

        Returns True if the output passes quality checks.
        """
        output = result.output.strip()

        if task.type in ("writing", "copywriting"):
            # Must have meaningful length and no empty sections
            if len(output) < 200:
                logger.warning(
                    "Quality gate FAIL for '%s': output too short (%d chars).",
                    task.id, len(output),
                )
                return False
            # Check for empty sections (## Header\n\n## Header pattern)
            if re.search(r"##\s+\w.*\n\s*\n##\s+\w", output):
                logger.warning(
                    "Quality gate FAIL for '%s': empty sections detected.", task.id
                )
                return False

        elif task.type == "code":
            # Check balanced braces/brackets
            for open_c, close_c in [("{", "}"), ("[", "]"), ("(", ")")]:
                if output.count(open_c) != output.count(close_c):
                    logger.warning(
                        "Quality gate FAIL for '%s': unbalanced '%s'/'%s'.",
                        task.id, open_c, close_c,
                    )
                    return False
            # Check for TODO/FIXME markers
            if re.search(r"\b(TODO|FIXME)\b", output, re.IGNORECASE):
                logger.warning(
                    "Quality gate FAIL for '%s': TODO/FIXME markers found.", task.id
                )
                return False

        elif task.type == "research":
            # Must include sources/references
            has_sources = any(
                marker in output.lower()
                for marker in ["http", "fonte", "source", "referencia", "referência", "[1]", "[2]"]
            )
            if not has_sources:
                logger.warning(
                    "Quality gate FAIL for '%s': no sources/references found.", task.id
                )
                return False

        return True

    # ==================================================================
    # Context window optimization
    # ==================================================================

    def _build_context(self, task: Task) -> str:
        """Build context string from completed dependency outputs,
        summarizing long outputs to save tokens."""
        parts: list[str] = []
        for dep_id in task.dependencies:
            dep_result = self._results.get(dep_id)
            if dep_result and dep_result.success:
                output = dep_result.output
                if len(output) > CONTEXT_SUMMARIZE_THRESHOLD:
                    output = self._truncate_context(output)
                parts.append(
                    f"--- Resultado da tarefa '{dep_id}' ---\n{output}\n"
                )
        return "\n".join(parts) if parts else ""

    def _truncate_context(self, text: str) -> str:
        """Truncate long context to key points, keeping first and last sections.

        For truly long texts, we keep the first ~800 chars + last ~200 chars
        with a note about truncation. This is synchronous and free (no LLM call).
        The async summarization via Gemini is available as _optimize_context().
        """
        if len(text) <= CONTEXT_SUMMARIZE_THRESHOLD:
            return text
        head = text[:800]
        tail = text[-200:]
        return (
            f"{head}\n\n[... conteudo truncado ({len(text)} chars total) ...]\n\n{tail}"
        )

    async def _optimize_context(self, dependencies_outputs: dict[str, str]) -> str:
        """Optimize context by summarizing long dependency outputs via Groq.

        Reescrito 2026-04-07: Groq Llama 3.3 70B substitui Gemini 2.5 Pro porque
        o thinking mode do Gemini consumia o budget inteiro pensando e retornava
        finishReason=MAX_TOKENS sem texto. Groq e ultra-rapido (~10x menos
        latencia), nao tem thinking mode, custa fracao de centavo por chamada,
        e e o LLM canonico para summarization conforme TASK_TYPES.

        For outputs < threshold chars, passes directly.
        For longer outputs, calls Groq with a summarization prompt.
        Falls back to truncation if Groq is unavailable.
        """
        parts: list[str] = []

        for dep_id, output in dependencies_outputs.items():
            if len(output) <= CONTEXT_SUMMARIZE_THRESHOLD:
                parts.append(f"--- Resultado da tarefa '{dep_id}' ---\n{output}\n")
                continue

            # Try to summarize via Groq (rapido + sem thinking + canonico)
            summarizer_cfg = LLM_CONFIGS.get("groq") or LLM_CONFIGS.get("gemini")
            if summarizer_cfg and summarizer_cfg.available:
                try:
                    client = LLMClient(summarizer_cfg)
                    response = await client.query(
                        prompt=(
                            f"Resuma os pontos-chave do texto abaixo em no maximo 500 palavras. "
                            f"Mantenha dados, numeros e conclusoes importantes.\n\n{output}"
                        ),
                        system="Voce e um assistente de sumarizacao. Seja conciso e preciso.",
                        max_tokens=1500,
                    )
                    self.cost_tracker.record(
                        task_id=f"ctx_summary_{dep_id}",
                        llm=summarizer_cfg.name,
                        tokens_in=response.tokens_input,
                        tokens_out=response.tokens_output,
                        cost=response.cost,
                    )
                    parts.append(
                        f"--- Resultado da tarefa '{dep_id}' (resumido por {summarizer_cfg.name}) ---\n{response.text}\n"
                    )
                    continue
                except Exception as exc:
                    logger.warning(
                        "Failed to summarize context for '%s' via %s: %s. Using truncation.",
                        dep_id, summarizer_cfg.name, exc,
                    )

            # Fallback: simple truncation
            truncated = self._truncate_context(output)
            parts.append(f"--- Resultado da tarefa '{dep_id}' (truncado) ---\n{truncated}\n")

        return "\n".join(parts) if parts else ""

    # ==================================================================
    # Gemini staggered execution
    # ==================================================================

    async def _run_gemini_staggered(
        self, task_ids: list[str], wave_index: int
    ) -> None:
        """Run Gemini-routed tasks sequentially with staggered gaps.

        Gemini billing ativo allows 30 RPM = 1 request per 2 seconds.
        The rate limiter handles the actual throttling, but we also
        stagger task launches to avoid queuing up too many requests.
        """
        limiter = RateLimiter.get_instance()
        gemini_interval = limiter.min_interval(Provider.GOOGLE)

        for i, tid in enumerate(task_ids):
            if i > 0:
                logger.info(
                    "Gemini stagger: waiting %.1fs before task '%s' (%d/%d)",
                    gemini_interval,
                    tid,
                    i + 1,
                    len(task_ids),
                )
                await asyncio.sleep(gemini_interval)
            await self._run_task(tid, wave_index)

    # ==================================================================
    # Task execution
    # ==================================================================

    # ------------------------------------------------------------------
    # Timeout resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _get_timeout(task_type: str) -> float:
        """Return the appropriate timeout for a task type."""
        return TIMEOUT_BY_TASK_TYPE.get(task_type, DEFAULT_TIMEOUT)

    async def _run_task(self, task_id: str, wave_index: int = -1) -> None:
        """Execute a single task, iterating through the full fallback chain.

        Uses early termination: if an LLM succeeds AND passes quality gate,
        stop immediately — no further fallbacks needed. If quality fails,
        try the next LLM in the chain. If all LLMs fail, keep the best
        result available.
        """
        tracer = TraceManager.get_instance()
        task = self._task_map[task_id]
        task.status = TaskStatus.RUNNING

        # Start task span
        task_span = tracer.start_span(
            f"task.{task_id}",
            task_type=task.type,
            task_id=task_id,
            wave_index=wave_index,
            dependencies=task.dependencies,
            complexity=task.complexity.value if hasattr(task, 'complexity') else "medium",
        )

        # Build prompt with optimized dependency context
        dep_outputs = {}
        for dep_id in task.dependencies:
            dep_result = self._results.get(dep_id)
            if dep_result and dep_result.success:
                dep_outputs[dep_id] = dep_result.output

        has_long = any(len(v) > CONTEXT_SUMMARIZE_THRESHOLD for v in dep_outputs.values())
        if has_long:
            context = await self._optimize_context(dep_outputs)
        else:
            context = self._build_context(task)

        prompt = task.description
        if context:
            prompt = (
                f"Contexto das tarefas anteriores:\n{context}\n\n"
                f"Tarefa atual:\n{task.description}"
            )
        if task.expected_output:
            prompt += f"\n\nFormato esperado da saida: {task.expected_output}"

        start_time_ms = int(time.perf_counter_ns() / 1_000_000)

        # Iterate through the full fallback chain
        tried: set[str] = set()
        result: TaskResult | None = None
        chain_log: list[str] = []

        while True:
            next_cfg = self.router.get_next_in_chain(task, tried)
            if next_cfg is None:
                break

            tried.add(next_cfg.name)
            chain_log.append(next_cfg.name)

            # Atualiza estatísticas: tarefa iniciada neste LLM
            llm_key = next_cfg.name
            if llm_key in self._llm_stats:
                self._llm_stats[llm_key]["assigned"] += 1
                self._llm_stats[llm_key]["status"] = "running"

            attempt_result = await self._call_llm(task, next_cfg, prompt)
            attempt_result.wave_index = wave_index
            attempt_result.start_time_ms = start_time_ms - self._execution_start_ms

            if attempt_result.success:
                self.router.update_stats(
                    task.type, next_cfg.name, True,
                    attempt_result.duration_ms, attempt_result.cost,
                )

                # Atualiza estatísticas: tarefa concluída com sucesso
                if llm_key in self._llm_stats:
                    self._llm_stats[llm_key]["completed"] += 1
                    self._llm_stats[llm_key]["tokens"] += attempt_result.tokens_used or 0
                    self._llm_stats[llm_key]["cost"] += attempt_result.cost or 0.0
                    self._llm_stats[llm_key]["status"] = "done"

                # Early termination: quality gate passes -> done
                if self._quality_check(task, attempt_result):
                    result = attempt_result
                    logger.debug(
                        "Task '%s': %s passed quality gate. Chain: %s",
                        task_id, next_cfg.name, " > ".join(chain_log),
                    )
                    break

                # Quality failed — mark and try next in chain
                logger.info(
                    "Quality gate failed for '%s' via %s, trying next in chain.",
                    task_id, next_cfg.name,
                )
                attempt_result.quality_retried = True
                # Keep best successful result so far
                if result is None or not result.success:
                    result = attempt_result
                else:
                    result = attempt_result
            else:
                # LLM call failed entirely
                self.router.update_stats(
                    task.type, next_cfg.name, False,
                    attempt_result.duration_ms, attempt_result.cost,
                )
                logger.warning(
                    "LLM '%s' failed for task '%s': %s. Trying next in chain.",
                    next_cfg.name, task_id, attempt_result.error,
                )
                if result is None:
                    result = attempt_result

        # Safety fallback: if no result at all
        if result is None:
            result = TaskResult(
                task_id=task_id,
                llm_used="none",
                output="",
                success=False,
                error="Nenhum LLM disponivel na cadeia de fallback.",
            )
            result.wave_index = wave_index
            result.start_time_ms = start_time_ms - self._execution_start_ms

        # Record final state
        task.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
        task.assigned_llm = result.llm_used
        task.result = result.output if result.success else result.error
        task.cost = result.cost
        task.duration_ms = result.duration_ms
        self._results[task_id] = result

        # Finish task span with result metadata
        tracer.finish_span(
            task_span,
            status="ok" if result.success else "error",
            llm_used=result.llm_used,
            cost=result.cost,
            tokens_in=result.tokens_input,
            tokens_out=result.tokens_output,
            duration_ms=result.duration_ms,
            quality_retried=result.quality_retried,
            fallback_chain_tried=" > ".join(chain_log),
        )

        # Persist result immediately
        self._save_task_result(task_id, result)

    async def _call_llm(
        self, task: Task, config: LLMConfig, prompt: str
    ) -> TaskResult:
        """Call a specific LLM and return a TaskResult.

        Uses task-type-specific timeouts via connection pool.
        Includes FinOps budget check before the call and cost recording after.
        If the provider is over budget, attempts to find a cheaper alternative
        via the router. If no alternative is available, returns a failed result.
        """
        finops = get_finops()

        # FinOps: check provider budget before calling
        try:
            finops.check_budget(config.name)
        except BudgetExceededError as exc:
            logger.warning(
                "FinOps bloqueou chamada para '%s' (tarefa '%s'): %s. "
                "Tentando alternativa mais barata...",
                config.name, task.id, exc,
            )
            cheaper = finops.get_cheapest_available()
            if cheaper and cheaper != config.name and cheaper in LLM_CONFIGS:
                logger.info(
                    "FinOps: redirecionando tarefa '%s' de '%s' para '%s'.",
                    task.id, config.name, cheaper,
                )
                config = LLM_CONFIGS[cheaper]
                try:
                    finops.check_budget(config.name)
                except BudgetExceededError:
                    return TaskResult(
                        task_id=task.id,
                        llm_used=config.name,
                        output="",
                        cost=0.0,
                        duration_ms=0,
                        tokens_used=0,
                        success=False,
                        error=f"BudgetExceededError: todos os providers atingiram o limite diario.",
                    )
            else:
                return TaskResult(
                    task_id=task.id,
                    llm_used=config.name,
                    output="",
                    cost=0.0,
                    duration_ms=0,
                    tokens_used=0,
                    success=False,
                    error=f"BudgetExceededError: {exc}",
                )

        # Use task-type-specific timeout
        timeout = self._get_timeout(task.type)
        client = LLMClient(config, timeout_override=timeout)
        t0 = time.perf_counter_ns()

        try:
            response = await client.query(
                prompt=prompt,
                system=(
                    f"Voce esta executando a tarefa '{task.id}' (tipo: {task.type}). "
                    f"Responda de forma precisa e direta."
                ),
                max_tokens=config.max_tokens,
            )
            duration_ms = int((time.perf_counter_ns() - t0) / 1_000_000)

            self.cost_tracker.record(
                task_id=task.id,
                llm=config.name,
                tokens_in=response.tokens_input,
                tokens_out=response.tokens_output,
                cost=response.cost,
            )

            # FinOps: record cost after successful call
            finops.record_cost(
                task_id=task.id,
                provider_or_llm=config.name,
                tokens_in=response.tokens_input,
                tokens_out=response.tokens_output,
                cost=response.cost,
            )

            return TaskResult(
                task_id=task.id,
                llm_used=config.name,
                output=response.text,
                cost=response.cost,
                duration_ms=duration_ms,
                tokens_used=response.tokens_input + response.tokens_output,
                tokens_input=response.tokens_input,
                tokens_output=response.tokens_output,
                success=True,
            )

        except Exception as exc:
            duration_ms = int((time.perf_counter_ns() - t0) / 1_000_000)
            return TaskResult(
                task_id=task.id,
                llm_used=config.name,
                output="",
                cost=0.0,
                duration_ms=duration_ms,
                tokens_used=0,
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )

    # ==================================================================
    # Topological sort (kept for compatibility)
    # ==================================================================

    def _topological_sort(self) -> list[str]:
        """Kahn's algorithm for topological ordering."""
        in_degree: dict[str, int] = {t.id: 0 for t in self.plan.tasks}
        dependents: dict[str, list[str]] = defaultdict(list)

        for task in self.plan.tasks:
            for dep in task.dependencies:
                if dep in in_degree:
                    in_degree[task.id] += 1
                    dependents[dep].append(task.id)

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        order: list[str] = []

        while queue:
            tid = queue.pop(0)
            order.append(tid)
            for child in dependents[tid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        # Append any remaining (cycle) nodes at the end
        for tid in in_degree:
            if tid not in order:
                order.append(tid)

        return order

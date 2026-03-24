"""Execution engine — runs tasks in dependency order with parallelism.

Performs topological sorting, launches independent tasks concurrently,
handles fallback on failure, quality gates, context optimization,
and robust checkpointing for resumable execution.
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
    LLM_CONFIGS,
    LLMConfig,
    OUTPUT_DIR,
)
from .cost_tracker import CostTracker
from .llm_client import LLMClient
from .models import Plan, Task, TaskResult, TaskStatus
from .router import Router

logger = logging.getLogger(__name__)


class Pipeline:
    """Execute a Plan respecting task dependencies and maximizing parallelism.

    Features:
    - Wave-based parallel execution (groups of independent tasks)
    - Checkpoint/resume for interrupted executions
    - Quality gates with automatic retry on fallback LLM
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

        # Directories for persistence
        self._results_dir = OUTPUT_DIR / ".results"
        self._checkpoint_path = OUTPUT_DIR / ".checkpoint.json"
        self._results_dir.mkdir(parents=True, exist_ok=True)

    # ==================================================================
    # Main execution
    # ==================================================================

    async def execute(self) -> dict[str, TaskResult]:
        """Run all tasks in wave-based topological order, parallelizing
        independent tasks within each wave.

        Returns a dict mapping task_id -> TaskResult.
        """
        self._execution_start_ms = int(time.perf_counter_ns() / 1_000_000)

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

            wave_start = time.perf_counter_ns()
            logger.info(
                "Wave %d (%d tasks): %s",
                wave_idx + 1,
                len(pending_ids),
                ", ".join(f"{self._task_map[tid].type}" for tid in pending_ids),
            )

            # Save checkpoint before wave execution
            self._save_checkpoint(wave_idx, pending_ids)

            # Run wave in parallel
            await asyncio.gather(
                *(self._run_task(tid, wave_idx) for tid in pending_ids)
            )

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

        # Clean up checkpoint on successful completion
        self._clear_checkpoint()

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
        """Persist a single task result to disk immediately."""
        result_path = self._results_dir / f"{task_id}.json"
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
        """Optimize context by summarizing long dependency outputs via Gemini Flash.

        For outputs < threshold chars, passes directly.
        For longer outputs, calls Gemini with a summarization prompt.
        Falls back to truncation if Gemini is unavailable.
        """
        parts: list[str] = []

        for dep_id, output in dependencies_outputs.items():
            if len(output) <= CONTEXT_SUMMARIZE_THRESHOLD:
                parts.append(f"--- Resultado da tarefa '{dep_id}' ---\n{output}\n")
                continue

            # Try to summarize via Gemini (cheapest LLM)
            gemini_cfg = LLM_CONFIGS.get("gemini")
            if gemini_cfg and gemini_cfg.available:
                try:
                    client = LLMClient(gemini_cfg)
                    response = await client.query(
                        prompt=(
                            f"Resuma os pontos-chave do texto abaixo em no maximo 500 palavras. "
                            f"Mantenha dados, numeros e conclusoes importantes.\n\n{output}"
                        ),
                        system="Voce e um assistente de sumarizacao. Seja conciso e preciso.",
                        max_tokens=1000,
                    )
                    self.cost_tracker.record(
                        task_id=f"ctx_summary_{dep_id}",
                        llm="gemini",
                        tokens_in=response.tokens_input,
                        tokens_out=response.tokens_output,
                        cost=response.cost,
                    )
                    parts.append(
                        f"--- Resultado da tarefa '{dep_id}' (resumido) ---\n{response.text}\n"
                    )
                    continue
                except Exception as exc:
                    logger.warning(
                        "Failed to summarize context for '%s' via Gemini: %s. Using truncation.",
                        dep_id, exc,
                    )

            # Fallback: simple truncation
            truncated = self._truncate_context(output)
            parts.append(f"--- Resultado da tarefa '{dep_id}' (truncado) ---\n{truncated}\n")

        return "\n".join(parts) if parts else ""

    # ==================================================================
    # Task execution
    # ==================================================================

    async def _run_task(self, task_id: str, wave_index: int = -1) -> None:
        """Execute a single task with fallback on failure and quality gate."""
        task = self._task_map[task_id]
        task.status = TaskStatus.RUNNING

        # Build prompt with optimized dependency context
        dep_outputs = {}
        for dep_id in task.dependencies:
            dep_result = self._results.get(dep_id)
            if dep_result and dep_result.success:
                dep_outputs[dep_id] = dep_result.output

        # Use async context optimization for long outputs
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

        # Try primary LLM
        primary_cfg = self.router.route(task)
        result = await self._call_llm(task, primary_cfg, prompt)
        result.wave_index = wave_index
        result.start_time_ms = start_time_ms - self._execution_start_ms

        if result.success:
            # Update router stats on success
            self.router.update_stats(
                task.type, primary_cfg.name, True, result.duration_ms, result.cost
            )

            # Quality gate check
            if not self._quality_check(task, result):
                logger.info(
                    "Quality gate failed for '%s', retrying with fallback.", task_id
                )
                fallback_cfg = self.router.get_fallback(task)
                if fallback_cfg and fallback_cfg.name != primary_cfg.name:
                    retry_result = await self._call_llm(task, fallback_cfg, prompt)
                    retry_result.wave_index = wave_index
                    retry_result.start_time_ms = start_time_ms - self._execution_start_ms
                    retry_result.quality_retried = True
                    if retry_result.success:
                        self.router.update_stats(
                            task.type, fallback_cfg.name, True,
                            retry_result.duration_ms, retry_result.cost,
                        )
                        result = retry_result
                    else:
                        self.router.update_stats(
                            task.type, fallback_cfg.name, False,
                            retry_result.duration_ms, retry_result.cost,
                        )
                        # Keep original result if fallback also fails quality
                        result.quality_retried = True
        else:
            # Primary failed — update stats and try fallback
            self.router.update_stats(
                task.type, primary_cfg.name, False, result.duration_ms, result.cost
            )
            fallback_cfg = self.router.get_fallback(task)
            if fallback_cfg and fallback_cfg.name != primary_cfg.name:
                result = await self._call_llm(task, fallback_cfg, prompt)
                result.wave_index = wave_index
                result.start_time_ms = start_time_ms - self._execution_start_ms
                if result.success:
                    self.router.update_stats(
                        task.type, fallback_cfg.name, True,
                        result.duration_ms, result.cost,
                    )
                else:
                    self.router.update_stats(
                        task.type, fallback_cfg.name, False,
                        result.duration_ms, result.cost,
                    )

        # Record final state
        task.status = TaskStatus.COMPLETED if result.success else TaskStatus.FAILED
        task.assigned_llm = result.llm_used
        task.result = result.output if result.success else result.error
        task.cost = result.cost
        task.duration_ms = result.duration_ms
        self._results[task_id] = result

        # Persist result immediately
        self._save_task_result(task_id, result)

    async def _call_llm(
        self, task: Task, config: LLMConfig, prompt: str
    ) -> TaskResult:
        """Call a specific LLM and return a TaskResult."""
        client = LLMClient(config)
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

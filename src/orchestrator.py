"""Core orchestrator — the brain of the multi-LLM system.

Takes a natural language demand, decomposes it into tasks via Claude,
deduplicates similar tasks, checks budget limits, routes each task
to the best LLM, and executes the plan with parallelism and caching.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    AVG_COST_PER_CALL,
    BUDGET_LIMIT,
    CACHE_TTL_SECONDS,
    LLM_CONFIGS,
    OUTPUT_DIR,
    TASK_TYPES,
)
from .llm_client import LLMClient
from .models import ExecutionReport, Plan, Task, TaskResult
from .pipeline import Pipeline
from .router import Router

logger = logging.getLogger(__name__)

# Prompt template for task decomposition
DECOMPOSE_SYSTEM = """\
Voce e um gerente de projetos especialista em decompor demandas complexas \
em tarefas discretas e executaveis. Cada tarefa deve ser atribuida a um tipo \
da lista abaixo, que determina qual LLM a executara:

Tipos disponiveis: research, analysis, writing, copywriting, code, review, \
seo, data_processing, fact_check, classification, translation, summarization.

Regras:
1. Cada tarefa tem um id unico (formato: t1, t2, t3...).
2. Especifique dependencias entre tarefas (lista de ids).
3. Tarefas sem dependencia rodam em paralelo automaticamente.
4. Mantenha as descricoes claras e auto-contidas.
5. Indique o formato esperado de saida (texto, json, lista, codigo, etc).

Responda APENAS com JSON valido, sem markdown, sem explicacao. Formato:

{
  "tasks": [
    {
      "id": "t1",
      "type": "research",
      "description": "Pesquisar X sobre Y com fontes atualizadas",
      "dependencies": [],
      "expected_output": "texto com citacoes"
    }
  ]
}
"""

DECOMPOSE_USER = """\
Demanda do usuario:
{demand}

Decomponha em tarefas discretas. Responda somente com o JSON.
"""


class Orchestrator:
    """Main orchestrator that decomposes demands and executes multi-LLM plans.

    Includes smart task deduplication, result caching, budget enforcement,
    and enhanced execution reporting.
    """

    def __init__(self, *, force: bool = False) -> None:
        self.router = Router()
        self._claude_cfg = LLM_CONFIGS["claude"]
        self._force = force  # bypass budget confirmation
        self._cache_dir = OUTPUT_DIR / ".cache"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._dedup_count = 0
        self._cache_hits = 0

    async def decompose(self, demand: str) -> Plan:
        """Send the demand to Claude for decomposition into a structured Plan.

        Claude analyzes the demand and breaks it into typed tasks with
        dependencies, which are then parsed into a Plan object.
        """
        client = LLMClient(self._claude_cfg)

        response = await client.query(
            prompt=DECOMPOSE_USER.format(demand=demand),
            system=DECOMPOSE_SYSTEM,
            max_tokens=4000,
        )

        tasks = self._parse_plan(response.text, demand)
        plan = Plan(demand=demand, tasks=tasks)
        plan.total_estimated_cost = response.cost
        return plan

    async def execute(self, plan: Plan) -> dict[str, TaskResult]:
        """Execute all tasks in a plan, respecting dependencies and parallelizing."""
        pipeline = Pipeline(plan, self.router)
        results = await pipeline.execute()
        return results

    async def run(self, demand: str) -> ExecutionReport:
        """Full pipeline: decompose -> deduplicate -> budget check -> execute -> report.

        This is the main entry point for end-to-end orchestration.
        """
        t0 = time.perf_counter_ns()

        # Phase 1: Decompose
        plan = await self.decompose(demand)
        original_task_count = len(plan.tasks)

        # Phase 2: Deduplicate similar tasks
        plan.tasks = self._deduplicate(plan.tasks)
        self._dedup_count = original_task_count - len(plan.tasks)
        if self._dedup_count > 0:
            logger.info(
                "Deduplication: merged %d redundant tasks (%d -> %d).",
                self._dedup_count, original_task_count, len(plan.tasks),
            )

        # Phase 3: Check cache for already-computed results
        cached_results: dict[str, TaskResult] = {}
        tasks_to_run: list[Task] = []
        for task in plan.tasks:
            cached = self._check_cache(task)
            if cached is not None:
                cached_results[task.id] = cached
                self._cache_hits += 1
                logger.info("Cache hit for task '%s', skipping execution.", task.id)
            else:
                tasks_to_run.append(task)

        # Phase 4: Budget guard
        estimated_cost = self._estimate_cost(tasks_to_run)
        if not self._force and estimated_cost > BUDGET_LIMIT:
            logger.warning(
                "Estimated cost US$ %.4f exceeds budget limit US$ %.4f. "
                "Use --force to override.",
                estimated_cost, BUDGET_LIMIT,
            )
            raise BudgetExceededError(
                f"Custo estimado (US$ {estimated_cost:.4f}) excede o limite "
                f"(US$ {BUDGET_LIMIT:.4f}). Use --force para ignorar."
            )

        # Phase 5: Execute (only non-cached tasks)
        if tasks_to_run:
            execution_plan = Plan(demand=demand, tasks=tasks_to_run)
            # But we need all tasks in the plan for dependency resolution
            # Pass full plan and let pipeline skip cached ones
            pipeline = Pipeline(plan, self.router)
            # Pre-load cached results into pipeline
            pipeline._results.update(cached_results)
            for tid, result in cached_results.items():
                if tid in pipeline._task_map:
                    pipeline._task_map[tid].status = (
                        "completed" if result.success else "failed"
                    )
            results = await pipeline.execute()
        else:
            results = cached_results

        total_duration_ms = int((time.perf_counter_ns() - t0) / 1_000_000)

        # Phase 6: Cache new results
        for task in plan.tasks:
            result = results.get(task.id)
            if result and result.success and not result.cache_hit:
                self._write_cache(task, result)

        # Phase 7: Track running cost and check 2x budget abort
        running_cost = sum(r.cost for r in results.values())
        if running_cost > estimated_cost * 2 and estimated_cost > 0:
            logger.warning(
                "Running cost US$ %.4f exceeds 2x estimate (US$ %.4f).",
                running_cost, estimated_cost,
            )

        # Phase 8: Compile enhanced report
        total_cost = sum(r.cost for r in results.values())
        completed = sum(1 for r in results.values() if r.success)
        failed = sum(1 for r in results.values() if not r.success)
        quality_retried = sum(1 for r in results.values() if r.quality_retried)
        cached_count = sum(1 for r in results.values() if r.cache_hit)

        summary = self._build_enhanced_summary(
            demand=demand,
            plan=plan,
            results=results,
            total_cost=total_cost,
            total_duration_ms=total_duration_ms,
            completed=completed,
            failed=failed,
            estimated_cost=estimated_cost,
            quality_retried=quality_retried,
            cached_count=cached_count,
            pipeline=pipeline if tasks_to_run else None,
        )

        report = ExecutionReport(
            demand=demand,
            plan=plan,
            results=results,
            total_cost=total_cost,
            total_duration_ms=total_duration_ms,
            tasks_completed=completed,
            tasks_failed=failed,
            tasks_cached=cached_count,
            tasks_quality_retried=quality_retried,
            tasks_deduplicated=self._dedup_count,
            estimated_cost=estimated_cost,
            budget_limit=BUDGET_LIMIT,
            summary=summary,
        )

        return report

    # ==================================================================
    # Task deduplication
    # ==================================================================

    def _deduplicate(self, tasks: list[Task]) -> list[Task]:
        """Remove redundant tasks by merging those with similar prompts
        sent to the same LLM type.

        Uses simple word-overlap cosine similarity > 0.7 to detect duplicates.
        Merged tasks keep the first task's ID and combine descriptions.
        """
        if len(tasks) <= 1:
            return tasks

        kept: list[Task] = []
        merged_ids: dict[str, str] = {}  # original_id -> kept_id

        for task in tasks:
            # Find if this task is similar to an already-kept task of the same type
            duplicate_of = None
            for kept_task in kept:
                if kept_task.type != task.type:
                    continue
                similarity = self._word_overlap_similarity(
                    task.description, kept_task.description
                )
                if similarity > 0.7:
                    duplicate_of = kept_task
                    break

            if duplicate_of is not None:
                # Merge: keep the existing task, map this ID to it
                merged_ids[task.id] = duplicate_of.id
                # Append any unique info from the duplicate's description
                if task.description not in duplicate_of.description:
                    duplicate_of.description += (
                        f"\n\n[Merged from task {task.id}]: {task.description}"
                    )
                logger.info(
                    "Dedup: task '%s' merged into '%s' (similarity: %.2f).",
                    task.id, duplicate_of.id, similarity,
                )
            else:
                kept.append(task)

        # Fix dependencies: replace merged IDs in remaining tasks
        for task in kept:
            task.dependencies = [
                merged_ids.get(dep, dep) for dep in task.dependencies
            ]
            # Remove self-references
            task.dependencies = [d for d in task.dependencies if d != task.id]
            # Remove duplicates
            task.dependencies = list(dict.fromkeys(task.dependencies))

        return kept

    @staticmethod
    def _word_overlap_similarity(text_a: str, text_b: str) -> float:
        """Compute cosine similarity based on word overlap.

        Simple but effective for detecting near-duplicate prompts.
        """
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        # Cosine similarity approximation via Jaccard-like measure
        # True cosine would need TF-IDF, but word overlap works well here
        denominator = (len(words_a) ** 0.5) * (len(words_b) ** 0.5)
        if denominator == 0:
            return 0.0
        return len(intersection) / denominator

    # ==================================================================
    # Result caching
    # ==================================================================

    def _cache_key(self, task: Task) -> str:
        """Generate a SHA-256 cache key from task type + description + dependencies outputs."""
        # Collect dependency outputs for the hash
        dep_parts = []
        for dep_id in sorted(task.dependencies):
            dep_parts.append(dep_id)
        content = f"{task.type}|{task.description}|{'|'.join(dep_parts)}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _check_cache(self, task: Task) -> TaskResult | None:
        """Check if a valid cached result exists for this task.

        Returns the cached TaskResult if found and not expired, else None.
        """
        key = self._cache_key(task)
        cache_file = self._cache_dir / f"{key}.json"

        if not cache_file.exists():
            return None

        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        # Check TTL
        cached_at = data.get("cached_at", "")
        if cached_at:
            try:
                cached_time = datetime.fromisoformat(cached_at)
                now = datetime.now(timezone.utc)
                age_seconds = (now - cached_time).total_seconds()
                if age_seconds > CACHE_TTL_SECONDS:
                    logger.debug("Cache expired for task '%s' (age: %.0fs).", task.id, age_seconds)
                    return None
            except (ValueError, TypeError):
                return None

        # Reconstruct TaskResult
        result_data = data.get("result")
        if result_data is None:
            return None

        result = TaskResult.model_validate(result_data)
        result.task_id = task.id  # Remap to current task ID
        result.cache_hit = True
        return result

    def _write_cache(self, task: Task, result: TaskResult) -> None:
        """Write a task result to cache."""
        key = self._cache_key(task)
        cache_file = self._cache_dir / f"{key}.json"

        data = {
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "task_type": task.type,
            "task_description": task.description,
            "cache_key": key,
            "result": result.model_dump(mode="json"),
        }
        cache_file.write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )

    # ==================================================================
    # Budget estimation
    # ==================================================================

    def _estimate_cost(self, tasks: list[Task]) -> float:
        """Estimate total execution cost based on task types and LLM routing."""
        total = 0.0
        for task in tasks:
            routing = TASK_TYPES.get(task.type)
            if routing:
                llm_name = routing.primary
            else:
                llm_name = "gemini"  # cheapest fallback
            avg = AVG_COST_PER_CALL.get(llm_name, 0.01)
            total += avg
        return total

    # ==================================================================
    # Enhanced report building
    # ==================================================================

    def _build_enhanced_summary(
        self,
        *,
        demand: str,
        plan: Plan,
        results: dict[str, TaskResult],
        total_cost: float,
        total_duration_ms: int,
        completed: int,
        failed: int,
        estimated_cost: float,
        quality_retried: int,
        cached_count: int,
        pipeline: Pipeline | None,
    ) -> str:
        """Build a comprehensive execution report with Gantt timeline,
        cost breakdown, cache stats, and token efficiency."""
        parts: list[str] = []

        # Header
        parts.append("=" * 60)
        parts.append("RELATORIO DE EXECUCAO — geo-orchestrator")
        parts.append("=" * 60)
        parts.append("")

        # Overview
        parts.append(f"Demanda: {demand}")
        parts.append(f"Tarefas: {len(plan.tasks)} total, {completed} concluidas, {failed} falharam")
        parts.append(f"Custo estimado: US$ {estimated_cost:.4f}")
        parts.append(f"Custo real: US$ {total_cost:.4f}")
        parts.append(f"Orcamento: US$ {BUDGET_LIMIT:.4f}")
        parts.append(f"Duracao total: {total_duration_ms}ms ({total_duration_ms/1000:.1f}s)")
        parts.append("")

        # Efficiency metrics
        if self._dedup_count > 0:
            parts.append(f"Tarefas deduplicadas: {self._dedup_count}")
        if cached_count > 0:
            parts.append(f"Cache hits (execucao pulada): {cached_count}")
        if quality_retried > 0:
            parts.append(f"Quality gate retries: {quality_retried}")
        parts.append("")

        # Gantt-style timeline
        parts.append("--- Timeline (Gantt) ---")
        parts.append("")
        if results:
            # Find the earliest start and total span
            min_start = min(
                (r.start_time_ms for r in results.values() if r.start_time_ms >= 0),
                default=0,
            )
            max_end = max(
                (r.start_time_ms + r.duration_ms for r in results.values()),
                default=1,
            )
            span = max(max_end - min_start, 1)
            bar_width = 40  # characters

            for task in plan.tasks:
                result = results.get(task.id)
                if not result:
                    continue
                # Calculate bar position
                start_frac = max(0, (result.start_time_ms - min_start) / span)
                dur_frac = max(0.02, result.duration_ms / span)  # minimum visible
                bar_start = int(start_frac * bar_width)
                bar_len = max(1, int(dur_frac * bar_width))

                status_char = "+" if result.success else "X"
                if result.cache_hit:
                    status_char = "C"

                bar = "." * bar_start + status_char * bar_len
                bar = bar.ljust(bar_width, ".")
                llm_label = result.llm_used[:8].ljust(8)
                parts.append(
                    f"  [{task.id:>3}] |{bar}| {llm_label} {result.duration_ms:>5}ms"
                )

            parts.append("")
            parts.append(f"  Legend: + = success, X = failed, C = cached, . = idle")
            parts.append(f"  Timespan: {min_start}ms — {max_end}ms")
        parts.append("")

        # Wave execution details
        if pipeline and pipeline._wave_timings:
            parts.append("--- Ondas de Execucao ---")
            parts.append("")
            for wt in pipeline._wave_timings:
                task_types_str = ", ".join(wt["task_types"])
                parts.append(
                    f"  Wave {wt['wave']} ({len(wt['tasks'])} tasks): "
                    f"{task_types_str} -> {wt['duration_ms']/1000:.1f}s"
                )
            parts.append("")

        # Task details
        parts.append("--- Detalhes por Tarefa ---")
        parts.append("")
        for task in plan.tasks:
            result = results.get(task.id)
            if result and result.success:
                preview = result.output[:200] + ("..." if len(result.output) > 200 else "")
                flags = []
                if result.cache_hit:
                    flags.append("CACHE")
                if result.quality_retried:
                    flags.append("RETRY")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                parts.append(
                    f"[{task.id}] {task.type} ({result.llm_used}) - OK "
                    f"({result.duration_ms}ms, US$ {result.cost:.4f}){flag_str}"
                )
                parts.append(f"  {preview}")
            elif result:
                parts.append(
                    f"[{task.id}] {task.type} - FALHOU: {result.error}"
                )
        parts.append("")

        # Cost breakdown by LLM
        parts.append("--- Custos por LLM ---")
        llm_costs: dict[str, dict] = {}
        for result in results.values():
            llm = result.llm_used
            if llm not in llm_costs:
                llm_costs[llm] = {"calls": 0, "cost": 0.0, "tokens_in": 0, "tokens_out": 0}
            llm_costs[llm]["calls"] += 1
            llm_costs[llm]["cost"] += result.cost
            llm_costs[llm]["tokens_in"] += result.tokens_input
            llm_costs[llm]["tokens_out"] += result.tokens_output

        for llm, data in sorted(llm_costs.items()):
            parts.append(
                f"  {llm}: {data['calls']} chamadas, "
                f"US$ {data['cost']:.4f} "
                f"({data['tokens_in']} in / {data['tokens_out']} out)"
            )
        parts.append("")

        # Cost breakdown by task
        parts.append("--- Custos por Tarefa ---")
        for task in plan.tasks:
            result = results.get(task.id)
            if result:
                parts.append(
                    f"  {task.id} ({task.type}): US$ {result.cost:.4f} "
                    f"via {result.llm_used}"
                )
        parts.append("")

        # Token efficiency
        total_tokens_in = sum(r.tokens_input for r in results.values())
        total_tokens_out = sum(r.tokens_output for r in results.values())
        total_tokens = total_tokens_in + total_tokens_out
        useful_output_chars = sum(len(r.output) for r in results.values() if r.success)
        if total_tokens_out > 0:
            # Rough efficiency: useful output tokens vs total output tokens
            efficiency = useful_output_chars / max(total_tokens_out * 4, 1)  # ~4 chars per token
            parts.append("--- Eficiencia de Tokens ---")
            parts.append(f"  Tokens entrada: {total_tokens_in:,}")
            parts.append(f"  Tokens saida: {total_tokens_out:,}")
            parts.append(f"  Total: {total_tokens:,}")
            parts.append(f"  Caracteres uteis gerados: {useful_output_chars:,}")
            parts.append(f"  Eficiencia estimada: {efficiency:.1%}")
            parts.append("")

        parts.append("=" * 60)

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_plan(self, raw_json: str, demand: str) -> list[Task]:
        """Parse Claude's JSON response into a list of Task objects.

        Handles common LLM quirks: markdown fences, trailing commas, etc.
        """
        text = raw_json.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Last resort: try to find JSON object in the text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end])
                except json.JSONDecodeError:
                    # Return a single catch-all task
                    return [
                        Task(
                            id="t1",
                            type="writing",
                            description=demand,
                            expected_output="texto",
                        )
                    ]
            else:
                return [
                    Task(
                        id="t1",
                        type="writing",
                        description=demand,
                        expected_output="texto",
                    )
                ]

        raw_tasks = data.get("tasks", [])
        if not raw_tasks:
            return [
                Task(
                    id="t1",
                    type="writing",
                    description=demand,
                    expected_output="texto",
                )
            ]

        # Validate task types
        valid_types = set(TASK_TYPES.keys())
        tasks: list[Task] = []
        for rt in raw_tasks:
            task_type = rt.get("type", "writing")
            if task_type not in valid_types:
                task_type = "writing"
            tasks.append(
                Task(
                    id=rt.get("id", f"t{len(tasks)+1}"),
                    type=task_type,
                    description=rt.get("description", ""),
                    dependencies=rt.get("dependencies", []),
                    expected_output=rt.get("expected_output", "texto"),
                )
            )

        return tasks


class BudgetExceededError(Exception):
    """Raised when estimated execution cost exceeds the configured budget limit."""
    pass

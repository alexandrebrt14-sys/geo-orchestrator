"""Adaptive wave-by-wave demand decomposition inspired by HALO (arXiv 2505.13516).

Instead of decomposing the entire demand upfront, generates one wave at a time
and adapts based on actual outputs from previous waves.
"""

from __future__ import annotations

import json
import logging
import re

from .config import LLM_CONFIGS
from .llm_client import LLMClient
from .models import Task, TaskComplexity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

MACRO_PLAN_SYSTEM = (
    "Voce e o planejador da Brasil GEO. Crie um plano MACRO de alto nivel (3-5 etapas) "
    "para resolver a demanda. NAO detalhe tarefas — apenas liste as etapas macro.\n\n"
    'Responda em JSON: {"macro_steps": ["etapa1", "etapa2", ...], "estimated_waves": 3}'
)

WAVE_DECOMPOSE_SYSTEM = (
    "Voce e o decompositor da Brasil GEO. Gere APENAS as tarefas da proxima wave.\n\n"
    "Contexto:\n"
    "- Demanda original: {demand}\n"
    "- Plano macro: {macro_plan}\n"
    "- Etapas ja concluidas: {completed_steps}\n"
    "- Resultados da wave anterior (resumo): {previous_summary}\n"
    "- Wave atual: {wave_number}\n\n"
    "REGRAS:\n"
    '- Gere APENAS tarefas para esta wave (nao planeje waves futuras)\n'
    '- Se os resultados anteriores ja cobrem a demanda, retorne {{"tasks": [], "complete": true}}\n'
    "- Maximo 5 tarefas por wave\n"
    "- Cada tarefa deve ser independente dentro desta wave (paralelizavel)\n"
    "- Cada tarefa deve ter: id, type, description, expected_output, complexity (low/medium/high)\n\n"
    'Responda em JSON: {{"tasks": [...], "complete": false}}'
)

# Keywords that hint at task complexity
_HIGH_KEYWORDS = re.compile(
    r"arquitetura|codigo|implementa|refator|design|complexo|critico|sistema",
    re.IGNORECASE,
)
_LOW_KEYWORDS = re.compile(
    r"classific|resum|lista|filtr|simples|rapido|triag|traduz",
    re.IGNORECASE,
)


def _estimate_complexity(description: str) -> TaskComplexity:
    """Estimate task complexity from description keywords."""
    if _HIGH_KEYWORDS.search(description):
        return TaskComplexity.HIGH
    if _LOW_KEYWORDS.search(description):
        return TaskComplexity.LOW
    return TaskComplexity.MEDIUM


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown fences."""
    # Strip markdown code fences if present
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip().rstrip("`")

    # Find first { ... last }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in LLM response: {text[:200]}")
    return json.loads(cleaned[start : end + 1])


class AdaptiveDecomposer:
    """Decomposes demands wave-by-wave, adapting based on intermediate results.

    Inspired by HALO: instead of pre-planning the entire workflow,
    generates one wave at a time and adjusts based on actual outputs.
    """

    def __init__(self, llm_client_unused: LLMClient | None = None, max_waves: int = 4) -> None:
        self.max_waves = max_waves
        # We build our own clients: Gemini for macro plan, Claude for wave decomposition
        self._gemini_client = LLMClient(LLM_CONFIGS["gemini"])
        self._claude_client = LLMClient(LLM_CONFIGS["claude"])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def decompose_wave(
        self,
        demand: str,
        wave_index: int,
        previous_results: dict[str, str] | None = None,
        macro_plan: list[str] | None = None,
    ) -> list[Task]:
        """Generate tasks for the next wave based on demand and previous results.

        Returns an empty list when the demand is fully covered or max_waves is
        reached, signalling that no more waves are needed.
        """
        # Safety: enforce max_waves
        if wave_index >= self.max_waves:
            logger.info(
                "ADAPTIVE: max_waves (%d) reached — stopping decomposition",
                self.max_waves,
            )
            return []

        if wave_index == 0 and previous_results is None:
            return await self._phase1_macro_plan(demand)
        else:
            return await self._phase2_micro_decompose(
                demand,
                wave_index,
                previous_results or {},
                macro_plan or [],
            )

    # ------------------------------------------------------------------
    # Phase 1 — Macro Plan (wave 0 only)
    # ------------------------------------------------------------------

    async def _phase1_macro_plan(self, demand: str) -> list[Task]:
        """Generate a high-level macro plan and return wave-0 tasks."""
        logger.info("ADAPTIVE: wave 0 — generating macro plan via Gemini")

        response = await self._gemini_client.query(
            prompt=f"Demanda: {demand}",
            system=MACRO_PLAN_SYSTEM,
            max_tokens=1000,
        )

        data = _extract_json(response.text)
        macro_steps: list[str] = data.get("macro_steps", [])

        if not macro_steps:
            logger.warning("ADAPTIVE: macro plan returned zero steps, creating fallback task")
            return [
                Task(
                    id="w0_t0",
                    type="analysis",
                    description=f"Analisar a demanda: {demand}",
                    dependencies=[],
                    expected_output="Analise completa da demanda",
                    complexity=TaskComplexity.MEDIUM,
                )
            ]

        # Create one task per macro step for wave 0 (max 5)
        tasks: list[Task] = []
        for i, step in enumerate(macro_steps[:5]):
            complexity = _estimate_complexity(step)
            task = Task(
                id=f"w0_t{i}",
                type=self._infer_task_type(step),
                description=step,
                dependencies=[],
                expected_output=f"Resultado da etapa: {step}",
                complexity=complexity,
            )
            tasks.append(task)

        logger.info(
            "ADAPTIVE: wave 0 — generating %d tasks (macro steps: %s)",
            len(tasks),
            [s[:50] for s in macro_steps],
        )
        return tasks

    # ------------------------------------------------------------------
    # Phase 2 — Micro Decomposition (waves 1+)
    # ------------------------------------------------------------------

    async def _phase2_micro_decompose(
        self,
        demand: str,
        wave_index: int,
        previous_results: dict[str, str],
        macro_plan: list[str],
    ) -> list[Task]:
        """Generate tasks for a subsequent wave, adapting to previous results."""
        previous_summary = self._summarize_results(previous_results)

        # Determine which macro steps are already covered
        completed_steps = self._identify_completed_steps(macro_plan, previous_results)

        system_prompt = WAVE_DECOMPOSE_SYSTEM.format(
            demand=demand,
            macro_plan=json.dumps(macro_plan, ensure_ascii=False),
            completed_steps=json.dumps(completed_steps, ensure_ascii=False),
            previous_summary=previous_summary,
            wave_number=wave_index + 1,
        )

        logger.info(
            "ADAPTIVE: wave %d — requesting decomposition via Claude (completed: %d/%d macro steps)",
            wave_index,
            len(completed_steps),
            len(macro_plan),
        )

        response = await self._claude_client.query(
            prompt=f"Gere as tarefas da wave {wave_index + 1} para a demanda.",
            system=system_prompt,
            max_tokens=2000,
        )

        data = _extract_json(response.text)

        # Check if the LLM signals completion
        if data.get("complete", False) or not data.get("tasks"):
            logger.info("ADAPTIVE: demand fully covered — no more waves needed")
            return []

        raw_tasks: list[dict] = data.get("tasks", [])
        tasks: list[Task] = []
        for i, raw in enumerate(raw_tasks[:5]):
            description = raw.get("description", raw.get("name", f"Task {i}"))
            task_type = raw.get("type", self._infer_task_type(description))
            raw_complexity = raw.get("complexity", "medium").lower()
            complexity = (
                TaskComplexity.LOW if raw_complexity == "low"
                else TaskComplexity.HIGH if raw_complexity == "high"
                else TaskComplexity.MEDIUM
            )

            task = Task(
                id=f"w{wave_index}_t{i}",
                type=task_type,
                description=description,
                dependencies=[],
                expected_output=raw.get("expected_output", ""),
                complexity=complexity,
            )
            tasks.append(task)

        current_macro = macro_plan[wave_index] if wave_index < len(macro_plan) else "adaptativo"
        logger.info(
            "ADAPTIVE: wave %d — generating %d tasks (macro step: '%s')",
            wave_index,
            len(tasks),
            current_macro[:60],
        )
        return tasks

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _summarize_results(self, results: dict[str, str], max_chars: int = 1000) -> str:
        """Create a brief summary of previous wave results for context."""
        if not results:
            return "(nenhum resultado anterior)"

        lines: list[str] = []
        for task_id, output in results.items():
            truncated = output[:200] + "..." if len(output) > 200 else output
            lines.append(f"- {task_id}: {truncated}")

        joined = "\n".join(lines)
        if len(joined) > max_chars:
            joined = joined[:max_chars] + "\n... (truncado)"
        return joined

    def _identify_completed_steps(
        self,
        macro_plan: list[str],
        previous_results: dict[str, str],
    ) -> list[str]:
        """Identify which macro steps are likely covered by previous results."""
        if not previous_results:
            return []

        all_outputs = " ".join(previous_results.values()).lower()
        completed: list[str] = []

        for step in macro_plan:
            # Simple heuristic: check if key words from the step appear in outputs
            words = [w for w in step.lower().split() if len(w) > 4]
            if words and sum(1 for w in words if w in all_outputs) >= len(words) * 0.5:
                completed.append(step)

        return completed

    @staticmethod
    def _infer_task_type(description: str) -> str:
        """Infer task type from description keywords."""
        desc = description.lower()
        mapping = [
            ("pesquis", "research"),
            ("busca", "research"),
            ("investigar", "research"),
            ("escrev", "writing"),
            ("redig", "writing"),
            ("redacao", "writing"),
            ("artigo", "writing"),
            ("copy", "copywriting"),
            ("analis", "analysis"),
            ("avaliar", "analysis"),
            ("codigo", "code"),
            ("implementa", "code"),
            ("programa", "code"),
            ("revis", "review"),
            ("auditar", "review"),
            ("seo", "seo"),
            ("classific", "classification"),
            ("categori", "classification"),
            ("resum", "summarization"),
            ("sintetiz", "summarization"),
            ("traduz", "translation"),
            ("dados", "data_processing"),
            ("processar", "data_processing"),
            ("verificar", "fact_check"),
            ("checar", "fact_check"),
        ]
        for keyword, task_type in mapping:
            if keyword in desc:
                return task_type
        return "analysis"

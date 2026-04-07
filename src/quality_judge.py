"""LLM-as-Judge with Rubric — Quality evaluation for orchestrator output.

Inspired by Anthropic Engineering's multi-agent research system. After the
final output is consolidated, this module evaluates quality across 5 dimensions
using Groq (cheapest fast model) as the judge.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from .config import LLM_CONFIGS
from .llm_client import LLMClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rubric prompt
# ---------------------------------------------------------------------------

RUBRIC_PROMPT = """Avalie o output abaixo em 5 dimensões (0-10 cada).

DEMANDA ORIGINAL: {demand}

OUTPUT A AVALIAR:
{output}

DIMENSÕES:
1. PRECISÃO FACTUAL (0-10): As afirmações são corretas e verificáveis?
2. COMPLETUDE (0-10): A demanda original foi respondida integralmente?
3. QUALIDADE PT-BR (0-10): Acentuação completa? Sem padrões mecânicos de IA?
4. EFICIÊNCIA (0-10): O conteúdo é conciso e sem redundância?
5. FONTES (0-10): As fontes são autoritativas? (Se não há fontes, avalie se eram necessárias)

Responda APENAS no formato JSON:
{{
  "factual_accuracy": <0-10>,
  "completeness": <0-10>,
  "ptbr_quality": <0-10>,
  "efficiency": <0-10>,
  "source_quality": <0-10>,
  "critical_issues": ["issue1", "issue2", "issue3"],
  "suggestions": ["suggestion1", "suggestion2"]
}}
"""


# ---------------------------------------------------------------------------
# QualityScore dataclass
# ---------------------------------------------------------------------------

@dataclass
class QualityScore:
    """Result of a quality evaluation across 5 dimensions."""

    factual_accuracy: int    # 0-10
    completeness: int        # 0-10
    ptbr_quality: int        # 0-10
    efficiency: int          # 0-10
    source_quality: int      # 0-10
    total: int               # 0-50
    percentage: float        # 0-100
    verdict: str             # "APROVADO", "APROVADO_COM_RESSALVAS", "REPROVADO"
    critical_issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        issues = "; ".join(self.critical_issues) if self.critical_issues else "nenhum"
        return (
            f"QualityScore({self.total}/50 — {self.percentage:.0f}% — {self.verdict}) "
            f"| Problemas: {issues}"
        )


# ---------------------------------------------------------------------------
# QualityJudge
# ---------------------------------------------------------------------------

class QualityJudge:
    """Evaluate orchestrator output quality using Claude as judge with explicit rubric."""

    @staticmethod
    def _parse_json(raw: str) -> dict | None:
        """Extract and parse JSON from LLM response, handling markdown fences."""
        text = raw.strip()
        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Falha ao parsear JSON do judge. Raw: %s", raw[:300])
            return None

    @staticmethod
    def _clamp(value: int | float, lo: int = 0, hi: int = 10) -> int:
        """Clamp a value to [lo, hi] and coerce to int."""
        try:
            return max(lo, min(hi, int(value)))
        except (TypeError, ValueError):
            return 5  # safe default

    @staticmethod
    def _determine_verdict(percentage: float) -> str:
        if percentage >= 80:
            return "APROVADO"
        elif percentage >= 60:
            return "APROVADO_COM_RESSALVAS"
        else:
            return "REPROVADO"

    def _build_default_score(self, note: str) -> QualityScore:
        """Return a neutral score when evaluation fails."""
        return QualityScore(
            factual_accuracy=5,
            completeness=5,
            ptbr_quality=5,
            efficiency=5,
            source_quality=5,
            total=25,
            percentage=50.0,
            verdict="APROVADO_COM_RESSALVAS",
            critical_issues=[note],
            suggestions=["Reexecutar avaliação de qualidade manualmente"],
        )

    async def evaluate(
        self,
        demand: str,
        final_output: str,
        llm_client: LLMClient | None = None,
    ) -> QualityScore:
        """Evaluate the final output against the original demand.

        Parameters
        ----------
        demand:
            The original user demand / prompt.
        final_output:
            The consolidated output to be evaluated.
        llm_client:
            Optional pre-configured LLMClient. If None, a new client is created
            using the Groq config from LLM_CONFIGS.

        Returns
        -------
        QualityScore with scores, verdict, issues, and suggestions.
        """
        # Build Groq client if not provided
        if llm_client is None:
            judge_config = LLM_CONFIGS["groq"]
            llm_client = LLMClient(judge_config)

        prompt = RUBRIC_PROMPT.format(demand=demand, output=final_output)

        try:
            response = await llm_client.query(
                prompt=prompt,
                system="Você é um avaliador de qualidade rigoroso. Responda apenas em JSON válido.",
                max_tokens=1000,
            )
            raw_text = response.text  # LLMResponse expoe .text (corrigido 2026-04-07)
        except Exception as exc:
            logger.error("Erro ao chamar LLM judge: %s", exc)
            return self._build_default_score(f"Erro na chamada ao judge: {exc}")

        # Parse response
        data = self._parse_json(raw_text)
        if data is None:
            return self._build_default_score("Falha ao parsear JSON retornado pelo judge")

        # Extract and clamp scores
        factual = self._clamp(data.get("factual_accuracy", 5))
        completeness = self._clamp(data.get("completeness", 5))
        ptbr = self._clamp(data.get("ptbr_quality", 5))
        efficiency = self._clamp(data.get("efficiency", 5))
        sources = self._clamp(data.get("source_quality", 5))

        total = factual + completeness + ptbr + efficiency + sources
        percentage = (total / 50) * 100
        verdict = self._determine_verdict(percentage)

        # Extract issues and suggestions (max 3 each)
        critical_issues = data.get("critical_issues", [])
        if not isinstance(critical_issues, list):
            critical_issues = []
        critical_issues = [str(i) for i in critical_issues[:3]]

        suggestions = data.get("suggestions", [])
        if not isinstance(suggestions, list):
            suggestions = []
        suggestions = [str(s) for s in suggestions[:3]]

        # Only include critical_issues if score is low
        if percentage >= 60:
            critical_issues = []

        score = QualityScore(
            factual_accuracy=factual,
            completeness=completeness,
            ptbr_quality=ptbr,
            efficiency=efficiency,
            source_quality=sources,
            total=total,
            percentage=percentage,
            verdict=verdict,
            critical_issues=critical_issues,
            suggestions=suggestions,
        )

        logger.info("Quality evaluation: %s", score)
        return score

    # ------------------------------------------------------------------
    # Cache TTL helper
    # ------------------------------------------------------------------

    def get_cache_ttl(self, score: QualityScore) -> int:
        """Higher quality = longer cache TTL.

        Returns TTL in seconds:
        - >= 80%: 48 h (high quality, safe to cache longer)
        - >= 60%: 24 h (standard)
        - < 60%:   0   (don't cache low quality)
        """
        if score.percentage >= 80:
            return 48 * 3600  # 48h for high quality
        elif score.percentage >= 60:
            return 24 * 3600  # 24h standard
        else:
            return 0  # don't cache low quality

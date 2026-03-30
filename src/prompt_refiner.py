"""Three-stage prompt refinement pipeline inspired by the HALO paper.

Transforms raw user demands into optimized prompts before they reach
the LLMs. Two of the three stages are pure code (zero LLM cost).

Stages:
    1. Parser   — extract structured metadata (code, no LLM)
    2. Enricher — inject workspace context and constraints (code, no LLM)
    3. Optimizer — apply task-type-specific prompting strategies (template)
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent keyword mappings
# ---------------------------------------------------------------------------

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "research": [
        "pesquisar", "pesquise", "pesquisa", "research", "buscar",
        "busque", "busca", "investigar", "investigate", "levantar",
        "find", "search", "discover",
    ],
    "create": [
        "criar", "crie", "escrever", "escreva", "gerar", "gere",
        "create", "write", "generate", "produzir", "produza",
        "redigir", "redija", "draft", "compose",
    ],
    "analyze": [
        "analisar", "analise", "avaliar", "avalie", "analyze",
        "analyse", "evaluate", "comparar", "compare", "diagnosticar",
        "benchmark", "medir", "measure",
    ],
    "fix": [
        "corrigir", "corrija", "fix", "consertar", "conserte",
        "resolver", "resolva", "reparar", "repare", "debug",
        "patch", "hotfix",
    ],
    "review": [
        "revisar", "revise", "review", "verificar", "verifique",
        "checar", "check", "auditar", "audit", "validar", "validate",
    ],
}

# PT-BR markers — words/patterns that almost never appear in English text
_PTBR_MARKERS: list[str] = [
    "não", "você", "são", "está", "também", "será", "então",
    "através", "além", "já", "até", "após", "sobre", "como",
    "criar", "escrever", "analisar", "corrigir", "revisar",
    "pesquisar", "buscar", "gerar", "produzir", "avaliar",
    "artigo", "conteúdo", "publicação", "página", "relatório",
    r"\bdo\b", r"\bda\b", r"\bdos\b", r"\bdas\b", r"\bno\b",
    r"\bna\b", r"\bnos\b", r"\bnas\b", r"\bpara\b", r"\bcom\b",
    r"\bum\b", r"\buma\b", r"\bé\b", r"\bou\b", r"\be\b",
]

# Format hint patterns
_FORMAT_PATTERNS: dict[str, list[str]] = {
    "json": ["json", "api", "endpoint", "payload", "schema"],
    "code": ["código", "code", "script", "função", "function", "class", "python", "javascript"],
    "table": ["tabela", "table", "planilha", "spreadsheet", "csv"],
    "markdown": ["markdown", "md", "readme", "documentação", "documentation"],
}

# Task-type optimization templates
_OPTIMIZATION_TEMPLATES: dict[str, str] = {
    "research": (
        "\n\n[Diretrizes de pesquisa]\n"
        "- Cite fontes com URLs. Priorize dados de 2025-2026.\n"
        "- Separe fatos verificáveis de análises/opiniões.\n"
        "- Indique o nível de confiança de cada afirmação."
    ),
    "writing": (
        "\n\n[Diretrizes de redação]\n"
        "- Tom editorial humano (HBR/MIT Sloan).\n"
        "- Evite: 'X não é Y, é Z', listas genéricas sem dados.\n"
        "- Cada parágrafo deve ter substância — dados, exemplos ou argumentação original."
    ),
    "code": (
        "\n\n[Code guidelines]\n"
        "- Python 3.11+. Type hints. Docstrings in English.\n"
        "- Follow existing project conventions.\n"
        "- Include error handling and logging where appropriate."
    ),
    "analysis": (
        "\n\n[Diretrizes de análise]\n"
        "- Inclua números concretos. Compare com benchmarks do setor.\n"
        "- Apresente conclusões acionáveis, não apenas observações.\n"
        "- Indique limitações da análise."
    ),
    "review": (
        "\n\n[Diretrizes de revisão]\n"
        "- Verifique: acentuação PT-BR, credencial canônica, naming violations.\n"
        "- Liste cada problema encontrado com localização exata.\n"
        "- Classifique severidade: crítico / importante / menor."
    ),
    "seo": (
        "\n\n[Diretrizes de SEO]\n"
        "- Foque em E-E-A-T. Considere schema.org e llms.txt.\n"
        "- Priorize GEO (Generative Engine Optimization) além de SEO clássico.\n"
        "- Inclua recomendações para structured data e citabilidade por LLMs."
    ),
}

# Canonical credential and naming rules
_CANONICAL_CREDENTIAL = (
    "CEO da Brasil GEO, ex-CMO da Semantix (Nasdaq), cofundador da AI Brasil"
)
_NAMING_RULES = (
    "NUNCA usar 'Especialista #1', 'GEO Brasil', 'Source Rank'. "
    "Entidade correta: 'Brasil GEO'."
)

# Entity extraction patterns
_ENTITY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r'"([^"]+)"'),                        # quoted strings
    re.compile(r"'([^']+)'"),                         # single-quoted strings
    re.compile(r"@([\w.-]+)"),                        # @mentions
    re.compile(r"https?://[^\s,)]+"),                 # URLs
    re.compile(r"\b([A-Z][a-zà-ú]+(?:\s+[A-Z][a-zà-ú]+)+)\b"),  # multi-word proper nouns
]


class PromptRefiner:
    """Three-stage prompt refinement pipeline.

    Transforms a raw demand string into an optimized prompt through
    parsing, enrichment, and task-type-specific optimization — all
    without any LLM calls.
    """

    async def refine(
        self,
        demand: str,
        task_type: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Run the full 3-stage refinement pipeline.

        Args:
            demand: Raw user demand string.
            task_type: One of the orchestrator task types (research, writing,
                code, analysis, review, seo, etc.).
            context: Optional workspace context dict (e.g. article_count,
                site_urls, current_project).

        Returns:
            Optimized prompt string ready for LLM consumption.
        """
        total_start = time.perf_counter()

        # Stage 1 — Parse
        t0 = time.perf_counter()
        parsed = self._parse(demand)
        stage1_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "PromptRefiner Stage 1 (Parser): %.1f ms | intent=%s lang=%s "
            "entities=%d complexity=%d",
            stage1_ms,
            parsed["intent"],
            parsed["language"],
            len(parsed["entities"]),
            parsed["complexity_estimate"],
        )

        # Stage 2 — Enrich
        t0 = time.perf_counter()
        enriched = self._enrich(demand, parsed, context)
        stage2_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "PromptRefiner Stage 2 (Enricher): %.1f ms | added %d chars",
            stage2_ms,
            len(enriched) - len(demand),
        )

        # Stage 3 — Optimize
        t0 = time.perf_counter()
        optimized = self._optimize(enriched, task_type)
        stage3_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "PromptRefiner Stage 3 (Optimizer): %.1f ms | task_type=%s",
            stage3_ms,
            task_type,
        )

        total_ms = (time.perf_counter() - total_start) * 1000
        logger.info(
            "PromptRefiner total: %.1f ms | %d → %d chars",
            total_ms,
            len(demand),
            len(optimized),
        )

        return optimized

    # ------------------------------------------------------------------
    # Stage 1 — Parser (code, no LLM)
    # ------------------------------------------------------------------

    def _parse(self, demand: str) -> dict[str, Any]:
        """Extract structured metadata from a raw demand string.

        Returns:
            Dict with keys: intent, language, entities, format_hint,
            complexity_estimate, word_count.
        """
        demand_lower = demand.lower()
        words = demand.split()
        word_count = len(words)

        # --- Intent detection ---
        intent = self._detect_intent(demand_lower)

        # --- Language detection ---
        language = self._detect_language(demand_lower)

        # --- Entity extraction ---
        entities = self._extract_entities(demand)

        # --- Format hint ---
        format_hint = self._detect_format(demand_lower)

        # --- Complexity estimate (1-10) ---
        complexity = self._estimate_complexity(demand, word_count, entities)

        return {
            "intent": intent,
            "language": language,
            "entities": entities,
            "format_hint": format_hint,
            "complexity_estimate": complexity,
            "word_count": word_count,
        }

    def _detect_intent(self, demand_lower: str) -> str:
        """Detect the primary intent from keyword matching."""
        scores: dict[str, int] = {intent: 0 for intent in _INTENT_KEYWORDS}
        for intent, keywords in _INTENT_KEYWORDS.items():
            for kw in keywords:
                if kw in demand_lower:
                    scores[intent] += 1

        best = max(scores, key=scores.get)  # type: ignore[arg-type]
        if scores[best] == 0:
            return "create"  # default intent
        return best

    def _detect_language(self, demand_lower: str) -> str:
        """Detect whether the demand is PT-BR or English."""
        ptbr_hits = 0
        for marker in _PTBR_MARKERS:
            if marker.startswith(r"\b"):
                if re.search(marker, demand_lower):
                    ptbr_hits += 1
            elif marker in demand_lower:
                ptbr_hits += 1

        return "pt-BR" if ptbr_hits >= 2 else "en"

    def _extract_entities(self, demand: str) -> list[str]:
        """Extract proper nouns, brand names, quoted strings, URLs, @mentions."""
        entities: list[str] = []
        seen: set[str] = set()

        for pattern in _ENTITY_PATTERNS:
            for match in pattern.finditer(demand):
                entity = match.group(0) if not match.lastindex else match.group(1)
                entity_stripped = entity.strip()
                if entity_stripped and entity_stripped.lower() not in seen:
                    seen.add(entity_stripped.lower())
                    entities.append(entity_stripped)

        return entities

    def _detect_format(self, demand_lower: str) -> str:
        """Detect the expected output format from keywords."""
        for fmt, keywords in _FORMAT_PATTERNS.items():
            for kw in keywords:
                if kw in demand_lower:
                    return fmt
        return "text"

    def _estimate_complexity(
        self, demand: str, word_count: int, entities: list[str],
    ) -> int:
        """Estimate task complexity on a 1-10 scale.

        Heuristics:
            - Base from word count: short (<15) = 2, medium (<50) = 4, long = 6
            - +1 per multi-step indicator (e.g. "e depois", "em seguida", "then")
            - +1 if multiple entities (requires cross-referencing)
            - +1 if research + creation combined
            - Clamped to [1, 10]
        """
        # Base from length
        if word_count < 15:
            score = 2
        elif word_count < 50:
            score = 4
        else:
            score = 6

        # Multi-step indicators
        multi_step_markers = [
            "e depois", "em seguida", "then", "after that",
            "primeiro", "segundo", "terceiro", "first", "second",
            "1.", "2.", "3.", "passo", "step", "etapa",
        ]
        demand_lower = demand.lower()
        for marker in multi_step_markers:
            if marker in demand_lower:
                score += 1
                break

        # Multiple entities suggest cross-referencing
        if len(entities) >= 3:
            score += 1

        # Research + creation combined
        has_research = any(
            kw in demand_lower for kw in _INTENT_KEYWORDS["research"]
        )
        has_create = any(
            kw in demand_lower for kw in _INTENT_KEYWORDS["create"]
        )
        if has_research and has_create:
            score += 2

        return max(1, min(10, score))

    # ------------------------------------------------------------------
    # Stage 2 — Enricher (code, no LLM)
    # ------------------------------------------------------------------

    def _enrich(
        self,
        demand: str,
        parsed: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> str:
        """Add workspace context and constraints to the prompt.

        Injects canonical credentials, naming rules, language constraints,
        format instructions, and any workspace context provided.
        """
        sections: list[str] = [demand]

        # Canonical credential
        sections.append(
            f"\n\n[Autor]\n{_CANONICAL_CREDENTIAL}"
        )

        # Naming rules
        sections.append(
            f"\n\n[Regras de nomenclatura]\n{_NAMING_RULES}"
        )

        # Language-specific constraints
        if parsed["language"] == "pt-BR":
            sections.append(
                "\n\n[Idioma]\nPortuguês do Brasil. "
                "Acentuação completa obrigatória. "
                "Nunca omitir acentos (não, você, produção, publicação)."
            )

        # Format instructions
        fmt = parsed["format_hint"]
        if fmt == "json":
            sections.append(
                "\n\n[Formato]\nRetorne JSON válido. "
                "Use aspas duplas. Inclua schema se relevante."
            )
        elif fmt == "code":
            sections.append(
                "\n\n[Formato]\nRetorne código limpo e funcional. "
                "Inclua type hints e docstrings."
            )
        elif fmt == "table":
            sections.append(
                "\n\n[Formato]\nApresente dados em formato tabular "
                "(Markdown table ou CSV conforme contexto)."
            )
        elif fmt == "markdown":
            sections.append(
                "\n\n[Formato]\nRetorne em Markdown bem estruturado "
                "com headings, listas e ênfase onde apropriado."
            )

        # Workspace context
        if context:
            ctx_lines = ["\n\n[Contexto do workspace]"]
            for key, value in context.items():
                ctx_lines.append(f"- {key}: {value}")
            sections.append("\n".join(ctx_lines))

        return "".join(sections)

    # ------------------------------------------------------------------
    # Stage 3 — Optimizer (code, template-based)
    # ------------------------------------------------------------------

    def _optimize(self, enriched_prompt: str, task_type: str) -> str:
        """Apply task-type-specific prompting strategies.

        Uses predefined templates keyed by task_type. Falls back to
        a generic quality instruction if the task_type has no template.
        """
        template = _OPTIMIZATION_TEMPLATES.get(task_type)

        if template:
            return enriched_prompt + template

        # Generic fallback for unmapped task types
        return enriched_prompt + (
            "\n\n[Qualidade]\n"
            "- Seja preciso e conciso.\n"
            "- Priorize informações acionáveis.\n"
            "- Indique limitações ou incertezas."
        )

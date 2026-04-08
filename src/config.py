"""Configuration for the multi-LLM orchestrator.

Defines LLM providers, model settings, task type routing, cost parameters,
and FinOps safety limits.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class Provider(str, Enum):
    """Supported LLM providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    PERPLEXITY = "perplexity"
    GROQ = "groq"


@dataclass(frozen=True)
class LLMConfig:
    """Configuration for a single LLM provider/model."""
    name: str
    provider: Provider
    model: str
    api_key_env: str
    strengths: list[str]
    cost_per_1k_input: float
    cost_per_1k_output: float
    max_tokens: int
    role: str

    @property
    def api_key(self) -> str | None:
        """Get API key from environment. NEVER log or print this value."""
        key = os.environ.get(self.api_key_env)
        if key is None:
            logger.warning("Missing API key for env var: %s", self.api_key_env)
        return key

    @property
    def available(self) -> bool:
        return bool(os.environ.get(self.api_key_env))

    def __repr__(self) -> str:
        """Hide API key in string representation to prevent accidental exposure."""
        return (
            f"LLMConfig(name={self.name!r}, provider={self.provider.value!r}, "
            f"model={self.model!r}, api_key_env={self.api_key_env!r})"
        )

    def __str__(self) -> str:
        """Safe string representation — no secrets."""
        return f"LLMConfig({self.name}, {self.provider.value}, {self.model})"


# ---------------------------------------------------------------------------
# LLM configurations
# ---------------------------------------------------------------------------

# REGRA: sempre usar a versao mais moderna e potente de cada provider.
# Atualizar modelos quando novas versoes forem lancadas.
# Ultima revisao: 2026-03-30
LLM_CONFIGS: dict[str, LLMConfig] = {
    "claude": LLMConfig(
        name="claude",
        provider=Provider.ANTHROPIC,
        model="claude-opus-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        strengths=["architecture", "complex_code", "critical_review", "reasoning"],
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        max_tokens=8192,
        role="Arquiteto e revisor principal. Raciocinio complexo, codigo critico e decomposicao de demandas.",
    ),
    "gpt4o": LLMConfig(
        name="gpt4o",
        provider=Provider.OPENAI,
        model="gpt-4o",
        api_key_env="OPENAI_API_KEY",
        strengths=["long_form_writing", "copywriting", "seo_content", "creative_text", "translation"],
        cost_per_1k_input=0.0025,
        cost_per_1k_output=0.010,
        max_tokens=8192,
        role="Redator e copywriter. Conteudo longo, SEO, traducao e texto criativo.",
    ),
    "gemini": LLMConfig(
        name="gemini",
        provider=Provider.GOOGLE,
        model="gemini-2.5-pro",
        api_key_env="GOOGLE_AI_API_KEY",
        strengths=["deep_analysis", "bulk_processing", "summarization", "classification", "reasoning"],
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.005,
        max_tokens=16384,
        role="Analista profundo. Processamento em massa, resumos, classificacao e raciocinio avancado.",
    ),
    "perplexity": LLMConfig(
        name="perplexity",
        provider=Provider.PERPLEXITY,
        model="sonar-pro",
        api_key_env="PERPLEXITY_API_KEY",
        strengths=["live_research", "fact_checking", "citations", "web_search"],
        # Sprint 5 (2026-04-08): aligned to catalog/model_catalog.yaml SoT.
        # Antes: 0.001/0.001 (incorreto — subestimava custo real ~3x).
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_tokens=4096,
        role="Pesquisador. Busca ao vivo com fontes, verificacao de fatos e citacoes.",
    ),
    "groq": LLMConfig(
        name="groq",
        provider=Provider.GROQ,
        model="llama-3.3-70b-versatile",
        api_key_env="GROQ_API_KEY",
        strengths=["ultra_fast_inference", "code_review", "quick_analysis", "translation", "summarization"],
        cost_per_1k_input=0.00059,
        cost_per_1k_output=0.00079,
        max_tokens=8192,
        role="Velocista. Inferencia ultra-rapida (~10x mais rapido que outros). Ideal para tarefas que precisam de velocidade: triagem, classificacao, traducao, resumos rapidos, code review leve.",
    ),
    # Tier interno Claude (adicionado 2026-04-07 sprint 2):
    # downgrade automatico via Router._downgrade_claude_by_complexity.
    # Mantem familia Claude (mesma qualidade de raciocinio) mas no tier
    # de custo certo para complexity low/medium.
    "claude_sonnet": LLMConfig(
        name="claude_sonnet",
        provider=Provider.ANTHROPIC,
        model="claude-sonnet-4-6",
        api_key_env="ANTHROPIC_API_KEY",
        strengths=["balanced_reasoning", "code_review", "writing_long_form", "moderate_architecture"],
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        max_tokens=8192,
        role="Sonnet 4.6 — tier intermediario da familia Claude. 5x mais barato que Opus, mantendo 90%+ da qualidade para tarefas medium-complexity.",
    ),
    "claude_haiku": LLMConfig(
        name="claude_haiku",
        provider=Provider.ANTHROPIC,
        model="claude-haiku-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        strengths=["fast_inference", "classification", "summarization", "simple_code"],
        cost_per_1k_input=0.0008,
        cost_per_1k_output=0.004,
        max_tokens=8192,
        role="Haiku 4.5 — tier mais barato da familia Claude. ~19x mais barato que Opus, ideal para low-complexity (triagem, classificacao, summarization).",
    ),
}

# Sprint 7 (2026-04-08): tenta substituir LLM_CONFIGS pelo catalog runtime.
# Se PyYAML estiver disponivel e o catalog YAML existir, usamos ele como SoT.
# Caso contrario, fallback para o dict hardcoded acima (mantem retro-compat).
# As metadatas `strengths` e `role` continuam vindo dos defaults estaticos.
# Set GEO_DISABLE_CATALOG_RUNTIME=1 para forcar uso do dict hardcoded.
if not os.environ.get("GEO_DISABLE_CATALOG_RUNTIME"):
    try:
        from .catalog_loader import build_llm_configs_from_catalog  # noqa: E402
        _strengths = {k: cfg.strengths for k, cfg in LLM_CONFIGS.items()}
        _roles = {k: cfg.role for k, cfg in LLM_CONFIGS.items()}
        _from_catalog = build_llm_configs_from_catalog(
            strengths_overrides=_strengths,
            role_overrides=_roles,
        )
        if _from_catalog and len(_from_catalog) >= len(LLM_CONFIGS):
            LLM_CONFIGS = _from_catalog
            logger.info(
                "config: LLM_CONFIGS carregado do catalog YAML (%d aliases)",
                len(LLM_CONFIGS),
            )
    except Exception as _exc:
        logger.warning(
            "config: catalog runtime nao disponivel (fallback para dict hardcoded): %s",
            _exc,
        )


# ---------------------------------------------------------------------------
# Task-type -> LLM routing table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskRouting:
    """Primary and fallback LLM for a task type."""
    primary: str
    fallback: str


TASK_TYPES: dict[str, TaskRouting] = {
    "research":        TaskRouting(primary="perplexity", fallback="gemini"),
    "analysis":        TaskRouting(primary="gemini",     fallback="groq"),
    "writing":         TaskRouting(primary="gpt4o",      fallback="claude"),
    "copywriting":     TaskRouting(primary="gpt4o",      fallback="claude"),
    "code":            TaskRouting(primary="claude",      fallback="gpt4o"),
    "review":          TaskRouting(primary="claude",      fallback="groq"),
    "seo":             TaskRouting(primary="gpt4o",       fallback="perplexity"),
    "data_processing": TaskRouting(primary="gemini",      fallback="groq"),
    "fact_check":      TaskRouting(primary="perplexity",  fallback="gemini"),
    "classification":  TaskRouting(primary="groq",        fallback="gemini"),
    "translation":     TaskRouting(primary="groq",        fallback="gpt4o"),
    "summarization":   TaskRouting(primary="groq",        fallback="gemini"),
}


# ---------------------------------------------------------------------------
# Model tiers: cost-performance routing by complexity
# ---------------------------------------------------------------------------

MODEL_TIERS: dict[str, list[str]] = {
    # Tier 1 (cheap, fast): classification, summarization, simple analysis
    "low": ["gemini", "claude"],       # Gemini Flash is cheapest; Claude Haiku would go here too
    # Tier 2 (balanced): writing, research
    "medium": ["gpt4o", "perplexity"],
    # Tier 3 (premium): complex code, architecture, critical review
    "high": ["claude", "gpt4o"],
}


# ---------------------------------------------------------------------------
# Fallback chains per task type (ordered priority list)
# ---------------------------------------------------------------------------

FALLBACK_CHAINS: dict[str, list[str]] = {
    "research":        ["perplexity", "gpt4o", "gemini", "claude"],
    "writing":         ["gpt4o", "claude", "perplexity", "gemini"],
    "copywriting":     ["gpt4o", "claude", "perplexity", "gemini"],
    "code":            ["claude", "gpt4o", "gemini", "perplexity"],
    "review":          ["claude", "gpt4o", "gemini", "perplexity"],
    "analysis":        ["gemini", "claude", "gpt4o", "perplexity"],
    "seo":             ["gpt4o", "perplexity", "claude", "gemini"],
    "data_processing": ["gemini", "gpt4o", "claude", "perplexity"],
    "fact_check":      ["perplexity", "gemini", "claude", "gpt4o"],
    "classification":  ["gemini", "claude", "gpt4o", "perplexity"],
    "translation":     ["gpt4o", "gemini", "claude", "perplexity"],
    "summarization":   ["gemini", "gpt4o", "claude", "perplexity"],
}


# ---------------------------------------------------------------------------
# Timeout tiers by task type (seconds)
# ---------------------------------------------------------------------------

TIMEOUT_BY_TASK_TYPE: dict[str, float] = {
    "research":        60.0,   # Perplexity needs time to search the web
    "writing":        120.0,   # Long-form content generation
    "copywriting":    120.0,   # Long-form content generation
    "code":           300.0,   # Complex code generation (Claude needs time)
    "architecture":   300.0,   # System design (Claude needs time)
    "code_generation":300.0,   # Complex code generation (Claude needs time)
    "review":         120.0,   # Detailed review takes time
    "seo":             60.0,   # SEO analysis
    "analysis":        45.0,   # Analytical tasks
    "classification":  30.0,   # Quick classification
    "summarization":   30.0,   # Fast summarization
    "data_processing": 45.0,   # Bulk processing
    "fact_check":      60.0,   # Needs web search
    "translation":     60.0,   # Moderate
}

# Default timeout for unknown task types
DEFAULT_TIMEOUT: float = 120.0


# ---------------------------------------------------------------------------
# Budget and output settings
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

# Maximum allowed cost (USD) per single orchestration run
BUDGET_LIMIT: float = float(os.environ.get("GEO_BUDGET_LIMIT", "5.00"))

# Base output directory (relative to project root)
OUTPUT_DIR: Path = Path(os.environ.get("GEO_OUTPUT_DIR", "output"))

# Cache TTL in seconds (24 hours default)
CACHE_TTL_SECONDS: int = int(os.environ.get("GEO_CACHE_TTL", str(24 * 3600)))

# Context summarization threshold (chars)
CONTEXT_SUMMARIZE_THRESHOLD: int = 2000

# Average cost estimate per LLM call (used for pre-execution budget check).
# Sprint 4 (2026-04-07) — recalibrado para incluir tier interno Claude.
# Sprint 3 baixou tanto o custo real (downgrade Opus->Sonnet/Haiku) que o
# cost_estimate_accuracy ficou em 0.13-0.24x, fora da banda saudavel
# 0.7-1.5x. Adicionando entradas de Sonnet/Haiku alem de fazer smart_router
# aplicar downgrade no pre_check, a estimativa volta para a banda.
#
# Valores baseados em medias reais dos runs #2-#5 (com tier interno):
# - claude (Opus 4.6): ~$0.10/call em tarefas high (descricoes longas)
# - claude_sonnet 4.6: ~$0.025/call (tarefas medium, ~5x mais barato)
# - claude_haiku 4.5: ~$0.005/call (tarefas low, ~19x mais barato)
# - gpt4o: ~$0.015/call (mantido)
# - gemini 2.5 Pro: ~$0.005/call (mantido)
# - perplexity sonar-pro: ~$0.008/call (mantido)
# - groq llama 3.3 70B: ~$0.001/call (mantido)
AVG_COST_PER_CALL: dict[str, float] = {
    "claude":        0.10,
    "claude_sonnet": 0.025,
    "claude_haiku":  0.005,
    "gpt4o":         0.015,
    "gemini":        0.005,
    "perplexity":    0.008,
    "groq":          0.001,
}


# ---------------------------------------------------------------------------
# FinOps: Per-provider daily budget limits (USD)
# ---------------------------------------------------------------------------

FINOPS_DAILY_LIMITS: dict[str, float] = {
    "anthropic":  float(os.environ.get("FINOPS_LIMIT_ANTHROPIC", "2.00")),
    "openai":     float(os.environ.get("FINOPS_LIMIT_OPENAI", "2.00")),
    "google":     float(os.environ.get("FINOPS_LIMIT_GOOGLE", "1.00")),   # Billing ativo (R$500 credito)
    "perplexity": float(os.environ.get("FINOPS_LIMIT_PERPLEXITY", "1.00")),
    "groq":       float(os.environ.get("FINOPS_LIMIT_GROQ", "2.00")),     # Free tier generoso (300 RPM)
}

# Global daily budget (sum of all providers, with safety margin)
FINOPS_DAILY_GLOBAL: float = float(os.environ.get("FINOPS_LIMIT_GLOBAL", "8.00"))

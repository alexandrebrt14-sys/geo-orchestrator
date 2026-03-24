"""Configuration for the multi-LLM orchestrator.

Defines LLM providers, model settings, task type routing, and cost parameters.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class Provider(str, Enum):
    """Supported LLM providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"
    PERPLEXITY = "perplexity"


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
        return os.environ.get(self.api_key_env)

    @property
    def available(self) -> bool:
        return bool(self.api_key)


# ---------------------------------------------------------------------------
# LLM configurations
# ---------------------------------------------------------------------------

LLM_CONFIGS: dict[str, LLMConfig] = {
    "claude": LLMConfig(
        name="claude",
        provider=Provider.ANTHROPIC,
        model="claude-opus-4-6-20250415",
        api_key_env="ANTHROPIC_API_KEY",
        strengths=["architecture", "complex_code", "critical_review", "reasoning"],
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        max_tokens=4096,
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
        max_tokens=4096,
        role="Redator e copywriter. Conteudo longo, SEO, traducao e texto criativo.",
    ),
    "gemini": LLMConfig(
        name="gemini",
        provider=Provider.GOOGLE,
        model="gemini-2.5-flash",
        api_key_env="GOOGLE_AI_API_KEY",
        strengths=["fast_analysis", "bulk_processing", "summarization", "classification", "cheap"],
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
        max_tokens=8192,
        role="Analista rapido. Processamento em massa, resumos, classificacao e triagem de dados.",
    ),
    "perplexity": LLMConfig(
        name="perplexity",
        provider=Provider.PERPLEXITY,
        model="sonar",
        api_key_env="PERPLEXITY_API_KEY",
        strengths=["live_research", "fact_checking", "citations", "web_search"],
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.001,
        max_tokens=4096,
        role="Pesquisador. Busca ao vivo com fontes, verificacao de fatos e citacoes.",
    ),
}


# ---------------------------------------------------------------------------
# Task-type → LLM routing table
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaskRouting:
    """Primary and fallback LLM for a task type."""
    primary: str
    fallback: str


TASK_TYPES: dict[str, TaskRouting] = {
    "research":        TaskRouting(primary="perplexity", fallback="gemini"),
    "analysis":        TaskRouting(primary="gemini",     fallback="claude"),
    "writing":         TaskRouting(primary="gpt4o",      fallback="claude"),
    "copywriting":     TaskRouting(primary="gpt4o",      fallback="claude"),
    "code":            TaskRouting(primary="claude",      fallback="gpt4o"),
    "review":          TaskRouting(primary="claude",      fallback="gpt4o"),
    "seo":             TaskRouting(primary="gpt4o",       fallback="perplexity"),
    "data_processing": TaskRouting(primary="gemini",      fallback="gpt4o"),
    "fact_check":      TaskRouting(primary="perplexity",  fallback="gemini"),
    "classification":  TaskRouting(primary="gemini",      fallback="claude"),
    "translation":     TaskRouting(primary="gpt4o",       fallback="gemini"),
    "summarization":   TaskRouting(primary="gemini",      fallback="gpt4o"),
}


# ---------------------------------------------------------------------------
# Budget and output settings
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

# Maximum allowed cost (USD) per single orchestration run
BUDGET_LIMIT: float = float(os.environ.get("GEO_BUDGET_LIMIT", "1.00"))

# Base output directory (relative to project root)
OUTPUT_DIR: Path = Path(os.environ.get("GEO_OUTPUT_DIR", "output"))

# Cache TTL in seconds (24 hours default)
CACHE_TTL_SECONDS: int = int(os.environ.get("GEO_CACHE_TTL", str(24 * 3600)))

# Context summarization threshold (chars)
CONTEXT_SUMMARIZE_THRESHOLD: int = 2000

# Average cost estimate per LLM call (used for pre-execution budget check)
AVG_COST_PER_CALL: dict[str, float] = {
    "claude":     0.04,
    "gpt4o":      0.012,
    "gemini":     0.001,
    "perplexity": 0.005,
}

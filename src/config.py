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
    # 2026-05-17 — 6o provider canonico. xAI Grok (com K) e diferente de
    # Groq Inc (com Q, ja em uso acima). xAI tem modelos proprios (grok-4.3,
    # grok-4.20-*) com diferencial unico de busca live em X/Twitter via
    # search_parameters. API OpenAI-compatible em https://api.x.ai/v1.
    # Conta canonica: alexandre.brt14@gmail.com (team caramaschigeo).
    XAI = "xai"


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
        model="claude-opus-4-7",
        api_key_env="ANTHROPIC_API_KEY",
        strengths=["architecture", "complex_code", "critical_review", "reasoning"],
        cost_per_1k_input=0.015,
        cost_per_1k_output=0.075,
        max_tokens=32000,
        role="Arquiteto e revisor principal. Raciocinio complexo, codigo critico e decomposicao de demandas.",
    ),
    "gpt4o": LLMConfig(
        # 2026-05-17 v4 — upgrade canonico para gpt-5.5 (lancado 23/04/2026,
        # versao mais avancada estavel). Mantemos a chave "gpt4o" por
        # compatibilidade reversa com routers, mas o model id e atualizado.
        # gpt-4o (2024-08) ficou 9 meses defasado; gpt-5.5 traz: contexto
        # 1M tokens nativo, reasoning interno otimizado para copywriting longo,
        # e melhor adequacao a pt-BR. Pricing oficial (OpenAI 23/04/2026).
        name="gpt4o",
        provider=Provider.OPENAI,
        model=os.environ.get("OPENAI_MODEL", "gpt-5.5"),
        api_key_env="OPENAI_API_KEY",
        strengths=["long_form_writing", "copywriting", "seo_content", "creative_text", "translation", "reasoning"],
        cost_per_1k_input=0.005,
        cost_per_1k_output=0.015,
        max_tokens=32768,
        role="Redator e copywriter. Conteudo longo, SEO, traducao e texto criativo. Reasoning nativo a partir de gpt-5.5.",
    ),
    "gemini": LLMConfig(
        name="gemini",
        provider=Provider.GOOGLE,
        # 2026-05-02 v3 — modelo configuravel via env. gemini-2.5-pro continua
        # default (pesado, raciocinio profundo) mas pode ser baixado para flash
        # via GEMINI_MODEL=gemini-2.5-flash em janelas de outage 503 sustentado
        # do tier Pro (probe direto: 60% taxa de 503 em 2026-05-02).
        model=os.environ.get("GEMINI_MODEL", "gemini-2.5-pro"),
        api_key_env="GOOGLE_AI_API_KEY",
        strengths=["deep_analysis", "bulk_processing", "summarization", "classification", "reasoning"],
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.005,
        max_tokens=65536,
        role="Analista profundo. Processamento em massa, resumos, classificacao e raciocinio avancado.",
    ),
    # 2026-05-02 v3 — Tier Flash do Google. Adicionado depois do diagnostico
    # de 503 sustentado em gemini-2.5-pro (saturacao compartilhada do tier
    # standard do Google AI). Flash mantem 1M ctx, latencia ~3x menor e custo
    # ~5x menor que Pro. Usado como primary em tasks medium (analysis, data
    # processing, fact_check fallback, classification, summarization,
    # extraction). Pro continua reservado para code/architecture/decomposition
    # onde a inteligencia adicional vale o premium.
    "gemini_flash": LLMConfig(
        name="gemini_flash",
        provider=Provider.GOOGLE,
        model=os.environ.get("GEMINI_FLASH_MODEL", "gemini-2.5-flash"),
        api_key_env="GOOGLE_AI_API_KEY",
        strengths=["fast_inference", "bulk_processing", "summarization", "classification", "data_processing"],
        cost_per_1k_input=0.00030,
        cost_per_1k_output=0.0025,
        max_tokens=65536,
        role="Gemini Flash — analise rapida, bulk processing, classificacao e resumos. ~5x mais barato que Pro com 1M ctx mantido. Tier de protecao quando Pro entra em outage 503 sustentado.",
    ),
    "perplexity": LLMConfig(
        name="perplexity",
        provider=Provider.PERPLEXITY,
        # 2026-04-14: upgrade sonar-pro -> sonar-deep-research para tarefas
        # complexas (ebook, curso). Pricing oficial Perplexity: $2/M input,
        # $8/M output + $3/M reasoning + $5 per 1000 searches.
        model="sonar-deep-research",
        api_key_env="PERPLEXITY_API_KEY",
        strengths=["deep_research", "live_research", "fact_checking", "citations", "web_search"],
        cost_per_1k_input=0.002,
        cost_per_1k_output=0.008,
        max_tokens=8192,
        role="Pesquisador profundo. Deep research com fontes multiplas, verificacao rigorosa e citacoes academicas.",
    ),
    "groq": LLMConfig(
        name="groq",
        provider=Provider.GROQ,
        model=os.environ.get("GROQ_DEFAULT_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        api_key_env="GROQ_API_KEY",
        strengths=["ultra_fast_inference", "code_review", "quick_analysis", "translation", "summarization"],
        # 2026-05-17 — upgrade Llama 3.3 70B ($0.59/$0.79) -> Llama 4 Scout
        # 17B 16E Instruct ($0.11/$0.34) — 5x mais barato com qualidade
        # comparable em classification/summarization/code_review leve.
        cost_per_1k_input=0.00011,
        cost_per_1k_output=0.00034,
        max_tokens=32768,
        role="Velocista. Inferencia ultra-rapida (~10x mais rapido que outros). Ideal para tarefas que precisam de velocidade: triagem, classificacao, traducao, resumos rapidos, code review leve.",
    ),
    # 2026-05-02 — Tier de raciocinio Groq (modelo grande, ainda ultra-rapido).
    # Override por env var GROQ_HEAVY_MODEL. Default aponta para llama-3.3-70b
    # como compatibilidade segura; trocar para openai/gpt-oss-120b ou
    # qwen/qwen3-32b quando o usuario quiser ativar potencia maxima Groq.
    # Pricing aproximado mantem a faixa Groq (ainda ~10x mais barato que Opus).
    "groq_heavy": LLMConfig(
        name="groq_heavy",
        provider=Provider.GROQ,
        model=os.environ.get("GROQ_HEAVY_MODEL", "openai/gpt-oss-120b"),
        api_key_env="GROQ_API_KEY",
        strengths=["fast_reasoning", "code_review_heavy", "deep_classification", "structured_extraction", "decomposition"],
        cost_per_1k_input=0.00150,
        cost_per_1k_output=0.00200,
        max_tokens=32768,
        role="Groq Heavy — modelo de raciocinio ainda na infra Groq. Para sub-reviews codigo, decomposicao auxiliar, classificacao profunda e extracao estruturada. Mantem a velocidade Groq (~5-10x mais rapido que Opus) com qualidade de raciocinio compativel para tarefas medium-high.",
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
        max_tokens=64000,
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
        max_tokens=64000,
        role="Haiku 4.5 — tier mais barato da familia Claude. ~19x mais barato que Opus, ideal para low-complexity (triagem, classificacao, summarization).",
    ),
    # 2026-05-17 — Familia xAI Grok adicionada como 6o provider canonico.
    # Conta: alexandre.brt14@gmail.com / team caramaschigeo (xAI Console).
    # API OpenAI-compatible em https://api.x.ai/v1. Pricing flat $1.25/$2.50
    # por 1M tokens em toda linha GA (4.3 e 4.20-*). Diferenciacao por
    # capability/context, nao por preco. Diferencial unico: search_parameters
    # com busca live em X/Twitter (nenhum outro provider tem).
    "grok": LLMConfig(
        name="grok",
        provider=Provider.XAI,
        model=os.environ.get("XAI_MODEL", "grok-4.3"),
        api_key_env="XAI_API_KEY",
        strengths=["realtime_search", "live_x_data", "reasoning", "long_context_1m", "structured_outputs"],
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.00250,
        max_tokens=131072,  # ctx 1M; max output mantem em 131K para nao estourar finops
        role="Grok 4.3 — flagship xAI com busca live em X/Twitter (search_parameters), 1M context, reasoning + vision + function calling. Canal exclusivo para realtime_search, social_listening, brand_monitoring e current_events. Tom edgy por padrao — exigir prompt explicito de tom neutro quando preciso.",
    ),
    "grok_multi": LLMConfig(
        name="grok_multi",
        provider=Provider.XAI,
        model=os.environ.get("XAI_MULTI_MODEL", "grok-4.20-multi-agent-0309"),
        api_key_env="XAI_API_KEY",
        strengths=["multi_agent_decomposition", "long_context_2m", "deep_reasoning", "cross_check"],
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.00250,
        max_tokens=131072,
        role="Grok Multi-Agent — 4 agentes paralelos (Grok+Harper+Benjamin+Lucas) com 2M context. Para multi_perspective_decomposition, long_context_synthesis e cross-check de research. Substitui o slot 'grok_heavy' do brief original (nao confundir com 'groq_heavy' da Groq Inc).",
    ),
    "grok_fast": LLMConfig(
        name="grok_fast",
        provider=Provider.XAI,
        model=os.environ.get("XAI_FAST_MODEL", "grok-4.20-0309-non-reasoning"),
        api_key_env="XAI_API_KEY",
        strengths=["fast_inference", "classification", "extraction", "live_search_quick"],
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.00250,
        max_tokens=131072,
        role="Grok 4.20 non-reasoning — variante rapida sem CoT interno. Para classificacao, extracao e respostas curtas onde reasoning explicito nao agrega. NAO substitui Groq (com Q) para bulk: Groq Llama 3.3 70B mantem vantagem ~10x em throughput LPU.",
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


# 2026-05-02 v3 — SPLIT GEMINI PRO/FLASH POS-DIAGNOSTICO 503 SUSTENTADO.
# Probe direto na API Google retornou 60% de 503 em gemini-2.5-pro (saturacao
# compartilhada do tier standard). gemini-2.5-flash 100% saudavel mesma chave.
# Diretriz: Pro continua em tasks que precisam de raciocinio premium (code,
# architecture/critical_review fallback, decomposition fallback, code_review,
# writing/copywriting fallback, research fallback). Flash entra em tasks
# medium-economy (analysis, review fallback, data_processing, fact_check
# fallback, classification fallback, summarization fallback, extraction
# fallback). Mantem regra dura cross-provider top-2.
TASK_TYPES: dict[str, TaskRouting] = {
    "research":         TaskRouting(primary="perplexity",    fallback="gemini"),
    "analysis":         TaskRouting(primary="gemini_flash",  fallback="groq_heavy"),
    # 2026-05-17 Sprint 12 — diretriz canonica COPY PREMIUM ONLY.
    # writing/copywriting/seo so podem cair em modelos TOP-tier: gpt-5.5 (OpenAI
    # flagship), claude-opus-4-7 (Anthropic flagship) ou gemini-2.5-pro (Google
    # flagship). NUNCA cair em Sonnet/Haiku/Flash em copy — qualidade editorial
    # do copy de Alexandre exige modelos com reasoning nativo + 1M ctx + voz
    # PT-BR de alta densidade. Sonnet/Haiku/Flash continuam disponiveis em
    # outras fases (decomposition, classification, summarization, fallback de
    # ultimo recurso em FALLBACK_CHAINS) mas SEMPRE depois dos 3 premium.
    "writing":          TaskRouting(primary="gpt4o",         fallback="claude"),
    "copywriting":      TaskRouting(primary="gpt4o",         fallback="claude"),
    "code":             TaskRouting(primary="gemini",        fallback="claude_sonnet"),
    "review":           TaskRouting(primary="groq_heavy",    fallback="gemini_flash"),
    "architecture":     TaskRouting(primary="claude",        fallback="gemini"),
    "critical_review":  TaskRouting(primary="claude",        fallback="gemini"),
    "decomposition":    TaskRouting(primary="claude_sonnet", fallback="gemini"),
    "code_review":      TaskRouting(primary="groq_heavy",    fallback="claude_sonnet"),
    "seo":              TaskRouting(primary="gpt4o",         fallback="claude"),
    "data_processing":  TaskRouting(primary="gemini_flash",  fallback="groq"),
    "fact_check":       TaskRouting(primary="perplexity",    fallback="gemini_flash"),
    "classification":   TaskRouting(primary="groq",          fallback="gemini_flash"),
    "translation":      TaskRouting(primary="groq",          fallback="gpt4o"),
    "summarization":    TaskRouting(primary="groq",          fallback="gemini_flash"),
    "extraction":       TaskRouting(primary="groq_heavy",    fallback="gemini_flash"),
    # ===========================================================
    # 2026-05-17 — NOVOS TASK TYPES exclusivos da familia xAI Grok
    # ===========================================================
    # Grok tem o unico canal de busca live em X/Twitter (search_parameters);
    # Perplexity faz live web mas nao alcanca timeline X em tempo real.
    "realtime_search":  TaskRouting(primary="grok",          fallback="perplexity"),
    "social_listening": TaskRouting(primary="grok",          fallback="perplexity"),
    "current_events":   TaskRouting(primary="grok",          fallback="perplexity"),
    "brand_monitoring": TaskRouting(primary="grok",          fallback="perplexity"),
    # 2026-05-17 — Multi-agent decomposition usa Grok multi-agent nativo
    # (4 agentes paralelos no mesmo call) em vez de decomposition via Claude.
    "multi_perspective_decomposition": TaskRouting(primary="grok_multi", fallback="claude_sonnet"),
    # 2026-05-17 — Long context synthesis (>500K tokens) — Grok multi-agent
    # (2M ctx) vs Gemini 2.5 Pro (2M ctx). Grok ganha quando precisa
    # cross-check com timeline social; Gemini ganha em texto puro.
    "long_context_synthesis": TaskRouting(primary="grok_multi", fallback="gemini"),
}


# ---------------------------------------------------------------------------
# Model tiers: cost-performance routing by complexity
# ---------------------------------------------------------------------------

MODEL_TIERS: dict[str, list[str]] = {
    # Tier 1 (cheap, fast): classification, summarization, simple analysis
    "low": ["groq", "claude_haiku", "gemini_flash"],
    # Tier 2 (balanced): writing, code, review, deep analysis
    "medium": ["gemini_flash", "groq_heavy", "gpt4o", "claude_sonnet"],
    # Tier 3 (premium): architecture e critical review apenas
    "high": ["claude", "gemini", "gpt4o"],
}


# ---------------------------------------------------------------------------
# Fallback chains per task type (ordered priority list)
# ---------------------------------------------------------------------------

# 2026-04-14: cobertura 5/5 — cada chain inclui TODOS os 5 providers canonicos.
# 2026-05-17: cobertura agora 6/6 com xAI Grok. Chains de task types novos
# (realtime_search/social_listening/current_events/brand_monitoring/multi_*)
# tem grok/grok_multi no 1o slot. Demais chains mantem 5 providers + grok
# como fallback de ultimo recurso (custo similar ao Opus).
# Garante que se 4 falharem, o 5o ainda executa (graceful degradation total).
# 2026-05-02 v2 — REGRA DURA: os 2 primeiros slots de cada chain sao de
# providers DIFERENTES (cross-provider diversity). Quando o primary cai,
# o 1o fallback nao depende da mesma infra. Reduz o "single provider, single
# point of failure" do rebalance original.
FALLBACK_CHAINS: dict[str, list[str]] = {
    # 2026-05-17 Sprint 12 — research/fact_check com Perplexity como PRIORIDADE
    # absoluta. Em research profunda, Perplexity sonar-deep-research e o unico
    # com live web search + citacoes academicas verificaveis. Cap voltou
    # 0.35 -> 0.50 (era do tempo da bateria 360 quando 1 task domava 84% wall
    # time). Sprint 12 prioriza qualidade de fonte sobre folga de cap; o cap
    # mais largo nao implica concentracao — quase todas as runs uso Perplexity
    # em 1-2 tasks, nao em 50% do plano.
    "research":         ["perplexity",    "gemini",        "claude",        "gpt4o",         "claude_sonnet", "groq_heavy"],
    # 2026-05-17 Sprint 12 — DIRETRIZ COPY PREMIUM ONLY.
    # Os 3 primeiros slots de writing/copywriting/seo SAO TODOS premium-tier:
    # gpt-5.5 (OpenAI flagship 1M ctx + reasoning nativo), claude-opus-4-7
    # (Anthropic flagship $15/$75 raciocinio profundo + voz editorial), e
    # gemini-2.5-pro (Google flagship 1M ctx). Perplexity entra no 4o slot
    # por fornecer citacoes ao vivo (util em copy autoral com fatos novos).
    # claude_sonnet e groq_heavy so como ultimo recurso em outage simultaneo
    # dos 4 flagships acima. NUNCA Flash/Haiku em copy.
    "writing":          ["gpt4o",         "claude",        "gemini",        "perplexity",    "claude_sonnet", "groq_heavy"],
    "copywriting":      ["gpt4o",         "claude",        "gemini",        "perplexity",    "claude_sonnet", "groq_heavy"],
    "code":             ["gemini",        "claude_sonnet", "groq_heavy",    "gpt4o",         "claude",       "groq"],
    "review":           ["groq_heavy",    "gemini_flash",  "claude_sonnet", "gpt4o",         "claude",       "groq"],
    "architecture":     ["claude",        "gemini",        "gpt4o",         "claude_sonnet", "groq_heavy",   "groq"],
    "critical_review":  ["claude",        "gpt4o",         "gemini",        "claude_sonnet", "groq_heavy",   "groq"],
    "decomposition":    ["claude_sonnet", "gemini",        "gpt4o",         "groq_heavy",    "claude",       "groq"],
    "code_review":      ["groq_heavy",    "claude_sonnet", "gemini",        "gpt4o",         "claude",       "groq"],
    "analysis":         ["gemini_flash",  "groq_heavy",    "gpt4o",         "claude_sonnet", "perplexity",   "groq"],
    "seo":              ["gpt4o",         "claude",        "gemini",        "perplexity",    "claude_sonnet", "groq_heavy"],
    "data_processing":  ["gemini_flash",  "groq_heavy",    "gpt4o",         "claude_sonnet", "perplexity",   "groq"],
    "fact_check":       ["perplexity",    "gemini_flash",  "gpt4o",         "claude_sonnet", "groq_heavy",   "groq"],
    "classification":   ["groq",          "gemini_flash",  "claude_haiku",  "groq_heavy",    "gpt4o",        "perplexity"],
    "translation":      ["groq",          "gpt4o",         "gemini_flash",  "claude_haiku",  "groq_heavy",   "perplexity"],
    "summarization":    ["groq",          "gemini_flash",  "claude_haiku",  "groq_heavy",    "gpt4o",        "perplexity"],
    "extraction":       ["groq_heavy",    "gemini_flash",  "claude_sonnet", "gpt4o",         "groq",         "claude_haiku"],
    # 2026-05-17 — fallback chains para os 5 task types novos da familia xAI.
    # Mantem cross-provider diversity: 1o slot xAI, 2o slot Perplexity (live web),
    # demais slots em providers diferentes para graceful degradation total.
    "realtime_search":  ["grok",          "perplexity",    "gemini_flash",  "gpt4o",         "claude_sonnet", "groq"],
    "social_listening": ["grok",          "perplexity",    "gpt4o",         "gemini_flash",  "claude_sonnet", "groq"],
    "current_events":   ["grok",          "perplexity",    "gemini_flash",  "gpt4o",         "claude_sonnet", "groq"],
    "brand_monitoring": ["grok",          "perplexity",    "gpt4o",         "gemini_flash",  "claude_sonnet", "groq"],
    "multi_perspective_decomposition": ["grok_multi", "claude_sonnet", "gemini",   "gpt4o",         "groq_heavy",   "groq"],
    "long_context_synthesis":          ["grok_multi", "gemini",        "claude",   "gpt4o",         "groq_heavy",   "perplexity"],
}


# ---------------------------------------------------------------------------
# Timeout tiers by task type (seconds)
# ---------------------------------------------------------------------------

# 2026-04-14: timeouts elevados — demandas profundas (ebook, curso, research
# academico) travavam em sonar-deep-research (60s nao basta) e em analises
# multi-step do Gemini 2.5 Pro com thinking mode. Run bzqofvp7j perdeu 2/10
# tasks por timeout. Nova banda dimensiona para deep work sem estourar UX.
TIMEOUT_BY_TASK_TYPE: dict[str, float] = {
    "research":        240.0,  # sonar-deep-research pode levar 2-4min por query profunda
    "writing":        180.0,   # ebook/curso: +50% para long-form denso
    "copywriting":    180.0,
    "code":           300.0,   # Gemini 2.5 Pro como primary; Opus so como fallback critico
    "architecture":   420.0,   # Opus com raciocinio arquitetural
    "code_generation":300.0,
    "code_review":    120.0,   # Groq Heavy ultra-rapido para code review
    "review":         180.0,   # Gemini 2.5 Pro como primary
    "critical_review":300.0,   # Opus reservado para review critico
    "decomposition":  120.0,   # Gemini decomposition (substitui Sonnet)
    "extraction":      90.0,   # Groq Heavy para extracao estruturada
    "seo":            120.0,
    "analysis":       150.0,   # Gemini 2.5 Pro thinking mode + groq_heavy fallback
    "classification":  45.0,
    "summarization":   45.0,
    "data_processing": 90.0,
    "fact_check":     180.0,   # fact_check tambem usa sonar/perplexity
    "translation":     90.0,
    # 2026-05-17 — timeouts para task types xAI Grok.
    "realtime_search":  120.0,  # Grok com search_parameters: auto pode levar ~30-90s
    "social_listening": 120.0,
    "current_events":   120.0,
    "brand_monitoring": 120.0,
    "multi_perspective_decomposition": 240.0,  # multi-agent 4-paralelo demora
    "long_context_synthesis":          300.0,  # 2M ctx + multi-agent
}

# Default timeout for unknown task types
DEFAULT_TIMEOUT: float = 180.0


# ---------------------------------------------------------------------------
# Budget and output settings
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

# Maximum allowed cost (USD) per single orchestration run.
# 2026-04-14: elevado de $5 para $15 — demandas profundas (ebook, curso,
# deep research) chegavam perto do limite e bloqueavam early. $15 cobre
# run tipico com Opus + sonar-deep-research sem cortar potencia.
BUDGET_LIMIT: float = float(os.environ.get("GEO_BUDGET_LIMIT", "15.00"))

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
    "gemini_flash":  0.001,   # ~5x mais barato que Pro (Flash 2.5: $0.30/$2.50 por M tok)
    # 2026-05-13: recalibrado de $0.008 para $0.05. Bateria 360 mostrou
    # sonar-deep-research em research profunda gasta $0.04-0.07/call (5-9x
    # acima do default antigo). Calibrator rejeitava amostras reais por
    # ratio 8.6x do limite saudavel; valor agora reflete uso real.
    "perplexity":    0.05,
    "groq":          0.001,
    "groq_heavy":    0.0025,
    # 2026-05-17 — xAI Grok adicionada. Pricing flat $1.25/$2.50 por 1M tokens.
    # Estimativa conservadora ~$0.008/call para grok 4.3 em uso tipico
    # (5k tokens in + 1k out). grok_multi pode ir a $0.015 com 2M ctx;
    # grok_fast em $0.005 para classificacao/extracao curta.
    "grok":          0.008,
    "grok_multi":    0.015,
    "grok_fast":     0.005,
}


# ---------------------------------------------------------------------------
# FinOps: Per-provider daily budget limits (USD)
# ---------------------------------------------------------------------------

# 2026-04-14: limites diarios elevados. Anthropic estourava em 1 run profundo
# ($2/dia bloqueava Opus/Sonnet/Haiku simultaneamente). Nova banda permite
# 3-5 demandas profundas/dia sem bloquear top-tier. Cap 80% continua protegendo
# contra concentracao indevida; os limites abaixo sao o teto absoluto diario.
FINOPS_DAILY_LIMITS: dict[str, float] = {
    # 2026-05-09 — modo POTENCIA MAXIMA por ordem direta CEO Brasil GEO.
    # Caps elevados ~10x para suportar bateria de 5 waves com pesquisa profunda
    # (sonar-deep-research + Opus 32k + Gemini Pro 65k tokens) sem bloqueio.
    "anthropic":  float(os.environ.get("FINOPS_LIMIT_ANTHROPIC", "100.00")),
    "openai":     float(os.environ.get("FINOPS_LIMIT_OPENAI", "50.00")),
    "google":     float(os.environ.get("FINOPS_LIMIT_GOOGLE", "50.00")),
    "perplexity": float(os.environ.get("FINOPS_LIMIT_PERPLEXITY", "30.00")),
    "groq":       float(os.environ.get("FINOPS_LIMIT_GROQ", "30.00")),
    # 2026-05-17 — xAI Grok com cap conservador. Conta iniciada com USD 25
    # de credito (alexandre.brt14@gmail.com / team caramaschigeo). Cap diario
    # $15 deixa 40h+ de operacao continua antes de esgotar credito inicial.
    "xai":        float(os.environ.get("FINOPS_LIMIT_XAI", "15.00")),
}

# Global daily budget (sum of all providers, with safety margin).
# 2026-05-09: $30 -> $250 (modo potencia maxima).
FINOPS_DAILY_GLOBAL: float = float(os.environ.get("FINOPS_LIMIT_GLOBAL", "250.00"))


# ---------------------------------------------------------------------------
# Concentration caps por PROVIDER (2026-05-02)
# ---------------------------------------------------------------------------
#
# Antes: cap era por nome de LLM (claude, claude_sonnet, claude_haiku
# contavam separadamente, deixando Anthropic chegar a 90% facil).
# Agora: cap por provider familia. Anthropic (Opus+Sonnet+Haiku somados)
# nao pode passar de 30% das tasks por run; Gemini e Groq ganham folga
# para serem protagonistas em demandas tipicas.
# 2026-05-02 v2: caps re-balanceados para nao deixar nenhum provider unico
# dominar. google 0.55 -> 0.45 (estava amplo demais e amplificou o blast
# radius do outage); anthropic 0.30 -> 0.40 (Sonnet/Haiku precisam de
# espaco para cobrir wave 1 + reviews); openai 0.45 (mantido); perplexity
# 0.50 (mantido); groq 0.65 (mantido — ela e a vala comum cheap-and-fast).
PROVIDER_SHARE_CAP: dict[str, float] = {
    "anthropic":  float(os.environ.get("CAP_ANTHROPIC", "0.40")),
    "openai":     float(os.environ.get("CAP_OPENAI", "0.45")),
    "google":     float(os.environ.get("CAP_GOOGLE", "0.45")),
    # 2026-05-17 Sprint 12 — cap RESTAURADO 0.35 -> 0.50 por diretriz canonica
    # "Perplexity como prioridade em research". O cap de 0.35 (Sprint 10) era
    # justificado quando research nao tinha decomposicao mas sufocava deep
    # research em runs editoriais. Sprint 12 reverte para 0.50, mantendo a
    # decomposicao adaptativa do adaptive_decomposer como mecanismo principal
    # de controle de wall time (em vez de cap rigido). Perplexity sonar-deep
    # -research e o unico provider com citacoes academicas verificaveis +
    # live web — qualidade de fonte vence folga de cap.
    "perplexity": float(os.environ.get("CAP_PERPLEXITY", "0.50")),
    "groq":       float(os.environ.get("CAP_GROQ", "0.65")),
    # 2026-05-17 — xAI Grok com cap moderado inicial. Cuidado: pricing flat
    # $1.25/$2.50 nao tem tier "barato"; sem cap, demanda multi-task pode
    # estourar credito rapido. Conservador ate ter baseline real de uso.
    "xai":        float(os.environ.get("CAP_XAI", "0.30")),
}

# Mapa LLM-name -> provider (resolvido a partir de LLM_CONFIGS).
def llm_to_provider(llm_name: str) -> str:
    """Retorna o provider canonico (anthropic/openai/google/perplexity/groq)
    de um alias de LLM (claude, claude_sonnet, gemini, groq_heavy, etc)."""
    cfg = LLM_CONFIGS.get(llm_name)
    if cfg is None:
        return ""
    return cfg.provider.value

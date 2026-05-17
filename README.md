# geo-orchestrator

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![LLMs](https://img.shields.io/badge/LLMs-6_providers-ff6b35)
![Tests](https://img.shields.io/badge/tests-140%20passed-brightgreen.svg)
![Coverage](https://img.shields.io/badge/coverage-53%25-yellow.svg)
[![CI](https://github.com/alexandrebrt14-sys/geo-orchestrator/actions/workflows/tests.yml/badge.svg)](https://github.com/alexandrebrt14-sys/geo-orchestrator/actions/workflows/tests.yml)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

Multi-LLM orchestration pipeline for Generative Engine Optimization (GEO) content production. Receives a natural-language demand, decomposes it into atomic tasks via Claude Sonnet 4.6, routes each task to the most appropriate LLM (12 models across **6 providers**) based on **complexity-aware tier routing** + **provider concentration caps** + **diversity guarantee in COMPLEX plans** + adaptive scoring, and executes waves in parallel with caching, checkpoints, quality gates, FinOps governance and **WhatsApp/email alerts** on budget thresholds.

**12,500+ lines | 1,189+ calls tracked | 12 models / 6 providers | unified tracking via [geo-finops](https://github.com/alexandrebrt14-sys/geo-finops)**

> **Updated 2026-05-17 (Sprint 12) — DIRETRIZ CANÔNICA COPY PREMIUM ONLY + PERPLEXITY PRIORIDADE EM RESEARCH**.
> Quatro mudanças canônicas:
> 1. **Copy (`writing` / `copywriting` / `seo`)** só pode ser produzido por modelos **PREMIUM-tier**: `gpt-5.5` (OpenAI flagship, default), `claude-opus-4-7` (Anthropic flagship, 1º fallback) ou `gemini-2.5-pro` (Google flagship, 2º fallback). **Sonnet / Haiku / Flash banidos de copy** (qualidade editorial PT-BR exige reasoning nativo + 1M ctx). Refletido em `TASK_TYPES` + `FALLBACK_CHAINS` + `smart_router.upgrade_hints`.
> 2. **Research / fact_check** com **Perplexity sonar-deep-research como prioridade absoluta**. Cap por provider RESTAURADO `0.35 → 0.50` (era de Sprint 10/bateria 360 e sufocava deep research editorial). Fallback chain prioriza Gemini 2.5 Pro (1M ctx) → Opus 4.7 (raciocínio) → gpt-5.5; groq/groq_heavy só como último recurso.
> 3. **Catalog YAML sincronizado**: `gpt-4o` → `gpt-5.5` no catalog (estava em drift desde Sprint 11). Pricing $5.00/$15.00 por Mtok refletido em testes (`test_sprint7.py:catalog_provides_correct_costs`).
> 4. **Smart router hints atualizadas**: quando Anthropic ausente em plano COMPLEX, antes de promover decomposition → Sonnet, tenta promover `writing/copywriting/seo` → Opus 4.7. Mesma lógica em Gemini Pro para 3º tier de copy.
>
> **Updated 2026-05-17 (Sprint 11)** — Adicionado **xAI Grok (com K) como 6º provider canônico**, distinto de Groq Inc (com Q, chips LPU). 3 entradas Grok (`grok` grok-4.3 / `grok_multi` grok-4.20-multi-agent / `grok_fast` grok-4.20-non-reasoning). 6 task types novos exclusivos: realtime_search, social_listening, current_events, brand_monitoring, multi_perspective_decomposition, long_context_synthesis. Upgrades simultâneos: Claude Opus 4.6→4.7, Groq default Llama 3.3 70B → Llama 4 Scout 17B 16E, Groq Heavy default → openai/gpt-oss-120b. **Diversity guarantee em planos COMPLEX 5+ tasks** baseado em Mixture of Agents (Wang 2024) + DAAO (2509.11079) + AdaptOrch (2602.16873). Detalhes: [docs/research/multi-llm-orchestration-2026.md](docs/research/multi-llm-orchestration-2026.md).
>
> **Updated 2026-04-07** — Migrated from single-model-per-task-type (96.7% cost concentration in Opus 4) to **tier routing by complexity** (Haiku 4.5 → Sonnet 4.6 → Opus 4.7). Added Kimi K2 + Qwen 3 32B in Groq, sonar-deep-research in Perplexity, Gemini 2.5 Pro for analysis. **Projected savings: 20-40% per execution**. Full audit: [docs/AUDIT_2026-04-07.md](docs/AUDIT_2026-04-07.md).
>
> **Unified FinOps tracking** — All calls (this orchestrator + papers + curso-factory + caramaschi + landing-page-geo probes) now flow into a single SQLite local database with nightly Supabase sync. Live dashboard at https://alexandrecaramaschi.com/finops. See the standalone [`geo-finops`](https://github.com/alexandrebrt14-sys/geo-finops) repository (initial release [v1.1.0](https://github.com/alexandrebrt14-sys/geo-finops/blob/main/CHANGELOG.md)).

---

## Architecture

```
Demand --> Orchestrator (Claude decomposes) --> Router (adaptive scoring)
                                                       |
                                       +---------------+---------------+
                                       |               |               |
                               Wave 1 (parallel) Wave 2 (parallel)  Wave 3
                               +--+--+--+         +--+--+            +--+
                               |P |G |O |         |C |G |            |C |
                               +--+--+--+         +--+--+            +--+
                               P=Perplexity G=Gemini O=OpenAI C=Claude Q=Groq

                                       |
                                       v
                              Consolidated result
                           (report + Gantt + cost breakdown)
```

---

## 12 Models across 6 Providers (with tier routing + diversity guarantee)

| Provider | Model | Tier / Role | Cost/1M tokens (in/out) |
|---|---|---|---|
| **Anthropic** | claude-opus-4-7 | premium · architecture/critical_review complexity 4-5 | $15.00 / $75.00 |
| **Anthropic** | claude-sonnet-4-6 | balanced · default for code/review complexity 3 | $3.00 / $15.00 |
| **Anthropic** | claude-haiku-4-5 | economy · classification/summarization complexity 1-2 | $0.80 / $4.00 |
| **OpenAI** | gpt-5.5 ⭐ | **PREMIUM canonical p/ writing, copywriting, SEO** (Sprint 12) | $5.00 / $15.00 |
| **Google** | gemini-2.5-pro | analysis, code, decomposition (Pro reservado p/ raciocínio profundo) | $1.25 / $5.00 |
| **Google** | gemini-2.5-flash | analysis medium, classification, data_processing (5x mais barato que Pro) | $0.30 / $2.50 |
| **Perplexity** | sonar-deep-research | research profunda com 5-40 citações verificáveis | $2.00 / $8.00 |
| **Groq Inc (com Q)** | meta-llama/llama-4-scout-17b-16e-instruct | ultra-fast LPU · classification/summarization | $0.11 / $0.34 |
| **Groq Inc (com Q)** | openai/gpt-oss-120b (groq_heavy) | reasoning rápido em LPU · code_review, decomposition | $0.15 / $0.20 |
| **xAI Grok (com K) ⓘ** | grok-4.3 | flagship com busca live X/Twitter + reasoning + vision (1M ctx) | $1.25 / $2.50 |
| **xAI Grok (com K) ⓘ** | grok-4.20-multi-agent-0309 | 4 agentes paralelos nativos (Grok+Harper+Benjamin+Lucas) — 2M ctx | $1.25 / $2.50 |
| **xAI Grok (com K) ⓘ** | grok-4.20-0309-non-reasoning | classificação rápida + live_search_quick | $1.25 / $2.50 |

> ⓘ **xAI Grok (com K) ≠ Groq Inc (com Q)**. Adicionado 2026-05-17. Conta canônica `alexandre.brt14@gmail.com` / team `caramaschigeo`. API OpenAI-compatible em `https://api.x.ai/v1`. Diferencial único: `search_parameters` com busca live em X/Twitter (`realtime_search`, `social_listening`, `current_events`, `brand_monitoring`).
| **Perplexity** | sonar-deep-research | research multi-step para complexity 4-5 (raciocinio profundo) | $2.00 / $8.00 |
| **Groq** | llama-3.3-70b-versatile | ultra-rapida (~10x), default para Groq tier 1-2 | $0.59 / $0.79 |
| **Groq** | moonshotai/kimi-k2-instruct | Kimi K2 1T params, raciocinio agentic, complexity 4-5 | $1.00 / $3.00 |
| **Groq** | qwen/qwen3-32b | multilingue, traducao primary | $0.29 / $0.59 |

### Tier Routing (automatic via Router._apply_claude_tier and _apply_perplexity_tier)

| Complexity | Claude family | Groq family | Perplexity family |
|---|---|---|---|
| 1-2 (low) | claude-haiku-4-5 | llama-3.3-70b-versatile | sonar-pro |
| 3 (medium) | claude-sonnet-4-5 | qwen/qwen3-32b | sonar-pro |
| 4-5 (high) | claude-opus-4-6 | moonshotai/kimi-k2-instruct | sonar-deep-research |

### Provider Concentration Cap (80% default)

After 5+ tasks executed in a session, if any provider exceeds its `CAP_*_SHARE` environment variable (default 0.80), the router rebalances to the first viable alternative from a different provider. Configurable per provider via `CAP_ANTHROPIC_SHARE`, `CAP_OPENAI_SHARE`, etc.

### Pipeline Role Assignment

| Stage | LLM | Function |
|---|---|---|
| Research | Perplexity | Gathers live data with citations |
| Writing | GPT-4o | Produces final long-form content |
| Analysis | Gemini 2.5 Pro | Analyzes and structures data |
| Classification | Groq/Llama-3.3-70B | Fast categorization and tagging |
| Review | Claude Opus 4.6 | Quality check and final revision |

---

## 12 Task Types

**Sprint 12 (2026-05-17) — diretriz canônica COPY PREMIUM ONLY + Perplexity prioridade.** Os 4 primeiros slots de `writing/copywriting/seo` são todos premium-tier (gpt-5.5, claude-opus-4-7, gemini-2.5-pro, perplexity); Sonnet/Haiku/Flash só como último recurso. Research tem Perplexity como prioridade absoluta com Gemini Pro + Opus 4.7 como fallback.

| Type | Primary LLM | Fallback | Premium chain |
|---|---|---|---|
| `research` | **Perplexity** (sonar-deep-research) | Gemini Pro | perplexity → gemini → claude → gpt-5.5 |
| `fact_check` | **Perplexity** (sonar-deep-research) | Gemini Pro | perplexity → gemini → claude → gpt-5.5 |
| `writing` ⭐ | **gpt-5.5** | Claude Opus 4.7 | gpt-5.5 → claude → gemini → perplexity |
| `copywriting` ⭐ | **gpt-5.5** | Claude Opus 4.7 | gpt-5.5 → claude → gemini → perplexity |
| `seo` ⭐ | **gpt-5.5** | Claude Opus 4.7 | gpt-5.5 → claude → gemini → perplexity |
| `analysis` | Gemini Flash | Groq Heavy | — |
| `code` | Gemini Pro | Claude Sonnet | — |
| `review` | Groq Heavy | Gemini Flash | — |
| `architecture` | Claude Opus 4.7 | Gemini Pro | — |
| `critical_review` | Claude Opus 4.7 | Gemini Pro | — |
| `decomposition` | Claude Sonnet | Gemini Pro | — |
| `code_review` | Groq Heavy | Claude Sonnet | — |
| `data_processing` | Gemini Flash | Groq | — |
| `classification` | Groq (Llama 4 Scout) | Gemini Flash | — |
| `translation` | Groq | gpt-5.5 | — |
| `summarization` | Groq | Gemini Flash | — |
| `realtime_search` | xAI Grok 4.3 | Perplexity | — |
| `social_listening` | xAI Grok 4.3 | Perplexity | — |
| `multi_perspective_decomposition` | xAI Grok Multi-Agent | Claude Sonnet | — |

⭐ = task type sujeito à diretriz **COPY PREMIUM ONLY** (Sprint 12).

---

## CLI Commands

```bash
# Full pipeline
python cli.py run "Write a complete study on GEO vs traditional SEO"

# View plan without executing
python cli.py plan "Research competitors and write report"

# LLM status
python cli.py status

# List configured models and pricing
python cli.py models

# Cost report
python cli.py cost-report

# FinOps
python cli.py finops status     # Current limit state
python cli.py finops reset      # Reset daily counters
python cli.py finops report     # Detailed cost report

# Tracing
python cli.py trace list        # List recent traces
python cli.py trace show <id>   # Trace details
python cli.py trace last        # Last trace
```

### Run Options

```bash
python cli.py run "demand" --dry-run          # Show plan without executing
python cli.py run "demand" --verbose          # Detailed progress output
python cli.py run "demand" --output-dir ./out # Custom output directory
python cli.py run "demand" --force            # Override budget guard
```

---

## Claude Code Integration

A bridge script enables direct integration from Claude Code sessions:

```bash
bash C:/Sandyboxclaude/scripts/bin/geo-bridge.sh "your demand here"
```

The bridge script automatically:
- Loads API keys from `geo-orchestrator/.env`
- Changes to the orchestrator directory
- Executes `python cli.py run "$@" --verbose`
- Displays a summary of LLMs used and their costs

---

## Integration: curso-factory

The orchestrator integrates with [curso-factory](https://github.com/alexandrebrt14-sys/curso-factory) for automated course generation:

- Perplexity researches the topic and market context
- GPT-4o drafts module content and scripts
- Gemini structures the curriculum and learning objectives
- Claude reviews quality and pedagogical consistency
- Output feeds directly into the curso-factory Jinja2 templates

The alexandrecaramaschi.com platform currently hosts **35 courses, 387 modules, 122K+ lines** produced through this pipeline.

---

## Improvement Rounds

| Round | Focus | Key Deliverables |
|---|---|---|
| **Round 1** | Foundation | Orchestrator, pipeline, adaptive router, 4 agents, CLI, SHA-256 cache, checkpoints, quality gates, budget guard |
| **Round 2** | Resilience & observability | FinOps with daily limits, token bucket rate limiter, tracing with spans, connection pool, cost tracker, context pipeline, feedback loop |
| **Round 3** | Advanced intelligence | Circuit breaker, metrics dashboard, token budget allocator, agent memory, session load balancer, task re-prioritization, complexity scoring |
| **Round 4** | Web showcase | Showcase page generated by 5 LLMs, automatic deploy |
| **Round 5** | Security hardening | API keys via headers (not URL params), git filter-repo to clean history, .gitignore on all repos, key rotation |
| **Round 6** | MARL analysis | Analysis based on Foerster/Jaques/Albrecht. 12 tasks, 7 groups, 5 LLMs, $2.68. Proposals: inter-agent communication, collaborative feedback, adaptive balancer |

---

## Intelligence Features

### Foundation (Round 1)

- **Result Cache**: SHA-256 with 24h TTL. Identical tasks are not re-executed.
- **Checkpoints**: State saved per wave. Resume without re-executing completed tasks.
- **Quality Gates**: Automatic validation per task type. Failure triggers retry on fallback.
- **Budget Guard**: Pre-execution cost estimate. Blocks if cost > limit, alert if real > 2x estimate.
- **Adaptive Router**: Weighted score — success (60%), cost (20%), latency (20%).
- **Deduplication**: Cosine similarity > 0.7 merges tasks automatically.
- **Context Optimization**: Long outputs summarized via Gemini before injecting as context.

### Resilience (Round 2)

- **Rate Limiter**: Token bucket per provider with burst and stagger for Gemini.
- **FinOps**: Daily limits per provider, cost reports, history in JSONL.
- **Tracing**: Spans per task with timeline, duration, and metadata.
- **Connection Pool**: HTTP connection reuse per provider.
- **Feedback Loop**: Quality gate results adjust router scores.

### Advanced Intelligence (Round 3)

- **Circuit Breaker**: Protection against offline providers. Opens circuit after consecutive failures, tries half-open periodically.
- **Dashboard**: Consolidated usage, cost, and performance metrics per provider.
- **Token Budget Allocator**: Intelligent distribution of token budget between tasks based on complexity.
- **Agent Memory**: Agents maintain context between executions to progressively improve quality.

---

## FinOps and Governance

### Daily Limits per Provider

| Provider | Daily Limit (USD) | Env Variable |
|---|---|---|
| Anthropic | $2.00 | `FINOPS_LIMIT_ANTHROPIC` |
| OpenAI | $2.00 | `FINOPS_LIMIT_OPENAI` |
| Google | $1.00 | `FINOPS_LIMIT_GOOGLE` |
| Perplexity | $1.00 | `FINOPS_LIMIT_PERPLEXITY` |
| Groq | $1.00 | `FINOPS_LIMIT_GROQ` |
| **Global** | **$5.00** | `FINOPS_LIMIT_GLOBAL` |

### Estimated Costs by Demand Type

| Demand Type | Tasks | Estimated Cost |
|---|---|---|
| Simple research | 2-3 | $0.01-0.05 |
| Article with research | 4-5 | $0.05-0.15 |
| Complete study | 6-8 | $0.10-0.50 |
| Site with content | 7-10 | $0.50-1.50 |

---

## Installation

```bash
git clone https://github.com/alexandrebrt14-sys/geo-orchestrator.git
cd geo-orchestrator
pip install -e .
cp .env.example .env
# Edit .env with your API keys
```

### Required Keys

| Variable | Provider |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic (Claude) |
| `OPENAI_API_KEY` | OpenAI (GPT-4o) |
| `PERPLEXITY_API_KEY` | Perplexity (Sonar) |
| `GOOGLE_AI_API_KEY` | Google (Gemini) |
| `GROQ_API_KEY` | Groq (Llama 3.3 70B) |

---

## Project Structure

```
geo-orchestrator/
  cli.py                    # Main CLI (Click) — entry point
  pyproject.toml            # Project configuration and dependencies
  .env.example              # Environment variable template
  src/
    config.py               # LLM configs, task routing, FinOps limits
    models.py               # Pydantic models (Task, Plan, TaskResult, ExecutionReport)
    orchestrator.py         # Core: decompose, deduplicate, cache, budget guard, report
    pipeline.py             # Execution engine: waves, checkpoints, quality gates, fallback
    router.py               # Adaptive router: scoring, fallback, session load balancer
    llm_client.py           # Unified HTTP client for 5 providers (retry, backoff)
    rate_limiter.py         # Token bucket per provider (RPM limits, burst, stagger)
    cost_tracker.py         # Cost tracking per task and per LLM
    finops.py               # FinOps engine: daily limits, alerts, reports
    tracer.py               # Tracing with spans: timeline and observability
    connection_pool.py      # HTTP connection pool per provider
    circuit_breaker.py      # Circuit breaker per provider: CLOSED/OPEN/HALF_OPEN
    agents/
      researcher.py         # Perplexity agent (research with citations)
      writer.py             # GPT-4o agent (writing, copy, SEO)
      architect.py          # Claude agent (code, architecture, review)
      analyzer.py           # Gemini agent (analysis, classification, batch)
      groq_agent.py         # Groq Llama 3.3 70B agent (speed, rapid drafts)
  scripts/
    run_5llm_board.py       # 5-LLM board: collaborative audit and improvement
    implement_improvements.py
    round3_deep_improvements.py
  docs/
    MANUAL.md               # Complete technical manual
    ARCHITECTURE.md         # Detailed technical architecture
  output/                   # Execution reports, cache, checkpoints
```

---

## Security

- API keys **never in URLs** — Google API key via `x-goog-api-key` header
- All secrets via environment variables (`.env` in `.gitignore`)
- `output/` directory excluded from git (contains logs with sensitive data)
- Git history cleaned with `git filter-repo` after GitGuardian incident
- Audit module: `papers/src/finops/secrets.py` with leak scanning

---

## Repository

- **GitHub**: https://github.com/alexandrebrt14-sys/geo-orchestrator
- **Owner**: Alexandre Caramaschi — CEO of Brasil GEO, former CMO at Semantix (Nasdaq), co-founder of AI Brasil

---

## Ecosystem

| Property | Stack | Status |
|---|---|---|
| [alexandrecaramaschi.com](https://alexandrecaramaschi.com) | Next.js 16 + React 19 + Supabase | Production — 35 courses, 25 insights, 122K+ lines |
| [brasilgeo.ai](https://brasilgeo.ai) | Cloudflare Workers | Production — 14 articles |
| [geo-orchestrator](https://github.com/alexandrebrt14-sys/geo-orchestrator) | Python + 5 LLMs | Active — multi-LLM pipeline |
| [curso-factory](https://github.com/alexandrebrt14-sys/curso-factory) | Python + Jinja2 | Active — course generation pipeline |
| [geo-checklist](https://github.com/alexandrebrt14-sys/geo-checklist) | Markdown | Open-source — GEO audit checklist |
| [llms-txt-templates](https://github.com/alexandrebrt14-sys/llms-txt-templates) | Markdown + JSON | Open-source — llms.txt standard |
| [geo-taxonomy](https://github.com/alexandrebrt14-sys/geo-taxonomy) | JSON + CSV + Markdown | Open-source — 60+ GEO terms |
| [entity-consistency-playbook](https://github.com/alexandrebrt14-sys/entity-consistency-playbook) | Markdown | Open-source — entity consistency |
| [papers](https://github.com/alexandrebrt14-sys/papers) | Python + Supabase | Research — LLM citation study |

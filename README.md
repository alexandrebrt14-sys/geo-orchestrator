# geo-orchestrator

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![LLMs](https://img.shields.io/badge/LLMs-5_providers-ff6b35)
![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)

Multi-LLM orchestration pipeline for Generative Engine Optimization (GEO) content production. Receives a natural-language demand, decomposes it into atomic tasks via Claude, routes each task to the most appropriate LLM based on adaptive scoring, and executes waves in parallel with caching, checkpoints, quality gates, and full FinOps governance.

**8,100+ lines | 72+ files | 21 commits | 12+ orchestrator executions**

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

## 5 LLMs and Their Roles

| Provider | Model | Role | Cost/1M tokens (in/out) | RPM |
|---|---|---|---|---|
| **Perplexity** | sonar | Live research with sources and citations | $1.00 / $1.00 | 20 |
| **OpenAI** | gpt-4o | Long-form writing, copywriting, SEO, translation | $2.50 / $10.00 | 60 |
| **Google** | gemini-2.5-pro | Deep analysis, classification, batch processing | $3.50 / $10.50 | 30 |
| **Groq** | llama-3.3-70b-versatile | High-speed classification, rapid drafts | $0.59 / $0.79 | 300 |
| **Anthropic** | claude-opus-4-6 | Decomposition, architecture, code, review | $15.00 / $75.00 | 60 |

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

| Type | Primary LLM | Fallback |
|---|---|---|
| `research` | Perplexity | Gemini |
| `analysis` | Gemini | Claude |
| `writing` | GPT-4o | Claude |
| `copywriting` | GPT-4o | Claude |
| `code` | Claude | GPT-4o |
| `review` | Claude | GPT-4o |
| `seo` | GPT-4o | Perplexity |
| `data_processing` | Gemini | GPT-4o |
| `fact_check` | Perplexity | Gemini |
| `classification` | Groq | Gemini |
| `translation` | GPT-4o | Gemini |
| `summarization` | Gemini | GPT-4o |

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

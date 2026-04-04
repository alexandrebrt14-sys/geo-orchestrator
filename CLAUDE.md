# CLAUDE.md — geo-orchestrator

## Proposito

Orquestrador multi-LLM da Brasil GEO. Recebe uma demanda em linguagem natural,
decompoe em tarefas via Claude, roteia cada tarefa para o LLM mais adequado
(scoring adaptativo + fallback), e executa em waves paralelas com cache,
checkpoints, quality gates e governanca FinOps.

**Estado atual**: v2.0 | ~12.500 linhas de Python | 42 arquivos | 33 commits | 116+ execuções

## v2.0 — Upgrade (29/Mar/2026)

Baseado na analise de 38 artigos academicos (CASTER, HALO, AFlow, Anthropic Engineering, Google Research).

### 4 novos modulos:

| Modulo | Arquivo | Inspiracao | Impacto |
|--------|---------|------------|---------|
| **Code-First Gate** | `src/code_executor.py` | Huryn/Medium | -40% latencia, -30% custo. Resolve tarefas deterministicas sem LLM |
| **Prompt Refiner** | `src/prompt_refiner.py` | HALO (arXiv 2505.13516) | +25% qualidade. Pipeline de 3 etapas: parser → enricher → optimizer |
| **Smart Router** | `src/smart_router.py` | CASTER (arXiv 2601.19793) + Google Research | -72% custo roteamento. Classifica demanda em SIMPLE/MODERATE/COMPLEX |
| **Quality Judge** | `src/quality_judge.py` | Anthropic Engineering | +35% qualidade mensuravel. Rubrica de 5 dimensoes via Groq |
| **Semantic Cache** | `src/semantic_cache.py` | AFlow (arXiv 2410.10762) | +250% cache hit rate. Jaccard similarity sobre bag-of-words |
| **Adaptive Decomposer** | `src/adaptive_decomposer.py` | HALO (arXiv 2505.13516) | +30% qualidade. Macro plan → wave-by-wave micro decomposition |

### Mudancas no fluxo:

```
ANTES (v1.0):
  demanda → Claude decompoe tudo → 5 LLMs OBRIGATORIOS → quality gates basicos → output

DEPOIS (v2.0):
  demanda → Prompt Refiner (3 etapas) → Semantic Cache check → Claude decompoe
  → Code-First Gate (tarefas deterministicas) → Smart Router classifica tier
  → 2-5 LLMs sob demanda → Early Stopping entre waves → Quality Judge (5 dimensoes) → output
  (Adaptive Decomposer disponivel para wave-by-wave via flag)
```

### Metricas projetadas v1.0 vs v2.0:

| Metrica | v1.0 | v2.0 |
|---------|------|------|
| Custo/execucao | $1.85 | ~$0.60 |
| Tempo/execucao | 35 min | ~12 min |
| LLMs/execucao | 5 (sempre) | 2-5 (sob demanda) |
| Qualidade | nao medida | 75-85% (rubrica) |

### Validacao real (30/Mar/2026):

Teste com 5 tarefas (1 por LLM) — todas concluidas com sucesso:

| LLM | Tempo | Custo | Tokens |
|-----|-------|-------|--------|
| Groq Llama 3.3 70B | 3.0s | $0.0019 | 2.937 |
| GPT-4o | 5.4s | $0.0112 | 3.315 |
| Gemini 2.5 Flash | 6.9s | $0.0004 | 1.550 |
| Perplexity sonar-pro | 8.9s | $0.0187 | 1.510 |
| Claude Opus 4.6 | 53.5s | $0.2234 | 7.454 |
| **TOTAL** | **77.7s** | **$0.2557** | **16.766** |

Custo real **$0.26** vs estimado v1.0 **$1.85** = **reducao de 86%**.

## Arquitetura

```
cli.py                          # CLI Click — ponto de entrada
src/
  __init__.py
  config.py                     # LLM configs, task routing (12 tipos), FinOps limits
  models.py                     # Pydantic: Task, Plan, TaskResult, LLMResponse, ExecutionReport
  orchestrator.py               # Cerebro: decompose, deduplicate, cache, budget guard, report
  pipeline.py                   # Engine: waves, checkpoints, quality gates, fallback, stagger
  router.py                     # Router adaptativo: score (success*0.6 + cost*0.2 + latency*0.2), session load balancer
  llm_client.py                 # Cliente HTTP unificado: 5 providers, retry, backoff, rate limit
  rate_limiter.py               # Token bucket por provider (singleton), RPM limits
  cost_tracker.py               # Acumulador de custos por tarefa/LLM, relatorio Markdown
  finops.py                     # FinOps engine: limites diarios, alertas, reset, relatorios (round 2)
  tracer.py                     # Tracing com spans: timeline, duracao, metadata (round 2)
  connection_pool.py            # Pool de conexoes HTTP por provider (round 2)
  agents/
    base.py                     # BaseAgent (legacy), TaskResult, TaskType
    researcher.py               # Perplexity (sonar)
    writer.py                   # GPT-4o
    architect.py                # Claude Opus
    analyzer.py                 # Gemini Flash
    groq_agent.py               # Groq Llama 3.3 70B (round 2)
  circuit_breaker.py              # Circuit breaker por provider: CLOSED/OPEN/HALF_OPEN (round 3)
  performance_router.py           # Router com historico de performance e scoring adaptativo (round 3)
  code_executor.py                # Code-First Gate: resolve tarefas deterministicas sem LLM (v2.0)
  prompt_refiner.py               # Pipeline de 3 etapas para refinar prompts (v2.0)
  smart_router.py                 # Router inteligente com classificacao SIMPLE/MODERATE/COMPLEX (v2.0)
  quality_judge.py                # LLM-as-Judge com rubrica de 5 dimensoes via Groq (v2.0)
  semantic_cache.py               # Cache semantico com Jaccard similarity (v2.0)
  adaptive_decomposer.py          # Decomposicao wave-by-wave adaptativa (v2.0)
  templates/
    decomposition.py            # Prompt de decomposicao (legacy, usado pelo CLI antigo)
    agent_prompts.py            # System prompts por tipo de agente
scripts/
  run_5llm_board.py             # Banca de 5 LLMs — auditoria e melhoria colaborativa
  implement_improvements.py     # Implementador de melhorias (round 2)
  round3_deep_improvements.py   # Melhorias profundas (round 3)
output/                         # Relatorios, cache, checkpoints, router stats
docs/
  MANUAL.md                     # Manual tecnico completo
  ARCHITECTURE.md               # Arquitetura tecnica
```

## Source of Truth

- Modelos, preços, capabilities e budgets: `catalog/model_catalog.yaml`
- API keys: `.env` (NÃO duplicar em outros repos — este é a fonte canônica)
- Custos reais: `output/execution_*.json` + `output/.finops/`
- Métricas do ecossistema: `project_inventory.json` (gerado por caramaschi/project_inventory.py)

## Como executar

```bash
# Instalar dependencias
pip install -e .

# Configurar chaves
cp .env.example .env
# Editar .env com suas chaves

# Executar pipeline completo
python cli.py run "sua demanda"

# Apenas decompor
python cli.py plan "sua demanda"

# Status dos LLMs
python cli.py status

# Listar modelos e precos
python cli.py models

# Historico de custos
python cli.py cost-report

# FinOps
python cli.py finops status
python cli.py finops reset
python cli.py finops report

# Tracing
python cli.py trace list
python cli.py trace show <id>
python cli.py trace last
```

## 5 LLMs configurados

| LLM | Modelo | Provider | Papel |
|-----|--------|----------|-------|
| claude | claude-opus-4-6-20250415 | Anthropic | Decomposicao, arquitetura, codigo, revisao |
| gpt4o | gpt-4o | OpenAI | Redacao, copywriting, SEO, traducao |
| gemini | gemini-2.5-flash | Google | Analise, classificacao, sumarizacao, lotes |
| perplexity | sonar-pro | Perplexity | Pesquisa com fontes, fact check |
| groq | llama-3.3-70b-versatile | Groq | Velocidade, classificacao rapida, rascunhos |

> **REGRA**: Sempre usar a versao mais moderna e potente de cada provider.
> Atualizar modelos quando novas versoes forem lancadas.

## 12 tipos de tarefa e roteamento

| Tipo | Primario | Fallback |
|------|----------|----------|
| research | perplexity | gemini |
| analysis | gemini | claude |
| writing | gpt4o | claude |
| copywriting | gpt4o | claude |
| code | claude | gpt4o |
| review | claude | gpt4o |
| seo | gpt4o | perplexity |
| data_processing | gemini | gpt4o |
| fact_check | perplexity | gemini |
| classification | gemini | claude |
| translation | gpt4o | gemini |
| summarization | gemini | gpt4o |

## Modulos por rodada

### Round 1 — Fundacao
- `orchestrator.py` — decomposicao, deduplicacao, cache, budget guard
- `pipeline.py` — waves, checkpoints, quality gates, fallback
- `router.py` — scoring adaptativo, fallback chains
- `llm_client.py` — cliente HTTP unificado, retry, backoff
- `config.py` — configuracoes de LLMs e tarefas
- `models.py` — Pydantic models de dominio
- `rate_limiter.py` — token bucket por provider
- `cost_tracker.py` — rastreamento de custos

### Round 2 — Resiliencia e observabilidade
- `finops.py` — limites diarios, alertas, reset, relatorios
- `tracer.py` — tracing com spans, timeline, metadata
- `connection_pool.py` — pool de conexoes HTTP por provider
- `groq_agent.py` — 5o agente (Groq Llama 3.3 70B)
- Feedback loop integrado ao router
- Context pipeline entre waves
- Task re-prioritization

### Round 3 — Inteligencia avancada
- Circuit breaker (protecao contra providers fora do ar)
- Dashboard de metricas (uso, custos, performance)
- Token budget allocator (distribuicao inteligente de tokens)
- Agent memory (contexto entre execucoes)
- Session load balancer (distribuicao de carga)
- Complexity scoring (estimativa automatica de complexidade)

## Scripts de melhoria

| Script | Funcao |
|--------|--------|
| `scripts/run_5llm_board.py` | Banca de 5 LLMs: cada LLM audita o projeto e sugere melhorias |
| `scripts/implement_improvements.py` | Implementa melhorias consensuais da banca (round 2) |
| `scripts/round3_deep_improvements.py` | Melhorias profundas — circuit breaker, dashboard, memory, token allocator |

## Sem Emojis
Proibido emojis em qualquer conteúdo, output ou documentação.

## Convencoes

- **Idioma**: PT-BR com acentuacao completa para conteudo, ingles para codigo.
- **Entidade**: Sempre "Brasil GEO" (nunca "GEO Brasil").
- **Credencial**: "CEO da Brasil GEO, ex-CMO da Semantix (Nasdaq), cofundador da AI Brasil".
- **Models**: Pydantic para dominio (src/models.py), dataclass para infra (rate_limiter, cost_tracker).
- **HTTP**: httpx async para todas as chamadas LLM (nao SDKs oficiais).
- **Rate limiting**: Token bucket por provider, singleton global.
- **Cache**: SHA-256 key, TTL 24h, em output/.cache/.
- **Checkpoints**: JSON em output/.checkpoint.json, salvo por wave.
- **Router stats**: JSON em output/.router_stats.json, atualizado por tarefa.
- **Custos**: Rastreados por tarefa em CostTracker, historico em output/cost_history.jsonl.

## FinOps

- Budget limit por execucao: US$ 5.00 (env: GEO_BUDGET_LIMIT)
- Limites diarios: Anthropic US$ 5.00, OpenAI US$ 2.00, Google US$ 1.00, Perplexity US$ 1.00, Groq US$ 2.00
- Global diario: US$ 10.00
- Budget guard: bloqueia se estimativa > limite, alert se real > 2x estimativa

## RPM Limits

- Anthropic: 60 RPM, burst 3
- OpenAI: 60 RPM, burst 3
- Google: 30 RPM, burst 3 (billing ativo, R$500 credito)
- Perplexity: 20 RPM, burst 2
- Groq: 300 RPM, burst 10 (free tier: 500K RPM, 300K tokens/min)

## GitHub

https://github.com/alexandrebrt14-sys/geo-orchestrator

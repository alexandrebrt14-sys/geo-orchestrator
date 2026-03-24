# CLAUDE.md — geo-orchestrator

## Proposito

Orquestrador multi-LLM da Brasil GEO. Recebe uma demanda em linguagem natural,
decompoe em tarefas via Claude, roteia cada tarefa para o LLM mais adequado
(scoring adaptativo + fallback), e executa em waves paralelas com cache,
checkpoints, quality gates e governanca FinOps.

## Arquitetura

```
cli.py                      # CLI Click — ponto de entrada
src/
  __init__.py
  config.py                 # LLM configs, task routing (12 tipos), FinOps limits
  models.py                 # Pydantic: Task, Plan, TaskResult, LLMResponse, ExecutionReport
  orchestrator.py           # Cerebro: decompose, deduplicate, cache, budget guard, report
  pipeline.py               # Engine: waves, checkpoints, quality gates, fallback, stagger
  router.py                 # Router adaptativo: score (success*0.6 + cost*0.2 + latency*0.2)
  llm_client.py             # Cliente HTTP unificado: 4 providers, retry, backoff, rate limit
  rate_limiter.py           # Token bucket por provider (singleton), RPM limits
  cost_tracker.py           # Acumulador de custos por tarefa/LLM, relatorio Markdown
  agents/
    base.py                 # BaseAgent (legacy), TaskResult, TaskType
    researcher.py           # Perplexity (sonar)
    writer.py               # GPT-4o
    architect.py            # Claude Opus
    analyzer.py             # Gemini Flash
  templates/
    decomposition.py        # Prompt de decomposicao (legacy, usado pelo CLI antigo)
    agent_prompts.py        # System prompts por tipo de agente
output/                     # Relatorios, cache, checkpoints, router stats
docs/
  MANUAL.md                 # Manual tecnico completo
  ARCHITECTURE.md           # Arquitetura tecnica
```

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
```

## 4 LLMs configurados

| LLM | Modelo | Provider | Papel |
|-----|--------|----------|-------|
| claude | claude-opus-4-6-20250415 | Anthropic | Decomposicao, arquitetura, codigo, revisao |
| gpt4o | gpt-4o | OpenAI | Redacao, copywriting, SEO, traducao |
| gemini | gemini-2.5-flash | Google | Analise, classificacao, sumarizacao, lotes |
| perplexity | sonar | Perplexity | Pesquisa com fontes, fact check |

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

- Budget limit por execucao: US$ 1.00 (env: GEO_BUDGET_LIMIT)
- Limites diarios: Anthropic US$ 0.50, OpenAI US$ 0.50, Google US$ 0.50, Perplexity US$ 0.50
- Global diario: US$ 1.50
- Budget guard: bloqueia se estimativa > limite, alert se real > 2x estimativa

## RPM Limits

- Anthropic: 60 RPM, burst 3
- OpenAI: 60 RPM, burst 3
- Google: 30 RPM, burst 3 (billing ativo, R$500 credito)
- Perplexity: 20 RPM, burst 2

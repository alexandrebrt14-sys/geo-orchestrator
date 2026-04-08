# CLAUDE.md — geo-orchestrator

## Proposito

Orquestrador multi-LLM da Brasil GEO. Recebe uma demanda em linguagem natural,
decompoe em tarefas via Claude, roteia cada tarefa para o LLM mais adequado
(scoring adaptativo + fallback), e executa em waves paralelas com cache,
checkpoints, quality gates e governanca FinOps.

**Estado atual**: v2.0 | ~13.500 linhas de Python | 49 arquivos | 140/140 tests | 117+ execuções

## 2026-04-08 — Sprint 7 (catalog runtime SoT + /health + dashboard HTML + safety calibration + coverage)

A sprint 7 atacou as 6 prioridades vindas da analise tecnica do orchestrator
run #7 (executado pelos proprios 5 LLMs, $0.0718, 11/11 tasks). Foco:
fechar o ultimo gap arquitetural critico (catalog YAML como SoT runtime),
expor health pollable HTTP, dashboard publico e blindar o auto-calibrator
contra drift destrutivo.

| Item | Origem | Status |
|---|---|---|
| **catalog YAML como runtime SoT** (LLM_CONFIGS) | Gap #4 sev 4 (gemini t6) | RESOLVIDO + 4 tests + retro-compat |
| **Rollback safety do auto-calibrator** | Crítica gpt4o t5 (review sprint 6) | RESOLVIDO + 2 tests + comando `finops calibrate-rollback` |
| **/health HTTP endpoint** (stdlib) | Gap #3 sev 4 | RESOLVIDO + 4 tests + comando `cli serve` |
| **Dashboard HTML estatico** (Chart.js) | Gap #5 sev 4 | RESOLVIDO + 3 tests + opcao `dashboard --html` |
| **Coverage badge + pytest-cov** | Gap #6 sev 2 | RESOLVIDO + CI atualizado, codecov action |
| Bug oportunistico: gpt4o cost no test_outlier | Detectado pelo safety threshold | RESOLVIDO |

**Marcos da sprint 7**:

- `src/catalog_loader.build_llm_configs_from_catalog()` constroi `LLMConfig`
  dict em runtime a partir de `catalog/model_catalog.yaml` (que ganhou
  `api_key_env` por provider). `config.py` tenta o catalog primeiro;
  fallback automatico para o dict hardcoded se PyYAML/catalog ausente.
  `GEO_CATALOG_PATH` env var permite hot-reload; `GEO_DISABLE_CATALOG_RUNTIME`
  forca o fallback para debug. Strengths/role continuam vindo do dict
  estatico (metadata de apresentacao, nao roteamento).
- `cost_calibrator` ganhou `SAFETY_DEVIATION_MAX=5.0` e `SAFETY_DEVIATION_MIN=0.2`:
  candidatos que divergem mais que isso do default sao **rejeitados**
  e logados em `safety_rejections[]`. Antes de cada `recalibrate(persist=True)`,
  o `.cost_calibration.json` atual e copiado para `.cost_calibration.backup.json`.
  Comando novo: `cli.py finops calibrate-rollback` restaura o backup.
- `src/health_server.py` (stdlib `http.server`) expoe `GET /health`,
  `GET /metrics`, `GET /` com status 200/503. Reusa os mesmos 6 checks do
  `cli doctor` mas em formato pollable para LB/k8s/cron. Comando novo:
  `cli.py serve --port 8080`. Zero deps adicionais.
- `src/dashboard_html.py` gera HTML auto-contido com 5 graficos Chart.js
  (CDN), KPI cards, tabela dos 10 ultimos runs e palette dark do GitHub.
  Deployable em qualquer servidor estatico. Comando novo:
  `cli.py dashboard --html PATH`. Consolida tier interno Claude no slot
  canonico para o bar chart de uso por LLM.
- `.github/workflows/tests.yml` agora roda com `pytest-cov`, gera
  `coverage.xml` e faz upload pra Codecov via `codecov/codecov-action@v4`.
  Coverage atual: **53% global** (Sprint 5/6/7 modulos: 70-98%).

**Dados do orchestrator run #7 que originou esta sprint**:
- 11/11 tasks completas em 204s, $0.0718
- Anthropic estava bloqueado (102% do limite diario) — fallback chain
  redirecionou TODAS as tasks Claude para Sonnet/Groq sem falha
- 5 LLMs canonicos usados (4/5 em runtime, claude via tier interno Sonnet)
- Triple review paralelo (acentuacao + codigo + estilo) na wave 3
- Quality Judge: APROVADO (87/100)

## 2026-04-08 — Sprint 6 (E2E mockado + auto-trigger calibracao + doctor + CI)

A sprint 6 fechou os 4 itens que faltavam para considerar o produto
"funcional, avancado e estavel". Foco: cobertura de regressao do contrato
inteiro, fechamento do loop de calibracao sem intervencao humana, e
ferramenta de health check pronta para CI/cron.

| Item | Tipo | Status |
|---|---|---|
| Tests legados quebrados (test_core asyncio + scripts/) | Bug | RESOLVIDO — `pyproject.toml` testpaths + `asyncio.run` |
| E2E test suite com pipeline mockado | P1 deferido sprint 5 | RESOLVIDO + 6 tests (`tests/test_e2e.py`) |
| Auto-trigger de calibracao quando drift dispara | Sprint 6 | RESOLVIDO + 1 test (orchestrator.run) |
| `cli.py doctor` health check | Sprint 6 | RESOLVIDO + 3 tests (6 checks: keys, catalog, finops, kpi, calibration, drift) |
| GitHub Actions CI workflow | Sprint 6 | RESOLVIDO (`.github/workflows/tests.yml` matriz 3.11/3.12) |
| Bug calibrator: results lista vs dict | Sprint 6 | RESOLVIDO defensivamente em `_load_costs_by_llm` |
| KPI quality_judge_pass aceita verdicts PT-BR | Sprint 6 | RESOLVIDO — antes so aceitava EN, real do QualityJudge e "APROVADO" |

**Marcos da sprint 6**:

- `tests/test_e2e.py` mocka `LLMClient.query` + `QualityJudge.evaluate` e
  executa `Orchestrator.run()` ponta-a-ponta (PromptRefiner -> decompose ->
  waves -> quality -> kpi -> report). Roda em 1.5s sem nenhuma chamada
  de rede. Cobre tambem cache hit, calibrator E2E, replay e auto-trigger.
- Orchestrator agora dispara `recalibrate()` automaticamente quando
  `detect_drift()` retorna alerta — fecha o loop completo do drift sem
  intervencao humana. Marca o auto-fix no `report.summary`.
- `python cli.py doctor [--json] [--strict]` roda 6 health checks:
  api_keys, catalog_consistency (via `assert_catalog_consistent`),
  finops_daily, kpi_history freshness, cost_calibration age, drift_detector.
  Saida humana (Rich) ou JSON estruturado. `--strict` faz exit 1 em
  ATENCAO/CRITICO — pronto para Task Scheduler/cron gating.
- `.github/workflows/tests.yml` roda pytest em matriz Python 3.11+3.12
  + smoke do CLI doctor + valida o catalog YAML em todo PR contra main.
- Fix oportunistico: `compute_quality_judge_pass_rate` so reconhecia
  verdicts em ingles. Agora aceita "APROVADO", "APROVADO_COM_RESSALVAS"
  (canonicos do `QualityJudge` PT-BR) alem dos aliases EN.
- Fix oportunistico: `cost_calibrator._load_costs_by_llm` aceita tanto
  `results` como dict (formato canonico) quanto list (formato legado v1.0).

## 2026-04-08 — Sprint 5 (auto-calibracao + 2 KPIs novos + replay + --since + catalog SoT)

A sprint 5 fechou os 4 itens P1 + 2 P2 do backlog publico (4 commits, +20 tests).
Foco: fechar o loop do drift de custo sem intervencao humana e amadurecer o
CLI/dashboard como ferramenta de auditoria e replay.

| Item | Tipo | Status |
|---|---|---|
| Adaptive AVG_COST_PER_CALL auto-calibration | P1 | RESOLVIDO + 4 tests (`src/cost_calibrator.py`) |
| KPI quality_judge_pass_rate | P1 | RESOLVIDO + 3 tests + persistido em jsonl |
| KPI parallelism_efficiency (speedup) | P1 | RESOLVIDO + 3 tests + alimentado pelo Pipeline._wave_timings |
| catalog/model_catalog.yaml SoT | P1 | RESOLVIDO + 3 tests + `src/catalog_loader.py` validator |
| dashboard --since 7d/24h/30d filter | P2 | RESOLVIDO + 2 tests |
| `cli.py replay <execution_id>` | P2 | RESOLVIDO + 3 tests |

**Marcos da sprint 5**:

- `src/cost_calibrator.py` aprende AVG_COST_PER_CALL do historico real de
  `output/execution_*.json` e persiste em `output/.cost_calibration.json`.
  Orchestrator._estimate_cost e FinOps.pre_execution_check passam a usar
  `get_calibrated_avg_cost()` em vez do dict estatico de `config.py`.
  Comando novo: `python cli.py finops calibrate --window 30`.
- 2 KPIs novos no `.kpi_history.jsonl`: `quality_judge_pass` (1.0/0.0/None
  por run, agregado em pass_rate pelo dashboard) e `parallelism_efficiency`
  (sum(task_durations) / total_duration — speedup vs sequencial).
- Dashboard ganhou colunas QJ + Par e filtro `--since 7d` antes de aplicar
  `--limit`. CSV/JSON export tambem incluem os 2 KPIs novos.
- `cli.py replay 20260408_120000` (ou `replay last`) le um execution report
  historico e re-renderiza summary completo sem custo de LLM. Util para
  auditoria, demos e comparacao de runs.
- `catalog/model_catalog.yaml` v2.0 sincronizado com `LLM_CONFIGS` canonico
  (IDs reais: claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5, gpt-4o,
  gemini-2.5-pro, sonar-pro, llama-3.3-70b-versatile). `src/catalog_loader.py`
  fornece `assert_catalog_consistent()` para travar drift no CI.
- Fix oportunista: `LLM_CONFIGS["perplexity"]` estava com `cost_per_1k=$0.001`
  (subestimando ~3x). Realinhado para $0.003/$0.015 conforme catalog.

## 2026-04-07 — Sprint 4 (recalibracao de custo + 2 KPIs novos + 7 fixes)

A sprint 4 atacou o drift PARA BAIXO detectado na sprint 3 — pre_check estimava
com Opus enquanto runtime ja desviava para Sonnet/Haiku via tier interno.
Run #6: cost_estimate_accuracy subiu de 0.24x para **0.43x** (trajetoria de
retorno para a banda saudavel 0.7-1.5x). 97/97 tests verde (era 75/75).

| Fix | Status |
|---|---|
| #19 (P0) AVG_COST_PER_CALL recalibrado + smart_route aplica downgrade | RESOLVIDO + 7 tests |
| #21 (P1) decompose() usa claude_sonnet em vez de Opus (-80%/call) | RESOLVIDO + 2 tests |
| #23 (P1) DECOMPOSE_SYSTEM com regra reforcada de sub-decomposicao review | RESOLVIDO + 2 tests |
| #24 (P1) KPI tier_internal_engagement_rate persistido | RESOLVIDO + 5 tests |
| #25 (P1) KPI fallback_chain_save_rate persistido | RESOLVIDO + 3 tests |
| #26 (P2) cli.py dashboard --export csv\|json | RESOLVIDO + 2 tests |
| Bonus: dashboard com 2 colunas novas (Tier% e Save%) | RESOLVIDO |

**Marcos da sprint 4**:
- Pre_check FinOps + runtime agora consistentes (smart_route._route_complex
  usa chain[0] canonico em vez de _compute_score)
- decompose() do Orchestrator gasta ~80% menos por chamada (Sonnet vs Opus)
- 2 KPIs novos no jsonl: tier_internal_engagement_rate (50% no run #6),
  fallback_chain_save_rate_cumulative (acumulativo, 0% nos 4 runs sem falhas reais)
- Dashboard CLI ganhou colunas Tier% e Save% + flag --export para Looker/Metabase

## 2026-04-07 — Sprint 3 (8 fixes + tier interno em runtime + KPI history)

A sprint 3 fechou os 5 itens P0/P1/P2 do backlog publico + 3 bonus durante validacao.
Run #5 da bateria: **US$ 0.0727** (vs US$ 0.6653 no run #1 — **−89%**), 97.5s (vs 240.8s — **−60%**),
75/75 tests (era 51/51), 5/5 LLMs.

| Fix | Status |
|---|---|
| #11 sanitizacao ASCII de paths (`src/sanitize.py` novo) | RESOLVIDO + 9 tests |
| #13 decomposer marca complexity variavel | RESOLVIDO + 5 tests + RUNTIME (Haiku acionou) |
| #14 KPI history persistido em jsonl (`src/kpi_history.py` novo) | RESOLVIDO + 4 tests |
| #15 drift alert se 3 runs fora de banda 0.7-1.5x | RESOLVIDO + 3 tests |
| #16 `cli.py dashboard` | RESOLVIDO + 2 tests |
| Bonus: display Anthropic agrega Sonnet/Haiku | RESOLVIDO |
| Bonus: race condition session_usage 2x | RESOLVIDO |
| Bonus: parser task IDs robusto (regex) | RESOLVIDO |

**Marco mais importante**: tier interno Claude (Opus/Sonnet/Haiku) acionou em runtime
pela 1a vez. t7 review do run #5 foi para `claude_haiku` (-95% custo vs Opus).
Decomposer refinado defaulta code/review como MEDIUM (era HIGH) e aplica lexical
override (low_keywords/high_keywords) + thresholds maiores (180/600 chars).

**Novos comandos**: `python cli.py dashboard` mostra timeseries dos KPIs persistidos
em `output/.kpi_history.jsonl` com semaforo verde/amarelo/vermelho e drift alert
visual quando 3 runs consecutivos saem da banda saudavel.

## 2026-04-07 — CLI religado ao Orchestrator v2.0 (refatoracao critica)

A auditoria de 2026-04-07 confirmou que o `cli.py` executava um caminho legacy
v1.0 (`_execute_plan`) que ignorava toda a infraestrutura v2.0 — SmartRouter,
cap 80%, quality gates, semantic cache, code-first gate, fallback chain
estruturada, FinOps por tarefa, checkpoint/resume. Sintoma: na execucao
20260407_180740, **12/12 tarefas foram para Claude (100% concentracao)** e o
gasto diario Anthropic atingiu US$ 4.97 / US$ 5.00 (limite). Cap 80% nunca rodou.

A refatoracao na branch `refactor/cli-orchestrator-v2` religou o CLI ao
`Orchestrator(smart=True).run()` e fechou de uma vez 9 gaps:

| Gap | Status |
|---|---|
| SmartRouter (SIMPLE/MODERATE/COMPLEX) | ATIVO no CLI |
| Cap 80% por provider | ATIVO no CLI |
| Quality gates por wave | ATIVO no CLI |
| Quality Judge (5 dimensoes) | ATIVO no CLI |
| Fallback chain estruturada (4-5 LLMs) | ATIVO no CLI |
| Semantic Cache (Jaccard) | ATIVO no CLI |
| Code-First Gate (tarefas deterministicas sem LLM) | ATIVO no CLI |
| FinOps `check_budget()` por tarefa + redirect cheapest | ATIVO no CLI |
| Timeout granular por task type | ATIVO no CLI |
| Tier routing duplicado em `cli.py` | REMOVIDO (fonte unica: `LLM_CONFIGS`) |
| Comando `cli resume <checkpoint>` | NOVO — expoe `Pipeline.resume()` |

Ver `docs/REFACTOR_2026-04-07.md` para detalhes e `cli.py` (~759 linhas) para
o codigo final. Os testes existentes (20/20) continuam passando.

### Comandos novos / atualizados

- `python cli.py run "<demanda>"` — agora chama `Orchestrator(smart=True).run()`
- `python cli.py run "<demanda>" --dry-run` — usa `Orchestrator.decompose()`
- `python cli.py run "<demanda>" --force` — bypass do budget guard
- `python cli.py run "<demanda>" --no-smart` — debug, usa Router classico
- `python cli.py resume [-c output/.checkpoint.json]` — retoma execucao interrompida
- `python cli.py status` — agora alem do status mostra alerta FinOps quando provider passa de 80% do limite diario

### Compatibilidade

- Plan/Task/TaskResult passam a usar os tipos canonicos `src/models.py`
  (nao mais `src/agents/base.py`).
- O JSON de relatorio em `output/execution_*.json` mudou de schema (plan e
  agora `Plan.model_dump()`, results e `dict[task_id, TaskResult]`). Scripts
  externos que liam o formato antigo precisam ser ajustados.


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

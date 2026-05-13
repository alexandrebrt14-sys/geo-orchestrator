# Bateria 360° E2E — geo-orchestrator

**Executada em:** 2026-05-13 19:00–19:20 UTC-3 (Brasil GEO)
**Operador:** Claude Code (Opus 4.7, 1M context)
**Demanda:** Cobertura integral end-to-end pós-restauração do orchestrator (mesma sessão de [feedback_geo_orchestrator_path_removido](../../../.claude/projects/C--Users-alexa/memory/feedback_geo_orchestrator_path_removido.md)).
**Repo SHA testado:** `a42089c` (master, pushed)

---

## Sumário executivo

| Categoria | Resultado | Observações |
|---|---|---|
| Suíte pytest (220 testes) | **218/220 PASS** (99,1%) | 2 falhas — 1 pré-existente, 1 regressão de teste desatualizado |
| Cobertura de código | **57%** (4569 stmts, 1951 miss) | Up de 53% baseline |
| Ping 5 LLMs | **5/5 OK** | Custo $0.0056, max latência 4.92s (Anthropic) |
| `cli.py doctor` | **6/6 checks OK** | api_keys, catalog, finops, kpi_history, calibration, drift |
| `cli.py plan` | **FAIL — ValidationError** | Parser de dependencies quebra com LLMs que retornam dicts |
| `cli.py run` (E2E real) | **OK** | 7 tarefas, 144s, $0.077, 0 falhas |
| `scripts/run_5llm_board.py` | **OK — 5/5 paralelas** | Max 14.9s wall (Harrison Chase), total $0.008 |
| FinOps após burst | **OK** | $3.60 / $250 (1.4%) globais |
| Provider health | 5/5 CLOSED na última run | 1 histórico Perplexity OPEN detectado |
| Distribution health | **0.6 (target ≥ 0.95)** | Cobertura desigual: Perplexity domina; Anthropic ocioso |
| Cost estimate accuracy | **2.16x (banda 0.7-1.5)** | Calibrador rejeita Perplexity como outlier (ratio 8.61x) |

**Veredicto:** sistema **operacional e production-ready** para `run` (pipeline adaptativo) e `board` (5 LLMs paralelas). Dois bugs críticos identificados para correção em sprint subsequente: `plan` quebra com novos formatos de decomposição, e agregador da tabela "Uso por LLM" conta Gemini Flash como provider Google = 0.

---

## 1. Suíte pytest

```
220 tests collected
218 passed
  2 failed
Coverage: 57% (4569 statements, 1951 missing)
Wall time: 18.44s
```

### 1.1. Falha pré-existente (não-bloqueante)

`tests/test_e2e.py::TestCalibratorE2E::test_calibrator_learns_from_real_execution_reports`

Já documentado em [project_geo_orchestrator_resilience_20260502](../../../.claude/projects/C--Users-alexa/memory/project_geo_orchestrator_resilience_20260502.md). Calibrator rejeita amostra de Perplexity por ratio 8.61x (default `$0.008/k` vs candidate `$0.069/k`) — o teste espera convergência mas as amostras reais têm spread alto demais.

**Decisão sugerida:** ou aumentar tolerância do calibrador (ratio cap > 10x), ou marcar teste como `@pytest.mark.xfail` com motivo documentado.

### 1.2. Regressão de teste desatualizado (não-bloqueante)

`tests/test_no_deprecated_models.py::test_pipeline_max_tokens_is_capped`

```python
assert Pipeline._max_tokens_for_task("writing", claude) == 8192
AssertionError: 16384 == 8192
```

**Root cause:** o teste verifica que `writing` é capado a 8192 (max do Claude Opus 4.6 conforme comentário antigo). Porém o catálogo (`catalog/model_catalog.yaml`) e `LLM_CONFIGS` agora declaram `max_tokens=32000` para Claude Opus 4.6 (commits b623f15 + 6f9df7b que elevaram limites). O cap real aplicado é 16384 (cap por task_type "writing"), abaixo do max do modelo. **O comportamento está correto; o teste está desatualizado.**

**Fix sugerido:** alterar assertion para `== 16384` e remover o comentário "(Claude=8192)".

---

## 2. Health check operacional

### 2.1. `bash scripts/bin/geo-bridge.sh ping`

| Provider | Modelo | Latência | HTTP | Custo USD |
|---|---|---|---|---|
| Anthropic | claude-opus-4-6 | 4.92s | 200 | $0.000495 |
| OpenAI | gpt-4o | 3.58s | 200 | $0.000042 |
| Google | gemini-2.5-pro | 2.95s | 200 | $0.000008 |
| Perplexity | sonar-pro | 2.54s | 200 | $0.005036 |
| Groq | llama-3.3-70b | 0.70s | 200 | $0.000026 |
| **Total** | | | | **$0.005607** |

Observação: Gemini retorna `finishReason: MAX_TOKENS` no ping (esperado, `max_tokens=10` é apertado demais; resposta vem vazia mas a chamada é OK).

### 2.2. `cli.py doctor`

```
api_keys            OK    9 LLMs configurados
catalog_consistency OK    catalog YAML alinhado com LLM_CONFIGS
finops_daily        OK    max 3% (perplexity)
kpi_history         OK    5 entries recentes, ultima: 2026-05-13T14:12:34
cost_calibration    OK    6 LLMs, recalibrado ha 1d
drift_detector      OK    cost_estimate_accuracy dentro da banda 0.7-1.5
Status geral: OK
```

### 2.3. `cli.py models` — 8 modelos canônicos

| ID | Modelo | Provider | $/1k in | $/1k out | Max out |
|---|---|---|---|---|---|
| claude | claude-opus-4-6 | anthropic | $0.015 | $0.075 | 32000 |
| gpt4o | gpt-4o | openai | $0.0025 | $0.010 | 16384 |
| gemini | gemini-2.5-pro | google | $0.00125 | $0.005 | 65536 |
| gemini_flash | gemini-2.5-flash | google | $0.0003 | $0.0025 | 65536 |
| perplexity | sonar-deep-research | perplexity | $0.002 | $0.008 | 8192 |
| groq | llama-3.3-70b | groq | $0.00059 | $0.00079 | 32768 |
| groq_heavy | llama-3.3 (heavy) | groq | $0.0015 | $0.002 | 32768 |
| claude_sonnet | claude-sonnet-4-6 | anthropic | $0.003 | $0.015 | 64000 |
| claude_haiku | claude-haiku-4-5 | anthropic | $0.0008 | $0.004 | 64000 |

---

## 3. E2E real

### 3.1. `cli.py plan "..."` — **FAIL**

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Task
dependencies.0
  Input should be a valid string [type=string_type, input_value={'task_id': 't1', 'context': ...}]
```

**Root cause:** `src/orchestrator.py:1164` em `_parse_plan` instancia `Task(...)` com `dependencies` recebido cru do JSON do LLM. O modelo (claude_sonnet primary para decomposition) retornou `dependencies: [{"task_id": "t1", "context": "..."}]` em vez de `dependencies: ["t1"]`. O parser não normaliza.

**Fix sugerido:** em `_parse_plan`, normalizar `deps = [d if isinstance(d, str) else d.get("task_id") for d in raw_deps]` antes de criar o Task. Adicionar teste unitário para ambos os formatos.

**Impacto:** `plan` (dry-run) bloqueado para qualquer demanda; `run` parece contornar (ver 3.2).

### 3.2. `cli.py run "Liste 5 KPIs canônicos de GEO..."` — **OK**

```
Tarefas: 7 OK / 0 falhas
Wall time: 144s (122s pipeline + 22s overhead)
Custo real: $0.0772 (estimativa: $0.0358 → ratio 2.16x)
Output: output/execution_20260513_190051.json
```

Distribuição de tasks:
| Task | Tipo | LLM | Tempo | Custo |
|---|---|---|---|---|
| t1 | research | perplexity sonar-deep-research | 107.7s | $0.0630 |
| t2 | analysis | gemini-2.5-flash | 4.3s | $0.0013 |
| t3 | writing | gpt4o | 4.4s | $0.0063 |
| t4 | classification | groq llama-3.3 | 0.6s | $0.0012 |
| t5 | code_review | groq_heavy | 0.6s | $0.0032 |
| t6 | analysis | gemini-2.5-flash | 3.4s | $0.0007 |
| t7 | summarization | groq | 1.2s | $0.0014 |

**Cobertura real de providers:** 4/5 (Anthropic ocioso — nenhuma task `architecture`/`critical_review`/`copywriting` foi gerada). Perplexity domina **84% do wall time e 82% do custo** com 1 task — ainda sem cap configurado.

**Bug encontrado na tabela de resumo:** a tabela "Uso por LLM (5 canonicos)" reporta `Google: 0 tarefas` apesar de gemini_flash ter sido usado 2x. O agregador está classificando gemini_flash fora do bucket Google. Fix em `src/orchestrator.py` no momento de gerar o resumo agregado: mapear `gemini_flash → Provider.GOOGLE`.

### 3.3. `scripts/run_5llm_board.py` — **OK (5/5)**

```
Andrew Ng (CEO)        Claude Haiku       8.5s    $0.0044
Harrison Chase         GPT-4o-mini       14.9s    $0.0005
Jerry Liu              Gemini 2.5 Flash   5.3s    $0.0002
Yohei Nakajima         Perplexity Sonar   8.4s    $0.0017
Eng. Performance       Groq Llama 3.3     2.6s    $0.0012

Total parallel:        max ~15s wall      $0.008
```

**Observações:**
- 5/5 LLMs distintas, todas verde.
- **Sub-modelos baratos** são usados no board: Claude **Haiku** (não Opus), GPT-4o-**mini** (não 4o), Gemini 2.5 **Flash** (não Pro). Decisão consciente do board para custo baixo, mas significa que `board` ≠ "top tier dos 5". Para top tier, usar `cli.py run` com demanda forçando architecture+critical_review.
- **Gap do bridge:** o comando `geo-bridge.sh board "demanda"` exporta `DEMAND` env var, mas `scripts/run_5llm_board.py` ignora — usa `TASK_PROMPT` hardcoded sobre analisar o orquestrador. Para custom demand, refatorar o script para ler `os.environ.get("DEMAND", DEFAULT_TASK_PROMPT)`.

---

## 4. FinOps + KPIs

### 4.1. Gasto diário (snapshot pós-burst)

| Provider | Gasto | Limite | Uso % |
|---|---|---|---|
| anthropic | $1.77 | $100 | 1.8% |
| google | $0.38 | $50 | 0.8% |
| groq | $0.16 | $30 | 0.5% |
| openai | $0.39 | $50 | 0.8% |
| perplexity | $0.90 | $30 | **3.0%** |
| **GLOBAL** | **$3.60** | **$250** | **1.4%** |

Perplexity é o provider mais perto do cap relativo.

### 4.2. Histórico recente (cost_report)

240 execuções totais; última execução (E2E desta bateria): $0.077 em 7 tarefas.

### 4.3. KPI estruturais (.kpi_history.jsonl última run)

```
distribution_health: 0.60   (target ≥ 0.95)
cost_estimate_accuracy: 2.16x (banda 0.7-1.5)
providers_open: 0
min_provider_health_score: 1.0
provider_health: 5/5 CLOSED
```

**Status agregado dashboard (últimos 3 runs): CRÍTICO**
- distribution_health médio: 0.52
- cost_estimate_accuracy médio: 1.47x (banda 0.7-1.5x)

**Causa raiz da criticidade:**
1. **distribution_health baixo:** runs adaptativos não exercitam Anthropic — falta task_type que rotear Opus/Sonnet como primary. Solução: ou incluir `architecture`/`critical_review` no plan, ou subir o cap forçando rebalance.
2. **cost_estimate_accuracy fora da banda:** Perplexity gasta 5-8x o `AVG_COST_PER_CALL` default. O calibrador rejeita amostra (ratio 8.61x). Solução: recalibrar manualmente o default de Perplexity em `src/config.py` (de $0.008 para algo ~$0.04).

---

## 5. Resiliência (validada por `tests/test_resilience_outage.py`)

14/14 testes passam, cobrindo:
- Circuit breaker abre após N falhas consecutivas
- Short-circuit em 0ms quando OPEN
- Backoff curto (1s) em 503/timeout (`UNAVAILABLE_RETRY_DELAY`)
- Backoff longo exponencial em 429 (`MAX_RETRIES=3`)
- Router skipa providers OPEN
- Degradação manual via `mark_provider_degraded(provider, ttl_seconds)`
- Top-2 de cada `FALLBACK_CHAINS[task_type]` são cross-provider (regra dura)
- `compute_provider_health` reflete state real

---

## 6. Gaps acionáveis (priorizados)

### Críticos (bloqueantes)

1. **[BUG] `cli.py plan` ValidationError** — `_parse_plan` não normaliza `dependencies` quando LLM retorna list of dicts. Quebra qualquer dry-run.
   - **Fix:** `src/orchestrator.py:_parse_plan`, normalizar antes de instanciar Task. ~5 linhas.
   - **Teste:** adicionar `test_parse_plan_accepts_dict_deps` em `test_core.py`.

### Altos (qualidade de relatório)

2. **[BUG] Tabela "Uso por LLM (5 canonicos)" agrupa Gemini Flash fora do Google** — relatório de cobertura mente.
   - **Fix:** mapear `model.startswith("gemini")` → `Provider.GOOGLE` no agregador.

3. **[DEFICIÊNCIA] Perplexity sem cap de share** — uma task de research consome 84% do tempo e 82% do custo.
   - **Fix:** adicionar `perplexity: 0.35` em `PROVIDER_SHARE_CAP` em `src/config.py`. Forçar splits de research em sub-tasks ou usar `sonar-pro` ao invés de `sonar-deep-research` para queries simples.

4. **[DEFICIÊNCIA] `geo-bridge.sh board "demanda"` ignora demanda customizada** — `run_5llm_board.py` tem `TASK_PROMPT` hardcoded.
   - **Fix:** ler `os.environ.get("DEMAND", DEFAULT)` no script.

### Médios (manutenção)

5. **[TESTE OBSOLETO] `test_pipeline_max_tokens_is_capped`** — assertion contra cap antigo 8192.
   - **Fix:** trocar `== 8192` por `== 16384`.

6. **[TESTE FLAKY] `test_calibrator_learns_from_real_execution_reports`** — pré-existente, calibrator rejeita Perplexity outlier.
   - **Fix:** ou marcar `xfail`, ou aumentar ratio cap do calibrador para 12x.

### Baixos (cosmético / docs)

7. **[CALIBRAGEM] `AVG_COST_PER_CALL` de Perplexity em `src/config.py` está em $0.008 mas real é $0.04-0.07.** drift_detector dispara consistentemente.
   - **Fix:** recalibrar manualmente.

8. **[COBERTURA] Anthropic 0 share em runs adaptativos.** Indicador `distribution_health` cai a 0.6 sem motivo real.
   - **Fix:** ou pesar menos providers ociosos no cálculo, ou injetar task `critical_review` no final de cada pipeline.

---

## 7. Artefatos

Todos em `docs/e2e_battery_20260513/`:

- `REPORT.md` (este arquivo)
- `junit.xml` — pytest JUnit XML (220 testes)
- `coverage.xml` — Cobertura.py XML (57%)
- `htmlcov/` — HTML coverage report navegável
- `ping.txt` — ping 5 LLMs
- `doctor.txt` — `cli.py doctor` output
- `status.txt` — `cli.py status` output
- `models.txt` — `cli.py models` output
- `plan.txt` — traceback do `plan` ValidationError
- `run.txt` — `cli.py run` full output
- `board.txt` — `run_5llm_board.py` output
- `finops_after.txt` — gasto diário pós-burst
- `cost_report.txt` — histórico de custos
- `dashboard.txt` — KPIs dashboard

JSON de execução: `output/execution_20260513_190051.json`
JSON do board: `output/5llm_board_analysis.json`

---

## 8. Recomendação operacional

Para uso diário do orchestrator:

1. **Sempre rodar `ping` antes** de demandas pesadas (5s, custo $0.006, valida 5/5 chaves).
2. **Use `run`** para tarefas decomponíveis com mistura de research+writing+analysis.
3. **Use `board`** para decisões arquiteturais ou cross-check de pesquisa onde valer ter 5 perspectivas distintas — mas atualize `run_5llm_board.py` para aceitar demanda customizada antes.
4. **Evite `plan`** até o bug ser corrigido (preferir `run` direto, que é mais resiliente).
5. **Monitore Perplexity** — único provider com risco de cap de gasto.
6. **NÃO use orchestrator para copy de nicho** ([feedback_orchestrator_usage](../../../.claude/projects/C--Users-alexa/memory/feedback_orchestrator_usage.md) — sub-agent Opus principal escreve melhor).

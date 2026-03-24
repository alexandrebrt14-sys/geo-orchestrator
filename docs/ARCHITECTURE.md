# Arquitetura Tecnica — geo-orchestrator

Documento tecnico descrevendo a arquitetura do orquestrador multi-LLM da Brasil GEO.

---

## Visao geral do sistema

```
+===========================================================================+
|                            geo-orchestrator                               |
+===========================================================================+
|                                                                           |
|  +-------------------+                                                    |
|  |     CLI (Click)   |  <-- Entrada: demanda em linguagem natural         |
|  +--------+----------+                                                    |
|           |                                                               |
|           v                                                               |
|  +--------+----------+     +-------------------+                          |
|  |   Orchestrator     +---->|  Claude Opus      |  Decomposicao           |
|  |   (orchestrator.py)|<----+  (via LLMClient)  |                          |
|  +--------+----------+     +-------------------+                          |
|           |                                                               |
|           | Deduplicacao + Cache check + Budget guard                     |
|           v                                                               |
|  +--------+----------+     +-------------------+                          |
|  |   Router           +---->| Score adaptativo  |  Stats historicas        |
|  |   (router.py)      |<----+ + Tabela estatica |                          |
|  +--------+----------+     +-------------------+                          |
|           |                                                               |
|           v                                                               |
|  +--------+----------+     +-------------------+                          |
|  |   Pipeline         +---->| RateLimiter       |  Token bucket/provider  |
|  |   (pipeline.py)    |     | (rate_limiter.py) |                          |
|  +--+--+--+--+-------+     +-------------------+                          |
|     |  |  |  |                                                            |
|     v  v  v  v                                                            |
|  +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+               |
|  |              LLMClient (llm_client.py)               |               |
|  |  Retry + Backoff + Retry-After + Rate Limiting       |               |
|  |                                                       |               |
|  |  +----------+ +---------+ +----------+ +----------+  |               |
|  |  | Anthropic| | OpenAI  | |  Google  | |Perplexity|  |               |
|  |  | (Claude) | | (GPT4o) | | (Gemini) | | (Sonar)  |  |               |
|  |  +----------+ +---------+ +----------+ +----------+  |               |
|  +-------------------------------------------------------+               |
|           |                                                               |
|           | TaskResult[]                                                  |
|           v                                                               |
|  +--------+----------+     +-------------------+                          |
|  |   CostTracker      |     | output/           |                          |
|  |   (cost_tracker.py) +--->| - execution.json  |                          |
|  +--------+----------+     | - .cache/          |                          |
|           |                 | - .checkpoint.json |                          |
|           v                 | - .router_stats    |                          |
|  +--------+----------+     | - cost_history     |                          |
|  |  ExecutionReport   |     +-------------------+                          |
|  |  (Gantt + custos)  |                                                    |
|  +-------------------+                                                    |
+===========================================================================+

APIs externas:
  [Anthropic API]  <---> LLMClient (decomposicao + code + review)
  [OpenAI API]     <---> LLMClient (writing + copywriting + seo)
  [Google AI API]  <---> LLMClient (analysis + classification + summarization)
  [Perplexity API] <---> LLMClient (research + fact_check)
```

---

## Fluxo de dados

### 1. Entrada

```
Usuario -> CLI (cli.py) -> string (demanda em PT-BR)
```

### 2. Decomposicao (Orchestrator)

```
demanda -> Claude Opus (via LLMClient) -> JSON {
  tasks: [{ id, type, description, dependencies, expected_output }]
}
```

O parser `_parse_plan()` trata:
- Markdown fences
- JSON parcial
- Tipos invalidos (mapeados para "writing")
- Fallback para tarefa unica se tudo falhar

### 3. Deduplicacao

```
tasks[] -> word_overlap_similarity() -> tasks_deduplicados[]
```

Tarefas do mesmo tipo com cosine similarity > 0.7 sao fundidas. A descricao da tarefa duplicada e adicionada a tarefa mantida. Dependencias sao remapeadas.

### 4. Cache check

```
Para cada tarefa:
  cache_key = SHA256(type + description + dependency_ids)
  Se output/.cache/{key}.json existe e TTL < 24h:
    -> TaskResult (cache_hit=True)
  Senao:
    -> adicionar a fila de execucao
```

### 5. Budget guard

```
custo_estimado = sum(AVG_COST_PER_CALL[llm_roteado] para cada tarefa)
Se custo_estimado > BUDGET_LIMIT:
  -> BudgetExceededError (a menos que --force)
```

### 6. Execucao (Pipeline)

```
Para cada wave (sequencial entre waves):
  Separar tarefas Gemini (staggered) e nao-Gemini (paralelo)
  Para cada tarefa (paralelo dentro da wave):
    1. Resolver dependencias -> coletar TaskResults anteriores
    2. Otimizar contexto (truncar ou sumarizar via Gemini)
    3. Montar prompt: contexto + descricao + expected_output
    4. Router.route(task) -> LLMConfig
    5. RateLimiter.acquire(provider) -> aguardar slot
    6. LLMClient.query(prompt, system, max_tokens) -> LLMResponse
    7. Quality gate -> retry com fallback se falhar
    8. Router.update_stats() -> atualizar score adaptativo
    9. CostTracker.record() -> registrar custo
    10. Checkpoint -> salvar estado
```

### 7. Saida

```
dict[task_id -> TaskResult] -> ExecutionReport {
  plan, results, total_cost, total_duration_ms,
  tasks_completed, tasks_failed, tasks_cached,
  tasks_quality_retried, tasks_deduplicated,
  summary (Gantt + breakdown)
}
```

---

## Responsabilidades por modulo

| Modulo | Arquivo | Responsabilidade |
|--------|---------|-----------------|
| CLI | `cli.py` | Parsing de argumentos, orquestracao das 3 fases, exibicao formatada (Rich) |
| Config | `src/config.py` | LLM configs, task routing, FinOps limits, budget, cache TTL |
| Models | `src/models.py` | Pydantic models: Task, Plan, TaskResult, LLMResponse, ExecutionReport |
| Orchestrator | `src/orchestrator.py` | Decomposicao, deduplicacao, cache, budget guard, report builder |
| Pipeline | `src/pipeline.py` | Execucao por waves, checkpoints, quality gates, fallback, context optimization |
| Router | `src/router.py` | Roteamento adaptativo, fallback chains, stats tracking |
| LLMClient | `src/llm_client.py` | Cliente HTTP unificado, retry com backoff, 4 providers |
| RateLimiter | `src/rate_limiter.py` | Token bucket por provider, singleton, stagger Gemini |
| CostTracker | `src/cost_tracker.py` | Acumulacao de custos, breakdown por LLM/tarefa, relatorio Markdown |
| Agents (legacy) | `src/agents/` | BaseAgent, ResearcherAgent, WriterAgent, ArchitectAgent, AnalyzerAgent |
| Templates | `src/templates/` | Prompts de decomposicao e system prompts por agente |

---

## Estrategia de tratamento de erros

### Nivel do LLMClient

- **Retry com backoff exponencial**: 2s, 4s, 8s + jitter aleatorio (0-1s)
- **Retry-After**: Respeita header HTTP em respostas 429
- **Codigos retentaveis**: 429 (rate limit), 500, 502, 503 (server errors), timeouts
- **Max retries**: 2 (total 3 tentativas)
- **Prompt validation**: Rejeita prompts vazios e max_tokens <= 0

### Nivel do Pipeline

- **Fallback entre LLMs**: Se o primario falhar, tenta o fallback configurado
- **Quality gate retry**: Se o output passar na execucao mas falhar no quality gate, tenta o fallback
- **asyncio.gather**: Falhas individuais nao abortam a wave (resultados sao `TaskResult(success=False)`)
- **Dependencias quebradas**: Se wave detectar dependencias irresoluveis, adiciona tarefas como wave final

### Nivel do Orchestrator

- **Budget exceeded**: Lanca `BudgetExceededError` (nao e retry, e bloqueio)
- **Cost overrun**: Warning se custo real > 2x estimativa
- **Parse failure**: Fallback para tarefa unica do tipo "writing"

### Nivel do Checkpoint

- **Checkpoint corrompido**: Ignorado silenciosamente (retorna set vazio)
- **Demanda diferente**: Checkpoint de outra demanda e ignorado
- **Limpeza automatica**: Checkpoint removido apos execucao completa

---

## Estrategia de otimizacao de custos

### Roteamento por custo-beneficio

O principio: usar o LLM mais barato que atende a qualidade para cada tipo de tarefa.

| Tarefa | LLM | Custo/1M in | Alternativa cara | Economia |
|--------|-----|------------|------------------|----------|
| Analise | Gemini (US$ 0.15) | Claude (US$ 15.00) | ~99% |
| Pesquisa | Perplexity (US$ 1.00) | Claude + busca manual | ~93% |
| Redacao | GPT-4o (US$ 2.50) | Claude (US$ 15.00) | ~83% |
| Codigo | Claude (US$ 15.00) | (insubstituivel) | — |

### Deduplicacao

Tarefas similares sao fundidas antes da execucao, eliminando chamadas redundantes.

### Cache

Resultados sao cacheados por 24h. Reexecucoes da mesma demanda custam zero para tarefas cacheadas.

### Context optimization

Outputs longos de dependencias sao:
1. Primeiro tentativa: sumarizados via Gemini (custo minimo)
2. Fallback: truncados (primeiros 800 + ultimos 200 caracteres)

Isso reduz tokens de entrada em tarefas que dependem de outputs extensos.

### Paralelismo como otimizacao de tempo

Waves paralelas reduzem tempo total. Uma pipeline com 7 tarefas em 4 waves leva ~4x o tempo de uma tarefa (nao 7x).

---

## Seguranca

### Chaves de API

- **Nunca logadas**: `LLMConfig.__repr__()` e `__str__()` ocultam chaves
- **Nunca em cache**: Apenas resultados sao cacheados, nunca credenciais
- **Leitura via `os.environ`**: Chaves nao sao armazenadas em variaveis alem do necessario
- **Warning em missing**: Se uma chave estiver ausente, e logado warning (nunca o valor)

### Dados sensiveis

- `.env` esta no `.gitignore`
- Outputs sao salvos localmente em `output/` (nao comitados)
- Stats do router (`output/.router_stats.json`) contem apenas metricas, nunca conteudo

---

## Observabilidade

### Arquitetura de tracing

```
Orchestrator.run()
  |
  +-- decompose()          -> custo de decomposicao
  +-- deduplicate()        -> contagem de merges
  +-- cache check          -> contagem de cache hits
  +-- budget guard         -> estimativa vs limite
  |
  +-- Pipeline.execute()
  |     |
  |     +-- Wave 1
  |     |     +-- task t1  -> TaskResult (llm, cost, duration, tokens, success)
  |     |     +-- task t2  -> TaskResult (...)
  |     |
  |     +-- Wave 2
  |           +-- task t3  -> TaskResult (quality_retried=True)
  |
  +-- ExecutionReport
        +-- Timeline Gantt (ASCII)
        +-- Wave timings
        +-- Cost breakdown by LLM
        +-- Cost breakdown by task
        +-- Token efficiency
```

### Metricas registradas por tarefa

| Campo | Tipo | Descricao |
|-------|------|-----------|
| `task_id` | str | Identificador da tarefa |
| `llm_used` | str | Nome do LLM que executou |
| `cost` | float | Custo em USD |
| `duration_ms` | int | Tempo de execucao em ms |
| `tokens_input` | int | Tokens de entrada |
| `tokens_output` | int | Tokens de saida |
| `success` | bool | Se a tarefa foi bem-sucedida |
| `cache_hit` | bool | Se o resultado veio do cache |
| `quality_retried` | bool | Se houve retry via quality gate |
| `wave_index` | int | Indice da wave em que executou |
| `start_time_ms` | int | Timestamp relativo ao inicio da execucao |

### Persistencia

| Arquivo | Conteudo |
|---------|---------|
| `output/execution_{timestamp}.json` | Relatorio completo da execucao |
| `output/.cache/{hash}.json` | Resultados cacheados por tarefa |
| `output/.checkpoint.json` | Estado para retomada |
| `output/.router_stats.json` | Estatisticas do router adaptativo |
| `output/.results/{task_id}.json` | Resultado individual por tarefa |
| `output/cost_history.jsonl` | Log incremental de custos |

---

## Decisoes de design

### Por que httpx e nao SDKs oficiais?

Uniformidade. Cada provider (Anthropic, OpenAI, Google, Perplexity) tem um SDK com interface diferente. Usar httpx diretamente permite:
- Interface consistente no `LLMClient`
- Controle total sobre headers, timeouts, retries e rate limiting
- Menos dependencias (httpx e a unica dep HTTP)

### Por que Pydantic para models?

Validacao automatica de tipos, serializacao JSON nativa, e suporte a `model_validate()` para reconstruir objetos de cache/checkpoint.

### Por que token bucket para rate limiting?

O token bucket permite burst controlado (ex: 3 requests simultaneos para Anthropic) enquanto mantem o RPM medio dentro do limite. Alternativas como janela deslizante simples nao permitem burst.

### Por que decomposicao via LLM?

O Claude entende nuances de demandas em linguagem natural, adapta-se a qualquer tipo de pedido e pode estimar complexidade. Regras fixas ou regex seriam inflexiveis e frageis.

### Por que score adaptativo no router?

O roteamento estatico e bom para comecar, mas na pratica alguns LLMs podem ter disponibilidade variavel, latencia inesperada ou falhas em tipos especificos. O score adaptativo ajusta o roteamento baseado em dados reais.

### Por que waves e nao grafo de dependencias completo?

Waves sao mais simples de implementar e depurar. Cada wave e um "nivel" no DAG de dependencias. O resultado e equivalente a executar o grafo com paralelismo maximo, mas com codigo mais legivel e checkpoints naturais entre waves.

### Por que stagger para Gemini?

O Gemini com billing ativo permite 30 RPM (R$500 credito pago). Sem stagger, multiplas tarefas Gemini na mesma wave ainda podem causar 429 em rajadas. O stagger garante gap minimo de 2s entre requests Gemini.

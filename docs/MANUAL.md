# Manual Tecnico — geo-orchestrator

Manual completo do orquestrador multi-LLM da Brasil GEO.

---

## Conceitos fundamentais

### Orchestrator

O `Orchestrator` (em `src/orchestrator.py`) e o cerebro do sistema. Ele recebe uma demanda em linguagem natural e coordena todo o fluxo: decomposicao via Claude, deduplicacao de tarefas similares, verificacao de cache, budget guard, execucao via Pipeline e compilacao do relatorio final (`ExecutionReport`).

### Router

O `Router` (em `src/router.py`) decide qual LLM executa cada tarefa. Usa uma tabela estatica de roteamento (`TASK_TYPES` em `config.py`) como base, mas aplica um sistema de scoring adaptativo que aprende com execucoes anteriores. O score combina taxa de sucesso (60%), custo (20%) e latencia (20%).

### Pipeline

O `Pipeline` (em `src/pipeline.py`) e o engine de execucao. Computa waves de execucao (tarefas independentes em paralelo), gerencia checkpoints, aplica quality gates, faz fallback automatico entre LLMs e otimiza contexto entre tarefas.

### LLMClient

O `LLMClient` (em `src/llm_client.py`) e o cliente HTTP unificado para os 4 providers. Encapsula diferencas de API (Anthropic, OpenAI, Google, Perplexity), implementa retry com backoff exponencial, respeita headers `Retry-After` e integra com o rate limiter.

### Models

Os modelos Pydantic (em `src/models.py`) definem as estruturas de dados do dominio: `Task`, `Plan`, `TaskResult`, `LLMResponse` e `ExecutionReport`.

### CostTracker

O `CostTracker` (em `src/cost_tracker.py`) acumula registros de custo por tarefa e por LLM durante uma execucao, e gera relatorios em formato dicionario ou Markdown.

### RateLimiter

O `RateLimiter` (em `src/rate_limiter.py`) e um singleton que gerencia token buckets por provider, garantindo que limites de RPM sejam respeitados mesmo com multiplas tarefas concorrentes.

---

## Como funciona a decomposicao

### Fluxo

1. O usuario envia uma demanda em linguagem natural.
2. O `Orchestrator.decompose()` chama o Claude (modelo configurado em `LLM_CONFIGS["claude"]`) com o prompt de decomposicao.
3. O prompt (definido em `src/orchestrator.py` como `DECOMPOSE_SYSTEM`) instrui o Claude a quebrar a demanda em tarefas tipadas com dependencias.
4. O Claude retorna JSON puro com a lista de tarefas.
5. O parser (`_parse_plan()`) valida tipos, extrai dependencias e cria objetos `Task`.

### Prompt de decomposicao

O prompt lista os 12 tipos de tarefa disponiveis e suas regras:
- Cada tarefa tem `id`, `type`, `description`, `dependencies` e `expected_output`
- Tipos validos: research, analysis, writing, copywriting, code, review, seo, data_processing, fact_check, classification, translation, summarization
- Tarefas sem dependencia mutua rodam em paralelo

### Exemplo de JSON retornado

```json
{
  "tasks": [
    {
      "id": "t1",
      "type": "research",
      "description": "Pesquisar estado da arte em GEO com fontes atualizadas",
      "dependencies": [],
      "expected_output": "texto com citacoes"
    },
    {
      "id": "t2",
      "type": "research",
      "description": "Mapear concorrentes em GEO",
      "dependencies": [],
      "expected_output": "lista com URLs"
    },
    {
      "id": "t3",
      "type": "analysis",
      "description": "Consolidar dados de t1 e t2",
      "dependencies": ["t1", "t2"],
      "expected_output": "json estruturado"
    },
    {
      "id": "t4",
      "type": "writing",
      "description": "Redigir artigo completo baseado em t3",
      "dependencies": ["t3"],
      "expected_output": "markdown"
    },
    {
      "id": "t5",
      "type": "review",
      "description": "Revisar qualidade e consistencia",
      "dependencies": ["t4"],
      "expected_output": "json com issues e score"
    }
  ]
}
```

### Fallback de parsing

Se o Claude retornar JSON invalido, o parser tenta:
1. Remover markdown fences (` ```json ... ``` `)
2. Encontrar o primeiro `{` e ultimo `}` no texto
3. Se tudo falhar, cria uma tarefa unica do tipo `writing` com a demanda original

---

## Como funciona o roteamento

### Tabela estatica

A tabela `TASK_TYPES` em `config.py` define o LLM primario e o fallback para cada tipo de tarefa:

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

### Score adaptativo

O Router mantem estatisticas historicas em `output/.router_stats.json`. Para cada combinacao `task_type:llm_name`, registra:
- Numero de sucessos e falhas
- Latencia total
- Custo total

A formula de score:

```
score = (success_rate * 0.6) + (1/cost_normalizado * 0.2) + (1/latency_normalizada * 0.2)
```

- `success_rate`: fracao de sucesso (otimistic default 0.8 para combos nao testados)
- `cost_normalizado`: `min(1.0 / (avg_cost * 100), 1.0)` — menor custo = melhor
- `latency_normalizada`: `min(1000.0 / avg_latency_ms, 1.0)` — menor latencia = melhor

Sao necessarias no minimo 3 amostras (`_MIN_SAMPLES`) antes de confiar nas estatisticas. Se um LLM tem taxa de falha acima de 30% (`_FAILURE_THRESHOLD`), ele e desprioritizado.

### Cadeia de fallback

1. Tenta o LLM sugerido pelo score adaptativo (se houver dados suficientes)
2. Se indisponivel, usa o primario da tabela estatica
3. Se indisponivel, usa o fallback da tabela estatica
4. Se indisponivel, tenta qualquer LLM com chave de API configurada
5. Se nenhum disponivel, lanca `RuntimeError`

---

## Como funciona o paralelismo

### Waves

O Pipeline computa waves de execucao via ordenacao topologica:

- **Wave 1**: Tarefas sem dependencias (todas rodam em paralelo)
- **Wave 2**: Tarefas que dependem apenas de tarefas da Wave 1
- **Wave N**: Tarefas que dependem apenas de tarefas das Waves 1..N-1

```
Wave 1: [T1, T2]      <-- executam ao mesmo tempo
         |
Wave 2: [T3]           <-- espera Wave 1 terminar
         |
Wave 3: [T4, T5]      <-- executam ao mesmo tempo
         |
Wave 4: [T6]           <-- espera Wave 3 terminar
```

Dentro de cada wave, tarefas nao-Gemini executam via `asyncio.gather()`. Tarefas Gemini sao escalonadas separadamente.

### Gemini stagger

O Gemini com billing ativo permite 30 RPM (R$500 credito pago). Quando uma wave contem multiplas tarefas Gemini, elas sao executadas sequencialmente com gaps calculados pelo rate limiter:

```python
gemini_interval = limiter.min_interval(Provider.GOOGLE)  # 6.0s
```

Tarefas de outros providers na mesma wave continuam em paralelo.

### Deteccao de ciclos

Se o algoritmo de waves detectar dependencias circulares ou irresoluveis, as tarefas restantes sao adicionadas como uma wave final (falharao graciosamente com contexto incompleto).

---

## Rate Limiting

### Token Bucket

Cada provider tem um `TokenBucket` com:
- `requests_per_minute`: RPM maximo
- `burst_size`: maximo de requests simultaneos antes de throttling
- `refill_rate`: tokens por segundo (RPM / 60)

O bucket comeca cheio ate `burst_size`. Cada request consome 1 token. Tokens sao recarregados continuamente.

### Limites por provider

| Provider | RPM | Burst |
|----------|----:|------:|
| Anthropic | 60 | 3 |
| OpenAI | 60 | 3 |
| Google (Gemini) | 30 | 3 |
| Perplexity | 20 | 2 |

### Comportamento

Quando nao ha tokens disponiveis, `acquire()` calcula o tempo de espera e bloqueia a coroutine. O lock e liberado durante o sleep para que outras coroutines possam verificar seus proprios buckets.

O rate limiter e um singleton (`RateLimiter.get_instance()`) compartilhado por todo o processo.

---

## FinOps

### Limites diarios

Configurados em `config.py` via variaveis de ambiente:

```python
FINOPS_DAILY_LIMITS = {
    "anthropic":  0.50,   # FINOPS_LIMIT_ANTHROPIC
    "openai":     0.50,   # FINOPS_LIMIT_OPENAI
    "google":     0.50,   # FINOPS_LIMIT_GOOGLE (billing ativo)
    "perplexity": 0.50,   # FINOPS_LIMIT_PERPLEXITY
}
FINOPS_DAILY_GLOBAL = 1.50   # FINOPS_LIMIT_GLOBAL
```

### Budget Guard

1. **Pre-execucao**: O Orchestrator estima o custo total usando `AVG_COST_PER_CALL` por LLM:
   - claude: US$ 0.04
   - gpt4o: US$ 0.012
   - gemini: US$ 0.001
   - perplexity: US$ 0.005

2. Se a estimativa exceder `BUDGET_LIMIT` (padrao US$ 1.00, via `GEO_BUDGET_LIMIT`), lanca `BudgetExceededError`.

3. **Runtime**: Se o custo real ultrapassar 2x a estimativa, emite warning no log.

4. **Override**: `--force` no CLI ignora o budget guard.

### Rastreamento de custos

O `CostTracker` registra cada chamada LLM com:
- `task_id`, `llm`, `tokens_in`, `tokens_out`, `cost`

O metodo `summary()` retorna breakdown por LLM e por tarefa. O metodo `to_markdown()` gera relatorio formatado.

---

## Observabilidade

### Timeline Gantt

O relatorio final inclui uma timeline ASCII mostrando a execucao temporal de cada tarefa:

```
  [ t1] |++++++..............................| perple    1200ms
  [ t2] |++++++..............................| perple    1100ms
  [ t3] |......++++..........................| gemini     800ms
  [ t4] |..........+++++++++++...............| gpt4o     2200ms
  [ t5] |.....................++++++++++.....| claude    2800ms

  Legend: + = success, X = failed, C = cached, . = idle
  Timespan: 0ms - 8100ms
```

### Wave timings

Cada wave registra: indice, tarefas, tipos, duracao em ms.

### Breakdown de custos

- **Por LLM**: chamadas, custo total, tokens in/out
- **Por tarefa**: custo, LLM usado
- **Eficiencia de tokens**: tokens gerados vs caracteres uteis

### Logs

O sistema usa `logging` padrao do Python. Configure com `logging.basicConfig(level=logging.INFO)` para ver o fluxo completo.

---

## Cache e Checkpoints

### Cache de resultados

- **Chave**: SHA-256 de `task_type|description|dependency_ids`
- **Armazenamento**: `output/.cache/{hash}.json`
- **TTL**: Padrao 24h (configuravel via `GEO_CACHE_TTL` em segundos)
- **Fluxo**: Antes de executar, o Orchestrator verifica cache para cada tarefa. Se valido, pula execucao.

### Checkpoints

- **Armazenamento**: `output/.checkpoint.json`
- **Quando salvo**: Antes de cada wave
- **Conteudo**: Plan completo + resultados ja concluidos + wave atual
- **Retomada**: Se a pipeline detectar checkpoint com mesma demanda, restaura resultados e pula waves ja concluidas.
- **Limpeza**: Checkpoint e removido apos execucao completa bem-sucedida.
- **Retomada manual**: `Pipeline.resume(checkpoint_path, router)` recria o pipeline e continua da interrupcao.

---

## Quality Gates

### Validacao por tipo de tarefa

| Tipo | Verificacao | Criterio de falha |
|------|------------|-------------------|
| `writing`, `copywriting` | Tamanho minimo | Output < 200 caracteres |
| `writing`, `copywriting` | Secoes vazias | Padrao `## Header\n\n## Header` detectado |
| `code` | Balanceamento | `{`, `}`, `[`, `]`, `(`, `)` desbalanceados |
| `code` | Marcadores pendentes | `TODO` ou `FIXME` encontrados |
| `research` | Fontes | Nenhuma URL, `[1]`, `fonte` ou `referencia` encontrada |

### Fluxo de retry

1. Tarefa executada no LLM primario
2. Quality gate verifica o output
3. Se falhar: tenta o LLM de fallback com o mesmo prompt
4. Se o fallback tambem falhar o quality gate: mantém o resultado original (marcado com `quality_retried=True`)

---

## Adicionando novos LLMs

1. **Adicionar configuracao em `src/config.py`**:
   ```python
   LLM_CONFIGS["novo_llm"] = LLMConfig(
       name="novo_llm",
       provider=Provider.OPENAI,  # ou novo Provider
       model="modelo-id",
       api_key_env="NOVO_LLM_API_KEY",
       strengths=["strength1", "strength2"],
       cost_per_1k_input=0.001,
       cost_per_1k_output=0.005,
       max_tokens=4096,
       role="Descricao do papel do LLM",
   )
   ```

2. **Se for um provider novo**, adicionar enum em `Provider` e implementar `_call_novo_provider()` em `src/llm_client.py`.

3. **Adicionar ao roteamento em `config.py`** nas `TASK_TYPES` relevantes.

4. **Configurar rate limit** em `src/rate_limiter.py`:
   ```python
   PROVIDER_LIMITS[Provider.NOVO] = ProviderLimit(requests_per_minute=30, burst_size=2)
   ```

5. **Adicionar custo medio** em `AVG_COST_PER_CALL` para o budget guard.

6. **Adicionar variavel** no `.env.example`.

---

## Adicionando novos tipos de tarefa

1. **Adicionar roteamento em `src/config.py`**:
   ```python
   TASK_TYPES["novo_tipo"] = TaskRouting(primary="gemini", fallback="gpt4o")
   ```

2. **Atualizar o prompt de decomposicao** em `src/orchestrator.py` (variavel `DECOMPOSE_SYSTEM`) para incluir o novo tipo na lista.

3. **Opcionalmente**, adicionar quality gate em `Pipeline._quality_check()` para o novo tipo.

4. **Opcionalmente**, adicionar custo medio estimado em `AVG_COST_PER_CALL`.

---

## Troubleshooting

### Erro 429 (rate limit)

O sistema tem retry automatico com backoff exponencial (2s, 4s, 8s + jitter). Se o provider retornar header `Retry-After`, o sistema respeita o tempo indicado. Para Gemini (10 RPM), o stagger automatico espera 6s entre requests.

**Se persistir**: Verifique se nao ha outro processo usando a mesma chave de API. Considere aumentar `burst_size` ou reduzir paralelismo.

### Budget exceeded

```
BudgetExceededError: Custo estimado (US$ 1.20) excede o limite (US$ 1.00)
```

**Solucoes**:
- Use `--force` para ignorar o budget guard
- Aumente `GEO_BUDGET_LIMIT` no `.env`
- Simplifique a demanda para gerar menos tarefas

### Respostas vazias

Todos os agents tem fallback para respostas vazias ou JSON invalido. O parser tenta multiplas estrategias antes de criar uma tarefa generica. Se o output for consistentemente vazio, verifique:
- Chave de API valida (`python cli.py status`)
- Modelo correto (nao deprecado)
- Prompt nao excedendo o limite de tokens do modelo

### "Nenhum LLM disponivel"

```
RuntimeError: Nenhum LLM disponivel para a tarefa 't3'
```

Todas as chaves de API estao ausentes ou todos os LLMs estao marcados como rate-limited. Verifique o `.env`.

### Checkpoint corrompido

Se um checkpoint impedir a execucao, remova manualmente:
```bash
rm output/.checkpoint.json
```

### Cache desatualizado

Para forcar reexecucao ignorando cache:
```bash
rm -rf output/.cache/
```

Ou ajuste o TTL via `GEO_CACHE_TTL=0` no `.env`.

---

## Referencia da API

### `src/orchestrator.py`

| Classe/Metodo | Descricao |
|---|---|
| `Orchestrator(force=False)` | Inicializa com Router, cache e budget |
| `Orchestrator.decompose(demand)` | Decompoe demanda via Claude -> Plan |
| `Orchestrator.execute(plan)` | Executa plan via Pipeline -> dict[TaskResult] |
| `Orchestrator.run(demand)` | Pipeline completo: decompose -> dedup -> cache -> execute -> report |
| `BudgetExceededError` | Excecao quando custo estimado excede limite |

### `src/pipeline.py`

| Classe/Metodo | Descricao |
|---|---|
| `Pipeline(plan, router)` | Engine de execucao com checkpoints e quality gates |
| `Pipeline.execute()` | Executa waves com paralelismo -> dict[TaskResult] |
| `Pipeline.resume(checkpoint_path, router)` | Retoma execucao de checkpoint (classmethod) |

### `src/router.py`

| Classe/Metodo | Descricao |
|---|---|
| `Router()` | Inicializa com stats adaptativas |
| `Router.route(task)` | Retorna LLMConfig para tarefa (adaptativo + fallback) |
| `Router.get_fallback(task)` | Retorna LLMConfig de fallback |
| `Router.get_best_llm(task_type)` | Melhor LLM por score adaptativo |
| `Router.update_stats(task_type, llm, success, latency_ms, cost)` | Registra resultado |
| `Router.mark_rate_limited(llm)` | Marca LLM como rate-limited |

### `src/llm_client.py`

| Classe/Metodo | Descricao |
|---|---|
| `LLMClient(config)` | Cliente unificado para 4 providers |
| `LLMClient.query(prompt, system, max_tokens)` | Envia prompt com retry e rate limiting -> LLMResponse |

### `src/rate_limiter.py`

| Classe/Metodo | Descricao |
|---|---|
| `RateLimiter.get_instance()` | Singleton accessor |
| `RateLimiter.acquire(provider)` | Aguarda slot disponivel (async) |
| `RateLimiter.current_rpm(provider)` | RPM atual do provider |
| `RateLimiter.status()` | Status de todos os buckets |
| `RateLimiter.min_interval(provider)` | Intervalo minimo entre requests (segundos) |
| `TokenBucket(limit)` | Token bucket para um provider |
| `TokenBucket.acquire(provider_name)` | Aguarda e consome 1 token (async) |

### `src/cost_tracker.py`

| Classe/Metodo | Descricao |
|---|---|
| `CostTracker()` | Acumulador de custos |
| `CostTracker.record(task_id, llm, tokens_in, tokens_out, cost)` | Registra custo |
| `CostTracker.summary()` | Breakdown por LLM e por tarefa (dict) |
| `CostTracker.to_markdown()` | Relatorio formatado em Markdown |

### `src/config.py`

| Constante/Classe | Descricao |
|---|---|
| `Provider` | Enum: ANTHROPIC, OPENAI, GOOGLE, PERPLEXITY |
| `LLMConfig` | Dataclass com config de um LLM (model, costs, api_key) |
| `LLM_CONFIGS` | Dict com configs dos 4 LLMs |
| `TASK_TYPES` | Dict task_type -> TaskRouting(primary, fallback) |
| `BUDGET_LIMIT` | Limite de custo por execucao (padrao US$ 1.00) |
| `FINOPS_DAILY_LIMITS` | Limites diarios por provider |
| `FINOPS_DAILY_GLOBAL` | Limite diario global (padrao US$ 1.50) |
| `CACHE_TTL_SECONDS` | TTL do cache em segundos (padrao 86400) |
| `AVG_COST_PER_CALL` | Custo medio estimado por LLM |

### `src/models.py`

| Classe | Descricao |
|---|---|
| `Task` | Tarefa Pydantic com id, type, description, dependencies, status, cost |
| `Plan` | Plano de execucao com lista de Tasks |
| `TaskResult` | Resultado de execucao com output, custo, tokens, cache_hit, quality_retried |
| `LLMResponse` | Resposta bruta de um provider |
| `ExecutionReport` | Relatorio final com plan, results, totals, summary |
| `TaskStatus` | Enum: PENDING, RUNNING, COMPLETED, FAILED, SKIPPED |
| `TaskComplexity` | Enum: LOW, MEDIUM, HIGH |

### `src/agents/base.py`

| Classe/Funcao | Descricao |
|---|---|
| `TaskType` | Enum (legacy) com tipos de tarefa |
| `TaskResult` | Dataclass (legacy) com resultado de execucao |
| `BaseAgent` | Classe base abstrata para agentes |
| `format_context_from_results(results)` | Formata resultados para injecao de contexto |

### `cli.py`

| Comando | Descricao |
|---|---|
| `run <demanda>` | Pipeline completo com opcoes --dry-run, --verbose, --output-dir, --force |
| `plan <demanda>` | Apenas decompoe a demanda e mostra o plano |
| `status` | Status dos LLMs configurados (chaves, modelos) |
| `cost-report` | Historico de custos (ultimas 20 execucoes) |
| `models` | Lista modelos com precos e tarefas atribuidas |

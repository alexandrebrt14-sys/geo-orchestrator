# Arquitetura Técnica — geo-orchestrator

Documento técnico descrevendo a arquitetura do orquestrador multi-LLM da Brasil GEO.

---

## Diagrama do sistema

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
|  |   Decompositor    +---->|  Claude Sonnet    |  API Anthropic           |
|  |   (Fase 1)        |<----+  (decomposição)   |                          |
|  +--------+----------+     +-------------------+                          |
|           |                                                               |
|           | JSON: { tasks[], execution_plan }                             |
|           v                                                               |
|  +--------+----------+                                                    |
|  |   Executor de     |                                                    |
|  |   Pipeline        |                                                    |
|  |   (Fase 2)        |                                                    |
|  +--+--+--+--+-------+                                                    |
|     |  |  |  |                                                            |
|     v  v  v  v                                                            |
|  +--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+--+               |
|  |           Pool de Agentes (asyncio.gather)            |               |
|  |                                                       |               |
|  |  +-----------+  +---------+  +-----------+  +-------+ |               |
|  |  | Researcher|  | Writer  |  | Architect |  |Analyzer| |               |
|  |  | (Perplx.) |  | (GPT4o) |  | (Claude)  |  |(Gemini)| |               |
|  |  +-----------+  +---------+  +-----------+  +-------+ |               |
|  +-------------------------------------------------------+               |
|           |                                                               |
|           | TaskResult[]                                                  |
|           v                                                               |
|  +--------+----------+     +-------------------+                          |
|  |   Consolidador    +---->|  output/           |                          |
|  |   (Fase 3)        |     |  - execution.json  |                          |
|  +-------------------+     |  - cost_history    |                          |
|                             |  - task outputs    |                          |
|                             +-------------------+                          |
+===========================================================================+

APIs externas:
  [Anthropic] <---> Decompositor + ArchitectAgent
  [OpenAI]    <---> WriterAgent
  [Perplexity]<---> ResearcherAgent
  [Google AI] <---> AnalyzerAgent
```

---

## Fluxo de dados

### 1. Entrada

```
Usuário -> CLI -> string (demanda em PT-BR)
```

### 2. Decomposição

```
demanda -> Claude Sonnet -> JSON {
  tasks: [{ id, type, title, description, dependencies, complexity }],
  execution_plan: { parallel_groups: [{ group, tasks }] }
}
```

### 3. Execução por grupo

```
Para cada parallel_group (sequencial entre grupos):
  Para cada task no grupo (paralelo dentro do grupo):
    1. Resolver dependências -> coletar TaskResults anteriores
    2. Formatar contexto (format_context_from_results)
    3. Montar messages: [system_prompt, contexto, tarefa]
    4. Chamar LLM via httpx (async)
    5. Pós-processar resposta (JSON parse, code extraction, cleanup)
    6. Retornar TaskResult com output, tokens, custo, duração
```

### 4. Saída

```
TaskResult[] -> relatório JSON + arquivos individuais + log de custos
```

---

## Responsabilidades por módulo

### `cli.py`

- Parsear argumentos e opções do usuário
- Orquestrar as 3 fases (decomposição, execução, consolidação)
- Instanciar agentes com os clientes HTTP corretos
- Exibir progresso e resultados formatados (rich)
- Salvar relatórios e histórico de custos

### `src/agents/base.py`

- Definir interface abstrata para agentes (`BaseAgent`)
- Padronizar resultados (`TaskResult`)
- Enumerar tipos de tarefa (`TaskType`)
- Formatar contexto para injeção entre tarefas
- Calcular custos por execução

### `src/agents/researcher.py`

- Chamada à API Perplexity com `return_citations`
- Injeção de URLs de citação no texto
- Extração e validação de URLs
- Parse JSON com fallback para texto

### `src/agents/writer.py`

- Chamada à API OpenAI com temperatura 0.7
- Suporte a 5 modos de escrita
- Limpeza de preâmbulos do LLM
- Retorno em Markdown puro

### `src/agents/architect.py`

- Chamada à API Anthropic (formato messages)
- Extração de blocos de código com nomes de arquivo
- Separação de explicação e código
- Retorno estruturado com file_count

### `src/agents/analyzer.py`

- Chamada à API Google Gemini com `responseMimeType: application/json`
- Formato Gemini (contents + systemInstruction)
- Parse JSON com duplo fallback

### `src/templates/decomposition.py`

- Prompt detalhado para Claude decompor demandas
- Referência completa de tipos de tarefa
- Exemplo de entrada e saída
- Regras de decomposição

### `src/templates/agent_prompts.py`

- Prompts centralizados para todos os agentes
- Mapa `AGENT_PROMPTS` para lookup por tipo de tarefa
- Instruções por modo de escrita

---

## Estratégia de tratamento de erros

### Nível do agente

Cada agente captura exceções em `execute()` e retorna um `TaskResult` com `success=False` e `error` preenchido. Isso garante que uma falha em uma tarefa não interrompe o pipeline inteiro.

### Nível do pipeline

O executor usa `asyncio.gather(return_exceptions=True)` para que falhas individuais não abortem o grupo. Exceções são convertidas em `TaskResult` com erro.

### Nível da decomposição

Se a API Anthropic falhar na decomposição, o CLI aborta com código de saída 1 e mensagem de erro. Sem plano, não há como executar.

### Fallback de pós-processamento

Todos os agentes que esperam JSON têm fallback: se o parse falhar, o conteúdo bruto é encapsulado em uma estrutura padronizada com `confidence: "medium"`.

### O que NÃO é tratado (v1.0)

- Retry automático com backoff (planejado para v1.1)
- Circuit breaker por provider
- Fallback entre LLMs (ex: se Perplexity falhar, tentar GPT-4o para pesquisa)

---

## Estratégia de otimização de custos

### Roteamento por custo-benefício

O princípio fundamental: usar o LLM mais barato que atende à qualidade necessária para cada tipo de tarefa.

| Tarefa | LLM escolhido | Alternativa mais cara | Economia estimada |
|---|---|---|---|
| Análise de dados | Gemini Flash ($0.075/1M) | GPT-4o ($2.5/1M in) | ~97% |
| Pesquisa | Perplexity ($3/1M in) | Claude + busca manual | ~80% em tempo |
| Redação | GPT-4o ($2.5/1M in) | Claude Opus ($15/1M in) | ~83% |
| Código | Claude Opus | (insubstituível para qualidade) | — |

### Minimização de tokens

- Contexto é formatado de forma concisa (sem repetição de metadados)
- Tarefas sem dependência não recebem contexto desnecessário
- System prompts são fixos e otimizados para cada LLM

### Paralelismo como otimização de tempo

Embora não reduza custo em dinheiro, o paralelismo reduz o tempo total de execução. Uma pipeline com 7 tarefas em 5 grupos leva ~5x o tempo de uma tarefa individual (não 7x).

### Monitoramento

O histórico de custos em `output/cost_history.jsonl` permite identificar padrões e otimizar. Use `python cli.py cost-report` para análise.

---

## Decisões de design

### Por que httpx e não SDKs oficiais?

Uniformidade. Cada provider tem um SDK com interface diferente. Usar httpx diretamente permite:
- Interface consistente entre agentes
- Menos dependências
- Controle total sobre headers, timeouts e retries

### Por que Click e não argparse?

Click oferece subcomandos, help automático, validação de tipos e composição de comandos com menos boilerplate.

### Por que Rich?

Tabelas formatadas, árvores de dependência e barras de progresso tornam a saída do CLI legível e profissional. Essencial para depurar pipelines com múltiplas tarefas.

### Por que decomposição via LLM?

Alternativas consideradas:
- Regras fixas: inflexível, não entende demandas em linguagem natural
- Regex/NLP: complexo e frágil
- LLM (escolhido): entende nuances, adapta-se a qualquer demanda, pode estimar complexidade

O Claude Sonnet foi escolhido para decomposição por ser rápido e suficientemente inteligente para planejamento (não precisa de Opus para isso).

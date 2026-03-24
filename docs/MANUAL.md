# Manual do geo-orchestrator

Manual completo do orquestrador multi-LLM da Brasil GEO.

---

## Conceitos fundamentais

### Orquestrador

O orquestrador é o sistema central que coordena a execução de tarefas entre múltiplos LLMs. Ele recebe uma demanda em linguagem natural, decompõe em tarefas atômicas e gerencia o fluxo de execução.

### Roteador

O roteador decide qual LLM executa cada tarefa. A decisão é baseada no tipo de tarefa: pesquisa vai para Perplexity, redação para GPT-4o, código para Claude Opus, análise para Gemini Flash.

### Pipeline

O pipeline é a sequência de etapas que transforma uma demanda em resultados. Cada pipeline tem:
- **Decomposição**: quebrar a demanda em tarefas
- **Roteamento**: atribuir LLMs às tarefas
- **Execução**: rodar tarefas respeitando dependências
- **Consolidação**: reunir todos os resultados

### Agentes

Agentes são wrappers especializados para cada LLM. Cada agente tem:
- Um system prompt otimizado
- Lógica de chamada à API específica do provider
- Pós-processamento do output
- Cálculo de custos

---

## Como a decomposição funciona

Quando você envia uma demanda, o orquestrador usa Claude Sonnet para analisá-la e quebrá-la em tarefas. O prompt de decomposição (definido em `src/templates/decomposition.py`) instrui o modelo a:

1. Entender o objetivo final da demanda
2. Identificar as etapas necessárias
3. Atribuir um tipo a cada tarefa
4. Definir dependências entre tarefas
5. Agrupar tarefas para execução paralela
6. Estimar custos e tempo

### Exemplo de decomposição

**Demanda**: "Pesquise sobre GEO e escreva um artigo completo"

**Resultado da decomposição**:

```
Grupo 1 (paralelo):
  T1 [research] Pesquisar definição e estado da arte de GEO
  T2 [research] Pesquisar cases e dados de mercado

Grupo 2:
  T3 [analysis] Consolidar e analisar dados de T1 e T2

Grupo 3:
  T4 [writing] Redigir artigo completo baseado em T3

Grupo 4:
  T5 [review] Revisar qualidade, entidade e acentuação
```

T1 e T2 executam em paralelo. T3 espera ambos terminarem. T4 espera T3. T5 espera T4.

---

## Como o roteamento funciona

O roteamento é determinístico, baseado no tipo de tarefa:

| Tipo de tarefa | LLM alvo | Classe do agente | Justificativa |
|---|---|---|---|
| `research` | Perplexity sonar-pro | `ResearcherAgent` | Único com acesso a dados em tempo real e citação de fontes |
| `analysis` | Gemini Flash | `AnalyzerAgent` | Mais rápido e barato para processar dados estruturados |
| `data_processing` | Gemini Flash | `AnalyzerAgent` | Operações em lote com custo mínimo |
| `writing` | GPT-4o | `WriterAgent` | Melhor qualidade para texto longo em PT-BR |
| `architecture` | Claude Opus | `ArchitectAgent` | Raciocínio mais profundo para decisões técnicas |
| `code_generation` | Claude Opus | `ArchitectAgent` | Código de produção com tratamento de erros |
| `review` | Claude Opus | `ArchitectAgent` | Melhor em detectar edge cases e inconsistências |
| `deploy` | local | — | Executado via scripts locais, sem LLM |

---

## Como o paralelismo funciona

O sistema usa grupos paralelos (parallel_groups) para maximizar throughput:

```
Grupo 1: [T1, T2]      ← executam ao mesmo tempo
         ↓
Grupo 2: [T3]           ← espera grupo 1 terminar
         ↓
Grupo 3: [T4, T5]      ← executam ao mesmo tempo
         ↓
Grupo 4: [T6]           ← espera grupo 3 terminar
```

Dentro de cada grupo, todas as tarefas executam via `asyncio.gather()`. O próximo grupo só inicia quando todas as tarefas do grupo anterior terminaram.

O contexto de tarefas anteriores é injetado automaticamente. Quando T3 depende de T1 e T2, os resultados de ambos são formatados e passados como contexto no prompt de T3.

---

## Como o rastreamento de custos funciona

Cada agente calcula o custo da sua execução com base em:
- Tokens de entrada (prompt + contexto)
- Tokens de saída (resposta)
- Tabela de preços por modelo

O custo é registrado em cada `TaskResult` e consolidado no relatório final. Além disso, um log incremental é salvo em `output/cost_history.jsonl` para análise histórica.

### Fórmula

```
custo = (tokens_input / 1000) * custo_por_1k_input
      + (tokens_output / 1000) * custo_por_1k_output
```

### Comando de relatório

```bash
python cli.py cost-report
```

Mostra as últimas 20 execuções com custo total acumulado.

---

## Como adicionar novos tipos de tarefa

1. **Definir o tipo em `base.py`**:
   ```python
   class TaskType(str, Enum):
       # ... existentes ...
       SEO_AUDIT = "seo_audit"
   ```

2. **Criar ou reutilizar um agente** em `src/agents/`. Se o novo tipo usa um LLM existente, basta adicionar o mapeamento no CLI.

3. **Adicionar prompt em `agent_prompts.py`**:
   ```python
   SEO_AUDIT_PROMPT = """..."""
   AGENT_PROMPTS["seo_audit"] = SEO_AUDIT_PROMPT
   ```

4. **Atualizar o roteador em `cli.py`**:
   ```python
   elif task_type in ("seo_audit",):
       cfg = MODELS["gemini-flash"]
       # ...
   ```

5. **Atualizar o prompt de decomposição** em `decomposition.py` para incluir o novo tipo na lista de tipos disponíveis.

---

## Como adicionar novos LLMs

1. **Adicionar configuração em `cli.py`**:
   ```python
   MODELS["novo-modelo"] = {
       "name": "modelo-id",
       "provider": "ProviderName",
       "env_key": "PROVIDER_API_KEY",
       "tasks": ["tipo1", "tipo2"],
       "cost_1k_in": 0.001,
       "cost_1k_out": 0.005,
   }
   ```

2. **Criar classe do agente** em `src/agents/novo_agente.py`:
   - Herdar de `BaseAgent`
   - Implementar `_call_llm()` com a API do provider
   - Implementar `_post_process()` para o formato de saída

3. **Adicionar variável de ambiente** no `.env.example`.

4. **Atualizar `_create_agent()`** em `cli.py` para instanciar o novo agente.

5. **Atualizar a documentação**: CLAUDE.md, README.md, este manual.

---

## Resolução de problemas

### "ANTHROPIC_API_KEY não configurada"

A decomposição usa Claude Sonnet e requer a chave da Anthropic. Verifique o arquivo `.env`.

### Tarefa falha com timeout

O timeout padrão é 120 segundos. Para tarefas complexas de código ou redação longa, isso pode não ser suficiente. Ajuste o timeout em `_get_httpx_client()`.

### JSON inválido na resposta

Todos os agentes têm fallback para respostas não-JSON. O output é encapsulado em uma estrutura padrão com `confidence: "medium"`. Verifique o `raw_response` no relatório.

### Custo maior que o esperado

Verifique o relatório em `output/`. Tarefas com muito contexto (muitas dependências) consomem mais tokens de entrada. Considere reduzir dependências ou usar `--dry-run` para verificar o plano antes.

### Erro 429 (rate limit)

Aguarde alguns segundos e tente novamente. O sistema não implementa retry automático na versão atual. Para adicionar, implemente backoff exponencial em `_call_llm()` de cada agente.

### Resultado de pesquisa sem fontes

O Perplexity nem sempre retorna citações para todos os tópicos. Verifique se a demanda é específica o suficiente. Termos muito genéricos tendem a gerar respostas sem fontes.

---

## Referência de API por módulo

### `src/agents/base.py`

| Classe/Função | Descrição |
|---|---|
| `TaskType` | Enum com tipos de tarefa suportados |
| `TaskResult` | Dataclass com resultado de execução |
| `BaseAgent` | Classe base abstrata para agentes |
| `BaseAgent.execute(task, context, task_id)` | Executa tarefa completa |
| `format_context_from_results(results)` | Formata resultados para injeção |

### `src/agents/researcher.py`

| Classe/Função | Descrição |
|---|---|
| `ResearcherAgent` | Agente de pesquisa (Perplexity) |
| `_inject_citation_urls(content, citations)` | Substitui [1] por URLs |
| `_extract_urls(text)` | Extrai URLs de texto |

### `src/agents/writer.py`

| Classe/Função | Descrição |
|---|---|
| `WritingMode` | Enum: article, landing_page_copy, study, report, email |
| `WriterAgent` | Agente de redação (GPT-4o) |

### `src/agents/architect.py`

| Classe/Função | Descrição |
|---|---|
| `CodeBlock` | Dataclass com filename, language, content |
| `ArchitectAgent` | Agente de código/arquitetura (Claude Opus) |
| `_extract_code_blocks(content)` | Extrai blocos de código com nomes |

### `src/agents/analyzer.py`

| Classe/Função | Descrição |
|---|---|
| `AnalyzerAgent` | Agente de análise (Gemini Flash) |

### `src/templates/decomposition.py`

| Constante | Descrição |
|---|---|
| `DECOMPOSITION_PROMPT` | Prompt completo para decomposição de demandas |
| `TASK_TYPES_REFERENCE` | Referência dos tipos de tarefa disponíveis |

### `src/templates/agent_prompts.py`

| Constante | Descrição |
|---|---|
| `RESEARCHER_PROMPT` | System prompt do pesquisador |
| `WRITER_PROMPT` | System prompt do redator |
| `ARCHITECT_PROMPT` | System prompt do arquiteto |
| `ANALYZER_PROMPT` | System prompt do analista |
| `REVIEWER_PROMPT` | System prompt do revisor |
| `AGENT_PROMPTS` | Dicionário tipo_tarefa -> prompt |
| `WRITER_MODE_INSTRUCTIONS` | Instruções por modo de escrita |

### `cli.py`

| Comando | Descrição |
|---|---|
| `run <demanda>` | Pipeline completo |
| `run --dry-run` | Mostra plano sem executar |
| `run --verbose` | Mostra progresso detalhado |
| `run --output-dir` | Define diretório de saída |
| `plan <demanda>` | Apenas decompõe a demanda |
| `status` | Status dos LLMs configurados |
| `cost-report` | Histórico de custos |
| `models` | Lista modelos e preços |

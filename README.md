# geo-orchestrator

**5 LLMs** | **7.471 linhas** | **72 arquivos** | **14 commits** | **3 rodadas de melhoria multi-LLM**

---

## O que e

O geo-orchestrator e o orquestrador multi-LLM da Brasil GEO. Ele recebe uma demanda em linguagem natural, usa o Claude para decompor automaticamente em tarefas atomicas e discretas, e roteia cada tarefa para o LLM mais adequado com base em um sistema de scoring adaptativo. Tarefas independentes sao executadas em paralelo (waves), e os resultados de cada etapa alimentam as etapas seguintes como contexto otimizado.

O sistema inclui governanca FinOps completa com limites diarios por provider, budget guard pre-execucao, cache de resultados com TTL, checkpoints para retomada de execucoes interrompidas, quality gates por tipo de tarefa com retry automatico via fallback, rate limiting por provider com token bucket, deduplicacao inteligente de tarefas similares e observabilidade com timeline Gantt e breakdown de custos. A partir da rodada 3, inclui circuit breaker, dashboard de metricas, token budget allocator e memoria de agentes.

---

## 3 rodadas de melhoria com 5 LLMs

O geo-orchestrator passou por 3 rodadas de auto-aprimoramento executadas por uma banca de 5 LLMs, com custo total de **US$ 0.045**.

| Rodada | Foco | Principais entregas |
|--------|------|---------------------|
| **Round 1** | Fundacao | Orquestrador, pipeline, router adaptativo, 4 agentes, CLI, cache SHA-256, checkpoints, quality gates, budget guard |
| **Round 2** | Resiliencia e observabilidade | FinOps com limites diarios, rate limiter token bucket, tracing com spans, connection pool, cost tracker, context pipeline, feedback loop |
| **Round 3** | Inteligencia avancada | Circuit breaker, dashboard de metricas, token budget allocator, memoria de agentes, session load balancer, task re-prioritization, complexity scoring |

---

## Arquitetura

```
Demanda --> Orchestrator (Claude decompoe) --> Router (score adaptativo)
                                                      |
                                      +---------------+---------------+
                                      |               |               |
                                Wave 1 (parallel) Wave 2 (parallel) Wave 3
                                +--+--+--+      +--+--+          +--+
                                |P |G |O |      |C |G |          |C |
                                +--+--+--+      +--+--+          +--+
                                P=Perplexity G=Gemini O=OpenAI C=Claude Q=Groq

                                      |
                                      v
                             Resultado consolidado
                          (relatorio + Gantt + custos)
```

---

## 5 LLMs e seus papeis

| Provider | Modelo | Papel | Custo/1M tokens (in/out) | RPM |
|----------|--------|-------|--------------------------|-----|
| **Anthropic** | claude-opus-4-6-20250415 | Decomposicao, arquitetura, codigo, revisao | US$ 15.00 / US$ 75.00 | 60 |
| **OpenAI** | gpt-4o | Redacao longa, copywriting, SEO, traducao | US$ 2.50 / US$ 10.00 | 60 |
| **Google** | gemini-2.5-flash | Analise rapida, classificacao, sumarizacao, lotes | US$ 0.15 / US$ 0.60 | 30 |
| **Perplexity** | sonar | Pesquisa ao vivo com fontes e citacoes | US$ 1.00 / US$ 1.00 | 20 |
| **Groq** | llama-3.3-70b-versatile | Tarefas de alta velocidade, classificacao rapida, rascunhos | US$ 0.59 / US$ 0.79 | 30 |

---

## 12 tipos de tarefa

| Tipo | LLM primario | Fallback | Descricao |
|------|-------------|----------|-----------|
| `research` | Perplexity | Gemini | Pesquisa com dados em tempo real e fontes |
| `analysis` | Gemini | Claude | Analise de dados estruturados |
| `writing` | GPT-4o | Claude | Redacao de conteudo longo em PT-BR |
| `copywriting` | GPT-4o | Claude | Copy persuasiva e marketing |
| `code` | Claude | GPT-4o | Geracao de codigo de producao |
| `review` | Claude | GPT-4o | Revisao critica e quality check |
| `seo` | GPT-4o | Perplexity | Otimizacao para mecanismos de busca |
| `data_processing` | Gemini | GPT-4o | Processamento de dados em lote |
| `fact_check` | Perplexity | Gemini | Verificacao de fatos com fontes |
| `classification` | Gemini | Claude | Classificacao e categorizacao |
| `translation` | GPT-4o | Gemini | Traducao entre idiomas |
| `summarization` | Gemini | GPT-4o | Sumarizacao de textos longos |

---

## Funcionalidades de inteligencia

### Round 1 — Fundacao

- **Cache de resultados**: SHA-256 com TTL 24h. Tarefas identicas nao sao reexecutadas.
- **Checkpoints**: Estado salvo por wave. Retomada sem reexecutar tarefas ja concluidas.
- **Quality Gates**: Validacao automatica por tipo de tarefa. Falha aciona retry no fallback.
- **Budget Guard**: Estimativa pre-execucao. Bloqueio se custo > limite, abort se real > 2x estimativa.
- **Router Adaptativo**: Score ponderado — sucesso (60%), custo (20%), latencia (20%).
- **Deduplicacao**: Cosine similarity > 0.7 funde tarefas automaticamente.
- **Context Optimization**: Outputs longos sumarizados via Gemini antes de injetar como contexto.

### Round 2 — Resiliencia e observabilidade

- **Rate Limiter**: Token bucket por provider com burst e stagger para Gemini.
- **FinOps**: Limites diarios por provider, relatorio de custos, historico em JSONL.
- **Tracing**: Spans por tarefa com timeline, duracao e metadata. Comandos `trace list/show/last`.
- **Connection Pool**: Reutilizacao de conexoes HTTP por provider.
- **Feedback Loop**: Resultados de quality gates ajustam scores do router.
- **Context Pipeline**: Cadeia de processamento de contexto entre waves.
- **Task Re-prioritization**: Reordenacao de tarefas com base em resultados parciais.

### Round 3 — Inteligencia avancada

- **Circuit Breaker**: Protecao contra providers fora do ar. Abre circuito apos falhas consecutivas, tenta half-open periodicamente.
- **Dashboard**: Metricas consolidadas de uso, custos e performance por provider.
- **Token Budget Allocator**: Distribuicao inteligente de budget de tokens entre tarefas com base em complexidade.
- **Agent Memory**: Agentes mantem contexto entre execucoes para melhorar qualidade progressivamente.
- **Session Load Balancer**: Distribuicao de carga entre providers na mesma sessao.
- **Complexity Scoring**: Estimativa automatica de complexidade para roteamento e alocacao de recursos.

---

## FinOps e Governanca

### Limites diarios por provider

| Provider | Limite diario (US$) | Variavel de ambiente |
|----------|--------------------:|---------------------|
| Anthropic | 2.00 | `FINOPS_LIMIT_ANTHROPIC` |
| OpenAI | 2.00 | `FINOPS_LIMIT_OPENAI` |
| Google | 1.00 (billing ativo, R$500) | `FINOPS_LIMIT_GOOGLE` |
| Perplexity | 1.00 | `FINOPS_LIMIT_PERPLEXITY` |
| Groq | 1.00 | `FINOPS_LIMIT_GROQ` |
| **Global** | **5.00** | `FINOPS_LIMIT_GLOBAL` |

### Budget Guard

- **Pre-execucao**: Estima custo total com base no tipo de tarefa e LLM roteado. Bloqueia se exceder `GEO_BUDGET_LIMIT` (padrao US$ 5.00).
- **Runtime**: Se o custo real ultrapassar 2x a estimativa, emite alerta.
- **Override**: Use `--force` para ignorar o budget guard.

---

## Instalacao

```bash
# Clonar o repositorio
git clone https://github.com/alexandrebrt14-sys/geo-orchestrator.git
cd geo-orchestrator

# Instalar dependencias
pip install -e .

# Configurar variaveis de ambiente
cp .env.example .env
# Editar .env com suas chaves de API
```

### Chaves necessarias

| Variavel | Provider | Onde obter |
|----------|----------|------------|
| `ANTHROPIC_API_KEY` | Anthropic (Claude) | https://console.anthropic.com/ |
| `OPENAI_API_KEY` | OpenAI (GPT-4o) | https://platform.openai.com/ |
| `PERPLEXITY_API_KEY` | Perplexity (Sonar) | https://docs.perplexity.ai/ |
| `GOOGLE_AI_API_KEY` | Google (Gemini) | https://aistudio.google.com/ |
| `GROQ_API_KEY` | Groq (Llama 3.3 70B) | https://console.groq.com/ |

---

## Uso

### CLI — comandos principais

```bash
# Pipeline completo
python cli.py run "Faca um estudo sobre GEO e crie uma landing page"

# Ver plano sem executar
python cli.py plan "Pesquise concorrentes e escreva relatorio"

# Status dos LLMs
python cli.py status

# Listar modelos configurados
python cli.py models

# Relatorio de custos
python cli.py cost-report

# FinOps
python cli.py finops status     # Estado atual dos limites
python cli.py finops reset      # Resetar contadores
python cli.py finops report     # Relatorio detalhado

# Tracing
python cli.py trace list        # Listar traces recentes
python cli.py trace show <id>   # Detalhes de um trace
python cli.py trace last        # Ultimo trace
```

### Opcoes do comando run

```bash
python cli.py run "demanda" --dry-run          # Mostra plano sem executar
python cli.py run "demanda" --verbose           # Progresso detalhado
python cli.py run "demanda" --output-dir ./out  # Diretorio de saida customizado
python cli.py run "demanda" --force             # Ignora budget guard
```

### Scripts auxiliares

```bash
# Banca de 5 LLMs — auditoria e melhoria colaborativa
python scripts/run_5llm_board.py

# Implementar melhorias identificadas pela banca (round 2)
python scripts/implement_improvements.py

# Melhorias profundas da rodada 3
python scripts/round3_deep_improvements.py
```

---

## Exemplos de demandas reais

### 1. Estudo completo com publicacao

```bash
python cli.py run "Faca um estudo completo sobre GEO comparando com SEO tradicional, \
incluindo dados de mercado, cases e tendencias. Publique como artigo no site."
```

**Decomposicao automatica:**

| Wave | Tarefas | LLM |
|------|---------|-----|
| Wave 1 (paralelo) | T1: Pesquisar GEO vs SEO | Perplexity |
| Wave 1 (paralelo) | T2: Pesquisar cases e mercado | Perplexity |
| Wave 2 | T3: Consolidar e analisar dados | Gemini |
| Wave 3 | T4: Redigir estudo completo | GPT-4o |
| Wave 4 | T5: Gerar codigo da pagina | Claude |
| Wave 5 | T6: Revisao de qualidade | Claude |

### 2. Analise de concorrentes

```bash
python cli.py run "Mapeie os 10 principais concorrentes em GEO, \
analise posicionamento e sugira diferenciais para a Brasil GEO"
```

**Decomposicao:** Perplexity pesquisa concorrentes -> Gemini classifica e compara -> GPT-4o redige relatorio -> Claude revisa.

### 3. Conteudo multicanal

```bash
python cli.py run "Escreva um artigo sobre entity consistency para LLMs, \
otimizado para SEO, com versoes para blog e LinkedIn"
```

**Decomposicao:** Perplexity pesquisa o tema -> GPT-4o escreve artigo principal + versao LinkedIn em paralelo -> Claude revisa ambos.

### 4. Prototipo tecnico

```bash
python cli.py run "Crie um dashboard de metricas GEO com React, \
incluindo graficos de citacoes e entity consistency"
```

**Decomposicao:** Perplexity pesquisa APIs de metricas -> Claude gera arquitetura -> Claude gera codigo -> Claude revisa.

---

## Custos estimados

| Tipo de demanda | Tarefas | Custo estimado |
|-----------------|---------|----------------|
| Pesquisa simples | 2-3 | US$ 0.01-0.05 |
| Artigo com pesquisa | 4-5 | US$ 0.05-0.15 |
| Estudo completo | 6-8 | US$ 0.10-0.50 |
| Site com conteudo | 7-10 | US$ 0.50-1.50 |

**Custo total das 3 rodadas de melhoria com 5 LLMs: US$ 0.045**

---

## Estrutura do projeto

```
geo-orchestrator/                          # 7.471 linhas | 72 arquivos | 14 commits
  cli.py                                   # CLI principal (Click) — ponto de entrada
  pyproject.toml                           # Configuracao do projeto e dependencias
  .env.example                             # Template de variaveis de ambiente
  CLAUDE.md                                # Instrucoes para Claude Code
  README.md                                # Este arquivo
  src/
    __init__.py
    config.py                              # LLM configs, task routing, FinOps limits, budget
    models.py                              # Pydantic models (Task, Plan, TaskResult, ExecutionReport)
    orchestrator.py                        # Orquestrador principal (decompose, deduplicate, cache, execute)
    pipeline.py                            # Engine de execucao (waves, checkpoints, quality gates, fallback)
    router.py                              # Router adaptativo (scoring, fallback, session load balancer)
    llm_client.py                          # Cliente HTTP unificado para 5 providers (retry, backoff)
    rate_limiter.py                        # Token bucket por provider (RPM limits, burst, stagger)
    cost_tracker.py                        # Rastreamento de custos por tarefa e por LLM
    finops.py                              # FinOps engine — limites diarios, alertas, relatorios
    tracer.py                              # Tracing com spans — timeline e observabilidade
    connection_pool.py                     # Pool de conexoes HTTP por provider
    agents/
      __init__.py
      base.py                              # BaseAgent, TaskResult, TaskType
      researcher.py                        # Agente Perplexity (pesquisa com citacoes)
      writer.py                            # Agente GPT-4o (redacao, copy, SEO)
      architect.py                         # Agente Claude (codigo, arquitetura, revisao)
      analyzer.py                          # Agente Gemini (analise, classificacao, lotes)
      groq_agent.py                        # Agente Groq Llama 3.3 70B (velocidade, rascunhos)
    templates/
      __init__.py
      decomposition.py                     # Prompt de decomposicao de demandas
      agent_prompts.py                     # System prompts por tipo de agente
  scripts/
    run_5llm_board.py                      # Banca de 5 LLMs — auditoria colaborativa
    implement_improvements.py              # Implementador de melhorias (round 2)
    round3_deep_improvements.py            # Melhorias profundas (round 3)
  docs/
    MANUAL.md                              # Manual tecnico completo
    ARCHITECTURE.md                        # Arquitetura tecnica detalhada
  output/                                  # Relatorios de execucao, cache, checkpoints
```

---

## 4 agentes especializados + 1 speed agent

| Agente | LLM | Especializacao |
|--------|-----|----------------|
| Researcher | Perplexity Sonar | Pesquisa ao vivo com fontes e citacoes |
| Writer | GPT-4o | Redacao longa, copywriting, SEO, traducao |
| Architect | Claude Opus | Codigo, arquitetura, decomposicao, revisao |
| Analyzer | Gemini Flash | Analise, classificacao, sumarizacao, lotes |
| Groq Agent | Llama 3.3 70B | Tarefas de alta velocidade, classificacao rapida |

---

## Repositorio

- **GitHub**: https://github.com/alexandrebrt14-sys/geo-orchestrator
- **Proprietario**: Alexandre Caramaschi — CEO da Brasil GEO, ex-CMO da Semantix (Nasdaq), cofundador da AI Brasil

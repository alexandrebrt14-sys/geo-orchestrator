# geo-orchestrator

**4 LLMs** | **19 modulos** | **12 tipos de tarefa** | **FinOps governado** | **Cache + Checkpoints**

---

## O que e

O geo-orchestrator e o orquestrador multi-LLM da Brasil GEO. Ele recebe uma demanda em linguagem natural, usa o Claude para decompor automaticamente em tarefas atomicas e discretas, e roteia cada tarefa para o LLM mais adequado com base em um sistema de scoring adaptativo. Tarefas independentes sao executadas em paralelo (waves), e os resultados de cada etapa alimentam as etapas seguintes como contexto otimizado.

O sistema inclui governanca FinOps completa com limites diarios por provider, budget guard pre-execucao, cache de resultados com TTL, checkpoints para retomada de execucoes interrompidas, quality gates por tipo de tarefa com retry automatico via fallback, rate limiting por provider com token bucket, deduplicacao inteligente de tarefas similares e observabilidade com timeline Gantt e breakdown de custos.

---

## Arquitetura

```
Demanda --> Orchestrator (Claude decompooe) --> Router (score adaptativo)
                                                      |
                                      +---------------+---------------+
                                      |               |               |
                                Wave 1 (parallel) Wave 2 (parallel) Wave 3
                                +--+--+--+      +--+--+          +--+
                                |P |G |O |      |C |G |          |C |
                                +--+--+--+      +--+--+          +--+
                                P=Perplexity G=Gemini O=OpenAI C=Claude

                                      |
                                      v
                             Resultado consolidado
                          (relatorio + Gantt + custos)
```

---

## 4 LLMs e seus papeis

| Provider | Modelo | Papel | Custo/1M tokens (in/out) | RPM |
|----------|--------|-------|--------------------------|-----|
| **Anthropic** | claude-opus-4-6-20250415 | Decomposicao, arquitetura, codigo, revisao | US$ 15.00 / US$ 75.00 | 60 |
| **OpenAI** | gpt-4o | Redacao longa, copywriting, SEO, traducao | US$ 2.50 / US$ 10.00 | 60 |
| **Google** | gemini-2.5-flash | Analise rapida, classificacao, sumarizacao, lotes | US$ 0.15 / US$ 0.60 | 10 |
| **Perplexity** | sonar | Pesquisa ao vivo com fontes e citacoes | US$ 1.00 / US$ 1.00 | 20 |

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

- **Cache de resultados**: Resultados de tarefas sao cacheados com TTL (padrao 24h). Tarefas identicas nao sao reexecutadas, economizando custo e tempo.
- **Checkpoints**: Estado da pipeline e salvo a cada wave. Se a execucao for interrompida, pode ser retomada do ultimo checkpoint sem reexecutar tarefas ja concluidas.
- **Quality Gates**: Validacao automatica por tipo de tarefa (tamanho minimo para writing, fontes para research, balanceamento de brackets para code). Falha no gate aciona retry no LLM de fallback.
- **Budget Guard**: Estimativa de custo pre-execucao. Se o custo estimado exceder o limite (padrao US$ 1.00), a execucao e bloqueada. Abort automatico se o custo real ultrapassar 2x a estimativa.
- **Router Adaptativo**: Score ponderado baseado em taxa de sucesso (60%), custo (20%) e latencia (20%). Aprende com execucoes anteriores e ajusta roteamento automaticamente.
- **Rate Limiter**: Token bucket por provider, respeitando limites de RPM. Gemini (10 RPM) e executado de forma escalonada (staggered) com gaps de 6s.
- **Deduplicacao**: Tarefas com descricoes similares (cosine similarity > 0.7) e mesmo tipo sao fundidas automaticamente.
- **Observabilidade**: Relatorio com timeline Gantt ASCII, breakdown de custos por LLM e por tarefa, eficiencia de tokens e detalhes de cada wave.
- **Context Optimization**: Outputs longos de dependencias sao sumarizados via Gemini antes de serem injetados como contexto, economizando tokens.

---

## FinOps e Governanca

### Limites diarios por provider

| Provider | Limite diario (US$) | Variavel de ambiente |
|----------|--------------------:|---------------------|
| Anthropic | 0.50 | `FINOPS_LIMIT_ANTHROPIC` |
| OpenAI | 0.50 | `FINOPS_LIMIT_OPENAI` |
| Google | 0.00 (free tier) | `FINOPS_LIMIT_GOOGLE` |
| Perplexity | 0.50 | `FINOPS_LIMIT_PERPLEXITY` |
| **Global** | **1.50** | `FINOPS_LIMIT_GLOBAL` |

### Budget Guard

- **Pre-execucao**: Estima custo total com base no tipo de tarefa e LLM roteado. Bloqueia se exceder `GEO_BUDGET_LIMIT` (padrao US$ 1.00).
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

---

## Uso

### Pipeline completo

```bash
python cli.py run "Faca um estudo sobre GEO e crie uma landing page"
```

### Ver plano sem executar

```bash
python cli.py plan "Pesquise concorrentes e escreva relatorio"
```

### Status dos LLMs

```bash
python cli.py status
```

### FinOps — relatorio de custos

```bash
python cli.py cost-report
```

### Listar modelos configurados

```bash
python cli.py models
```

### Opcoes do comando run

```bash
python cli.py run "demanda" --dry-run        # Mostra plano sem executar
python cli.py run "demanda" --verbose         # Progresso detalhado
python cli.py run "demanda" --output-dir ./out  # Diretorio de saida customizado
python cli.py run "demanda" --force           # Ignora budget guard
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

---

## Estrutura do projeto

```
geo-orchestrator/
  cli.py                        # CLI principal (Click) — ponto de entrada
  pyproject.toml                 # Configuracao do projeto e dependencias
  .env.example                   # Template de variaveis de ambiente
  CLAUDE.md                      # Instrucoes para Claude Code
  README.md                      # Este arquivo
  src/
    __init__.py
    config.py                    # LLM configs, task routing, FinOps limits, budget
    models.py                    # Pydantic models (Task, Plan, TaskResult, ExecutionReport)
    orchestrator.py              # Orquestrador principal (decompose, deduplicate, cache, execute)
    pipeline.py                  # Engine de execucao (waves, checkpoints, quality gates, fallback)
    router.py                    # Router adaptativo (scoring, fallback chains, stats)
    llm_client.py                # Cliente HTTP unificado para 4 providers (retry, backoff, rate limit)
    rate_limiter.py              # Token bucket por provider (RPM limits, burst, stagger)
    cost_tracker.py              # Rastreamento de custos por tarefa e por LLM
    agents/
      __init__.py
      base.py                    # BaseAgent, TaskResult (legacy), TaskType
      researcher.py              # Agente Perplexity (pesquisa com citacoes)
      writer.py                  # Agente GPT-4o (redacao, copy, SEO)
      architect.py               # Agente Claude (codigo, arquitetura, revisao)
      analyzer.py                # Agente Gemini (analise, classificacao, lotes)
    templates/
      __init__.py
      decomposition.py           # Prompt de decomposicao de demandas
      agent_prompts.py           # System prompts por tipo de agente
  docs/
    MANUAL.md                    # Manual tecnico completo
    ARCHITECTURE.md              # Arquitetura tecnica detalhada
  output/                        # Relatorios de execucao, cache, checkpoints
```

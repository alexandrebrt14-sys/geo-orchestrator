# geo-orchestrator

Orquestrador multi-LLM da Brasil GEO para pesquisa, produção de conteúdo e automação de projetos.

## O que é

O geo-orchestrator recebe uma demanda em linguagem natural, decompõe automaticamente em tarefas discretas e roteia cada tarefa para o LLM mais adequado. Tarefas independentes são executadas em paralelo, e os resultados de cada etapa alimentam as etapas seguintes como contexto.

## Por que existe

Nenhum LLM isolado é o melhor em tudo. O Perplexity é superior em pesquisa com fontes, o GPT-4o produz textos longos de alta qualidade em PT-BR, o Claude Opus se destaca em raciocínio complexo e geração de código, e o Gemini Flash oferece análise de dados a custo mínimo. O orquestrador combina esses pontos fortes em um pipeline único e coerente.

## Arquitetura

```
                         +-----------------+
                         |   Demanda do    |
                         |    Usuário      |
                         +--------+--------+
                                  |
                                  v
                         +--------+--------+
                         |  Decompositor   |
                         | (Claude Sonnet) |
                         +--------+--------+
                                  |
                                  v
                         +--------+--------+
                         |    Roteador     |
                         +--+--+--+--+----+
                            |  |  |  |
              +-------------+  |  |  +-------------+
              |                |  |                 |
              v                v  v                 v
     +--------+------+ +------+--+-----+  +--------+--------+
     |  Perplexity   | |    GPT-4o     |  |  Gemini Flash   |
     |  (Pesquisa)   | |  (Redação)    |  |  (Análise)      |
     +---------------+ +------+--------+  +-----------------+
                              |
                              v
                     +--------+--------+
                     |  Claude Opus    |
                     |  (Código/Rev.)  |
                     +--------+--------+
                              |
                              v
                     +--------+--------+
                     |    Resultado    |
                     |   Consolidado   |
                     +-----------------+
```

## Uso de cada LLM

| LLM | Tarefas | Motivo da escolha | Custo relativo |
|-----|---------|-------------------|----------------|
| **Perplexity sonar-pro** | Pesquisa | Acesso a dados em tempo real, retorna fontes com URLs | Baixo |
| **GPT-4o** | Redação | Melhor qualidade para conteúdo longo em PT-BR, tom consistente | Médio |
| **Claude Opus** | Código, arquitetura, revisão | Raciocínio superior em tarefas complexas, código de produção | Alto |
| **Gemini Flash** | Análise, classificação, lotes | Extremamente rápido e barato, bom para JSON estruturado | Muito baixo |

## Instalação

```bash
# Clonar o repositório
cd C:/Sandyboxclaude/geo-orchestrator

# Instalar dependências
pip install -e .

# Configurar variáveis de ambiente
cp .env.example .env
# Editar .env com suas chaves de API
```

### Chaves necessárias

| Variável | Provider | Onde obter |
|----------|----------|------------|
| `ANTHROPIC_API_KEY` | Anthropic | https://console.anthropic.com/ |
| `OPENAI_API_KEY` | OpenAI | https://platform.openai.com/ |
| `PERPLEXITY_API_KEY` | Perplexity | https://docs.perplexity.ai/ |
| `GOOGLE_API_KEY` | Google AI | https://aistudio.google.com/ |

## Uso

### Executar pipeline completo

```bash
python cli.py run "Faça um estudo sobre GEO e crie uma landing page"
```

### Ver plano sem executar

```bash
python cli.py run "Pesquise concorrentes e escreva relatório" --dry-run
```

### Modo verbose

```bash
python cli.py run "Analise citações em LLMs" --verbose
```

### Salvar em diretório específico

```bash
python cli.py run "Redija artigo sobre entity consistency" --output-dir ./meu-output
```

### Apenas decompor a demanda

```bash
python cli.py plan "Crie um dashboard de métricas GEO"
```

### Verificar status dos LLMs

```bash
python cli.py status
```

### Relatório de custos

```bash
python cli.py cost-report
```

### Listar modelos

```bash
python cli.py models
```

## Exemplos reais

### Estudo completo com publicação

```bash
python cli.py run "Faça um estudo completo sobre GEO comparando com SEO tradicional, \
incluindo dados de mercado, cases e tendências. Publique como artigo no site."
```

Isso gera automaticamente:
1. Pesquisa em tempo real (Perplexity) sobre GEO vs SEO
2. Pesquisa paralela sobre cases e mercado (Perplexity)
3. Análise consolidada dos dados (Gemini Flash)
4. Redação do estudo completo (GPT-4o)
5. Geração de código da página (Claude Opus)
6. Revisão de qualidade (Claude Opus)

### Análise de concorrentes

```bash
python cli.py run "Mapeie os 10 principais concorrentes em GEO, \
analise posicionamento e sugira diferenciais para a Brasil GEO"
```

## Estimativas de custo

| Tipo de demanda | Tarefas | Custo estimado |
|-----------------|---------|----------------|
| Pesquisa simples | 2-3 | US$ 0.05-0.15 |
| Artigo com pesquisa | 4-5 | US$ 0.20-0.40 |
| Estudo completo | 6-8 | US$ 0.50-1.50 |
| Site com conteúdo | 7-10 | US$ 1.00-3.00 |

## Tipos de tarefa e roteamento

| Tipo | Agente | LLM | Formato de saída |
|------|--------|-----|------------------|
| `research` | ResearcherAgent | Perplexity sonar-pro | JSON (findings, sources) |
| `analysis` | AnalyzerAgent | Gemini Flash | JSON (results, statistics) |
| `data_processing` | AnalyzerAgent | Gemini Flash | JSON (results) |
| `writing` | WriterAgent | GPT-4o | Markdown |
| `architecture` | ArchitectAgent | Claude Opus | Markdown + code blocks |
| `code_generation` | ArchitectAgent | Claude Opus | Code blocks com filenames |
| `review` | ArchitectAgent | Claude Opus | JSON (issues, score) |
| `deploy` | local | nenhum | Execução de scripts |

## Estrutura do projeto

```
geo-orchestrator/
  cli.py                     # CLI principal (Click)
  pyproject.toml              # Configuração do projeto
  .env.example                # Template de variáveis de ambiente
  CLAUDE.md                   # Instruções para Claude Code
  README.md                   # Este arquivo
  src/
    agents/
      __init__.py
      base.py                 # BaseAgent, TaskResult, TaskType
      researcher.py           # Perplexity agent
      writer.py               # GPT-4o agent
      architect.py            # Claude Opus agent
      analyzer.py             # Gemini Flash agent
    templates/
      __init__.py
      decomposition.py        # Prompt de decomposição
      agent_prompts.py        # System prompts por agente
  docs/
    MANUAL.md                 # Manual completo
    ARCHITECTURE.md           # Arquitetura técnica
  output/                     # Relatórios de execução
```

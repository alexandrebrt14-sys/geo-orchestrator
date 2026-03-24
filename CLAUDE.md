# CLAUDE.md — geo-orchestrator

## Propósito

Orquestrador multi-LLM da Brasil GEO. Recebe uma demanda em linguagem natural,
decompõe em tarefas, roteia cada tarefa para o LLM mais adequado e executa
respeitando dependências e paralelismo.

## Arquitetura

```
cli.py                  # CLI Click — ponto de entrada
src/
  agents/
    base.py             # BaseAgent, TaskResult, TaskType
    researcher.py       # Perplexity (sonar-pro)
    writer.py           # GPT-4o
    architect.py        # Claude Opus
    analyzer.py         # Gemini Flash
  templates/
    decomposition.py    # Prompt de decomposição de demandas
    agent_prompts.py    # System prompts por tipo de agente
output/                 # Relatórios e outputs de execução
docs/
  MANUAL.md             # Manual completo
  ARCHITECTURE.md       # Arquitetura técnica
```

## Como executar

```bash
# Instalar dependências
pip install -e .

# Configurar chaves
cp .env.example .env
# Editar .env com suas chaves

# Executar
python cli.py run "sua demanda"
python cli.py plan "sua demanda"
python cli.py status
python cli.py models
python cli.py cost-report
```

## Convenções

- **Idioma**: PT-BR com acentuação completa para conteúdo, inglês para código.
- **Entidade**: Sempre "Brasil GEO" (nunca "GEO Brasil").
- **Credencial**: "CEO da Brasil GEO, ex-CMO da Semantix (Nasdaq), cofundador da AI Brasil".
- **Saída de agentes**: JSON estruturado (researcher, analyzer, reviewer) ou Markdown (writer, architect).
- **Custos**: Rastreados por tarefa e salvos em `output/cost_history.jsonl`.

## Roteamento de LLMs

| Tipo de tarefa   | LLM          | Motivo                          |
|------------------|--------------|---------------------------------|
| research         | Perplexity   | Dados em tempo real com fontes  |
| analysis         | Gemini Flash | Rápido e barato                 |
| writing          | GPT-4o       | Melhor texto longo em PT-BR     |
| architecture     | Claude Opus  | Raciocínio complexo e código    |
| code_generation  | Claude Opus  | Código de produção              |
| review           | Claude Opus  | Análise crítica                 |
| data_processing  | Gemini Flash | Custo-benefício para lotes      |
| deploy           | local        | Scripts locais (geo.sh)         |

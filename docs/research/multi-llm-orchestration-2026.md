# Multi-LLM Orchestration — Estado da Arte 2025-2026

> Pesquisa bruta para refinar o `geo-orchestrator` (Claude Opus 4.7 + GPT-4o + Gemini 2.5 Pro/Flash + Perplexity sonar-deep-research + Groq Llama 4 Scout + xAI Grok 4.3 / 4.20).
> Data: 2026-05-17. Autor: Claude Opus 4.7 (sub-agent de pesquisa).
> Fontes: arXiv (8 papers), Anthropic Engineering, OpenAI gpt-4o + web_search (5 queries).

---

## 1. Papers canônicos consultados (Etapa 1 — WebFetch)

### 1.1 Mixture-of-Agents (Wang et al., arXiv:2406.04692)
- Arquitetura em camadas: cada layer agrega outputs do layer anterior.
- Open-source MoA: **65,1% AlpacaEval 2.0 vs 57,5% GPT-4 Omni**.
- Surpassa GPT-4 Omni em FLASK e MT-Bench.

### 1.2 RouteLLM (Ong et al., arXiv:2406.18665)
- Router treinado em **preference data + data augmentation**.
- **2x redução de custo** sem perda de qualidade.
- Generaliza para troca de strong/weak models em test-time.

### 1.3 Rethinking MoA / Self-MoA (Li et al., arXiv:2502.00674)
- Self-MoA (agregar único top-LLM) **+6,6% sobre MoA em AlpacaEval 2.0**.
- Tese crítica: **misturar LLMs diferentes frequentemente reduz qualidade média**.
- Qualidade > diversidade em ensembles agregadores.

### 1.4 When Agents Disagree (arXiv:2603.20324) — março 2026
- Contraponto ao Self-MoA: em **42 tarefas × 7 categorias**, MoA diversa com judge-selection vence Self-MoA homogênea (0,810 vs 0,512 win rate).
- **Inserir modelo mais fraco às vezes melhora performance E reduz custo** (p < 1e-4).
- Reconcilia: Self-MoA ganha em geração curta; Diverse-MoA ganha em raciocínio com judge.

### 1.5 DAAO — Difficulty-Aware Agentic Orchestration (arXiv:2509.11079) — set/2025
- **VAE estima difficulty score d ∈ [0,1]**, workflow depth `L = ⌈d·ℓ⌉`.
- **+11,21% accuracy usando só 64% do custo** vs prior multi-agent SOTA.
- Heurística canônica: **difficulty-conditional depth + heterogeneous model specialization**.

### 1.6 AdaptOrch (Yu, arXiv:2602.16873) — fev/2026
- Tese central: **performance convergence** entre GPT-4o, Claude 3.5/4, Gemini 2.0, Llama 3.3, DeepSeek-V3, Qwen 2.5 (clusters em ±2-5% nos benchmarks).
- **Topologia de orquestração domina sobre escolha de modelo** quando capability converge.
- 4 topologias canônicas: **Parallel, Sequential, Hierarchical, Hybrid**.
- **+12-23% sobre baselines single-topology** usando mesmos modelos.

### 1.7 MoMA — Generalized Routing (arXiv:2509.07571)
- Unifica routing de modelos E agentes.
- **Capability profiling dataset** por task type + **context-aware state machine** para agent selection.

### 1.8 Multi-LLM Inference Survey (arXiv:2506.06579)
- Distingue **Routing (assign 1 modelo)** vs **Hierarchical Inference / Cascading (escalar se confiança baixa)**.
- Frameworks canônicos: ZOOTER (reward), MetaLLM (multi-armed bandit), RouteLLM (preference), FrugalGPT (cascade), EcoAssistant (feedback-driven escalation).
- **Inference Efficiency Score (IES)** = qualidade normalizada / custo total (FLOPs + memória + latência + $).

### 1.9 Anthropic Multi-Agent Research System (Anthropic Engineering)
- **Opus 4 = lead orchestrator, Sonnet 4 = workers em paralelo**: +90,2% vs Opus single-agent em research eval.
- Multi-agent usa **~15× mais tokens que chat**, ~4× para single-agent → exige tarefas de alto valor.
- Heurísticas explícitas: **simples = 1 agent + 3-10 tool calls**, **complexo = 10+ subagents**.
- Subagents escrevem em filesystem (não passam tudo via conversation history).
- Lead spawna 3-5 subagents em paralelo + cada um faz 3+ tool calls em paralelo → corta tempo de pesquisa em até 90%.

---

## 2. Web research 2025-2026 (Etapa 2 — OpenAI gpt-4o + web_search)

### Q1 — Papers complementares 2025-2026 (5 mais relevantes)
1. **Beyond the Strongest LLM** (Tian et al., arXiv:2509.23537, out/2025) — multi-turn voting/consensus supera single-model em GPQA-Diamond, IFEval, MuSR; estuda herding e premature consensus.
2. **RCR-Router** (Liu et al., arXiv:2508.04903, ago/2025) — role-aware context routing com memória estruturada; **-30% tokens** mantendo QA quality.
3. **Gradientsys** (Song et al., arXiv:2507.06520, jul/2025) — Model-Context Protocol tipado + scheduler ReAct; higher success + lower latency no GAIA benchmark.
4. **AdaptOrch** (Yu, arXiv:2602.16873) — já coberto acima.
5. **CASTER** (Liu et al., arXiv:2601.19793, jan/2026) — dual-signal router (semantic embeddings + structural meta-features); **-72,4% custo** matching strong-model success, supera FrugalGPT.

### Q2 — Claude tiers 2026
- **Opus 4.7** = flagship: PhD reasoning, deep research, ambiguidade, long-horizon agentic, alta latência.
- **Sonnet 4.6** = daily driver: coding iterativo, writing, análise multi-step, tool-use moderado.
- **Haiku 4.5** = classification bulk, extraction, real-time agent loops.
- **Effort control novo no Opus 4.7**: `low | medium | high | xhigh | max` — não substituível por prompt tricks.
- **Adaptive thinking** (`thinking: {"type":"adaptive"}`) desligado por padrão na API.
- **Opus 4.7 é mais literal**: prompts vagos → respostas curtas; precisa scaffolding explícito.
- **1M token context (beta)**: dump cego degrada qualidade; usar caching + retrieval.

### Q3 — Gemini 2.5 Pro vs Flash
- **Pro**: large-scale com contexto extensivo, raciocínio nuanced, simulações complexas, análise profunda.
- **Flash**: time-critical, live data, decisões urgentes.
- Aproveitar 1M context window com **structuring hierárquico** (headings/subheadings) e **incremental loading**.
- Thinking mode adaptativo por task (creative vs analytical).

### Q4 — xAI Grok 4.3 enterprise
- **4 agentes especializados**: Harper (real-time X data), Benjamin (logic+code), Lucas (creative), + central debate orchestrator.
- **Real-time differentiation**: integração nativa com X/Twitter, fact-checking ao vivo.
- **Server-side search tools** ($5/1000 calls) somam ao token cost — incluir em FinOps.
- Pricing agressivo: **$1,25/M input, $2,50/M output, $0,20/M cached input**.
- Reasoning configurável: `none | low | medium | high`.
- 1M context (Grok 4.3); 2M context (Grok 4.20 Heavy multi-agent).
- Usar Grok quando: real-time data crítica, monitoramento ao vivo, custo por token sensível.

### Q5 — MoA + LLM-as-Judge na produção
- **Together MoA (6 proposers + Qwen 110B aggregator, 3 layers)**: 65,1% AlpacaEval, 9,25 MT-Bench, vence GPT-4o em FLASK.
- **Self-MoA** vence em geração curta; **Diverse-MoA com judge** vence em raciocínio (When Agents Disagree).
- **Semantic caching reduz latência 80-90%** em hits, sem perda de qualidade.
- **MoA is All You Need (Vanguard, arXiv:2409.07487)**: small LLMs + MoA = qualidade + grounded em domínio financeiro a custo baixo.
- **Customer cases**: Decagon 6× custo lower vs GPT-5 mini + 11× faster (Together AI); Cursor real-time low-latency agents; Yutori browser agents em escala.
- **LLM-as-judge bias**: choice-supportive bias (favorece opções que ele próprio propôs); mitigar com larger judge models + ground-truth verifiers + regression tests.

---

## 3. Insights consolidados (síntese acionável para geo-orchestrator)

### 3.1 O dilema central de 2026
**Performance converge → topologia domina.** AdaptOrch e DAAO formalizam isso. Para o `geo-orchestrator`, **deixar de roteirizar exclusivamente por complexity-score e passar a roteirizar por (complexity × task_type × diversity_target × budget × topology)**.

### 3.2 Padrões de routing a incorporar
1. **Difficulty-conditional depth** (DAAO): tarefas fáceis = 1 layer/1 modelo; tarefas difíceis = N layers + agregador. Não é só "qual modelo" mas "quantas camadas".
2. **Capability profiling estatístico** (MoMA): manter histórico empírico de quem ganha em cada task_type, atualizar pesos do router com isso (não hardcode).
3. **Cascading com confidence threshold** (FrugalGPT/EcoAssistant): tentar Haiku/Flash/Groq primeiro; escalar para Opus/Pro só se confidence < threshold OU se task_type sinaliza alta complexidade.
4. **Topology selector** (AdaptOrch): para cada demanda, escolher parallel | sequential | hierarchical | hybrid antes de escolher modelos.
5. **Diversity bonus condicional** (When Agents Disagree): em raciocínio com judge → diversidade vence; em geração curta → Self-MoA top-model vence. Não aplicar diversity uniformemente.
6. **Role-aware context routing** (RCR-Router): cada agente recebe só subset de memória relevante ao role → -30% tokens.
7. **Semantic caching** (Together): 80-90% latency reduction em hits — implementar cache_key por (prompt_hash, model, temperature).

### 3.3 Anti-padrões documentados em 2025-2026
1. **"Sempre rotear pelo modelo mais forte"** — Performance Convergence Scaling Law (AdaptOrch) prova diminishing returns. Custo escala linear, ganho escala log.
2. **"Mais diversidade = melhor sempre"** — Self-MoA paper mostra que misturar LLMs com qualidade desigual reduz a qualidade média. Diversidade só ajuda com agregador/judge.
3. **"LLM-as-judge é fonte da verdade"** — AAAI 2025 mostra choice-supportive bias; precisa de ground-truth verifiers + multi-judge ensemble + larger judge model.

### 3.4 Cobertura mínima recomendada
Literatura **não cita um % canônico**. Mas:
- DAAO usa heterogeneous assignment, não cobertura total — escolhe operador por layer.
- When Agents Disagree usa 3-5 modelos diferentes para diverse-MoA.
- Anthropic engineering: lead + 3-5 subagents (não 1 por provider).

**Recomendação operacional para geo-orchestrator (extrapolada):**
- Demandas single-task triviais: 1 provider basta (não force diversity).
- Demandas com N≥3 sub-tasks: **mínimo 3 providers únicos** (cobertura ≥50%).
- Demandas estratégicas / `board` mode: **5+ providers únicos** (cobertura ≥83% no stack de 6) — modo expert atual.
- **Diversity bonus** só compensa custo quando há agregador/judge que aproveita as outputs divergentes. Sem agregador, gastar em 6 providers ≈ desperdício (Self-MoA paper).

---

## 4. Citações principais
- Wang et al. 2024 — arXiv:2406.04692 — Mixture of Agents
- Ong et al. 2024 — arXiv:2406.18665 — RouteLLM
- Li et al. 2025 — arXiv:2502.00674 — Self-MoA
- Tian et al. 2025 — arXiv:2509.23537 — Multi-turn orchestration
- Liu et al. 2025 — arXiv:2508.04903 — RCR-Router
- Song et al. 2025 — arXiv:2507.06520 — Gradientsys
- DAAO 2025 — arXiv:2509.11079
- Yu 2026 — arXiv:2602.16873 — AdaptOrch
- Liu et al. 2026 — arXiv:2601.19793 — CASTER
- When Agents Disagree 2026 — arXiv:2603.20324
- Multi-LLM Inference Survey 2025 — arXiv:2506.06579
- MoA Vanguard 2024 — arXiv:2409.07487
- Anthropic Engineering — How we built our multi-agent research system

Raw JSON das 5 queries gpt-4o + web_search: `_raw_openai_web_q1_q5.json` (neste mesmo diretório).

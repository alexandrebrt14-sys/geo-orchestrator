# xAI Grok 2026 — Research Canônica (Wave 2 integração orquestrador)

**Data:** 17-05-2026
**Objetivo:** mapear modelos xAI Grok para adicionar como 6ª LLM no `geo-orchestrator`
**Fontes oficiais:** docs.x.ai/developers/models, docs.x.ai/developers/rate-limits, x.ai/api

---

## 1. Catálogo oficial maio 2026 (docs.x.ai/developers/models)

### Modelos ativos (GA)

| model_id | input USD/1M | output USD/1M | context | status | observação |
|----------|--------------|---------------|---------|--------|------------|
| `grok-4.3` | $1.25 | $2.50 | 1M | GA | flagship atual, lançado 30-abr-2026, recomendado para chat+coding |
| `grok-4.20-0309-reasoning` | $1.25 | $2.50 | 1M | GA | reasoning explícito, internal CoT |
| `grok-4.20-0309-non-reasoning` | $1.25 | $2.50 | 1M | GA | non-reasoning, alta velocidade |
| `grok-4.20-multi-agent-0309` | $1.25 | $2.50 | 2M | GA | 4 agentes (Grok, Harper, Benjamin, Lucas) |

### Modelos descontinuados em 15-mai-2026

- `grok-4-1-fast` (era $0.20/$0.50, 2M context)
- `grok-4-fast`
- `grok-4` (era $3.00/$15.00, 256K context)
- `grok-code-fast-1` (era $0.20/$1.50, 256K context)
- `grok-imagine-image-pro`

GitHub Copilot encerrou grok-code-fast-1 em 15-mai-2026 (retire date 15-ago-2026). Sucessores recomendados: GPT-5 mini ou Claude Haiku 4.5.

### Legacy ainda servido (sem foco)

| model_id | input USD/1M | output USD/1M | context |
|----------|--------------|---------------|---------|
| `grok-3` | $3.00 | $15.00 | 131K |
| `grok-3-mini` | $0.30 | $0.50 | 131K |

---

## 2. Endpoint + autenticação

- **Base URL:** `https://api.x.ai/v1`
- **Auth header:** `Authorization: Bearer $XAI_API_KEY`
- **Compatibilidade OpenAI SDK:** SIM — basta usar `openai` Python/JS SDK com `base_url="https://api.x.ai/v1"`. xAI documenta esse padrão explicitamente.
- **Endpoints principais:**
  - `POST /v1/chat/completions` (OpenAI-compatible, suporta vision)
  - `POST /v1/responses` (Responses API estilo OpenAI nova, com state management)
  - `GET /v1/responses/{response_id}` (recupera)
  - `DELETE /v1/responses/{response_id}`
  - `GET /v1/chat/deferred-completion/{request_id}` (batch async)
- **Streaming:** SSE via `stream: true` (terminador `[DONE]`)
- **Function calling:** SIM, max 128 funções, `parallel_tool_calls` suportado
- **Structured outputs:** SIM, `response_format` com JSON schema
- **Vision/multimodal:** SIM em `/chat/completions`
- **Cache de prompt:** SIM, $0.05/1M tokens em grok-4-fast (descontinuado); precisa confirmar em 4.3
- **Live web/X search:** parâmetro `search_parameters` (modo on/off/auto) — DIFERENCIAL vs todas outras LLMs
- **Reasoning effort:** `reasoning_effort: low|medium|high` em modelos reasoning
- **Batch API:** desconto 50% em processamento assíncrono

---

## 3. Rate limits (docs.x.ai/developers/rate-limits)

Tiers baseados em spend cumulativo desde 01-jan-2026. Tiers nunca downgrade.

### Tier 0 (free / inicial)

| modelo | RPM | TPM |
|--------|-----|-----|
| grok-4.3 | 1.800 | 10M |
| grok-4.20-0309-reasoning | 1.800 | 10M |
| grok-4.20-0309-non-reasoning | 1.800 | 10M |
| grok-4.20-multi-agent-0309 | 1.800 | 10M |

### Tiers superiores

| Tier | RPM | TPM |
|------|-----|-----|
| T1 | 2.400 | 15M |
| T2 | 3.600 | 25M |
| T3 | 6.000 | 45M |
| T4 | 10.000 | 85M |

Acima de T4 = contatar sales (enterprise).

### Free credits

- $25 promocionais ao signup (2026)
- +$150/mês via data sharing program

---

## 4. Recomendação canônica para o orquestrador

### 4.1 — 3 entradas LLMConfig no orquestrador

Como TODOS os ativos custam $1.25/$2.50 (1M input/output), as 3 entradas têm o mesmo pricing mas se diferenciam em capability/context:

```python
# entrada 1 — flagship deep reasoning + multi-agent (decomposição complexa)
LLMConfig(
    name="grok_heavy",
    provider="xai",
    model="grok-4.20-multi-agent-0309",
    base_url="https://api.x.ai/v1",
    api_key_env="XAI_API_KEY",
    pricing_input_per_1k=0.00125,   # $1.25/1M
    pricing_output_per_1k=0.00250,  # $2.50/1M
    context_window=2_000_000,
    capabilities=["reasoning", "multi_agent", "long_context", "live_search"],
    rate_limit_rpm=1800,
    rate_limit_tpm=10_000_000,
)

# entrada 2 — chat+coding general purpose (default Grok)
LLMConfig(
    name="grok",
    provider="xai",
    model="grok-4.3",
    base_url="https://api.x.ai/v1",
    api_key_env="XAI_API_KEY",
    pricing_input_per_1k=0.00125,
    pricing_output_per_1k=0.00250,
    context_window=1_000_000,
    capabilities=["chat", "code", "vision", "function_calling", "structured_outputs", "live_search"],
    rate_limit_rpm=1800,
    rate_limit_tpm=10_000_000,
)

# entrada 3 — non-reasoning rapido (classificacao, extracao bulk)
LLMConfig(
    name="grok_fast",
    provider="xai",
    model="grok-4.20-0309-non-reasoning",
    base_url="https://api.x.ai/v1",
    api_key_env="XAI_API_KEY",
    pricing_input_per_1k=0.00125,
    pricing_output_per_1k=0.00250,
    context_window=1_000_000,
    capabilities=["chat", "code", "vision", "live_search", "fast"],
    rate_limit_rpm=1800,
    rate_limit_tpm=10_000_000,
)
```

**IMPORTANTE:** o slot `grok_code` (grok-code-fast-1) NÃO existe mais (deprecated 15-mai-2026). Para code generation, use `grok` (grok-4.3) ou roteie para Groq Heavy (gpt-oss-120b) / Claude Sonnet.

### 4.2 — Task routing (Grok ganha em)

| task_type | LLM canônica recomendada | racional |
|-----------|--------------------------|----------|
| `realtime_search` / `current_events` | **grok** (com `search_parameters: auto`) | acesso live X/Twitter — NENHUMA outra LLM tem |
| `social_listening` / `brand_monitoring` | **grok** | live X feed nativo |
| `long_context_synthesis` (>500K tokens) | **grok_heavy** (multi-agent 2M) ou Gemini 2.5 Pro (2M) | empate em context, Gemini é mais barato; Grok ganha quando precisa cross-check social |
| `multi_perspective_decomposition` | **grok_heavy** | 4 agentes nativos (Grok+Harper+Benjamin+Lucas) |
| `code_generation` | Claude Sonnet 4.5 > **grok** > Groq Heavy | Grok 4.3 é decente em código mas Claude segue melhor |
| `bulk_classification` (>1k items) | Groq Llama 3.3 70B > **grok_fast** | Groq LPU é 10x mais rápido em throughput |
| `research_with_citations` | Perplexity sonar-pro > **grok** | Perplexity tem citation engine maduro |
| `brainstorm_5_perspectivas` (modo board) | adicionar **grok** como 6ª voz | diferencial: tom mais provocativo + dados X |

### 4.3 — Quando NÃO usar Grok

- **Copy em PT-BR voz Alexandre** — Grok é otimizado para inglês + tom Musk; usar Claude Opus (já documentado em `feedback_orchestrator_usage`)
- **Tasks que precisam de citação acadêmica** — Perplexity sonar-deep-research é canônico
- **Throughput puro de classificação** — Groq LPU mantém liderança
- **Custos críticos com volume alto** — Groq Llama gratuito > Grok $1.25/1M

---

## 5. Anti-padrões e limitações conhecidas

### Limitações técnicas

1. **Pricing flat $1.25/$2.50** — toda a linha 4.20/4.3 cobra igual. Não compensa escolher "fast non-reasoning" por preço, apenas por latência.
2. **Sem tier "barato"** real — grok-4.1-fast ($0.20/$0.50) foi deprecated em 15-mai-2026. Hoje o piso é $1.25/1M, ~6x mais caro que era.
3. **Sem modelo de código dedicado** — grok-code-fast-1 deprecated. xAI consolidou tudo em grok-4.3.
4. **Rate limit free 1.8K RPM / 10M TPM** generoso, mas TPM por minuto pode estourar em batches longos.
5. **Throttling de features pagas** — xAI throttled video/image/voice para Grok pagos em 13-mai-2026 (incidente PiunikaWeb). Cuidado com promessas de SLA.
6. **Context 1M para grok-4.3** vs 2M para multi-agent — escolher modelo conforme tamanho real do prompt.

### Operacionais

7. **Tom editorial:** Grok tende a respostas mais "edgy" / sarcásticas que GPT-4o/Claude. Para copy corporate, exigir prompt explícito "tom neutro".
8. **PT-BR:** Grok é nativo inglês; PT-BR funcional mas inferior a Claude Opus em nuance. Voice Guard obrigatório se assinar como Alexandre.
9. **Disponibilidade geográfica:** docs mencionam variação por região; testar latência Brasil (us-east-1 provável).
10. **Live search consome quota** — `search_parameters: auto` pode adicionar custo oculto (consultar billing).
11. **Sem prompt cache documentado para 4.3** — confirmar antes de orçar economia (4-fast tinha $0.05/1M cached, mas 4-fast morreu).
12. **OpenAI SDK compat ≠ 100% paridade** — alguns campos do `/chat/completions` da OpenAI (logprobs, seed determinístico) podem comportar diferente em xAI; testar.

---

## 6. Fontes consultadas

### Oficiais xAI
- https://docs.x.ai/developers/models — catálogo + deprecações
- https://docs.x.ai/docs/overview — API overview + auth
- https://docs.x.ai/docs/api-reference — endpoints
- https://docs.x.ai/docs/pricing — pricing
- https://docs.x.ai/developers/rate-limits — tiers RPM/TPM

### Terceiros validados
- https://mem0.ai/blog/xai-grok-api-pricing — pricing pre-4.3 (mar/2026)
- https://aicostcheck.com/blog/xai-grok-pricing-guide-2026 — comparativo 4.20 vs 4.1 Fast
- https://venturebeat.com/technology/xai-launches-grok-4-3-at-an-aggressively-low-price-and-a-new-fast-powerful-voice-cloning-suite — lançamento Grok 4.3
- https://writingmate.ai/blog/grok-4-fast-2m-context-window-pricing-vs-chatgpt-2026 — Grok 4 Fast specs (descontinuado)
- https://pricepertoken.com/pricing-page/model/xai-grok-4-fast — pricing histórico
- https://github.blog/changelog/2026-05-15-grok-code-fast-1-deprecated/ — confirmação deprecation code-fast-1
- https://www.grizzlypeaksoftware.com/articles/p/grok-api-pricing-explained — comparativo abril/2026

### Conflitos de fonte (resolução canônica)
- **Pricing grok-4.20:** docs.x.ai oficial = $1.25/$2.50 (canônico). Grizzly Peak (abr/2026) trazia $2.00/$6.00 — dado obsoleto, xAI cortou preço com lançamento 4.3.
- **grok-4.1-fast vs grok-4-1-fast:** ambos nomes referenciam mesmo modelo $0.20/$0.50, **deprecated 15-mai-2026**.
- **Data de deprecação grok-4:** docs oficial = 15-mai-2026 12:00 PT. Confirmado.

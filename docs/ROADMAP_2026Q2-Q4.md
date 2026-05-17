# Roadmap 2026 Q2-Q3-Q4 — geo-orchestrator

> Fonte: [`.cto/review-2026-05-04-masterplan-15-repos.md`](.cto/review-2026-05-04-masterplan-15-repos.md) e `planoCTO.html` (913 linhas).
> Próxima revisão CTO: **2026-08-01**.
> Owner: **Alexandre Caramaschi**.

## 2026-05-17 (tarde) — Upgrade OpenAI gpt-4o → gpt-5.5

Marco de upgrade pontual disparado por ordem direta do CEO Brasil GEO:

- **`LLM_CONFIGS["gpt4o"].model`** atualizado de `gpt-4o` (lançado ago/2024) para **`gpt-5.5`** (lançado 23/04/2026 pela OpenAI), via `os.environ.get("OPENAI_MODEL", "gpt-5.5")`. Alias `"gpt4o"` mantido por compat reversa com routers.
- **Pricing** atualizado: `cost_per_1k_input` 0,0025 → **0,005** · `cost_per_1k_output` 0,010 → **0,015**.
- **Context window** ampliado de 16k para **32k tokens** (gpt-5.5 suporta 1M nativos, conservador em 32k para FinOps).
- **API compatibility fix**: `src/llm_client.py:_call_openai` detecta automaticamente modelos `gpt-5*` / `o1` / `o3` / `o4` e usa `max_completion_tokens` em vez de `max_tokens` (mudança breaking que o legacy `gpt-4*` não exige).
- **Validação**: `geo-bridge.sh ping` retorna **6/6 OK** com gpt-5.5 respondendo em 3,37s a custo real de US$ 0,000300 por health check.
- **Modelos premium ainda mais novos disponíveis** na conta: `gpt-5.5-pro-2026-04-23` (não compatível com `/v1/chat/completions`, requer `/v1/responses`), `gpt-5.1-codex-max`, `gpt-5.3-codex`. Avaliação para `groq_heavy` / tier premium em sprint futura.
- **`scripts/test_5llm_ping.py`** atualizado: chama `gpt-5.5` com `max_completion_tokens` e label "OpenAI gpt-5.5".

## 2026-05-17 — Wave xAI Grok (6º provider) + upgrade modelos canônicos

Marco de plataforma — orquestrador agora roda **6 LLM providers** (era 5):

- **Adicionado xAI Grok (com K)** como 6º provider canônico. Conta `alexandre.brt14@gmail.com` / team `caramaschigeo`. 3 entradas LLMConfig: `grok` (grok-4.3 flagship 1M ctx) + `grok_multi` (grok-4.20-multi-agent 2M ctx, 4 agentes nativos) + `grok_fast` (non-reasoning). API OpenAI-compatible em `https://api.x.ai/v1`. Pricing flat $1,25/$2,50 por 1M tokens.
- **6 task types novos exclusivos**: `realtime_search`, `social_listening`, `current_events`, `brand_monitoring`, `multi_perspective_decomposition`, `long_context_synthesis`. Diferencial único do Grok: `search_parameters` com busca live em X/Twitter (nenhum outro provider tem).
- **Upgrade simultâneo de modelos canônicos**: Claude Opus 4.6 → **4.7**, Groq default Llama 3.3 70B → **Llama 4 Scout 17B 16E** (5× mais barato), Groq Heavy default → **openai/gpt-oss-120b** (120B parâmetros).
- **Diversity guarantee em planos COMPLEX 5+ tasks** (`smart_router._ensure_provider_diversity`). Em demandas COMPLEX, garante cobertura mínima de 4 providers únicos (66%) fazendo upgrades estratégicos quando o rebalance inicial não atinge o alvo. Baseado em Mixture of Agents (Wang 2024), DAAO (2509.11079, set/2025), AdaptOrch (2602.16873, fev/2026), CASTER (2601.19793, jan/2026), When Agents Disagree (2603.20324, mar/2026).
- **Keywords premium no demand classifier**: `realtime`, `multi_perspective`, `premium_reasoning` puxam pra COMPLEX mesmo em demandas curtas com sinais críticos (ex.: "monitorar X agora" sobe pra COMPLEX automaticamente).
- **Adaptive decomposer atualizado**: `_infer_task_type` agora reconhece keywords PT-BR/EN dos 6 task types xAI (testadas ANTES das genéricas para que "monitorar Twitter" vire `social_listening` em vez de `research`).
- **Validação end-to-end**: ping 6/6 OK ($0,006 por health check); `cli.py doctor` STATUS GERAL: OK; demanda real "Pesquise tendências + monitore X + analise múltiplas perspectivas + redija + revisão crítica" gerou 8 tasks usando 5/5 providers originais (Claude Opus 4.7 t7 critical_review $0,18, cobertura 100%). Próximo refinamento: adaptive decomposer marcar tasks de X/Twitter como `realtime_search` em vez de `research` para ativar Grok.

**Próximos passos identificados pela literatura 2025-2026** (vide `docs/research/multi-llm-orchestration-2026.md`):

1. **Topology-first routing** (AdaptOrch fev/2026): decidir parallel/sequential/hierarchical/hybrid ANTES de escolher modelo. +12-23% sobre baselines.
2. **Difficulty-conditional depth** (DAAO set/2025): substituir complexity score único por `(difficulty ∈ [0,1], n_subtasks, needs_judge, evidence_required, realtime_data)`. +11,21% accuracy com 64% do custo.
3. **Confidence-based cascading** (FrugalGPT/EcoAssistant): Scout/Flash primeiro, escala para Opus/Pro se confidence < threshold.
4. **Role-aware context routing** (RCR-Router): cada subagent recebe só subset relevante da memória → -30% tokens.

## Sumário

- **Categoria:** plataforma
- **Criticidade:** alta
- **Deadline principal do trimestre:** 2026-08-25 (billing GCP + Gemini estavel)
- **Gates obrigatórios:** secret-scan, quality-gate

## Decisões pendentes do owner

- Ativar billing GCP (resolve 60% dos 503 Gemini)
- B-024 cron diario hard-delete LGPD

## Q2 2026 (mai-jun-jul) — janelas críticas

| ID | Janela | Esforço (h) | Owner | Critical path | Saída esperada | Pré-requisitos |
|---|---|---|---|---|---|---|
| Hardening-Gemini | manutencao corretiva | 8 | Alexandre | Não | Quebrar prompt < 5KB e output < 30KB; documentar limites | — |

## Q3 2026 (ago-set-out) — consolidação e infraestrutura

| ID | Janela | Esforço (h) | Owner | Critical path | Saída esperada | Pré-requisitos |
|---|---|---|---|---|---|---|
| Q3-W3 | 10-08 a 25-08 | 30 | Alexandre | Não | Ativar billing Google Cloud + monitoramento Gemini estavel | Decisao billing GCP |

## Q4 2026 (nov-dez-jan/27) — captação 2027.1 + colheita

_Sem ondas planejadas para Q4 2026._

## Observabilidade

Bearer /health, prompts SHA256 versionados, fallback chain testada, FinOps SQLite WAL atomic, CircuitBreaker.

## Política de qualidade

Toda mudança neste repo passa pelos gates transversais aplicáveis:

- **Quality gate canônico** (Next.js/TS): `tsc` + `lint` + `vitest` + `next build` antes de push.
- **Voice Guard** (conteúdo Alexandre): `python scripts/python/voice_guard.py check --file ...` antes de publicar.
- **Migration gate pt_br** (SQL): grep de acentos obrigatório antes de `apply` via Management API.
- **Pre-commit hook** (todo repo cliente): `secret_guard` ativo via `git config core.hooksPath .githooks`.
- **Snapshot Shopify** (mutations produto/variant): JSON em `data/raw/shopify-audit-logs/` antes de `productUpdate`/`variantsBulkUpdate`.
- **Browser MCP visual double-check** (mudanças de UI): `getComputedStyle` antes/depois em 1440x900 e 390x844.
- **Schema.org JSON-LD** (todo conteúdo público): validação com `validate_graphql_codeblocks` ou Rich Results.

## Disciplina de deploy

- `landing-page-geo` no Vercel: máximo **2 pushes/dia** (build minutes ~$0,26/push).
- Pre-push hook roda `next build` localmente; falhar localmente = abortar push.
- Janelas com 2+ streams paralelos exigem revisão semanal de carga em segunda 09h BRT.

## FinOps

- LLM API spend rastreado em [`geo-finops/calls.db`](https://github.com/alexandre-/geo-finops).
- Build minutes Vercel monitorados; alertas WhatsApp/email em ≥80% da quota.
- Quebrar prompts no orchestrator: `< 5KB` input e `< 30KB` output (limite Gemini MAX_TOKENS).

## Política de revisão

- Toda decisão arquitetural significativa registrada como ADR em `docs/adr/`.
- Drift entre `adminalexandre` e `landing-page-geo`: pre-commit hook (deadline 20-05).
- Revisão CTO trimestral próxima: **2026-08-01**.

---

_Gerado automaticamente pela skill `/cto` em 2026-05-04 a partir do masterplan dos 15 repositórios._

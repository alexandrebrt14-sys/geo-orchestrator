"""Round 3: Deep improvements — consult 5 LLMs, then implement.

Phase 1: Ask all 5 LLMs for their deepest improvement proposal
Phase 2: Synthesize a unified plan
Phase 3: Each LLM implements one piece of the plan
Phase 4: Generate integration report
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(override=True)
sys.stdout.reconfigure(encoding="utf-8")

SRC = Path(__file__).parent.parent / "src"
OUTPUT = Path(__file__).parent.parent / "output"
OUTPUT.mkdir(exist_ok=True)


def read_src(name):
    p = SRC / name
    return p.read_text(encoding="utf-8")[:2000] if p.exists() else ""


# Compact project summary for all agents
PROJECT = (
    "geo-orchestrator: 5.600 linhas Python, 22 modulos. "
    "5 LLMs: Claude Opus, GPT-4o, Gemini 2.5 Flash, Perplexity Sonar, Groq Llama 3.3. "
    "Decompoe demandas em tarefas tipadas, roteia via score adaptativo, executa em waves paralelas. "
    "Tem: cache SHA-256 24h, checkpoints, quality gates, budget guard, rate limiter token bucket, "
    "tracing com spans, FinOps com daily limits, connection pool, session load balancer. "
    "Rodada anterior implementou: feedback loop, routing feedback, context pipeline, "
    "task re-prioritization, complexity scoring. "
    "O sistema funciona mas precisa de aprimoramentos PROFUNDOS para ser production-grade."
)


# ============================================================
# PHASE 1: Consult all 5 LLMs for deep improvement proposals
# ============================================================

async def consult_claude():
    t0 = time.time()
    r = httpx.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 1200,
              "system": "Voce e um arquiteto de sistemas distribuidos senior. Pense em RESILIENCIA, RECUPERACAO DE FALHAS e OBSERVABILIDADE PROFUNDA.",
              "messages": [{"role": "user", "content": f"{PROJECT}\n\nProponha O APRIMORAMENTO MAIS PROFUNDO e impactante que falta neste orquestrador para ser production-grade. Seja especifico: modulo, funcao, algoritmo. Inclua pseudocodigo. Max 500 palavras. PT-BR."}]},
        timeout=60)
    d = r.json(); u = d.get("usage", {})
    return {"agent": "Claude", "role": "Resilience Architect", "time": round(time.time()-t0,1),
            "tokens_in": u.get("input_tokens",0), "tokens_out": u.get("output_tokens",0),
            "cost": u.get("input_tokens",0)/1e6*0.80 + u.get("output_tokens",0)/1e6*4.00,
            "proposal": d["content"][0]["text"]}

async def consult_gpt4o():
    t0 = time.time()
    r = httpx.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "gpt-4o-mini", "max_tokens": 1200,
              "messages": [{"role": "system", "content": "Voce e um especialista em UX de ferramentas CLI e developer experience. Pense em como FACILITAR O USO e AUMENTAR A PRODUTIVIDADE do desenvolvedor."},
                           {"role": "user", "content": f"{PROJECT}\n\nProponha O APRIMORAMENTO MAIS PROFUNDO para a experiencia do usuario deste orquestrador. Como tornar a CLI mais intuitiva, os reports mais uteis, a configuracao mais simples? Seja especifico. Max 500 palavras. PT-BR."}]},
        timeout=60)
    d = r.json(); u = d.get("usage", {})
    return {"agent": "GPT-4o", "role": "DX Specialist", "time": round(time.time()-t0,1),
            "tokens_in": u.get("prompt_tokens",0), "tokens_out": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*0.15 + u.get("completion_tokens",0)/1e6*0.60,
            "proposal": d["choices"][0]["message"]["content"]}

async def consult_gemini():
    t0 = time.time()
    key = os.getenv("GOOGLE_AI_API_KEY", "")
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
        headers={"Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": f"Voce e um especialista em otimizacao de custos de IA e FinOps. Pense em como MINIMIZAR CUSTOS sem perder qualidade.\n\n{PROJECT}\n\nProponha O APRIMORAMENTO MAIS PROFUNDO para reducao de custos. Como gastar menos tokens, usar modelos mais baratos quando possivel, cachear melhor? Seja especifico com numeros. Max 500 palavras. PT-BR."}]}],
              "generationConfig": {"maxOutputTokens": 1200}},
        timeout=60)
    d = r.json()
    if "candidates" not in d:
        return {"agent": "Gemini", "role": "Cost Optimizer", "time": round(time.time()-t0,1),
                "tokens_in": 0, "tokens_out": 0, "cost": 0, "proposal": f"ERRO: {d.get('error',{}).get('message','')[:200]}"}
    u = d.get("usageMetadata", {})
    return {"agent": "Gemini", "role": "Cost Optimizer", "time": round(time.time()-t0,1),
            "tokens_in": u.get("promptTokenCount",0), "tokens_out": u.get("candidatesTokenCount",0),
            "cost": u.get("promptTokenCount",0)/1e6*0.15 + u.get("candidatesTokenCount",0)/1e6*0.60,
            "proposal": d["candidates"][0]["content"]["parts"][0]["text"]}

async def consult_perplexity():
    t0 = time.time()
    r = httpx.post("https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('PERPLEXITY_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "sonar", "max_tokens": 1200,
              "messages": [{"role": "system", "content": "Voce e um pesquisador que conhece os frameworks mais modernos de orquestracao de IA (LangGraph, CrewAI, AutoGen, BabyAGI). Pesquise tendencias reais."},
                           {"role": "user", "content": f"{PROJECT}\n\nPesquise os frameworks mais modernos de orquestracao multi-agente (2025-2026) e proponha O APRIMORAMENTO MAIS PROFUNDO que este orquestrador deveria ter para competir com eles. Cite fontes. Max 500 palavras. PT-BR."}]},
        timeout=60)
    d = r.json(); u = d.get("usage", {})
    return {"agent": "Perplexity", "role": "Trend Researcher", "time": round(time.time()-t0,1),
            "tokens_in": u.get("prompt_tokens",0), "tokens_out": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*1.00 + u.get("completion_tokens",0)/1e6*1.00,
            "proposal": d["choices"][0]["message"]["content"]}

async def consult_groq():
    t0 = time.time()
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "max_tokens": 1200,
              "messages": [{"role": "system", "content": "Voce e um engenheiro de testes e QA senior. Pense em CONFIABILIDADE, TESTES AUTOMATIZADOS e VALIDACAO."},
                           {"role": "user", "content": f"{PROJECT}\n\nProponha O APRIMORAMENTO MAIS PROFUNDO em termos de TESTES e CONFIABILIDADE. Como garantir que o orquestrador funcione corretamente em producao? Que testes sao essenciais? Max 500 palavras. PT-BR."}]},
        timeout=60)
    d = r.json(); u = d.get("usage", {})
    return {"agent": "Groq", "role": "QA Engineer", "time": round(time.time()-t0,1),
            "tokens_in": u.get("prompt_tokens",0), "tokens_out": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*0.59 + u.get("completion_tokens",0)/1e6*0.79,
            "proposal": d["choices"][0]["message"]["content"]}


# ============================================================
# PHASE 2: Execute implementation based on proposals
# ============================================================

async def implement_claude(proposals_summary):
    """Claude implements: Error recovery + circuit breaker per LLM"""
    t0 = time.time()
    code_context = read_src("llm_client.py") + "\n\n" + read_src("pipeline.py")
    r = httpx.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY",""), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 2500,
              "messages": [{"role": "user", "content": f"Baseado nestas propostas da banca:\n{proposals_summary}\n\nImplemente um CircuitBreaker robusto por provider em Python.\nClasse CircuitBreaker com estados: CLOSED, OPEN, HALF_OPEN.\nMetodos: can_execute(), record_success(), record_failure(), get_state().\nConfigs: failure_threshold=3, recovery_timeout=60s, half_open_max=1.\nInclua docstring, type hints, logging. Codigo completo pronto para salvar.\n\nContexto atual:\n{code_context[:3000]}"}]},
        timeout=120)
    d = r.json(); u = d.get("usage",{})
    return {"agent": "Claude", "task": "CircuitBreaker class", "file": "src/circuit_breaker.py",
            "time": round(time.time()-t0,1), "tokens_in": u.get("input_tokens",0), "tokens_out": u.get("output_tokens",0),
            "cost": u.get("input_tokens",0)/1e6*0.80 + u.get("output_tokens",0)/1e6*4.00,
            "code": d["content"][0]["text"]}

async def implement_gpt4o(proposals_summary):
    """GPT-4o implements: Rich CLI dashboard with live progress"""
    t0 = time.time()
    r = httpx.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "gpt-4o-mini", "max_tokens": 2500,
              "messages": [{"role": "user", "content": f"Baseado nestas propostas:\n{proposals_summary}\n\nImplemente uma funcao Python render_live_dashboard(results, plan, costs) que usa a biblioteca rich para mostrar um dashboard em tempo real no terminal com:\n- Tabela de progresso das tarefas (status, LLM, tempo, custo)\n- Barra de progresso geral\n- Metricas de custo acumulado por provider\n- Timeline ASCII das waves\nCodigo completo, pronto para importar. Max 80 linhas."}]},
        timeout=120)
    d = r.json(); u = d.get("usage",{})
    return {"agent": "GPT-4o", "task": "Live CLI Dashboard", "file": "src/dashboard.py",
            "time": round(time.time()-t0,1), "tokens_in": u.get("prompt_tokens",0), "tokens_out": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*0.15 + u.get("completion_tokens",0)/1e6*0.60,
            "code": d["choices"][0]["message"]["content"]}

async def implement_gemini(proposals_summary):
    """Gemini implements: Smart token budget allocator"""
    t0 = time.time()
    key = os.getenv("GOOGLE_AI_API_KEY","")
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
        headers={"Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": f"Baseado nestas propostas:\n{proposals_summary}\n\nImplemente uma funcao Python allocate_token_budget(tasks: list, total_budget_tokens: int) -> dict que distribui um orcamento de tokens entre tarefas de forma inteligente:\n- Tarefas de code/writing recebem mais tokens\n- Tarefas de classification/summarization recebem menos\n- Nunca excede o budget total\n- Retorna dict task_id -> max_tokens\nCodigo completo com docstring. Max 40 linhas."}]}],
              "generationConfig": {"maxOutputTokens": 1500}},
        timeout=120)
    d = r.json()
    if "candidates" not in d:
        return {"agent": "Gemini", "task": "Token Budget Allocator", "file": "src/token_allocator.py",
                "time": round(time.time()-t0,1), "tokens_in": 0, "tokens_out": 0, "cost": 0,
                "code": f"ERRO: {d.get('error',{}).get('message','')[:200]}"}
    u = d.get("usageMetadata",{})
    return {"agent": "Gemini", "task": "Token Budget Allocator", "file": "src/token_allocator.py",
            "time": round(time.time()-t0,1), "tokens_in": u.get("promptTokenCount",0), "tokens_out": u.get("candidatesTokenCount",0),
            "cost": u.get("promptTokenCount",0)/1e6*0.15 + u.get("candidatesTokenCount",0)/1e6*0.60,
            "code": d["candidates"][0]["content"]["parts"][0]["text"]}

async def implement_perplexity(proposals_summary):
    """Perplexity implements: Agent memory system with context persistence"""
    t0 = time.time()
    r = httpx.post("https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('PERPLEXITY_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "sonar", "max_tokens": 2500,
              "messages": [{"role": "user", "content": f"Baseado nestas propostas:\n{proposals_summary}\n\nImplemente uma classe Python AgentMemory que permite agentes armazenarem e recuperarem conhecimento entre execucoes:\n- save_memory(agent_name, key, value, ttl_hours=24)\n- recall(agent_name, key) -> value or None\n- recall_all(agent_name) -> dict\n- cleanup_expired()\nPersistencia em JSON local. Codigo completo com docstring. Max 60 linhas."}]},
        timeout=120)
    d = r.json(); u = d.get("usage",{})
    return {"agent": "Perplexity", "task": "Agent Memory System", "file": "src/memory.py",
            "time": round(time.time()-t0,1), "tokens_in": u.get("prompt_tokens",0), "tokens_out": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*1.00 + u.get("completion_tokens",0)/1e6*1.00,
            "code": d["choices"][0]["message"]["content"]}

async def implement_groq(proposals_summary):
    """Groq implements: Quick validation test suite"""
    t0 = time.time()
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "max_tokens": 2000,
              "messages": [{"role": "user", "content": f"Baseado nestas propostas:\n{proposals_summary}\n\nImplemente um script Python de smoke tests que valida:\n1. Todas as 5 API keys estao configuradas e respondem\n2. Rate limiter funciona (nao dispara sem controle)\n3. Router atribui LLMs diferentes para tipos diferentes\n4. Config tem todos os providers registrados\n5. Cada teste imprime PASS/FAIL com detalhes\nCodigo completo executavel. Max 60 linhas."}]},
        timeout=60)
    d = r.json(); u = d.get("usage",{})
    return {"agent": "Groq", "task": "Smoke Test Suite", "file": "tests/smoke_test.py",
            "time": round(time.time()-t0,1), "tokens_in": u.get("prompt_tokens",0), "tokens_out": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*0.59 + u.get("completion_tokens",0)/1e6*0.79,
            "code": d["choices"][0]["message"]["content"]}


async def main():
    print("=" * 70)
    print("RODADA 3: APRIMORAMENTOS PROFUNDOS — 5 LLMs")
    print("=" * 70)

    # PHASE 1: Consult
    print("\n--- FASE 1: Consultando 5 especialistas em paralelo ---\n")
    t0 = time.time()
    proposals = await asyncio.gather(
        consult_claude(), consult_gpt4o(), consult_gemini(), consult_perplexity(), consult_groq()
    )
    phase1_time = round(time.time()-t0, 1)
    phase1_cost = sum(p["cost"] for p in proposals)

    print(f"Fase 1 concluida: {phase1_time}s, ${phase1_cost:.4f}")
    print()
    for p in proposals:
        status = "OK" if "ERRO" not in p["proposal"] else "XX"
        print(f"  [{status}] {p['agent']:<12} ({p['role']:<20}) {p['time']:>5}s  {p['tokens_out']:>5} tokens  ${p['cost']:.4f}")
        # Show first line of proposal
        first_line = p["proposal"].strip().split("\n")[0][:80]
        print(f"       -> {first_line}")
    print()

    # Build summary for phase 2
    proposals_summary = "\n\n".join(
        f"[{p['agent']} - {p['role']}]: {p['proposal'][:300]}"
        for p in proposals if "ERRO" not in p["proposal"]
    )

    # PHASE 2: Implement
    print("--- FASE 2: 5 LLMs implementando em paralelo ---\n")
    t1 = time.time()
    implementations = await asyncio.gather(
        implement_claude(proposals_summary),
        implement_gpt4o(proposals_summary),
        implement_gemini(proposals_summary),
        implement_perplexity(proposals_summary),
        implement_groq(proposals_summary),
    )
    phase2_time = round(time.time()-t1, 1)
    phase2_cost = sum(i["cost"] for i in implementations)

    print(f"Fase 2 concluida: {phase2_time}s, ${phase2_cost:.4f}")
    print()

    total_time = round(time.time()-t0, 1)
    total_cost = phase1_cost + phase2_cost
    total_in = sum(p["tokens_in"] for p in proposals) + sum(i["tokens_in"] for i in implementations)
    total_out = sum(p["tokens_out"] for p in proposals) + sum(i["tokens_out"] for i in implementations)

    # Final summary
    print("=" * 70)
    print("RESUMO FINAL")
    print("=" * 70)
    header = f"  {'Agente':<12} {'Tarefa':<25} {'Tempo':>6} {'In':>6} {'Out':>6} {'Custo':>8}"
    print(header)
    print("  " + "-" * 70)
    for i in implementations:
        s = "OK" if "ERRO" not in i["code"] else "XX"
        print(f"  [{s}] {i['agent']:<10} {i['task']:<25} {i['time']:>5}s {i['tokens_in']:>5} {i['tokens_out']:>5} ${i['cost']:>7.4f}")
    print("  " + "-" * 70)
    print(f"  TOTAL (2 fases)  10 chamadas LLM         {total_time:>5}s {total_in:>5} {total_out:>5} ${total_cost:>7.4f}")
    print()

    # Show implementations
    for i in implementations:
        print("=" * 70)
        print(f"{i['agent']} -> {i['task']} ({i['file']})")
        print("=" * 70)
        print(i["code"][:500])
        if len(i["code"]) > 500:
            print(f"... [{len(i['code'])} chars]")
        print()

    # Save
    report = {
        "round": 3,
        "phases": ["consult", "implement"],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "phase1": {"time_s": phase1_time, "cost_usd": phase1_cost, "proposals": proposals},
        "phase2": {"time_s": phase2_time, "cost_usd": phase2_cost, "implementations": implementations},
        "totals": {"time_s": total_time, "cost_usd": total_cost, "tokens_in": total_in, "tokens_out": total_out},
    }
    out_path = OUTPUT / "round3_deep_improvements.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Resultados salvos em {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

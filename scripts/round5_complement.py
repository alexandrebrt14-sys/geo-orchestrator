"""Round 5: 4 experts complement the page with deep insights.

Each expert provides specific content to enrich the page based on their specialty.
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

OUTPUT = Path(__file__).parent.parent / "output"
OUTPUT.mkdir(exist_ok=True)

CONTEXT = (
    "O geo-orchestrator e um orquestrador multi-LLM com 7.471 linhas Python, 5 LLMs "
    "(Claude, GPT-4o, Gemini, Perplexity, Groq), 22 modulos, 17 commits. "
    "Decompoe demandas em linguagem natural, roteia com score adaptativo, executa em waves "
    "paralelas. 4 rodadas de auto-melhoria custaram $0.07. "
    "Funcionalidades: cache, checkpoints, quality gates, budget guard, rate limiter, "
    "tracing, FinOps, circuit breaker, agent memory, token allocator. "
    "CLI: python cli.py run 'demanda'. Pagina web em alexandrecaramaschi.com/geo-orchestrator."
)


async def andrew_ng():
    """Andrew Ng: How agentic workflows make this different"""
    t0 = time.time()
    r = httpx.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY",""), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 600,
              "system": "Voce e Andrew Ng. Explique por que ITERACAO DE AGENTES e o diferencial deste orquestrador vs usar um unico LLM grande. Dados concretos, sem buzzwords. PT-BR. Max 200 palavras.",
              "messages": [{"role": "user", "content": CONTEXT}]},
        timeout=60)
    d = r.json(); u = d.get("usage",{})
    return {"expert": "Andrew Ng", "llm": "Claude", "focus": "Agentic Workflows",
            "time": round(time.time()-t0,1), "tokens": u.get("output_tokens",0),
            "cost": u.get("input_tokens",0)/1e6*0.80 + u.get("output_tokens",0)/1e6*4.00,
            "insight": d["content"][0]["text"]}

async def harrison_chase():
    """Harrison Chase: How this compares to LangChain/LangGraph"""
    t0 = time.time()
    r = httpx.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "gpt-4o-mini", "max_tokens": 600,
              "messages": [{"role": "system", "content": "Voce e Harrison Chase (LangChain/LangGraph). Compare este orquestrador com LangGraph. O que ele faz diferente? Onde e melhor? Onde pode melhorar? PT-BR. Max 200 palavras."},
                           {"role": "user", "content": CONTEXT}]},
        timeout=60)
    d = r.json(); u = d.get("usage",{})
    return {"expert": "Harrison Chase", "llm": "GPT-4o", "focus": "vs LangGraph",
            "time": round(time.time()-t0,1), "tokens": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*0.15 + u.get("completion_tokens",0)/1e6*0.60,
            "insight": d["choices"][0]["message"]["content"]}

async def jerry_liu():
    """Jerry Liu: How RAG patterns could enhance this"""
    t0 = time.time()
    r = httpx.post("https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('PERPLEXITY_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "sonar", "max_tokens": 600,
              "messages": [{"role": "system", "content": "Voce e Jerry Liu (LlamaIndex). Explique como padroes de RAG agentico poderiam tornar este orquestrador ainda mais poderoso. Cite tendencias reais de 2025-2026. PT-BR. Max 200 palavras."},
                           {"role": "user", "content": CONTEXT}]},
        timeout=60)
    d = r.json(); u = d.get("usage",{})
    return {"expert": "Jerry Liu", "llm": "Perplexity", "focus": "Agentic RAG",
            "time": round(time.time()-t0,1), "tokens": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*1.0 + u.get("completion_tokens",0)/1e6*1.0,
            "insight": d["choices"][0]["message"]["content"]}

async def yohei_nakajima():
    """Yohei Nakajima: Autonomous task management insights"""
    t0 = time.time()
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "max_tokens": 600,
              "messages": [{"role": "system", "content": "Voce e Yohei Nakajima (BabyAGI). Explique como o conceito de Task-Driven Autonomous Agents se manifesta neste orquestrador e o que falta para autonomia total. PT-BR. Max 200 palavras."},
                           {"role": "user", "content": CONTEXT}]},
        timeout=30)
    d = r.json(); u = d.get("usage",{})
    return {"expert": "Yohei Nakajima", "llm": "Groq", "focus": "Autonomous Agents",
            "time": round(time.time()-t0,1), "tokens": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*0.59 + u.get("completion_tokens",0)/1e6*0.79,
            "insight": d["choices"][0]["message"]["content"]}


async def main():
    print("=" * 70)
    print("RODADA 5: INSIGHTS DE 4 ESPECIALISTAS PARA COMPLEMENTAR A PAGINA")
    print("=" * 70)
    print()

    t0 = time.time()
    results = await asyncio.gather(andrew_ng(), harrison_chase(), jerry_liu(), yohei_nakajima())
    wall = round(time.time()-t0, 1)
    total_cost = sum(r["cost"] for r in results)

    for r in results:
        print(f"  [{r['llm']:<11}] {r['expert']:<18} ({r['focus']:<20}) {r['time']:>5}s  ${r['cost']:.4f}")
    print(f"\n  Total: {wall}s, ${total_cost:.4f}")
    print()

    for r in results:
        print(f"{'='*70}")
        print(f"{r['expert']} ({r['focus']}) via {r['llm']}")
        print(f"{'='*70}")
        print(r["insight"][:500])
        if len(r["insight"]) > 500: print("...[truncado]")
        print()

    report = {"round": 5, "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "wall_time": wall, "total_cost": total_cost, "experts": results}
    out = OUTPUT / "round5_expert_insights.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Salvos em {out}")


if __name__ == "__main__":
    asyncio.run(main())

"""5-LLM Expert Board — Parallel analysis of geo-orchestrator.

Each LLM plays a specific AI expert role:
- Claude (Andrew Ng): CEO, Agentic Workflows
- GPT-4o (Harrison Chase): LangChain/LangGraph, Agent Orchestration
- Gemini (Jerry Liu): LlamaIndex, Agentic RAG
- Perplexity (Yohei Nakajima): BabyAGI, Task-Driven Autonomous Agents
- Groq (Performance Engineer): Speed & Efficiency
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

# Read source code for context
SRC = Path(__file__).parent.parent / "src"
CODE_FILES = {}
for name in ["orchestrator.py", "router.py", "pipeline.py", "config.py", "llm_client.py"]:
    p = SRC / name
    if p.exists():
        CODE_FILES[name] = p.read_text(encoding="utf-8")[:2500]

CODE_CONTEXT = "\n\n".join(f"=== {k} ===\n{v}" for k, v in CODE_FILES.items())

TASK_PROMPT = (
    "Analise este orquestrador multi-LLM (geo-orchestrator) e proponha 1 melhoria "
    "concreta e implementavel. O sistema decompoe demandas em tarefas, distribui entre "
    "5 LLMs (Claude, GPT-4o, Gemini, Perplexity, Groq), executa em waves paralelas. "
    "Tem cache, checkpoints, quality gates, budget guard, router adaptativo, rate limiter, tracing. "
    "Diga: arquivo, funcao, como implementar. Max 400 palavras. PT-BR."
)


async def call_claude():
    """CEO/Observer - Andrew Ng (Agentic Workflows)"""
    t0 = time.time()
    system = (
        "Voce e Andrew Ng, maior educador de IA do mundo e evangelista de Agentic Workflows. "
        "Seu papel: CEO e observador. Analise a arquitetura focando em ITERACAO DE AGENTES. "
        "Defenda que iteracao e mais importante que tamanho do modelo."
    )
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": os.getenv("ANTHROPIC_API_KEY", ""),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 800,
            "system": system,
            "messages": [{"role": "user", "content": TASK_PROMPT + "\n\n" + CODE_CONTEXT[:4000]}],
        },
        timeout=60,
    )
    data = r.json()
    elapsed = round(time.time() - t0, 1)
    u = data.get("usage", {})
    ti, to = u.get("input_tokens", 0), u.get("output_tokens", 0)
    text = data["content"][0]["text"]
    cost = ti / 1_000_000 * 0.80 + to / 1_000_000 * 4.00
    return ("Andrew Ng (CEO)", "Claude Haiku", elapsed, ti, to, cost, text)


async def call_gpt4o():
    """Harrison Chase - LangChain/LangGraph"""
    t0 = time.time()
    system = (
        "Voce e Harrison Chase, criador do LangChain e LangGraph. "
        "Foco: ORQUESTRACAO DE AGENTES CICLICOS com grafos e loops de auto-correcao."
    )
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}",
            "Content-Type": "application/json",
        },
        json={
            "model": "gpt-4o-mini",
            "max_tokens": 800,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": TASK_PROMPT + "\n\n" + CODE_CONTEXT[:4000]},
            ],
        },
        timeout=60,
    )
    data = r.json()
    elapsed = round(time.time() - t0, 1)
    u = data.get("usage", {})
    ti, to = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    text = data["choices"][0]["message"]["content"]
    cost = ti / 1_000_000 * 0.15 + to / 1_000_000 * 0.60
    return ("Harrison Chase (LangChain)", "GPT-4o-mini", elapsed, ti, to, cost, text)


async def call_gemini():
    """Jerry Liu - LlamaIndex (Agentic RAG)"""
    t0 = time.time()
    key = os.getenv("GOOGLE_AI_API_KEY", "")
    prompt = (
        "Voce e Jerry Liu, CEO do LlamaIndex. Foco: RAG AGENTICO. "
        "Analise como o orquestrador pode usar recuperacao inteligente de dados e contexto "
        "entre tarefas, similar ao LlamaIndex.\n\n"
        + TASK_PROMPT + "\n\n" + CODE_CONTEXT[:3500]
    )
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
        headers={"Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 800},
        },
        timeout=60,
    )
    data = r.json()
    elapsed = round(time.time() - t0, 1)
    if "candidates" not in data:
        err = data.get("error", {}).get("message", "unknown")[:200]
        return ("Jerry Liu (LlamaIndex)", "Gemini 2.5 Flash", elapsed, 0, 0, 0.0, f"ERRO: {err}")
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    u = data.get("usageMetadata", {})
    ti, to = u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)
    cost = ti / 1_000_000 * 0.15 + to / 1_000_000 * 0.60
    return ("Jerry Liu (LlamaIndex)", "Gemini 2.5 Flash", elapsed, ti, to, cost, text)


async def call_perplexity():
    """Yohei Nakajima - BabyAGI (Task-Driven Autonomous Agents)"""
    t0 = time.time()
    system = (
        "Voce e Yohei Nakajima, criador do BabyAGI. Foco: AGENTES AUTONOMOS ORIENTADOS A TAREFAS. "
        "Analise como o orquestrador pode se tornar mais autonomo, gerando e re-priorizando tarefas."
    )
    r = httpx.post(
        "https://api.perplexity.ai/chat/completions",
        headers={
            "Authorization": f"Bearer {os.getenv('PERPLEXITY_API_KEY', '')}",
            "Content-Type": "application/json",
        },
        json={
            "model": "sonar",
            "max_tokens": 800,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": TASK_PROMPT + "\n\n" + CODE_CONTEXT[:3500]},
            ],
        },
        timeout=60,
    )
    data = r.json()
    elapsed = round(time.time() - t0, 1)
    u = data.get("usage", {})
    ti, to = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    text = data["choices"][0]["message"]["content"]
    cost = ti / 1_000_000 * 1.00 + to / 1_000_000 * 1.00
    return ("Yohei Nakajima (BabyAGI)", "Perplexity Sonar", elapsed, ti, to, cost, text)


async def call_groq():
    """Performance Engineer - Speed & Efficiency"""
    t0 = time.time()
    system = (
        "Voce e um engenheiro de performance senior. "
        "Foco: VELOCIDADE e EFICIENCIA. Como reduzir latencia e desperdicio de tokens."
    )
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}",
            "Content-Type": "application/json",
        },
        json={
            "model": "llama-3.3-70b-versatile",
            "max_tokens": 800,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": TASK_PROMPT + "\n\n" + CODE_CONTEXT[:3500]},
            ],
        },
        timeout=60,
    )
    data = r.json()
    elapsed = round(time.time() - t0, 1)
    u = data.get("usage", {})
    ti, to = u.get("prompt_tokens", 0), u.get("completion_tokens", 0)
    text = data["choices"][0]["message"]["content"]
    cost = ti / 1_000_000 * 0.59 + to / 1_000_000 * 0.79
    return ("Eng. Performance (Groq)", "Groq Llama 3.3", elapsed, ti, to, cost, text)


async def main():
    print("=" * 70)
    print("BANCA DE 5 ESPECIALISTAS EM IA - 5 LLMs EM PARALELO")
    print("=" * 70)
    print()
    print("Lancando 5 agentes em paralelo...")
    print("  [1] Andrew Ng (CEO)       -> Claude Haiku")
    print("  [2] Harrison Chase         -> GPT-4o-mini")
    print("  [3] Jerry Liu              -> Gemini 2.5 Flash")
    print("  [4] Yohei Nakajima         -> Perplexity Sonar")
    print("  [5] Eng. Performance       -> Groq Llama 3.3")
    print()

    t0 = time.time()
    results = await asyncio.gather(
        call_claude(),
        call_gpt4o(),
        call_gemini(),
        call_perplexity(),
        call_groq(),
    )
    wall = round(time.time() - t0, 1)

    total_cost = sum(r[5] for r in results)
    total_in = sum(r[3] for r in results)
    total_out = sum(r[4] for r in results)
    ok_count = sum(1 for r in results if "ERRO" not in r[6])

    print(f"Concluido: {ok_count}/5 agentes em {wall}s (paralelo)")
    print()

    # Summary table
    header = f"  {'Agente':<28} {'LLM':<18} {'Tempo':>6} {'In':>6} {'Out':>6} {'Custo':>8}"
    print(header)
    print("  " + "-" * 76)
    for name, llm, elapsed, ti, to, cost, text in results:
        status = "OK" if "ERRO" not in text else "XX"
        print(f"  [{status}] {name:<26} {llm:<18} {elapsed:>5}s {ti:>5} {to:>5} ${cost:>7.4f}")
    print("  " + "-" * 76)
    print(f"  {'TOTAL':<28} {'5 LLMs':<18} {wall:>5}s {total_in:>5} {total_out:>5} ${total_cost:>7.4f}")
    print()

    # Detailed results
    for name, llm, elapsed, ti, to, cost, text in results:
        print("=" * 70)
        print(f"{name} ({llm}, {elapsed}s, ${cost:.4f})")
        print("=" * 70)
        print(text[:800])
        if len(text) > 800:
            print("...[truncado]")
        print()

    # Save
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wall_time_s": wall,
        "total_cost_usd": total_cost,
        "total_tokens_in": total_in,
        "total_tokens_out": total_out,
        "agents_ok": ok_count,
        "agents": [],
    }
    for name, llm, elapsed, ti, to, cost, text in results:
        report["agents"].append({
            "name": name,
            "llm": llm,
            "time_s": elapsed,
            "tokens_in": ti,
            "tokens_out": to,
            "cost_usd": cost,
            "response": text,
        })

    out_path = Path("output/5llm_board_analysis.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Resultados salvos em {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

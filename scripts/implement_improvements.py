"""Round 2: Each LLM implements its own proposed improvement.

Based on the 5-expert board analysis, each LLM now generates
concrete code for its own recommendation:

1. Claude (Andrew Ng): Feedback loop with task iteration
2. GPT-4o (Harrison Chase): Adaptive routing feedback system
3. Gemini (Jerry Liu): Agentic RAG context pipeline
4. Perplexity (Yohei Nakajima): Dynamic task re-prioritization
5. Groq (Performance Engineer): Predictive complexity scoring
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

# Load current code for context
def read_file(name):
    p = SRC / name
    return p.read_text(encoding="utf-8") if p.exists() else ""

ORCHESTRATOR = read_file("orchestrator.py")
PIPELINE = read_file("pipeline.py")
ROUTER = read_file("router.py")
CONFIG = read_file("config.py")


async def task_claude():
    """Andrew Ng: Implement feedback loop in orchestrator.py"""
    t0 = time.time()
    prompt = (
        "Voce e um senior Python developer. Implemente um Feedback Loop Adaptativo no orchestrator.\n\n"
        "REQUISITO: Apos executar todas as waves, o sistema deve:\n"
        "1. Avaliar a qualidade dos resultados (quality score 0-100)\n"
        "2. Se alguma tarefa tem score < 60, re-executar com prompt refinado\n"
        "3. Maximo 2 iteracoes para evitar loop infinito\n"
        "4. Logar cada iteracao com metricas\n\n"
        "Gere APENAS o codigo Python de uma funcao async _feedback_loop(self, results, plan) -> dict.\n"
        "Deve se integrar no orchestrator.py apos pipeline.execute().\n"
        "Inclua docstring e type hints. Max 80 linhas.\n\n"
        f"CONTEXTO (orchestrator.py atual):\n{ORCHESTRATOR[:3000]}"
    )
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 2000, "messages": [{"role": "user", "content": prompt}]},
        timeout=90,
    )
    data = r.json()
    u = data.get("usage", {})
    return {
        "agent": "Claude (Andrew Ng)", "task": "Feedback Loop", "file": "orchestrator.py",
        "time": round(time.time() - t0, 1), "tokens_in": u.get("input_tokens", 0),
        "tokens_out": u.get("output_tokens", 0),
        "cost": u.get("input_tokens", 0) / 1e6 * 0.80 + u.get("output_tokens", 0) / 1e6 * 4.00,
        "code": data["content"][0]["text"],
    }


async def task_gpt4o():
    """Harrison Chase: Implement routing feedback in router.py"""
    t0 = time.time()
    prompt = (
        "Voce e um senior Python developer. Implemente um sistema de Feedback Adaptativo no router.\n\n"
        "REQUISITO: Apos cada tarefa executada, o router deve:\n"
        "1. Receber feedback (success, quality_score, latency, cost)\n"
        "2. Atualizar um scoring por (task_type, llm) persistido em JSON\n"
        "3. Na proxima vez que rotear, preferir LLMs com melhor score recente\n"
        "4. Decay: scores antigos perdem peso (weighted moving average)\n\n"
        "Gere APENAS o codigo Python de 2 funcoes:\n"
        "- record_feedback(self, task_type, llm, success, quality, latency, cost)\n"
        "- _compute_adaptive_score(self, task_type, llm) -> float\n"
        "Inclua docstring e type hints. Max 60 linhas.\n\n"
        f"CONTEXTO (router.py atual):\n{ROUTER[:2500]}"
    )
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}", "Content-Type": "application/json"},
        json={"model": "gpt-4o-mini", "max_tokens": 2000,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=90,
    )
    data = r.json()
    u = data.get("usage", {})
    return {
        "agent": "GPT-4o (Harrison Chase)", "task": "Routing Feedback", "file": "router.py",
        "time": round(time.time() - t0, 1), "tokens_in": u.get("prompt_tokens", 0),
        "tokens_out": u.get("completion_tokens", 0),
        "cost": u.get("prompt_tokens", 0) / 1e6 * 0.15 + u.get("completion_tokens", 0) / 1e6 * 0.60,
        "code": data["choices"][0]["message"]["content"],
    }


async def task_gemini():
    """Jerry Liu: Implement context pipeline between tasks"""
    t0 = time.time()
    key = os.getenv("GOOGLE_AI_API_KEY", "")
    prompt = (
        "Voce e um senior Python developer. Implemente um Context Pipeline Agentico.\n\n"
        "REQUISITO: Entre waves, o pipeline deve:\n"
        "1. Coletar outputs de tarefas concluidas\n"
        "2. Extrair entidades, fatos-chave e dados estruturados\n"
        "3. Criar um 'context summary' compacto para tarefas dependentes\n"
        "4. Evitar passar texto bruto (economiza tokens)\n\n"
        "Gere APENAS o codigo Python de uma funcao:\n"
        "- build_context_summary(completed_results: dict[str, str]) -> str\n"
        "Que recebe outputs das tarefas e retorna contexto otimizado.\n"
        "Inclua docstring e type hints. Max 50 linhas.\n\n"
        f"CONTEXTO (pipeline.py atual):\n{PIPELINE[:2500]}"
    )
    r = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
        headers={"Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 2000}},
        timeout=90,
    )
    data = r.json()
    if "candidates" not in data:
        err = data.get("error", {}).get("message", "unknown")[:200]
        return {"agent": "Gemini (Jerry Liu)", "task": "Context Pipeline", "file": "pipeline.py",
                "time": round(time.time() - t0, 1), "tokens_in": 0, "tokens_out": 0, "cost": 0, "code": f"ERRO: {err}"}
    u = data.get("usageMetadata", {})
    return {
        "agent": "Gemini (Jerry Liu)", "task": "Context Pipeline", "file": "pipeline.py",
        "time": round(time.time() - t0, 1), "tokens_in": u.get("promptTokenCount", 0),
        "tokens_out": u.get("candidatesTokenCount", 0),
        "cost": u.get("promptTokenCount", 0) / 1e6 * 0.15 + u.get("candidatesTokenCount", 0) / 1e6 * 0.60,
        "code": data["candidates"][0]["content"]["parts"][0]["text"],
    }


async def task_perplexity():
    """Yohei Nakajima: Implement dynamic task re-prioritization"""
    t0 = time.time()
    prompt = (
        "Voce e um senior Python developer. Implemente Re-priorizacao Dinamica de tarefas.\n\n"
        "REQUISITO: Apos cada wave, o pipeline deve:\n"
        "1. Avaliar resultados parciais\n"
        "2. Re-priorizar tarefas pendentes baseado no que ja foi produzido\n"
        "3. Poder ADICIONAR novas tarefas se gaps foram identificados\n"
        "4. Poder REMOVER tarefas redundantes\n\n"
        "Gere APENAS o codigo Python de uma funcao:\n"
        "- reprioritize_tasks(completed: dict, pending: list[dict], demand: str) -> list[dict]\n"
        "Que retorna a lista de tarefas pendentes reordenada/modificada.\n"
        "Inclua docstring e type hints. Max 60 linhas.\n\n"
        f"CONTEXTO (pipeline.py atual):\n{PIPELINE[:2500]}"
    )
    r = httpx.post(
        "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('PERPLEXITY_API_KEY', '')}", "Content-Type": "application/json"},
        json={"model": "sonar", "max_tokens": 2000,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=90,
    )
    data = r.json()
    u = data.get("usage", {})
    return {
        "agent": "Perplexity (Yohei Nakajima)", "task": "Task Re-prioritization", "file": "pipeline.py",
        "time": round(time.time() - t0, 1), "tokens_in": u.get("prompt_tokens", 0),
        "tokens_out": u.get("completion_tokens", 0),
        "cost": u.get("prompt_tokens", 0) / 1e6 * 1.00 + u.get("completion_tokens", 0) / 1e6 * 1.00,
        "code": data["choices"][0]["message"]["content"],
    }


async def task_groq():
    """Groq: Implement predictive complexity scoring"""
    t0 = time.time()
    prompt = (
        "Voce e um senior Python developer. Implemente Scoring de Complexidade Preditivo.\n\n"
        "REQUISITO: Antes de rotear, o sistema deve:\n"
        "1. Analisar a descricao da tarefa (comprimento, palavras-chave)\n"
        "2. Atribuir score de complexidade: low (0-33), medium (34-66), high (67-100)\n"
        "3. Tarefas low -> LLMs baratos (Groq, Gemini)\n"
        "4. Tarefas high -> LLMs poderosos (Claude, GPT-4o)\n\n"
        "Gere APENAS o codigo Python de uma funcao:\n"
        "- predict_complexity(task_type: str, description: str, deps_count: int) -> tuple[str, int]\n"
        "Que retorna (nivel, score). Nivel e 'low', 'medium' ou 'high'.\n"
        "Inclua docstring e type hints. Max 40 linhas.\n\n"
        f"CONTEXTO (config.py atual):\n{CONFIG[:2000]}"
    )
    r = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "max_tokens": 1500,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=60,
    )
    data = r.json()
    u = data.get("usage", {})
    return {
        "agent": "Groq (Performance Eng.)", "task": "Complexity Scoring", "file": "config.py",
        "time": round(time.time() - t0, 1), "tokens_in": u.get("prompt_tokens", 0),
        "tokens_out": u.get("completion_tokens", 0),
        "cost": u.get("prompt_tokens", 0) / 1e6 * 0.59 + u.get("completion_tokens", 0) / 1e6 * 0.79,
        "code": data["choices"][0]["message"]["content"],
    }


async def main():
    print("=" * 70)
    print("RODADA 2: 5 LLMs IMPLEMENTANDO MELHORIAS EM PARALELO")
    print("=" * 70)
    print()
    print("Cada LLM implementa a melhoria que ele mesmo propôs:")
    print("  [Claude]     Feedback Loop Adaptativo     -> orchestrator.py")
    print("  [GPT-4o]     Routing Feedback System       -> router.py")
    print("  [Gemini]     Context Pipeline Agêntico     -> pipeline.py")
    print("  [Perplexity] Task Re-prioritization        -> pipeline.py")
    print("  [Groq]       Predictive Complexity Scoring -> config.py")
    print()

    t0 = time.time()
    results = await asyncio.gather(
        task_claude(), task_gpt4o(), task_gemini(), task_perplexity(), task_groq()
    )
    wall = round(time.time() - t0, 1)

    total_cost = sum(r["cost"] for r in results)
    total_in = sum(r["tokens_in"] for r in results)
    total_out = sum(r["tokens_out"] for r in results)
    ok = sum(1 for r in results if "ERRO" not in r["code"])

    print(f"\nConcluído: {ok}/5 implementações em {wall}s (paralelo)")
    print()

    header = f"  {'Agente':<28} {'Tarefa':<25} {'Tempo':>6} {'In':>6} {'Out':>6} {'Custo':>8}"
    print(header)
    print("  " + "-" * 80)
    for r in results:
        s = "OK" if "ERRO" not in r["code"] else "XX"
        print(f"  [{s}] {r['agent']:<26} {r['task']:<25} {r['time']:>5}s {r['tokens_in']:>5} {r['tokens_out']:>5} ${r['cost']:>7.4f}")
    print("  " + "-" * 80)
    print(f"  {'TOTAL':<28} {'5 implementações':<25} {wall:>5}s {total_in:>5} {total_out:>5} ${total_cost:>7.4f}")
    print()

    # Show code snippets
    for r in results:
        print("=" * 70)
        print(f"{r['agent']} -> {r['task']} ({r['file']})")
        print("=" * 70)
        code = r["code"]
        # Show first 600 chars
        print(code[:600])
        if len(code) > 600:
            print(f"... [{len(code)} chars total]")
        print()

    # Save full results
    report = {
        "round": 2,
        "purpose": "5 LLMs implementing their own proposed improvements",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "wall_time_s": wall,
        "total_cost_usd": total_cost,
        "total_tokens_in": total_in,
        "total_tokens_out": total_out,
        "implementations": results,
    }
    out_path = OUTPUT / "round2_implementations.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Resultados completos salvos em {out_path}")


if __name__ == "__main__":
    asyncio.run(main())

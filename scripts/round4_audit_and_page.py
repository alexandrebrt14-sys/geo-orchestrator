"""Round 4: Full audit of geo-orchestrator + create alexandrecaramaschi.com page.

5 LLMs working in orchestrated phases:
Phase 1 (parallel): Research + Audit + Analysis
Phase 2 (parallel): Write content + Design page structure
Phase 3 (parallel): Generate code + Review
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

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src"
OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)

# Collect project metrics
def get_project_metrics():
    py_files = list(SRC.rglob("*.py")) + [ROOT / "cli.py"]
    py_files = [f for f in py_files if "__pycache__" not in str(f)]
    total_lines = sum(len(f.read_text(encoding="utf-8").splitlines()) for f in py_files)

    modules = []
    for f in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(f) or "__init__" in f.name:
            continue
        lines = len(f.read_text(encoding="utf-8").splitlines())
        modules.append({"name": f.stem, "path": str(f.relative_to(ROOT)), "lines": lines})

    # Read README for features
    readme = (ROOT / "README.md").read_text(encoding="utf-8")[:3000]
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")[:2000]

    # Git log
    import subprocess
    git_log = subprocess.run(["git", "log", "--oneline"], capture_output=True, text=True, cwd=str(ROOT)).stdout

    return {
        "total_py_files": len(py_files),
        "total_py_lines": total_lines,
        "modules": modules,
        "commits": git_log.strip().split("\n"),
        "readme_excerpt": readme,
        "claude_md_excerpt": claude_md,
    }


METRICS = get_project_metrics()
METRICS_JSON = json.dumps({
    "files": METRICS["total_py_files"],
    "lines": METRICS["total_py_lines"],
    "modules": [{"name": m["name"], "lines": m["lines"]} for m in METRICS["modules"]],
    "commits_count": len(METRICS["commits"]),
    "last_5_commits": METRICS["commits"][:5],
}, ensure_ascii=False, indent=2)

print(f"Projeto: {METRICS['total_py_files']} arquivos, {METRICS['total_py_lines']} linhas, {len(METRICS['commits'])} commits")
print()


# ============================================================
# PHASE 1: Research + Audit + Analysis (3 LLMs in parallel)
# ============================================================

async def phase1_perplexity_research():
    """Perplexity: Research best practices for showcasing dev tools"""
    t0 = time.time()
    r = httpx.post("https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('PERPLEXITY_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "sonar", "max_tokens": 1500,
              "messages": [{"role": "system", "content": "Pesquise com fontes reais e atualizadas sobre como apresentar projetos de IA open-source."},
                           {"role": "user", "content": "Pesquise as melhores praticas para criar uma pagina de showcase de um projeto de orquestracao multi-LLM. Como LangChain, CrewAI e AutoGen apresentam seus projetos? Que secoes sao essenciais? Que metricas impressionam? Cite fontes reais. PT-BR."}]},
        timeout=60)
    d = r.json(); u = d.get("usage", {})
    return {"agent": "Perplexity", "task": "research", "time": round(time.time()-t0,1),
            "tokens_in": u.get("prompt_tokens",0), "tokens_out": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*1.0 + u.get("completion_tokens",0)/1e6*1.0,
            "output": d["choices"][0]["message"]["content"]}


async def phase1_claude_audit():
    """Claude: Deep technical audit of the project"""
    t0 = time.time()
    r = httpx.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY",""), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 2000,
              "messages": [{"role": "user", "content": f"Faca uma auditoria tecnica completa deste projeto baseado nos metadados abaixo. Liste:\n1. Todas as capacidades (o que o sistema faz)\n2. Stack tecnologica completa\n3. Pontos fortes\n4. Diferenciais competitivos vs LangChain/CrewAI\n5. Metricas impressionantes\n\nMETRICAS DO PROJETO:\n{METRICS_JSON}\n\nREADME:\n{METRICS['readme_excerpt'][:2000]}\n\nCLAUDE.MD:\n{METRICS['claude_md_excerpt'][:1500]}\n\nResponda em PT-BR com formatacao Markdown."}]},
        timeout=90)
    d = r.json(); u = d.get("usage", {})
    return {"agent": "Claude", "task": "audit", "time": round(time.time()-t0,1),
            "tokens_in": u.get("input_tokens",0), "tokens_out": u.get("output_tokens",0),
            "cost": u.get("input_tokens",0)/1e6*0.80 + u.get("output_tokens",0)/1e6*4.00,
            "output": d["content"][0]["text"]}


async def phase1_groq_analysis():
    """Groq: Quick analysis of page structure needed"""
    t0 = time.time()
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "max_tokens": 1000,
              "messages": [{"role": "user", "content": f"Analise rapidamente este projeto e proponha a ESTRUTURA EXATA de uma pagina web para apresenta-lo:\n\nProjeto: geo-orchestrator\n{METRICS_JSON[:1500]}\n\nListe as secoes da pagina em ordem, com titulo e descricao de cada uma. Formato: lista numerada. Max 300 palavras. PT-BR."}]},
        timeout=30)
    d = r.json(); u = d.get("usage", {})
    return {"agent": "Groq", "task": "structure", "time": round(time.time()-t0,1),
            "tokens_in": u.get("prompt_tokens",0), "tokens_out": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*0.59 + u.get("completion_tokens",0)/1e6*0.79,
            "output": d["choices"][0]["message"]["content"]}


# ============================================================
# PHASE 2: Write content + Design (2 LLMs in parallel)
# ============================================================

async def phase2_gpt4o_content(context):
    """GPT-4o: Write all page content sections"""
    t0 = time.time()
    r = httpx.post("https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "gpt-4o-mini", "max_tokens": 3000,
              "messages": [{"role": "system", "content": "Voce e um copywriter tecnico senior. Escreva conteudo para uma pagina web de showcase de projeto de IA. Tom: profissional, dados concretos, sem buzzwords vazios. PT-BR."},
                           {"role": "user", "content": f"Baseado nesta auditoria e pesquisa, escreva o CONTEUDO COMPLETO para cada secao da pagina do geo-orchestrator:\n\nAUDITORIA:\n{context['audit'][:2000]}\n\nPESQUISA:\n{context['research'][:1500]}\n\nESTRUTURA:\n{context['structure'][:1000]}\n\nEscreva o texto de cada secao: Hero (titulo + subtitulo + CTA), Como Funciona, 5 LLMs, Funcionalidades, Metricas, Stack, Como Usar. Markdown formatado."}]},
        timeout=120)
    d = r.json(); u = d.get("usage", {})
    return {"agent": "GPT-4o", "task": "content", "time": round(time.time()-t0,1),
            "tokens_in": u.get("prompt_tokens",0), "tokens_out": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*0.15 + u.get("completion_tokens",0)/1e6*0.60,
            "output": d["choices"][0]["message"]["content"]}


async def phase2_gemini_design(context):
    """Gemini: Design the page layout and component structure"""
    t0 = time.time()
    key = os.getenv("GOOGLE_AI_API_KEY","")
    r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}",
        headers={"Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": f"Voce e um designer de paginas web. Baseado nesta estrutura, defina o LAYOUT VISUAL da pagina:\n\nESTRUTURA:\n{context['structure'][:1000]}\n\nDefina para cada secao: cor de fundo, layout (grid/flex), numero de colunas, icones sugeridos, estilo dos cards. Use o design system Salesforce/Lucida (accent: #0176d3, success: #2e844a, card-dark: #032d60). Formato: lista com CSS inline sugerido por secao. Max 400 palavras. PT-BR."}]}],
              "generationConfig": {"maxOutputTokens": 1500}},
        timeout=60)
    d = r.json()
    if "candidates" not in d:
        return {"agent": "Gemini", "task": "design", "time": round(time.time()-t0,1),
                "tokens_in": 0, "tokens_out": 0, "cost": 0, "output": f"ERRO: {d.get('error',{}).get('message','')[:200]}"}
    u = d.get("usageMetadata", {})
    return {"agent": "Gemini", "task": "design", "time": round(time.time()-t0,1),
            "tokens_in": u.get("promptTokenCount",0), "tokens_out": u.get("candidatesTokenCount",0),
            "cost": u.get("promptTokenCount",0)/1e6*0.15 + u.get("candidatesTokenCount",0)/1e6*0.60,
            "output": d["candidates"][0]["content"]["parts"][0]["text"]}


# ============================================================
# PHASE 3: Generate Next.js page code (Claude - heavy lifting)
# ============================================================

async def phase3_claude_code(context):
    """Claude: Generate the actual Next.js page.tsx"""
    t0 = time.time()
    r = httpx.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.getenv("ANTHROPIC_API_KEY",""), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 4000,
              "messages": [{"role": "user", "content": f"Gere o codigo COMPLETO de uma pagina Next.js (page.tsx) para o geo-orchestrator.\n\nCONTEUDO:\n{context['content'][:3000]}\n\nDESIGN:\n{context['design'][:1500]}\n\nREQUISITOS:\n- export const metadata com SEO completo (title, description, openGraph, twitter)\n- Secoes: Hero, Como Funciona (3 steps), 5 LLMs (cards), Funcionalidades (grid), Metricas (KPIs), Stack, Como Usar (code blocks), CTA final\n- Usar Tailwind classes + inline styles quando necessario\n- Design Salesforce/Lucida: accent #0176d3, dark #032d60, success #2e844a\n- Dados hardcoded (nao precisa de API)\n- Responsivo\n- JSON-LD SchemaOrg (SoftwareApplication)\n- Gere APENAS o codigo TSX, sem explicacao.\n\nO arquivo sera salvo em src/app/geo-orchestrator/page.tsx no landing-page-geo."}]},
        timeout=180)
    d = r.json(); u = d.get("usage", {})
    return {"agent": "Claude", "task": "code", "time": round(time.time()-t0,1),
            "tokens_in": u.get("input_tokens",0), "tokens_out": u.get("output_tokens",0),
            "cost": u.get("input_tokens",0)/1e6*0.80 + u.get("output_tokens",0)/1e6*4.00,
            "output": d["content"][0]["text"]}


async def phase3_groq_review(code):
    """Groq: Quick review of generated code"""
    t0 = time.time()
    r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY','')}", "Content-Type": "application/json"},
        json={"model": "llama-3.3-70b-versatile", "max_tokens": 800,
              "messages": [{"role": "user", "content": f"Revise este codigo Next.js rapidamente. Liste apenas PROBLEMAS encontrados (se houver). Se estiver OK, diga 'APROVADO'. Max 200 palavras.\n\n{code[:3000]}"}]},
        timeout=30)
    d = r.json(); u = d.get("usage", {})
    return {"agent": "Groq", "task": "review", "time": round(time.time()-t0,1),
            "tokens_in": u.get("prompt_tokens",0), "tokens_out": u.get("completion_tokens",0),
            "cost": u.get("prompt_tokens",0)/1e6*0.59 + u.get("completion_tokens",0)/1e6*0.79,
            "output": d["choices"][0]["message"]["content"]}


async def main():
    all_results = []
    total_cost = 0.0
    t_start = time.time()

    print("=" * 70)
    print("RODADA 4: AUDITORIA + PAGINA WEB — 3 FASES, 5 LLMs")
    print("=" * 70)

    # PHASE 1
    print("\n--- FASE 1: Pesquisa + Auditoria + Analise (3 LLMs paralelo) ---\n")
    t0 = time.time()
    p1_results = await asyncio.gather(
        phase1_perplexity_research(),
        phase1_claude_audit(),
        phase1_groq_analysis(),
    )
    p1_time = round(time.time()-t0, 1)
    p1_cost = sum(r["cost"] for r in p1_results)
    total_cost += p1_cost
    all_results.extend(p1_results)

    for r in p1_results:
        s = "OK" if "ERRO" not in r["output"] else "XX"
        print(f"  [{s}] {r['agent']:<12} {r['task']:<12} {r['time']:>5}s  {r['tokens_out']:>5} tok  ${r['cost']:.4f}")
    print(f"  Fase 1: {p1_time}s, ${p1_cost:.4f}")

    context = {
        "research": next(r["output"] for r in p1_results if r["task"] == "research"),
        "audit": next(r["output"] for r in p1_results if r["task"] == "audit"),
        "structure": next(r["output"] for r in p1_results if r["task"] == "structure"),
    }

    # PHASE 2
    print("\n--- FASE 2: Conteudo + Design (2 LLMs paralelo) ---\n")
    t0 = time.time()
    p2_results = await asyncio.gather(
        phase2_gpt4o_content(context),
        phase2_gemini_design(context),
    )
    p2_time = round(time.time()-t0, 1)
    p2_cost = sum(r["cost"] for r in p2_results)
    total_cost += p2_cost
    all_results.extend(p2_results)

    for r in p2_results:
        s = "OK" if "ERRO" not in r["output"] else "XX"
        print(f"  [{s}] {r['agent']:<12} {r['task']:<12} {r['time']:>5}s  {r['tokens_out']:>5} tok  ${r['cost']:.4f}")
    print(f"  Fase 2: {p2_time}s, ${p2_cost:.4f}")

    context["content"] = next(r["output"] for r in p2_results if r["task"] == "content")
    context["design"] = next(r["output"] for r in p2_results if r["task"] == "design")

    # PHASE 3
    print("\n--- FASE 3: Geracao de codigo + Revisao (2 LLMs) ---\n")
    t0 = time.time()
    code_result = await phase3_claude_code(context)
    all_results.append(code_result)
    print(f"  [OK] {code_result['agent']:<12} {code_result['task']:<12} {code_result['time']:>5}s  {code_result['tokens_out']:>5} tok  ${code_result['cost']:.4f}")

    review_result = await phase3_groq_review(code_result["output"])
    all_results.append(review_result)
    print(f"  [OK] {review_result['agent']:<12} {review_result['task']:<12} {review_result['time']:>5}s  {review_result['tokens_out']:>5} tok  ${review_result['cost']:.4f}")

    p3_time = round(time.time()-t0, 1)
    p3_cost = code_result["cost"] + review_result["cost"]
    total_cost += p3_cost
    print(f"  Fase 3: {p3_time}s, ${p3_cost:.4f}")

    # FINAL SUMMARY
    total_time = round(time.time()-t_start, 1)
    total_in = sum(r["tokens_in"] for r in all_results)
    total_out = sum(r["tokens_out"] for r in all_results)

    print()
    print("=" * 70)
    print("RESUMO FINAL")
    print("=" * 70)
    print(f"  Fases: 3 ({len(all_results)} chamadas LLM)")
    print(f"  Tempo total: {total_time}s")
    print(f"  Tokens: {total_in} in, {total_out} out")
    print(f"  Custo total: ${total_cost:.4f}")
    print(f"  LLMs usados: Claude, GPT-4o, Gemini, Perplexity, Groq")
    print()

    # Save code output
    code_output = code_result["output"]
    # Extract TSX from markdown code blocks if present
    if "```tsx" in code_output:
        code_output = code_output.split("```tsx")[1].split("```")[0]
    elif "```typescript" in code_output:
        code_output = code_output.split("```typescript")[1].split("```")[0]
    elif "```" in code_output:
        parts = code_output.split("```")
        if len(parts) >= 3:
            code_output = parts[1]
            if code_output.startswith("tsx\n") or code_output.startswith("typescript\n"):
                code_output = code_output.split("\n", 1)[1]

    code_path = OUTPUT / "geo_orchestrator_page.tsx"
    code_path.write_text(code_output.strip(), encoding="utf-8")
    print(f"Codigo da pagina salvo em: {code_path}")
    print(f"  {len(code_output.strip().splitlines())} linhas")
    print()
    print(f"Review do Groq: {review_result['output'][:200]}")

    # Save full report
    report = {
        "round": 4,
        "purpose": "Audit geo-orchestrator + generate showcase page",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "phases": [
            {"name": "Research+Audit+Analysis", "time_s": p1_time, "cost_usd": p1_cost, "llms": ["Perplexity", "Claude", "Groq"]},
            {"name": "Content+Design", "time_s": p2_time, "cost_usd": p2_cost, "llms": ["GPT-4o", "Gemini"]},
            {"name": "Code+Review", "time_s": p3_time, "cost_usd": p3_cost, "llms": ["Claude", "Groq"]},
        ],
        "totals": {"time_s": total_time, "cost_usd": total_cost, "tokens_in": total_in, "tokens_out": total_out, "llm_calls": len(all_results)},
        "results": all_results,
    }
    report_path = OUTPUT / "round4_audit_and_page.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Relatorio completo salvo em: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())

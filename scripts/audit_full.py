"""Auditoria completa: agrega todos os execution_*.json por modelo, dia, status."""
import json, glob, os
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
files = sorted(glob.glob(os.path.join(ROOT, "output", "execution_*.json")))

by_model = defaultdict(lambda: {"cost": 0.0, "tokens_in": 0, "tokens_out": 0, "calls": 0, "fails": 0})
by_day = defaultdict(float)
by_day_model = defaultdict(lambda: defaultdict(float))
by_provider = defaultdict(lambda: {"cost": 0.0, "calls": 0, "fails": 0})
by_task_type = defaultdict(lambda: {"cost": 0.0, "calls": 0})
total_cost = 0.0
total_calls = 0
total_fails = 0
exec_count = 0

# Map model -> provider
def provider_of(m):
    if not m: return "unknown"
    m = m.lower()
    if "claude" in m: return "anthropic"
    if "gpt" in m or m.startswith("o1") or m.startswith("o3"): return "openai"
    if "gemini" in m or "gemma" in m: return "google"
    if "sonar" in m or "perplex" in m: return "perplexity"
    if "llama" in m or "mixtral" in m or "groq" in m or "kimi" in m: return "groq"
    return "unknown"

for fp in files:
    try:
        d = json.load(open(fp, encoding="utf-8"))
    except: continue
    exec_count += 1
    ts = d.get("timestamp", "")[:10]
    for r in d.get("results", []):
        m = r.get("model_used") or "unknown"
        cost = float(r.get("cost_usd") or 0)
        ti = int(r.get("tokens_input") or 0)
        to = int(r.get("tokens_output") or 0)
        ok = r.get("success")
        prov = provider_of(m)
        by_model[m]["cost"] += cost
        by_model[m]["tokens_in"] += ti
        by_model[m]["tokens_out"] += to
        by_model[m]["calls"] += 1
        if not ok: by_model[m]["fails"] += 1
        by_day[ts] += cost
        by_day_model[ts][m] += cost
        by_provider[prov]["cost"] += cost
        by_provider[prov]["calls"] += 1
        if not ok: by_provider[prov]["fails"] += 1
        tt = r.get("task_type") or "unknown"
        by_task_type[tt]["cost"] += cost
        by_task_type[tt]["calls"] += 1
        total_cost += cost
        total_calls += 1
        if not ok: total_fails += 1

print(f"=== AUDITORIA GEO-ORCHESTRATOR ===")
print(f"Execucoes: {exec_count}  |  Calls: {total_calls}  |  Falhas: {total_fails} ({100*total_fails/max(total_calls,1):.1f}%)")
print(f"Custo total: ${total_cost:.2f}\n")

print("--- POR PROVIDER ---")
print(f"{'Provider':<14} {'Custo USD':>12} {'%':>7} {'Calls':>8} {'Fails':>7}")
for p, v in sorted(by_provider.items(), key=lambda x: -x[1]["cost"]):
    pct = 100 * v["cost"] / total_cost if total_cost else 0
    print(f"{p:<14} {v['cost']:>12.2f} {pct:>6.1f}% {v['calls']:>8} {v['fails']:>7}")

print("\n--- POR MODELO ---")
print(f"{'Modelo':<32} {'Custo USD':>12} {'%':>7} {'Calls':>8} {'Fails':>7} {'Tok IN':>10} {'Tok OUT':>10}")
for m, v in sorted(by_model.items(), key=lambda x: -x[1]["cost"]):
    pct = 100 * v["cost"] / total_cost if total_cost else 0
    print(f"{m[:32]:<32} {v['cost']:>12.2f} {pct:>6.1f}% {v['calls']:>8} {v['fails']:>7} {v['tokens_in']:>10} {v['tokens_out']:>10}")

print("\n--- POR DIA ---")
for d in sorted(by_day):
    print(f"  {d}: ${by_day[d]:>8.2f}")

print("\n--- POR TIPO DE TAREFA ---")
for t, v in sorted(by_task_type.items(), key=lambda x: -x[1]["cost"]):
    pct = 100 * v["cost"] / total_cost if total_cost else 0
    print(f"  {t:<20} ${v['cost']:>8.2f} ({pct:>5.1f}%)  calls={v['calls']}")

# Cross-check vs cost_history.jsonl
ch_total = 0
ch_n = 0
for line in open(os.path.join(ROOT, "output", "cost_history.jsonl")):
    ch_total += json.loads(line)["cost_usd"]
    ch_n += 1
print(f"\n--- CROSS-CHECK ---")
print(f"cost_history.jsonl:   ${ch_total:.2f}  ({ch_n} entradas)")
print(f"sum execution_*.json: ${total_cost:.2f}  ({exec_count} arquivos)")
print(f"diferenca:            ${ch_total - total_cost:.2f}")

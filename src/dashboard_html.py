"""Dashboard HTML estatico (sprint 7 — 2026-04-08).

Gera um arquivo HTML auto-contido a partir de `.kpi_history.jsonl` com:

- Timeseries de cost_estimate_accuracy (com banda saudavel 0.7-1.5)
- Timeseries de distribution_health
- Timeseries de parallelism_efficiency e custo real por run
- Bar chart de uso por LLM (5 canonicos)
- Tabela dos ultimos N runs

Chart.js via CDN, dados embedados como JSON inline. Zero deps Python
adicionais. Output deployable em qualquer servidor estatico (ex.:
alexandrecaramaschi.com/geo-orchestrator/dashboard.html).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .kpi_history import load_recent_entries

CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.0"


def _aggregate_llm_usage(entries: list[dict]) -> dict[str, int]:
    """Soma llm_usage acumulado dos N runs em cada LLM canonico."""
    out = {"claude": 0, "gpt4o": 0, "gemini": 0, "perplexity": 0, "groq": 0}
    for e in entries:
        usage = e.get("llm_usage", {}) or {}
        for k, v in usage.items():
            # Consolida tier interno Claude no slot canonico
            bucket = "claude" if k in ("claude_sonnet", "claude_haiku") else k
            if bucket in out:
                out[bucket] += int(v or 0)
    return out


def render_dashboard_html(
    entries: list[dict] | None = None,
    output_path: Path | None = None,
    title: str = "geo-orchestrator dashboard",
    n_recent: int = 30,
) -> str:
    """Renderiza o dashboard HTML e (opcional) salva em disco.

    Returns:
        O HTML como string.
    """
    if entries is None:
        entries = load_recent_entries(n=n_recent)

    # Series temporais
    labels = [e.get("timestamp", "")[:19] for e in entries]
    accuracy = [e.get("cost_estimate_accuracy", 0) for e in entries]
    health = [e.get("distribution_health", 0) for e in entries]
    parallelism = [e.get("parallelism_efficiency", 0) for e in entries]
    real_cost = [e.get("real_cost_usd", 0) for e in entries]
    estimated_cost = [e.get("estimated_cost_usd", 0) for e in entries]

    # KPIs Sprint 5
    qj_pass = [
        (e.get("quality_judge_pass") if e.get("quality_judge_pass") is not None else None)
        for e in entries
    ]

    # Uso por LLM agregado
    llm_usage = _aggregate_llm_usage(entries)

    # Stats agregadas
    total_runs = len(entries)
    total_cost = sum(real_cost)
    avg_accuracy = sum(a for a in accuracy if a) / max(1, sum(1 for a in accuracy if a))
    avg_health = sum(health) / max(1, len(health))

    qj_known = [v for v in qj_pass if v is not None]
    qj_pass_rate = (sum(qj_known) / len(qj_known) * 100) if qj_known else None

    data_blob = {
        "labels": labels,
        "accuracy": accuracy,
        "health": health,
        "parallelism": parallelism,
        "real_cost": real_cost,
        "estimated_cost": estimated_cost,
        "qj_pass": [v if v is not None else None for v in qj_pass],
        "llm_usage": llm_usage,
    }

    summary_html = f"""
        <div class="kpi"><span class="label">Runs</span><span class="value">{total_runs}</span></div>
        <div class="kpi"><span class="label">Custo total</span><span class="value">US$ {total_cost:.4f}</span></div>
        <div class="kpi"><span class="label">Accuracy media</span><span class="value">{avg_accuracy:.2f}x</span></div>
        <div class="kpi"><span class="label">Health medio</span><span class="value">{avg_health:.2f}</span></div>
        <div class="kpi"><span class="label">Quality Judge pass rate</span><span class="value">{(f'{qj_pass_rate:.0f}%' if qj_pass_rate is not None else '—')}</span></div>
    """

    # Tabela dos ultimos 10 runs
    rows_html = []
    for e in entries[-10:][::-1]:
        rows_html.append(f"""
        <tr>
            <td>{e.get('timestamp', '')[:19]}</td>
            <td>{(e.get('demand', '') or '')[:60]}</td>
            <td class="num">{e.get('distribution_health', 0):.2f}</td>
            <td class="num">{e.get('cost_estimate_accuracy', 0):.2f}x</td>
            <td class="num">{e.get('parallelism_efficiency', 0):.1f}x</td>
            <td class="num">${e.get('real_cost_usd', 0):.4f}</td>
            <td class="num">{e.get('tasks_completed', 0)}</td>
        </tr>""")
    table_html = "".join(rows_html) or "<tr><td colspan='7'>nenhum run ainda</td></tr>"

    generated_at = datetime.now().isoformat()

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="{CHART_JS_CDN}"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    background: #0f1419;
    color: #e6edf3;
    padding: 24px;
    line-height: 1.5;
}}
header {{
    border-bottom: 1px solid #30363d;
    padding-bottom: 16px;
    margin-bottom: 24px;
}}
h1 {{ font-size: 24px; font-weight: 600; color: #58a6ff; }}
header p {{ color: #8b949e; font-size: 13px; margin-top: 4px; }}
.kpis {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin-bottom: 32px;
}}
.kpi {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 16px;
    display: flex;
    flex-direction: column;
}}
.kpi .label {{ font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }}
.kpi .value {{ font-size: 22px; font-weight: 600; color: #e6edf3; margin-top: 4px; }}
.charts {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 24px;
    margin-bottom: 32px;
}}
.chart-card {{
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 20px;
}}
.chart-card h3 {{
    font-size: 14px;
    color: #8b949e;
    margin-bottom: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
.chart-wrap {{ position: relative; height: 240px; }}
table {{
    width: 100%;
    border-collapse: collapse;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    overflow: hidden;
    font-size: 13px;
}}
th {{
    background: #21262d;
    color: #8b949e;
    text-align: left;
    padding: 10px 12px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    font-size: 11px;
}}
td {{ padding: 10px 12px; border-top: 1px solid #21262d; }}
td.num {{ font-variant-numeric: tabular-nums; text-align: right; }}
footer {{
    margin-top: 32px;
    padding-top: 16px;
    border-top: 1px solid #30363d;
    color: #8b949e;
    font-size: 12px;
    text-align: center;
}}
@media (max-width: 768px) {{
    .charts {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<header>
    <h1>geo-orchestrator dashboard</h1>
    <p>Multi-LLM orchestration · Brasil GEO · gerado em {generated_at}</p>
</header>

<section class="kpis">
{summary_html}
</section>

<section class="charts">
    <div class="chart-card">
        <h3>Cost Estimate Accuracy (banda saudavel 0.7-1.5x)</h3>
        <div class="chart-wrap"><canvas id="chartAccuracy"></canvas></div>
    </div>
    <div class="chart-card">
        <h3>Distribution Health (alvo &gt;= 0.95)</h3>
        <div class="chart-wrap"><canvas id="chartHealth"></canvas></div>
    </div>
    <div class="chart-card">
        <h3>Parallelism Efficiency (speedup vs sequencial)</h3>
        <div class="chart-wrap"><canvas id="chartParallelism"></canvas></div>
    </div>
    <div class="chart-card">
        <h3>Custo por run (estimado vs real)</h3>
        <div class="chart-wrap"><canvas id="chartCost"></canvas></div>
    </div>
    <div class="chart-card" style="grid-column: 1 / -1;">
        <h3>Uso acumulado por LLM (5 canonicos)</h3>
        <div class="chart-wrap"><canvas id="chartLLMs"></canvas></div>
    </div>
</section>

<h3 style="font-size: 14px; color: #8b949e; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Ultimos 10 runs</h3>
<table>
    <thead>
        <tr>
            <th>Timestamp</th><th>Demanda</th><th>Health</th>
            <th>Accuracy</th><th>Parallelism</th><th>Custo</th><th>Tasks</th>
        </tr>
    </thead>
    <tbody>{table_html}</tbody>
</table>

<footer>
    geo-orchestrator v2.0 · {total_runs} runs analisados ·
    <a href="https://github.com/alexandrebrt14-sys/geo-orchestrator" style="color: #58a6ff;">github</a>
</footer>

<script>
const D = {json.dumps(data_blob, ensure_ascii=False)};
const PALETTE = ['#58a6ff', '#3fb950', '#d29922', '#f85149', '#a371f7'];
const COMMON = {{
    plugins: {{ legend: {{ labels: {{ color: '#8b949e' }} }} }},
    scales: {{
        x: {{ ticks: {{ color: '#8b949e', maxTicksLimit: 8 }}, grid: {{ color: '#21262d' }} }},
        y: {{ ticks: {{ color: '#8b949e' }}, grid: {{ color: '#21262d' }} }}
    }},
    maintainAspectRatio: false,
}};

new Chart(document.getElementById('chartAccuracy'), {{
    type: 'line',
    data: {{
        labels: D.labels,
        datasets: [{{
            label: 'cost_estimate_accuracy',
            data: D.accuracy, borderColor: PALETTE[0], backgroundColor: PALETTE[0] + '20',
            tension: 0.3, fill: true,
        }}]
    }},
    options: COMMON,
}});

new Chart(document.getElementById('chartHealth'), {{
    type: 'line',
    data: {{
        labels: D.labels,
        datasets: [{{
            label: 'distribution_health',
            data: D.health, borderColor: PALETTE[1], backgroundColor: PALETTE[1] + '20',
            tension: 0.3, fill: true,
        }}]
    }},
    options: {{...COMMON, scales: {{...COMMON.scales, y: {{...COMMON.scales.y, min: 0, max: 1}} }} }},
}});

new Chart(document.getElementById('chartParallelism'), {{
    type: 'line',
    data: {{
        labels: D.labels,
        datasets: [{{
            label: 'parallelism_efficiency (speedup)',
            data: D.parallelism, borderColor: PALETTE[4], backgroundColor: PALETTE[4] + '20',
            tension: 0.3, fill: true,
        }}]
    }},
    options: COMMON,
}});

new Chart(document.getElementById('chartCost'), {{
    type: 'line',
    data: {{
        labels: D.labels,
        datasets: [
            {{ label: 'estimado', data: D.estimated_cost, borderColor: PALETTE[2], borderDash: [5,5], tension: 0.3 }},
            {{ label: 'real', data: D.real_cost, borderColor: PALETTE[3], backgroundColor: PALETTE[3] + '20', tension: 0.3, fill: true }},
        ]
    }},
    options: COMMON,
}});

new Chart(document.getElementById('chartLLMs'), {{
    type: 'bar',
    data: {{
        labels: Object.keys(D.llm_usage),
        datasets: [{{
            label: 'tasks',
            data: Object.values(D.llm_usage),
            backgroundColor: PALETTE,
        }}]
    }},
    options: {{...COMMON, plugins: {{ legend: {{ display: false }} }} }},
}});
</script>
</body>
</html>
"""

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")

    return html

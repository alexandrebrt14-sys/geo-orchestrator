"""
CLI do geo-orchestrator — orquestrador multi-LLM da Brasil GEO.

Uso:
    python cli.py run "sua demanda aqui"
    python cli.py run "demanda" --dry-run
    python cli.py run "demanda" --verbose
    python cli.py run "demanda" --output-dir ./output
    python cli.py plan "demanda"
    python cli.py status
    python cli.py cost-report
    python cli.py models
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from src.finops import FinOps, get_finops
from src.orchestrator import Orchestrator
from src.pipeline import Pipeline
from src.router import Router
from src.smart_router import SmartRouter
from src.models import ExecutionReport, Plan, TaskResult
from src.config import LLM_CONFIGS

load_dotenv()

# Forçar UTF-8 no Windows para suportar caracteres Unicode no Rich
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

console = Console(force_terminal=True)

# ---------------------------------------------------------------------------
# Constantes (modelos e tier routing vivem em src/config.py — LLM_CONFIGS)
# ---------------------------------------------------------------------------
COST_LOG_PATH = Path("output/cost_history.jsonl")
REPORT_DIR = Path("output")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check_api_key(env_key: str) -> bool:
    return bool(os.getenv(env_key))


# Mapa LLM canonico (LLM_CONFIGS) -> rotulo de provider para narrativa.
_LLM_PROVIDER_LABEL: dict[str, str] = {
    "claude":     "Anthropic/Claude",
    "gpt4o":      "OpenAI/GPT-4o",
    "gemini":     "Google/Gemini",
    "perplexity": "Perplexity/Sonar",
    "groq":       "Groq/Llama",
}

_LLM_COLOR: dict[str, str] = {
    "claude":     "blue",
    "gpt4o":      "green",
    "gemini":     "yellow",
    "perplexity": "magenta",
    "groq":       "cyan",
}


def _compute_waves_from_plan(plan: Plan) -> list[list]:
    """Calcula waves de execucao via dependencias topologicas (mesmo algoritmo do Pipeline)."""
    task_map = {t.id: t for t in plan.tasks}
    completed: set[str] = set()
    waves: list[list] = []
    remaining = set(task_map.keys())
    while remaining:
        wave = [
            task_map[tid]
            for tid in remaining
            if all(dep in completed for dep in task_map[tid].dependencies)
        ]
        if not wave:
            wave = [task_map[tid] for tid in remaining]
            waves.append(wave)
            break
        waves.append(wave)
        for t in wave:
            completed.add(t.id)
            remaining.discard(t.id)
    return waves


def _display_plan(plan: Plan) -> None:
    """Exibe o plano de execucao (Plan v2.0) em forma de arvore por waves."""
    console.print()
    console.print(Panel(
        f"[bold]{plan.demand[:200]}[/bold]\n\n"
        f"Tarefas: {len(plan.tasks)}  |  "
        f"Custo estimado (decomposicao): US$ {plan.total_estimated_cost:.4f}",
        title="Plano de Execucao (v2.0)",
        border_style="cyan",
    ))

    waves = _compute_waves_from_plan(plan)
    tree = Tree("[bold cyan]Pipeline[/bold cyan]")
    for idx, wave in enumerate(waves, start=1):
        wave_branch = tree.add(
            f"[bold yellow]Wave {idx}[/bold yellow] — {len(wave)} tarefa(s) paralelas"
        )
        for task in wave:
            dep_str = f" (depende de: {', '.join(task.dependencies)})" if task.dependencies else ""
            wave_branch.add(
                f"[green]{task.id}[/green] [{task.type}] "
                f"{task.description[:80]}{'...' if len(task.description) > 80 else ''}"
                f"{dep_str} "
                f"(complexidade: {task.complexity.value})"
            )
    console.print(tree)
    console.print()


def _display_summary(report: ExecutionReport) -> None:
    """Exibe resumo da ExecutionReport (v2.0): tabela de tarefas + uso por LLM + cobertura."""
    plan_task_types = {t.id: t.type for t in report.plan.tasks}
    results_list = list(report.results.values())

    table = Table(title="Resumo da Execucao")
    table.add_column("Tarefa", style="cyan")
    table.add_column("Tipo", style="dim")
    table.add_column("LLM", style="green")
    table.add_column("Status")
    table.add_column("Tempo (s)", justify="right")
    table.add_column("Tokens In", justify="right")
    table.add_column("Tokens Out", justify="right")
    table.add_column("Custo (US$)", justify="right")

    total_cost = 0.0
    total_time_ms = 0
    total_in = 0
    total_out = 0

    for r in results_list:
        if r.success:
            status = "[green]OK[/green]" if not r.cache_hit else "[cyan]CACHE[/cyan]"
        else:
            status = "[red]FALHA[/red]"
        table.add_row(
            r.task_id,
            plan_task_types.get(r.task_id, "?"),
            r.llm_used,
            status,
            f"{(r.duration_ms or 0) / 1000:.1f}",
            str(r.tokens_input),
            str(r.tokens_output),
            f"{r.cost:.4f}",
        )
        total_cost += r.cost
        total_time_ms += r.duration_ms or 0
        total_in += r.tokens_input
        total_out += r.tokens_output

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]", "", "", "",
        f"[bold]{total_time_ms / 1000:.1f}[/bold]",
        f"[bold]{total_in}[/bold]",
        f"[bold]{total_out}[/bold]",
        f"[bold]{total_cost:.4f}[/bold]",
    )
    console.print()
    console.print(table)

    # Tabela: Uso por LLM (5 canonicos). Sprint 3: tier interno Claude
    # (claude_sonnet, claude_haiku) consolidado no slot 'claude' para
    # mostrar familia Anthropic unificada.
    llm_stats: dict[str, dict] = {
        name: {"tasks": 0, "ok": 0, "fail": 0, "cost": 0.0, "tokens": 0, "time_ms": 0}
        for name in ["claude", "gpt4o", "gemini", "perplexity", "groq"]
    }
    # Mapa para consolidar tier interno Claude no slot canonico.
    canonical_alias = {
        "claude_sonnet": "claude",
        "claude_haiku": "claude",
    }
    for r in results_list:
        raw = r.llm_used
        bucket = canonical_alias.get(raw, raw)
        if bucket not in llm_stats:
            continue  # 'semantic_cache', 'code_executor' nao contam
        llm_stats[bucket]["tasks"] += 1
        llm_stats[bucket]["ok"] += 1 if r.success else 0
        llm_stats[bucket]["fail"] += 0 if r.success else 1
        llm_stats[bucket]["cost"] += r.cost
        llm_stats[bucket]["tokens"] += r.tokens_input + r.tokens_output
        llm_stats[bucket]["time_ms"] += r.duration_ms or 0

    usage_table = Table(title="Uso por LLM (5 canonicos)", show_lines=False)
    usage_table.add_column("Provider", style="bold")
    usage_table.add_column("Tarefas", justify="center")
    usage_table.add_column("OK", justify="center", style="green")
    usage_table.add_column("Falha", justify="center", style="red")
    usage_table.add_column("Share %", justify="right")
    usage_table.add_column("Tokens", justify="right")
    usage_table.add_column("Custo (US$)", justify="right")
    usage_table.add_column("Tempo (s)", justify="right")
    usage_table.add_column("Cobertura", justify="center")

    used_count = 0
    total_tasks_routed = sum(s["tasks"] for s in llm_stats.values()) or 1
    for name in ["claude", "gpt4o", "gemini", "perplexity", "groq"]:
        s = llm_stats[name]
        share = (s["tasks"] / total_tasks_routed) * 100
        if s["tasks"] > 0:
            used_count += 1
            coverage = "[green]USADO[/green]"
        else:
            coverage = "[red]NAO USADO[/red]"
        # Marca cap se share > 80% (regra v2.0)
        share_str = f"{share:.0f}%"
        if share > 80:
            share_str = f"[red]{share:.0f}%[/red]"
        elif share > 50:
            share_str = f"[yellow]{share:.0f}%[/yellow]"
        usage_table.add_row(
            _LLM_PROVIDER_LABEL[name],
            str(s["tasks"]),
            str(s["ok"]),
            str(s["fail"]),
            share_str,
            f"{s['tokens']:,}",
            f"{s['cost']:.4f}",
            f"{s['time_ms'] / 1000:.1f}",
            coverage,
        )
    console.print()
    console.print(usage_table)
    coverage_pct = (used_count / 5) * 100
    color = "green" if used_count == 5 else "yellow" if used_count >= 3 else "red"
    console.print(f"\n[{color}]Cobertura de LLMs: {used_count}/5 ({coverage_pct:.0f}%)[/{color}]")

    # Resumo v2.0: cache, dedup, quality retries
    extras = Table(title="Indicadores v2.0", show_lines=False, box=None)
    extras.add_column("Indicador", style="bold")
    extras.add_column("Valor", justify="right")
    extras.add_row("Tarefas concluidas", f"{report.tasks_completed}")
    extras.add_row("Tarefas falhas", f"{report.tasks_failed}")
    extras.add_row("Tarefas em cache", f"{report.tasks_cached}")
    extras.add_row("Tarefas deduplicadas", f"{report.tasks_deduplicated}")
    extras.add_row("Retentativas por qualidade", f"{report.tasks_quality_retried}")
    extras.add_row("Custo estimado", f"US$ {report.estimated_cost:.4f}")
    extras.add_row("Custo real", f"US$ {report.total_cost:.4f}")
    extras.add_row("Tempo total", f"{report.total_duration_ms / 1000:.1f}s")
    extras.add_row("Limite de orcamento", f"US$ {report.budget_limit:.2f}")
    console.print()
    console.print(extras)


def _save_report(report: ExecutionReport, output_dir: Path) -> Path:
    """Salva ExecutionReport em output/execution_<ts>.json + append em cost_history.jsonl."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"execution_{timestamp}.json"

    payload = {
        "timestamp": datetime.now().isoformat(),
        "demand": report.demand,
        "summary": report.summary,
        "totals": {
            "cost_usd": report.total_cost,
            "estimated_cost_usd": report.estimated_cost,
            "duration_ms": report.total_duration_ms,
            "tasks_completed": report.tasks_completed,
            "tasks_failed": report.tasks_failed,
            "tasks_cached": report.tasks_cached,
            "tasks_deduplicated": report.tasks_deduplicated,
            "tasks_quality_retried": report.tasks_quality_retried,
            "budget_limit": report.budget_limit,
        },
        "plan": report.plan.model_dump(mode="json"),
        "results": {tid: r.model_dump(mode="json") for tid, r in report.results.items()},
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    console.print(f"\nRelatorio salvo em: [cyan]{report_path}[/cyan]")

    # Append ao log de custos consolidado
    COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(COST_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "cost_usd": report.total_cost,
            "tasks": report.tasks_completed + report.tasks_failed,
            "tokens": sum(r.tokens_input + r.tokens_output for r in report.results.values()),
        }) + "\n")

    return report_path


# ---------------------------------------------------------------------------
# Comandos CLI
# ---------------------------------------------------------------------------
@click.group()
def cli():
    """geo-orchestrator — Orquestrador multi-LLM da Brasil GEO."""
    pass


@cli.command()
@click.argument("demand")
@click.option("--dry-run", is_flag=True, help="Mostra o plano sem executar.")
@click.option("--verbose", is_flag=True, help="Mostra cada passo durante a execucao.")
@click.option("--output-dir", type=click.Path(), default="output", help="Diretorio para salvar resultados.")
@click.option("--force", is_flag=True, help="Ignora o budget guard (BUDGET_LIMIT).")
@click.option("--no-smart", is_flag=True, help="Desliga o SmartRouter (debug; usa Router classico).")
@click.option("--force-5-llm", "force_5_llm", is_flag=True, help="Forca uso dos 5 LLMs canonicos antes de fallback (QA/demos).")
def run(demand: str, dry_run: bool, verbose: bool, output_dir: str, force: bool, no_smart: bool, force_5_llm: bool):
    """Executa o pipeline v2.0 completo para uma demanda.

    Caminho v2.0 (Orchestrator): SmartRouter + cap 80% + quality gates +
    semantic cache + code-first gate + checkpoint + fallback chain estruturada
    + FinOps budget check por tarefa.
    """
    console.print(Panel(
        f"[bold]Demanda:[/bold] {demand}",
        title="geo-orchestrator v2.0",
        border_style="blue",
    ))

    # Banca canonica via LLM_CONFIGS (5 LLMs source of truth)
    banca = Table(title="Banca de Modelos (canonica)", show_lines=False, box=None)
    banca.add_column("LLM", style="bold")
    banca.add_column("Modelo", style="cyan")
    banca.add_column("Provider")
    banca.add_column("Papel")
    banca.add_column("Status")
    for name, cfg in LLM_CONFIGS.items():
        banca.add_row(
            name,
            cfg.model,
            cfg.provider.value,
            cfg.role[:60] + ("..." if len(cfg.role) > 60 else ""),
            "[green]ativo[/green]" if _check_api_key(cfg.api_key_env) else "[red]sem chave[/red]",
        )
    console.print(banca)
    console.print()

    if verbose:
        logging.getLogger().setLevel(logging.INFO)

    # Modo dry-run: usa apenas Orchestrator.decompose para mostrar o plano
    if dry_run:
        console.print("[bold cyan]Dry-run:[/bold cyan] decompondo demanda via Orchestrator...\n")
        orch = Orchestrator(force=force, smart=not no_smart, force_all_llms=force_5_llm)
        plan_obj = asyncio.run(orch.decompose(demand))
        _display_plan(plan_obj)

        # Salvar plano para inspecao
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        plan_path = REPORT_DIR / f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        plan_path.write_text(
            json.dumps(plan_obj.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        console.print(f"\nPlano salvo em: [cyan]{plan_path}[/cyan]")
        console.print("[yellow]Modo dry-run: execucao pulada.[/yellow]")
        return

    # Execucao completa via Orchestrator v2.0
    console.print("[bold cyan]Pipeline v2.0:[/bold cyan] PromptRefiner -> Decompose -> SmartRouter -> CodeFirst -> Cache -> Pipeline (cap 80% + quality + fallback)\n")
    if force_5_llm:
        console.print("[bold yellow]--force-5-llm ativo:[/bold yellow] router prioriza LLMs canonicos ainda nao usados\n")
    orch = Orchestrator(force=force, smart=not no_smart, force_all_llms=force_5_llm)
    try:
        report: ExecutionReport = asyncio.run(orch.run(demand))
    except Exception as exc:
        console.print(f"\n[red]Falha na execucao do orchestrator:[/red] {exc}")
        sys.exit(1)

    _display_summary(report)
    _save_report(report, Path(output_dir))

    if report.tasks_failed:
        console.print(f"\n[red]{report.tasks_failed} tarefa(s) falharam. Verifique o relatorio.[/red]")
        sys.exit(1)
    else:
        console.print("\n[green]Todas as tarefas concluidas com sucesso.[/green]")


@cli.command()
@click.argument("demand")
@click.option("--no-smart", is_flag=True, help="Desliga o SmartRouter (usa Router classico).")
def plan(demand: str, no_smart: bool):
    """Decompoe a demanda via Orchestrator v2.0 e mostra o plano sem executar."""
    console.print("[bold cyan]Decompondo demanda via Orchestrator v2.0...[/bold cyan]")
    orch = Orchestrator(smart=not no_smart)
    plan_obj = asyncio.run(orch.decompose(demand))
    _display_plan(plan_obj)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    plan_path = REPORT_DIR / f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    plan_path.write_text(
        json.dumps(plan_obj.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    console.print(f"Plano salvo em: [cyan]{plan_path}[/cyan]")


@cli.command()
@click.option(
    "--checkpoint",
    "-c",
    type=click.Path(exists=True, dir_okay=False),
    default="output/.checkpoint.json",
    show_default=True,
    help="Arquivo de checkpoint a retomar.",
)
@click.option("--no-smart", is_flag=True, help="Desliga o SmartRouter (usa Router classico).")
@click.option("--output-dir", type=click.Path(), default="output", help="Diretorio para salvar resultados.")
def resume(checkpoint: str, no_smart: bool, output_dir: str):
    """Retoma uma execucao interrompida a partir de um checkpoint.

    O checkpoint e gravado pelo Pipeline a cada wave em output/.checkpoint.json.
    Em caso de crash, timeout ou Ctrl-C, basta `python cli.py resume` para
    continuar do ultimo wave salvo sem reexecutar tarefas concluidas.
    """
    checkpoint_path = Path(checkpoint)
    console.print(Panel(
        f"[bold]Checkpoint:[/bold] {checkpoint_path}",
        title="geo-orchestrator resume",
        border_style="cyan",
    ))

    router = SmartRouter() if not no_smart else Router()
    try:
        results = asyncio.run(Pipeline.resume(checkpoint_path, router))
    except FileNotFoundError as exc:
        console.print(f"[red]Checkpoint nao encontrado:[/red] {exc}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[red]Falha ao retomar pipeline:[/red] {exc}")
        sys.exit(1)

    completed = sum(1 for r in results.values() if r.success)
    failed = sum(1 for r in results.values() if not r.success)
    total_cost = sum(r.cost for r in results.values())

    console.print()
    console.print(f"[green]Retomada concluida:[/green] {completed} ok, {failed} falhas, custo US$ {total_cost:.4f}")

    # Salvar payload simples (sem ExecutionReport completo)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    resume_path = out_dir / f"resume_{timestamp}.json"
    payload = {
        "timestamp": datetime.now().isoformat(),
        "checkpoint": str(checkpoint_path),
        "totals": {
            "cost_usd": total_cost,
            "tasks_completed": completed,
            "tasks_failed": failed,
        },
        "results": {tid: r.model_dump(mode="json") for tid, r in results.items()},
    }
    resume_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    console.print(f"Resultado salvo em: [cyan]{resume_path}[/cyan]")

    if failed:
        sys.exit(1)


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host.")
@click.option("--port", default=8080, show_default=True, type=int, help="Bind port.")
def serve(host: str, port: int):
    """Sprint 7 (2026-04-08): sobe servidor HTTP /health + /metrics.

    Endpoints:

    \b
    - GET /health   -> 200 (OK/ATENCAO), 503 (CRITICO). Mesmo dado do `cli doctor`.
    - GET /metrics  -> KPI timeseries dos ultimos 20 runs (.kpi_history.jsonl).
    - GET /         -> docs minimos.

    Stdlib http.server (zero deps adicionais). Para producao com volume,
    rodar atras de nginx ou trocar por uvicorn+fastapi mantendo o mesmo
    contrato JSON.
    """
    from src.health_server import run_server
    console.print(f"[cyan]geo-orchestrator health server em http://{host}:{port}[/cyan]")
    console.print("[dim]GET /health · GET /metrics · GET /[/dim]")
    console.print("[dim]Ctrl-C para encerrar[/dim]\n")
    run_server(host=host, port=port)


@cli.command()
@click.option("--strict", is_flag=True, help="Sai com codigo 1 se algum check estiver em ATENCAO ou CRITICO.")
@click.option("--json", "as_json", is_flag=True, help="Saida JSON estruturada (para CI/cron).")
def doctor(strict: bool, as_json: bool):
    """Sprint 6 (2026-04-08): health check abrangente do sistema.

    Verifica em uma unica chamada:

    \b
    - API keys dos 5 LLMs canonicos (presenca, nao conteudo)
    - Catalog YAML consistente com src/config.LLM_CONFIGS
    - FinOps daily limits saudaveis (< 80% por provider)
    - KPI history existe e tem entries recentes
    - Cost calibration aplicada (idade < 30 dias)
    - Drift detector verde

    Saida humana por default. `--json` para CI/cron. `--strict` faz exit 1
    em qualquer ATENCAO/CRITICO — adequado para Task Scheduler/CI gating.
    """
    checks: list[dict] = []

    # 1. API keys
    missing_keys = []
    for name, cfg in LLM_CONFIGS.items():
        if not _check_api_key(cfg.api_key_env):
            missing_keys.append(name)
    if not missing_keys:
        checks.append({"name": "api_keys", "status": "OK",
                       "detail": f"{len(LLM_CONFIGS)} LLMs configurados"})
    else:
        checks.append({"name": "api_keys", "status": "CRITICO",
                       "detail": f"chaves ausentes: {', '.join(missing_keys)}"})

    # 2. Catalog YAML consistency
    try:
        from src.catalog_loader import validate_catalog_vs_config
        errors = validate_catalog_vs_config()
        if not errors:
            checks.append({"name": "catalog_consistency", "status": "OK",
                           "detail": "catalog YAML alinhado com LLM_CONFIGS"})
        else:
            checks.append({"name": "catalog_consistency", "status": "ATENCAO",
                           "detail": f"{len(errors)} divergencias: {errors[0]}"})
    except Exception as exc:
        checks.append({"name": "catalog_consistency", "status": "ATENCAO",
                       "detail": f"validator falhou: {exc}"})

    # 3. FinOps daily limits
    try:
        fo = get_finops()
        status_data = fo.daily_status()
        max_pct = 0.0
        offender = None
        for provider, data in status_data.items():
            if provider.startswith("_"):
                continue
            pct = data.get("usage_pct", 0)
            if pct > max_pct:
                max_pct = pct
                offender = provider
        if max_pct < 80:
            checks.append({"name": "finops_daily", "status": "OK",
                           "detail": f"max {max_pct:.0f}% ({offender})"})
        elif max_pct < 95:
            checks.append({"name": "finops_daily", "status": "ATENCAO",
                           "detail": f"{offender} em {max_pct:.0f}% do limite"})
        else:
            checks.append({"name": "finops_daily", "status": "CRITICO",
                           "detail": f"{offender} bloqueado em {max_pct:.0f}%"})
    except Exception as exc:
        checks.append({"name": "finops_daily", "status": "ATENCAO",
                       "detail": f"FinOps inacessivel: {exc}"})

    # 4. KPI history freshness
    try:
        from src.kpi_history import KPI_HISTORY_PATH, load_recent_entries
        if not KPI_HISTORY_PATH.exists():
            checks.append({"name": "kpi_history", "status": "ATENCAO",
                           "detail": "nenhum historico — rode `cli.py run` ao menos 1x"})
        else:
            entries = load_recent_entries(n=5)
            if not entries:
                checks.append({"name": "kpi_history", "status": "ATENCAO",
                               "detail": "historico vazio"})
            else:
                last_ts = entries[-1].get("timestamp", "")
                checks.append({"name": "kpi_history", "status": "OK",
                               "detail": f"{len(entries)} entries recentes, ultima: {last_ts[:19]}"})
    except Exception as exc:
        checks.append({"name": "kpi_history", "status": "ATENCAO",
                       "detail": f"falha ao ler historico: {exc}"})

    # 5. Cost calibration freshness
    try:
        from src.cost_calibrator import load_calibration, CALIBRATION_PATH
        cal = load_calibration()
        if cal is None:
            checks.append({"name": "cost_calibration", "status": "ATENCAO",
                           "detail": "nenhuma calibracao — rode `cli.py finops calibrate`"})
        else:
            n_calibrated = len(cal.get("calibrated_avg_cost_per_call", {}) or {})
            last = cal.get("last_calibrated_at", "?")
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                from datetime import timezone as _tz
                age_days = (datetime.now(_tz.utc) - last_dt).days
            except Exception:
                age_days = 999
            if age_days <= 7:
                checks.append({"name": "cost_calibration", "status": "OK",
                               "detail": f"{n_calibrated} LLMs, recalibrado ha {age_days}d"})
            elif age_days <= 30:
                checks.append({"name": "cost_calibration", "status": "ATENCAO",
                               "detail": f"calibracao desatualizada ({age_days}d)"})
            else:
                checks.append({"name": "cost_calibration", "status": "CRITICO",
                               "detail": f"calibracao envelhecida ({age_days}d)"})
    except Exception as exc:
        checks.append({"name": "cost_calibration", "status": "ATENCAO",
                       "detail": f"calibracao inacessivel: {exc}"})

    # 6. Drift detector
    try:
        from src.kpi_history import detect_drift
        drift = detect_drift()
        if drift is None:
            checks.append({"name": "drift_detector", "status": "OK",
                           "detail": "cost_estimate_accuracy dentro da banda 0.7-1.5"})
        else:
            checks.append({"name": "drift_detector", "status": "ATENCAO",
                           "detail": f"drift {drift['direction']}, media {drift['average']:.2f}x"})
    except Exception as exc:
        checks.append({"name": "drift_detector", "status": "ATENCAO",
                       "detail": f"drift detector falhou: {exc}"})

    # Output
    has_critical = any(c["status"] == "CRITICO" for c in checks)
    has_warning = any(c["status"] == "ATENCAO" for c in checks)
    overall = "CRITICO" if has_critical else ("ATENCAO" if has_warning else "OK")

    if as_json:
        print(json.dumps({
            "overall": overall,
            "checks": checks,
            "timestamp": datetime.now().isoformat(),
        }, ensure_ascii=False, indent=2))
    else:
        table = Table(title="geo-orchestrator doctor")
        table.add_column("Check", style="cyan")
        table.add_column("Status")
        table.add_column("Detalhe", style="dim")
        for c in checks:
            color = {"OK": "green", "ATENCAO": "yellow", "CRITICO": "red"}.get(c["status"], "white")
            table.add_row(c["name"], f"[{color}]{c['status']}[/{color}]", c["detail"])
        console.print(table)
        overall_color = {"OK": "green", "ATENCAO": "yellow", "CRITICO": "red"}[overall]
        console.print(f"\nStatus geral: [bold {overall_color}]{overall}[/bold {overall_color}]")

    if strict and overall != "OK":
        sys.exit(1)


@cli.command()
@click.argument("execution_id")
@click.option("--output-dir", type=click.Path(), default="output", help="Diretorio onde estao os execution reports.")
@click.option("--show-results", is_flag=True, help="Imprime tambem o output (truncado) de cada tarefa.")
def replay(execution_id: str, output_dir: str, show_results: bool):
    """Sprint 5 (2026-04-08): re-renderiza um execution report historico.

    Aceita 3 formas de identificar o report:

    \b
    - Timestamp completo: `cli.py replay 20260408_103015`
      Le diretamente output/execution_20260408_103015.json
    - Caminho de arquivo: `cli.py replay path/to/execution_xxx.json`
    - 'last' (alias): `cli.py replay last` -> ultimo execution_*.json

    Nao re-executa LLMs, apenas le o JSON e renderiza o resumo no console
    (mesmo formato do `cli.py run`). Util para auditoria, demos e
    comparacao de runs sem custo adicional.
    """
    out_dir = Path(output_dir)
    candidate: Path | None = None
    if execution_id.lower() == "last":
        files = sorted(out_dir.glob("execution_*.json"))
        candidate = files[-1] if files else None
    else:
        p = Path(execution_id)
        if p.is_file():
            candidate = p
        else:
            # tenta como timestamp/id
            candidate = out_dir / f"execution_{execution_id}.json"
            if not candidate.exists():
                # match parcial pelo prefixo
                matches = sorted(out_dir.glob(f"execution_{execution_id}*.json"))
                candidate = matches[-1] if matches else None

    if candidate is None or not candidate.exists():
        console.print(f"[red]Execution report nao encontrado:[/red] {execution_id}")
        console.print(f"[dim]Procurado em: {out_dir}/execution_*.json[/dim]")
        sys.exit(1)

    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]Falha ao ler {candidate}:[/red] {exc}")
        sys.exit(1)

    console.print(Panel(
        f"[bold]{payload.get('demand', '?')[:200]}[/bold]\n\n"
        f"Arquivo: {candidate.name}\n"
        f"Timestamp: {payload.get('timestamp', '?')}",
        title=f"Replay — {candidate.stem}",
        border_style="cyan",
    ))

    totals = payload.get("totals", {}) or {}
    results_dict = payload.get("results", {}) or {}
    plan_dict = payload.get("plan", {}) or {}
    plan_tasks = plan_dict.get("tasks", []) if isinstance(plan_dict, dict) else []
    type_by_id = {t.get("id"): t.get("type", "?") for t in plan_tasks}

    table = Table(title="Resumo (replay)")
    table.add_column("Tarefa", style="cyan")
    table.add_column("Tipo", style="dim")
    table.add_column("LLM", style="green")
    table.add_column("Status")
    table.add_column("Tempo (s)", justify="right")
    table.add_column("Tokens In", justify="right")
    table.add_column("Tokens Out", justify="right")
    table.add_column("Custo (US$)", justify="right")

    for tid, r in results_dict.items():
        success = r.get("success", False)
        cache = r.get("cache_hit", False)
        if success:
            status_str = "[cyan]CACHE[/cyan]" if cache else "[green]OK[/green]"
        else:
            status_str = "[red]FALHA[/red]"
        table.add_row(
            tid,
            type_by_id.get(tid, "?"),
            r.get("llm_used", "?"),
            status_str,
            f"{(r.get('duration_ms') or 0) / 1000:.1f}",
            str(r.get("tokens_input", 0)),
            str(r.get("tokens_output", 0)),
            f"{r.get('cost', 0):.4f}",
        )

    console.print()
    console.print(table)

    extras = Table(title="Totais", show_lines=False, box=None)
    extras.add_column("Indicador", style="bold")
    extras.add_column("Valor", justify="right")
    extras.add_row("Tarefas concluidas", str(totals.get("tasks_completed", 0)))
    extras.add_row("Tarefas falhas", str(totals.get("tasks_failed", 0)))
    extras.add_row("Tarefas em cache", str(totals.get("tasks_cached", 0)))
    extras.add_row("Custo estimado", f"US$ {totals.get('estimated_cost_usd', 0):.4f}")
    extras.add_row("Custo real", f"US$ {totals.get('cost_usd', 0):.4f}")
    extras.add_row("Tempo total", f"{(totals.get('duration_ms', 0)) / 1000:.1f}s")
    extras.add_row("Limite de orcamento", f"US$ {totals.get('budget_limit', 0):.2f}")
    console.print()
    console.print(extras)

    if show_results:
        console.print()
        for tid, r in results_dict.items():
            output = (r.get("output") or "")[:1500]
            if not output:
                continue
            console.print(Panel(
                output, title=f"{tid} · {r.get('llm_used', '?')}",
                border_style="dim",
            ))


@cli.command()
@click.option("--ping", is_flag=True, help="Faz uma chamada minima a cada LLM para validar model_id e API key.")
def status(ping: bool):
    """Mostra LLMs canonicos (LLM_CONFIGS) e seu status — alem do FinOps diario.

    Com --ping faz um smoke test live em cada um dos 5 LLMs (1 token cada,
    custo desprezivel) — pega model_id invalidos antes da execucao.
    """
    table = Table(title="Status dos LLMs (canonicos)")
    table.add_column("LLM", style="cyan")
    table.add_column("Modelo")
    table.add_column("Provider")
    table.add_column("Strengths")
    table.add_column("API Key")
    table.add_column("Status")

    for name, cfg in LLM_CONFIGS.items():
        has_key = _check_api_key(cfg.api_key_env)
        status_str = "[green]Configurado[/green]" if has_key else "[red]Faltando[/red]"
        key_preview = f"{os.getenv(cfg.api_key_env, '')[:8]}..." if has_key else "N/A"
        table.add_row(
            name,
            cfg.model,
            cfg.provider.value,
            ", ".join(cfg.strengths[:3]),
            key_preview,
            status_str,
        )
    console.print(table)

    # Smoke test live de model IDs (--ping)
    if ping:
        from src.llm_client import LLMClient
        console.print("\n[bold cyan]Smoke test live (--ping):[/bold cyan] 1 token por LLM")
        ping_table = Table(show_lines=False)
        ping_table.add_column("LLM", style="bold")
        ping_table.add_column("Model ID")
        ping_table.add_column("Resultado")
        ping_table.add_column("Latencia")

        async def _ping_one(name: str, cfg) -> tuple[str, str, str]:
            import time as _t
            t0 = _t.perf_counter()
            try:
                client = LLMClient(cfg)
                # max_tokens=200 acomoda thinking budget default do Gemini 2.5 Pro
                resp = await client.query(prompt="Responda apenas: ok", system="", max_tokens=200)
                dt = (_t.perf_counter() - t0) * 1000
                return ("[green]OK[/green]", f"{int(dt)}ms", "")
            except Exception as exc:
                dt = (_t.perf_counter() - t0) * 1000
                msg = str(exc)[:80]
                return ("[red]FALHA[/red]", f"{int(dt)}ms", msg)

        async def _ping_all():
            results = {}
            for name, cfg in LLM_CONFIGS.items():
                if not _check_api_key(cfg.api_key_env):
                    results[name] = ("[yellow]SEM CHAVE[/yellow]", "-", "")
                    continue
                results[name] = await _ping_one(name, cfg)
            return results

        ping_results = asyncio.run(_ping_all())
        any_fail = False
        for name, cfg in LLM_CONFIGS.items():
            status_str, latency, err = ping_results.get(name, ("?", "-", ""))
            ping_table.add_row(name, cfg.model, status_str, latency)
            if "FALHA" in status_str:
                any_fail = True
                console.print(f"  [red]>>[/red] {name}: {err}")
        console.print(ping_table)
        if any_fail:
            console.print("\n[red]Atencao: 1 ou mais LLMs falharam no smoke test. Verifique model_id e API keys antes de executar 'cli.py run'.[/red]")
        else:
            console.print("\n[green]Todos os LLMs canonicos passaram no smoke test.[/green]")

    # Pre-aviso FinOps: providers proximos do limite diario (gap fechado pela refatoracao)
    try:
        fo = get_finops()
        finops_status = fo.daily_status()
        warnings = []
        for provider, data in finops_status.items():
            if provider.startswith("_"):
                continue
            pct = data.get("usage_pct", 0)
            if pct >= 80:
                tag = "[red]BLOQUEADO[/red]" if pct >= 95 else "[yellow]ALERTA[/yellow]"
                warnings.append(
                    f"  {tag} {provider}: US$ {data['spent']:.4f} / {data['limit']:.2f} ({pct:.0f}%)"
                )
        if warnings:
            console.print("\n[bold yellow]FinOps — providers proximos do limite:[/bold yellow]")
            for w in warnings:
                console.print(w)
    except Exception:
        pass


@cli.command(name="cost-report")
def cost_report():
    """Mostra histórico de custos."""
    if not COST_LOG_PATH.exists():
        console.print("[yellow]Nenhum histórico de custos encontrado.[/yellow]")
        return

    entries = []
    with open(COST_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    if not entries:
        console.print("[yellow]Histórico vazio.[/yellow]")
        return

    table = Table(title="Histórico de Custos")
    table.add_column("Data/Hora")
    table.add_column("Tarefas", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Custo (US$)", justify="right")

    total = 0.0
    for e in entries[-20:]:  # últimas 20 execuções
        table.add_row(
            e["timestamp"][:19],
            str(e["tasks"]),
            f"{e['tokens']:,}",
            f"{e['cost_usd']:.4f}",
        )
        total += e["cost_usd"]

    table.add_section()
    table.add_row("[bold]TOTAL[/bold]", "", "", f"[bold]{total:.4f}[/bold]")

    console.print(table)
    console.print(f"\n{len(entries)} execuções registradas no total.")


@cli.command()
@click.option("--limit", "-n", default=20, help="Numero de runs recentes a exibir.")
@click.option(
    "--export",
    type=click.Choice(["csv", "json"], case_sensitive=False),
    default=None,
    help="Exportar para CSV ou JSON em vez de renderizar tabela. Sprint 4: integra com Looker/Metabase.",
)
@click.option("--out", type=click.Path(), default=None, help="Caminho do arquivo de export. Default: stdout.")
@click.option(
    "--since",
    default=None,
    help="Sprint 5: filtra runs por janela temporal (ex.: 24h, 7d, 30d). Aplica antes do --limit.",
)
@click.option(
    "--html",
    "html_path",
    default=None,
    type=click.Path(),
    help="Sprint 7: gera dashboard HTML estatico em PATH (Chart.js inline, deployable).",
)
def dashboard(limit: int, export: str | None, out: str | None, since: str | None, html_path: str | None):
    """Dashboard CLI dos KPIs estruturais (.kpi_history.jsonl).

    Sprint 3 (2026-04-07): consome o jsonl de historico gravado pelo
    Orchestrator a cada run e renderiza:
    - Tabela timeseries dos ultimos N runs com distribution_health,
      cost_estimate_accuracy, real_cost, used_llms, max_share.
    - Card de status agregado (saudavel / atencao / drift).
    - Alerta visual se 3 runs consecutivos saem da banda 0.7-1.5x.

    Sprint 4 (2026-04-07): novas colunas tier_internal_engagement_rate
    e fallback_chain_save_rate. Flag --export csv|json para integrar
    com dashboards externos (Looker/Metabase).
    """
    from src.kpi_history import (
        load_recent_entries, detect_drift,
        ACCURACY_BAND_LOW, ACCURACY_BAND_HIGH, KPI_HISTORY_PATH,
    )

    if not KPI_HISTORY_PATH.exists():
        console.print(
            "[yellow]Nenhum historico de KPI encontrado em {}.[/yellow]".format(KPI_HISTORY_PATH)
        )
        console.print("[dim]Rode 'cli.py run' pelo menos uma vez para gerar entradas.[/dim]")
        return

    # Sprint 5 (2026-04-08): --since filtra por janela temporal antes do --limit
    if since:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        unit = since[-1].lower()
        try:
            qty = int(since[:-1])
        except ValueError:
            console.print(f"[red]Formato invalido para --since: {since!r}. Use 24h, 7d, 30d.[/red]")
            return
        deltas = {"h": _td(hours=qty), "d": _td(days=qty), "w": _td(weeks=qty)}
        if unit not in deltas:
            console.print(f"[red]Unidade desconhecida em --since: {unit!r}. Use h, d ou w.[/red]")
            return
        cutoff = _dt.now(_tz.utc) - deltas[unit]
        all_entries = load_recent_entries(n=10_000)

        def _ts(e):
            try:
                ts = e.get("timestamp", "")
                return _dt.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                return None

        filtered = [e for e in all_entries if (_ts(e) is not None and _ts(e) >= cutoff)]
        entries = filtered[-limit:]
        console.print(f"[dim]--since {since}: {len(entries)} runs em janela (cutoff {cutoff.isoformat()})[/dim]")
    else:
        entries = load_recent_entries(n=limit)
    if not entries:
        console.print("[yellow]Historico vazio.[/yellow]")
        return

    # Sprint 7: export HTML estatico
    if html_path:
        from src.dashboard_html import render_dashboard_html
        render_dashboard_html(entries=entries, output_path=Path(html_path))
        console.print(f"[green]Dashboard HTML salvo em {html_path}[/green]")
        console.print(f"[dim]{len(entries)} runs renderizados. Abra no navegador.[/dim]")
        return

    # Sprint 4: export csv/json
    if export:
        import csv as _csv
        import io as _io
        if export.lower() == "json":
            payload = json.dumps(entries, ensure_ascii=False, indent=2)
        else:
            buf = _io.StringIO()
            cols = [
                "timestamp", "demand", "distribution_health", "cost_estimate_accuracy",
                "tier_internal_engagement_rate", "fallback_chain_save_rate_cumulative",
                # Sprint 5 (2026-04-08): novos KPIs
                "quality_judge_pass", "parallelism_efficiency",
                "real_cost_usd", "estimated_cost_usd", "duration_ms",
                "tasks_completed", "tasks_failed",
            ]
            writer = _csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for e in entries:
                writer.writerow({c: e.get(c, "") for c in cols})
            payload = buf.getvalue()

        if out:
            Path(out).write_text(payload, encoding="utf-8")
            console.print(f"[green]Exportado {len(entries)} entradas para {out}[/green]")
        else:
            print(payload)
        return

    table = Table(title=f"KPI History — ultimos {len(entries)} runs")
    table.add_column("#", style="dim", width=3)
    table.add_column("Timestamp", style="cyan", width=19)
    table.add_column("Demanda", style="white")
    table.add_column("Health", justify="right")
    table.add_column("Accuracy", justify="right")
    table.add_column("Tier%", justify="right")  # Sprint 4: tier_internal_engagement_rate
    table.add_column("Save%", justify="right")  # Sprint 4: fallback_chain_save_rate_cumulative
    table.add_column("QJ", justify="right")     # Sprint 5: quality_judge_pass
    table.add_column("Par", justify="right")    # Sprint 5: parallelism_efficiency (speedup)
    table.add_column("Custo", justify="right")
    table.add_column("LLMs", justify="center")
    table.add_column("Max%", justify="right")
    table.add_column("Tasks", justify="right")

    for i, e in enumerate(entries, start=1):
        meta = e.get("_meta", {})
        used = meta.get("used_llms", 0)
        max_share = meta.get("max_share", 0)
        health = e.get("distribution_health", 0)
        accuracy = e.get("cost_estimate_accuracy", 0)
        # Sprint 4: novos KPIs
        tier_rate = e.get("tier_internal_engagement_rate", 0)
        save_rate = e.get("fallback_chain_save_rate_cumulative", 0)
        # Sprint 5: novos KPIs
        qj_pass = e.get("quality_judge_pass")  # 1.0 / 0.0 / None
        par_eff = e.get("parallelism_efficiency", 0)

        # Cores: verde se saudavel, amarelo atencao, vermelho ruim
        health_color = "green" if health >= 0.95 else ("yellow" if health >= 0.8 else "red")
        if ACCURACY_BAND_LOW <= accuracy <= ACCURACY_BAND_HIGH:
            acc_color = "green"
        elif 0.5 <= accuracy <= 2.0:
            acc_color = "yellow"
        else:
            acc_color = "red"
        max_color = "red" if max_share > 0.80 else ("yellow" if max_share > 0.60 else "green")
        tier_color = "green" if tier_rate >= 0.4 else ("yellow" if tier_rate > 0 else "dim")
        save_color = "green" if save_rate > 0.2 else "dim"
        # Sprint 5 colors
        if qj_pass is None:
            qj_label = "[dim]—[/dim]"
        elif qj_pass >= 1.0:
            qj_label = "[green]PASS[/green]"
        else:
            qj_label = "[red]FAIL[/red]"
        if par_eff >= 2.0:
            par_color = "green"
        elif par_eff >= 1.2:
            par_color = "yellow"
        else:
            par_color = "dim"
        par_label = f"[{par_color}]{par_eff:.1f}x[/{par_color}]" if par_eff else "[dim]—[/dim]"
        tasks_total = e.get("tasks_completed", 0) + e.get("tasks_failed", 0)
        failed = e.get("tasks_failed", 0)
        task_label = f"{tasks_total - failed}/{tasks_total}" if tasks_total else "—"

        table.add_row(
            str(i),
            e.get("timestamp", "?")[:19],
            e.get("demand", "")[:50] + ("…" if len(e.get("demand", "")) > 50 else ""),
            f"[{health_color}]{health:.2f}[/{health_color}]",
            f"[{acc_color}]{accuracy:.2f}x[/{acc_color}]",
            f"[{tier_color}]{tier_rate*100:.0f}%[/{tier_color}]",
            f"[{save_color}]{save_rate*100:.0f}%[/{save_color}]",
            qj_label,
            par_label,
            f"${e.get('real_cost_usd', 0):.4f}",
            f"{used}/5",
            f"[{max_color}]{max_share*100:.0f}%[/{max_color}]",
            task_label,
        )

    console.print(table)

    # Drift detection
    drift = detect_drift()
    if drift:
        console.print()
        console.print(Panel(
            f"[bold red]COST_ESTIMATE_DRIFT[/bold red] — {drift['count']} runs consecutivos fora da banda saudavel.\n"
            f"Ultimos valores: {drift['last_values']} (media {drift['average']:.2f}x, banda {drift['band']}).\n"
            f"[bold]Acao recomendada:[/bold] {drift['recommended_action']}",
            title="Alerta de Drift",
            border_style="red",
        ))
    else:
        # Status agregado dos ultimos 3 runs
        last_3 = entries[-3:] if len(entries) >= 3 else entries
        avg_health = sum(e.get("distribution_health", 0) for e in last_3) / len(last_3)
        avg_acc = sum(e.get("cost_estimate_accuracy", 0) for e in last_3) / len(last_3)
        if avg_health >= 0.95 and ACCURACY_BAND_LOW <= avg_acc <= ACCURACY_BAND_HIGH:
            status = "[green]SAUDAVEL[/green]"
        elif avg_health >= 0.8:
            status = "[yellow]ATENCAO[/yellow]"
        else:
            status = "[red]CRITICO[/red]"
        console.print()
        console.print(Panel(
            f"Status agregado (ultimos {len(last_3)} runs): {status}\n"
            f"distribution_health medio: [bold]{avg_health:.2f}[/bold] (alvo >= 0.95)\n"
            f"cost_estimate_accuracy medio: [bold]{avg_acc:.2f}x[/bold] (banda {ACCURACY_BAND_LOW}-{ACCURACY_BAND_HIGH}x)",
            title="Resumo",
            border_style="cyan",
        ))


@cli.command()
def models():
    """Lista os 5 LLMs canonicos (LLM_CONFIGS — source of truth)."""
    table = Table(title="LLMs Configurados (canonicos)")
    table.add_column("ID", style="cyan")
    table.add_column("Modelo")
    table.add_column("Provider")
    table.add_column("Custo/1K In (US$)", justify="right")
    table.add_column("Custo/1K Out (US$)", justify="right")
    table.add_column("Max Tokens", justify="right")
    table.add_column("Strengths")

    for name, cfg in LLM_CONFIGS.items():
        table.add_row(
            name,
            cfg.model,
            cfg.provider.value,
            f"{cfg.cost_per_1k_input:.6f}",
            f"{cfg.cost_per_1k_output:.6f}",
            str(cfg.max_tokens),
            ", ".join(cfg.strengths[:3]),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Comandos FinOps
# ---------------------------------------------------------------------------
@cli.group()
def finops():
    """Governanca FinOps — limites diarios, gastos e relatorios."""
    pass


@finops.command(name="status")
def finops_status():
    """Mostra gasto diario atual por provider."""
    fo = get_finops()
    status_data = fo.daily_status()

    table = Table(title="FinOps — Gasto Diario")
    table.add_column("Provider", style="cyan")
    table.add_column("Gasto (US$)", justify="right")
    table.add_column("Limite (US$)", justify="right")
    table.add_column("Restante (US$)", justify="right")
    table.add_column("Uso (%)", justify="right")
    table.add_column("Status")

    for provider, data in sorted(status_data.items()):
        if provider.startswith("_"):
            continue
        pct = data["usage_pct"]
        if pct >= 95:
            status_str = "[red]BLOQUEADO[/red]"
        elif pct >= 80:
            status_str = "[yellow]ALERTA[/yellow]"
        else:
            status_str = "[green]OK[/green]"
        table.add_row(
            provider,
            f"{data['spent']:.4f}",
            f"{data['limit']:.2f}",
            f"{data['remaining']:.4f}",
            f"{pct:.1f}%",
            status_str,
        )

    # Global row
    g = status_data.get("_global", {})
    table.add_section()
    g_pct = g.get("usage_pct", 0)
    if g_pct >= 95:
        g_status = "[red]BLOQUEADO[/red]"
    elif g_pct >= 80:
        g_status = "[yellow]ALERTA[/yellow]"
    else:
        g_status = "[green]OK[/green]"
    table.add_row(
        "[bold]GLOBAL[/bold]",
        f"[bold]{g.get('spent', 0):.4f}[/bold]",
        f"[bold]{g.get('limit', 0):.2f}[/bold]",
        f"[bold]{g.get('remaining', 0):.4f}[/bold]",
        f"[bold]{g_pct:.1f}%[/bold]",
        g_status,
    )

    console.print(table)


@finops.command(name="reset")
def finops_reset():
    """Reseta contadores diarios de gasto."""
    fo = get_finops()
    fo.reset_daily()
    console.print("[green]Contadores diarios resetados com sucesso.[/green]")


@finops.command(name="calibrate")
@click.option("--window", default=30, show_default=True, help="Numero de execution_*.json recentes a varrer.")
def finops_calibrate(window: int):
    """Sprint 5 (2026-04-08): recalibra AVG_COST_PER_CALL a partir do historico real.

    Varre os ultimos N execution reports em output/, agrupa custos por LLM
    e persiste output/.cost_calibration.json. As proximas execucoes do
    orchestrator passarao a usar essa tabela calibrada no pre_check do
    FinOps e no _estimate_cost.

    LLMs com amostra menor que MIN_SAMPLE (3) ficam com o default estatico.
    """
    from src.cost_calibrator import recalibrate, CALIBRATION_PATH

    payload = recalibrate(window=window)
    table = Table(title=f"Calibracao FinOps — {payload['sources_scanned']} reports varridos")
    table.add_column("LLM", style="cyan")
    table.add_column("Amostras", justify="right")
    table.add_column("Default (US$/call)", justify="right")
    table.add_column("Calibrado (US$/call)", justify="right")
    table.add_column("Delta", justify="right")

    statics = payload["static_defaults"]
    calibrated = payload["calibrated_avg_cost_per_call"]
    samples = payload["sample_sizes"]
    all_llms = sorted(set(statics.keys()) | set(samples.keys()))
    for llm in all_llms:
        s = samples.get(llm, 0)
        d = statics.get(llm, 0.0)
        c = calibrated.get(llm)
        if c is None:
            cal_str = "[dim]—[/dim]"
            delta_str = "[dim]insuficiente[/dim]"
        else:
            cal_str = f"{c:.6f}"
            if d > 0:
                delta = (c - d) / d * 100
                color = "red" if abs(delta) > 50 else ("yellow" if abs(delta) > 20 else "green")
                delta_str = f"[{color}]{delta:+.0f}%[/{color}]"
            else:
                delta_str = "—"
        table.add_row(llm, str(s), f"{d:.6f}", cal_str, delta_str)

    console.print(table)
    console.print(f"\nCalibracao persistida em: [cyan]{CALIBRATION_PATH}[/cyan]")
    console.print(f"[dim]Window={payload['window']}, MIN_SAMPLE={payload['min_sample']}[/dim]")


@finops.command(name="calibrate-rollback")
def finops_calibrate_rollback():
    """Sprint 7 (2026-04-08): restaura o backup do .cost_calibration.json.

    Util quando uma calibracao automatica do auto-trigger introduziu valores
    piores que o default. O backup e gerado automaticamente antes de cada
    `finops calibrate` ou auto-trigger via `recalibrate(persist=True)`.
    """
    from src.cost_calibrator import rollback_calibration, CALIBRATION_BACKUP_PATH
    if rollback_calibration():
        console.print(f"[green]Rollback aplicado a partir de {CALIBRATION_BACKUP_PATH}[/green]")
    else:
        console.print(f"[yellow]Nenhum backup encontrado em {CALIBRATION_BACKUP_PATH}[/yellow]")
        sys.exit(1)


@finops.command(name="report")
def finops_report():
    """Gera relatorio Markdown da ultima sessao."""
    fo = get_finops()
    report = fo.session_report()

    # Save to file
    report_dir = Path("output")
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"finops_report_{timestamp}.md"
    report_path.write_text(report, encoding="utf-8")

    console.print(Panel(report, title="Relatorio FinOps", border_style="cyan"))
    console.print(f"\nRelatorio salvo em: [cyan]{report_path}[/cyan]")


# ---------------------------------------------------------------------------
# Comandos de Trace (Observability)
# ---------------------------------------------------------------------------
@cli.group()
def trace():
    """Distributed tracing — visualizar execucoes passadas."""
    pass


@trace.command(name="list")
@click.option("--limit", default=20, help="Numero maximo de traces a listar.")
def trace_list(limit: int):
    """Lista traces recentes salvos em output/.traces/."""
    from src.tracer import list_traces

    traces = list_traces(limit=limit)

    if not traces:
        console.print("[yellow]Nenhum trace encontrado em output/.traces/[/yellow]")
        return

    table = Table(title="Traces Recentes")
    table.add_column("Trace ID", style="cyan")
    table.add_column("Demanda")
    table.add_column("Inicio")
    table.add_column("Duracao", justify="right")
    table.add_column("Custo (US$)", justify="right")
    table.add_column("Spans", justify="right")

    for t in traces:
        started = t["started_at"][:19] if t["started_at"] else "?"
        duration_s = t["total_duration_ms"] / 1000
        table.add_row(
            t["trace_id"],
            t["demand"][:50] + ("..." if len(t["demand"]) > 50 else ""),
            started,
            f"{duration_s:.1f}s",
            f"{t['total_cost']:.4f}",
            str(t["span_count"]),
        )

    console.print(table)
    console.print(f"\n{len(traces)} trace(s) encontrado(s).")


@trace.command(name="show")
@click.argument("trace_id")
def trace_show(trace_id: str):
    """Mostra timeline e resumo de um trace especifico."""
    from src.tracer import load_trace, export_timeline, export_summary

    t = load_trace(trace_id)
    if t is None:
        console.print(f"[red]Trace '{trace_id}' nao encontrado.[/red]")
        return

    # Timeline
    timeline = export_timeline(t)
    console.print(Panel(timeline, title="Timeline", border_style="cyan"))

    # Summary
    summary = export_summary(t)
    console.print(Panel(summary, title="Resumo", border_style="green"))


@trace.command(name="last")
def trace_last():
    """Mostra o trace mais recente."""
    from src.tracer import load_latest_trace, export_timeline, export_summary

    t = load_latest_trace()
    if t is None:
        console.print("[yellow]Nenhum trace encontrado em output/.traces/[/yellow]")
        return

    # Timeline
    timeline = export_timeline(t)
    console.print(Panel(timeline, title=f"Timeline — {t.trace_id}", border_style="cyan"))

    # Summary
    summary = export_summary(t)
    console.print(Panel(summary, title="Resumo", border_style="green"))


if __name__ == "__main__":
    cli()

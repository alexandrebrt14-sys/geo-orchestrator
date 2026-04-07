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

    # Tabela: Uso por LLM (canonico, 5 LLMs do LLM_CONFIGS)
    llm_stats: dict[str, dict] = {
        name: {"tasks": 0, "ok": 0, "fail": 0, "cost": 0.0, "tokens": 0, "time_ms": 0}
        for name in LLM_CONFIGS
    }
    for r in results_list:
        bucket = r.llm_used if r.llm_used in llm_stats else None
        # Aliases (ex.: 'semantic_cache', 'code_executor' nao contam para 5 LLMs)
        if bucket is None:
            continue
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
def run(demand: str, dry_run: bool, verbose: bool, output_dir: str, force: bool, no_smart: bool):
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
        orch = Orchestrator(force=force, smart=not no_smart)
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
    orch = Orchestrator(force=force, smart=not no_smart)
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

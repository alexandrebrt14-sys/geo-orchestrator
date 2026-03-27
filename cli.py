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
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import click
import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.tree import Tree

from src.agents.base import TaskType, TaskResult, format_context_from_results
from src.agents.researcher import ResearcherAgent
from src.agents.writer import WriterAgent, WritingMode
from src.agents.architect import ArchitectAgent
from src.agents.analyzer import AnalyzerAgent
from src.agents.groq_agent import GroqAgent
from src.finops import FinOps, get_finops
from src.templates.decomposition import DECOMPOSITION_PROMPT

load_dotenv()

# Forçar UTF-8 no Windows para suportar caracteres Unicode no Rich
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

console = Console(force_terminal=True)

# ---------------------------------------------------------------------------
# Configuração de modelos
# ---------------------------------------------------------------------------
MODELS = {
    "perplexity": {
        "name": "sonar-pro",
        "provider": "Perplexity",
        "env_key": "PERPLEXITY_API_KEY",
        "tasks": ["research"],
        "cost_1k_in": 0.003,
        "cost_1k_out": 0.015,
    },
    "gpt4o": {
        "name": "gpt-4o",
        "provider": "OpenAI",
        "env_key": "OPENAI_API_KEY",
        "tasks": ["writing"],
        "cost_1k_in": 0.0025,
        "cost_1k_out": 0.01,
    },
    "claude-opus": {
        "name": "claude-opus-4-20250514",
        "provider": "Anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "tasks": ["architecture", "code_generation", "review"],
        "cost_1k_in": 0.015,
        "cost_1k_out": 0.075,
    },
    "gemini-flash": {
        "name": "gemini-2.5-flash",
        "provider": "Google",
        "env_key": "GOOGLE_AI_API_KEY",
        "tasks": ["analysis", "data_processing"],
        "cost_1k_in": 0.00015,
        "cost_1k_out": 0.0006,
    },
    "groq": {
        "name": "llama-3.3-70b-versatile",
        "provider": "Groq",
        "env_key": "GROQ_API_KEY",
        "tasks": ["classification", "summarization", "translation"],
        "cost_1k_in": 0.00059,
        "cost_1k_out": 0.00079,
    },
}

COST_LOG_PATH = Path("output/cost_history.jsonl")
REPORT_DIR = Path("output")

# Mapeamento tipo de tarefa → modelo + provider para narração
TASK_MODEL_MAP = {
    "research": ("sonar-pro", "Perplexity", "magenta"),
    "analysis": ("gemini-2.5-flash", "Google/Gemini", "yellow"),
    "writing": ("gpt-4o", "OpenAI", "green"),
    "copywriting": ("gpt-4o", "OpenAI", "green"),
    "seo": ("gpt-4o", "OpenAI", "green"),
    "translation": ("gpt-4o", "OpenAI", "green"),
    "architecture": ("claude-opus-4-6", "Anthropic/Claude", "blue"),
    "code_generation": ("claude-opus-4-6", "Anthropic/Claude", "blue"),
    "review": ("claude-opus-4-6", "Anthropic/Claude", "blue"),
    "data_processing": ("gemini-2.5-flash", "Google/Gemini", "yellow"),
    "classification": ("gemini-2.5-flash", "Google/Gemini", "yellow"),
    "summarization": ("gemini-2.5-flash", "Google/Gemini", "yellow"),
    "fact_check": ("sonar-pro", "Perplexity", "magenta"),
    "classification": ("llama-3.3-70b", "Groq", "red"),
    "summarization": ("llama-3.3-70b", "Groq", "red"),
    "translation": ("llama-3.3-70b", "Groq", "red"),
    "deploy": ("local", "Execução Local", "white"),
}


def _narrate_task(task_def: dict, phase: str = "start") -> None:
    """Narra qual modelo está executando cada tarefa — sempre visível."""
    task_type = task_def.get("type", "unknown")
    model, provider, color = TASK_MODEL_MAP.get(task_type, ("unknown", "Unknown", "white"))
    task_id = task_def.get("id", "?")
    title = task_def.get("title", "")

    if phase == "start":
        console.print(
            f"  [{color}][{provider}/{model}][/{color}] "
            f"[bold]{task_id}[/bold]: {title}"
        )
    elif phase == "done":
        console.print(
            f"  [{color}][{provider}/{model}][/{color}] "
            f"[bold]{task_id}[/bold]: [green]concluída[/green]"
        )
    elif phase == "fail":
        console.print(
            f"  [{color}][{provider}/{model}][/{color}] "
            f"[bold]{task_id}[/bold]: [red]falhou[/red]"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _check_api_key(env_key: str) -> bool:
    return bool(os.getenv(env_key))


def _get_httpx_client(provider: str, timeout: float = 300.0) -> httpx.AsyncClient:
    """Retorna um cliente httpx configurado para o provider."""
    headers: dict[str, str] = {}

    if provider == "Perplexity":
        headers["Authorization"] = f"Bearer {os.getenv('PERPLEXITY_API_KEY', '')}"
    elif provider == "OpenAI":
        headers["Authorization"] = f"Bearer {os.getenv('OPENAI_API_KEY', '')}"
    elif provider == "Anthropic":
        headers["x-api-key"] = os.getenv("ANTHROPIC_API_KEY", "")
        headers["anthropic-version"] = "2023-06-01"
    elif provider == "Google":
        # Gemini usa query param com GOOGLE_AI_API_KEY, não header
        pass
    elif provider == "Groq":
        headers["Authorization"] = f"Bearer {os.getenv('GROQ_API_KEY', '')}"

    return httpx.AsyncClient(headers=headers, timeout=timeout)


def _create_agent(task_type: str, writing_mode: str = "article"):
    """Cria o agente apropriado para o tipo de tarefa."""
    if task_type in ("research",):
        cfg = MODELS["perplexity"]
        client = _get_httpx_client("Perplexity")
        return ResearcherAgent(client, model_name=cfg["name"])

    elif task_type in ("writing",):
        cfg = MODELS["gpt4o"]
        client = _get_httpx_client("OpenAI")
        mode = WritingMode(writing_mode) if writing_mode in WritingMode.__members__.values() else WritingMode.ARTICLE
        return WriterAgent(client, model_name=cfg["name"], writing_mode=mode)

    elif task_type in ("architecture", "code_generation", "review"):
        cfg = MODELS["claude-opus"]
        client = _get_httpx_client("Anthropic")
        return ArchitectAgent(client, model_name=cfg["name"])

    elif task_type in ("analysis", "data_processing"):
        cfg = MODELS["gemini-flash"]
        client = _get_httpx_client("Google")
        return AnalyzerAgent(client, model_name=cfg["name"])

    elif task_type in ("classification", "summarization", "translation"):
        cfg = MODELS["groq"]
        client = _get_httpx_client("Groq")
        return GroqAgent(client, model_name=cfg["name"])

    else:
        raise ValueError(f"Tipo de tarefa desconhecido: {task_type}")


async def _decompose_demand(demand: str) -> dict:
    """Usa Claude para decompor a demanda em tarefas."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]ANTHROPIC_API_KEY não configurada. Necessária para decomposição.[/red]")
        sys.exit(1)

    async with httpx.AsyncClient(
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        timeout=60.0,
    ) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 4096,
                "system": DECOMPOSITION_PROMPT,
                "messages": [{"role": "user", "content": demand}],
            },
        )
        response.raise_for_status()
        data = response.json()

    content = ""
    for block in data.get("content", []):
        if block["type"] == "text":
            content += block["text"]

    # Limpar markdown wrapping se presente
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    if not content:
        console.print("[red]Erro: resposta vazia da API de decomposição.[/red]")
        sys.exit(1)

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        # Tentar extrair JSON de dentro do texto
        import re
        match = re.search(r'\{[\s\S]*\}', content)
        if match:
            return json.loads(match.group())
        console.print(f"[red]Erro ao parsear JSON da decomposição: {e}[/red]")
        console.print(f"[dim]Conteúdo recebido (primeiros 500 chars):[/dim]\n{content[:500]}")
        sys.exit(1)


def _display_plan(plan: dict) -> None:
    """Exibe o plano de execução formatado."""
    console.print()
    console.print(Panel(
        f"[bold]{plan['demand_summary']}[/bold]\n\n"
        f"Tarefas: {plan['total_tasks']}  |  "
        f"Custo estimado: US$ {plan.get('estimated_total_cost_usd', '?')}  |  "
        f"Tempo estimado: {plan.get('estimated_duration_minutes', '?')} min",
        title="Plano de Execução",
        border_style="cyan",
    ))

    # Árvore de tarefas por grupo paralelo
    tree = Tree("[bold cyan]Pipeline[/bold cyan]")
    for group in plan.get("execution_plan", {}).get("parallel_groups", []):
        group_branch = tree.add(
            f"[bold yellow]Grupo {group['group']}[/bold yellow] — {group['description']}"
        )
        for task_id in group["tasks"]:
            task = next((t for t in plan["tasks"] if t["id"] == task_id), None)
            if task:
                dep_str = f" (depende de: {', '.join(task['dependencies'])})" if task["dependencies"] else ""
                group_branch.add(
                    f"[green]{task['id']}[/green] [{task['type']}] "
                    f"{task['title']}{dep_str} "
                    f"(complexidade: {task['complexity']}, ~US$ {task.get('estimated_cost_usd', '?')})"
                )

    console.print(tree)
    console.print()


async def _execute_plan(plan: dict, verbose: bool = False, output_dir: Path = REPORT_DIR) -> list[TaskResult]:
    """Executa o plano de tarefas respeitando dependências e paralelismo."""
    results: dict[str, TaskResult] = {}
    all_results: list[TaskResult] = []

    groups = plan.get("execution_plan", {}).get("parallel_groups", [])

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        for group in groups:
            group_desc = f"Grupo {group['group']}: {group['description']}"
            pg_task = progress.add_task(group_desc, total=len(group["tasks"]))

            # Preparar tarefas do grupo para execução paralela
            async def run_task(task_def: dict) -> TaskResult:
                task_type = task_def["type"]
                task_id = task_def["id"]

                # Narrar qual modelo executa — sempre visível
                _narrate_task(task_def, "start")

                # Montar contexto das dependências
                dep_results = [results[dep_id] for dep_id in task_def.get("dependencies", []) if dep_id in results]
                context = format_context_from_results(dep_results)

                # Pular tarefas do tipo deploy (execução local)
                if task_type == "deploy":
                    _narrate_task(task_def, "done")
                    return TaskResult(
                        task_id=task_id,
                        task_type=TaskType.DEPLOY,
                        agent_name="LocalDeploy",
                        model_used="local",
                        success=True,
                        output={"message": "Deploy deve ser executado manualmente via geo.sh"},
                    )

                agent = _create_agent(task_type)
                result = await agent.execute(
                    task=task_def["description"],
                    context=context,
                    task_id=task_id,
                )
                return result

            # Executar tarefas do grupo em paralelo
            tasks_in_group = [t for t in plan["tasks"] if t["id"] in group["tasks"]]
            group_results = await asyncio.gather(
                *(run_task(t) for t in tasks_in_group),
                return_exceptions=True,
            )

            for task_def, result in zip(tasks_in_group, group_results):
                if isinstance(result, Exception):
                    result = TaskResult(
                        task_id=task_def["id"],
                        task_type=TaskType(task_def["type"]) if task_def["type"] in TaskType.__members__.values() else TaskType.ANALYSIS,
                        agent_name="Error",
                        model_used="none",
                        success=False,
                        error=str(result),
                    )
                results[task_def["id"]] = result
                all_results.append(result)
                progress.advance(pg_task)

                # Sempre narrar resultado por modelo
                _narrate_task(task_def, "done" if result.success else "fail")
                model_info = TASK_MODEL_MAP.get(task_def["type"], ("?", "?", "white"))
                console.print(
                    f"    [dim]{result.duration_seconds:.1f}s | "
                    f"{result.tokens_input} > {result.tokens_output} tokens | "
                    f"US$ {result.cost_usd:.4f}[/dim]"
                )

    return all_results


def _display_summary(results: list[TaskResult]) -> None:
    """Exibe resumo de custos e resultados."""
    table = Table(title="Resumo da Execução")
    table.add_column("Tarefa", style="cyan")
    table.add_column("Agente", style="green")
    table.add_column("Modelo")
    table.add_column("Status")
    table.add_column("Tempo (s)", justify="right")
    table.add_column("Tokens In", justify="right")
    table.add_column("Tokens Out", justify="right")
    table.add_column("Custo (US$)", justify="right")

    total_cost = 0.0
    total_time = 0.0
    total_in = 0
    total_out = 0

    for r in results:
        status = "[green]OK[/green]" if r.success else "[red]FALHA[/red]"
        table.add_row(
            r.task_id,
            r.agent_name,
            r.model_used,
            status,
            f"{r.duration_seconds:.1f}",
            str(r.tokens_input),
            str(r.tokens_output),
            f"{r.cost_usd:.4f}",
        )
        total_cost += r.cost_usd
        total_time += r.duration_seconds
        total_in += r.tokens_input
        total_out += r.tokens_output

    table.add_section()
    table.add_row(
        "[bold]TOTAL[/bold]", "", "", "",
        f"[bold]{total_time:.1f}[/bold]",
        f"[bold]{total_in}[/bold]",
        f"[bold]{total_out}[/bold]",
        f"[bold]{total_cost:.4f}[/bold]",
    )

    console.print()
    console.print(table)

    # ── Status de uso por modelo (5 LLMs) ──
    model_stats: dict[str, dict] = {}
    for r in results:
        provider = r.agent_name or "unknown"
        if provider not in model_stats:
            model_stats[provider] = {"tasks": 0, "ok": 0, "fail": 0, "cost": 0.0, "tokens": 0, "time": 0.0}
        model_stats[provider]["tasks"] += 1
        model_stats[provider]["ok"] += 1 if r.success else 0
        model_stats[provider]["fail"] += 0 if r.success else 1
        model_stats[provider]["cost"] += r.cost_usd
        model_stats[provider]["tokens"] += r.tokens_input + r.tokens_output
        model_stats[provider]["time"] += r.duration_seconds

    usage_table = Table(title="Uso por Modelo (5 LLMs)", show_lines=False)
    usage_table.add_column("Provider", style="bold")
    usage_table.add_column("Tarefas", justify="center")
    usage_table.add_column("OK", justify="center", style="green")
    usage_table.add_column("Falha", justify="center", style="red")
    usage_table.add_column("Tokens", justify="right")
    usage_table.add_column("Custo (US$)", justify="right")
    usage_table.add_column("Tempo (s)", justify="right")
    usage_table.add_column("Cobertura", justify="center")

    all_providers = ["Anthropic/Claude", "OpenAI/GPT-4o", "Google/Gemini", "Perplexity/Sonar", "Groq/Llama"]
    provider_aliases = {
        "Anthropic/Claude": ["anthropic", "claude", "architect"],
        "OpenAI/GPT-4o": ["openai", "gpt4o", "gpt-4o", "writer"],
        "Google/Gemini": ["google", "gemini", "analyzer"],
        "Perplexity/Sonar": ["perplexity", "sonar", "researcher"],
        "Groq/Llama": ["groq", "llama", "groq_agent"],
    }

    used_count = 0
    for display_name in all_providers:
        aliases = provider_aliases[display_name]
        stats = {"tasks": 0, "ok": 0, "fail": 0, "cost": 0.0, "tokens": 0, "time": 0.0}
        for alias in aliases:
            for key, val in model_stats.items():
                if alias.lower() in key.lower():
                    for k in stats:
                        stats[k] += val[k]
        if stats["tasks"] > 0:
            used_count += 1
            coverage = "[green]USADO[/green]"
        else:
            coverage = "[red]NAO USADO[/red]"
        usage_table.add_row(
            display_name,
            str(stats["tasks"]),
            str(stats["ok"]),
            str(stats["fail"]),
            f"{stats['tokens']:,}",
            f"{stats['cost']:.4f}",
            f"{stats['time']:.1f}",
            coverage,
        )

    console.print()
    console.print(usage_table)
    coverage_pct = (used_count / 5) * 100
    color = "green" if used_count == 5 else "yellow" if used_count >= 3 else "red"
    console.print(f"\n[{color}]Cobertura de modelos: {used_count}/5 ({coverage_pct:.0f}%)[/{color}]")
    if used_count < 5:
        console.print("[yellow]Atenção: nem todos os 5 modelos foram utilizados nesta execução.[/yellow]")


def _save_report(plan: dict, results: list[TaskResult], output_dir: Path) -> None:
    """Salva relatório completo da execução."""
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"execution_{timestamp}.json"

    report = {
        "timestamp": datetime.now().isoformat(),
        "plan": plan,
        "results": [
            {
                "task_id": r.task_id,
                "task_type": r.task_type.value,
                "agent_name": r.agent_name,
                "model_used": r.model_used,
                "success": r.success,
                "error": r.error,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "cost_usd": r.cost_usd,
                "duration_seconds": r.duration_seconds,
                "output_preview": str(r.output)[:500] if r.output else None,
            }
            for r in results
        ],
        "totals": {
            "cost_usd": sum(r.cost_usd for r in results),
            "duration_seconds": sum(r.duration_seconds for r in results),
            "tokens_input": sum(r.tokens_input for r in results),
            "tokens_output": sum(r.tokens_output for r in results),
            "tasks_succeeded": sum(1 for r in results if r.success),
            "tasks_failed": sum(1 for r in results if not r.success),
        },
    }

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\nRelatório salvo em: [cyan]{report_path}[/cyan]")

    # Salvar outputs individuais
    for r in results:
        if r.success and r.output:
            task_path = output_dir / f"{timestamp}_{r.task_id}.json"
            task_path.write_text(
                json.dumps(r.output, ensure_ascii=False, indent=2) if isinstance(r.output, dict) else str(r.output),
                encoding="utf-8",
            )

    # Append ao log de custos
    COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(COST_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "cost_usd": report["totals"]["cost_usd"],
            "tasks": report["totals"]["tasks_succeeded"] + report["totals"]["tasks_failed"],
            "tokens": report["totals"]["tokens_input"] + report["totals"]["tokens_output"],
        }) + "\n")


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
@click.option("--verbose", is_flag=True, help="Mostra cada passo durante a execução.")
@click.option("--output-dir", type=click.Path(), default="output", help="Diretório para salvar resultados.")
def run(demand: str, dry_run: bool, verbose: bool, output_dir: str):
    """Executa o pipeline completo para uma demanda."""
    console.print(Panel(
        f"[bold]Demanda:[/bold] {demand}",
        title="geo-orchestrator",
        border_style="blue",
    ))

    # Mostrar a banca de modelos disponíveis
    banca = Table(title="Banca de Modelos", show_lines=False, box=None)
    banca.add_column("Papel", style="bold")
    banca.add_column("Modelo", style="cyan")
    banca.add_column("Provider")
    banca.add_column("Status")
    banca.add_row("Pesquisador", "sonar-pro", "Perplexity", "[green]ativo[/green]" if _check_api_key("PERPLEXITY_API_KEY") else "[red]sem chave[/red]")
    banca.add_row("Redator", "gpt-4o", "OpenAI", "[green]ativo[/green]" if _check_api_key("OPENAI_API_KEY") else "[red]sem chave[/red]")
    banca.add_row("Arquiteto", "claude-opus-4-6", "Anthropic", "[green]ativo[/green]" if _check_api_key("ANTHROPIC_API_KEY") else "[red]sem chave[/red]")
    banca.add_row("Analista", "gemini-2.5-flash", "Google", "[green]ativo[/green]" if _check_api_key("GOOGLE_AI_API_KEY") else "[red]sem chave[/red]")
    banca.add_row("Velocista", "llama-3.3-70b", "Groq", "[green]ativo[/green]" if _check_api_key("GROQ_API_KEY") else "[red]sem chave[/red]")
    console.print(banca)
    console.print()

    # Fase 1: Decomposição
    console.print("[bold cyan]Fase 1:[/bold cyan] [blue][Anthropic/Claude][/blue] Decompondo demanda em tarefas...\n")
    plan = asyncio.run(_decompose_demand(demand))
    _display_plan(plan)

    if dry_run:
        console.print("[yellow]Modo dry-run: execução pulada.[/yellow]")
        return

    # Fase 2: Execução
    console.print("[bold cyan]Fase 2:[/bold cyan] Executando tarefas com roteamento por modelo...\n")
    results = asyncio.run(_execute_plan(plan, verbose=verbose, output_dir=Path(output_dir)))

    # Fase 3: Relatório
    _display_summary(results)
    _save_report(plan, results, Path(output_dir))

    failed = sum(1 for r in results if not r.success)
    if failed:
        console.print(f"\n[red]{failed} tarefa(s) falharam. Verifique o relatório.[/red]")
        sys.exit(1)
    else:
        console.print("\n[green]Todas as tarefas concluídas com sucesso.[/green]")


@cli.command()
@click.argument("demand")
def plan(demand: str):
    """Decompõe a demanda e mostra o plano sem executar."""
    console.print("[bold cyan]Decompondo demanda...[/bold cyan]")
    decomposed = asyncio.run(_decompose_demand(demand))
    _display_plan(decomposed)

    # Salvar plano
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    plan_path = REPORT_DIR / f"plan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    plan_path.write_text(json.dumps(decomposed, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"Plano salvo em: [cyan]{plan_path}[/cyan]")


@cli.command()
def status():
    """Mostra LLMs disponíveis e seu status."""
    table = Table(title="Status dos LLMs")
    table.add_column("Provider", style="cyan")
    table.add_column("Modelo")
    table.add_column("Tarefas")
    table.add_column("API Key")
    table.add_column("Status")

    for key, cfg in MODELS.items():
        has_key = _check_api_key(cfg["env_key"])
        status_str = "[green]Configurado[/green]" if has_key else "[red]Faltando[/red]"
        key_preview = f"{os.getenv(cfg['env_key'], '')[:8]}..." if has_key else "N/A"

        table.add_row(
            cfg["provider"],
            cfg["name"],
            ", ".join(cfg["tasks"]),
            key_preview,
            status_str,
        )

    console.print(table)


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
    """Lista todos os modelos configurados."""
    table = Table(title="Modelos Configurados")
    table.add_column("ID", style="cyan")
    table.add_column("Modelo")
    table.add_column("Provider")
    table.add_column("Custo/1K In (US$)", justify="right")
    table.add_column("Custo/1K Out (US$)", justify="right")
    table.add_column("Tarefas")

    for key, cfg in MODELS.items():
        table.add_row(
            key,
            cfg["name"],
            cfg["provider"],
            f"{cfg['cost_1k_in']:.6f}",
            f"{cfg['cost_1k_out']:.6f}",
            ", ".join(cfg["tasks"]),
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

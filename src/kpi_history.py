"""KPI history persistence + drift alert (sprint 3 — 2026-04-07).

Persiste 2 KPIs estruturais em output/.kpi_history.jsonl apos cada execucao
do Orchestrator, e detecta drift quando 3 runs consecutivos saem da banda
saudavel de cost_estimate_accuracy.

KPIs persistidos:

1. **distribution_health** = (used_llms / 5) * (1 - max(0, max_share - 0.8))
   - Mede se a carga esta distribuida saudavelmente entre os 5 LLMs.
   - Range: 0.0 a 1.0. Alvo: >= 0.95.
   - Penalidade dupla:
     a) Cobertura: quanto maior o numero de LLMs distintos usados, melhor.
     b) Cap: passar de 80% em qualquer LLM derruba o score linearmente.
   - Exemplos:
     - 5 LLMs, max 33% concentracao: 1.0 * 1.0 = 1.0 (otimo)
     - 5 LLMs, max 50%: 1.0 * 1.0 = 1.0 (cap nao acionou)
     - 5 LLMs, max 90%: 1.0 * (1 - 0.10) = 0.90 (cap violado)
     - 2 LLMs, max 83%: 0.4 * (1 - 0.03) = 0.388 (run #1 da bateria)

2. **cost_estimate_accuracy** = real_cost / estimated_cost
   - Mede precisao da estimativa de custo do FinOps.
   - Banda saudavel: [0.7, 1.5]. Fora disso = AVG_COST_PER_CALL precisa
     recalibrar (mesmo bug que crashou o sprint 1).
   - Range tipico: 0.5 a 10.0 (em casos extremos).
   - Alerta: 3 runs consecutivos fora da banda dispara warning automatico.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import OUTPUT_DIR

logger = logging.getLogger(__name__)

# Banda saudavel do cost_estimate_accuracy
ACCURACY_BAND_LOW: float = 0.7
ACCURACY_BAND_HIGH: float = 1.5

# Quantos runs consecutivos fora da banda disparam alerta
DRIFT_THRESHOLD: int = 3

# Arquivo de historico (jsonl append-only)
KPI_HISTORY_PATH: Path = OUTPUT_DIR / ".kpi_history.jsonl"


def compute_distribution_health(
    llm_usage: dict[str, int],
    cap_threshold: float = 0.80,
) -> tuple[float, dict]:
    """Computa o KPI distribution_health a partir do session_usage do Router.

    Returns:
        Tupla (score, metadata) onde metadata tem
        {used_llms, total_tasks, max_share, max_share_provider}.
    """
    # Filtra apenas os 5 canonicos (ignora claude_sonnet, claude_haiku que sao tier interno)
    canonicos = ["claude", "gpt4o", "gemini", "perplexity", "groq"]
    canonical_usage = {k: llm_usage.get(k, 0) for k in canonicos}
    total_tasks = sum(canonical_usage.values())

    if total_tasks == 0:
        return 0.0, {
            "used_llms": 0,
            "total_tasks": 0,
            "max_share": 0.0,
            "max_share_provider": None,
        }

    used_llms = sum(1 for v in canonical_usage.values() if v > 0)
    max_share = max(canonical_usage.values()) / total_tasks
    max_share_provider = max(canonical_usage, key=canonical_usage.get)

    coverage_term = used_llms / 5.0
    cap_penalty = 1.0 - max(0.0, max_share - cap_threshold)
    health = coverage_term * cap_penalty

    return round(health, 4), {
        "used_llms": used_llms,
        "total_tasks": total_tasks,
        "max_share": round(max_share, 4),
        "max_share_provider": max_share_provider,
    }


def compute_cost_estimate_accuracy(real_cost: float, estimated_cost: float) -> float:
    """Computa a razao real/estimado. None se estimativa for zero."""
    if estimated_cost <= 0:
        return 0.0
    return round(real_cost / estimated_cost, 4)


def compute_tier_internal_engagement_rate(llm_usage: dict[str, int]) -> tuple[float, dict]:
    """Sprint 4 (2026-04-07): % de tarefas Claude que foram para Sonnet/Haiku.

    Mede adocao do tier interno em runtime. Score:
    = (claude_sonnet + claude_haiku) / (claude + claude_sonnet + claude_haiku)

    Range: 0.0 (so usa Opus) a 1.0 (so usa Sonnet/Haiku).
    Alvo: > 0.4 indica que o decomposer esta marcando complexity variavel
    e o downgrade automatico esta operando.
    """
    opus = llm_usage.get("claude", 0)
    sonnet = llm_usage.get("claude_sonnet", 0)
    haiku = llm_usage.get("claude_haiku", 0)
    total = opus + sonnet + haiku
    if total == 0:
        return 0.0, {"opus": 0, "sonnet": 0, "haiku": 0, "claude_total": 0}
    rate = (sonnet + haiku) / total
    return round(rate, 4), {
        "opus": opus,
        "sonnet": sonnet,
        "haiku": haiku,
        "claude_total": total,
    }


def compute_quality_judge_pass_rate(
    quality_verdict: str | None,
    pass_verdicts: tuple[str, ...] = (
        # PT-BR verdicts reais do QualityJudge (src/quality_judge.py)
        "aprovado", "aprovado_com_ressalvas",
        # Aliases EN para compatibilidade com mocks/testes
        "approved", "good", "excellent", "pass",
    ),
) -> float | None:
    """Sprint 5 (2026-04-08): pass rate do Quality Judge por run.

    Retorna 1.0 se o verdict do Quality Judge esta entre os aceitos,
    0.0 se rejeitou, None se o judge nao foi invocado nesta run.

    Aceita verdicts PT-BR canonicos do QualityJudge real ("APROVADO",
    "APROVADO_COM_RESSALVAS", "REPROVADO") + aliases EN para mocks.

    A medida acumulativa (taxa media nos ultimos N runs) e calculada
    pelo dashboard a partir do .kpi_history.jsonl.
    """
    if not quality_verdict:
        return None
    return 1.0 if quality_verdict.strip().lower() in pass_verdicts else 0.0


def compute_parallelism_efficiency(
    wave_timings: list[dict] | None,
    task_durations_ms: list[int] | None,
    total_duration_ms: int,
) -> tuple[float, dict]:
    """Sprint 5 (2026-04-08): speedup do pipeline em waves vs execucao sequencial.

    speedup = sum(task_duration_ms) / max(total_duration_ms, 1)

    - 1.0 = nenhum ganho (tudo sequencial)
    - 5.0 = 5x mais rapido que sequencial (5 tarefas paralelas perfeitas)
    - Range tipico do orchestrator: 2.0 a 4.5 (5 LLMs, mas waves > 1)

    Tambem retorna metadata com max wave width (gargalo de paralelizacao).
    """
    if not task_durations_ms:
        return 0.0, {"task_count": 0, "max_wave_width": 0, "wave_count": 0, "sequential_ms": 0}

    sequential_ms = sum(d for d in task_durations_ms if d)
    if total_duration_ms <= 0:
        return 0.0, {
            "task_count": len(task_durations_ms),
            "max_wave_width": 0,
            "wave_count": 0,
            "sequential_ms": sequential_ms,
        }

    speedup = sequential_ms / total_duration_ms
    max_wave_width = 0
    wave_count = 0
    if wave_timings:
        wave_count = len(wave_timings)
        widths = [len(w.get("task_ids", []) or []) for w in wave_timings]
        max_wave_width = max(widths) if widths else 0

    return round(speedup, 4), {
        "task_count": len(task_durations_ms),
        "max_wave_width": max_wave_width,
        "wave_count": wave_count,
        "sequential_ms": sequential_ms,
    }


def compute_fallback_save_rate(fallback_saves: int, total_runs: int) -> float:
    """Sprint 4 (2026-04-07): % de runs onde a fallback chain salvou >= 1 task.

    Score acumulativo via .kpi_history.jsonl. Mede a importancia da
    fallback chain estruturada — quanto maior, mais o sistema esta
    sendo salvo de falhas reais (Gemini 503, Claude timeout, etc.)
    e nao precisa de intervencao humana.
    """
    if total_runs == 0:
        return 0.0
    return round(fallback_saves / total_runs, 4)


def append_kpi_entry(
    *,
    demand: str,
    real_cost: float,
    estimated_cost: float,
    duration_ms: int,
    llm_usage: dict[str, int],
    tasks_completed: int,
    tasks_failed: int,
    fallback_saves: int = 0,
    quality_verdict: str | None = None,
    wave_timings: list[dict] | None = None,
    task_durations_ms: list[int] | None = None,
    history_path: Path | None = None,
) -> dict:
    """Acrescenta uma entrada ao .kpi_history.jsonl e retorna o registro escrito.

    Cria o arquivo se nao existir. JSONL append-only.

    Sprint 4 (2026-04-07): adiciona tier_internal_engagement_rate e
    fallback_chain_save_rate (acumulativo). Mantem retro compatibilidade
    com entries antigas (campos novos defaultam para 0/None se ausentes).
    """
    path = history_path or KPI_HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    health, health_meta = compute_distribution_health(llm_usage)
    accuracy = compute_cost_estimate_accuracy(real_cost, estimated_cost)
    tier_rate, tier_meta = compute_tier_internal_engagement_rate(llm_usage)
    qj_pass = compute_quality_judge_pass_rate(quality_verdict)
    par_eff, par_meta = compute_parallelism_efficiency(
        wave_timings, task_durations_ms, duration_ms
    )

    # Acumulado: le entries anteriores e soma fallback_saves
    prior = load_recent_entries(n=1000, history_path=path)
    total_runs_so_far = len(prior) + 1  # incluindo este
    cumulative_saves = sum(e.get("fallback_saves", 0) for e in prior) + fallback_saves
    save_rate = compute_fallback_save_rate(cumulative_saves, total_runs_so_far)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "demand": demand[:200],
        "distribution_health": health,
        "cost_estimate_accuracy": accuracy,
        "tier_internal_engagement_rate": tier_rate,
        "fallback_saves": fallback_saves,
        "fallback_chain_save_rate_cumulative": save_rate,
        # Sprint 5 (2026-04-08): 2 KPIs novos
        "quality_judge_pass": qj_pass,  # 1.0 / 0.0 / None por run
        "parallelism_efficiency": par_eff,  # speedup vs sequencial (>=1.0)
        "real_cost_usd": round(real_cost, 4),
        "estimated_cost_usd": round(estimated_cost, 4),
        "duration_ms": duration_ms,
        "tasks_completed": tasks_completed,
        "tasks_failed": tasks_failed,
        "llm_usage": dict(llm_usage),
        "_meta": {**health_meta, **tier_meta, **par_meta},
    }

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info(
        "KPI HISTORY: health=%.4f accuracy=%.2fx tier_engagement=%.0f%% fallback_saves=%d "
        "(cumulative_rate=%.0f%%) qj_pass=%s parallelism_efficiency=%.2fx",
        health, accuracy, tier_rate * 100, fallback_saves, save_rate * 100,
        ("-" if qj_pass is None else f"{qj_pass:.0f}"), par_eff,
    )
    return entry


def load_recent_entries(n: int = 10, history_path: Path | None = None) -> list[dict]:
    """Carrega os ultimos N registros do .kpi_history.jsonl.

    Returns vazio se o arquivo nao existir.
    """
    path = history_path or KPI_HISTORY_PATH
    if not path.exists():
        return []

    try:
        with open(path, encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        recent = lines[-n:]
        return [json.loads(line) for line in recent]
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Falha ao ler %s: %s", path, exc)
        return []


def detect_drift(history_path: Path | None = None) -> dict | None:
    """Verifica se os ULTIMOS DRIFT_THRESHOLD runs estao TODOS fora da banda.

    Returns:
        None se nao ha drift detectado.
        Dict com {alert, count, last_values, recommended_action} se ha drift.
    """
    recent = load_recent_entries(n=DRIFT_THRESHOLD, history_path=history_path)
    if len(recent) < DRIFT_THRESHOLD:
        return None  # ainda nao ha runs suficientes

    out_of_band = [
        e for e in recent
        if not (ACCURACY_BAND_LOW <= e["cost_estimate_accuracy"] <= ACCURACY_BAND_HIGH)
    ]
    if len(out_of_band) < DRIFT_THRESHOLD:
        return None  # nem todos os ultimos N estao fora — sem drift

    # Drift confirmado
    last_values = [round(e["cost_estimate_accuracy"], 2) for e in recent]
    avg = sum(last_values) / len(last_values)
    direction = "subestimando" if avg > ACCURACY_BAND_HIGH else "superestimando"

    alert = {
        "alert": "COST_ESTIMATE_DRIFT",
        "count": DRIFT_THRESHOLD,
        "last_values": last_values,
        "average": round(avg, 2),
        "direction": direction,
        "band": [ACCURACY_BAND_LOW, ACCURACY_BAND_HIGH],
        "recommended_action": (
            f"Recalibrar AVG_COST_PER_CALL em src/config.py. "
            f"Os ultimos {DRIFT_THRESHOLD} runs estao consistentemente {direction} "
            f"a estimativa (media={avg:.2f}x, banda saudavel={ACCURACY_BAND_LOW}-{ACCURACY_BAND_HIGH}x)."
        ),
    }
    logger.warning(
        "DRIFT ALERT: cost_estimate_accuracy fora da banda em %d/%d runs consecutivos. "
        "Ultimos valores: %s. Acao: %s",
        len(out_of_band), len(recent), last_values, alert["recommended_action"],
    )
    return alert

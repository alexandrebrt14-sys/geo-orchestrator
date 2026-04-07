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


def append_kpi_entry(
    *,
    demand: str,
    real_cost: float,
    estimated_cost: float,
    duration_ms: int,
    llm_usage: dict[str, int],
    tasks_completed: int,
    tasks_failed: int,
    history_path: Path | None = None,
) -> dict:
    """Acrescenta uma entrada ao .kpi_history.jsonl e retorna o registro escrito.

    Cria o arquivo se nao existir. JSONL append-only.
    """
    path = history_path or KPI_HISTORY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    health, health_meta = compute_distribution_health(llm_usage)
    accuracy = compute_cost_estimate_accuracy(real_cost, estimated_cost)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "demand": demand[:200],
        "distribution_health": health,
        "cost_estimate_accuracy": accuracy,
        "real_cost_usd": round(real_cost, 4),
        "estimated_cost_usd": round(estimated_cost, 4),
        "duration_ms": duration_ms,
        "tasks_completed": tasks_completed,
        "tasks_failed": tasks_failed,
        "llm_usage": dict(llm_usage),
        "_meta": health_meta,
    }

    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    logger.info(
        "KPI HISTORY: distribution_health=%.4f cost_estimate_accuracy=%.2fx (banda saudavel: %.1f-%.1fx)",
        health, accuracy, ACCURACY_BAND_LOW, ACCURACY_BAND_HIGH,
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

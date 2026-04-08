"""Adaptive AVG_COST_PER_CALL auto-calibration (sprint 5 — 2026-04-08).

Aprende o custo medio por LLM a partir do historico real de execucoes
(`output/execution_*.json`) e persiste em `output/.cost_calibration.json`.
A estimativa pre-execucao do orchestrator passa a usar esses valores
calibrados em vez dos defaults estaticos de `config.AVG_COST_PER_CALL`.

Motivacao: o sprint 4 ja ajustou AVG_COST_PER_CALL manualmente apos o
drift PARA BAIXO detectado pelo `cost_estimate_accuracy`. Calibrar
automaticamente a partir do historico fecha o loop — drift acima ou
abaixo da banda saudavel sera corrigido sem intervencao humana.

Estrategia:
- Pega os ultimos N execution reports (default 30)
- Para cada result com `cost > 0`, agrupa por `llm_used`
- Calcula a media por LLM (com fallback para o default estatico se
  amostra < MIN_SAMPLE)
- Persiste em `output/.cost_calibration.json` com timestamp + sample sizes
- Carrega no proximo run via `get_calibrated_avg_cost()`

A funcao retorna um dict que e merge:
    {**AVG_COST_PER_CALL_static, **calibrated_overrides}

Chamadas tipicas:
    from src.cost_calibrator import get_calibrated_avg_cost, recalibrate
    avg = get_calibrated_avg_cost()  # le do disco, fallback estatico
    recalibrate(window=30)  # reescaneia execution_*.json
"""
from __future__ import annotations

import glob
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import AVG_COST_PER_CALL, OUTPUT_DIR

logger = logging.getLogger(__name__)

CALIBRATION_PATH: Path = OUTPUT_DIR / ".cost_calibration.json"
EXECUTION_GLOB: str = "execution_*.json"

# Minimo de amostras por LLM para considerar a calibracao confiavel.
# Abaixo disso usa o valor estatico de config.AVG_COST_PER_CALL.
MIN_SAMPLE: int = 3

# Janela default de execution reports a varrer (recentes primeiro).
DEFAULT_WINDOW: int = 30

# Chao e teto para evitar que outliers dominem a media.
# Custo absurdamente baixo (< 0.0001) costuma ser cache_hit residual,
# absurdamente alto (> 5.0) e quase certamente um run de stress test.
COST_FLOOR: float = 0.0001
COST_CEILING: float = 5.0

# Sprint 7 (2026-04-08): safety threshold contra calibracao destrutiva.
# Identificado pela revisao critica t5 do orchestrator run #7: o auto-trigger
# de calibracao pode amplificar drift se uma janela de 30 reports tiver
# anomalia sistematica. Rejeitamos o valor calibrado se ele divergir do
# default estatico em mais de SAFETY_DEVIATION_MAX (default 5x acima ou
# 0.2x abaixo). Nesse caso, mantemos o default e logamos warning.
SAFETY_DEVIATION_MAX: float = 5.0  # calibrated > default * 5 = rejeitado
SAFETY_DEVIATION_MIN: float = 0.2  # calibrated < default * 0.2 = rejeitado

# Sprint 7: backup do .cost_calibration.json antes de cada recalibrate
# para permitir rollback manual via `cli.py finops calibrate-rollback`.
CALIBRATION_BACKUP_PATH: Path = OUTPUT_DIR / ".cost_calibration.backup.json"


def _scan_execution_reports(
    output_dir: Path,
    window: int,
) -> list[Path]:
    """Retorna os ultimos `window` execution_*.json ordenados do mais recente."""
    pattern = str(output_dir / EXECUTION_GLOB)
    files = sorted(glob.glob(pattern), reverse=True)
    return [Path(p) for p in files[:window]]


def _load_costs_by_llm(
    report_paths: list[Path],
) -> dict[str, list[float]]:
    """Le os reports e devolve {llm_name: [cost1, cost2, ...]}."""
    by_llm: dict[str, list[float]] = {}
    for path in report_paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("calibrator: pulando %s (%s)", path.name, exc)
            continue
        results = payload.get("results", {}) or {}
        # Sprint 6: aceita tanto dict (formato canonico do orchestrator)
        # quanto list (formato legado de cli._save_report v1.0).
        if isinstance(results, list):
            iterable = results
        elif isinstance(results, dict):
            iterable = list(results.values())
        else:
            continue
        for r in iterable:
            if not isinstance(r, dict):
                continue
            llm = r.get("llm_used")
            cost = r.get("cost", 0.0) or 0.0
            cache_hit = r.get("cache_hit", False)
            if cache_hit or not llm:
                continue
            if cost < COST_FLOOR or cost > COST_CEILING:
                continue
            by_llm.setdefault(llm, []).append(float(cost))
    return by_llm


def recalibrate(
    window: int = DEFAULT_WINDOW,
    output_dir: Path | None = None,
    persist: bool = True,
) -> dict:
    """Recalcula AVG_COST_PER_CALL e persiste em CALIBRATION_PATH.

    Returns:
        Dict com {avg_cost_per_call, sample_sizes, window, last_calibrated_at}.
    """
    out = output_dir or OUTPUT_DIR
    paths = _scan_execution_reports(out, window=window)
    by_llm = _load_costs_by_llm(paths)

    calibrated: dict[str, float] = {}
    sample_sizes: dict[str, int] = {}
    safety_rejections: list[dict] = []
    for llm, costs in by_llm.items():
        sample_sizes[llm] = len(costs)
        if len(costs) < MIN_SAMPLE:
            continue
        # Media simples; outliers ja filtrados pelos floors/ceilings.
        candidate = round(sum(costs) / len(costs), 6)
        # Sprint 7 (2026-04-08): safety threshold — rejeita calibracoes que
        # divergem absurdamente do default estatico (proteje contra runs
        # anomalos dominando uma janela de 30).
        default = AVG_COST_PER_CALL.get(llm)
        if default and default > 0:
            ratio = candidate / default
            if ratio > SAFETY_DEVIATION_MAX or ratio < SAFETY_DEVIATION_MIN:
                safety_rejections.append({
                    "llm": llm,
                    "candidate": candidate,
                    "default": default,
                    "ratio": round(ratio, 4),
                    "reason": (
                        f"ratio {ratio:.2f}x fora da banda "
                        f"[{SAFETY_DEVIATION_MIN}, {SAFETY_DEVIATION_MAX}]"
                    ),
                })
                logger.warning(
                    "calibrator: rejeitando %s (candidate=%.6f, default=%.6f, ratio=%.2fx)",
                    llm, candidate, default, ratio,
                )
                continue
        calibrated[llm] = candidate

    payload = {
        "last_calibrated_at": datetime.now(timezone.utc).isoformat(),
        "window": window,
        "min_sample": MIN_SAMPLE,
        "sources_scanned": len(paths),
        "sample_sizes": sample_sizes,
        "calibrated_avg_cost_per_call": calibrated,
        "static_defaults": dict(AVG_COST_PER_CALL),
        # Sprint 7: rastreio das rejeicoes do safety threshold
        "safety_rejections": safety_rejections,
    }

    if persist:
        out.mkdir(parents=True, exist_ok=True)
        # Sprint 7 (2026-04-08): backup do calibration anterior antes de
        # sobrescrever — permite rollback manual via finops calibrate-rollback.
        if CALIBRATION_PATH.exists():
            try:
                CALIBRATION_BACKUP_PATH.write_text(
                    CALIBRATION_PATH.read_text(encoding="utf-8"), encoding="utf-8"
                )
            except OSError as exc:
                logger.warning("calibrator: backup falhou (continuando): %s", exc)
        CALIBRATION_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "calibrator: recalibrado de %d reports — %d LLMs com amostra >= %d (%d safety rejections)",
            len(paths), len(calibrated), MIN_SAMPLE, len(safety_rejections),
        )
    return payload


def rollback_calibration() -> bool:
    """Sprint 7: restaura o backup do .cost_calibration.json se existir.

    Returns:
        True se o rollback foi aplicado; False se nao havia backup.
    """
    if not CALIBRATION_BACKUP_PATH.exists():
        logger.warning("calibrator: nenhum backup para rollback em %s", CALIBRATION_BACKUP_PATH)
        return False
    try:
        CALIBRATION_PATH.write_text(
            CALIBRATION_BACKUP_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        logger.info("calibrator: rollback aplicado a partir de %s", CALIBRATION_BACKUP_PATH)
        return True
    except OSError as exc:
        logger.error("calibrator: rollback falhou: %s", exc)
        return False


def load_calibration() -> dict | None:
    """Le CALIBRATION_PATH se existir."""
    if not CALIBRATION_PATH.exists():
        return None
    try:
        return json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("calibrator: falha ao ler %s: %s", CALIBRATION_PATH, exc)
        return None


def get_calibrated_avg_cost() -> dict[str, float]:
    """Retorna AVG_COST_PER_CALL com overrides calibrados (se houver).

    Sempre devolve um dict completo com todos os LLMs do default estatico.
    LLMs nao calibrados (amostra insuficiente) ficam com o valor estatico.
    """
    merged: dict[str, float] = dict(AVG_COST_PER_CALL)
    payload = load_calibration()
    if not payload:
        return merged
    overrides = payload.get("calibrated_avg_cost_per_call", {}) or {}
    for llm, value in overrides.items():
        if isinstance(value, (int, float)) and value > 0:
            merged[llm] = float(value)
    return merged

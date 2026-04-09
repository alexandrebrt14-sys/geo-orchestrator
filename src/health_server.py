"""HTTP /health endpoint (sprint 7 — 2026-04-08).

Servidor HTTP minimal usando stdlib (zero dependencias adicionais) que
expoe os mesmos checks do `cli.py doctor` mas em formato pollable —
adequado para load balancers, Kubernetes liveness/readiness probes e
o dashboard web da sprint 7.

Endpoints:
    GET /health      -> 200 (OK), 200 (ATENCAO), 503 (CRITICO)
    GET /metrics     -> KPI snapshot dos ultimos N runs
    GET /            -> redirect / docs minimos

Decisao: stdlib em vez de fastapi/starlette para nao adicionar dependencia
ao projeto. Para producao com volume real, trocar por uvicorn+fastapi e
manter o mesmo contrato JSON.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Marca de inicio do processo para calculo de uptime
_PROCESS_START = time.time()


def _build_health_payload() -> tuple[dict[str, Any], str]:
    """Constroi o payload de /health reusando os checks do cli doctor.

    Returns:
        Tupla (payload, overall_status). Status: OK | ATENCAO | CRITICO.
    """
    from .config import LLM_CONFIGS
    from .kpi_history import KPI_HISTORY_PATH, load_recent_entries, detect_drift
    from .cost_calibrator import load_calibration

    checks: list[dict] = []
    import os

    # 1. API keys
    missing = [n for n, c in LLM_CONFIGS.items() if not os.environ.get(c.api_key_env)]
    checks.append({
        "name": "api_keys",
        "status": "OK" if not missing else "CRITICO",
        "detail": f"{len(LLM_CONFIGS) - len(missing)}/{len(LLM_CONFIGS)} configurados",
        "missing": missing,
    })

    # 2. Catalog consistency
    try:
        from .catalog_loader import validate_catalog_vs_config
        errors = validate_catalog_vs_config()
        checks.append({
            "name": "catalog_consistency",
            "status": "OK" if not errors else "ATENCAO",
            "detail": "alinhado" if not errors else f"{len(errors)} divergencias",
            "errors": errors[:3],
        })
    except Exception as exc:
        checks.append({"name": "catalog_consistency", "status": "ATENCAO",
                       "detail": f"validator falhou: {exc}"})

    # 3. FinOps
    try:
        from .finops import get_finops
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
            status = "OK"
        elif max_pct < 95:
            status = "ATENCAO"
        else:
            status = "CRITICO"
        checks.append({
            "name": "finops_daily",
            "status": status,
            "detail": f"max {max_pct:.0f}% ({offender})",
            "max_pct": max_pct,
            "offender": offender,
        })
    except Exception as exc:
        checks.append({"name": "finops_daily", "status": "ATENCAO",
                       "detail": f"finops inacessivel: {exc}"})

    # 4. KPI history snapshot
    kpi_snapshot = None
    try:
        if KPI_HISTORY_PATH.exists():
            entries = load_recent_entries(n=5)
            if entries:
                last = entries[-1]
                kpi_snapshot = {
                    "last_timestamp": last.get("timestamp"),
                    "distribution_health": last.get("distribution_health"),
                    "cost_estimate_accuracy": last.get("cost_estimate_accuracy"),
                    "tier_internal_engagement_rate": last.get("tier_internal_engagement_rate"),
                    "quality_judge_pass": last.get("quality_judge_pass"),
                    "parallelism_efficiency": last.get("parallelism_efficiency"),
                    "real_cost_usd": last.get("real_cost_usd"),
                    "n_recent": len(entries),
                }
                checks.append({"name": "kpi_history", "status": "OK",
                               "detail": f"{len(entries)} runs recentes"})
            else:
                checks.append({"name": "kpi_history", "status": "ATENCAO",
                               "detail": "historico vazio"})
        else:
            checks.append({"name": "kpi_history", "status": "ATENCAO",
                           "detail": "nenhum historico"})
    except Exception as exc:
        checks.append({"name": "kpi_history", "status": "ATENCAO",
                       "detail": f"kpi load falhou: {exc}"})

    # 5. Cost calibration
    cal = load_calibration()
    last_calibration = None
    if cal:
        last_calibration = cal.get("last_calibrated_at")
        try:
            last_dt = datetime.fromisoformat(last_calibration.replace("Z", "+00:00"))
            age_days = (datetime.now(timezone.utc) - last_dt).days
        except Exception:
            age_days = 999
        if age_days <= 7:
            cal_status = "OK"
        elif age_days <= 30:
            cal_status = "ATENCAO"
        else:
            cal_status = "CRITICO"
        checks.append({
            "name": "cost_calibration",
            "status": cal_status,
            "detail": f"{len(cal.get('calibrated_avg_cost_per_call', {}))} LLMs, {age_days}d",
            "age_days": age_days,
        })
    else:
        checks.append({"name": "cost_calibration", "status": "ATENCAO",
                       "detail": "nunca calibrado"})

    # 6. Drift detector
    try:
        drift = detect_drift()
        if drift is None:
            checks.append({"name": "drift_detector", "status": "OK",
                           "detail": "dentro da banda 0.7-1.5"})
        else:
            checks.append({"name": "drift_detector", "status": "ATENCAO",
                           "detail": f"{drift['direction']} {drift['average']:.2f}x"})
    except Exception as exc:
        checks.append({"name": "drift_detector", "status": "ATENCAO",
                       "detail": f"drift falhou: {exc}"})

    # Overall
    has_critical = any(c["status"] == "CRITICO" for c in checks)
    has_warning = any(c["status"] == "ATENCAO" for c in checks)
    overall = "CRITICO" if has_critical else ("ATENCAO" if has_warning else "OK")

    payload = {
        "service": "geo-orchestrator",
        "version": "2.0",
        "overall": overall,
        "uptime_seconds": int(time.time() - _PROCESS_START),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_status": {
            "n_configured": len(LLM_CONFIGS),
            "n_with_keys": len(LLM_CONFIGS) - len(missing),
        },
        "last_calibration_at": last_calibration,
        "kpi_snapshot": kpi_snapshot,
        "checks": checks,
    }
    return payload, overall


def _build_metrics_payload(n: int = 20) -> dict:
    """Constroi /metrics — serie temporal dos ultimos N runs do .kpi_history.jsonl."""
    from .kpi_history import load_recent_entries
    entries = load_recent_entries(n=n)
    return {
        "service": "geo-orchestrator",
        "n_entries": len(entries),
        "entries": [
            {
                "timestamp": e.get("timestamp"),
                "distribution_health": e.get("distribution_health"),
                "cost_estimate_accuracy": e.get("cost_estimate_accuracy"),
                "tier_internal_engagement_rate": e.get("tier_internal_engagement_rate"),
                "quality_judge_pass": e.get("quality_judge_pass"),
                "parallelism_efficiency": e.get("parallelism_efficiency"),
                "real_cost_usd": e.get("real_cost_usd"),
                "estimated_cost_usd": e.get("estimated_cost_usd"),
                "duration_ms": e.get("duration_ms"),
                "tasks_completed": e.get("tasks_completed"),
                "tasks_failed": e.get("tasks_failed"),
                "llm_usage": e.get("llm_usage", {}),
            }
            for e in entries
        ],
    }


def _check_auth(request_headers, path: str) -> tuple[bool, str]:
    """Valida bearer token opcional contra GEO_HEALTH_TOKEN env var.

    Achado F40 da auditoria 2026-04-08: antes deste guard, /health expunha
    metricas FinOps, KPIs, ultimos custos e estado de calibracao SEM
    autenticacao. Como o servidor pode rodar em 0.0.0.0 (lb/k8s) e nao
    so localhost, isso vazava informacao sensivel.

    Comportamento:
    - Se GEO_HEALTH_TOKEN nao estiver setado: endpoint publico (compat
      backward com setups existentes que dependem de polling sem auth)
    - Se setado: requer header `Authorization: Bearer <token>` com
      comparacao timing-safe via hmac.compare_digest

    Endpoint `/` (root docs) eh sempre publico — apenas mostra os paths
    disponiveis, sem dado sensivel.

    Returns:
        Tupla (autorizado, motivo). Motivo eh string vazia quando ok.
    """
    import hmac
    import os

    expected = os.environ.get("GEO_HEALTH_TOKEN", "").strip()
    if not expected:
        return True, ""  # auth desabilitada (compat)
    if path in ("", "/"):
        return True, ""  # docs publicas

    auth_header = request_headers.get("Authorization", "")
    if not auth_header:
        return False, "missing Authorization header"
    if not auth_header.startswith("Bearer "):
        return False, "Authorization must be Bearer <token>"
    received = auth_header[len("Bearer "):].strip()
    if not hmac.compare_digest(received, expected):
        return False, "invalid token"
    return True, ""


class HealthHandler(BaseHTTPRequestHandler):
    """Handler stdlib que responde /health, /metrics, /."""

    def log_message(self, format, *args):  # silenciar stdlib log
        logger.info("HTTP %s - %s", self.address_string(), format % args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")

        # Auth bearer opcional (achado F40)
        ok, reason = _check_auth(self.headers, path)
        if not ok:
            self.send_response(401)
            self.send_header(
                "WWW-Authenticate",
                'Bearer realm="geo-orchestrator-health"',
            )
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                json.dumps({"error": "unauthorized", "detail": reason}).encode("utf-8")
            )
            return

        if path in ("", "/"):
            self._send_json(200, {
                "service": "geo-orchestrator",
                "endpoints": {
                    "/health": "health checks (200 OK/ATENCAO, 503 CRITICO)",
                    "/metrics": "KPI timeseries (last 20 runs)",
                },
                "auth": "bearer optional via GEO_HEALTH_TOKEN env var",
            })
            return
        if path == "/health":
            payload, overall = _build_health_payload()
            status = 503 if overall == "CRITICO" else 200
            self._send_json(status, payload)
            return
        if path == "/metrics":
            self._send_json(200, _build_metrics_payload(n=20))
            return
        self._send_json(404, {"error": "not_found", "path": self.path})


def run_server(host: str = "0.0.0.0", port: int = 8080) -> None:
    """Sobe ThreadingHTTPServer bloqueante. Util para `cli.py serve`."""
    server = ThreadingHTTPServer((host, port), HealthHandler)
    logger.info("geo-orchestrator health server em http://%s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("encerrando health server")
    finally:
        server.server_close()

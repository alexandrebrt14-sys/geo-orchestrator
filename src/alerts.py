"""Sistema de alertas FinOps — WhatsApp + email.

Quando um provider ou o budget global atinge o threshold de alerta/bloqueio,
este modulo notifica via:
1. WhatsApp Business API (WHATSAPP_API_TOKEN + WHATSAPP_PHONE_ID)
2. Email via Resend (RESEND_API_KEY)

Tem dedup de 1 hora por (provider, severidade) para evitar spam.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

import httpx

from .config import OUTPUT_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Destinatarios (hardcoded por requisito explicito)
# ---------------------------------------------------------------------------
ALERT_WHATSAPP_TO = "5562998141505"  # +55 62 99814-1505
ALERT_EMAIL_TO = "caramaschiai@caramaschiai.io"
ALERT_EMAIL_FROM = os.environ.get("RESEND_FROM_EMAIL", "alerts@brasilgeo.ai")

# ---------------------------------------------------------------------------
# Dedup state — evita spam (1 alerta por hora por chave)
# ---------------------------------------------------------------------------
_DEDUP_PATH: Path = OUTPUT_DIR / ".finops" / "alerts_dedup.json"
_DEDUP_WINDOW_SECONDS = 3600  # 1 hora

Severity = Literal["warning", "block"]


def _load_dedup() -> dict[str, float]:
    """Carrega timestamps dos ultimos alertas enviados."""
    if not _DEDUP_PATH.exists():
        return {}
    try:
        return json.loads(_DEDUP_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_dedup(state: dict[str, float]) -> None:
    """Persiste estado de dedup."""
    _DEDUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    _DEDUP_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _should_send(key: str) -> bool:
    """Retorna True se ja passou _DEDUP_WINDOW_SECONDS desde o ultimo envio dessa chave."""
    state = _load_dedup()
    last = state.get(key, 0.0)
    if time.time() - last < _DEDUP_WINDOW_SECONDS:
        return False
    state[key] = time.time()
    _save_dedup(state)
    return True


# ---------------------------------------------------------------------------
# WhatsApp via WhatsApp Business API
# ---------------------------------------------------------------------------

def send_whatsapp_alert(message: str, to: str = ALERT_WHATSAPP_TO) -> bool:
    """Envia mensagem de alerta via WhatsApp Business API."""
    token = os.environ.get("WHATSAPP_API_TOKEN")
    phone_id = os.environ.get("WHATSAPP_PHONE_ID")
    if not token or not phone_id:
        logger.warning("WhatsApp alert nao enviado: WHATSAPP_API_TOKEN ou WHATSAPP_PHONE_ID ausentes")
        return False

    digits = "".join(c for c in to if c.isdigit())
    if not digits.startswith("55"):
        digits = f"55{digits}"

    url = f"https://graph.facebook.com/v18.0/{phone_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": digits,
        "type": "text",
        "text": {"body": message[:4000]},
    }
    try:
        r = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if r.status_code in (200, 201):
            logger.info("WhatsApp alert enviado para %s", digits)
            return True
        logger.error("WhatsApp alert HTTP %d: %s", r.status_code, r.text[:300])
        return False
    except Exception as exc:
        logger.error("Falha enviando WhatsApp alert: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Email via Resend
# ---------------------------------------------------------------------------

def send_email_alert(subject: str, body_html: str, to: str = ALERT_EMAIL_TO) -> bool:
    """Envia email de alerta via Resend."""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        logger.warning("Email alert nao enviado: RESEND_API_KEY ausente")
        return False

    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            json={
                "from": ALERT_EMAIL_FROM,
                "to": [to],
                "subject": subject,
                "html": body_html,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15,
        )
        if r.status_code in (200, 202):
            logger.info("Email alert enviado para %s", to)
            return True
        logger.error("Email alert HTTP %d: %s", r.status_code, r.text[:300])
        return False
    except Exception as exc:
        logger.error("Falha enviando email alert: %s", exc)
        return False


# ---------------------------------------------------------------------------
# API publica
# ---------------------------------------------------------------------------

def fire_finops_alert(
    severity: Severity,
    provider: str,
    spent: float,
    limit: float,
    is_global: bool = False,
) -> None:
    """Dispara alerta FinOps via WhatsApp + email com dedup de 1 hora.

    Args:
        severity: 'warning' (>=80%) ou 'block' (>=95%).
        provider: 'anthropic', 'openai', etc. ou '_global'.
        spent: gasto atual em USD.
        limit: limite em USD.
        is_global: True se for o limite global agregado.
    """
    dedup_key = f"{provider}:{severity}"
    if not _should_send(dedup_key):
        logger.debug("FinOps alert dedup: %s (ja enviado nas ultimas 1h)", dedup_key)
        return

    pct = (spent / limit * 100) if limit > 0 else 0
    scope = "GLOBAL" if is_global else provider.upper()
    severity_label = "ALERTA" if severity == "warning" else "BLOQUEIO"

    # WhatsApp — texto puro, sem emoji
    whatsapp_msg = (
        f"FinOps Brasil GEO {severity_label}\n"
        f"Escopo: {scope}\n"
        f"Gasto hoje: US$ {spent:.4f} de US$ {limit:.2f}\n"
        f"Uso: {pct:.0f}%\n"
        f"Acao: " + (
            "verificar console e revisar limites" if severity == "warning"
            else "novas chamadas serao bloqueadas automaticamente"
        )
    )

    # Email — HTML
    email_subject = f"[FinOps {severity_label}] {scope} em {pct:.0f}% do limite"
    email_html = f"""
    <h2>FinOps Brasil GEO — {severity_label}</h2>
    <table style="border-collapse:collapse;font-family:sans-serif;">
      <tr><td><b>Escopo</b></td><td>{scope}</td></tr>
      <tr><td><b>Gasto hoje</b></td><td>US$ {spent:.4f}</td></tr>
      <tr><td><b>Limite</b></td><td>US$ {limit:.2f}</td></tr>
      <tr><td><b>Uso</b></td><td>{pct:.0f}%</td></tr>
      <tr><td><b>Severidade</b></td><td>{severity}</td></tr>
    </table>
    <p>{'Verifique o console e revise os limites se necessario.' if severity == 'warning' else 'Novas chamadas para esse provider serao bloqueadas automaticamente ate que o limite seja restaurado.'}</p>
    <p style="color:#666;font-size:11px;">Disparado pelo geo-orchestrator FinOps tracker.</p>
    """

    send_whatsapp_alert(whatsapp_msg)
    send_email_alert(email_subject, email_html)

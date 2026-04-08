"""Adapter para o tracker centralizado geo-finops.

Integracao thin: o pipeline existente continua chamando finops.record_cost(),
e este adapter ALEM disso escreve no calls.db central.

Mantem compatibilidade reversa: nada quebra se o pacote geo_finops nao estiver
disponivel — o adapter so faz no-op.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Garante que geo_finops esta no PYTHONPATH (caminho conhecido)
_GEO_FINOPS_PATH = Path("C:/Sandyboxclaude/geo-finops")
if _GEO_FINOPS_PATH.exists() and str(_GEO_FINOPS_PATH) not in sys.path:
    sys.path.insert(0, str(_GEO_FINOPS_PATH))

try:
    from geo_finops import track_call as _track_call
    _AVAILABLE = True
except ImportError as exc:
    logger.warning("geo_finops nao disponivel: %s", exc)
    _AVAILABLE = False
    _track_call = None


PROJECT_NAME = "geo-orchestrator"


def record_to_unified(
    task_id: str,
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    run_id: str | None = None,
    task_type: str | None = None,
    success: bool = True,
) -> None:
    """Grava no calls.db central. No-op se geo_finops nao esta disponivel."""
    if not _AVAILABLE:
        return
    try:
        _track_call(
            project=PROJECT_NAME,
            model_id=model_id,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            run_id=f"{run_id}:{task_id}" if run_id else task_id,
            task_type=task_type,
            success=success,
        )
    except Exception as exc:
        logger.error("unified_finops: track_call falhou: %s", exc)

"""geo_orchestrator_sdk — API publica para consumers internos.

Achado B-019 da auditoria de ecossistema 2026-04-08 (Onda 3 filtrada
para projeto solo). Antes deste shim, os consumers internos do
ecossistema (papers, curso-factory, caramaschi) chamavam APIs LLM
direto via httpx — bypass total do orchestrator. Resultado: cap 80%
por provider, fallback chain, semantic cache, quality judge e tier
routing nao beneficiavam nenhum dos consumers.

Este modulo oferece uma fachada minima e estavel para que os consumers
possam importar e usar o orchestrator sem precisar conhecer a
estrutura interna de src/. Apenas a superficie publica e exportada.

Uso programatico em outro projeto:

    # Path-based install (sem PyPI)
    import sys
    sys.path.insert(0, "/path/to/geo-orchestrator")
    from geo_orchestrator_sdk import Orchestrator, run

    # Usa async API direto
    import asyncio
    report = asyncio.run(run("Pesquisar GEO no Brasil"))
    print(report.summary)

    # OU usa Orchestrator class para mais controle
    orch = Orchestrator(force=False, smart=True)
    report = await orch.run("Demanda")

API surface (estavel, semver):
- Orchestrator           classe principal
- run(demand)            funcao top-level (sync wrapper sobre asyncio)
- run_async(demand)      versao async pura
- get_health_status()    snapshot de health
- get_finops_status()    snapshot de finops por provider
- get_prompt_metadata()  SHA do prompt + metadata (B-009)
- __version__            string da versao do SDK
- ExecutionReport        modelo de retorno
- BudgetExceededError    excecao especifica

Versionamento: este modulo segue SemVer independente do orchestrator
interno. Mudancas em src/ que nao quebram a API publica nao afetam
este SDK. Mudancas que quebram a API exigem bump de major.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Importa a API interna mantendo o shim independente
# do CWD do consumer
import os
import sys
from pathlib import Path

_SDK_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SDK_DIR.parent
sys.path.insert(0, str(_REPO_ROOT))

# Garante chaves dummy quando o consumer importa em ambiente sem
# .env (ex: smoke imports). Isso evita ImportError em scripts
# auxiliares; o orchestrator ainda valida na hora da chamada real.
for _k in (
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
    "PERPLEXITY_API_KEY", "GROQ_API_KEY",
):
    os.environ.setdefault(_k, "")

# ─── Imports da API interna ────────────────────────────────────────────────

from src.orchestrator import Orchestrator as _Orchestrator  # noqa: E402
from src.models import ExecutionReport  # noqa: E402
from src.finops import BudgetExceededError  # noqa: E402

try:
    from src.prompt_registry import (  # noqa: E402
        PIPELINE_SYSTEM_BASE_SHA256,
        get_prompt_metadata as _get_prompt_metadata_internal,
    )
    _HAS_PROMPT_REGISTRY = True
except ImportError:
    _HAS_PROMPT_REGISTRY = False

# ─── Versao do SDK ─────────────────────────────────────────────────────────

__version__ = "0.1.0"


# ─── Re-exports limpos ─────────────────────────────────────────────────────


class Orchestrator(_Orchestrator):
    """Orchestrator multi-LLM da Brasil GEO.

    Ja inclui SmartRouter, FinOps cap 80%, semantic cache, fallback
    chain, quality judge e tier routing. Esta classe eh um re-export
    direto da implementacao interna em src/orchestrator.py — toda a
    API publica do __init__ original esta disponivel.

    Uso minimo:

        orch = Orchestrator(force=False, smart=True)
        report = await orch.run("sua demanda em linguagem natural")
        print(f"Custo real: ${report.total_cost:.4f}")
        print(f"Tasks completas: {report.tasks_completed}")
    """

    pass


async def run_async(
    demand: str,
    force: bool = False,
    smart: bool = True,
) -> ExecutionReport:
    """Roda o pipeline completo para uma demanda em linguagem natural.

    Args:
        demand: Demanda em PT-BR ou EN. Ex: "Pesquisar GEO no Brasil
                e escrever um artigo de 500 palavras".
        force: Se True, ignora budget guard (BUDGET_LIMIT). Use com
                cautela em producao.
        smart: Se True (default), usa SmartRouter. Se False, usa router
                classico (debug).

    Returns:
        ExecutionReport com plan, results, total_cost, total_duration_ms
        e prompt_metadata (SHA do prompt usado para auditoria).

    Raises:
        BudgetExceededError: Se a estimativa de custo exceder
                BUDGET_LIMIT (a menos que force=True).
    """
    orch = Orchestrator(force=force, smart=smart)
    return await orch.run(demand)


def run(
    demand: str,
    force: bool = False,
    smart: bool = True,
) -> ExecutionReport:
    """Versao sincrona de run_async — wrapper sobre asyncio.run.

    Use esta funcao em scripts simples que nao precisam de async.
    Para integracao em pipelines async, prefira run_async.
    """
    return asyncio.run(run_async(demand, force=force, smart=smart))


def get_prompt_metadata() -> dict:
    """Retorna metadata do prompt principal (SHA-256 + bytes + path).

    Util para auditoria ex-post: dado um execution_*.json, recuperar
    exatamente qual versao do prompt produziu o output.

    Returns:
        Dict com pipeline_system_base_sha256, pipeline_system_base_bytes,
        templates_dir. Vazio se prompt_registry nao estiver disponivel.
    """
    if _HAS_PROMPT_REGISTRY:
        return _get_prompt_metadata_internal()
    return {}


def get_finops_status() -> dict:
    """Retorna snapshot do estado FinOps por provider.

    Inclui: gasto diario por provider, percentual do limite, status
    (OK/ATENCAO/CRITICO/BLOQUEADO), reset hour, e calibracao recente.
    """
    from src.finops import get_finops
    fo = get_finops()
    return fo.daily_status()


def get_health_status() -> dict[str, Any]:
    """Snapshot de health checks (api_keys, catalog, finops, kpi, drift).

    Retorna o mesmo dict que GET /health do health_server, mas em-process
    sem precisar levantar o servidor HTTP.
    """
    from src.health_server import _build_health_payload
    payload, overall = _build_health_payload()
    payload["overall"] = overall
    return payload


# ─── Atributos publicos ────────────────────────────────────────────────────


__all__ = [
    "__version__",
    "Orchestrator",
    "ExecutionReport",
    "BudgetExceededError",
    "run",
    "run_async",
    "get_prompt_metadata",
    "get_finops_status",
    "get_health_status",
]

if _HAS_PROMPT_REGISTRY:
    __all__.append("PIPELINE_SYSTEM_BASE_SHA256")

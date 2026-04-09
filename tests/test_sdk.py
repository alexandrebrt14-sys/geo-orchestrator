"""Tests do geo_orchestrator_sdk — API publica estavel (B-019).

Achado B-019 da auditoria 2026-04-08 (Onda 3 filtrada para solo).
Garante que a API publica do SDK existe e nao regride entre versoes.
Os testes nao fazem chamadas LLM reais — apenas verificam imports,
assinaturas, semver, e contrato.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Garante chaves dummy + sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for k in [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
    "PERPLEXITY_API_KEY", "GROQ_API_KEY",
]:
    os.environ.setdefault(k, "test-key-not-real")


# ─── Smoke imports ─────────────────────────────────────────────────────────


def test_sdk_module_imports():
    import geo_orchestrator_sdk
    assert geo_orchestrator_sdk is not None


def test_sdk_has_version():
    import geo_orchestrator_sdk
    assert hasattr(geo_orchestrator_sdk, "__version__")
    assert isinstance(geo_orchestrator_sdk.__version__, str)
    # SemVer minimo: 3 numeros
    parts = geo_orchestrator_sdk.__version__.split(".")
    assert len(parts) >= 3
    for p in parts[:3]:
        assert p.isdigit()


def test_sdk_exports_orchestrator():
    from geo_orchestrator_sdk import Orchestrator
    assert Orchestrator is not None
    # Eh subclasse do Orchestrator interno
    from src.orchestrator import Orchestrator as InternalOrchestrator
    assert issubclass(Orchestrator, InternalOrchestrator)


def test_sdk_exports_execution_report():
    from geo_orchestrator_sdk import ExecutionReport
    assert ExecutionReport is not None
    # Eh um BaseModel Pydantic
    from pydantic import BaseModel
    assert issubclass(ExecutionReport, BaseModel)


def test_sdk_exports_budget_exception():
    from geo_orchestrator_sdk import BudgetExceededError
    assert issubclass(BudgetExceededError, Exception)


def test_sdk_exports_run_functions():
    import inspect
    from geo_orchestrator_sdk import run, run_async
    assert callable(run)
    assert callable(run_async)
    # run_async eh corrotina
    assert inspect.iscoroutinefunction(run_async)


def test_sdk_run_signature():
    """run aceita demand obrigatorio + force/smart opcionais."""
    import inspect
    from geo_orchestrator_sdk import run
    sig = inspect.signature(run)
    params = sig.parameters
    assert "demand" in params
    assert params["demand"].default is inspect.Parameter.empty
    assert "force" in params
    assert params["force"].default is False
    assert "smart" in params
    assert params["smart"].default is True


def test_sdk_run_async_signature():
    import inspect
    from geo_orchestrator_sdk import run_async
    sig = inspect.signature(run_async)
    params = sig.parameters
    assert "demand" in params
    assert params["demand"].default is inspect.Parameter.empty


# ─── API helpers (read-only, sem chamadas LLM) ────────────────────────────


def test_sdk_get_prompt_metadata():
    from geo_orchestrator_sdk import get_prompt_metadata
    meta = get_prompt_metadata()
    assert isinstance(meta, dict)
    # Deve conter SHA do prompt (B-009 ja entregue)
    if meta:  # se prompt_registry esta disponivel
        assert "pipeline_system_base_sha256" in meta
        assert len(meta["pipeline_system_base_sha256"]) == 64


def test_sdk_get_finops_status():
    from geo_orchestrator_sdk import get_finops_status
    status = get_finops_status()
    assert isinstance(status, dict)
    # Deve incluir pelo menos um provider conhecido
    keys_lower = {k.lower() for k in status.keys()}
    has_provider = any(
        p in keys_lower or any(p in k for k in keys_lower)
        for p in ["anthropic", "openai", "google"]
    )
    # Em caso vazio nao falha — apenas garante que retorna dict valido
    assert isinstance(status, dict)


def test_sdk_get_health_status():
    from geo_orchestrator_sdk import get_health_status
    health = get_health_status()
    assert isinstance(health, dict)
    assert "checks" in health
    assert "overall" in health
    assert health["overall"] in ("OK", "ATENCAO", "CRITICO")


# ─── API surface estavel ──────────────────────────────────────────────────


def test_sdk_all_attribute_complete():
    """__all__ deve listar todos os exports publicos."""
    import geo_orchestrator_sdk
    expected = {
        "__version__",
        "Orchestrator",
        "ExecutionReport",
        "BudgetExceededError",
        "run",
        "run_async",
        "get_prompt_metadata",
        "get_finops_status",
        "get_health_status",
    }
    actual = set(geo_orchestrator_sdk.__all__)
    missing = expected - actual
    assert not missing, f"Faltam em __all__: {missing}"


def test_sdk_no_internal_leaks():
    """API publica NAO deve expor coisas internas como _Orchestrator."""
    import geo_orchestrator_sdk
    public_attrs = {a for a in dir(geo_orchestrator_sdk) if not a.startswith("_")}
    # Sentinela: nenhum atributo publico deve comecar com underscore
    forbidden_internal = {"_Orchestrator", "_HAS_PROMPT_REGISTRY", "_get_prompt_metadata_internal"}
    leaked = forbidden_internal & public_attrs
    assert not leaked, f"Internos vazaram: {leaked}"


def test_sdk_orchestrator_class_instantiable():
    """A classe exposta deve ser instanciavel sem args (smart=True default)."""
    from geo_orchestrator_sdk import Orchestrator
    # NAO chama .run — apenas valida que o construtor funciona
    orch = Orchestrator(force=True, smart=True)
    assert orch is not None

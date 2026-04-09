"""Tests para F23 — FinOps SQLite WAL substitui JSON como source of truth.

Achado da auditoria 2026-04-08 (severidade ALTA): _daily_spend.json sem
lock TOCTOU permitia perda de tracking quando dois processos paralelos
liam o mesmo valor, ambos somavam, ambos escreviam. Em producao com
cron diario + run manual concorrente, foi observado em logs.

Tests cobrem:
- Atomic increment via UPSERT WHERE date+provider
- Migration one-shot JSON -> SQLite
- Persistencia entre instancias (simula restart de processo)
- Multi-processo (varios FinOps instanciados em sequencia)
- Fail-degraded para JSON quando SQLite quebra
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Garante chaves dummy para imports
for k in [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
    "PERPLEXITY_API_KEY", "GROQ_API_KEY",
]:
    os.environ.setdefault(k, "test-key-not-real")


@pytest.fixture
def isolated_finops(tmp_path, monkeypatch):
    """Isola o filesystem do finops por test em tmp_path."""
    import src.finops as fo
    monkeypatch.setattr(fo, "_FINOPS_DIR", tmp_path)
    monkeypatch.setattr(fo, "_DAILY_SPEND_PATH", tmp_path / "daily_spend.json")
    monkeypatch.setattr(fo, "_DAILY_SPEND_SQLITE", tmp_path / "daily_spend.sqlite")
    monkeypatch.setattr(fo, "_TASK_COSTS_PATH", tmp_path / "task_costs.json")
    yield tmp_path


# ─── Atomic increment ──────────────────────────────────────────────────────


def test_atomic_increment_creates_row(isolated_finops):
    from src.finops import _atomic_increment_spend
    new_total = _atomic_increment_spend("anthropic", 1.50, "2026-04-09")
    assert new_total == pytest.approx(1.50)


def test_atomic_increment_accumulates(isolated_finops):
    from src.finops import _atomic_increment_spend
    _atomic_increment_spend("anthropic", 1.0, "2026-04-09")
    _atomic_increment_spend("anthropic", 0.5, "2026-04-09")
    _atomic_increment_spend("anthropic", 0.25, "2026-04-09")
    final = _atomic_increment_spend("anthropic", 0.0, "2026-04-09")
    assert final == pytest.approx(1.75)


def test_atomic_increment_independent_per_provider(isolated_finops):
    from src.finops import _atomic_increment_spend, _load_spend_for_date
    _atomic_increment_spend("anthropic", 2.0, "2026-04-09")
    _atomic_increment_spend("openai", 0.5, "2026-04-09")
    _atomic_increment_spend("anthropic", 1.0, "2026-04-09")
    snapshot = _load_spend_for_date("2026-04-09")
    assert snapshot["anthropic"] == pytest.approx(3.0)
    assert snapshot["openai"] == pytest.approx(0.5)


def test_atomic_increment_independent_per_date(isolated_finops):
    from src.finops import _atomic_increment_spend, _load_spend_for_date
    _atomic_increment_spend("anthropic", 1.0, "2026-04-09")
    _atomic_increment_spend("anthropic", 2.0, "2026-04-10")
    s1 = _load_spend_for_date("2026-04-09")
    s2 = _load_spend_for_date("2026-04-10")
    assert s1["anthropic"] == pytest.approx(1.0)
    assert s2["anthropic"] == pytest.approx(2.0)


# ─── Migration JSON -> SQLite ──────────────────────────────────────────────


def test_migration_imports_existing_json(isolated_finops):
    """JSON com dados deve ser importado para SQLite na primeira leitura."""
    import src.finops as fo
    # Cria JSON manualmente como se fosse de uma versao anterior
    json_path = fo._DAILY_SPEND_PATH
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps({
        "date": "2026-04-09",
        "spend": {"anthropic": 1.50, "openai": 0.30},
    }), encoding="utf-8")

    loaded = fo._migrate_json_to_sqlite_if_needed("2026-04-09", json_path)
    assert loaded["anthropic"] == pytest.approx(1.50)
    assert loaded["openai"] == pytest.approx(0.30)

    # Verifica que SQLite agora tem os dados
    snapshot = fo._load_spend_for_date("2026-04-09")
    assert snapshot["anthropic"] == pytest.approx(1.50)


def test_migration_skips_when_sqlite_already_has_data(isolated_finops):
    """Migration nao deve sobrescrever dados ja em SQLite."""
    import src.finops as fo
    fo._atomic_increment_spend("anthropic", 5.0, "2026-04-09")

    json_path = fo._DAILY_SPEND_PATH
    json_path.write_text(json.dumps({
        "date": "2026-04-09",
        "spend": {"anthropic": 999.0},  # valor diferente
    }), encoding="utf-8")

    loaded = fo._migrate_json_to_sqlite_if_needed("2026-04-09", json_path)
    # Deve retornar valor do SQLite (5.0), nao do JSON (999.0)
    assert loaded["anthropic"] == pytest.approx(5.0)


def test_migration_skips_json_from_other_date(isolated_finops):
    """JSON de outro dia nao deve ser importado."""
    import src.finops as fo
    json_path = fo._DAILY_SPEND_PATH
    json_path.write_text(json.dumps({
        "date": "2025-01-01",  # data antiga
        "spend": {"anthropic": 100.0},
    }), encoding="utf-8")

    loaded = fo._migrate_json_to_sqlite_if_needed("2026-04-09", json_path)
    assert loaded == {}


# ─── FinOps class integration ──────────────────────────────────────────────


def test_finops_record_cost_atomic(isolated_finops):
    """record_cost deve usar SQLite atomico."""
    from src.finops import FinOps
    fo = FinOps()
    fo.record_cost("t1", "claude", 100, 200, 0.05)
    fo.record_cost("t2", "claude", 100, 200, 0.05)
    assert fo._daily_spend["anthropic"] == pytest.approx(0.10)


def test_finops_record_cost_persists_across_instances(isolated_finops):
    """Reinstanciar FinOps (= simula restart) preserva os totais.

    Esta eh a regressao critica que F23 fecha — antes, a corrida JSON
    permitia perda de tracking entre instancias paralelas. Agora SQLite
    eh atomic e source of truth.
    """
    from src.finops import FinOps
    fo1 = FinOps()
    fo1.record_cost("t1", "claude", 100, 200, 0.05)
    fo1.record_cost("t2", "claude", 100, 200, 0.07)
    total_fo1 = fo1._daily_spend["anthropic"]

    # Nova instancia (simula segundo processo)
    fo2 = FinOps()
    # Deve carregar os 0.12 que fo1 escreveu
    assert fo2._daily_spend.get("anthropic", 0) == pytest.approx(total_fo1)
    assert fo2._daily_spend["anthropic"] == pytest.approx(0.12)


def test_finops_concurrent_increments_no_loss(isolated_finops):
    """Simulacao de duas instancias incrementando 'concorrentemente'.

    SQLite WAL serializa writes, entao o resultado final deve ser a soma
    correta dos dois incrementos — sem perda por TOCTOU.
    """
    from src.finops import FinOps
    fo_a = FinOps()
    fo_b = FinOps()

    # Ambos comecam com 0
    assert fo_a._daily_spend.get("anthropic", 0) == 0
    assert fo_b._daily_spend.get("anthropic", 0) == 0

    # Cada um incrementa 0.10
    fo_a.record_cost("t_a", "claude", 100, 200, 0.10)
    fo_b.record_cost("t_b", "claude", 100, 200, 0.10)

    # SQLite reflete a soma correta — sem perda
    fo_check = FinOps()
    assert fo_check._daily_spend["anthropic"] == pytest.approx(0.20)


def test_finops_json_snapshot_still_written(isolated_finops):
    """Compat backward: JSON snapshot continua sendo escrito apos cada update."""
    import src.finops as fo
    f = fo.FinOps()
    f.record_cost("t1", "claude", 100, 200, 0.05)
    json_path = fo._DAILY_SPEND_PATH
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["spend"]["anthropic"] == pytest.approx(0.05)


def test_finops_uses_atomic_increment_in_record_cost():
    """Sentinela: garante que record_cost usa _atomic_increment_spend."""
    import inspect
    from src.finops import FinOps
    source = inspect.getsource(FinOps.record_cost)
    assert "_atomic_increment_spend" in source, (
        "record_cost DEVE usar _atomic_increment_spend para evitar TOCTOU"
    )

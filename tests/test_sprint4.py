"""Tests da sprint 4 (2026-04-07) — recalibracao AVG_COST + decompose Sonnet
+ tier_internal_engagement_rate + fallback_chain_save_rate + dashboard --export.

Bloqueia regressao dos 6 fixes da sprint 4:
- Fix #19 (P0): AVG_COST_PER_CALL inclui claude_sonnet/haiku + smart_route aplica downgrade
- Fix #21 (P1): Orchestrator.decompose() usa Sonnet em vez de Opus
- Fix #23 (P1): DECOMPOSE_SYSTEM com regra reforcada de sub-decomposicao de review
- Fix #24 (P1): tier_internal_engagement_rate KPI
- Fix #25 (P1): fallback_chain_save_rate KPI
- Fix #26 (P2): cli.py dashboard --export csv|json
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()
for env_var in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
                "PERPLEXITY_API_KEY", "GROQ_API_KEY"]:
    os.environ.setdefault(env_var, "test-key-not-real")


# ─── Fix #19: AVG_COST_PER_CALL inclui tier interno Claude ─────────────

class TestAvgCostRecalibration:
    """Sprint 4: AVG_COST_PER_CALL ganhou claude_sonnet e claude_haiku."""

    def test_claude_sonnet_in_avg_cost(self):
        from src.config import AVG_COST_PER_CALL
        assert "claude_sonnet" in AVG_COST_PER_CALL
        assert AVG_COST_PER_CALL["claude_sonnet"] < AVG_COST_PER_CALL["claude"]

    def test_claude_haiku_in_avg_cost(self):
        from src.config import AVG_COST_PER_CALL
        assert "claude_haiku" in AVG_COST_PER_CALL
        assert AVG_COST_PER_CALL["claude_haiku"] < AVG_COST_PER_CALL["claude_sonnet"]

    def test_haiku_is_at_least_10x_cheaper_than_opus(self):
        from src.config import AVG_COST_PER_CALL
        ratio = AVG_COST_PER_CALL["claude"] / AVG_COST_PER_CALL["claude_haiku"]
        assert ratio >= 10, f"Haiku deveria ser ~19x mais barato que Opus, foi {ratio:.1f}x"

    def test_groq_remains_cheapest(self):
        from src.config import AVG_COST_PER_CALL
        cheapest = min(AVG_COST_PER_CALL.values())
        assert AVG_COST_PER_CALL["groq"] == cheapest


# ─── Fix #19: smart_route aplica downgrade_claude_by_complexity ────────

class TestSmartRouteDowngrade:
    """smart_route deve aplicar downgrade Opus->Sonnet/Haiku no pre_check.

    Testes isolados do estado real do router (.router_stats.json) — em
    producao a deprioritizacao adaptativa pode mudar a escolha primaria,
    mas aqui validamos que SE claude for escolhido, vai para o tier certo.
    """

    def _make_router(self):
        """Cria SmartRouter com stats e session_usage zerados (isolado)."""
        from src.smart_router import SmartRouter
        router = SmartRouter()
        router._stats = {}  # zera adaptive stats persistidos
        router._session_usage = {k: 0 for k in router._session_usage}
        return router

    def _make_task(self, ttype, complexity_str):
        from src.models import Task, TaskComplexity
        return Task(id="t1", type=ttype, description="x", complexity=TaskComplexity(complexity_str))

    def test_smart_route_downgrades_low_complexity(self):
        from src.smart_router import DemandTier
        router = self._make_router()
        task = self._make_task("code", "low")
        cfg = router.smart_route(task, DemandTier.MODERATE)
        # Sem stats: primary=claude vence, e o downgrade aplica para Haiku
        assert cfg.name in ("claude_haiku", "claude_sonnet"), (
            f"Esperava downgrade para Haiku/Sonnet em low complexity, recebi {cfg.name}"
        )

    def test_smart_route_keeps_opus_for_high(self):
        from src.smart_router import DemandTier
        router = self._make_router()
        task = self._make_task("code", "high")
        cfg = router.smart_route(task, DemandTier.COMPLEX)
        # High mantem Opus (downgrade nao aplica)
        assert cfg.name == "claude"

    def test_smart_route_does_not_affect_non_claude(self):
        from src.smart_router import DemandTier
        router = self._make_router()
        task = self._make_task("research", "low")
        cfg = router.smart_route(task, DemandTier.SIMPLE)
        # Research vai pra perplexity, downgrade nao se aplica
        assert cfg.name == "perplexity"

    def test_downgrade_unit_test_independent_of_route(self):
        """Teste de unidade direto do downgrade — independe de _route_*."""
        from src.smart_router import SmartRouter
        from src.models import Task, TaskComplexity
        router = SmartRouter()
        task_low = Task(id="t", type="code", description="x", complexity=TaskComplexity.LOW)
        assert router.downgrade_claude_by_complexity("claude", task_low) == "claude_haiku"

        task_med = Task(id="t", type="code", description="x", complexity=TaskComplexity.MEDIUM)
        assert router.downgrade_claude_by_complexity("claude", task_med) == "claude_sonnet"

        task_high = Task(id="t", type="code", description="x", complexity=TaskComplexity.HIGH)
        assert router.downgrade_claude_by_complexity("claude", task_high) == "claude"


# ─── Fix #21: Orchestrator.decompose() usa Sonnet ──────────────────────

class TestDecomposerUsesSonnet:
    """Orchestrator._claude_cfg deve apontar para claude_sonnet (sprint 4)."""

    def test_orchestrator_claude_cfg_is_sonnet(self):
        from src.orchestrator import Orchestrator
        from src.config import LLM_CONFIGS
        orch = Orchestrator(smart=True)
        # _claude_cfg eh o config usado para decompose. Sprint 4: Sonnet.
        assert orch._claude_cfg.name == "claude_sonnet"
        assert orch._claude_cfg.model == LLM_CONFIGS["claude_sonnet"].model

    def test_orchestrator_falls_back_to_opus_if_sonnet_missing(self):
        """Se claude_sonnet for removido (regressao), deve cair pra claude (Opus)."""
        # Apenas verifica que o codigo usa .get com fallback, nao .[] direto
        from src import orchestrator
        import inspect
        source = inspect.getsource(orchestrator.Orchestrator.__init__)
        assert "claude_sonnet" in source
        assert "or LLM_CONFIGS" in source or '.get(' in source


# ─── Fix #23: DECOMPOSE_SYSTEM tem regra reforcada de review ──────────

class TestDecomposePromptReview:
    """DECOMPOSE_SYSTEM deve ter regras de sub-decomposicao de review."""

    def test_decompose_prompt_mentions_3_sub_reviews(self):
        from src.orchestrator import DECOMPOSE_SYSTEM
        # Sprint 2 + Sprint 4 reforcado: 3 sub-reviews paralelos
        assert "review_acentuacao" in DECOMPOSE_SYSTEM
        assert "review_codigo" in DECOMPOSE_SYSTEM
        assert "review_estilo" in DECOMPOSE_SYSTEM

    def test_decompose_prompt_mentions_paralelos_e_low_complexity(self):
        from src.orchestrator import DECOMPOSE_SYSTEM
        # Sprint 4 reforcou: marca complexity baixa para acionar tier interno
        prompt_lower = DECOMPOSE_SYSTEM.lower()
        assert "paralel" in prompt_lower
        assert "complexity" in prompt_lower or "tier interno" in prompt_lower


# ─── Fix #24: tier_internal_engagement_rate ───────────────────────────

class TestTierInternalEngagementRate:
    """compute_tier_internal_engagement_rate mede % Sonnet+Haiku / total Claude."""

    def test_no_claude_returns_zero(self):
        from src.kpi_history import compute_tier_internal_engagement_rate
        rate, meta = compute_tier_internal_engagement_rate({"groq": 5})
        assert rate == 0.0
        assert meta["claude_total"] == 0

    def test_only_opus_returns_zero(self):
        from src.kpi_history import compute_tier_internal_engagement_rate
        rate, meta = compute_tier_internal_engagement_rate({"claude": 5})
        assert rate == 0.0
        assert meta["opus"] == 5

    def test_only_haiku_returns_one(self):
        from src.kpi_history import compute_tier_internal_engagement_rate
        rate, meta = compute_tier_internal_engagement_rate({"claude_haiku": 3})
        assert rate == 1.0
        assert meta["haiku"] == 3

    def test_mixed_50_50(self):
        from src.kpi_history import compute_tier_internal_engagement_rate
        rate, meta = compute_tier_internal_engagement_rate({
            "claude": 2, "claude_sonnet": 1, "claude_haiku": 1,
        })
        # 2 sonnet+haiku / 4 total = 0.5
        assert rate == 0.5

    def test_kpi_entry_includes_tier_engagement(self):
        from src.kpi_history import append_kpi_entry
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.jsonl"
            entry = append_kpi_entry(
                demand="x", real_cost=0.1, estimated_cost=0.1,
                duration_ms=1000, llm_usage={"claude_haiku": 2, "claude": 1},
                tasks_completed=3, tasks_failed=0, history_path=path,
            )
            assert "tier_internal_engagement_rate" in entry
            # 2 haiku / (1 opus + 2 haiku) = 0.667
            assert entry["tier_internal_engagement_rate"] == pytest.approx(0.6667, abs=0.01)


# ─── Fix #25: fallback_chain_save_rate ───────────────────────────────

class TestFallbackSaveRate:
    """compute_fallback_save_rate é cumulativo por arquivo jsonl."""

    def test_compute_save_rate_zero_runs(self):
        from src.kpi_history import compute_fallback_save_rate
        assert compute_fallback_save_rate(0, 0) == 0.0

    def test_compute_save_rate_50pct(self):
        from src.kpi_history import compute_fallback_save_rate
        # 1 save em 2 runs = 50%
        assert compute_fallback_save_rate(1, 2) == 0.5

    def test_kpi_entry_records_fallback_saves(self):
        from src.kpi_history import append_kpi_entry
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.jsonl"
            # Run 1: 0 saves
            e1 = append_kpi_entry(
                demand="x", real_cost=0.1, estimated_cost=0.1, duration_ms=1000,
                llm_usage={"claude": 1}, tasks_completed=1, tasks_failed=0,
                fallback_saves=0, history_path=path,
            )
            assert e1["fallback_saves"] == 0
            assert e1["fallback_chain_save_rate_cumulative"] == 0.0

            # Run 2: 1 save
            e2 = append_kpi_entry(
                demand="x", real_cost=0.1, estimated_cost=0.1, duration_ms=1000,
                llm_usage={"claude": 1}, tasks_completed=1, tasks_failed=0,
                fallback_saves=1, history_path=path,
            )
            assert e2["fallback_saves"] == 1
            # 1 save em 2 runs = 0.5
            assert e2["fallback_chain_save_rate_cumulative"] == 0.5


# ─── Fix #26: dashboard --export csv|json ────────────────────────────

class TestDashboardExport:
    """cli.py dashboard com flag --export."""

    def test_dashboard_has_export_option(self):
        import cli
        cmd = cli.cli.commands["dashboard"]
        param_names = [p.name for p in cmd.params]
        assert "export" in param_names
        assert "out" in param_names

    def test_dashboard_export_csv_choices(self):
        import cli
        cmd = cli.cli.commands["dashboard"]
        export_param = next(p for p in cmd.params if p.name == "export")
        # click.Choice with case_sensitive=False
        choices = list(export_param.type.choices)
        assert "csv" in choices
        assert "json" in choices

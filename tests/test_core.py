"""Tests for geo-orchestrator core modules: rate_limiter, cost_tracker, router, config."""
import asyncio
import pytest
from src.config import LLM_CONFIGS, LLMConfig, Provider
from src.rate_limiter import TokenBucket, RateLimiter, ProviderLimit, PROVIDER_LIMITS
from src.cost_tracker import CostTracker
from src.router import Router


# ─── Rate Limiter ───────────────────────────────────────────

class TestTokenBucket:
    def _make_bucket(self, rpm=60, burst=5):
        limit = ProviderLimit(requests_per_minute=rpm, burst_size=burst)
        return TokenBucket(limit)

    def test_initial_state(self):
        bucket = self._make_bucket()
        assert bucket.current_rpm == 0

    @pytest.mark.asyncio
    async def test_acquire_increments_rpm(self):
        bucket = self._make_bucket()
        await bucket.acquire("test")
        assert bucket.current_rpm == 1

    def test_status_returns_dict(self):
        bucket = self._make_bucket()
        status = bucket.status()
        assert isinstance(status, dict)
        assert "available_tokens" in status or "current_rpm" in status


class TestRateLimiter:
    def setup_method(self):
        RateLimiter.reset_instance()

    def test_singleton(self):
        rl1 = RateLimiter.get_instance()
        rl2 = RateLimiter.get_instance()
        assert rl1 is rl2

    def test_acquire_provider(self):
        rl = RateLimiter.get_instance()
        # Sprint 6 (2026-04-08): asyncio.get_event_loop() depreciado em 3.12.
        # Usa asyncio.run em vez disso.
        asyncio.run(rl.acquire(Provider.GROQ))
        rpm = rl.current_rpm(Provider.GROQ)
        assert rpm == 1


# ─── Cost Tracker ───────────────────────────────────────────

class TestCostTracker:
    def test_initial_total_is_zero(self):
        ct = CostTracker()
        summary = ct.summary()
        assert summary["total_cost"] == 0.0

    def test_record_cost(self):
        ct = CostTracker()
        ct.record("T1", "claude", 1000, 500, 0.05)
        summary = ct.summary()
        assert summary["total_cost"] == pytest.approx(0.05)

    def test_cost_by_provider(self):
        ct = CostTracker()
        ct.record("T1", "claude", 1000, 500, 0.50)
        ct.record("T2", "gpt4o", 800, 400, 0.03)
        summary = ct.summary()
        assert summary["total_cost"] == pytest.approx(0.53)


# ─── Router ─────────────────────────────────────────────────

class TestRouter:
    def test_record_assignment(self):
        router = Router()
        router.record_assignment("claude")
        usage = router.get_session_usage()
        assert usage.get("claude", 0) == 1

    def test_get_unused_models(self):
        router = Router()
        router.record_assignment("claude")
        router.record_assignment("gpt4o")
        unused = router.get_unused_models()
        assert "claude" not in unused

    def test_least_used_prefers_unused(self):
        router = Router()
        router.record_assignment("claude")
        router.record_assignment("claude")
        router.record_assignment("gpt4o")
        result = router._least_used_llm(["claude", "gpt4o", "gemini"])
        assert result == "gemini"

    def test_model_status_table(self):
        router = Router()
        table = router.get_model_status_table()
        assert isinstance(table, str)
        assert len(table) > 0


# ─── Config ─────────────────────────────────────────────────

class TestConfig:
    def test_llm_configs_has_5_providers(self):
        assert len(LLM_CONFIGS) >= 5

    def test_all_configs_are_llm_config(self):
        for name, config in LLM_CONFIGS.items():
            assert isinstance(config, LLMConfig), f"{name} is not LLMConfig"

    def test_provider_limits_defined(self):
        for provider in Provider:
            assert provider in PROVIDER_LIMITS

    @pytest.mark.parametrize("name", ["claude", "gpt4o", "gemini", "perplexity", "groq"])
    def test_provider_has_model(self, name):
        assert name in LLM_CONFIGS
        assert LLM_CONFIGS[name].model is not None

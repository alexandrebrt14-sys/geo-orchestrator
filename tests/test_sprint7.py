"""Tests da sprint 7 (2026-04-08) — catalog runtime SoT + /health endpoint
+ rollback safety do calibrator + dashboard HTML + coverage badge.

Bloqueia regressao das entregas da sprint 7:
- LLM_CONFIGS construido em runtime via build_llm_configs_from_catalog
- Servidor /health + /metrics em src/health_server.py
- Calibrator rollback + safety threshold (testes em test_sprint5)
- Dashboard HTML estatico
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()
for env_var in [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
    "PERPLEXITY_API_KEY", "GROQ_API_KEY",
]:
    os.environ.setdefault(env_var, "test-key-not-real")


# ─── Catalog runtime SoT ────────────────────────────────────────────────

class TestCatalogRuntimeSoT:
    """Sprint 7: LLM_CONFIGS deve vir do catalog YAML em runtime."""

    def test_llm_configs_loaded_from_catalog(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML nao instalado")
        from src.config import LLM_CONFIGS
        # Os 5 canonicos + 2 tier interno = 7 aliases
        assert len(LLM_CONFIGS) >= 5
        for canonical in ["claude", "gpt4o", "gemini", "perplexity", "groq"]:
            assert canonical in LLM_CONFIGS

    def test_catalog_provides_correct_costs(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML nao instalado")
        from src.config import LLM_CONFIGS
        # gpt-4o = $2.50 / $10.00 per Mtok = 0.0025 / 0.010 per 1k
        gpt = LLM_CONFIGS["gpt4o"]
        assert gpt.cost_per_1k_input == pytest.approx(0.0025, abs=1e-6)
        assert gpt.cost_per_1k_output == pytest.approx(0.010, abs=1e-6)
        # claude opus = $15 / $75 per Mtok
        claude = LLM_CONFIGS["claude"]
        assert claude.cost_per_1k_input == pytest.approx(0.015, abs=1e-6)
        assert claude.cost_per_1k_output == pytest.approx(0.075, abs=1e-6)

    def test_build_from_catalog_directly(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML nao instalado")
        from src.catalog_loader import build_llm_configs_from_catalog
        configs = build_llm_configs_from_catalog()
        assert len(configs) >= 5
        for canonical in ["claude", "gpt4o", "gemini", "perplexity", "groq"]:
            assert canonical in configs
            assert configs[canonical].api_key_env

    def test_provider_enum_mapping(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML nao instalado")
        from src.config import LLM_CONFIGS, Provider
        assert LLM_CONFIGS["claude"].provider == Provider.ANTHROPIC
        assert LLM_CONFIGS["gpt4o"].provider == Provider.OPENAI
        assert LLM_CONFIGS["gemini"].provider == Provider.GOOGLE
        assert LLM_CONFIGS["perplexity"].provider == Provider.PERPLEXITY
        assert LLM_CONFIGS["groq"].provider == Provider.GROQ


# ─── /health endpoint ───────────────────────────────────────────────────

class TestHealthServer:
    def test_build_health_payload_structure(self):
        from src.health_server import _build_health_payload
        payload, overall = _build_health_payload()

        # Estrutura JSON esperada
        assert "service" in payload
        assert payload["service"] == "geo-orchestrator"
        assert "version" in payload
        assert "overall" in payload
        assert "uptime_seconds" in payload
        assert "timestamp" in payload
        assert "checks" in payload
        assert "model_status" in payload
        assert "kpi_snapshot" in payload
        assert overall in ("OK", "ATENCAO", "CRITICO")

    def test_health_has_all_6_checks(self):
        from src.health_server import _build_health_payload
        payload, _ = _build_health_payload()
        names = {c["name"] for c in payload["checks"]}
        for expected in ["api_keys", "catalog_consistency", "finops_daily",
                         "kpi_history", "cost_calibration", "drift_detector"]:
            assert expected in names, f"check ausente: {expected}"

    def test_metrics_payload_structure(self):
        from src.health_server import _build_metrics_payload
        payload = _build_metrics_payload(n=5)
        assert "service" in payload
        assert "n_entries" in payload
        assert "entries" in payload
        assert isinstance(payload["entries"], list)

    def test_health_handler_routes(self, tmp_path):
        """Testa o HealthHandler in-process via mock socket."""
        from src.health_server import HealthHandler
        from io import BytesIO

        class MockRequest:
            def __init__(self, raw):
                self._buf = BytesIO(raw)
            def makefile(self, *a, **kw):
                return self._buf

        # GET /health
        request_bytes = b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n"
        req = MockRequest(request_bytes)
        # BaseHTTPRequestHandler precisa de socket — usamos handler manual
        # mais simples: chamamos do_GET diretamente apos setup minimo
        handler = HealthHandler.__new__(HealthHandler)
        handler.path = "/health"
        handler.requestline = "GET /health HTTP/1.1"
        handler.request_version = "HTTP/1.1"
        handler.headers = {}
        handler.command = "GET"

        captured = []
        def fake_send_response(status):
            captured.append(("status", status))
        def fake_send_header(name, value):
            captured.append(("header", name, value))
        def fake_end_headers():
            captured.append(("end_headers",))
        class FakeWFile:
            def __init__(self):
                self.data = b""
            def write(self, b):
                self.data += b
        handler.wfile = FakeWFile()
        handler.send_response = fake_send_response
        handler.send_header = fake_send_header
        handler.end_headers = fake_end_headers
        handler.address_string = lambda: "test"

        handler.do_GET()
        # Status deve ser 200 ou 503
        statuses = [c[1] for c in captured if c[0] == "status"]
        assert statuses[0] in (200, 503)
        # Body deve ser JSON valido
        body = json.loads(handler.wfile.data.decode("utf-8"))
        assert "overall" in body
        assert "checks" in body

class TestDashboardHTML:
    """Sprint 7: dashboard HTML estatico com Chart.js inline."""

    def test_render_returns_html(self):
        from src.dashboard_html import render_dashboard_html
        entries = [{
            "timestamp": "2026-04-08T10:00:00",
            "demand": "test",
            "distribution_health": 0.95,
            "cost_estimate_accuracy": 1.0,
            "tier_internal_engagement_rate": 0.5,
            "fallback_chain_save_rate_cumulative": 0.0,
            "quality_judge_pass": 1.0,
            "parallelism_efficiency": 3.5,
            "real_cost_usd": 0.05,
            "estimated_cost_usd": 0.06,
            "duration_ms": 5000,
            "tasks_completed": 5, "tasks_failed": 0,
            "llm_usage": {"claude": 1, "gpt4o": 1, "gemini": 1, "perplexity": 1, "groq": 1},
        }]
        html = render_dashboard_html(entries=entries)
        assert "<html" in html
        assert "geo-orchestrator" in html
        assert "Chart" in html  # Chart.js
        assert "0.95" in html  # health value
        assert "claude" in html

    def test_render_to_file(self, tmp_path):
        from src.dashboard_html import render_dashboard_html
        out_path = tmp_path / "dash.html"
        render_dashboard_html(entries=[], output_path=out_path)
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content

    def test_aggregate_consolidates_tier_internal_claude(self):
        from src.dashboard_html import _aggregate_llm_usage
        entries = [
            {"llm_usage": {"claude": 2, "claude_sonnet": 3, "claude_haiku": 1}},
            {"llm_usage": {"groq": 5}},
        ]
        agg = _aggregate_llm_usage(entries)
        # Tier interno consolidado em 'claude' canonico
        assert agg["claude"] == 6  # 2 + 3 + 1
        assert agg["groq"] == 5
        assert agg["gpt4o"] == 0


class TestHealthHandlerExtras:
    def test_health_handler_404(self):
        from src.health_server import HealthHandler
        handler = HealthHandler.__new__(HealthHandler)
        handler.path = "/nonexistent"
        handler.headers = {}
        captured = []
        handler.send_response = lambda s: captured.append(s)
        handler.send_header = lambda *a: None
        handler.end_headers = lambda: None
        class FakeWFile:
            data = b""
            def write(self, b):
                self.data += b
        handler.wfile = FakeWFile()
        handler.address_string = lambda: "test"
        handler.do_GET()
        assert captured[0] == 404


# ─── F40: Bearer token opcional no /health ─────────────────────────────────


class TestHealthAuthF40:
    """Achado F40 da auditoria 2026-04-08: /health expunha metricas FinOps,
    KPIs, custos e calibracao sem auth. Bearer token opcional via env var
    GEO_HEALTH_TOKEN. Sem token = endpoint publico (compat backward)."""

    def _make_handler(self, path, headers=None):
        from src.health_server import HealthHandler
        h = HealthHandler.__new__(HealthHandler)
        h.path = path
        h.headers = headers or {}
        h._captured = []
        h.send_response = lambda s: h._captured.append(("status", s))
        h.send_header = lambda n, v: h._captured.append(("header", n, v))
        h.end_headers = lambda: h._captured.append(("end_headers",))
        class FakeWFile:
            def __init__(self):
                self.data = b""
            def write(self, b):
                self.data += b
        h.wfile = FakeWFile()
        h.address_string = lambda: "test"
        return h

    def test_no_token_set_acts_as_public(self, monkeypatch):
        """Sem GEO_HEALTH_TOKEN -> qualquer requisicao passa (compat)."""
        monkeypatch.delenv("GEO_HEALTH_TOKEN", raising=False)
        h = self._make_handler("/health")
        h.do_GET()
        statuses = [c[1] for c in h._captured if c[0] == "status"]
        assert statuses[0] in (200, 503)  # nao 401

    def test_token_set_blocks_missing_header(self, monkeypatch):
        """Com token + sem header Authorization -> 401."""
        monkeypatch.setenv("GEO_HEALTH_TOKEN", "secret-test-token-xyz")
        h = self._make_handler("/health", headers={})
        h.do_GET()
        statuses = [c[1] for c in h._captured if c[0] == "status"]
        assert statuses[0] == 401

    def test_token_set_blocks_wrong_token(self, monkeypatch):
        """Com token + Bearer errado -> 401."""
        monkeypatch.setenv("GEO_HEALTH_TOKEN", "secret-test-token-xyz")
        h = self._make_handler("/health", headers={"Authorization": "Bearer wrong-token"})
        h.do_GET()
        statuses = [c[1] for c in h._captured if c[0] == "status"]
        assert statuses[0] == 401

    def test_token_set_accepts_correct_token(self, monkeypatch):
        """Com token + Bearer correto -> 200/503 (passa para handler)."""
        monkeypatch.setenv("GEO_HEALTH_TOKEN", "secret-test-token-xyz")
        h = self._make_handler(
            "/health",
            headers={"Authorization": "Bearer secret-test-token-xyz"},
        )
        h.do_GET()
        statuses = [c[1] for c in h._captured if c[0] == "status"]
        assert statuses[0] in (200, 503)  # passou auth

    def test_root_docs_always_public_even_with_token(self, monkeypatch):
        """/ (root docs) sempre publica, mesmo com token configurado.
        Permite descoberta de endpoints sem expor dados sensiveis."""
        monkeypatch.setenv("GEO_HEALTH_TOKEN", "secret-test-token-xyz")
        h = self._make_handler("/", headers={})
        h.do_GET()
        statuses = [c[1] for c in h._captured if c[0] == "status"]
        assert statuses[0] == 200

    def test_check_auth_uses_compare_digest(self):
        """Static guard: _check_auth deve usar hmac.compare_digest (timing-safe)."""
        import inspect
        from src.health_server import _check_auth
        source = inspect.getsource(_check_auth)
        assert "hmac.compare_digest" in source, (
            "_check_auth DEVE usar hmac.compare_digest contra timing attack"
        )

    def test_unauthorized_response_includes_www_authenticate(self, monkeypatch):
        """RFC 7235: 401 deve incluir WWW-Authenticate header."""
        monkeypatch.setenv("GEO_HEALTH_TOKEN", "secret-test-token-xyz")
        h = self._make_handler("/health", headers={})
        h.do_GET()
        headers_sent = [(c[1], c[2]) for c in h._captured if c[0] == "header"]
        assert any("WWW-Authenticate" in n for n, _ in headers_sent)

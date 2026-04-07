"""Tests da sprint 3 (2026-04-07) — sanitize, KPI history, drift detection,
decomposer complexity refinada e cli.py dashboard.

Bloqueia regressao dos 5 fixes da sprint 3:
- Fix #11: src/sanitize.py — sanitize_filename / sanitize_path / sanitize_slug
- Fix #13: orchestrator._estimate_complexity refinado
- Fix #14: src/kpi_history.py — append_kpi_entry + compute_*
- Fix #15: detect_drift
- Fix #16: cli.py dashboard
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


# ─── Fix #11: sanitize.py ──────────────────────────────────────────────

class TestSanitizeFilename:
    """src/sanitize.py — sanitize_filename remove acentos, path traversal, etc."""

    def test_remove_accents(self):
        from src.sanitize import sanitize_filename
        assert sanitize_filename("relatório-final.json") == "relatorio-final.json"

    def test_blocks_path_traversal(self):
        from src.sanitize import sanitize_filename
        result = sanitize_filename("../../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert "\\" not in result

    def test_replaces_unsafe_chars_with_underscore(self):
        from src.sanitize import sanitize_filename
        result = sanitize_filename("foo bar*baz?qux")
        assert "*" not in result
        assert "?" not in result
        assert " " not in result

    def test_empty_returns_fallback(self):
        from src.sanitize import sanitize_filename
        assert sanitize_filename("") == "unnamed"
        assert sanitize_filename("...") == "unnamed"
        assert sanitize_filename("___") == "unnamed"

    def test_truncates_long_names(self):
        from src.sanitize import sanitize_filename, MAX_FILENAME_LENGTH
        long = "a" * 500 + ".json"
        result = sanitize_filename(long)
        assert len(result) <= MAX_FILENAME_LENGTH
        assert result.endswith(".json"), "extension should be preserved"

    def test_handles_unicode_combining(self):
        from src.sanitize import sanitize_filename
        # NFD: c + combining cedilla
        nfd = "rela\u0301torio"  # 'á' como NFD
        result = sanitize_filename(nfd)
        assert "relatorio" in result.lower()


class TestSanitizePath:
    """sanitize_path garante que o resultado fica DENTRO de base_dir."""

    def test_safe_path_inside_base(self):
        from src.sanitize import sanitize_path
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            result = sanitize_path(base, "task_acentuação.json")
            assert result.is_relative_to(base)
            assert "acentuacao" in result.name

    def test_path_traversal_blocked(self):
        from src.sanitize import sanitize_path
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            # Mesmo com tentativa de escape, deve ficar dentro de base
            result = sanitize_path(base, "../../../etc/passwd")
            assert result.is_relative_to(base)


class TestSanitizeSlug:
    """sanitize_slug produz slugs URL-safe."""

    def test_kebab_case(self):
        from src.sanitize import sanitize_slug
        assert sanitize_slug("Refatoração do Orquestrador v2.0!") == "refatoracao-do-orquestrador-v2-0"

    def test_slug_no_double_hyphens(self):
        from src.sanitize import sanitize_slug
        result = sanitize_slug("foo  --  bar")
        assert "--" not in result


# ─── Fix #13: decomposer marca complexity variável ────────────────────

class TestDecomposerComplexity:
    """orchestrator._estimate_complexity refinado para acionar tier interno."""

    def _make_orch(self):
        # Import lazy para nao quebrar quando ainda nao tem env
        from src.orchestrator import Orchestrator
        return Orchestrator(smart=False)  # nao precisa SmartRouter

    def _make_task(self, ttype, desc, deps=None):
        from src.models import Task, TaskComplexity
        return Task(
            id="t1", type=ttype, description=desc,
            dependencies=deps or [],
            complexity=TaskComplexity.MEDIUM,  # default
        )

    def test_code_default_is_medium_not_high(self):
        """Sprint 3: code/review nao defaultam mais para HIGH (era bug — sempre Opus)."""
        orch = self._make_orch()
        task = self._make_task("code", "Criar funcao helper de validacao de email com regex.")
        orch._estimate_complexity([task])
        # Antes da sprint 3 isso virava HIGH automaticamente
        assert task.complexity.value in ("low", "medium"), (
            f"code com descricao curta deve virar low/medium, virou {task.complexity.value}"
        )

    def test_long_code_description_becomes_high(self):
        orch = self._make_orch()
        long_desc = "Refatorar o módulo inteiro do orquestrador. " * 30  # >600 chars
        task = self._make_task("code", long_desc)
        orch._estimate_complexity([task])
        assert task.complexity.value == "high"

    def test_low_keyword_pushes_down(self):
        from src.orchestrator import Orchestrator
        from src.models import Task, TaskComplexity
        orch = Orchestrator(smart=False)
        task = Task(id="t", type="code", description="Tarefa simples e rapida de listar arquivos.", complexity=TaskComplexity.MEDIUM)
        orch._estimate_complexity([task])
        assert task.complexity.value == "low"

    def test_classification_stays_low(self):
        orch = self._make_orch()
        task = self._make_task("classification", "Classificar 100 registros por categoria.")
        orch._estimate_complexity([task])
        assert task.complexity.value == "low"

    def test_integration_task_boosted_by_deps(self):
        """3+ dependencias sobem um tier — mantido da implementacao original."""
        orch = self._make_orch()
        task = self._make_task("analysis", "Consolidar resultados.", deps=["t1", "t2", "t3", "t4"])
        orch._estimate_complexity([task])
        # MEDIUM default + 3+ deps -> HIGH
        assert task.complexity.value == "high"


# ─── Fix #14: KPI history persistence ──────────────────────────────────

class TestKPIHistory:
    """src/kpi_history.py — append_kpi_entry + compute_distribution_health + compute_cost_estimate_accuracy."""

    def test_compute_distribution_health_perfect(self):
        from src.kpi_history import compute_distribution_health
        usage = {"claude": 2, "gpt4o": 2, "gemini": 2, "perplexity": 2, "groq": 2}
        score, meta = compute_distribution_health(usage)
        assert score == 1.0
        assert meta["used_llms"] == 5
        assert meta["max_share"] == 0.2

    def test_compute_distribution_health_concentrated(self):
        from src.kpi_history import compute_distribution_health
        # Run #1 da bateria: 5 claude, 1 gemini -> 5/6 = 83% claude
        usage = {"claude": 5, "gpt4o": 0, "gemini": 1, "perplexity": 0, "groq": 0}
        score, meta = compute_distribution_health(usage)
        # 2/5 LLMs * (1 - max(0, 0.833 - 0.8)) = 0.4 * 0.967 = 0.387
        assert score < 0.5, f"Run com 83% concentracao deveria ter health baixa, foi {score}"
        assert meta["max_share_provider"] == "claude"

    def test_cost_estimate_accuracy(self):
        from src.kpi_history import compute_cost_estimate_accuracy
        # Run #1 da bateria: real 0.6653 / estimado 0.108 = 6.16
        assert compute_cost_estimate_accuracy(0.6653, 0.108) == pytest.approx(6.16, abs=0.01)
        # Run #2: 0.2191 / 0.288 = 0.76
        assert compute_cost_estimate_accuracy(0.2191, 0.288) == pytest.approx(0.76, abs=0.01)

    def test_append_creates_jsonl(self):
        from src.kpi_history import append_kpi_entry
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kpi.jsonl"
            entry = append_kpi_entry(
                demand="Teste de KPI",
                real_cost=0.20, estimated_cost=0.25, duration_ms=10000,
                llm_usage={"claude": 2, "gpt4o": 1, "gemini": 1, "perplexity": 1, "groq": 1},
                tasks_completed=6, tasks_failed=0,
                history_path=path,
            )
            assert path.exists()
            assert entry["distribution_health"] > 0.9
            assert entry["cost_estimate_accuracy"] == 0.8
            # Verifica que o arquivo tem 1 linha JSON
            lines = path.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 1
            parsed = json.loads(lines[0])
            assert parsed["demand"] == "Teste de KPI"


# ─── Fix #15: drift detection ─────────────────────────────────────────

class TestDriftDetection:
    """detect_drift dispara alerta se 3 runs consecutivos saem da banda."""

    def test_no_drift_when_healthy(self):
        from src.kpi_history import append_kpi_entry, detect_drift
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.jsonl"
            for _ in range(3):
                append_kpi_entry(
                    demand="ok", real_cost=0.20, estimated_cost=0.20,
                    duration_ms=1000, llm_usage={"claude": 1},
                    tasks_completed=1, tasks_failed=0, history_path=path,
                )
            assert detect_drift(history_path=path) is None

    def test_drift_triggers_after_3_out_of_band(self):
        from src.kpi_history import append_kpi_entry, detect_drift
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.jsonl"
            # 3 runs com ratio = 6.0x (fora de 0.7-1.5)
            for _ in range(3):
                append_kpi_entry(
                    demand="bad", real_cost=0.60, estimated_cost=0.10,
                    duration_ms=1000, llm_usage={"claude": 1},
                    tasks_completed=1, tasks_failed=0, history_path=path,
                )
            alert = detect_drift(history_path=path)
            assert alert is not None
            assert alert["alert"] == "COST_ESTIMATE_DRIFT"
            assert alert["count"] == 3
            assert alert["direction"] == "subestimando"
            assert all(v == 6.0 for v in alert["last_values"])

    def test_drift_resolves_when_returns_to_band(self):
        """Se 2 runs ruins seguidos de 1 bom, o ultimo entry quebra a sequencia."""
        from src.kpi_history import append_kpi_entry, detect_drift
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.jsonl"
            for _ in range(2):
                append_kpi_entry(
                    demand="bad", real_cost=0.60, estimated_cost=0.10,
                    duration_ms=1000, llm_usage={"claude": 1},
                    tasks_completed=1, tasks_failed=0, history_path=path,
                )
            # 1 run saudavel quebra a sequencia
            append_kpi_entry(
                demand="good", real_cost=0.20, estimated_cost=0.20,
                duration_ms=1000, llm_usage={"claude": 1},
                tasks_completed=1, tasks_failed=0, history_path=path,
            )
            assert detect_drift(history_path=path) is None


# ─── Fix #16: cli dashboard command ────────────────────────────────────

class TestDashboardCommand:
    """cli.py dashboard exposto e funcional."""

    def test_dashboard_in_commands(self):
        import cli
        assert "dashboard" in cli.cli.commands

    def test_dashboard_has_limit_option(self):
        import cli
        cmd = cli.cli.commands["dashboard"]
        param_names = [p.name for p in cmd.params]
        assert "limit" in param_names

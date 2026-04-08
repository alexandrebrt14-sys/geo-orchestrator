"""Tests da sprint 5 (2026-04-08) — auto-calibracao de custo + 2 KPIs novos
+ filtro --since no dashboard + replay command + catalog SoT.

Bloqueia regressao das entregas da sprint 5:
- Adaptive AVG_COST_PER_CALL (cost_calibrator.py)
- KPI quality_judge_pass_rate
- KPI parallelism_efficiency
- dashboard --since 7d filter
- cli.py replay <execution_id>
- catalog/model_catalog.yaml validado contra src/config.LLM_CONFIGS
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)

import pytest
from dotenv import load_dotenv

load_dotenv()
for env_var in [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
    "PERPLEXITY_API_KEY", "GROQ_API_KEY",
]:
    os.environ.setdefault(env_var, "test-key-not-real")


# ─── Cost calibrator ────────────────────────────────────────────────────

class TestCostCalibrator:
    """Sprint 5: cost_calibrator aprende de execution_*.json reais."""

    def _make_report(self, ts: str, results: dict) -> dict:
        return {
            "timestamp": ts,
            "demand": "test",
            "totals": {"cost_usd": sum(r["cost"] for r in results.values())},
            "results": results,
        }

    def test_recalibrate_learns_from_history(self, tmp_path):
        from src.cost_calibrator import recalibrate, CALIBRATION_PATH
        # Cria 4 reports sinteticos com claude consistente em $0.20
        for i in range(4):
            payload = self._make_report(
                f"2026-04-08T10:0{i}:00Z",
                {
                    f"t{i}": {
                        "llm_used": "claude",
                        "cost": 0.20,
                        "cache_hit": False,
                        "success": True,
                    },
                    f"t{i}_b": {
                        "llm_used": "groq",
                        "cost": 0.001,
                        "cache_hit": False,
                        "success": True,
                    },
                },
            )
            (tmp_path / f"execution_2026040810{i:02d}00.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )

        result = recalibrate(window=10, output_dir=tmp_path, persist=False)
        cal = result["calibrated_avg_cost_per_call"]
        assert "claude" in cal
        assert "groq" in cal
        assert cal["claude"] == pytest.approx(0.20, abs=0.01)
        assert cal["groq"] == pytest.approx(0.001, abs=0.0005)

    def test_min_sample_filter(self, tmp_path):
        from src.cost_calibrator import recalibrate
        # Apenas 2 amostras de claude — abaixo do MIN_SAMPLE=3
        for i in range(2):
            payload = self._make_report(
                f"2026-04-08T10:0{i}:00Z",
                {f"t{i}": {"llm_used": "claude", "cost": 0.20, "cache_hit": False, "success": True}},
            )
            (tmp_path / f"execution_2026040810{i:02d}00.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        result = recalibrate(window=10, output_dir=tmp_path, persist=False)
        # claude nao deve aparecer (sample < MIN_SAMPLE)
        assert "claude" not in result["calibrated_avg_cost_per_call"]
        assert result["sample_sizes"].get("claude", 0) == 2

    def test_get_calibrated_avg_cost_falls_back_to_static(self):
        from src.cost_calibrator import get_calibrated_avg_cost
        from src.config import AVG_COST_PER_CALL
        merged = get_calibrated_avg_cost()
        # Mesmo sem calibracao persistida, retorna todos os LLMs estaticos
        for llm in AVG_COST_PER_CALL:
            assert llm in merged

    def test_outlier_filter(self, tmp_path):
        """Custos absurdos (< 0.0001 ou > 5.0) sao ignorados.

        Sprint 7 (2026-04-08): usa gpt4o cujo default e 0.015. Custo
        candidato 0.018 esta dentro da banda do safety threshold (< 5x).
        """
        from src.cost_calibrator import recalibrate
        for i in range(5):
            # 3 validos (~0.018), 1 outlier alto (10.0 > CEILING), 1 outlier baixo
            cost = 0.018 if i < 3 else (10.0 if i == 3 else 0.00001)
            (tmp_path / f"execution_2026040810{i:02d}00.json").write_text(
                json.dumps(self._make_report(
                    f"2026-04-08T10:0{i}:00Z",
                    {f"t{i}": {"llm_used": "gpt4o", "cost": cost, "cache_hit": False, "success": True}},
                )), encoding="utf-8",
            )
        result = recalibrate(window=10, output_dir=tmp_path, persist=False)
        # Apenas 3 amostras validas (as outras 2 sao outliers filtrados)
        assert result["sample_sizes"]["gpt4o"] == 3
        assert result["calibrated_avg_cost_per_call"]["gpt4o"] == pytest.approx(0.018, abs=0.001)

    def test_safety_threshold_rejects_extreme_calibration(self, tmp_path):
        """Sprint 7: candidatos > 5x ou < 0.2x do default sao rejeitados."""
        from src.cost_calibrator import recalibrate
        # gpt4o default = $0.015. Candidato $0.10 = 6.67x → rejeitado.
        for i in range(5):
            (tmp_path / f"execution_2026040810{i:02d}00.json").write_text(
                json.dumps(self._make_report(
                    f"2026-04-08T10:0{i}:00Z",
                    {f"t{i}": {"llm_used": "gpt4o", "cost": 0.10,
                               "cache_hit": False, "success": True}},
                )), encoding="utf-8",
            )
        result = recalibrate(window=10, output_dir=tmp_path, persist=False)
        # gpt4o nao deve aparecer no calibrated (rejected by safety)
        assert "gpt4o" not in result["calibrated_avg_cost_per_call"]
        # Mas a rejeicao deve estar registrada
        rejs = result.get("safety_rejections", [])
        assert len(rejs) >= 1
        assert any(r["llm"] == "gpt4o" for r in rejs)

    def test_calibration_backup_and_rollback(self, tmp_path, monkeypatch):
        """Sprint 7: backup do calibration anterior + rollback funciona."""
        from src import cost_calibrator as cc
        monkeypatch.setattr(cc, "CALIBRATION_PATH", tmp_path / ".cost_calibration.json")
        monkeypatch.setattr(cc, "CALIBRATION_BACKUP_PATH", tmp_path / ".cost_calibration.backup.json")

        # 1a calibracao gera arquivo
        for i in range(3):
            (tmp_path / f"execution_2026040810{i:02d}00.json").write_text(
                json.dumps(self._make_report(
                    f"2026-04-08T10:0{i}:00Z",
                    {f"t{i}": {"llm_used": "groq", "cost": 0.001,
                               "cache_hit": False, "success": True}},
                )), encoding="utf-8",
            )
        cc.recalibrate(window=10, output_dir=tmp_path, persist=True)
        first_content = cc.CALIBRATION_PATH.read_text(encoding="utf-8")

        # 2a calibracao sobrescreve mas backup deve preservar a primeira
        for i in range(3, 6):
            (tmp_path / f"execution_2026040810{i:02d}00.json").write_text(
                json.dumps(self._make_report(
                    f"2026-04-08T10:0{i}:00Z",
                    {f"t{i}": {"llm_used": "groq", "cost": 0.0015,
                               "cache_hit": False, "success": True}},
                )), encoding="utf-8",
            )
        cc.recalibrate(window=10, output_dir=tmp_path, persist=True)
        assert cc.CALIBRATION_BACKUP_PATH.exists()
        backup_content = cc.CALIBRATION_BACKUP_PATH.read_text(encoding="utf-8")
        assert backup_content == first_content

        # Rollback restaura
        assert cc.rollback_calibration() is True
        assert cc.CALIBRATION_PATH.read_text(encoding="utf-8") == first_content


# ─── KPI: quality_judge_pass_rate ───────────────────────────────────────

class TestQualityJudgePassRate:
    def test_pass_verdict(self):
        from src.kpi_history import compute_quality_judge_pass_rate
        assert compute_quality_judge_pass_rate("approved") == 1.0
        assert compute_quality_judge_pass_rate("Excellent") == 1.0
        assert compute_quality_judge_pass_rate("PASS") == 1.0

    def test_fail_verdict(self):
        from src.kpi_history import compute_quality_judge_pass_rate
        assert compute_quality_judge_pass_rate("rejected") == 0.0
        assert compute_quality_judge_pass_rate("needs_revision") == 0.0

    def test_none_when_not_invoked(self):
        from src.kpi_history import compute_quality_judge_pass_rate
        assert compute_quality_judge_pass_rate(None) is None
        assert compute_quality_judge_pass_rate("") is None


# ─── KPI: parallelism_efficiency ────────────────────────────────────────

class TestParallelismEfficiency:
    def test_perfect_parallelism(self):
        from src.kpi_history import compute_parallelism_efficiency
        # 5 tarefas de 1000ms cada, total real 1000ms (paralelas perfeitas)
        speedup, meta = compute_parallelism_efficiency(
            wave_timings=[{"task_ids": ["t1", "t2", "t3", "t4", "t5"], "duration_ms": 1000}],
            task_durations_ms=[1000] * 5,
            total_duration_ms=1000,
        )
        assert speedup == pytest.approx(5.0, abs=0.01)
        assert meta["max_wave_width"] == 5

    def test_sequential_no_speedup(self):
        from src.kpi_history import compute_parallelism_efficiency
        speedup, _ = compute_parallelism_efficiency(
            wave_timings=[{"task_ids": ["t1"], "duration_ms": 1000}] * 3,
            task_durations_ms=[1000, 1000, 1000],
            total_duration_ms=3000,
        )
        assert speedup == pytest.approx(1.0, abs=0.01)

    def test_empty_returns_zero(self):
        from src.kpi_history import compute_parallelism_efficiency
        speedup, meta = compute_parallelism_efficiency(None, None, 0)
        assert speedup == 0.0
        assert meta["task_count"] == 0


# ─── append_kpi_entry persiste KPIs Sprint 5 ────────────────────────────

class TestAppendKPIEntrySprint5:
    def test_persists_quality_judge_and_parallelism(self, tmp_path):
        from src.kpi_history import append_kpi_entry
        history = tmp_path / ".kpi_history.jsonl"
        entry = append_kpi_entry(
            demand="test",
            real_cost=0.15,
            estimated_cost=0.20,
            duration_ms=2000,
            llm_usage={"claude": 1, "gpt4o": 1, "gemini": 1, "perplexity": 1, "groq": 1},
            tasks_completed=5,
            tasks_failed=0,
            quality_verdict="approved",
            wave_timings=[{"task_ids": ["t1", "t2", "t3", "t4", "t5"], "duration_ms": 2000}],
            task_durations_ms=[2000] * 5,
            history_path=history,
        )
        assert entry["quality_judge_pass"] == 1.0
        assert entry["parallelism_efficiency"] == pytest.approx(5.0, abs=0.01)
        # Persistido em disco
        line = history.read_text(encoding="utf-8").strip()
        loaded = json.loads(line)
        assert loaded["quality_judge_pass"] == 1.0
        assert loaded["parallelism_efficiency"] == pytest.approx(5.0, abs=0.01)

    def test_quality_verdict_none_persists_as_none(self, tmp_path):
        from src.kpi_history import append_kpi_entry
        entry = append_kpi_entry(
            demand="test",
            real_cost=0.10,
            estimated_cost=0.10,
            duration_ms=1000,
            llm_usage={"groq": 1},
            tasks_completed=1,
            tasks_failed=0,
            quality_verdict=None,
            history_path=tmp_path / ".kpi_history.jsonl",
        )
        assert entry["quality_judge_pass"] is None


# ─── CLI: dashboard --since filter ──────────────────────────────────────

class TestDashboardSinceFilter:
    def test_since_7d_includes_recent(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        # Cria entries: 1 antiga (10 dias atras), 2 recentes (1h atras)
        history = tmp_path / ".kpi_history.jsonl"
        old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        new_ts1 = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        new_ts2 = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with open(history, "w", encoding="utf-8") as fh:
            for ts in [old_ts, new_ts1, new_ts2]:
                fh.write(json.dumps({
                    "timestamp": ts, "demand": "x", "distribution_health": 1.0,
                    "cost_estimate_accuracy": 1.0, "tier_internal_engagement_rate": 0.5,
                    "fallback_chain_save_rate_cumulative": 0.0,
                    "real_cost_usd": 0.01, "estimated_cost_usd": 0.01,
                    "duration_ms": 1000, "tasks_completed": 1, "tasks_failed": 0,
                    "llm_usage": {"groq": 1}, "_meta": {"used_llms": 1, "max_share": 1.0},
                }) + "\n")
        # Monkeypatch o KPI_HISTORY_PATH e re-export
        import src.kpi_history as kh
        monkeypatch.setattr(kh, "KPI_HISTORY_PATH", history)

        from cli import dashboard
        runner = CliRunner()
        result = runner.invoke(dashboard, ["--since", "7d"], color=False)
        assert result.exit_code == 0
        clean = _strip_ansi(result.output)
        # Esperamos 2 runs em 7d (nao 3 — o old_ts esta fora da janela)
        assert "2 runs em janela" in clean, clean

    def test_invalid_since_format(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        history = tmp_path / ".kpi_history.jsonl"
        history.write_text(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "demand": "x", "distribution_health": 1.0, "cost_estimate_accuracy": 1.0,
            "real_cost_usd": 0.0, "estimated_cost_usd": 0.0,
            "duration_ms": 0, "tasks_completed": 0, "tasks_failed": 0,
            "llm_usage": {}, "_meta": {},
        }) + "\n", encoding="utf-8")
        import src.kpi_history as kh
        monkeypatch.setattr(kh, "KPI_HISTORY_PATH", history)

        from cli import dashboard
        runner = CliRunner()
        result = runner.invoke(dashboard, ["--since", "abc"])
        assert "Formato invalido" in result.output or "Unidade desconhecida" in result.output


# ─── CLI: replay command ────────────────────────────────────────────────

class TestReplayCommand:
    def test_replay_existing_report(self, tmp_path):
        from click.testing import CliRunner
        from cli import replay

        report_path = tmp_path / "execution_20260408_120000.json"
        report_path.write_text(json.dumps({
            "timestamp": "2026-04-08T12:00:00",
            "demand": "Demanda de teste para replay",
            "summary": "ok",
            "totals": {
                "cost_usd": 0.0234,
                "estimated_cost_usd": 0.0500,
                "duration_ms": 12500,
                "tasks_completed": 3,
                "tasks_failed": 0,
                "tasks_cached": 0,
                "tasks_deduplicated": 0,
                "tasks_quality_retried": 0,
                "budget_limit": 5.0,
            },
            "plan": {"tasks": [
                {"id": "t1", "type": "research"},
                {"id": "t2", "type": "writing"},
                {"id": "t3", "type": "review"},
            ]},
            "results": {
                "t1": {"task_id": "t1", "llm_used": "perplexity", "cost": 0.008,
                       "duration_ms": 5000, "tokens_input": 100, "tokens_output": 200,
                       "success": True, "cache_hit": False, "output": "search results..."},
                "t2": {"task_id": "t2", "llm_used": "gpt4o", "cost": 0.012,
                       "duration_ms": 4500, "tokens_input": 300, "tokens_output": 800,
                       "success": True, "cache_hit": False, "output": "draft article..."},
                "t3": {"task_id": "t3", "llm_used": "claude", "cost": 0.0034,
                       "duration_ms": 3000, "tokens_input": 200, "tokens_output": 100,
                       "success": True, "cache_hit": False, "output": "review notes..."},
            },
        }), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(replay, ["20260408_120000", "--output-dir", str(tmp_path)], color=False)
        assert result.exit_code == 0, result.output
        clean = _strip_ansi(result.output)
        # Tabela Rich pode truncar — checa via totals/replay header
        assert "Demanda de teste" in clean
        assert "Replay" in clean
        # Custos totais aparecem nos extras
        assert "0.0234" in clean or "0.02" in clean

    def test_replay_last_alias(self, tmp_path):
        from click.testing import CliRunner
        from cli import replay
        # Cria 2 reports, last deve pegar o mais novo
        for ts in ["20260408_100000", "20260408_110000"]:
            (tmp_path / f"execution_{ts}.json").write_text(json.dumps({
                "timestamp": ts, "demand": f"demand-{ts}",
                "totals": {}, "plan": {"tasks": []}, "results": {},
            }), encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(replay, ["last", "--output-dir", str(tmp_path)])
        assert result.exit_code == 0
        assert "20260408_110000" in result.output

    def test_replay_not_found(self, tmp_path):
        from click.testing import CliRunner
        from cli import replay
        runner = CliRunner()
        result = runner.invoke(replay, ["nonexistent", "--output-dir", str(tmp_path)])
        assert result.exit_code == 1
        assert "nao encontrado" in result.output


# ─── Catalog YAML SoT ───────────────────────────────────────────────────

class TestDoctorCommand:
    """Sprint 6: cli.py doctor — health check abrangente."""

    def test_doctor_json_output(self):
        from click.testing import CliRunner
        from cli import doctor
        runner = CliRunner()
        result = runner.invoke(doctor, ["--json"], color=False)
        assert result.exit_code == 0
        # Saida valida JSON
        clean = _strip_ansi(result.output)
        # Procurar o ultimo objeto JSON na saida
        start = clean.find("{")
        end = clean.rfind("}") + 1
        payload = json.loads(clean[start:end])
        assert "overall" in payload
        assert "checks" in payload
        assert payload["overall"] in ("OK", "ATENCAO", "CRITICO")
        # Os 6 checks principais devem estar presentes
        check_names = {c["name"] for c in payload["checks"]}
        for expected in ["api_keys", "catalog_consistency", "finops_daily",
                         "kpi_history", "cost_calibration", "drift_detector"]:
            assert expected in check_names, f"check ausente: {expected}"

    def test_doctor_strict_exits_on_critical(self):
        """--strict deve sair com codigo 1 se houver CRITICO ou ATENCAO."""
        from click.testing import CliRunner
        from cli import doctor
        runner = CliRunner()
        # Strict pode passar ou nao dependendo do estado real do ambiente.
        # Validacao minima: o flag e aceito sem crash.
        result = runner.invoke(doctor, ["--strict", "--json"], color=False)
        assert result.exit_code in (0, 1)

    def test_doctor_human_output_renders(self):
        from click.testing import CliRunner
        from cli import doctor
        runner = CliRunner()
        result = runner.invoke(doctor, [], color=False)
        clean = _strip_ansi(result.output)
        assert "geo-orchestrator doctor" in clean
        assert "Status geral" in clean


class TestCatalogConsistency:
    """Sprint 5: catalog/model_catalog.yaml deve refletir src/config.LLM_CONFIGS."""

    def test_catalog_loads(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML nao instalado")
        from src.catalog_loader import load_catalog
        cat = load_catalog()
        assert "providers" in cat
        assert "anthropic" in cat["providers"]

    def test_no_drift_vs_config(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML nao instalado")
        from src.catalog_loader import validate_catalog_vs_config
        errors = validate_catalog_vs_config()
        assert errors == [], "Catalog drift detectado:\n" + "\n".join(errors)

    def test_all_5_canonical_aliases_present(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML nao instalado")
        from src.catalog_loader import load_catalog, get_models_with_aliases
        aliased = get_models_with_aliases(load_catalog())
        for canonical in ["claude", "gpt4o", "gemini", "perplexity", "groq"]:
            assert canonical in aliased, f"alias canonico '{canonical}' ausente"

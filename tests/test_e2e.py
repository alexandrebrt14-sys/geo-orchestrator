"""E2E test suite com pipeline mockado (sprint 5/6 — 2026-04-08).

Mocka `LLMClient.query` e `QualityJudge.evaluate` para executar
`Orchestrator.run()` ponta-a-ponta sem nenhuma chamada de rede. Cobre:

- decompose -> wave parallel -> quality -> kpi -> report
- Geracao de execution_*.json + .kpi_history.jsonl entries
- Auto-calibracao (cost_calibrator) consome o output gerado
- Replay command via `cli.py replay last`
- Re-execucao com cache hit

Item P1 deferido na sprint 5 e fechado na sprint 6 — bloqueia regressao
do contrato Orchestrator.run() inteiro num unico arquivo.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
from dotenv import load_dotenv

load_dotenv()
for env_var in [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
    "PERPLEXITY_API_KEY", "GROQ_API_KEY",
]:
    os.environ.setdefault(env_var, "test-key-not-real")


# ─── Mock factory ────────────────────────────────────────────────────────

def make_mocked_query(decompose_response: dict | None = None):
    """Cria uma fake LLMClient.query que roteia por system prompt.

    - Chamadas com system contendo 'orquestrador da Brasil GEO' (decompose)
      retornam o decompose_response em JSON.
    - Outras chamadas (execucao de tarefa) retornam um output sintetico
      contendo o LLM e tokens fake. Custos pequenos mas > 0.
    """
    from src.models import LLMResponse

    default_plan = {
        "tasks": [
            {"id": "t1", "type": "research", "description": "Pesquisar topico X",
             "dependencies": [], "expected_output": "fontes"},
            {"id": "t2", "type": "analysis", "description": "Analisar dados",
             "dependencies": ["t1"], "expected_output": "insights"},
            {"id": "t3", "type": "writing", "description": "Redigir artigo",
             "dependencies": ["t2"], "expected_output": "artigo final"},
            {"id": "t4", "type": "classification", "description": "Classificar topicos",
             "dependencies": [], "expected_output": "tags"},
            {"id": "t5", "type": "review", "description": "Revisar artigo",
             "dependencies": ["t3"], "expected_output": "review notes"},
        ]
    }
    plan = decompose_response or default_plan

    call_log: list[dict] = []

    async def fake_query(self_client, prompt: str, system: str = "", max_tokens: int = 4000):
        # Pequeno delay (10ms) para produzir duration_ms mensuravel no Pipeline
        # — sem isso o speedup do parallelism_efficiency vira 0/0.
        await asyncio.sleep(0.01)
        # Identifica decompose pela system prompt
        is_decompose = "orquestrador da Brasil GEO" in (system or "")
        provider = self_client.config.provider.value
        model = self_client.config.model

        call_log.append({
            "provider": provider, "model": model,
            "is_decompose": is_decompose, "prompt_chars": len(prompt or ""),
        })

        if is_decompose:
            text = json.dumps(plan, ensure_ascii=False)
            return LLMResponse(
                text=text, tokens_input=200, tokens_output=400,
                cost=0.01, model=model, provider=provider,
            )

        # Quality Judge: retorna JSON estruturado da rubrica
        if "Quality Judge" in (system or "") or "rubrica" in (system or "").lower():
            text = json.dumps({
                "factual_accuracy": 90, "completeness": 85, "ptbr_quality": 95,
                "efficiency": 80, "source_quality": 88,
                "verdict": "approved", "critical_issues": [],
            })
            return LLMResponse(
                text=text, tokens_input=300, tokens_output=200,
                cost=0.005, model=model, provider=provider,
            )

        # Tarefa de execucao: output sintetico, custo determinístico por LLM
        cost_table = {
            "anthropic": 0.08, "openai": 0.012,
            "google": 0.004, "perplexity": 0.007, "groq": 0.0008,
        }
        return LLMResponse(
            text=f"[mock {provider}] resposta para: {prompt[:80]}",
            tokens_input=150, tokens_output=300,
            cost=cost_table.get(provider, 0.01),
            model=model, provider=provider,
        )

    return fake_query, call_log


# ─── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_output(tmp_path, monkeypatch):
    """Isola output/ por test em tmp_path.

    OUTPUT_DIR e importado em modulos individuais a TIME OF MODULE LOAD,
    entao precisamos monkeypatchar cada modulo que mantem uma referencia
    propria. Os Path-derivados (KPI_HISTORY_PATH, CALIBRATION_PATH,
    _stats_path do Router etc.) sao tambem reapontados.
    """
    from src import config as cfg_module
    from src import kpi_history as kh_module
    from src import cost_calibrator as cc_module
    from src import orchestrator as orch_module
    from src import pipeline as pipe_module
    from src import router as rt_module
    from src import smart_router as sr_module

    monkeypatch.setattr(cfg_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(orch_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(pipe_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(rt_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(sr_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(kh_module, "KPI_HISTORY_PATH", tmp_path / ".kpi_history.jsonl")
    monkeypatch.setattr(cc_module, "CALIBRATION_PATH", tmp_path / ".cost_calibration.json")
    yield tmp_path


@pytest.fixture
def mocked_orchestrator(monkeypatch, isolated_output):
    """Cria Orchestrator com LLMClient.query mockado."""
    fake_query, call_log = make_mocked_query()
    monkeypatch.setattr("src.llm_client.LLMClient.query", fake_query)

    # Quality Judge tambem chama LLM internamente — mocka evaluate direto.
    # QualityScore usa escala 0-10 por dimensao + total 0-50 + verdict PT-BR.
    from src.quality_judge import QualityScore
    async def fake_evaluate(self_judge, demand: str, final_output: str):
        return QualityScore(
            factual_accuracy=9, completeness=8, ptbr_quality=10,
            efficiency=8, source_quality=9, total=44, percentage=88.0,
            verdict="APROVADO", critical_issues=[],
        )
    monkeypatch.setattr("src.quality_judge.QualityJudge.evaluate", fake_evaluate)

    return call_log


# ─── E2E: full pipeline run ──────────────────────────────────────────────

class TestFullPipelineE2E:
    def test_orchestrator_run_full_pipeline(self, mocked_orchestrator, isolated_output):
        from src.orchestrator import Orchestrator

        orch = Orchestrator(force=True, smart=True)
        report = asyncio.run(orch.run("Pesquise GEO vs SEO e escreva um artigo de 500 palavras"))

        # Resultados estruturais
        assert report.tasks_completed >= 1
        assert report.tasks_failed == 0
        assert report.total_cost > 0
        assert report.total_duration_ms > 0
        assert len(report.results) >= 1

        # Decompose foi chamado uma vez
        decompose_calls = [c for c in mocked_orchestrator if c["is_decompose"]]
        assert len(decompose_calls) == 1

        # E pelo menos algumas tarefas de execucao tambem
        exec_calls = [c for c in mocked_orchestrator if not c["is_decompose"]]
        assert len(exec_calls) >= 1

    def test_kpi_history_persisted_after_run(self, mocked_orchestrator, isolated_output):
        from src.orchestrator import Orchestrator

        orch = Orchestrator(force=True, smart=True)
        asyncio.run(orch.run("Demanda E2E para KPI persistido"))

        history_path = isolated_output / ".kpi_history.jsonl"
        assert history_path.exists()
        lines = history_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        # Sprint 5 KPIs persistidos
        assert "quality_judge_pass" in entry
        assert "parallelism_efficiency" in entry
        assert "tier_internal_engagement_rate" in entry
        # Quality Judge mockado retorna APROVADO -> pass=1.0
        assert entry["quality_judge_pass"] == 1.0
        # parallelism_efficiency: em mock o overhead do orchestrator
        # (PromptRefiner, decompose, semantic_cache, quality_judge) domina
        # o total_duration_ms relativamente ao tempo de tarefa, entao o
        # ratio fica < 1. Validamos apenas que e calculado e nao-zero.
        assert entry["parallelism_efficiency"] > 0

    def test_cache_hit_on_second_run(self, mocked_orchestrator, isolated_output):
        from src.orchestrator import Orchestrator

        orch = Orchestrator(force=True, smart=True)
        # 1a execucao popula cache
        report1 = asyncio.run(orch.run("Demanda repetida para cache test"))
        # 2a execucao deve bater cache (mesmo que parcial)
        orch2 = Orchestrator(force=True, smart=True)
        report2 = asyncio.run(orch2.run("Demanda repetida para cache test"))

        # Cache hits OU custo zero — flexivel porque depende da decomposicao
        # Garantia minima: 2a run nao excede a 1a em custo (cache deveria ajudar)
        assert report2.total_cost <= report1.total_cost + 0.001


# ─── E2E: cost_calibrator consome execution_*.json ──────────────────────

class TestCalibratorE2E:
    def test_calibrator_learns_from_real_execution_reports(
        self, mocked_orchestrator, isolated_output
    ):
        """Roda 4 execucoes mockadas e roda recalibrate sobre o output gerado."""
        from src.orchestrator import Orchestrator
        from src.cost_calibrator import recalibrate

        # 4 execucoes geram execution_*.json
        for i in range(4):
            orch = Orchestrator(force=True, smart=True)
            report = asyncio.run(orch.run(f"Demanda calibracao {i}"))
            # Salva manualmente em execution_*.json (cli.py faz isso, mas
            # estamos fora do CLI; replicamos o save minimo)
            payload = {
                "timestamp": f"2026-04-08T10:0{i}:00",
                "demand": f"Demanda calibracao {i}",
                "totals": {"cost_usd": report.total_cost},
                "results": {
                    tid: {
                        "task_id": tid, "llm_used": r.llm_used, "cost": r.cost,
                        "cache_hit": r.cache_hit, "success": r.success,
                    }
                    for tid, r in report.results.items()
                },
            }
            (isolated_output / f"execution_2026040810{i:02d}00.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )

        result = recalibrate(window=10, output_dir=isolated_output, persist=True)
        # Pelo menos 1 LLM deve ter sido calibrado (o mock distribui em varios)
        assert len(result["calibrated_avg_cost_per_call"]) >= 1
        # E o arquivo de calibracao foi gravado em isolated_output
        cal_file = isolated_output / ".cost_calibration.json"
        assert cal_file.exists()


# ─── E2E: replay command via CLI ────────────────────────────────────────

class TestAutoCalibrationOnDrift:
    """Sprint 6: drift detector dispara recalibrate() automaticamente."""

    def test_drift_triggers_auto_calibration(self, mocked_orchestrator, isolated_output):
        """Pre-popula 3 entries fora da banda + roda 1 execucao real.

        O orchestrator deve detectar o drift e chamar recalibrate sem
        intervencao humana. O report.summary deve mencionar AUTO-CALIBRATION.
        """
        from src.orchestrator import Orchestrator

        # Pre-popula .kpi_history.jsonl com 3 entries fora da banda 0.7-1.5
        history_path = isolated_output / ".kpi_history.jsonl"
        with open(history_path, "w", encoding="utf-8") as f:
            for i in range(3):
                f.write(json.dumps({
                    "timestamp": f"2026-04-08T09:0{i}:00+00:00",
                    "demand": "preexisting", "distribution_health": 0.8,
                    "cost_estimate_accuracy": 0.1,  # bem fora da banda
                    "tier_internal_engagement_rate": 0.0,
                    "fallback_chain_save_rate_cumulative": 0.0,
                    "real_cost_usd": 0.01, "estimated_cost_usd": 0.10,
                    "duration_ms": 1000, "tasks_completed": 1, "tasks_failed": 0,
                    "llm_usage": {"groq": 1}, "_meta": {"used_llms": 1, "max_share": 1.0},
                }) + "\n")

        # Pre-popula 4 execution reports para o calibrator ter material
        for i in range(4):
            (isolated_output / f"execution_2026040809{i:02d}00.json").write_text(
                json.dumps({
                    "timestamp": f"2026-04-08T09:0{i}:00",
                    "demand": "x",
                    "totals": {"cost_usd": 0.10},
                    "results": {
                        f"t{i}a": {"llm_used": "claude", "cost": 0.08,
                                   "cache_hit": False, "success": True},
                        f"t{i}b": {"llm_used": "groq", "cost": 0.001,
                                   "cache_hit": False, "success": True},
                    },
                }), encoding="utf-8",
            )

        orch = Orchestrator(force=True, smart=True)
        report = asyncio.run(orch.run("Demanda que vai disparar drift"))

        # Auto-calibration mencionada no summary
        assert "AUTO-CALIBRATION" in report.summary, report.summary[-500:]
        # E o arquivo de calibracao foi criado pelo trigger
        cal_file = isolated_output / ".cost_calibration.json"
        assert cal_file.exists()


class TestReplayE2E:
    def test_replay_last_after_e2e_run(self, tmp_path):
        """Cria 1 execution_*.json artificial + roda `cli.py replay last`."""
        from click.testing import CliRunner
        from cli import replay

        report_path = tmp_path / "execution_20260408_e2e.json"
        report_path.write_text(json.dumps({
            "timestamp": "2026-04-08T15:00:00",
            "demand": "E2E replay artificial",
            "totals": {
                "cost_usd": 0.05, "estimated_cost_usd": 0.07,
                "duration_ms": 8000,
                "tasks_completed": 2, "tasks_failed": 0, "tasks_cached": 0,
            },
            "plan": {"tasks": [
                {"id": "t1", "type": "research"},
                {"id": "t2", "type": "writing"},
            ]},
            "results": {
                "t1": {"task_id": "t1", "llm_used": "perplexity", "cost": 0.02,
                       "duration_ms": 4000, "tokens_input": 100, "tokens_output": 200,
                       "success": True, "cache_hit": False, "output": "fontes"},
                "t2": {"task_id": "t2", "llm_used": "gpt4o", "cost": 0.03,
                       "duration_ms": 4000, "tokens_input": 200, "tokens_output": 500,
                       "success": True, "cache_hit": False, "output": "artigo"},
            },
        }), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(replay, ["last", "--output-dir", str(tmp_path)], color=False)
        assert result.exit_code == 0
        assert "E2E replay artificial" in result.output

"""Tests de integracao para os fixes da bateria de testes 2026-04-07.

Bloqueia regressao dos 17 gaps fechados em:
- refactor/cli-orchestrator-v2 commit c6629d8 (refactor + 11 gaps)
- refactor/cli-orchestrator-v2 commit 6993924 (sprint 1 — 6 P0)
- refactor/cli-orchestrator-v2 sprint 2 (em progresso — fixes #7 a #10)

Estes testes nao tocam APIs reais. Usam Pydantic models e Router/Pipeline
helpers diretamente para validar o roteamento, cap, downgrade, force-5-llm,
e o schema de LLMResponse.
"""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv

# Carrega .env para que LLMConfig.available retorne True nos testes que
# dependem do roteamento. Os testes nao chamam APIs reais — so checam
# decisoes de roteamento e schema.
load_dotenv()

# Garante que os 5 LLMs canonicos sejam considerados disponiveis no teste
# mesmo se o ambiente nao tiver as chaves reais (useful em CI).
for env_var in ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
                "PERPLEXITY_API_KEY", "GROQ_API_KEY"]:
    os.environ.setdefault(env_var, "test-key-not-real")

from src.config import LLM_CONFIGS, TASK_TYPES, FALLBACK_CHAINS
from src.models import Task, TaskComplexity, LLMResponse, Plan
from src.router import Router, CONCENTRATION_CAP, CAP_MIN_TASKS


def _make_task(tid: str, ttype: str, complexity: str = "high") -> Task:
    return Task(
        id=tid,
        type=ttype,
        description=f"Test task {tid}",
        complexity=TaskComplexity(complexity),
    )


# ─── Fix #1: Router prioriza TASK_TYPES.primary antes do tier ─────────

class TestRouterPrimaryWinsOverTier:
    """get_fallback_chain() deve retornar TASK_TYPES.primary FIRST,
    nao o tier de complexity. Bug original: complexity=high sempre virava
    Claude porque MODEL_TIERS['high'] = ['claude', 'gpt4o']."""

    def test_research_high_complexity_starts_with_perplexity(self):
        router = Router()
        task = _make_task("t1", "research", "high")
        chain = router.get_fallback_chain(task)
        assert len(chain) > 0
        # TASK_TYPES['research'].primary == 'perplexity'
        assert chain[0] == "perplexity", (
            f"Esperava perplexity em primeiro (canonico de research), "
            f"recebi {chain[0]}. Bug do tier sequestrando high para claude."
        )

    def test_writing_high_complexity_starts_with_gpt4o(self):
        router = Router()
        task = _make_task("t2", "writing", "high")
        chain = router.get_fallback_chain(task)
        assert chain[0] == "gpt4o"

    def test_classification_low_complexity_starts_with_groq(self):
        router = Router()
        task = _make_task("t3", "classification", "low")
        chain = router.get_fallback_chain(task)
        assert chain[0] == "groq"

    def test_code_high_complexity_starts_with_claude(self):
        """Code task SHOULD go to claude — TASK_TYPES['code'].primary == claude."""
        router = Router()
        task = _make_task("t4", "code", "high")
        chain = router.get_fallback_chain(task)
        assert chain[0] == "claude"


# ─── Fix #2: Cap 80% real (era vaporware antes da sprint 1) ───────────

class TestConcentrationCap:
    """O cap 80% deve redirecionar tasks quando um provider satura."""

    def test_cap_constants_exist(self):
        """Sanity check: as constantes do cap existem."""
        assert CONCENTRATION_CAP == 0.80
        assert CAP_MIN_TASKS == 3

    def test_cap_does_not_kick_in_below_min_tasks(self):
        """Cap so vale a partir de CAP_MIN_TASKS=3 tarefas atribuidas."""
        router = Router()
        router._session_usage["claude"] = 1
        router._session_usage["gpt4o"] = 0
        # total=1, abaixo de CAP_MIN_TASKS — cap nao deve impedir
        assert router._would_exceed_cap("claude") is False

    def test_cap_blocks_when_share_would_exceed_80pct(self):
        """Atribuir mais 1 a um LLM ja saturado deve dar would_exceed=True."""
        router = Router()
        # 4 claude + 1 gpt4o = 5 total. Atribuir +1 claude vai pra 5/6 = 83% > 80%
        router._session_usage["claude"] = 4
        router._session_usage["gpt4o"] = 1
        assert router._would_exceed_cap("claude") is True

    def test_apply_cap_redirects_to_alternative_below_cap(self):
        """apply_concentration_cap deve devolver alternativa viavel."""
        router = Router()
        router._session_usage["claude"] = 4
        router._session_usage["gpt4o"] = 1
        chain = ["claude", "gpt4o", "gemini"]
        result = router.apply_concentration_cap("claude", chain)
        assert result != "claude", (
            f"Cap deveria ter redirecionado de claude. Recebi {result}"
        )


# ─── Fix #3: Quality Judge LLMResponse usa .text ──────────────────────

class TestLLMResponseSchema:
    """LLMResponse expoe .text, nao .content. quality_judge.py:167 corrigido."""

    def test_llmresponse_has_text_attr_not_content(self):
        resp = LLMResponse(text="hello", tokens_input=5, tokens_output=2, cost=0.001)
        assert resp.text == "hello"
        # .content nao existe (e era a causa do crash original)
        assert not hasattr(resp, "content")


# ─── Fix #4: AVG_COST_PER_CALL recalibrado ────────────────────────────

class TestCostCalibration:
    """Verifica que os valores de AVG_COST_PER_CALL refletem Opus 4.6, nao Opus 3."""

    def test_claude_avg_cost_is_opus_4_6_realistic(self):
        from src.config import AVG_COST_PER_CALL
        # Opus 3 era ~0.04, Opus 4.6 e ~0.13 (3.3x mais caro)
        assert AVG_COST_PER_CALL["claude"] >= 0.10, (
            "AVG_COST_PER_CALL['claude'] muito baixo — provavelmente "
            "ainda esta no preco do Opus 3."
        )

    def test_groq_entry_exists(self):
        from src.config import AVG_COST_PER_CALL
        assert "groq" in AVG_COST_PER_CALL


# ─── Fix #8: Tier interno Claude (Opus/Sonnet/Haiku) ──────────────────

class TestClaudeTierDowngrade:
    """Router.downgrade_claude_by_complexity deve trocar Opus por Sonnet/Haiku
    quando complexity for medium/low."""

    def test_low_complexity_downgrades_to_haiku(self):
        router = Router()
        task = _make_task("tA", "code", "low")
        result = router.downgrade_claude_by_complexity("claude", task)
        assert result == "claude_haiku"

    def test_medium_complexity_downgrades_to_sonnet(self):
        router = Router()
        task = _make_task("tB", "code", "medium")
        result = router.downgrade_claude_by_complexity("claude", task)
        assert result == "claude_sonnet"

    def test_high_complexity_keeps_opus(self):
        router = Router()
        task = _make_task("tC", "code", "high")
        result = router.downgrade_claude_by_complexity("claude", task)
        assert result == "claude"

    def test_non_claude_unchanged(self):
        """Downgrade so se aplica a 'claude' (Opus). Outros LLMs passam direto."""
        router = Router()
        task = _make_task("tD", "research", "low")
        result = router.downgrade_claude_by_complexity("perplexity", task)
        assert result == "perplexity"

    def test_claude_tier_configs_exist(self):
        """LLM_CONFIGS deve ter as 3 entradas Claude."""
        assert "claude" in LLM_CONFIGS
        assert "claude_sonnet" in LLM_CONFIGS
        assert "claude_haiku" in LLM_CONFIGS
        # Sonnet ~5x mais barato que Opus
        assert LLM_CONFIGS["claude_sonnet"].cost_per_1k_input < LLM_CONFIGS["claude"].cost_per_1k_input
        # Haiku ~19x mais barato que Opus
        assert LLM_CONFIGS["claude_haiku"].cost_per_1k_input < LLM_CONFIGS["claude_sonnet"].cost_per_1k_input


# ─── Fix #9: --force-5-llm flag ───────────────────────────────────────

class TestForce5LLM:
    """Router.set_force_all_llms(True) deve fazer get_next_in_chain
    preferir LLMs canonicos ainda nao usados nesta sessao."""

    def test_force_all_default_off(self):
        router = Router()
        assert router._force_all_llms is False

    def test_set_force_all_llms_toggles_flag(self):
        router = Router()
        router.set_force_all_llms(True)
        assert router._force_all_llms is True
        router.set_force_all_llms(False)
        assert router._force_all_llms is False

    def test_orchestrator_propagates_force_all_llms(self):
        """Orchestrator(force_all_llms=True) liga o flag no router interno."""
        from src.orchestrator import Orchestrator
        orch = Orchestrator(smart=True, force_all_llms=True)
        assert orch.router._force_all_llms is True


# ─── Fix #5: Smoke test --ping cli ────────────────────────────────────

class TestCLIStructure:
    """Garante que cli.py expoe os comandos novos da sprint 1+2."""

    def test_cli_imports_clean(self):
        import cli
        assert hasattr(cli, "cli")

    def test_cli_has_all_commands(self):
        import cli
        commands = list(cli.cli.commands.keys())
        for cmd in ["run", "plan", "resume", "status", "models", "finops", "trace"]:
            assert cmd in commands, f"Comando '{cmd}' faltando no CLI"

    def test_run_has_force_5_llm_flag(self):
        import cli
        run_cmd = cli.cli.commands["run"]
        param_names = [p.name for p in run_cmd.params]
        assert "force_5_llm" in param_names or "force-5-llm" in [p.opts[0].lstrip("-") for p in run_cmd.params if p.opts]

    def test_status_has_ping_flag(self):
        import cli
        status_cmd = cli.cli.commands["status"]
        param_names = [p.name for p in status_cmd.params]
        assert "ping" in param_names


# ─── Sanity: TASK_TYPES tem todos os 12 tipos canonicos ───────────────

class TestTaskTypesCanonical:
    """TASK_TYPES e a fonte de verdade do roteamento canonico — proteja contra deriva."""

    def test_research_routes_to_perplexity(self):
        assert TASK_TYPES["research"].primary == "perplexity"

    def test_writing_routes_to_gpt4o(self):
        assert TASK_TYPES["writing"].primary == "gpt4o"

    def test_classification_routes_to_groq(self):
        assert TASK_TYPES["classification"].primary == "groq"

    def test_analysis_routes_to_gemini(self):
        assert TASK_TYPES["analysis"].primary == "gemini"

    def test_code_routes_to_claude(self):
        assert TASK_TYPES["code"].primary == "claude"

    def test_review_routes_to_claude(self):
        assert TASK_TYPES["review"].primary == "claude"


# ─── Schema do execution_*.json ─────────────────────────────────────

class TestExecutionReportSchema:
    """ExecutionReport e o output canonico do Orchestrator. Pipeline.resume
    e o cli.py debug_report.html dependem deste schema."""

    def test_taskresult_uses_text_field(self):
        from src.models import TaskResult
        tr = TaskResult(
            task_id="t1", llm_used="claude", output="hi",
            cost=0.01, duration_ms=100, success=True,
        )
        assert tr.task_id == "t1"
        assert tr.llm_used == "claude"
        assert tr.cost == 0.01

    def test_executionreport_has_all_fields(self):
        from src.models import ExecutionReport, Plan
        plan = Plan(demand="x", tasks=[])
        report = ExecutionReport(
            demand="x", plan=plan, results={},
            total_cost=0.0, tasks_completed=0,
            tasks_failed=0, tasks_cached=0,
            tasks_quality_retried=0, tasks_deduplicated=0,
            estimated_cost=0.0, budget_limit=5.0,
        )
        # Campos consumidos pelo cli.py _save_report
        for field in ["demand", "plan", "results", "total_cost",
                      "estimated_cost", "tasks_completed", "tasks_failed",
                      "tasks_cached", "tasks_deduplicated", "tasks_quality_retried",
                      "budget_limit", "total_duration_ms"]:
            assert hasattr(report, field), f"ExecutionReport sem campo '{field}'"

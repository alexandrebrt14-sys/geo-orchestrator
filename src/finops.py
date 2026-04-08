"""Unified FinOps governance module for the geo-orchestrator.

Provides pre-execution budget checks, real-time spend tracking per provider,
per-provider daily limit enforcement with fallback routing, cost-per-task
recording, session reports, and threshold-based alerts.

Persistence:
  output/.finops/daily_spend.json  — cumulative spend per provider per day
  output/.finops/task_costs.json   — cost record per task per session
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    AVG_COST_PER_CALL,
    BUDGET_LIMIT,
    FINOPS_DAILY_GLOBAL,
    FINOPS_DAILY_LIMITS,
    LLM_CONFIGS,
    OUTPUT_DIR,
    Provider,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alert thresholds (fraction of daily limit)
# ---------------------------------------------------------------------------
_WARN_THRESHOLD = 0.80
_BLOCK_THRESHOLD = 0.95

# ---------------------------------------------------------------------------
# Persistence paths
# ---------------------------------------------------------------------------
_FINOPS_DIR: Path = OUTPUT_DIR / ".finops"
_DAILY_SPEND_PATH: Path = _FINOPS_DIR / "daily_spend.json"
_TASK_COSTS_PATH: Path = _FINOPS_DIR / "task_costs.json"

# ---------------------------------------------------------------------------
# Provider name mapping (LLM name -> provider key used in FINOPS_DAILY_LIMITS)
# ---------------------------------------------------------------------------
_LLM_TO_PROVIDER: dict[str, str] = {}
for _name, _cfg in LLM_CONFIGS.items():
    _LLM_TO_PROVIDER[_name] = _cfg.provider.value


class BudgetExceededError(Exception):
    """Raised when a provider or global budget limit would be exceeded."""


class FinOps:
    """Singleton-style FinOps governance controller.

    Usage:
        finops = FinOps()
        finops.check_budget("anthropic")            # before LLM call
        finops.record_cost("task_1", "claude", 500, 200, 0.022)  # after call
        print(finops.session_report())               # end of pipeline
    """

    def __init__(self) -> None:
        self._daily_spend: dict[str, float] = {}
        self._task_costs: list[dict] = []
        self._session_estimated: float = 0.0
        self._session_start = datetime.now(timezone.utc).isoformat()
        self._today: str = ""

        _FINOPS_DIR.mkdir(parents=True, exist_ok=True)
        self._load_daily_spend()

    # ==================================================================
    # Persistence
    # ==================================================================

    def _current_date_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _load_daily_spend(self) -> None:
        """Load daily spend from disk. Reset if the date has changed."""
        today = self._current_date_key()
        self._today = today

        if _DAILY_SPEND_PATH.exists():
            try:
                data = json.loads(_DAILY_SPEND_PATH.read_text(encoding="utf-8"))
                if data.get("date") == today:
                    self._daily_spend = data.get("spend", {})
                    logger.info(
                        "FinOps: carregou gastos do dia %s — %s",
                        today,
                        {k: f"${v:.4f}" for k, v in self._daily_spend.items()},
                    )
                    return
                else:
                    logger.info(
                        "FinOps: dados de %s encontrados, mas hoje eh %s — resetando.",
                        data.get("date"), today,
                    )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("FinOps: falha ao ler daily_spend.json: %s", exc)

        # Initialize fresh spend counters
        self._daily_spend = {
            "anthropic": 0.0,
            "openai": 0.0,
            "google": 0.0,
            "perplexity": 0.0,
        }

    def _save_daily_spend(self) -> None:
        """Persist daily spend to disk."""
        data = {
            "date": self._today,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "spend": self._daily_spend,
        }
        _DAILY_SPEND_PATH.write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )

    def _save_task_costs(self) -> None:
        """Persist task costs for the current session."""
        data = {
            "session_start": self._session_start,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "tasks": self._task_costs,
        }
        _TASK_COSTS_PATH.write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )

    # ==================================================================
    # a) Pre-execution budget check
    # ==================================================================

    def pre_execution_check(self, task_count: int, llm_names: list[str] | None = None) -> None:
        """Estimate total cost before pipeline starts and block if over budget.

        Args:
            task_count: Number of tasks in the plan.
            llm_names: Optional list of LLM names to use for estimation.
                       If None, uses the average across all LLMs.

        Raises:
            BudgetExceededError: If estimated cost exceeds BUDGET_LIMIT.
        """
        # Sprint 5 (2026-04-08): usa AVG calibrado a partir do historico real
        from .cost_calibrator import get_calibrated_avg_cost
        avg_table = get_calibrated_avg_cost()
        if llm_names:
            avg = sum(avg_table.get(n, 0.01) for n in llm_names) / len(llm_names)
        else:
            avg = sum(avg_table.values()) / len(avg_table)

        estimated = task_count * avg
        self._session_estimated = estimated

        global_spent = sum(self._daily_spend.values())
        projected = global_spent + estimated

        logger.info(
            "FinOps pre-check: %d tarefas x $%.4f/tarefa = $%.4f estimado. "
            "Gasto global hoje: $%.4f. Projecao: $%.4f (limite: $%.2f).",
            task_count, avg, estimated, global_spent, projected, BUDGET_LIMIT,
        )

        if estimated > BUDGET_LIMIT:
            raise BudgetExceededError(
                f"Custo estimado (${estimated:.4f}) excede o limite por execucao "
                f"(${BUDGET_LIMIT:.2f}). Pipeline bloqueado."
            )

        if projected > FINOPS_DAILY_GLOBAL:
            raise BudgetExceededError(
                f"Custo projetado (${projected:.4f}) excederia o limite diario global "
                f"(${FINOPS_DAILY_GLOBAL:.2f}). Pipeline bloqueado."
            )

    # ==================================================================
    # b/c) Real-time spending tracking + per-provider limit enforcement
    # ==================================================================

    def check_budget(self, provider_or_llm: str) -> None:
        """Check if a provider still has budget available.

        Accepts either a provider name ("anthropic") or an LLM name ("claude").
        Raises BudgetExceededError if the provider has hit its daily limit.
        Logs a warning at 80% threshold.

        Args:
            provider_or_llm: Provider key or LLM config name.

        Raises:
            BudgetExceededError: If provider is at >= 95% of daily limit.
        """
        provider = self._resolve_provider(provider_or_llm)
        limit = FINOPS_DAILY_LIMITS.get(provider, 0.0)
        spent = self._daily_spend.get(provider, 0.0)

        # Also check global
        global_spent = sum(self._daily_spend.values())

        if limit > 0:
            ratio = spent / limit
            if ratio >= _BLOCK_THRESHOLD:
                raise BudgetExceededError(
                    f"Provider '{provider}' atingiu {ratio:.0%} do limite diario "
                    f"(${spent:.4f} / ${limit:.2f}). Chamadas bloqueadas."
                )
            if ratio >= _WARN_THRESHOLD:
                logger.warning(
                    "FinOps ALERTA: provider '%s' em %.0f%% do limite diario "
                    "(${spent:.4f} / ${limit:.2f}).",
                    provider, ratio * 100,
                )

        if FINOPS_DAILY_GLOBAL > 0 and global_spent >= FINOPS_DAILY_GLOBAL * _BLOCK_THRESHOLD:
            raise BudgetExceededError(
                f"Gasto global diario atingiu ${global_spent:.4f} "
                f"(limite: ${FINOPS_DAILY_GLOBAL:.2f}). Chamadas bloqueadas."
            )

    def is_provider_available(self, provider_or_llm: str) -> bool:
        """Check if a provider has budget remaining (non-raising version).

        Returns True if the provider can still accept calls.
        """
        try:
            self.check_budget(provider_or_llm)
            return True
        except BudgetExceededError:
            return False

    def get_cheapest_available(self) -> str | None:
        """Return the name of the cheapest available LLM with budget remaining.

        Returns None if all providers are exhausted.
        """
        from .cost_calibrator import get_calibrated_avg_cost
        avg_table = get_calibrated_avg_cost()
        candidates: list[tuple[str, float]] = []
        for llm_name, cfg in LLM_CONFIGS.items():
            if cfg.available and self.is_provider_available(llm_name):
                avg_cost = avg_table.get(llm_name, 0.01)
                candidates.append((llm_name, avg_cost))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[1])
        return candidates[0][0]

    # ==================================================================
    # d) Cost-per-task tracking
    # ==================================================================

    def record_cost(
        self,
        task_id: str,
        provider_or_llm: str,
        tokens_in: int,
        tokens_out: int,
        cost: float,
    ) -> None:
        """Record cost for a completed task and update daily spend.

        Args:
            task_id: Unique identifier for the task.
            provider_or_llm: Provider key or LLM config name.
            tokens_in: Number of input tokens.
            tokens_out: Number of output tokens.
            cost: Dollar cost of the call.
        """
        provider = self._resolve_provider(provider_or_llm)

        # Update daily spend
        self._daily_spend[provider] = self._daily_spend.get(provider, 0.0) + cost
        self._save_daily_spend()

        # Record task cost
        record = {
            "task_id": task_id,
            "provider": provider,
            "llm": provider_or_llm,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost": round(cost, 6),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._task_costs.append(record)
        self._save_task_costs()

        logger.info(
            "FinOps: tarefa '%s' -> provider '%s', $%.4f "
            "(tokens: %d in / %d out). Total dia '%s': $%.4f.",
            task_id, provider, cost, tokens_in, tokens_out,
            provider, self._daily_spend[provider],
        )

        # Check post-recording alerts
        self._check_alerts(provider)

    # ==================================================================
    # f) Alert system
    # ==================================================================

    def _check_alerts(self, provider: str) -> None:
        """Emit warnings/alerts based on current spend levels."""
        limit = FINOPS_DAILY_LIMITS.get(provider, 0.0)
        if limit <= 0:
            return

        spent = self._daily_spend.get(provider, 0.0)
        ratio = spent / limit

        if ratio >= _BLOCK_THRESHOLD:
            logger.warning(
                "FinOps BLOQUEIO: provider '%s' atingiu %.0f%% do limite diario "
                "($%.4f / $%.2f). Chamadas futuras serao bloqueadas.",
                provider, ratio * 100, spent, limit,
            )
        elif ratio >= _WARN_THRESHOLD:
            logger.warning(
                "FinOps ALERTA: provider '%s' atingiu %.0f%% do limite diario "
                "($%.4f / $%.2f).",
                provider, ratio * 100, spent, limit,
            )

    # ==================================================================
    # e) Session report
    # ==================================================================

    def session_report(self) -> str:
        """Generate a Markdown report for the current session.

        Includes:
        - Cost by provider table
        - Cost by task table
        - Budget remaining per provider
        - Efficiency metrics
        - Estimated vs actual cost comparison
        """
        lines: list[str] = []
        lines.append("# Relatorio FinOps da Sessao")
        lines.append("")
        lines.append(f"**Inicio da sessao:** {self._session_start}")
        lines.append(f"**Data:** {self._today}")
        lines.append("")

        # -- Cost by provider --
        lines.append("## Custo por Provider")
        lines.append("")
        lines.append("| Provider | Gasto Sessao (US$) | Gasto Dia (US$) | Limite Dia (US$) | Restante (US$) | Uso (%) |")
        lines.append("|----------|--------------------|-----------------|------------------|----------------|---------|")

        session_by_provider: dict[str, float] = {}
        session_tokens_in: int = 0
        session_tokens_out: int = 0
        session_total: float = 0.0

        for record in self._task_costs:
            prov = record["provider"]
            session_by_provider[prov] = session_by_provider.get(prov, 0.0) + record["cost"]
            session_tokens_in += record["tokens_in"]
            session_tokens_out += record["tokens_out"]
            session_total += record["cost"]

        for provider in sorted(set(list(FINOPS_DAILY_LIMITS.keys()) + list(session_by_provider.keys()))):
            session_cost = session_by_provider.get(provider, 0.0)
            day_cost = self._daily_spend.get(provider, 0.0)
            limit = FINOPS_DAILY_LIMITS.get(provider, 0.0)
            remaining = max(limit - day_cost, 0.0)
            pct = (day_cost / limit * 100) if limit > 0 else 0.0
            lines.append(
                f"| {provider} | {session_cost:.4f} | {day_cost:.4f} "
                f"| {limit:.2f} | {remaining:.4f} | {pct:.1f}% |"
            )

        lines.append("")
        lines.append(f"**Total da sessao:** US$ {session_total:.4f}")
        lines.append(f"**Total do dia (global):** US$ {sum(self._daily_spend.values()):.4f} / US$ {FINOPS_DAILY_GLOBAL:.2f}")
        lines.append("")

        # -- Cost by task --
        lines.append("## Custo por Tarefa")
        lines.append("")
        lines.append("| Tarefa | Provider | LLM | Tokens In | Tokens Out | Custo (US$) |")
        lines.append("|--------|----------|-----|-----------|------------|-------------|")

        for record in self._task_costs:
            lines.append(
                f"| {record['task_id']} | {record['provider']} | {record['llm']} "
                f"| {record['tokens_in']:,} | {record['tokens_out']:,} "
                f"| {record['cost']:.4f} |"
            )

        lines.append("")

        # -- Budget remaining --
        lines.append("## Orcamento Restante")
        lines.append("")
        for provider, limit in sorted(FINOPS_DAILY_LIMITS.items()):
            spent = self._daily_spend.get(provider, 0.0)
            remaining = max(limit - spent, 0.0)
            bar_len = 20
            used_bars = int((spent / limit) * bar_len) if limit > 0 else 0
            bar = "#" * used_bars + "-" * (bar_len - used_bars)
            lines.append(f"- **{provider}**: [{bar}] ${spent:.4f} / ${limit:.2f} (resta ${remaining:.4f})")
        lines.append("")

        # -- Efficiency --
        lines.append("## Eficiencia")
        lines.append("")
        total_tokens = session_tokens_in + session_tokens_out
        if total_tokens > 0:
            efficiency = session_tokens_out / total_tokens * 100
            lines.append(f"- **Tokens uteis (saida) / total:** {session_tokens_out:,} / {total_tokens:,} = {efficiency:.1f}%")
        else:
            lines.append("- **Tokens uteis / total:** N/A (nenhum token registrado)")
        lines.append(f"- **Tokens entrada:** {session_tokens_in:,}")
        lines.append(f"- **Tokens saida:** {session_tokens_out:,}")
        lines.append(f"- **Tarefas registradas:** {len(self._task_costs)}")
        lines.append("")

        # -- Estimated vs actual --
        lines.append("## Estimativa vs Real")
        lines.append("")
        lines.append(f"- **Custo estimado (pre-execucao):** US$ {self._session_estimated:.4f}")
        lines.append(f"- **Custo real:** US$ {session_total:.4f}")
        if self._session_estimated > 0:
            diff = session_total - self._session_estimated
            diff_pct = diff / self._session_estimated * 100
            direction = "acima" if diff > 0 else "abaixo"
            lines.append(f"- **Diferenca:** US$ {abs(diff):.4f} ({abs(diff_pct):.1f}% {direction} da estimativa)")
        lines.append("")

        return "\n".join(lines)

    def daily_status(self) -> dict:
        """Return current daily spend status as a dict (for CLI)."""
        status: dict[str, dict] = {}
        for provider, limit in FINOPS_DAILY_LIMITS.items():
            spent = self._daily_spend.get(provider, 0.0)
            remaining = max(limit - spent, 0.0)
            pct = (spent / limit * 100) if limit > 0 else 0.0
            status[provider] = {
                "spent": round(spent, 6),
                "limit": limit,
                "remaining": round(remaining, 6),
                "usage_pct": round(pct, 1),
            }
        status["_global"] = {
            "spent": round(sum(self._daily_spend.values()), 6),
            "limit": FINOPS_DAILY_GLOBAL,
            "remaining": round(max(FINOPS_DAILY_GLOBAL - sum(self._daily_spend.values()), 0.0), 6),
            "usage_pct": round(
                sum(self._daily_spend.values()) / FINOPS_DAILY_GLOBAL * 100
                if FINOPS_DAILY_GLOBAL > 0 else 0.0,
                1,
            ),
        }
        return status

    def reset_daily(self) -> None:
        """Force-reset all daily spend counters."""
        self._daily_spend = {
            "anthropic": 0.0,
            "openai": 0.0,
            "google": 0.0,
            "perplexity": 0.0,
        }
        self._today = self._current_date_key()
        self._save_daily_spend()
        logger.info("FinOps: contadores diarios resetados manualmente.")

    # ==================================================================
    # Internal helpers
    # ==================================================================

    def _resolve_provider(self, provider_or_llm: str) -> str:
        """Resolve an LLM name or provider name to a provider key.

        Examples:
            "claude" -> "anthropic"
            "gpt4o" -> "openai"
            "anthropic" -> "anthropic"
        """
        # Direct provider name
        if provider_or_llm in FINOPS_DAILY_LIMITS:
            return provider_or_llm

        # LLM name -> provider
        if provider_or_llm in _LLM_TO_PROVIDER:
            return _LLM_TO_PROVIDER[provider_or_llm]

        # Fallback: try lowercase match
        lower = provider_or_llm.lower()
        for key in FINOPS_DAILY_LIMITS:
            if lower in key or key in lower:
                return key

        logger.warning(
            "FinOps: nao foi possivel resolver provider para '%s', usando como esta.",
            provider_or_llm,
        )
        return provider_or_llm


# ---------------------------------------------------------------------------
# Module-level singleton for convenience
# ---------------------------------------------------------------------------
_instance: FinOps | None = None


def get_finops() -> FinOps:
    """Return the global FinOps singleton."""
    global _instance
    if _instance is None:
        _instance = FinOps()
    return _instance

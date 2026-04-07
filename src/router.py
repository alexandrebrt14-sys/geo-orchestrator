"""Task router — decides which LLM handles each task.

Uses cost-performance tiers, fallback chains per task type,
adaptive learning from success/failure rates, and complexity-aware routing.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .config import (
    FALLBACK_CHAINS,
    LLM_CONFIGS,
    MODEL_TIERS,
    OUTPUT_DIR,
    TASK_TYPES,
    LLMConfig,
)
from .models import Task, TaskComplexity

logger = logging.getLogger(__name__)

# Weighted score formula weights
_W_SUCCESS = 0.6
_W_COST = 0.2
_W_LATENCY = 0.2

# Threshold: if an LLM fails more than this fraction, prefer fallback
_FAILURE_THRESHOLD = 0.30

# Minimum number of samples before we trust the stats
_MIN_SAMPLES = 3

# Concentration cap: maximum share (0.0-1.0) of tasks that any single provider
# can take in a session. Once a provider exceeds this share, the router
# redirects subsequent tasks to alternatives. Critical for cost balance.
CONCENTRATION_CAP: float = 0.80

# Minimum task count before the cap kicks in (avoids penalizing the very first
# task which trivially is at 100% of 1 task).
CAP_MIN_TASKS: int = 3


class Router:
    """Route tasks to the most suitable LLM based on task type, complexity,
    availability, historical success rates, cost, and latency.

    Routing priority:
    1. Adaptive stats (data-driven, if enough samples exist)
    2. Complexity-tier routing (low/medium/high -> cheap/balanced/premium)
    3. Fallback chain per task type (ordered priority list)
    4. Static primary/fallback from TASK_TYPES table
    5. Any available LLM (last resort)
    """

    def __init__(self) -> None:
        self._rate_limited: set[str] = set()
        self._stats_path: Path = OUTPUT_DIR / ".router_stats.json"
        self._stats: dict = self._load_stats()
        # Session-level load balancer: tracks tasks assigned per LLM this session
        self._session_usage: dict[str, int] = {name: 0 for name in LLM_CONFIGS}

    def record_assignment(self, llm_name: str) -> None:
        """Track that an LLM was assigned a task in this session."""
        self._session_usage[llm_name] = self._session_usage.get(llm_name, 0) + 1

    def get_session_usage(self) -> dict[str, int]:
        """Return task assignment counts for this session."""
        return dict(self._session_usage)

    # ------------------------------------------------------------------
    # Force-all-models bridge
    # ------------------------------------------------------------------

    def get_unused_models(self) -> list[str]:
        """Return LLM names that haven't been used in this session."""
        return [name for name in LLM_CONFIGS if self._session_usage.get(name, 0) == 0 and self._is_usable(name)]

    def force_all_models_route(self, task: "Task") -> "LLMConfig":
        """Route a task, but prioritize LLMs that haven't been used yet.

        This ensures all 5 models get at least one task per session.
        Falls back to normal routing once all models have been used.
        """
        unused = self.get_unused_models()
        if unused:
            # Among unused models, pick the best fit for this task type
            routing = TASK_TYPES.get(task.type)
            # Prefer: unused model that is primary or fallback for this type
            preferred = []
            others = []
            for name in unused:
                if routing and name in (routing.primary, routing.fallback):
                    preferred.append(name)
                else:
                    others.append(name)
            chosen = (preferred + others)[0]
            self.record_assignment(chosen)
            logger.info(
                "BRIDGE force-all: task '%s' (%s) -> %s [unused models: %s]",
                task.id, task.type, chosen, ", ".join(unused),
            )
            return LLM_CONFIGS[chosen]
        # All models used at least once — normal routing
        return self.route(task)

    def get_model_status_table(self) -> str:
        """Return a formatted status table of all models and their usage."""
        lines = []
        lines.append("+-------------+----------+-----------+")
        lines.append("| Modelo      | Tarefas  | Status    |")
        lines.append("+-------------+----------+-----------+")
        for name in LLM_CONFIGS:
            cfg = LLM_CONFIGS[name]
            count = self._session_usage.get(name, 0)
            available = cfg.available
            if not available:
                status = "sem chave"
            elif name in self._rate_limited:
                status = "rate-limit"
            elif count > 0:
                status = "ATIVO"
            else:
                status = "aguardando"
            lines.append(f"| {name:<11} | {count:>6}   | {status:<9} |")
        lines.append("+-------------+----------+-----------+")
        return "\n".join(lines)

    def _least_used_llm(self, candidates: list[str]) -> str:
        """Among candidates, return the one with fewest assignments this session."""
        return min(candidates, key=lambda n: self._session_usage.get(n, 0))

    # ------------------------------------------------------------------
    # Rate-limit management
    # ------------------------------------------------------------------

    def mark_rate_limited(self, llm_name: str) -> None:
        """Flag an LLM as temporarily rate-limited."""
        self._rate_limited.add(llm_name)

    def clear_rate_limited(self, llm_name: str) -> None:
        """Remove rate-limit flag from an LLM."""
        self._rate_limited.discard(llm_name)

    # ------------------------------------------------------------------
    # Adaptive stats tracking
    # ------------------------------------------------------------------

    def _load_stats(self) -> dict:
        """Load historical routing stats from disk."""
        if self._stats_path.exists():
            try:
                return json.loads(self._stats_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Router stats corrupted, resetting.")
        return {}

    def _save_stats(self) -> None:
        """Persist current stats to disk."""
        self._stats_path.parent.mkdir(parents=True, exist_ok=True)
        self._stats_path.write_text(
            json.dumps(self._stats, indent=2), encoding="utf-8"
        )

    def update_stats(
        self,
        task_type: str,
        llm: str,
        success: bool,
        latency_ms: int = 0,
        cost: float = 0.0,
    ) -> None:
        """Record the outcome of an LLM call for adaptive routing."""
        key = f"{task_type}:{llm}"
        if key not in self._stats:
            self._stats[key] = {
                "successes": 0,
                "failures": 0,
                "total_latency_ms": 0,
                "total_cost": 0.0,
            }
        entry = self._stats[key]
        if success:
            entry["successes"] += 1
        else:
            entry["failures"] += 1
        entry["total_latency_ms"] += latency_ms
        entry["total_cost"] += cost
        self._save_stats()

    def _get_success_rate(self, task_type: str, llm: str) -> float | None:
        """Return success rate for a task_type + llm combo, or None if insufficient data."""
        key = f"{task_type}:{llm}"
        entry = self._stats.get(key)
        if not entry:
            return None
        total = entry["successes"] + entry["failures"]
        if total < _MIN_SAMPLES:
            return None
        return entry["successes"] / total

    def _get_avg_latency(self, task_type: str, llm: str) -> float:
        """Return average latency in ms, or a high default if unknown."""
        key = f"{task_type}:{llm}"
        entry = self._stats.get(key)
        if not entry:
            return 10000.0
        total = entry["successes"] + entry["failures"]
        if total == 0:
            return 10000.0
        return entry["total_latency_ms"] / total

    def _get_avg_cost(self, task_type: str, llm: str) -> float:
        """Return average cost per call, or estimated cost from config."""
        key = f"{task_type}:{llm}"
        entry = self._stats.get(key)
        if not entry:
            cfg = LLM_CONFIGS.get(llm)
            if cfg:
                return cfg.cost_per_1k_input + cfg.cost_per_1k_output
            return 0.01
        total = entry["successes"] + entry["failures"]
        if total == 0:
            return 0.01
        return entry["total_cost"] / total

    def _compute_score(self, task_type: str, llm: str) -> float:
        """Compute weighted routing score. Higher is better."""
        success_rate = self._get_success_rate(task_type, llm)
        if success_rate is None:
            success_rate = 0.8

        avg_cost = max(self._get_avg_cost(task_type, llm), 0.0001)
        avg_latency = max(self._get_avg_latency(task_type, llm), 1.0)

        cost_score = min(1.0 / (avg_cost * 100), 1.0)
        latency_score = min(1000.0 / avg_latency, 1.0)

        return (
            success_rate * _W_SUCCESS
            + cost_score * _W_COST
            + latency_score * _W_LATENCY
        )

    def get_best_llm(self, task_type: str) -> str | None:
        """Return the best LLM name for a task type based on adaptive scores.

        Returns None if there's not enough data to override the default routing.
        """
        routing = TASK_TYPES.get(task_type)
        if routing is None:
            return None

        candidates = [routing.primary, routing.fallback]
        for name in LLM_CONFIGS:
            if name not in candidates and self._is_usable(name):
                candidates.append(name)

        scored: list[tuple[str, float]] = []
        for llm in candidates:
            if not self._is_usable(llm):
                continue
            rate = self._get_success_rate(task_type, llm)
            if rate is not None:
                if rate < (1.0 - _FAILURE_THRESHOLD):
                    logger.info(
                        "LLM '%s' has %.0f%% failure rate for '%s', deprioritizing.",
                        llm, (1.0 - rate) * 100, task_type,
                    )
                scored.append((llm, self._compute_score(task_type, llm)))

        if not scored:
            return None

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]

    # ------------------------------------------------------------------
    # Complexity-tier routing
    # ------------------------------------------------------------------

    def _get_tier_candidates(self, complexity: TaskComplexity) -> list[str]:
        """Return LLM candidates for a given complexity tier."""
        tier_key = complexity.value  # "low", "medium", "high"
        return MODEL_TIERS.get(tier_key, [])

    # ------------------------------------------------------------------
    # Fallback chain
    # ------------------------------------------------------------------

    def get_fallback_chain(self, task: Task) -> list[str]:
        """Return the full fallback chain for a task, filtered by availability.

        ORDER (reviewed 2026-04-07 — fix critico do refator/cli-orchestrator-v2):
        1. TASK_TYPES.primary  (LLM canonico do task type — research->perplexity, etc.)
        2. TASK_TYPES.fallback (fallback canonico)
        3. FALLBACK_CHAINS[task_type] (cadeia estruturada por task type)
        4. Complexity-tier candidates (so como fallback de ultima instancia)
        5. Qualquer LLM disponivel (rede de seguranca)

        Antes (bug): tier de complexity 'high' = [claude, gpt4o] vinha primeiro,
        sequestrando 100% das tarefas de research/writing/analysis para Claude.
        """
        seen: set[str] = set()
        chain: list[str] = []

        # 1+2) TASK_TYPES.primary e fallback PRIMEIRO
        routing = TASK_TYPES.get(task.type)
        if routing:
            for name in [routing.primary, routing.fallback]:
                if name not in seen and self._is_usable(name):
                    chain.append(name)
                    seen.add(name)

        # 3) Cadeia estruturada FALLBACK_CHAINS por task type
        type_chain = FALLBACK_CHAINS.get(task.type, [])
        for name in type_chain:
            if name not in seen and self._is_usable(name):
                chain.append(name)
                seen.add(name)

        # 4) Complexity-tier candidates (depois — usado quando task type nao tem rota canonica)
        tier_candidates = self._get_tier_candidates(task.complexity)
        for name in tier_candidates:
            if name not in seen and self._is_usable(name):
                chain.append(name)
                seen.add(name)

        # 5) Qualquer LLM disponivel (rede de seguranca)
        for name in LLM_CONFIGS:
            if name not in seen and self._is_usable(name):
                chain.append(name)
                seen.add(name)

        return chain

    # ------------------------------------------------------------------
    # Concentration cap (NOVO 2026-04-07 — antes era vaporware)
    # ------------------------------------------------------------------

    def _current_share(self, llm_name: str) -> float:
        """Share atual deste LLM em relacao ao total de tarefas atribuidas na sessao."""
        total = sum(self._session_usage.values())
        if total == 0:
            return 0.0
        return self._session_usage.get(llm_name, 0) / total

    def _would_exceed_cap(self, llm_name: str) -> bool:
        """Verifica se atribuir mais uma tarefa a este LLM o levaria a exceder
        o CONCENTRATION_CAP. Cap so vale a partir de CAP_MIN_TASKS tarefas totais.
        """
        total = sum(self._session_usage.values())
        if total < CAP_MIN_TASKS:
            return False  # ainda nao ha tasks suficientes para o cap fazer sentido
        future_share = (self._session_usage.get(llm_name, 0) + 1) / (total + 1)
        return future_share > CONCENTRATION_CAP

    def apply_concentration_cap(
        self, chosen: str, chain: list[str]
    ) -> str:
        """Se o LLM escolhido excederia o cap, redireciona para a proxima
        alternativa viavel da chain que NAO o excederia.

        Retorna o nome do LLM final (chosen ou redirect).
        Se nenhum LLM da chain estiver abaixo do cap, mantem o chosen original
        e loga warning (cap nao pode ser respeitado dadas as constraints).
        """
        if not self._would_exceed_cap(chosen):
            return chosen

        # Procura alternativa viavel: dentro da chain, em ordem
        for alt in chain:
            if alt == chosen:
                continue
            if not self._is_usable(alt):
                continue
            if self._would_exceed_cap(alt):
                continue
            logger.info(
                "CAP %.0f%% acionado: '%s' redirecionado para '%s' "
                "(usage atual: %s)",
                CONCENTRATION_CAP * 100, chosen, alt,
                self._session_usage,
            )
            return alt

        # Nenhuma alternativa abaixo do cap — fora do nosso controle
        logger.warning(
            "CAP %.0f%% NAO pode ser respeitado: todas as alternativas tambem "
            "excederiam o cap. Mantendo '%s' (usage: %s).",
            CONCENTRATION_CAP * 100, chosen, self._session_usage,
        )
        return chosen

    # ------------------------------------------------------------------
    # Core routing
    # ------------------------------------------------------------------

    def _is_usable(self, llm_name: str) -> bool:
        """Check if an LLM is available (has key) and not rate-limited."""
        cfg = LLM_CONFIGS.get(llm_name)
        if cfg is None:
            return False
        if not cfg.available:
            return False
        if llm_name in self._rate_limited:
            return False
        return True

    def route(self, task: Task) -> LLMConfig:
        """Return the best LLM config for the given task.

        Routing priority:
        1. Adaptive stats (data-driven override)
        2. Complexity-tier + fallback chain
        3. Static primary/fallback
        4. Any available LLM

        Raises RuntimeError if no LLM is available at all.
        """
        # 1) Try adaptive routing first
        best = self.get_best_llm(task.type)
        if best and self._is_usable(best):
            self.record_assignment(best)
            logger.debug(
                "Adaptive routing: task '%s' (%s, %s) -> %s (session usage: %s)",
                task.id, task.type, task.complexity.value, best,
                self._session_usage,
            )
            return LLM_CONFIGS[best]

        # 2) Complexity-tier + fallback chain — prefer least-used among viable
        chain = self.get_fallback_chain(task)
        if chain:
            # Among the top candidates (primary + first fallback), pick least used
            top_candidates = [c for c in chain[:2] if self._is_usable(c)]
            if top_candidates:
                chosen = self._least_used_llm(top_candidates)
            else:
                chosen = chain[0]
            self.record_assignment(chosen)
            logger.debug(
                "Chain routing (balanced): task '%s' (%s) -> %s (chain: %s, usage: %s)",
                task.id, task.type, chosen, " > ".join(chain),
                self._session_usage,
            )
            return LLM_CONFIGS[chosen]

        # 3) Static routing with load balancing
        routing = TASK_TYPES.get(task.type)
        if routing is not None:
            candidates = [n for n in [routing.primary, routing.fallback] if self._is_usable(n)]
            if candidates:
                chosen = self._least_used_llm(candidates)
                self.record_assignment(chosen)
                return LLM_CONFIGS[chosen]

        # 4) Last resort: least-used available LLM
        available = [name for name in LLM_CONFIGS if self._is_usable(name)]
        if available:
            chosen = self._least_used_llm(available)
            self.record_assignment(chosen)
            return LLM_CONFIGS[chosen]

        raise RuntimeError(
            f"Nenhum LLM disponivel para a tarefa '{task.id}' (tipo: {task.type}). "
            "Verifique se as chaves de API estao configuradas no ambiente."
        )

    def get_fallback(self, task: Task, exclude: str | None = None) -> LLMConfig | None:
        """Return the next fallback LLM for a task, excluding already-tried LLMs.

        Args:
            task: The task being executed.
            exclude: LLM name to skip (typically the one that just failed).
        """
        chain = self.get_fallback_chain(task)
        for name in chain:
            if name != exclude and self._is_usable(name):
                return LLM_CONFIGS[name]
        return None

    def get_next_in_chain(
        self, task: Task, tried: set[str]
    ) -> LLMConfig | None:
        """Return the next untried LLM from the fallback chain.

        Used for iterating through the full chain on repeated failures.
        Aplica concentration cap (80%) automaticamente na PRIMEIRA tentativa
        da tarefa: se o primeiro candidato exceder o cap, redireciona para
        a primeira alternativa abaixo do cap.

        Retentativas (len(tried) > 0) NAO aplicam cap nem record_assignment
        novamente — sao continuacao da mesma tarefa, ja contabilizada.
        """
        chain = self.get_fallback_chain(task)
        valid = [name for name in chain if name not in tried and self._is_usable(name)]
        if not valid:
            return None

        # Primeira tentativa: aplica cap e registra
        if not tried:
            chosen = self.apply_concentration_cap(valid[0], valid)
            self.record_assignment(chosen)
            return LLM_CONFIGS[chosen]

        # Retentativa: usa proximo da chain, sem nova contabilidade
        return LLM_CONFIGS[valid[0]]

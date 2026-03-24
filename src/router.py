"""Task router — decides which LLM handles each task.

Uses the TASK_TYPES routing table, checks API key availability,
provides fallback selection, and adaptively learns from success/failure
rates to prefer reliable, cost-effective LLMs over time.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .config import LLM_CONFIGS, TASK_TYPES, OUTPUT_DIR, LLMConfig
from .models import Task

logger = logging.getLogger(__name__)

# Weighted score formula weights
_W_SUCCESS = 0.6
_W_COST = 0.2
_W_LATENCY = 0.2

# Threshold: if an LLM fails more than this fraction, prefer fallback
_FAILURE_THRESHOLD = 0.30

# Minimum number of samples before we trust the stats
_MIN_SAMPLES = 3


class Router:
    """Route tasks to the most suitable LLM based on task type, availability,
    historical success rates, cost, and latency."""

    def __init__(self) -> None:
        self._rate_limited: set[str] = set()
        self._stats_path: Path = OUTPUT_DIR / ".router_stats.json"
        self._stats: dict = self._load_stats()

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
        """Load historical routing stats from disk.

        Format: {
            "task_type:llm_name": {
                "successes": int,
                "failures": int,
                "total_latency_ms": int,
                "total_cost": float
            }
        }
        """
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
            return 10000.0  # default high latency for unknown
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
                # Rough estimate: 1K tokens in + 1K tokens out
                return cfg.cost_per_1k_input + cfg.cost_per_1k_output
            return 0.01
        total = entry["successes"] + entry["failures"]
        if total == 0:
            return 0.01
        return entry["total_cost"] / total

    def _compute_score(self, task_type: str, llm: str) -> float:
        """Compute weighted routing score.

        score = (success_rate * 0.6) + (1/cost * 0.2) + (1/latency * 0.2)
        Higher is better. All components are normalized to [0, 1] range.
        """
        success_rate = self._get_success_rate(task_type, llm)
        if success_rate is None:
            success_rate = 0.8  # optimistic default for untested combos

        avg_cost = max(self._get_avg_cost(task_type, llm), 0.0001)
        avg_latency = max(self._get_avg_latency(task_type, llm), 1.0)

        # Normalize cost: lower is better, cap inverse at 1.0
        # Gemini ~0.001, Claude ~0.09, so 1/cost ranges widely
        cost_score = min(1.0 / (avg_cost * 100), 1.0)

        # Normalize latency: lower is better
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
        # Also consider all available LLMs
        for name in LLM_CONFIGS:
            if name not in candidates and self._is_usable(name):
                candidates.append(name)

        # Only override if we have real stats
        scored: list[tuple[str, float]] = []
        for llm in candidates:
            if not self._is_usable(llm):
                continue
            rate = self._get_success_rate(task_type, llm)
            if rate is not None:
                # Check failure threshold: if primary fails too much, penalize it
                if rate < (1.0 - _FAILURE_THRESHOLD):
                    logger.info(
                        "LLM '%s' has %.0f%% failure rate for '%s', deprioritizing.",
                        llm, (1.0 - rate) * 100, task_type,
                    )
                scored.append((llm, self._compute_score(task_type, llm)))

        if not scored:
            return None  # not enough data, use default routing

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0]

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

        First checks adaptive stats for a data-driven choice, then
        falls back through: primary -> fallback -> any available LLM.
        Raises RuntimeError if no LLM is available at all.
        """
        # Try adaptive routing first
        best = self.get_best_llm(task.type)
        if best and self._is_usable(best):
            return LLM_CONFIGS[best]

        # Default static routing
        routing = TASK_TYPES.get(task.type)

        if routing is not None:
            if self._is_usable(routing.primary):
                return LLM_CONFIGS[routing.primary]
            if self._is_usable(routing.fallback):
                return LLM_CONFIGS[routing.fallback]

        # Last resort: pick any available LLM
        for name, cfg in LLM_CONFIGS.items():
            if self._is_usable(name):
                return cfg

        raise RuntimeError(
            f"Nenhum LLM disponivel para a tarefa '{task.id}' (tipo: {task.type}). "
            "Verifique se as chaves de API estao configuradas no ambiente."
        )

    def get_fallback(self, task: Task) -> LLMConfig | None:
        """Return the fallback LLM for a task, or None if unavailable."""
        routing = TASK_TYPES.get(task.type)
        if routing is None:
            return None

        primary = routing.primary
        fallback_name = routing.fallback

        # Only return fallback if it differs from what was already tried
        if self._is_usable(fallback_name) and fallback_name != primary:
            return LLM_CONFIGS[fallback_name]

        # Try any other available LLM as a last-resort fallback
        tried = {primary, fallback_name}
        for name, cfg in LLM_CONFIGS.items():
            if name not in tried and self._is_usable(name):
                return cfg

        return None

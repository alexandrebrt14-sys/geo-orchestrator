"""Smart router — intelligent LLM allocation inspired by CASTER and HALO.

Replaces the force_all_models_route() approach with demand-aware routing.
Google Research showed multi-agent degrades 39-70% on non-decomposable tasks
(arXiv 2601.19793). This module routes only the LLMs actually needed.

Key features:
- Demand classification into tiers (SIMPLE / MODERATE / COMPLEX)
- Smart routing that allocates 1-5 LLMs based on demand complexity
- Feedback loop that learns from routing outcomes
- Early stopping when completed results already cover the demand
"""

from __future__ import annotations

import json
import logging
import re
import time
from enum import Enum
from pathlib import Path

from .config import LLM_CONFIGS, OUTPUT_DIR, TASK_TYPES, LLMConfig
from .models import Task, TaskComplexity
from .router import Router

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Multi-domain keyword sets used by the demand classifier
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS: dict[str, set[str]] = {
    "research": {
        "pesquisar", "pesquisa", "research", "fontes", "sources", "citations",
        "fact-check", "verificar", "buscar", "busca", "investigar",
    },
    "code": {
        "code", "codigo", "script", "python", "javascript", "typescript",
        "implementar", "implement", "deploy", "api", "endpoint", "bug", "fix",
        "refactor", "test", "pipeline", "automacao",
    },
    "writing": {
        "escrever", "write", "artigo", "article", "post", "blog", "conteudo",
        "content", "copy", "redacao", "texto", "seo", "landing", "ebook",
    },
    "analysis": {
        "analisar", "analyze", "analysis", "metricas", "metrics", "dashboard",
        "relatorio", "report", "benchmark", "comparar", "compare", "audit",
    },
    "creative": {
        "criativo", "creative", "design", "branding", "visual", "campanha",
        "campaign", "estrategia", "strategy", "ideias", "brainstorm",
    },
}

# Minimum feedback samples before overriding default routing
_FEEDBACK_MIN_SAMPLES = 10

# Quality score threshold — if an alternative LLM scores this much better,
# override the default routing for that task type
_QUALITY_OVERRIDE_MARGIN = 0.15


class DemandTier(str, Enum):
    """Demand complexity tier that determines LLM allocation."""

    SIMPLE = "simple"      # 1-2 LLMs, direct execution
    MODERATE = "moderate"  # 2-3 LLMs, standard pipeline
    COMPLEX = "complex"    # 3-5 LLMs, full orchestration


class SmartRouter(Router):
    """Intelligent router that allocates only the LLMs actually needed.

    Extends the base Router with:
    - Demand classification (how many LLMs to use)
    - Tier-aware smart routing (skip unnecessary LLMs)
    - Feedback loop (learn from outcomes)
    - Early stopping (halt pipeline when results are sufficient)
    """

    def __init__(self) -> None:
        super().__init__()
        self._feedback_path: Path = OUTPUT_DIR / ".router_feedback.jsonl"
        self._feedback_cache: dict[str, list[dict]] = self._load_feedback()

    # ------------------------------------------------------------------
    # Feedback persistence
    # ------------------------------------------------------------------

    def _load_feedback(self) -> dict[str, list[dict]]:
        """Load feedback entries from the JSONL file, grouped by (task_type, llm)."""
        cache: dict[str, list[dict]] = {}
        if not self._feedback_path.exists():
            return cache
        try:
            for line in self._feedback_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                key = f"{entry['task_type']}:{entry['llm']}"
                cache.setdefault(key, []).append(entry)
        except (json.JSONDecodeError, KeyError, OSError) as exc:
            logger.warning("Failed to load router feedback: %s", exc)
        return cache

    # ------------------------------------------------------------------
    # Demand Classifier
    # ------------------------------------------------------------------

    def classify_demand(self, demand: str, task_count: int) -> DemandTier:
        """Classify demand complexity to determine LLM allocation.

        Scoring heuristics:
        - Word count: short demands (< 50 words) are simpler
        - Task count: 1-3 = simple, 4-7 = moderate, 8+ = complex
        - Multi-domain presence: demands touching multiple domains are complex
        - Distinct task types in the routing table

        Args:
            demand: The original user demand string.
            task_count: Number of decomposed tasks in the plan.

        Returns:
            DemandTier indicating how many LLMs should be allocated.
        """
        score = 0.0
        demand_lower = demand.lower()
        words = demand_lower.split()
        word_count = len(words)

        # --- Word count scoring (0-2 points) ---
        if word_count < 20:
            score += 0.0
        elif word_count < 50:
            score += 1.0
        elif word_count < 100:
            score += 1.5
        else:
            score += 2.0

        # --- Task count scoring (0-3 points) ---
        if task_count <= 2:
            score += 0.0
        elif task_count <= 4:
            score += 1.0
        elif task_count <= 7:
            score += 2.0
        else:
            score += 3.0

        # --- Multi-domain scoring (0-3 points) ---
        domains_hit = 0
        for _domain, keywords in _DOMAIN_KEYWORDS.items():
            if any(kw in demand_lower for kw in keywords):
                domains_hit += 1
        if domains_hit >= 4:
            score += 3.0
        elif domains_hit >= 3:
            score += 2.0
        elif domains_hit >= 2:
            score += 1.0

        # --- Classification thresholds ---
        if score <= 2.0:
            tier = DemandTier.SIMPLE
        elif score <= 5.0:
            tier = DemandTier.MODERATE
        else:
            tier = DemandTier.COMPLEX

        logger.info(
            "Demand classified as %s (score=%.1f, words=%d, tasks=%d, domains=%d)",
            tier.value, score, word_count, task_count, domains_hit,
        )
        return tier

    # ------------------------------------------------------------------
    # Smart Route
    # ------------------------------------------------------------------

    def smart_route(self, task: Task, tier: DemandTier) -> LLMConfig:
        """Route task to best LLM without forcing all models.

        Routing strategy varies by tier:
        - SIMPLE: use only the primary LLM for the task type (TASK_TYPES.primary).
        - MODERATE: primary, consider fallback if primary has high failure rate.
        - COMPLEX: adaptive scoring + load balancing, restrito ao top da chain
          (TASK_TYPES.primary primeiro, tier de complexity como segundo).

        Sprint 4 (2026-04-07): _route_complex agora restringe candidates aos
        top 2 da get_fallback_chain (que ja prioriza TASK_TYPES.primary apos
        o fix da sprint 1). Antes usava todos os 5 LLMs no scoring, fazendo
        com que cost_score sequestrasse code/review para gpt4o no pre_check.
        Apos escolher o LLM, aplica downgrade_claude_by_complexity para
        que o pre_execution_check do FinOps estime com tier interno.
        """
        routing = TASK_TYPES.get(task.type)
        feedback_override = self._get_feedback_override(task.type)

        if tier == DemandTier.SIMPLE:
            chosen_cfg = self._route_simple(task, routing, feedback_override)
        elif tier == DemandTier.MODERATE:
            chosen_cfg = self._route_moderate(task, routing, feedback_override)
        else:
            chosen_cfg = self._route_complex(task, routing, feedback_override)

        # Sprint 4: aplicar tier interno Claude tambem aqui (pre_check)
        downgraded_name = self.downgrade_claude_by_complexity(chosen_cfg.name, task)
        if downgraded_name != chosen_cfg.name:
            return LLM_CONFIGS[downgraded_name]
        return chosen_cfg

    def _route_simple(
        self,
        task: Task,
        routing: object | None,
        feedback_override: str | None,
    ) -> LLMConfig:
        """SIMPLE tier: use only the primary LLM for the task type."""
        # Feedback override takes precedence
        if feedback_override and self._is_usable(feedback_override):
            self.record_assignment(feedback_override)
            logger.info(
                "SMART[simple] task '%s' (%s) -> %s (feedback override)",
                task.id, task.type, feedback_override,
            )
            return LLM_CONFIGS[feedback_override]

        # Use primary if available
        if routing and self._is_usable(routing.primary):
            self.record_assignment(routing.primary)
            logger.info(
                "SMART[simple] task '%s' (%s) -> %s (primary)",
                task.id, task.type, routing.primary,
            )
            return LLM_CONFIGS[routing.primary]

        # Fallback to base router
        return self.route(task)

    def _route_moderate(
        self,
        task: Task,
        routing: object | None,
        feedback_override: str | None,
    ) -> LLMConfig:
        """MODERATE tier: primary + consider fallback if primary has high failure rate."""
        if feedback_override and self._is_usable(feedback_override):
            self.record_assignment(feedback_override)
            logger.info(
                "SMART[moderate] task '%s' (%s) -> %s (feedback override)",
                task.id, task.type, feedback_override,
            )
            return LLM_CONFIGS[feedback_override]

        if routing:
            primary = routing.primary
            fallback = routing.fallback

            # Check if primary has a concerning failure rate
            primary_success = self._get_success_rate(task.type, primary)
            if primary_success is not None and primary_success < 0.70:
                # Primary is unreliable — try fallback
                if self._is_usable(fallback):
                    self.record_assignment(fallback)
                    logger.info(
                        "SMART[moderate] task '%s' (%s) -> %s (primary '%s' has %.0f%% success)",
                        task.id, task.type, fallback, primary,
                        primary_success * 100,
                    )
                    return LLM_CONFIGS[fallback]

            # Primary is fine
            if self._is_usable(primary):
                self.record_assignment(primary)
                logger.info(
                    "SMART[moderate] task '%s' (%s) -> %s (primary)",
                    task.id, task.type, primary,
                )
                return LLM_CONFIGS[primary]

            # Primary unavailable, use fallback
            if self._is_usable(fallback):
                self.record_assignment(fallback)
                logger.info(
                    "SMART[moderate] task '%s' (%s) -> %s (fallback, primary unavailable)",
                    task.id, task.type, fallback,
                )
                return LLM_CONFIGS[fallback]

        # Fall through to base router
        return self.route(task)

    def _route_complex(
        self,
        task: Task,
        routing: object | None,
        feedback_override: str | None,
    ) -> LLMConfig:
        """COMPLEX tier: usa TASK_TYPES.primary canonico (sprint 4 fix).

        Antes da sprint 4: _compute_score considerava TODOS os 5 LLMs e o
        cost_score barato sequestrava code/review para gpt4o (mesmo bug que
        get_fallback_chain tinha na sprint 1, mas no path do pre_check).
        Mesmo restringindo aos top 2 candidates, gpt4o (avg_cost $0.0125)
        ainda vencia claude (avg_cost $0.09) por ser mais barato.

        Sprint 4 final: simplesmente usa chain[0] (primary canonico),
        como o runtime ja faz em _run_task->get_next_in_chain. Pre_check
        e runtime ficam consistentes — sem mais surpresas no FinOps estimate.

        Feedback override forte (>= 2x MIN_SAMPLES) ainda pode sequestrar.
        """
        if feedback_override and self._is_usable(feedback_override):
            feedback_entries = self._feedback_cache.get(
                f"{task.type}:{feedback_override}", []
            )
            if len(feedback_entries) >= _FEEDBACK_MIN_SAMPLES * 2:
                logger.info(
                    "SMART[complex] task '%s' (%s) -> %s (strong feedback override, %d samples)",
                    task.id, task.type, feedback_override, len(feedback_entries),
                )
                return LLM_CONFIGS[feedback_override]

        # Sprint 4: usa chain canonica (primary primeiro), igual ao runtime.
        chain = self.get_fallback_chain(task)
        if not chain:
            raise RuntimeError(
                f"No LLM available for task '{task.id}' (type: {task.type})."
            )

        chosen = chain[0]
        logger.info(
            "SMART[complex] task '%s' (%s) -> %s (canonico chain[0])",
            task.id, task.type, chosen,
        )
        return LLM_CONFIGS[chosen]

    # ------------------------------------------------------------------
    # Feedback Loop
    # ------------------------------------------------------------------

    def record_feedback(
        self,
        task_type: str,
        llm: str,
        success: bool,
        quality_score: float = 0.0,
        cost: float = 0.0,
        latency_ms: int = 0,
    ) -> None:
        """Record routing outcome for future optimization.

        Each entry is appended to output/.router_feedback.jsonl and cached
        in memory for efficient lookups.

        Args:
            task_type: The type of task that was executed.
            llm: Name of the LLM that handled the task.
            success: Whether the task completed successfully.
            quality_score: Quality rating from 0.0 to 1.0 (0.0 = not rated).
            cost: Actual cost in USD for this call.
            latency_ms: Actual latency in milliseconds.
        """
        entry = {
            "timestamp": time.time(),
            "task_type": task_type,
            "llm": llm,
            "success": success,
            "quality_score": quality_score,
            "cost": cost,
            "latency_ms": latency_ms,
        }

        # Persist to disk (append-only)
        self._feedback_path.parent.mkdir(parents=True, exist_ok=True)
        with self._feedback_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Update in-memory cache
        key = f"{task_type}:{llm}"
        self._feedback_cache.setdefault(key, []).append(entry)

        logger.debug(
            "Feedback recorded: %s on %s -> success=%s quality=%.2f",
            llm, task_type, success, quality_score,
        )

    def _get_feedback_override(self, task_type: str) -> str | None:
        """Check if feedback data suggests a better LLM than the default.

        Returns the name of a better LLM if one exists with significantly
        higher quality scores than the current primary, or None.
        """
        routing = TASK_TYPES.get(task_type)
        if routing is None:
            return None

        primary = routing.primary
        primary_key = f"{task_type}:{primary}"
        primary_entries = self._feedback_cache.get(primary_key, [])

        # Need enough samples for the primary to compare
        if len(primary_entries) < _FEEDBACK_MIN_SAMPLES:
            return None

        primary_avg = self._avg_quality(primary_entries)

        # Compare against all other LLMs
        best_alt: str | None = None
        best_alt_avg: float = primary_avg

        for name in LLM_CONFIGS:
            if name == primary:
                continue
            key = f"{task_type}:{name}"
            entries = self._feedback_cache.get(key, [])
            if len(entries) < _FEEDBACK_MIN_SAMPLES:
                continue
            avg = self._avg_quality(entries)
            if avg > best_alt_avg + _QUALITY_OVERRIDE_MARGIN:
                best_alt = name
                best_alt_avg = avg

        if best_alt:
            logger.info(
                "Feedback override for '%s': %s (avg=%.2f) beats primary '%s' (avg=%.2f)",
                task_type, best_alt, best_alt_avg, primary, primary_avg,
            )
        return best_alt

    @staticmethod
    def _avg_quality(entries: list[dict]) -> float:
        """Compute average quality score from feedback entries.

        Entries with quality_score == 0.0 are treated as unrated and use
        success/failure as a proxy (success=0.7, failure=0.3).
        """
        if not entries:
            return 0.0
        total = 0.0
        for e in entries:
            q = e.get("quality_score", 0.0)
            if q > 0.0:
                total += q
            else:
                # Use success as a rough proxy
                total += 0.7 if e.get("success", False) else 0.3
        return total / len(entries)

    # ------------------------------------------------------------------
    # Early Stop Check
    # ------------------------------------------------------------------

    def should_early_stop(
        self,
        demand: str,
        completed_results: list[str],
        remaining_tasks: int,
    ) -> bool:
        """Check if we can stop early because results already answer the demand.

        Uses keyword coverage as a heuristic: if completed results already
        contain > 80% of the meaningful keywords from the demand, the
        remaining tasks are unlikely to add significant value.

        Args:
            demand: The original user demand string.
            completed_results: List of output strings from completed tasks.
            remaining_tasks: Number of tasks still pending execution.

        Returns:
            True if early stopping is recommended.
        """
        # Never stop if only 1-2 tasks remain — just finish them
        if remaining_tasks <= 2:
            return False

        # Extract meaningful keywords from the demand (3+ chars, no stopwords)
        demand_keywords = self._extract_keywords(demand)
        if not demand_keywords:
            return False

        # Check coverage in completed results
        combined_results = " ".join(completed_results).lower()
        covered = sum(1 for kw in demand_keywords if kw in combined_results)
        coverage = covered / len(demand_keywords)

        should_stop = coverage > 0.80

        if should_stop:
            logger.info(
                "Early stop recommended: %.0f%% keyword coverage (%d/%d), "
                "%d tasks remaining",
                coverage * 100, covered, len(demand_keywords), remaining_tasks,
            )
        else:
            logger.debug(
                "Early stop check: %.0f%% coverage (%d/%d), %d remaining — continuing",
                coverage * 100, covered, len(demand_keywords), remaining_tasks,
            )

        return should_stop

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """Extract meaningful keywords from text for coverage analysis.

        Filters out common Portuguese and English stopwords and short tokens.
        Returns lowercase keyword set.
        """
        _STOPWORDS = {
            # Portuguese
            "que", "para", "com", "uma", "por", "mais", "como", "mas", "dos",
            "das", "nos", "nas", "isso", "esse", "essa", "este", "esta",
            "sobre", "entre", "depois", "antes", "cada", "todo", "toda",
            "muito", "pode", "deve", "ser", "ter", "fazer", "quando",
            "onde", "qual", "quais", "porque", "assim",
            # English
            "the", "and", "for", "with", "that", "this", "from", "have",
            "has", "are", "was", "were", "been", "will", "would", "could",
            "should", "about", "into", "than", "then", "also", "just",
            "more", "some", "what", "when", "where", "which", "who", "how",
        }

        words = re.findall(r"[a-zA-Z\u00C0-\u017F]{3,}", text.lower())
        return {w for w in words if w not in _STOPWORDS}

    # ------------------------------------------------------------------
    # Utility: max LLMs for a tier
    # ------------------------------------------------------------------

    @staticmethod
    def max_llms_for_tier(tier: DemandTier) -> int:
        """Return the maximum number of distinct LLMs to use for a tier.

        Useful for the pipeline to cap parallel LLM usage.
        """
        return {
            DemandTier.SIMPLE: 2,
            DemandTier.MODERATE: 3,
            DemandTier.COMPLEX: 5,
        }[tier]

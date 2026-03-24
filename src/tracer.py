"""Lightweight distributed tracing system for geo-orchestrator.

No external dependencies (no OpenTelemetry). Provides span tracking,
trace context, auto-instrumentation via decorators, and export to
JSON, ASCII timeline, and compact summary.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

logger = logging.getLogger(__name__)

# Context variable to track the current active span
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "_current_span", default=None
)

# Context variable to track the current active trace
_current_trace: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "_current_trace", default=None
)

# Default output directory for traces
TRACES_DIR = Path("output/.traces")


def _short_id() -> str:
    """Generate a short unique ID (first 12 chars of uuid4)."""
    return uuid4().hex[:12]


# ======================================================================
# Span
# ======================================================================

@dataclass
class Span:
    """A single timed operation within a trace.

    Supports nesting via parent_span_id and children list.
    Attributes dict stores arbitrary metadata (provider, model, tokens, etc.).
    """

    span_id: str = field(default_factory=_short_id)
    parent_span_id: str | None = None
    name: str = ""
    start_time: float = 0.0  # time.perf_counter() value
    end_time: float = 0.0
    duration_ms: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    children: list[Span] = field(default_factory=list)
    _start_wall: str = ""  # ISO wall-clock start time for export

    def start(self) -> Span:
        """Mark this span as started."""
        self.start_time = time.perf_counter()
        self._start_wall = datetime.now(timezone.utc).isoformat()
        return self

    def finish(self, **extra_attrs: Any) -> Span:
        """Mark this span as finished and compute duration."""
        self.end_time = time.perf_counter()
        self.duration_ms = round((self.end_time - self.start_time) * 1000, 2)
        if extra_attrs:
            self.attributes.update(extra_attrs)
        return self

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a single attribute on this span."""
        self.attributes[key] = value

    def set_attributes(self, attrs: dict[str, Any]) -> None:
        """Set multiple attributes at once."""
        self.attributes.update(attrs)

    def add_child(self, child: Span) -> None:
        """Add a child span."""
        child.parent_span_id = self.span_id
        self.children.append(child)

    def set_error(self, error: Exception) -> None:
        """Record an error on this span."""
        self.attributes["status"] = "error"
        self.attributes["error"] = f"{type(error).__name__}: {error}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize span to a dictionary (recursive for children)."""
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "start_wall": self._start_wall,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "children": [c.to_dict() for c in self.children],
        }


# ======================================================================
# Trace
# ======================================================================

@dataclass
class Trace:
    """Groups all spans for one pipeline execution.

    Provides the root context for a full orchestration run.
    """

    trace_id: str = field(default_factory=lambda: uuid4().hex[:16])
    demand: str = ""
    root_spans: list[Span] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    total_duration_ms: float = 0.0
    total_cost: float = 0.0
    _start_time: float = 0.0

    def start(self) -> Trace:
        """Mark trace as started."""
        self._start_time = time.perf_counter()
        self.started_at = datetime.now(timezone.utc).isoformat()
        return self

    def finish(self) -> Trace:
        """Mark trace as finished, compute totals."""
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.total_duration_ms = round(
            (time.perf_counter() - self._start_time) * 1000, 2
        )
        self.total_cost = self._sum_cost(self.root_spans)
        return self

    def add_root_span(self, span: Span) -> None:
        """Add a root-level span to this trace."""
        self.root_spans.append(span)

    def _sum_cost(self, spans: list[Span]) -> float:
        """Recursively sum cost from all spans."""
        total = 0.0
        for span in spans:
            total += span.attributes.get("cost", 0.0)
            total += self._sum_cost(span.children)
        return round(total, 6)

    def all_spans_flat(self) -> list[Span]:
        """Return all spans in a flat list (depth-first)."""
        result: list[Span] = []
        self._collect_spans(self.root_spans, result)
        return result

    def _collect_spans(self, spans: list[Span], result: list[Span]) -> None:
        for span in spans:
            result.append(span)
            self._collect_spans(span.children, result)

    def to_dict(self) -> dict[str, Any]:
        """Full trace as a serializable dictionary."""
        return {
            "trace_id": self.trace_id,
            "demand": self.demand,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total_duration_ms": self.total_duration_ms,
            "total_cost": self.total_cost,
            "spans": [s.to_dict() for s in self.root_spans],
        }


# ======================================================================
# Trace Manager (global singleton)
# ======================================================================

class TraceManager:
    """Manages the current trace context and span hierarchy.

    Use as a context manager or manually via start_trace/finish_trace.
    Thread-safe via contextvars.
    """

    _instance: TraceManager | None = None

    def __init__(self) -> None:
        self._traces: list[Trace] = []

    @classmethod
    def get_instance(cls) -> TraceManager:
        """Get or create the global TraceManager singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (useful for tests)."""
        cls._instance = None

    def start_trace(self, demand: str = "") -> Trace:
        """Start a new trace and set it as the active context."""
        trace = Trace(demand=demand).start()
        _current_trace.set(trace)
        _current_span.set(None)
        self._traces.append(trace)
        return trace

    def finish_trace(self, trace: Trace | None = None) -> Trace | None:
        """Finish the current (or specified) trace and save it."""
        t = trace or _current_trace.get()
        if t is None:
            return None
        t.finish()
        _current_trace.set(None)
        _current_span.set(None)
        # Auto-save to disk
        self._save_trace(t)
        return t

    def start_span(self, name: str, **attributes: Any) -> Span:
        """Start a new span, nesting under the current span if one exists."""
        span = Span(name=name, attributes=attributes).start()
        parent = _current_span.get()
        trace = _current_trace.get()

        if parent is not None:
            parent.add_child(span)
        elif trace is not None:
            trace.add_root_span(span)

        _current_span.set(span)
        return span

    def finish_span(self, span: Span, **extra_attrs: Any) -> Span:
        """Finish a span and restore the parent as current."""
        span.finish(**extra_attrs)
        # Restore parent span as current
        parent_id = span.parent_span_id
        if parent_id is not None:
            parent = self._find_span_by_id(parent_id)
            _current_span.set(parent)
        else:
            _current_span.set(None)
        return span

    def current_trace(self) -> Trace | None:
        """Get the currently active trace."""
        return _current_trace.get()

    def current_span(self) -> Span | None:
        """Get the currently active span."""
        return _current_span.get()

    def recent_traces(self, limit: int = 20) -> list[Trace]:
        """Get the most recent in-memory traces."""
        return self._traces[-limit:]

    def _find_span_by_id(self, span_id: str) -> Span | None:
        """Find a span by ID in the current trace."""
        trace = _current_trace.get()
        if trace is None:
            return None
        for span in trace.all_spans_flat():
            if span.span_id == span_id:
                return span
        return None

    def _save_trace(self, trace: Trace) -> None:
        """Save trace to output/.traces/{trace_id}.json."""
        try:
            traces_dir = TRACES_DIR
            traces_dir.mkdir(parents=True, exist_ok=True)
            path = traces_dir / f"{trace.trace_id}.json"
            path.write_text(
                json.dumps(trace.to_dict(), indent=2, default=str),
                encoding="utf-8",
            )
            logger.debug("Trace saved: %s", path)
        except Exception as exc:
            logger.warning("Failed to save trace %s: %s", trace.trace_id, exc)


# ======================================================================
# Auto-instrumentation decorator
# ======================================================================

def traced(operation_name: str, **default_attrs: Any) -> Callable:
    """Decorator that creates a span for the decorated function.

    Works with both sync and async functions. Records duration and
    any exceptions that occur.

    Usage:
        @traced("pipeline.execute")
        async def execute(self):
            ...

        @traced("util.parse", component="parser")
        def parse_data(data):
            ...
    """

    def decorator(func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                manager = TraceManager.get_instance()
                span = manager.start_span(operation_name, **default_attrs)
                try:
                    result = await func(*args, **kwargs)
                    span.set_attribute("status", "ok")
                    return result
                except Exception as exc:
                    span.set_error(exc)
                    raise
                finally:
                    manager.finish_span(span)

            return async_wrapper

        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                manager = TraceManager.get_instance()
                span = manager.start_span(operation_name, **default_attrs)
                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("status", "ok")
                    return result
                except Exception as exc:
                    span.set_error(exc)
                    raise
                finally:
                    manager.finish_span(span)

            return sync_wrapper

    return decorator


# ======================================================================
# Export functions
# ======================================================================

def export_json(trace: Trace) -> str:
    """Export full trace as formatted JSON string."""
    return json.dumps(trace.to_dict(), indent=2, default=str)


def export_timeline(trace: Trace) -> str:
    """Export an ASCII timeline showing parallel execution.

    Renders a visual representation of spans with their timing,
    showing nesting through indentation and parallel execution
    through overlapping time bars.
    """
    lines: list[str] = []
    lines.append(f"Trace: {trace.trace_id}")
    lines.append(f"Demand: {trace.demand}")
    lines.append(f"Total: {trace.total_duration_ms:.0f}ms | Cost: US$ {trace.total_cost:.4f}")
    lines.append("")

    all_spans = trace.all_spans_flat()
    if not all_spans:
        lines.append("  (no spans)")
        return "\n".join(lines)

    # Determine if we have live timing data or are working from disk
    has_live_timing = any(s.start_time > 0 for s in all_spans)

    if has_live_timing:
        min_start = min(s.start_time for s in all_spans if s.start_time > 0)
        ends = [s.end_time for s in all_spans if s.end_time > 0]
        max_end = max(ends) if ends else min_start + 0.001
        total_span = max(max_end - min_start, 0.001)
    else:
        # Loaded from disk: use total_duration_ms as the span
        total_span = max(trace.total_duration_ms, 1.0)  # in ms

    bar_width = 50

    lines.append(f"  {'Operation':<35} {'Duration':>10}  {'Timeline':>{bar_width}}")
    lines.append(f"  {'─' * 35} {'─' * 10}  {'─' * bar_width}")

    # For disk-loaded traces, track cumulative offset for sequential layout
    _disk_offset_ms = [0.0]

    def render_span(span: Span, depth: int = 0) -> None:
        indent = "  " * depth
        name = f"{indent}{span.name}"
        if len(name) > 33:
            name = name[:30] + "..."
        name = name.ljust(35)

        duration_str = f"{span.duration_ms:.0f}ms".rjust(10)

        # Compute bar position
        if has_live_timing and span.start_time > 0:
            start_frac = (span.start_time - min_start) / total_span
            dur_frac = max(0.02, (span.end_time - span.start_time) / total_span)
        else:
            # Disk-loaded: use sequential layout based on duration
            start_frac = _disk_offset_ms[0] / total_span if total_span > 0 else 0.0
            dur_frac = max(0.02, span.duration_ms / total_span) if total_span > 0 else 0.02
            _disk_offset_ms[0] += span.duration_ms * 0.1  # compact spacing

        bar_start = int(start_frac * bar_width)
        bar_len = max(1, int(dur_frac * bar_width))

        status = span.attributes.get("status", "ok")
        char = "X" if status == "error" else "="
        bar = "." * bar_start + char * bar_len
        bar = bar.ljust(bar_width, ".")

        lines.append(f"  {name} {duration_str}  |{bar}|")

        for child in span.children:
            render_span(child, depth + 1)

    for root_span in trace.root_spans:
        render_span(root_span, depth=0)

    lines.append("")
    lines.append(f"  Legend: = success, X = error, . = idle")

    return "\n".join(lines)


def export_summary(trace: Trace) -> str:
    """Export compact summary with key metrics.

    Shows span count, duration, cost breakdown, error count, and
    per-provider statistics.
    """
    lines: list[str] = []
    all_spans = trace.all_spans_flat()

    lines.append(f"Trace Summary: {trace.trace_id}")
    lines.append(f"  Demand: {trace.demand}")
    lines.append(f"  Started: {trace.started_at}")
    lines.append(f"  Finished: {trace.finished_at}")
    lines.append(f"  Duration: {trace.total_duration_ms:.0f}ms ({trace.total_duration_ms / 1000:.1f}s)")
    lines.append(f"  Total Cost: US$ {trace.total_cost:.4f}")
    lines.append(f"  Total Spans: {len(all_spans)}")

    # Count errors
    errors = [s for s in all_spans if s.attributes.get("status") == "error"]
    if errors:
        lines.append(f"  Errors: {len(errors)}")
        for err_span in errors[:5]:
            lines.append(f"    - {err_span.name}: {err_span.attributes.get('error', 'unknown')}")

    # Provider breakdown
    provider_stats: dict[str, dict[str, Any]] = {}
    for span in all_spans:
        provider = span.attributes.get("provider")
        if provider:
            if provider not in provider_stats:
                provider_stats[provider] = {
                    "calls": 0,
                    "cost": 0.0,
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "duration_ms": 0.0,
                }
            stats = provider_stats[provider]
            stats["calls"] += 1
            stats["cost"] += span.attributes.get("cost", 0.0)
            stats["tokens_in"] += span.attributes.get("tokens_in", 0)
            stats["tokens_out"] += span.attributes.get("tokens_out", 0)
            stats["duration_ms"] += span.duration_ms

    if provider_stats:
        lines.append("")
        lines.append("  Provider Breakdown:")
        for provider, stats in sorted(provider_stats.items()):
            lines.append(
                f"    {provider}: {stats['calls']} calls, "
                f"US$ {stats['cost']:.4f}, "
                f"{stats['tokens_in']}in/{stats['tokens_out']}out tokens, "
                f"{stats['duration_ms']:.0f}ms"
            )

    # Task breakdown
    task_spans = [s for s in all_spans if s.name.startswith("task.")]
    if task_spans:
        lines.append("")
        lines.append("  Task Breakdown:")
        ok = sum(1 for s in task_spans if s.attributes.get("status") == "ok")
        failed = sum(1 for s in task_spans if s.attributes.get("status") == "error")
        lines.append(f"    Completed: {ok}, Failed: {failed}")

    return "\n".join(lines)


# ======================================================================
# Disk-based trace loading (for CLI)
# ======================================================================

def list_traces(traces_dir: Path | None = None, limit: int = 20) -> list[dict[str, Any]]:
    """List recent traces from disk, sorted by modification time (newest first)."""
    d = traces_dir or TRACES_DIR
    if not d.exists():
        return []

    files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    results: list[dict[str, Any]] = []

    for f in files[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            results.append({
                "trace_id": data.get("trace_id", f.stem),
                "demand": data.get("demand", "")[:80],
                "started_at": data.get("started_at", ""),
                "total_duration_ms": data.get("total_duration_ms", 0),
                "total_cost": data.get("total_cost", 0.0),
                "span_count": _count_spans(data.get("spans", [])),
                "file": str(f),
            })
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Skipping invalid trace file %s: %s", f, exc)

    return results


def load_trace(trace_id: str, traces_dir: Path | None = None) -> Trace | None:
    """Load a trace from disk by its trace_id."""
    d = traces_dir or TRACES_DIR
    path = d / f"{trace_id}.json"
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _dict_to_trace(data)
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        logger.warning("Failed to load trace %s: %s", trace_id, exc)
        return None


def load_latest_trace(traces_dir: Path | None = None) -> Trace | None:
    """Load the most recent trace from disk."""
    d = traces_dir or TRACES_DIR
    if not d.exists():
        return None

    files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return None

    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        return _dict_to_trace(data)
    except (json.JSONDecodeError, OSError, KeyError) as exc:
        logger.warning("Failed to load latest trace: %s", exc)
        return None


def _count_spans(spans: list[dict]) -> int:
    """Recursively count spans in a serialized trace."""
    count = len(spans)
    for s in spans:
        count += _count_spans(s.get("children", []))
    return count


def _dict_to_trace(data: dict) -> Trace:
    """Reconstruct a Trace object from a serialized dict."""
    trace = Trace(
        trace_id=data["trace_id"],
        demand=data.get("demand", ""),
        started_at=data.get("started_at", ""),
        finished_at=data.get("finished_at", ""),
        total_duration_ms=data.get("total_duration_ms", 0.0),
        total_cost=data.get("total_cost", 0.0),
    )
    for span_data in data.get("spans", []):
        trace.root_spans.append(_dict_to_span(span_data))
    return trace


def _dict_to_span(data: dict) -> Span:
    """Reconstruct a Span from a serialized dict."""
    span = Span(
        span_id=data.get("span_id", _short_id()),
        parent_span_id=data.get("parent_span_id"),
        name=data.get("name", ""),
        duration_ms=data.get("duration_ms", 0.0),
        attributes=data.get("attributes", {}),
        _start_wall=data.get("start_wall", ""),
    )
    for child_data in data.get("children", []):
        child = _dict_to_span(child_data)
        child.parent_span_id = span.span_id
        span.children.append(child)
    return span

"""Pydantic models for the orchestrator domain objects.

Defines Task, Plan, TaskResult, ExecutionReport, and LLMResponse.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Lifecycle status of a task."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Task(BaseModel):
    """A single discrete task inside a plan."""
    id: str
    type: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    expected_output: str = ""
    assigned_llm: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    cost: float = 0.0
    duration_ms: int = 0


class Plan(BaseModel):
    """Structured execution plan produced by the decomposer."""
    demand: str
    tasks: list[Task]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_estimated_cost: float = 0.0


class TaskResult(BaseModel):
    """Outcome of executing a single task."""
    task_id: str
    llm_used: str
    output: str
    cost: float = 0.0
    duration_ms: int = 0
    tokens_used: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    success: bool = True
    error: str | None = None
    cache_hit: bool = False
    quality_retried: bool = False
    wave_index: int = -1
    start_time_ms: int = 0


class LLMResponse(BaseModel):
    """Raw response from an LLM provider."""
    text: str
    tokens_input: int = 0
    tokens_output: int = 0
    cost: float = 0.0
    model: str = ""
    provider: str = ""


class ExecutionReport(BaseModel):
    """Final report after full orchestration run."""
    demand: str
    plan: Plan
    results: dict[str, TaskResult]
    total_cost: float = 0.0
    total_duration_ms: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    tasks_cached: int = 0
    tasks_quality_retried: int = 0
    tasks_deduplicated: int = 0
    estimated_cost: float = 0.0
    budget_limit: float = 0.0
    summary: str = ""

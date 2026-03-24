"""Simple cost tracking per orchestration execution.

Records token usage and cost per task and per LLM, and generates summary reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostRecord:
    """Single cost record for a task/LLM interaction."""
    task_id: str
    llm: str
    tokens_in: int
    tokens_out: int
    cost: float


class CostTracker:
    """Accumulates cost records and produces summaries."""

    def __init__(self) -> None:
        self._records: list[CostRecord] = []

    def record(
        self,
        task_id: str,
        llm: str,
        tokens_in: int,
        tokens_out: int,
        cost: float,
    ) -> None:
        """Record a cost entry for a single LLM call."""
        self._records.append(
            CostRecord(
                task_id=task_id,
                llm=llm,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=cost,
            )
        )

    def summary(self) -> dict:
        """Return cost breakdown by LLM and by task.

        Returns:
            {
                "total_cost": float,
                "total_tokens_in": int,
                "total_tokens_out": int,
                "by_llm": { llm_name: { cost, tokens_in, tokens_out, calls } },
                "by_task": { task_id: { cost, llm, tokens_in, tokens_out } },
            }
        """
        by_llm: dict[str, dict] = {}
        by_task: dict[str, dict] = {}
        total_cost = 0.0
        total_in = 0
        total_out = 0

        for r in self._records:
            total_cost += r.cost
            total_in += r.tokens_in
            total_out += r.tokens_out

            # By LLM
            if r.llm not in by_llm:
                by_llm[r.llm] = {"cost": 0.0, "tokens_in": 0, "tokens_out": 0, "calls": 0}
            by_llm[r.llm]["cost"] += r.cost
            by_llm[r.llm]["tokens_in"] += r.tokens_in
            by_llm[r.llm]["tokens_out"] += r.tokens_out
            by_llm[r.llm]["calls"] += 1

            # By task
            if r.task_id not in by_task:
                by_task[r.task_id] = {"cost": 0.0, "llm": r.llm, "tokens_in": 0, "tokens_out": 0}
            by_task[r.task_id]["cost"] += r.cost
            by_task[r.task_id]["tokens_in"] += r.tokens_in
            by_task[r.task_id]["tokens_out"] += r.tokens_out

        return {
            "total_cost": round(total_cost, 6),
            "total_tokens_in": total_in,
            "total_tokens_out": total_out,
            "by_llm": by_llm,
            "by_task": by_task,
        }

    def to_markdown(self) -> str:
        """Generate a formatted Markdown cost report."""
        s = self.summary()
        lines: list[str] = []

        lines.append("# Relatorio de Custos da Execucao")
        lines.append("")
        lines.append(f"**Custo total:** US$ {s['total_cost']:.4f}")
        lines.append(f"**Tokens entrada:** {s['total_tokens_in']:,}")
        lines.append(f"**Tokens saida:** {s['total_tokens_out']:,}")
        lines.append("")

        # By LLM
        lines.append("## Por LLM")
        lines.append("")
        lines.append("| LLM | Chamadas | Tokens In | Tokens Out | Custo (US$) |")
        lines.append("|-----|----------|-----------|------------|-------------|")
        for llm, data in sorted(s["by_llm"].items()):
            lines.append(
                f"| {llm} | {data['calls']} | {data['tokens_in']:,} "
                f"| {data['tokens_out']:,} | {data['cost']:.4f} |"
            )
        lines.append("")

        # By task
        lines.append("## Por Tarefa")
        lines.append("")
        lines.append("| Tarefa | LLM | Tokens In | Tokens Out | Custo (US$) |")
        lines.append("|--------|-----|-----------|------------|-------------|")
        for tid, data in s["by_task"].items():
            lines.append(
                f"| {tid} | {data['llm']} | {data['tokens_in']:,} "
                f"| {data['tokens_out']:,} | {data['cost']:.4f} |"
            )
        lines.append("")

        return "\n".join(lines)

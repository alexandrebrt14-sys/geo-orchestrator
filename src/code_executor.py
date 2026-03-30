"""Code-First Gate — resolve deterministic tasks without calling an LLM.

Before any task is routed to a provider, this module checks whether it can be
resolved with pure Python (regex, json, arithmetic, etc.).  When it can, we
skip the LLM call entirely, saving ~2-3 s of latency and ~$0.01 per invocation.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PT-BR language markers (used by detect_language)
# ---------------------------------------------------------------------------
_PTBR_MARKERS: list[str] = [
    "você",
    "não",
    "são",
    "também",
    "até",
    "além",
    "é o",
    "na prática",
    "produção",
    "análise",
]

# ---------------------------------------------------------------------------
# Stats tracker
# ---------------------------------------------------------------------------

@dataclass
class CodeFirstStats:
    """Cumulative statistics for the Code-First Gate."""

    tasks_resolved: int = 0
    tasks_passed_to_llm: int = 0
    estimated_savings_usd: float = 0.0
    estimated_time_saved_ms: int = 0

    # -- helpers -------------------------------------------------------------

    def record_resolved(self) -> None:
        """Record a task that was resolved without an LLM."""
        self.tasks_resolved += 1
        self.estimated_savings_usd += 0.01
        self.estimated_time_saved_ms += 2000

    def record_passed(self) -> None:
        """Record a task that was forwarded to an LLM."""
        self.tasks_passed_to_llm += 1

    @property
    def total_tasks(self) -> int:
        return self.tasks_resolved + self.tasks_passed_to_llm

    @property
    def resolution_rate(self) -> float:
        """Fraction of tasks resolved locally (0.0 – 1.0)."""
        if self.total_tasks == 0:
            return 0.0
        return self.tasks_resolved / self.total_tasks

    def summary(self) -> str:
        """Return a human-readable summary line."""
        return (
            f"Code-First Gate: {self.tasks_resolved}/{self.total_tasks} resolvidos localmente "
            f"({self.resolution_rate:.0%}) — economia estimada: "
            f"US$ {self.estimated_savings_usd:.2f}, {self.estimated_time_saved_ms} ms"
        )


# Module-level singleton so the orchestrator can inspect it at report time.
stats = CodeFirstStats()

# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------

def _count_words(input_data: str) -> str:
    """Count words and estimate tokens (1 token ≈ 0.75 words for PT-BR)."""
    words = input_data.split()
    word_count = len(words)
    token_estimate = math.ceil(word_count / 0.75)
    return (
        f"Contagem de palavras: {word_count}\n"
        f"Estimativa de tokens: ~{token_estimate}"
    )


def _extract_urls(input_data: str) -> str:
    """Extract all URLs from the input text."""
    url_pattern = re.compile(
        r"https?://[^\s<>\"')\]},;]+", re.IGNORECASE
    )
    urls = url_pattern.findall(input_data)
    if not urls:
        return "Nenhuma URL encontrada no texto."
    unique = list(dict.fromkeys(urls))  # preserve order, deduplicate
    lines = [f"  {i+1}. {u}" for i, u in enumerate(unique)]
    return f"URLs encontradas ({len(unique)}):\n" + "\n".join(lines)


def _validate_json(input_data: str) -> str:
    """Validate whether input_data is well-formed JSON."""
    try:
        obj = json.loads(input_data)
        kind = type(obj).__name__
        if isinstance(obj, dict):
            detail = f"{len(obj)} chaves"
        elif isinstance(obj, list):
            detail = f"{len(obj)} itens"
        else:
            detail = repr(obj)[:80]
        return f"JSON válido ({kind}, {detail})."
    except json.JSONDecodeError as exc:
        return f"JSON inválido — erro na linha {exc.lineno}, coluna {exc.colno}: {exc.msg}"


def _detect_language(input_data: str) -> str:
    """Detect whether the text is PT-BR, EN, or undetermined."""
    lower = input_data.lower()
    ptbr_hits = sum(1 for m in _PTBR_MARKERS if m in lower)

    en_markers = ["the ", "and ", "is ", "are ", "with ", "this ", "that ", "for "]
    en_hits = sum(1 for m in en_markers if m in lower)

    if ptbr_hits >= 3:
        return f"Idioma detectado: Português do Brasil (pt-BR) — {ptbr_hits} marcadores encontrados."
    if en_hits >= 3:
        return f"Idioma detectado: Inglês (en) — {en_hits} marcadores encontrados."
    if ptbr_hits > en_hits:
        return f"Idioma provável: Português do Brasil (pt-BR) — {ptbr_hits} marcadores."
    if en_hits > ptbr_hits:
        return f"Idioma provável: Inglês (en) — {en_hits} marcadores."
    return "Idioma indeterminado — texto muito curto ou ambíguo."


def _format_list(input_data: str) -> str:
    """Format raw text into a numbered list (one item per line)."""
    raw_items = re.split(r"[\n;,]+", input_data)
    items = [item.strip() for item in raw_items if item.strip()]
    if not items:
        return "Nenhum item encontrado para formatar."
    lines = [f"  {i+1}. {item}" for i, item in enumerate(items)]
    return f"Lista formatada ({len(items)} itens):\n" + "\n".join(lines)


def _merge_texts(input_data: str) -> str:
    """Merge multiple text blocks separated by '---' into one consolidated text."""
    blocks = [b.strip() for b in input_data.split("---") if b.strip()]
    if len(blocks) <= 1:
        return input_data.strip()
    merged = "\n\n".join(blocks)
    return f"Texto consolidado ({len(blocks)} blocos):\n\n{merged}"


def _calculate(input_data: str) -> str:
    """Evaluate simple arithmetic expressions found in the input.

    Supports +, -, *, /, parentheses, and basic aggregation keywords
    (soma, média) over comma-separated numbers.
    """
    lower = input_data.lower()

    # Handle "soma" / "média" over a set of numbers
    numbers = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", input_data)]
    if not numbers:
        return "Nenhum número encontrado para calcular."

    if "média" in lower or "media" in lower or "average" in lower:
        avg = sum(numbers) / len(numbers)
        return f"Média de {len(numbers)} valores: {avg:.4f}"

    if "soma" in lower or "sum" in lower or "total" in lower:
        return f"Soma de {len(numbers)} valores: {sum(numbers):.4f}"

    # Try to evaluate a single arithmetic expression
    expr_match = re.search(r"[\d(][\d+\-*/().\s]+[\d)]", input_data)
    if expr_match:
        expr = expr_match.group()
        # Safety: only allow digits, operators, parens, dots, whitespace
        if re.fullmatch(r"[\d+\-*/().\s]+", expr):
            try:
                result = eval(expr)  # noqa: S307 — input is sanitised above
                return f"Resultado: {expr.strip()} = {result}"
            except Exception:
                pass

    return f"Soma de {len(numbers)} valores: {sum(numbers):.4f}"


def _extract_entities(input_data: str) -> str:
    """Extract common entities (emails, URLs, dates, CPFs) via regex."""
    findings: list[str] = []

    emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", input_data)
    if emails:
        findings.append(f"E-mails ({len(emails)}): {', '.join(dict.fromkeys(emails))}")

    urls = re.findall(r"https?://[^\s<>\"')\]},;]+", input_data, re.IGNORECASE)
    if urls:
        findings.append(f"URLs ({len(urls)}): {', '.join(dict.fromkeys(urls))}")

    # Dates: dd/mm/yyyy, yyyy-mm-dd
    dates = re.findall(
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b", input_data
    )
    if dates:
        findings.append(f"Datas ({len(dates)}): {', '.join(dict.fromkeys(dates))}")

    # Brazilian CPFs: 000.000.000-00
    cpfs = re.findall(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", input_data)
    if cpfs:
        findings.append(f"CPFs ({len(cpfs)}): {', '.join(dict.fromkeys(cpfs))}")

    if not findings:
        return "Nenhuma entidade reconhecida no texto."
    return "Entidades extraídas:\n  " + "\n  ".join(findings)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

# Each entry: (list of trigger phrases, handler function)
_DISPATCH: list[tuple[list[str], Callable[[str], str], str]] = [
    (
        ["contar palavras", "word count", "token count", "contar tokens"],
        _count_words,
        "count_words",
    ),
    (
        ["extrair urls", "extract urls", "listar links", "extrair url"],
        _extract_urls,
        "extract_urls",
    ),
    (
        ["validar json", "validate json", "parse json"],
        _validate_json,
        "validate_json",
    ),
    (
        ["detectar idioma", "detect language", "qual idioma"],
        _detect_language,
        "detect_language",
    ),
    (
        ["formatar lista", "format list", "organizar itens"],
        _format_list,
        "format_list",
    ),
    (
        ["extrair entidades", "extract entities"],
        _extract_entities,
        "extract_entities",
    ),
    (
        ["calcular", "calculate", "soma", "média"],
        _calculate,
        "calculate",
    ),
]

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def try_code_first(
    task_description: str,
    task_type: str,
    input_data: str = "",
) -> str | None:
    """Attempt to resolve a task deterministically, without calling an LLM.

    Parameters
    ----------
    task_description:
        Natural-language description of what needs to be done.
    task_type:
        One of the 12 canonical task types (e.g. ``"data_processing"``).
    input_data:
        The payload text to operate on (may be empty).

    Returns
    -------
    str | None
        The result string if resolved locally, or ``None`` if the task
        should be forwarded to an LLM.
    """
    desc_lower = task_description.lower()

    # 1. Check merge/consolidate (requires task_type == data_processing)
    if task_type == "data_processing":
        merge_triggers = ["merge", "consolidar", "juntar"]
        if any(t in desc_lower for t in merge_triggers):
            result = _merge_texts(input_data)
            stats.record_resolved()
            logger.info(
                "CODE-FIRST: task resolved without LLM (%s) — saved ~$0.01 and ~2s",
                "merge_texts",
            )
            return result

    # 2. Walk the generic dispatch table
    for triggers, handler, handler_name in _DISPATCH:
        if any(t in desc_lower for t in triggers):
            result = handler(input_data)
            stats.record_resolved()
            logger.info(
                "CODE-FIRST: task resolved without LLM (%s) — saved ~$0.01 and ~2s",
                handler_name,
            )
            return result

    # 3. No match — forward to LLM
    stats.record_passed()
    return None

"""Regression test: garante que nenhum modelo descontinuado vaza no codigo.

Historicamente, 96% do gasto LLM do orchestrator (US$ 240/30d) vinha de
referencias hardcoded ao `claude-opus-4-20250514` no architect.py default
e no model_override do catalog YAML. Esse teste falha qualquer regressao
que reintroduza o modelo descontinuado em codigo executavel.

Permite mencao em arquivos de documentacao historica (docs/, RFCs, audits).
"""

from __future__ import annotations

from pathlib import Path

import pytest


# Modelos descontinuados que NUNCA devem aparecer em codigo executavel.
# Adicionar aqui sempre que um modelo for marcado como deprecated.
DEPRECATED_MODELS = [
    "claude-opus-4-20250514",       # substituido por claude-opus-4-6 em 2026-04-07
    "claude-3-opus-20240229",       # generations anteriores
    "claude-3-sonnet-20240229",
    "claude-3-haiku-20240307",
    "claude-2.1",
    "claude-2.0",
    "gpt-4-0125-preview",           # GPT-4 versions antigas
    "gpt-4-1106-preview",
    "gpt-3.5-turbo-0125",
    "gemini-1.5-pro-002",           # superado por 2.5-pro
    "gemini-1.5-flash-002",
]


# Arquivos onde a string e PERMITIDA por contexto historico/documental
ALLOW_LIST_PATHS = {
    "docs/AUDIT_2026-04-07.md",
    "docs/RFC-ORCHESTRATOR-REFACTORING.md",
    "tests/test_no_deprecated_models.py",  # este proprio arquivo
}


def _orchestrator_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _scan_files() -> list[Path]:
    """Scaneia todos os .py e .yaml em src/ e catalog/."""
    root = _orchestrator_root()
    extensions = ("*.py", "*.yaml", "*.yml")
    files: list[Path] = []
    for sub in ("src", "catalog", "scripts"):
        sub_dir = root / sub
        if not sub_dir.exists():
            continue
        for ext in extensions:
            files.extend(sub_dir.rglob(ext))
    return files


@pytest.mark.parametrize("model", DEPRECATED_MODELS)
def test_no_deprecated_model_in_executable_code(model: str):
    """Falha se algum arquivo executavel referenciar modelo descontinuado."""
    root = _orchestrator_root()
    offenders: list[tuple[str, int, str]] = []

    for path in _scan_files():
        rel = path.relative_to(root).as_posix()
        if rel in ALLOW_LIST_PATHS:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            # Ignora linhas que sao APENAS comentario explicativo histórico
            stripped = line.strip()
            if stripped.startswith("#") and "antes:" in stripped.lower():
                continue
            if model in line:
                # Permite mencoes em comentarios que comecam com "# OLD:" ou
                # "# Antes:" — sao notas de migracao explicitas
                if stripped.startswith("#") and any(
                    marker in stripped.lower()
                    for marker in ("old:", "antes:", "deprecated:", "legacy:")
                ):
                    continue
                offenders.append((rel, lineno, stripped[:120]))

    assert not offenders, (
        f"Modelo descontinuado {model!r} encontrado em codigo executavel:\n"
        + "\n".join(f"  {f}:{ln}: {snippet}" for f, ln, snippet in offenders)
        + "\n\nUse claude-opus-4-6 (atual) e remova as referencias antigas. "
        "Se a referencia for documental, adicione o arquivo a ALLOW_LIST_PATHS."
    )


def test_pipeline_system_base_is_cacheable():
    """Garante que o system prompt do Pipeline e grande o suficiente para
    ativar prompt caching no _call_anthropic (limiar 4000 chars / ~1024 tokens
    minimo da Anthropic).
    """
    from src.pipeline import PIPELINE_SYSTEM_BASE

    assert len(PIPELINE_SYSTEM_BASE) >= 4000, (
        f"PIPELINE_SYSTEM_BASE tem {len(PIPELINE_SYSTEM_BASE)} chars, mas precisa "
        "de >=4000 para ativar cache_control no _call_anthropic. "
        "Veja docstring de PIPELINE_SYSTEM_BASE para contexto."
    )


def test_pipeline_max_tokens_is_capped():
    """Garante que o pipeline aplica cap por task_type em vez de usar
    config.max_tokens cego (que era 8192 fixo para Claude e saturava
    historicamente).
    """
    from src.pipeline import Pipeline
    from src.config import LLM_CONFIGS

    claude = LLM_CONFIGS["claude"]
    # Architecture deve cair para 4096 (vs 8192 do config)
    assert Pipeline._max_tokens_for_task("architecture", claude) == 4096
    # Writing pode manter 8192 (output legitimamente longo)
    assert Pipeline._max_tokens_for_task("writing", claude) == 8192
    # Tipo desconhecido cai para o default 4096
    assert Pipeline._max_tokens_for_task("inexistente_xyz", claude) == 4096
    # Cap pelo modelo: se LLMConfig.max_tokens for menor, prevalece
    perplexity = LLM_CONFIGS["perplexity"]  # max_tokens=4096
    assert Pipeline._max_tokens_for_task("writing", perplexity) == 4096

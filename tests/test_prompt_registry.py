"""Testes do prompt_registry — sentinela contra drift de bytes.

Achado B-009 da auditoria 2026-04-08. Migracao do PIPELINE_SYSTEM_BASE
de literal Python triple-quoted para arquivo .txt versionado em
src/templates/. Restricao critica: a string carregada do arquivo deve
ser BYTE-IDENTICA ao literal antigo, caso contrario o cache_control
ephemeral da Anthropic invalida e voltamos a pagar 100% input em cada
call.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

# Garante que imports do src funcionam mesmo sem chaves reais
for k in [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
    "PERPLEXITY_API_KEY", "GROQ_API_KEY",
]:
    os.environ.setdefault(k, "test-key-not-real")


# SHA256 do prompt original capturado em 2026-04-09 antes da refatoracao.
# Este valor eh sentinela: se mudar, alguem editou o prompt — pode ser
# intencional (e o teste atualiza para o novo SHA) ou acidental (commit
# rejeitado por code review).
ORIGINAL_PROMPT_SHA256 = "a6a266c8386009a21af4df23d67145471167551d25829df75a51db6fadc16b04"
ORIGINAL_PROMPT_BYTES = 5848


def test_prompt_registry_loads():
    """Modulo importa sem erro."""
    from src.prompt_registry import (
        PIPELINE_SYSTEM_BASE,
        PIPELINE_SYSTEM_BASE_SHA256,
        PIPELINE_SYSTEM_BASE_BYTES,
    )
    assert PIPELINE_SYSTEM_BASE
    assert isinstance(PIPELINE_SYSTEM_BASE, str)
    assert len(PIPELINE_SYSTEM_BASE_SHA256) == 64  # SHA-256 hex


def test_prompt_byte_identity_preserved():
    """SHA256 deve ser EXATAMENTE igual ao do literal original.

    Se este teste falhar, o cache_control: ephemeral da Anthropic
    invalida e custos sobem em ~10x. Validar com:
        python -c 'import hashlib; print(hashlib.sha256(open("src/templates/pipeline_system_base.txt","rb").read()).hexdigest())'
    """
    from src.prompt_registry import PIPELINE_SYSTEM_BASE_SHA256, PIPELINE_SYSTEM_BASE_BYTES
    assert PIPELINE_SYSTEM_BASE_SHA256 == ORIGINAL_PROMPT_SHA256, (
        f"DRIFT DETECTADO: prompt mudou sem aviso. "
        f"esperado={ORIGINAL_PROMPT_SHA256}, recebido={PIPELINE_SYSTEM_BASE_SHA256}. "
        f"Se mudanca for intencional, atualize ORIGINAL_PROMPT_SHA256 neste teste."
    )
    assert PIPELINE_SYSTEM_BASE_BYTES == ORIGINAL_PROMPT_BYTES


def test_pipeline_re_exports_constants_for_compat():
    """pipeline.py mantem PIPELINE_SYSTEM_BASE como nome publico (compat backward)."""
    from src.pipeline import PIPELINE_SYSTEM_BASE, PIPELINE_SYSTEM_BASE_SHA256
    assert PIPELINE_SYSTEM_BASE
    assert PIPELINE_SYSTEM_BASE_SHA256 == ORIGINAL_PROMPT_SHA256


def test_load_uses_read_bytes_not_read_text():
    """Garantia: o loader usa read_bytes (preserva newlines em qualquer SO).

    read_text() em modo texto pode normalizar CRLF -> LF no Windows,
    mudando o SHA256 entre dev (Windows) e producao (Linux).
    """
    import inspect
    import re
    from src.prompt_registry import _load_byte_perfect
    source = inspect.getsource(_load_byte_perfect)
    assert "read_bytes" in source, "loader DEVE usar read_bytes para byte-identity"
    # Procura por chamadas .read_text( (nao por mencao em docstring/comentario).
    # Removemos docstring antes de checar.
    code_only = re.sub(r'"""[\s\S]*?"""', "", source)
    assert ".read_text(" not in code_only, (
        "loader NAO PODE chamar .read_text() — pode normalizar newlines"
    )


def test_template_file_exists_in_templates_dir():
    """Arquivo .txt existe no caminho canonico."""
    from src.prompt_registry import _PIPELINE_SYSTEM_BASE_PATH
    assert _PIPELINE_SYSTEM_BASE_PATH.exists()
    assert _PIPELINE_SYSTEM_BASE_PATH.name == "pipeline_system_base.txt"
    assert _PIPELINE_SYSTEM_BASE_PATH.parent.name == "templates"


def test_get_prompt_metadata_structure():
    """Metadata retorna campos esperados para inclusao no ExecutionReport."""
    from src.prompt_registry import get_prompt_metadata
    meta = get_prompt_metadata()
    assert "pipeline_system_base_sha256" in meta
    assert "pipeline_system_base_bytes" in meta
    assert "templates_dir" in meta
    assert len(meta["pipeline_system_base_sha256"]) == 64
    assert meta["pipeline_system_base_bytes"] > 0


def test_execution_report_includes_prompt_metadata():
    """ExecutionReport tem campo prompt_metadata para auditoria."""
    from src.models import ExecutionReport, Plan
    report = ExecutionReport(
        demand="test",
        plan=Plan(demand="test", tasks=[]),
        results={},
    )
    assert hasattr(report, "prompt_metadata")
    assert isinstance(report.prompt_metadata, dict)


def test_repeated_loads_yield_identical_string():
    """Carregamento eh idempotente — multiplas importacoes/leituras retornam
    a mesma string byte-identica."""
    from src.prompt_registry import _load_byte_perfect, _PIPELINE_SYSTEM_BASE_PATH
    a = _load_byte_perfect(_PIPELINE_SYSTEM_BASE_PATH)
    b = _load_byte_perfect(_PIPELINE_SYSTEM_BASE_PATH)
    assert a == b
    assert hashlib.sha256(a.encode("utf-8")).hexdigest() == ORIGINAL_PROMPT_SHA256


def test_no_trailing_whitespace_introduced():
    """O arquivo nao deve ter trailing whitespace ou BOM que mudaria o SHA."""
    from src.prompt_registry import _PIPELINE_SYSTEM_BASE_PATH
    raw = _PIPELINE_SYSTEM_BASE_PATH.read_bytes()
    # Sem BOM UTF-8
    assert not raw.startswith(b"\xef\xbb\xbf")
    # SHA256 do raw deve bater
    assert hashlib.sha256(raw).hexdigest() == ORIGINAL_PROMPT_SHA256

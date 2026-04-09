"""Registry de prompts versionados — load + checksum.

Achado B-009 da auditoria de ecossistema 2026-04-08. Antes deste modulo,
PIPELINE_SYSTEM_BASE estava hardcoded como literal Python triple-quoted
em src/pipeline.py. Sem versionamento, sem checksum, sem rastreabilidade.

Por que importa:
- Reprodutibilidade cientifica (papers consome geo-orchestrator e precisa
  saber EXATAMENTE qual prompt gerou cada resposta)
- Auditoria post-incident (qual versao do prompt estava em producao
  quando o LLM produziu output X?)
- Detecao de drift acidental (alguem editou o prompt sem aviso?)

Restricao critica de implementacao:
A constante deve ser carregada UMA vez no module init e congelada como
imutavel. Qualquer mudanca de byte invalida o cache_control: ephemeral
da Anthropic (90% desconto em input tokens cacheados). Por isso:
- Arquivo .txt (nao .yaml) — YAML re-quote/re-escape pode subtilmente
  alterar bytes
- Path.read_bytes().decode("utf-8") — evita line ending mangling do
  Windows que aconteceria com Path.read_text() em modo texto
- SHA256 calculado UMA vez e cached
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Diretorio dos templates de prompt — sibling de src/
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Path canonico do prompt principal do pipeline
_PIPELINE_SYSTEM_BASE_PATH = _TEMPLATES_DIR / "pipeline_system_base.txt"


def _load_byte_perfect(path: Path) -> str:
    """Le um arquivo de texto preservando byte-identity.

    Usa read_bytes().decode("utf-8") em vez de read_text() porque
    read_text() pode aplicar normalizacao de newlines no Windows
    (CRLF -> LF), o que mudaria o SHA256 entre plataformas.

    Returns:
        String UTF-8 do conteudo, byte-identica ao arquivo no disco.

    Raises:
        FileNotFoundError: se o arquivo nao existir.
        UnicodeDecodeError: se o arquivo nao for UTF-8 valido.
    """
    return path.read_bytes().decode("utf-8")


def _sha256(content: str) -> str:
    """Calcula SHA-256 hex de uma string codificada em UTF-8."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# ─── Carregamento eager no module init ─────────────────────────────────────
#
# Estes valores sao calculados UMA vez quando o modulo eh importado pela
# primeira vez. Tornam-se "constantes congeladas" que podem ser referenciadas
# por pipeline.py, models.py (para incluir no ExecutionReport), e qualquer
# outro consumidor.
#
# Falha eager: se o arquivo nao existir ou nao for UTF-8, levanta na hora
# do import — comportamento desejado, evita silently usar fallback errado
# em producao.

try:
    PIPELINE_SYSTEM_BASE: str = _load_byte_perfect(_PIPELINE_SYSTEM_BASE_PATH)
    PIPELINE_SYSTEM_BASE_SHA256: str = _sha256(PIPELINE_SYSTEM_BASE)
    PIPELINE_SYSTEM_BASE_BYTES: int = len(PIPELINE_SYSTEM_BASE.encode("utf-8"))
    logger.debug(
        "loaded pipeline_system_base.txt: %d bytes, sha256=%s",
        PIPELINE_SYSTEM_BASE_BYTES,
        PIPELINE_SYSTEM_BASE_SHA256[:16],
    )
except FileNotFoundError as exc:
    raise RuntimeError(
        f"templates/pipeline_system_base.txt nao encontrado em {_PIPELINE_SYSTEM_BASE_PATH}. "
        f"Este arquivo eh OBRIGATORIO desde B-009 (auditoria 2026-04-08). "
        f"Restaure de git ou regenere via scripts."
    ) from exc


def get_prompt_metadata() -> dict:
    """Metadata canonica para incluir em ExecutionReport.

    Permite auditoria ex-post: dado um execution_*.json, recuperar
    exatamente qual versao do prompt foi usada.
    """
    return {
        "pipeline_system_base_sha256": PIPELINE_SYSTEM_BASE_SHA256,
        "pipeline_system_base_bytes": PIPELINE_SYSTEM_BASE_BYTES,
        "templates_dir": str(_TEMPLATES_DIR),
    }

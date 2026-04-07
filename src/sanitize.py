"""Sanitização de paths e slugs gerados (sprint 3 — 2026-04-07).

Reusa licao do incidente 2026-03-27 onde 55 hrefs do site landing-page-geo
foram corrompidos porque um script de fix de acentos rodou replace global
sem proteger paths/imports/JSX hrefs.

Politica:
- Texto visivel ao usuario: PT-BR com acentuacao COMPLETA.
- Identificadores tecnicos (filenames, paths, slugs URL, env vars,
  chaves de dict, task_ids): SEMPRE ASCII puro (a-z, 0-9, _, -, .).

Quando um LLM produz uma string que vira nome de arquivo ou slug
(ex: task_id "criar_helper_acentuação", filename "relatório.json"),
o caller deve passar pelo sanitize_path() antes de usar no filesystem.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# Caracteres permitidos em filenames/slugs (ASCII safe)
_SAFE_FILENAME_CHARS = re.compile(r"[^a-zA-Z0-9._-]")

# Caracteres inicial-final que sao perigosos
_LEADING_DOTS = re.compile(r"^\.+")  # esconde arquivos / path traversal
_TRAILING_DOTS = re.compile(r"\.+$")  # Windows nao gosta

# Path traversal markers
_PATH_TRAVERSAL = re.compile(r"\.\.[\\/]|\.\.")

# Comprimento maximo (Linux ext4 = 255, Windows NTFS = 255, mas ser conservador)
MAX_FILENAME_LENGTH = 200


class SanitizationError(ValueError):
    """Raised when input cannot be sanitized into a safe filename."""


def sanitize_filename(name: str, *, fallback: str = "unnamed") -> str:
    """Converte uma string arbitraria em filename ASCII seguro.

    Politica:
    1. Normaliza Unicode para NFD e remove diacriticos (ç -> c, á -> a).
    2. Substitui qualquer caractere fora de [a-zA-Z0-9._-] por underscore.
    3. Remove dots iniciais/finais (path traversal + Windows).
    4. Colapsa underscores duplos.
    5. Trunca em MAX_FILENAME_LENGTH chars.
    6. Se ficar vazia ou perigosa, retorna o fallback.

    Args:
        name: String arbitraria (potencialmente com acentos, espacos, /).
        fallback: Valor a usar se o nome ficar vazio/invalido apos sanitizacao.

    Returns:
        Filename ASCII safe, garantido nao vazio.

    Examples:
        >>> sanitize_filename("relatório-final.json")
        'relatorio-final.json'
        >>> sanitize_filename("../../etc/passwd")
        'etc_passwd'
        >>> sanitize_filename("criar_helper_acentuação")
        'criar_helper_acentuacao'
        >>> sanitize_filename("")
        'unnamed'
        >>> sanitize_filename("...")
        'unnamed'
    """
    if not isinstance(name, str):
        name = str(name) if name is not None else ""

    # 1. Remover path traversal markers ANTES de tudo
    name = _PATH_TRAVERSAL.sub("_", name)

    # 2. Remover separadores de path
    name = name.replace("/", "_").replace("\\", "_")

    # 3. Normalizar Unicode (NFD decompose) e dropar diacriticos
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")

    # 4. Substituir caracteres nao-safe por underscore
    safe = _SAFE_FILENAME_CHARS.sub("_", ascii_only)

    # 5. Remover dots iniciais (esconde arquivo) e finais (Windows)
    safe = _LEADING_DOTS.sub("", safe)
    safe = _TRAILING_DOTS.sub("", safe)

    # 6. Colapsar underscores duplos
    safe = re.sub(r"_+", "_", safe)

    # 7. Trim de underscores nas pontas
    safe = safe.strip("_-")

    # 8. Truncar
    if len(safe) > MAX_FILENAME_LENGTH:
        # Preservar extensao se existir
        if "." in safe[-10:]:
            stem, _, ext = safe.rpartition(".")
            safe = stem[: MAX_FILENAME_LENGTH - len(ext) - 1] + "." + ext
        else:
            safe = safe[:MAX_FILENAME_LENGTH]

    # 9. Fallback se vazio ou perigoso
    if not safe or safe in (".", "..", "_", "-"):
        return fallback

    return safe


def sanitize_path(base_dir: Path | str, name: str, *, fallback: str = "unnamed") -> Path:
    """Constroi um Path seguro juntando base_dir + sanitize_filename(name).

    Garante que o path final esta DENTRO de base_dir (defesa contra
    path traversal mesmo que o sanitize_filename falhe).

    Args:
        base_dir: Diretorio base (deve ser um Path absoluto ou relativo seguro).
        name: Nome do arquivo (potencialmente com acentos/path traversal).
        fallback: Nome de fallback se sanitize_filename ficar vazio.

    Returns:
        Path absoluto/resolvido garantidamente dentro de base_dir.

    Raises:
        SanitizationError: Se apos resolucao o path final escapar de base_dir.
    """
    base = Path(base_dir).resolve()
    safe_name = sanitize_filename(name, fallback=fallback)
    candidate = (base / safe_name).resolve()

    # Defesa final: garantir que candidate esta DENTRO de base
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise SanitizationError(
            f"Path '{candidate}' escapou de base '{base}' apos sanitizacao "
            f"(input original: {name!r})"
        ) from exc

    return candidate


def sanitize_slug(text: str, *, max_length: int = 80, fallback: str = "untitled") -> str:
    """Converte texto em slug URL-safe (kebab-case ASCII).

    Diferente de sanitize_filename: usa hifens em vez de underscores
    e e mais agressivo ao limpar (URLs aceitam menos caracteres).

    Args:
        text: Texto arbitrario (titulo, frase).
        max_length: Comprimento maximo do slug.
        fallback: Slug a usar se ficar vazio.

    Returns:
        Slug ASCII em kebab-case, sem caracteres especiais.

    Examples:
        >>> sanitize_slug("Refatoração do Orquestrador v2.0!")
        'refatoracao-do-orquestrador-v2-0'
        >>> sanitize_slug("Teste com / barra")
        'teste-com-barra'
    """
    if not isinstance(text, str):
        text = str(text) if text is not None else ""

    # NFD + drop diacriticos
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = nfkd.encode("ascii", "ignore").decode("ascii")

    # Lowercase
    ascii_only = ascii_only.lower()

    # Substituir nao-alfanumericos por hifen
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only)

    # Trim hifens das pontas e colapsar
    slug = slug.strip("-")
    slug = re.sub(r"-+", "-", slug)

    # Truncar
    if len(slug) > max_length:
        slug = slug[:max_length].rstrip("-")

    return slug or fallback

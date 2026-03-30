"""Cache semântico inspirado no AFlow (arXiv 2410.10762).

Usa similaridade bag-of-words leve (sem dependências ML externas)
para encontrar resultados em cache para tarefas semanticamente similares.
Fallback para matching exato SHA-256 para lookups sem custo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Stopwords PT-BR + EN (conjunto fixo, sem dependências externas)
STOPWORDS: frozenset[str] = frozenset({
    # PT-BR
    "de", "da", "do", "dos", "das", "em", "no", "na", "nos", "nas",
    "um", "uma", "uns", "umas", "o", "a", "os", "as", "que", "para",
    "com", "por", "se", "mais", "como", "mas", "foi", "ao", "aos",
    "ou", "ser", "ter", "seu", "sua", "esta", "este", "isso",
    # EN
    "the", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "into", "through", "and", "but",
    "or", "not", "no", "so", "if", "then", "than", "that", "this",
    "it", "its",
})


class SemanticCache:
    """Cache layer that finds results for semantically similar tasks.

    Uses lightweight bag-of-words cosine similarity (no external ML deps).
    Falls back to exact SHA-256 matching for zero-cost lookups.
    """

    def __init__(self, cache_dir: Path, default_ttl: int = 86400) -> None:
        """Inicializa o cache semântico.

        Args:
            cache_dir: Diretório para arquivos de cache (ex: output/.cache/).
            default_ttl: TTL padrão em segundos (24h).
        """
        self.cache_dir = Path(cache_dir)
        self.default_ttl = default_ttl
        self._index: list[dict] = []
        self._index_path = self.cache_dir / ".semantic_index.json"

        # Contadores de estatísticas (sessão atual)
        self._exact_hits = 0
        self._semantic_hits = 0
        self._misses = 0

        # Garante que o diretório existe
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._load_index()

    # ------------------------------------------------------------------
    # Métodos públicos
    # ------------------------------------------------------------------

    def lookup(
        self,
        description: str,
        task_type: str,
        threshold: float = 0.85,
    ) -> str | None:
        """Busca resultado em cache para uma descrição de tarefa.

        Tenta primeiro matching exato SHA-256 (grátis, instantâneo).
        Se não encontrar, calcula similaridade bag-of-words contra
        todas as entradas do mesmo task_type.

        Args:
            description: Descrição textual da tarefa.
            task_type: Tipo da tarefa (ex: "research", "write", "analyze").
            threshold: Similaridade mínima para considerar hit (0.0–1.0).

        Returns:
            Conteúdo em cache ou None se não encontrou match.
        """
        # 1. Tentativa exata (custo zero)
        exact_key = self._exact_key(description, task_type)
        exact_result = self._read_cache_file(exact_key)
        if exact_result is not None:
            self._exact_hits += 1
            logger.info(
                "SEMANTIC CACHE: exact hit for task_type='%s' key=%s",
                task_type,
                exact_key[:12],
            )
            return exact_result

        # 2. Busca semântica
        now = time.time()
        query_tokens = self._tokenize(description)
        if not query_tokens:
            self._misses += 1
            return None

        best_score = 0.0
        best_entry: dict | None = None

        for entry in self._index:
            # Filtra por task_type
            if entry.get("task_type") != task_type:
                continue
            # Verifica TTL
            if now - entry["timestamp"] > entry.get("ttl", self.default_ttl):
                continue
            # Calcula similaridade
            entry_tokens = set(entry.get("tokens", []))
            score = self._similarity(query_tokens, entry_tokens)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry is not None and best_score >= threshold:
            result = self._read_cache_file(best_entry["key"])
            if result is not None:
                self._semantic_hits += 1
                logger.info(
                    "SEMANTIC CACHE: hit (similarity=%.2f) for task_type='%s'",
                    best_score,
                    task_type,
                )
                return result

        self._misses += 1
        logger.debug(
            "SEMANTIC CACHE: miss for task_type='%s' (best=%.2f, threshold=%.2f)",
            task_type,
            best_score,
            threshold,
        )
        return None

    def store(
        self,
        description: str,
        task_type: str,
        result: str,
        ttl: int | None = None,
    ) -> str:
        """Armazena resultado no cache com chave SHA-256 e índice semântico.

        Args:
            description: Descrição textual da tarefa.
            task_type: Tipo da tarefa.
            result: Conteúdo a ser cacheado.
            ttl: TTL em segundos (usa default_ttl se None).

        Returns:
            Chave de cache (hash SHA-256).
        """
        effective_ttl = ttl if ttl is not None else self.default_ttl
        key = self._exact_key(description, task_type)

        # Grava arquivo de cache
        cache_file = self.cache_dir / f"{key}.txt"
        cache_file.write_text(result, encoding="utf-8")

        # Atualiza índice semântico
        tokens = self._tokenize(description)
        entry = {
            "key": key,
            "description_preview": description[:100],
            "task_type": task_type,
            "tokens": sorted(tokens),
            "timestamp": time.time(),
            "ttl": effective_ttl,
        }

        # Remove entrada anterior com mesma chave (se existir)
        self._index = [e for e in self._index if e.get("key") != key]
        self._index.append(entry)

        # Prune de entradas expiradas
        self._prune_expired()

        # Persiste índice
        self._save_index()

        logger.info(
            "SEMANTIC CACHE: stored key=%s task_type='%s' ttl=%ds tokens=%d",
            key[:12],
            task_type,
            effective_ttl,
            len(tokens),
        )
        return key

    def get_stats(self) -> dict:
        """Retorna estatísticas do cache.

        Returns:
            Dicionário com total_entries, exact_hits, semantic_hits,
            misses e hit_rate.
        """
        total_lookups = self._exact_hits + self._semantic_hits + self._misses
        hit_rate = (
            (self._exact_hits + self._semantic_hits) / total_lookups
            if total_lookups > 0
            else 0.0
        )
        return {
            "total_entries": len(self._index),
            "exact_hits": self._exact_hits,
            "semantic_hits": self._semantic_hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 4),
        }

    # ------------------------------------------------------------------
    # Métodos internos
    # ------------------------------------------------------------------

    def _tokenize(self, text: str) -> set[str]:
        """Tokeniza texto em conjunto de palavras únicas.

        Faz lowercase, split por não-alfanumérico, remove stopwords
        e palavras com menos de 3 caracteres.

        Args:
            text: Texto de entrada.

        Returns:
            Conjunto de tokens únicos.
        """
        words = re.split(r"[^a-zA-Z0-9À-ÿ]+", text.lower())
        return {
            w
            for w in words
            if len(w) >= 3 and w not in STOPWORDS
        }

    def _similarity(self, tokens_a: set[str], tokens_b: set[str]) -> float:
        """Calcula similaridade Jaccard entre dois conjuntos de tokens.

        Jaccard = |A ∩ B| / |A ∪ B|
        Simples, rápido, sem dependência de numpy.

        Args:
            tokens_a: Primeiro conjunto de tokens.
            tokens_b: Segundo conjunto de tokens.

        Returns:
            Valor entre 0.0 e 1.0.
        """
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b)
        return intersection / union if union > 0 else 0.0

    def _exact_key(self, description: str, task_type: str) -> str:
        """Gera chave SHA-256 determinística para lookup exato.

        Args:
            description: Descrição da tarefa.
            task_type: Tipo da tarefa.

        Returns:
            Hash SHA-256 hexadecimal.
        """
        raw = f"{task_type}:{description}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _read_cache_file(self, key: str) -> str | None:
        """Lê conteúdo de um arquivo de cache pelo key.

        Args:
            key: Hash SHA-256 do cache.

        Returns:
            Conteúdo do arquivo ou None se não existir.
        """
        cache_file = self.cache_dir / f"{key}.txt"
        if cache_file.exists():
            try:
                return cache_file.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Erro ao ler cache %s: %s", key[:12], exc)
        return None

    def _load_index(self) -> None:
        """Carrega índice semântico do disco."""
        if not self._index_path.exists():
            self._index = []
            return
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._index = data
                logger.debug(
                    "SEMANTIC CACHE: índice carregado com %d entradas",
                    len(self._index),
                )
            else:
                logger.warning("Formato inválido no índice semântico, reiniciando")
                self._index = []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Erro ao carregar índice semântico: %s", exc)
            self._index = []

    def _save_index(self) -> None:
        """Persiste índice semântico no disco."""
        try:
            self._index_path.write_text(
                json.dumps(self._index, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Erro ao salvar índice semântico: %s", exc)

    def _prune_expired(self) -> None:
        """Remove entradas expiradas do índice e seus arquivos de cache."""
        now = time.time()
        active: list[dict] = []
        pruned = 0

        for entry in self._index:
            age = now - entry.get("timestamp", 0)
            ttl = entry.get("ttl", self.default_ttl)
            if age <= ttl:
                active.append(entry)
            else:
                pruned += 1
                # Remove arquivo de cache correspondente
                cache_file = self.cache_dir / f"{entry['key']}.txt"
                if cache_file.exists():
                    try:
                        cache_file.unlink()
                    except OSError:
                        pass

        if pruned > 0:
            logger.info("SEMANTIC CACHE: %d entradas expiradas removidas", pruned)
        self._index = active

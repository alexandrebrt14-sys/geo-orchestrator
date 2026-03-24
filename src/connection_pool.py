"""Shared HTTP connection pool — one httpx.AsyncClient per provider.

Singleton pattern ensures all LLM calls reuse persistent connections,
avoiding the overhead of creating/destroying TCP+TLS per request.
"""

from __future__ import annotations

import asyncio
import logging
from typing import ClassVar

import httpx

from .config import DEFAULT_TIMEOUT, Provider

logger = logging.getLogger(__name__)

# Per-provider connection limits
_MAX_CONNECTIONS = 10
_MAX_KEEPALIVE = 20
_KEEPALIVE_EXPIRY = 30  # seconds


class ConnectionPool:
    """Maintains one httpx.AsyncClient per LLM provider.

    Usage:
        pool = ConnectionPool.get_instance()
        client = await pool.get_client(Provider.OPENAI, timeout=60.0)
        # use client for requests — do NOT close it manually
        # ...
        # At shutdown:
        await ConnectionPool.shutdown()
    """

    _instance: ClassVar[ConnectionPool | None] = None
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    def __init__(self) -> None:
        self._clients: dict[Provider, httpx.AsyncClient] = {}
        self._client_locks: dict[Provider, asyncio.Lock] = {
            p: asyncio.Lock() for p in Provider
        }

    @classmethod
    def get_instance(cls) -> ConnectionPool:
        """Return the singleton ConnectionPool instance (thread-safe via lock)."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def get_client(
        self, provider: Provider, timeout: float | None = None
    ) -> httpx.AsyncClient:
        """Return a reusable httpx.AsyncClient for the given provider.

        Creates the client lazily on first access. Subsequent calls return
        the same client with persistent connections.

        Args:
            provider: LLM provider enum value.
            timeout: Request timeout in seconds. Defaults to DEFAULT_TIMEOUT.
        """
        if provider in self._clients:
            return self._clients[provider]

        async with self._client_locks[provider]:
            # Double-check after acquiring lock
            if provider in self._clients:
                return self._clients[provider]

            effective_timeout = timeout or DEFAULT_TIMEOUT
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=10.0,
                    read=effective_timeout,
                    write=10.0,
                    pool=10.0,
                ),
                limits=httpx.Limits(
                    max_connections=_MAX_CONNECTIONS,
                    max_keepalive_connections=_MAX_KEEPALIVE,
                    keepalive_expiry=_KEEPALIVE_EXPIRY,
                ),
                http2=True,
            )
            self._clients[provider] = client
            logger.debug(
                "ConnectionPool: created client for %s (timeout=%.0fs, max_conn=%d)",
                provider.value,
                effective_timeout,
                _MAX_CONNECTIONS,
            )
            return client

    @classmethod
    async def shutdown(cls) -> None:
        """Close all persistent HTTP clients. Call at application shutdown."""
        if cls._instance is None:
            return

        instance = cls._instance
        for provider, client in instance._clients.items():
            try:
                await client.aclose()
                logger.debug("ConnectionPool: closed client for %s", provider.value)
            except Exception as exc:
                logger.warning(
                    "ConnectionPool: error closing client for %s: %s",
                    provider.value,
                    exc,
                )
        instance._clients.clear()
        cls._instance = None
        logger.info("ConnectionPool: all clients shut down.")

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton (for testing). Does NOT close clients."""
        cls._instance = None

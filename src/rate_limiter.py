"""Per-provider rate limiter using token bucket algorithm.

Ensures each LLM provider stays within its RPM limits, even when
multiple tasks try to call the same provider simultaneously.

Provider limits (as of 2026-03):
- Gemini 2.5 Flash: 30 RPM (billing ativo, R$500 credito)
- Perplexity Sonar: 20 RPM
- Anthropic Claude: 60 RPM
- OpenAI GPT-4o: 60 RPM
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from .config import Provider

logger = logging.getLogger(__name__)


@dataclass
class ProviderLimit:
    """Rate limit configuration for a single provider."""
    requests_per_minute: int
    burst_size: int = 1  # Max concurrent burst before throttling


# RPM limits per provider (billing ativo em todas as contas)
PROVIDER_LIMITS: dict[Provider, ProviderLimit] = {
    Provider.ANTHROPIC: ProviderLimit(requests_per_minute=60, burst_size=3),
    Provider.OPENAI: ProviderLimit(requests_per_minute=60, burst_size=3),
    Provider.GOOGLE: ProviderLimit(requests_per_minute=30, burst_size=3),  # Billing ativo (R$500 credito)
    Provider.PERPLEXITY: ProviderLimit(requests_per_minute=20, burst_size=2),
    Provider.GROQ: ProviderLimit(requests_per_minute=30, burst_size=5),    # Free tier generoso, inferencia ultra-rapida
}


class TokenBucket:
    """Token bucket rate limiter for a single provider.

    - Tokens refill at a steady rate (RPM / 60 per second).
    - Bucket starts full up to burst_size.
    - acquire() blocks until a token is available.
    - Thread-safe via asyncio.Lock.
    """

    def __init__(self, limit: ProviderLimit) -> None:
        self.rpm = limit.requests_per_minute
        self.burst_size = limit.burst_size
        self.tokens: float = float(limit.burst_size)
        self.last_refill: float = time.monotonic()
        self.refill_rate: float = limit.requests_per_minute / 60.0  # tokens/sec
        self._lock = asyncio.Lock()

        # Sliding window tracking for diagnostics
        self._request_timestamps: list[float] = []

    async def acquire(self, provider_name: str = "") -> None:
        """Wait until a token is available, then consume it.

        Args:
            provider_name: Used only for logging.
        """
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self.last_refill
                self.tokens = min(
                    self.tokens + elapsed * self.refill_rate,
                    float(self.burst_size),
                )
                self.last_refill = now

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    self._record_request(now)
                    return

                # Calculate wait time for next token
                wait_time = (1.0 - self.tokens) / self.refill_rate
                if provider_name:
                    logger.info(
                        "Rate limiter: %s throttled, waiting %.1fs (RPM limit: %d)",
                        provider_name,
                        wait_time,
                        self.rpm,
                    )
                # Release lock while sleeping so other coroutines can check
                self._lock.release()
                try:
                    await asyncio.sleep(wait_time)
                finally:
                    await self._lock.acquire()

    def _record_request(self, timestamp: float) -> None:
        """Record a request timestamp for sliding window RPM tracking."""
        self._request_timestamps.append(timestamp)
        # Prune timestamps older than 60 seconds
        cutoff = timestamp - 60.0
        self._request_timestamps = [
            t for t in self._request_timestamps if t > cutoff
        ]

    @property
    def current_rpm(self) -> int:
        """Return the number of requests in the last 60 seconds."""
        now = time.monotonic()
        cutoff = now - 60.0
        return sum(1 for t in self._request_timestamps if t > cutoff)

    def status(self) -> dict:
        """Return current state for diagnostics."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        available = min(
            self.tokens + elapsed * self.refill_rate,
            float(self.burst_size),
        )
        return {
            "tokens_available": round(available, 2),
            "burst_size": self.burst_size,
            "rpm_limit": self.rpm,
            "current_rpm": self.current_rpm,
        }


class RateLimiter:
    """Global rate limiter that manages per-provider token buckets.

    Usage:
        limiter = RateLimiter()
        await limiter.acquire(Provider.GOOGLE)  # blocks if Gemini is at 30 RPM
        # ... make API call ...
    """

    _instance: RateLimiter | None = None

    def __init__(self) -> None:
        self._buckets: dict[Provider, TokenBucket] = {}
        for provider, limit in PROVIDER_LIMITS.items():
            self._buckets[provider] = TokenBucket(limit)

    @classmethod
    def get_instance(cls) -> RateLimiter:
        """Singleton accessor — ensures one limiter per process."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    async def acquire(self, provider: Provider) -> None:
        """Wait until a request slot is available for the given provider.

        Args:
            provider: The LLM provider to acquire a slot for.
        """
        bucket = self._buckets.get(provider)
        if bucket is None:
            # Unknown provider — no rate limiting applied
            logger.warning("No rate limit configured for provider: %s", provider)
            return
        await bucket.acquire(provider_name=provider.value)

    def current_rpm(self, provider: Provider) -> int:
        """Return current RPM for a provider."""
        bucket = self._buckets.get(provider)
        if bucket is None:
            return 0
        return bucket.current_rpm

    def status(self) -> dict[str, dict]:
        """Return status of all provider buckets."""
        return {
            provider.value: bucket.status()
            for provider, bucket in self._buckets.items()
        }

    def min_interval(self, provider: Provider) -> float:
        """Return minimum seconds between requests for a provider."""
        limit = PROVIDER_LIMITS.get(provider)
        if limit is None:
            return 0.0
        return 60.0 / limit.requests_per_minute

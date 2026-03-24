"""Unified async HTTP client for all 4 LLM providers.

Handles API format differences, retries with exponential backoff,
timeouts, rate limiting, and cost calculation.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time

import httpx

from .config import LLMConfig, Provider
from .finops import BudgetExceededError, get_finops
from .models import LLMResponse
from .rate_limiter import RateLimiter
from .tracer import TraceManager

logger = logging.getLogger(__name__)


class LLMClient:
    """Async client that can query any of the 4 supported LLM providers.

    Supports per-task timeout overrides and connection pooling.
    """

    TIMEOUT = 60.0
    MAX_RETRIES = 2
    BASE_RETRY_DELAY = 2.0  # seconds — exponential: 2s, 4s, 8s

    def __init__(self, config: LLMConfig, timeout_override: float | None = None) -> None:
        self.config = config
        self._timeout = timeout_override or self.TIMEOUT
        self._rate_limiter = RateLimiter.get_instance()

    async def query(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4000,
    ) -> LLMResponse:
        """Send a prompt to the configured LLM and return a unified response.

        Features:
        - Per-provider rate limiting (respects RPM limits)
        - Exponential backoff with jitter: 2s, 4s, 8s
        - Respects Retry-After header on 429 responses
        - Retries on 429 (rate-limit), 500 (server error), and timeouts
        """
        if not prompt or not prompt.strip():
            raise ValueError("Prompt cannot be empty")
        if max_tokens <= 0:
            raise ValueError(f"max_tokens must be positive, got {max_tokens}")

        tracer = TraceManager.get_instance()
        last_error: Exception | None = None
        provider_name = self.config.provider.value

        for attempt in range(1 + self.MAX_RETRIES):
            try:
                # Rate limiter span
                rl_span = tracer.start_span(
                    f"rate_limit.wait.{provider_name}",
                    provider=provider_name,
                )
                await self._rate_limiter.acquire(self.config.provider)
                tracer.finish_span(rl_span, status="ok")

                # LLM call span
                llm_span = tracer.start_span(
                    f"llm.query.{provider_name}",
                    provider=provider_name,
                    model=self.config.model,
                    attempt=attempt + 1,
                )
                response = await self._call(prompt, system, max_tokens)
                tracer.finish_span(
                    llm_span,
                    status="ok",
                    tokens_in=response.tokens_input,
                    tokens_out=response.tokens_output,
                    cost=response.cost,
                )
                return response

            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                retryable = status in (429, 500, 502, 503)

                # Finish the LLM span with error
                if 'llm_span' in dir():
                    tracer.finish_span(llm_span, status="error", http_status=status)

                if retryable and attempt < self.MAX_RETRIES:
                    wait_time = self._compute_backoff(attempt, exc)

                    # Retry span
                    retry_span = tracer.start_span(
                        f"llm.retry.{provider_name}",
                        provider=provider_name,
                        attempt=attempt + 1,
                        http_status=status,
                        wait_seconds=round(wait_time, 2),
                        is_rate_limit=status == 429,
                    )

                    logger.warning(
                        "Retry %d/%d for %s (HTTP %d): waiting %.1fs",
                        attempt + 1,
                        self.MAX_RETRIES,
                        self.config.provider.value,
                        status,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    tracer.finish_span(retry_span, status="ok")
                    continue
                raise

            except httpx.TimeoutException as exc:
                last_error = exc

                # Finish the LLM span with error
                if 'llm_span' in dir():
                    tracer.finish_span(llm_span, status="error", error="timeout")

                if attempt < self.MAX_RETRIES:
                    wait_time = self._compute_backoff(attempt)

                    # Retry span
                    retry_span = tracer.start_span(
                        f"llm.retry.{provider_name}",
                        provider=provider_name,
                        attempt=attempt + 1,
                        reason="timeout",
                        wait_seconds=round(wait_time, 2),
                    )

                    logger.warning(
                        "Retry %d/%d for %s (timeout): waiting %.1fs",
                        attempt + 1,
                        self.MAX_RETRIES,
                        self.config.provider.value,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    tracer.finish_span(retry_span, status="ok")
                    continue
                raise

        # Should never reach here, but guarantee we always raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("Unexpected state: no error captured in retry loop")

    def _compute_backoff(
        self,
        attempt: int,
        exc: httpx.HTTPStatusError | None = None,
    ) -> float:
        """Compute exponential backoff delay with jitter.

        - Base: 2^(attempt+1) seconds -> 2s, 4s, 8s
        - Jitter: random 0-1s added
        - On 429: respects Retry-After header if present
        """
        # Check for Retry-After header on 429
        if exc is not None and exc.response.status_code == 429:
            retry_after = exc.response.headers.get("retry-after")
            if retry_after:
                try:
                    server_wait = float(retry_after)
                    # Add small jitter to server-specified wait
                    return server_wait + random.uniform(0.1, 0.5)
                except (ValueError, TypeError):
                    pass

        # Exponential backoff: 2s, 4s, 8s + jitter 0-1s
        delay = self.BASE_RETRY_DELAY * (2 ** attempt)
        jitter = random.uniform(0.0, 1.0)
        return delay + jitter

    # ------------------------------------------------------------------
    # Provider-specific call dispatchers
    # ------------------------------------------------------------------

    async def _call(
        self, prompt: str, system: str, max_tokens: int
    ) -> LLMResponse:
        """Dispatch to the correct provider handler and record cost via FinOps."""
        provider = self.config.provider
        if provider == Provider.ANTHROPIC:
            response = await self._call_anthropic(prompt, system, max_tokens)
        elif provider == Provider.OPENAI:
            response = await self._call_openai(prompt, system, max_tokens)
        elif provider == Provider.GOOGLE:
            response = await self._call_google(prompt, system, max_tokens)
        elif provider == Provider.PERPLEXITY:
            response = await self._call_perplexity(prompt, system, max_tokens)
        elif provider == Provider.GROQ:
            response = await self._call_groq(prompt, system, max_tokens)
        else:
            raise ValueError(f"Provedor desconhecido: {provider}")

        # FinOps: record cost from every LLM call
        try:
            finops = get_finops()
            finops.record_cost(
                task_id=f"_llmclient_{self.config.name}",
                provider_or_llm=self.config.name,
                tokens_in=response.tokens_input,
                tokens_out=response.tokens_output,
                cost=response.cost,
            )
        except Exception:
            # FinOps recording should never break the LLM call
            logger.debug("FinOps recording failed for %s call", self.config.name, exc_info=True)

        return response

    # ------------------------------------------------------------------
    # Anthropic (Claude)
    # ------------------------------------------------------------------

    async def _call_anthropic(
        self, prompt: str, system: str, max_tokens: int
    ) -> LLMResponse:
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.config.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body: dict = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        text = data["content"][0]["text"]
        usage = data.get("usage", {})
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        cost = (
            tokens_in / 1000 * self.config.cost_per_1k_input
            + tokens_out / 1000 * self.config.cost_per_1k_output
        )

        return LLMResponse(
            text=text,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost=cost,
            model=self.config.model,
            provider=self.config.provider.value,
        )

    # ------------------------------------------------------------------
    # OpenAI (GPT-4o)
    # ------------------------------------------------------------------

    async def _call_openai(
        self, prompt: str, system: str, max_tokens: int
    ) -> LLMResponse:
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key or ''}",
            "Content-Type": "application/json",
        }
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)
        cost = (
            tokens_in / 1000 * self.config.cost_per_1k_input
            + tokens_out / 1000 * self.config.cost_per_1k_output
        )

        return LLMResponse(
            text=text,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost=cost,
            model=self.config.model,
            provider=self.config.provider.value,
        )

    # ------------------------------------------------------------------
    # Google (Gemini)
    # ------------------------------------------------------------------

    async def _call_google(
        self, prompt: str, system: str, max_tokens: int
    ) -> LLMResponse:
        model = self.config.model
        api_key = self.config.api_key or ""
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            f":generateContent?key={api_key}"
        )
        headers = {"Content-Type": "application/json"}

        contents: list[dict] = []
        if system:
            contents.append({"role": "user", "parts": [{"text": system}]})
            contents.append({"role": "model", "parts": [{"text": "Entendido."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        body = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        tokens_in = usage.get("promptTokenCount", 0)
        tokens_out = usage.get("candidatesTokenCount", 0)
        cost = (
            tokens_in / 1000 * self.config.cost_per_1k_input
            + tokens_out / 1000 * self.config.cost_per_1k_output
        )

        return LLMResponse(
            text=text,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost=cost,
            model=self.config.model,
            provider=self.config.provider.value,
        )

    # ------------------------------------------------------------------
    # Groq (Llama 3.3 70B — OpenAI-compatible, ultra-fast inference)
    # ------------------------------------------------------------------

    async def _call_groq(
        self, prompt: str, system: str, max_tokens: int
    ) -> LLMResponse:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key or ''}",
            "Content-Type": "application/json",
        }
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)
        cost = (
            tokens_in / 1000 * self.config.cost_per_1k_input
            + tokens_out / 1000 * self.config.cost_per_1k_output
        )

        return LLMResponse(
            text=text,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost=cost,
            model=self.config.model,
            provider=self.config.provider.value,
        )

    # ------------------------------------------------------------------
    # Perplexity (Sonar — OpenAI-compatible)
    # ------------------------------------------------------------------

    async def _call_perplexity(
        self, prompt: str, system: str, max_tokens: int
    ) -> LLMResponse:
        url = "https://api.perplexity.ai/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key or ''}",
            "Content-Type": "application/json",
        }
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        tokens_in = usage.get("prompt_tokens", 0)
        tokens_out = usage.get("completion_tokens", 0)
        cost = (
            tokens_in / 1000 * self.config.cost_per_1k_input
            + tokens_out / 1000 * self.config.cost_per_1k_output
        )

        return LLMResponse(
            text=text,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost=cost,
            model=self.config.model,
            provider=self.config.provider.value,
        )

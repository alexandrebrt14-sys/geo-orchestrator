"""Unified async HTTP client for all 4 LLM providers.

Handles API format differences, retries, timeouts, and cost calculation.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from .config import LLMConfig, Provider
from .models import LLMResponse


class LLMClient:
    """Async client that can query any of the 4 supported LLM providers."""

    TIMEOUT = 60.0
    RETRY_DELAY = 3.0
    MAX_RETRIES = 1

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    async def query(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4000,
    ) -> LLMResponse:
        """Send a prompt to the configured LLM and return a unified response.

        Retries once on 429 (rate-limit) or 500 (server error).
        """
        last_error: Exception | None = None

        for attempt in range(1 + self.MAX_RETRIES):
            try:
                return await self._call(prompt, system, max_tokens)
            except (httpx.HTTPStatusError, httpx.TimeoutException) as exc:
                last_error = exc
                retryable = False
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 500):
                    retryable = True
                if isinstance(exc, httpx.TimeoutException):
                    retryable = True
                if retryable and attempt < self.MAX_RETRIES:
                    await asyncio.sleep(self.RETRY_DELAY)
                    continue
                raise

        # Should never reach here, but satisfy type checker
        raise last_error  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Provider-specific call dispatchers
    # ------------------------------------------------------------------

    async def _call(
        self, prompt: str, system: str, max_tokens: int
    ) -> LLMResponse:
        """Dispatch to the correct provider handler."""
        provider = self.config.provider
        if provider == Provider.ANTHROPIC:
            return await self._call_anthropic(prompt, system, max_tokens)
        if provider == Provider.OPENAI:
            return await self._call_openai(prompt, system, max_tokens)
        if provider == Provider.GOOGLE:
            return await self._call_google(prompt, system, max_tokens)
        if provider == Provider.PERPLEXITY:
            return await self._call_perplexity(prompt, system, max_tokens)
        raise ValueError(f"Provedor desconhecido: {provider}")

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

        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
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

        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
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

        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
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

        async with httpx.AsyncClient(timeout=self.TIMEOUT) as client:
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

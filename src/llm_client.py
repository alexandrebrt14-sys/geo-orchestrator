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

from .circuit_breaker import (
    CircuitBreakerError,
    circuit_breaker_registry,
)
from .config import LLMConfig, Provider
from .connection_pool import ConnectionPool
from .finops import BudgetExceededError, get_finops
from .models import LLMResponse
from .rate_limiter import RateLimiter
from .tracer import TraceManager

logger = logging.getLogger(__name__)


# Provider-level circuit breaker config. Threshold/timeouts pequenos: o
# objetivo nao e "isolar bug do provider" e sim "parar de queimar 50s/task
# enquanto o provider esta em outage sustentado". 3 falhas seguidas em
# qualquer task abrem o circuito por 90s; tasks subsequentes na chain pulam
# o provider em 0ms ate o circuito tentar half-open.
_BREAKER_FAILURE_THRESHOLD = 3
_BREAKER_SUCCESS_THRESHOLD = 1
_BREAKER_TIMEOUT_SECONDS = 90.0


def get_provider_breaker(provider: Provider):
    """Return the singleton circuit breaker for a provider."""
    return circuit_breaker_registry.get_or_create(
        name=f"provider:{provider.value}",
        failure_threshold=_BREAKER_FAILURE_THRESHOLD,
        success_threshold=_BREAKER_SUCCESS_THRESHOLD,
        timeout=_BREAKER_TIMEOUT_SECONDS,
        expected_exception=Exception,
    )


# Pricing real por modelo Gemini. Necessario porque o fallback intra-provider
# Pro -> Flash usa um modelo diferente do que esta em LLMConfig; sem este map
# o FinOps cobraria preco de Pro mesmo quando a chamada caiu para Flash.
# Valores em USD por 1k tokens (input, output).
_GEMINI_PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-pro":   (0.00125, 0.005),
    "gemini-2.5-flash": (0.00030, 0.0025),
}


def _gemini_pricing_for(
    model: str, default_in: float, default_out: float,
) -> tuple[float, float]:
    """Retorna (input_price, output_price) por 1k tokens para o modelo dado.
    Se o modelo nao estiver mapeado, devolve o pricing do LLMConfig original."""
    return _GEMINI_PRICING.get(model, (default_in, default_out))


class LLMClient:
    """Async client that can query any of the 4 supported LLM providers.

    Supports per-task timeout overrides and connection pooling.
    """

    # 2026-04-14: timeouts e retries mais generosos para demandas profundas.
    # 2026-05-02: separamos retry por classe de erro. 503/UNAVAILABLE em
    # outage sustentado nao melhora em 14s — esperar e desperdicio. 429
    # mantem backoff longo respeitando Retry-After.
    TIMEOUT = 180.0
    MAX_RETRIES = 3
    BASE_RETRY_DELAY = 2.0  # 429: exponential 2s, 4s, 8s

    # Backoff curto para 5xx/timeout — 1 retry rapido e cair pro fallback.
    # Outage sustentado nao melhora esperando; melhor liberar a chain.
    UNAVAILABLE_MAX_RETRIES = 1
    UNAVAILABLE_RETRY_DELAY = 1.0

    def __init__(self, config: LLMConfig, timeout_override: float | None = None) -> None:
        self.config = config
        self._timeout = timeout_override or self.TIMEOUT
        self._rate_limiter = RateLimiter.get_instance()

    async def query(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 16000,
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
        breaker = get_provider_breaker(self.config.provider)

        # Circuit breaker: provider em OPEN -> raise imediato sem queimar
        # rate limiter, conexao ou ate retry interno. Pipeline cai pro
        # proximo da fallback chain em ~0ms em vez de ~50s.
        if breaker.state.value == "OPEN":
            raise CircuitBreakerError(
                f"Provider {provider_name} circuit OPEN — skipping to fallback. "
                f"Stats: {breaker.stats}"
            )

        # Limite total de tentativas e calculado por classe de erro a cada
        # attempt; cap absoluto = MAX_RETRIES (compatibilidade com codigo
        # legado que assume <=4 tentativas).
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
                # Sucesso: registra no breaker (fecha HALF_OPEN -> CLOSED)
                breaker._on_success()
                return response

            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code

                # 4xx (exceto 429) NAO sao falha de provider — registrar como
                # falha do breaker mascararia bugs do nosso lado (auth, payload).
                # Apenas 429/5xx contam para o circuit breaker.
                if status in (429, 500, 502, 503, 504):
                    breaker._on_failure()

                # Finish the LLM span with error
                if 'llm_span' in dir():
                    tracer.finish_span(llm_span, status="error", http_status=status)

                # Backoff por classe:
                # - 429: backoff exponencial classico, respeita Retry-After
                # - 5xx (500/502/503/504): 1 retry curto (1s) e cai pro fallback
                # - 4xx (auth/bad request): nao retentar
                if status == 429 and attempt < self.MAX_RETRIES:
                    retry_budget = self.MAX_RETRIES
                    wait_time = self._compute_backoff(attempt, exc)
                elif status in (500, 502, 503, 504) and attempt < self.UNAVAILABLE_MAX_RETRIES:
                    retry_budget = self.UNAVAILABLE_MAX_RETRIES
                    wait_time = self.UNAVAILABLE_RETRY_DELAY + random.uniform(0.0, 0.5)
                else:
                    raise

                # Retry span
                retry_span = tracer.start_span(
                    f"llm.retry.{provider_name}",
                    provider=provider_name,
                    attempt=attempt + 1,
                    http_status=status,
                    wait_seconds=round(wait_time, 2),
                    is_rate_limit=status == 429,
                    is_unavailable=status in (500, 502, 503, 504),
                )
                logger.warning(
                    "Retry %d/%d for %s (HTTP %d): waiting %.1fs",
                    attempt + 1,
                    retry_budget,
                    self.config.provider.value,
                    status,
                    wait_time,
                )
                await asyncio.sleep(wait_time)
                tracer.finish_span(retry_span, status="ok")
                # Re-checa circuit antes de continuar — outras tasks paralelas
                # podem ter aberto o circuito enquanto esperavamos.
                if breaker.state.value == "OPEN":
                    raise CircuitBreakerError(
                        f"Provider {provider_name} tripped circuit during retry — "
                        f"abandoning attempts."
                    )
                continue

            except httpx.TimeoutException as exc:
                last_error = exc
                # Timeout conta como falha de provider (provider esta lento
                # demais ou indisponivel) — alimenta o breaker.
                breaker._on_failure()

                # Finish the LLM span with error
                if 'llm_span' in dir():
                    tracer.finish_span(llm_span, status="error", error="timeout")

                # Timeout: 1 retry curto e desiste — consistente com 5xx.
                if attempt < self.UNAVAILABLE_MAX_RETRIES:
                    wait_time = self.UNAVAILABLE_RETRY_DELAY + random.uniform(0.0, 0.5)

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
                        self.UNAVAILABLE_MAX_RETRIES,
                        self.config.provider.value,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    tracer.finish_span(retry_span, status="ok")
                    if breaker.state.value == "OPEN":
                        raise CircuitBreakerError(
                            f"Provider {provider_name} tripped circuit during timeout retry."
                        )
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
        elif provider == Provider.XAI:
            response = await self._call_xai(prompt, system, max_tokens)
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

    # Limiar minimo de chars do system prompt para ativar prompt caching.
    # Anthropic exige >=1024 tokens cacheaveis (Sonnet/Opus) — em chars,
    # 4000 e uma estimativa conservadora (4 chars/token). Abaixo disso,
    # o cache nem e criado pelo backend, e o overhead de billing do create
    # nao compensa.
    _CACHE_MIN_CHARS = 4000

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
            # Prompt caching: para system prompts grandes, marca cache_control
            # ephemeral. Hits subsequentes (mesmo system, ate 5 min) cobram
            # 0.10x do preco normal de input tokens — economia de 90% no
            # bloco cacheado. Create cobra 1.25x apenas na 1a vez.
            if len(system) >= self._CACHE_MIN_CHARS:
                body["system"] = [
                    {
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            else:
                body["system"] = system

        pool = ConnectionPool.get_instance()
        client = await pool.get_client(Provider.ANTHROPIC, timeout=self._timeout)
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

        text = data["content"][0]["text"]
        usage = data.get("usage", {})
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        # Cache accounting: a Anthropic retorna 2 campos extras quando
        # caching esta ativo. Esses tokens NAO entram em input_tokens.
        cache_create = usage.get("cache_creation_input_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0

        # Cost: input normal + output normal + (create * 1.25) + (read * 0.10)
        # https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching#pricing
        in_price = self.config.cost_per_1k_input / 1000.0
        out_price = self.config.cost_per_1k_output / 1000.0
        cost = (
            tokens_in * in_price
            + tokens_out * out_price
            + cache_create * in_price * 1.25
            + cache_read * in_price * 0.10
        )

        if cache_create or cache_read:
            logger.info(
                "anthropic cache: create=%d read=%d in=%d out=%d cost=$%.5f",
                cache_create, cache_read, tokens_in, tokens_out, cost,
            )

        return LLMResponse(
            text=text,
            tokens_input=tokens_in + cache_create + cache_read,
            tokens_output=tokens_out,
            cost=cost,
            model=self.config.model,
            provider=self.config.provider.value,
        )

    # ------------------------------------------------------------------
    # OpenAI (GPT-5.5+ / GPT-4o)
    # 2026-05-17: gpt-5+ models requerem max_completion_tokens em vez de
    # max_tokens. Detectamos pelo prefix do model id e ajustamos o body.
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

        # gpt-5 family + o1/o3/o4 series usam max_completion_tokens.
        # gpt-4* legacy mantem max_tokens.
        model_id = self.config.model.lower()
        uses_completion_tokens = (
            model_id.startswith("gpt-5") or
            model_id.startswith("o1") or
            model_id.startswith("o3") or
            model_id.startswith("o4")
        )
        token_key = "max_completion_tokens" if uses_completion_tokens else "max_tokens"

        body: dict = {
            "model": self.config.model,
            token_key: max_tokens,
            "messages": messages,
        }

        pool = ConnectionPool.get_instance()
        client = await pool.get_client(Provider.OPENAI, timeout=self._timeout)
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

    # 2026-05-02 v3 — Fallback intra-provider para outage 503 sustentado.
    # Diagnostico do dia: gemini-2.5-pro com 60% taxa de 503 ("high demand")
    # em probe direto na API; gemini-2.5-flash 100% saudavel mesma chave.
    # Em vez de propagar 503 e queimar slot do circuit breaker (que abre
    # google inteiro, incluindo flash saudavel), tenta-se imediatamente o
    # modelo de fallback dentro do mesmo provider antes de escalar erro.
    GEMINI_INTRA_FALLBACK: dict[str, str] = {
        "gemini-2.5-pro": "gemini-2.5-flash",
    }

    async def _call_google(
        self, prompt: str, system: str, max_tokens: int
    ) -> LLMResponse:
        primary_model = self.config.model
        try:
            return await self._call_google_model(
                primary_model, prompt, system, max_tokens,
            )
        except httpx.HTTPStatusError as exc:
            fallback_model = self.GEMINI_INTRA_FALLBACK.get(primary_model)
            if exc.response.status_code != 503 or fallback_model is None:
                raise
            logger.warning(
                "Gemini intra-provider fallback: %s -> %s (HTTP 503 high demand)",
                primary_model, fallback_model,
            )
            return await self._call_google_model(
                fallback_model, prompt, system, max_tokens,
                fallback_from=primary_model,
            )

    async def _call_google_model(
        self,
        model: str,
        prompt: str,
        system: str,
        max_tokens: int,
        fallback_from: str | None = None,
    ) -> LLMResponse:
        api_key = self.config.api_key or ""
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
            f":generateContent"
        )
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }

        contents: list[dict] = []
        if system:
            contents.append({"role": "user", "parts": [{"text": system}]})
            contents.append({"role": "model", "parts": [{"text": "Entendido."}]})
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        body = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens},
        }

        pool = ConnectionPool.get_instance()
        client = await pool.get_client(Provider.GOOGLE, timeout=self._timeout)
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

        # Parser robusto: Gemini pode retornar candidates sem parts em
        # finishReason=MAX_TOKENS / SAFETY / RECITATION. Fallback gracioso.
        try:
            candidates = data.get("candidates") or []
            if not candidates:
                raise ValueError(f"Gemini sem candidates. finishReason={data.get('promptFeedback')}")
            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            if not parts:
                finish = candidates[0].get("finishReason", "UNKNOWN")
                raise ValueError(f"Gemini sem parts (finishReason={finish}, schema={list(candidates[0].keys())})")
            text = parts[0].get("text", "")
            if not text:
                raise ValueError(f"Gemini retornou parts vazias: {parts[0]}")
        except (KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(f"Resposta Gemini com schema inesperado: {exc}") from exc
        usage = data.get("usageMetadata", {})
        tokens_in = usage.get("promptTokenCount", 0)
        tokens_out = usage.get("candidatesTokenCount", 0)

        # Quando o fallback intra-provider acionou, aplicar o pricing real
        # do modelo usado (Flash e ~5x mais barato que Pro). Sem isso, FinOps
        # superestima custo do fallback.
        in_price, out_price = _gemini_pricing_for(
            model, self.config.cost_per_1k_input, self.config.cost_per_1k_output,
        )
        cost = tokens_in / 1000 * in_price + tokens_out / 1000 * out_price

        return LLMResponse(
            text=text,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            cost=cost,
            model=model,
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

        pool = ConnectionPool.get_instance()
        client = await pool.get_client(Provider.GROQ, timeout=self._timeout)
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
    # xAI Grok (4.3 / 4.20-* — OpenAI-compatible)
    # 2026-05-17 — 6o provider. Diferenca chave vs Groq (com Q):
    # xAI tem modelos proprios (grok-4.3) com busca live em X via
    # search_parameters; Groq Inc serve open-source (Llama 3.3) ultra-rapido.
    # ------------------------------------------------------------------

    async def _call_xai(
        self, prompt: str, system: str, max_tokens: int
    ) -> LLMResponse:
        url = "https://api.x.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key or ''}",
            "Content-Type": "application/json",
        }
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict = {
            "model": self.config.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        # Diferencial xAI: search_parameters habilita busca live em X/Twitter
        # quando o LLMConfig pede capability realtime_search/live_x_data.
        # Default off para evitar quota oculta; ativar via env por LLMConfig.
        if any(s in self.config.strengths for s in ("realtime_search", "live_x_data", "live_search_quick")):
            body["search_parameters"] = {"mode": "auto"}

        pool = ConnectionPool.get_instance()
        client = await pool.get_client(Provider.XAI, timeout=self._timeout)
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

        pool = ConnectionPool.get_instance()
        client = await pool.get_client(Provider.PERPLEXITY, timeout=self._timeout)
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

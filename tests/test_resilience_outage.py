"""Testes de resiliencia para outage sustentado de provider (2026-05-02).

Cenario que motivou: Gemini 2.5 Pro com 503 sustentado de Google + rebalance
do dia tinha promovido Gemini como primary em 5 task types. Cada task em
paralelo queimava ~50s (rate limiter + 3 retries com backoff 2/4/8s) antes
de cair no fallback. Em uma wave de 12 tasks, isso era O(N) overhead.

Esta suite valida que apos integrar circuit_breaker no LLMClient:
1. Apos 3 falhas seguidas de provider, circuit abre
2. Tasks subsequentes raise CircuitBreakerError em ~0ms (skipped)
3. Pipeline conclui usando fallback chain
4. Tempo total da wave nao escala linearmente com o numero de tasks
   (porque so as 3 primeiras pagam o custo do outage)
5. Backoff de 503 e curto (1 retry de ~1s), nao mais 2/4/8s

Estrategia de mock: monkeypatch em LLMClient._call para simular HTTP 503
sustentado em providers especificos.
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx
import pytest

# Garante chaves fake antes de importar src.* (router checa cfg.available)
for env_var in [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_AI_API_KEY",
    "PERPLEXITY_API_KEY", "GROQ_API_KEY",
]:
    os.environ.setdefault(env_var, "test-key-not-real")


@pytest.fixture(autouse=True)
def reset_breaker_registry():
    """Cada teste comeca com circuit breakers limpos."""
    from src.circuit_breaker import circuit_breaker_registry
    circuit_breaker_registry.reset_all()
    yield
    circuit_breaker_registry.reset_all()


def _make_503_response(provider_name: str) -> httpx.HTTPStatusError:
    """Constroi um HTTPStatusError(503) realistico."""
    request = httpx.Request("POST", f"https://{provider_name}.example/api")
    response = httpx.Response(
        status_code=503,
        request=request,
        text=f"<html>503 Service Unavailable from {provider_name}</html>",
    )
    return httpx.HTTPStatusError(
        f"Server error '503 Service Unavailable' for url '{request.url}'",
        request=request,
        response=response,
    )


def _make_call_func(failing_providers: set[str]):
    """Retorna fake _call que levanta 503 para providers em failing_providers,
    sucesso para os demais. Conta calls por provider em call_log."""
    from src.models import LLMResponse

    call_log: list[dict] = []
    timestamps: dict[str, list[float]] = {}

    async def fake_call(self_client, prompt, system, max_tokens):
        provider = self_client.config.provider.value
        timestamps.setdefault(provider, []).append(time.perf_counter())
        call_log.append({"provider": provider, "model": self_client.config.model})

        if provider in failing_providers:
            raise _make_503_response(provider)

        # Simula chamada bem-sucedida
        await asyncio.sleep(0.001)
        return LLMResponse(
            text=f"[ok via {provider}] " + ("conteudo " * 30),
            tokens_input=100,
            tokens_output=200,
            cost=0.001,
            model=self_client.config.model,
            provider=provider,
        )

    return fake_call, call_log, timestamps


class TestCircuitBreakerOnSustainedOutage:
    """Quando um provider esta em outage sustentado, o circuit breaker
    deve abrir apos 3 falhas e poupar tasks subsequentes."""

    def test_breaker_opens_after_threshold_failures(self, monkeypatch):
        """Falhas repetidas em 'google' devem abrir o circuito.

        Cada query() pode acumular ate 2 falhas no breaker (1 attempt + 1
        retry curto em 503). Apos ~3 falhas, o breaker abre. A query() que
        cruza o threshold pode levantar CircuitBreakerError (durante retry
        a re-checagem ja detecta OPEN); chamadas posteriores tambem.
        """
        from src import llm_client
        from src.circuit_breaker import (
            CircuitBreakerError,
            circuit_breaker_registry,
        )

        fake_call, call_log, _ = _make_call_func(failing_providers={"google"})
        monkeypatch.setattr(llm_client.LLMClient, "_call", fake_call)

        from src.config import LLM_CONFIGS
        from src.llm_client import LLMClient

        cfg = LLM_CONFIGS["gemini"]
        client = LLMClient(cfg)

        # Repete ate o breaker abrir ou ate o teto de tentativas
        for _ in range(5):
            try:
                asyncio.run(client.query("test prompt"))
            except (httpx.HTTPStatusError, CircuitBreakerError):
                pass
            breaker = circuit_breaker_registry.get_or_create(name="provider:google")
            if breaker.state.value == "OPEN":
                break

        breaker = circuit_breaker_registry.get_or_create(name="provider:google")
        assert breaker.state.value == "OPEN", (
            f"Esperado OPEN apos falhas seguidas, got {breaker.state.value}. "
            f"Stats: {breaker.stats}"
        )

    def test_subsequent_call_short_circuits_immediately(self, monkeypatch):
        """Quando o circuit esta OPEN, query() levanta CircuitBreakerError
        em <100ms sem nem chegar ao _call (sem retry, sem backoff)."""
        from src import llm_client
        from src.circuit_breaker import (
            CircuitBreakerError,
            circuit_breaker_registry,
        )

        fake_call, call_log, timestamps = _make_call_func(failing_providers={"google"})
        monkeypatch.setattr(llm_client.LLMClient, "_call", fake_call)

        from src.config import LLM_CONFIGS
        from src.llm_client import LLMClient

        cfg = LLM_CONFIGS["gemini"]
        client = LLMClient(cfg)

        # Aquece o breaker ate OPEN (aceita HTTPStatusError ou
        # CircuitBreakerError durante warm-up)
        for _ in range(5):
            try:
                asyncio.run(client.query("warm-up"))
            except (httpx.HTTPStatusError, CircuitBreakerError):
                pass
            if circuit_breaker_registry.get_or_create(name="provider:google").state.value == "OPEN":
                break

        assert circuit_breaker_registry.get_or_create(name="provider:google").state.value == "OPEN"

        calls_before = len(call_log)
        start = time.perf_counter()
        with pytest.raises(CircuitBreakerError):
            asyncio.run(client.query("post-trip call"))
        elapsed = time.perf_counter() - start

        # _call nao foi invocado
        assert len(call_log) == calls_before, (
            "Esperado short-circuit (sem invocar _call), mas _call foi chamado"
        )
        # Latencia minima
        assert elapsed < 0.5, f"Short-circuit demorou {elapsed:.2f}s (esperado <0.5s)"

    def test_unavailable_backoff_is_short(self, monkeypatch):
        """503 deve fazer no maximo 1 retry rapido, nao 3 com backoff exponencial."""
        from src import llm_client

        fake_call, call_log, _ = _make_call_func(failing_providers={"openai"})
        monkeypatch.setattr(llm_client.LLMClient, "_call", fake_call)

        from src.config import LLM_CONFIGS
        from src.llm_client import LLMClient

        cfg = LLM_CONFIGS["gpt4o"]
        client = LLMClient(cfg)

        start = time.perf_counter()
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(client.query("test"))
        elapsed = time.perf_counter() - start

        # 1 attempt + 1 retry com ~1s = <3s. Antes era 2+4+8 = ~14s.
        assert elapsed < 4.0, (
            f"Backoff de 503 demorou {elapsed:.2f}s — esperado <4s "
            f"(1 retry curto). Backoff longo desperdiça tempo em outage sustentado."
        )
        # Esperamos exatamente 2 calls (attempt + 1 retry)
        google_calls = [c for c in call_log if c["provider"] == "openai"]
        assert len(google_calls) == 2, (
            f"Esperado 2 calls (1 + 1 retry), got {len(google_calls)}"
        )

    def test_429_keeps_long_backoff(self, monkeypatch):
        """Rate-limit (429) deve manter o backoff mais longo do regime
        anterior — eh sinal legitimo de slow down, nao outage."""
        from src import llm_client
        from src.models import LLMResponse

        call_log: list[dict] = []

        async def fake_call_429(self_client, prompt, system, max_tokens):
            call_log.append({"provider": self_client.config.provider.value})
            request = httpx.Request("POST", "https://example/api")
            response = httpx.Response(status_code=429, request=request)
            raise httpx.HTTPStatusError(
                "429 Too Many Requests", request=request, response=response,
            )

        monkeypatch.setattr(llm_client.LLMClient, "_call", fake_call_429)

        from src.config import LLM_CONFIGS
        from src.llm_client import LLMClient

        # Reduzimos MAX_RETRIES via monkeypatch para o teste rodar rapido
        monkeypatch.setattr(LLMClient, "MAX_RETRIES", 2)
        monkeypatch.setattr(LLMClient, "BASE_RETRY_DELAY", 0.05)

        cfg = LLM_CONFIGS["claude_sonnet"]
        client = LLMClient(cfg)

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(client.query("test"))

        # 1 attempt + 2 retries = 3 calls (mantem comportamento de 429)
        assert len(call_log) == 3, (
            f"Esperado 3 calls (1+2 retries) em 429, got {len(call_log)}"
        )


class TestRouterHealthAware:
    """Router consulta circuit breaker + degradation antes de rotear."""

    def test_router_skips_open_provider(self, monkeypatch):
        """Quando provider:google esta OPEN, _is_usable('gemini') retorna False
        e fallback chain promove o proximo automaticamente."""
        from src.circuit_breaker import circuit_breaker_registry
        from src.router import Router

        # Forca circuit OPEN para google
        breaker = circuit_breaker_registry.get_or_create(name="provider:google")
        for _ in range(5):
            breaker._on_failure()
        assert breaker.state.value == "OPEN"

        router = Router()
        assert router._is_usable("gemini") is False, (
            "Gemini deveria estar bloqueado pelo circuit breaker do provider"
        )
        # Outro provider continua usavel
        assert router._is_usable("claude_sonnet") is True

    def test_router_degraded_provider_blocked_for_ttl(self):
        """mark_provider_degraded bloqueia provider pelo TTL configurado."""
        from src.router import Router

        router = Router()
        assert router._is_usable("gpt4o") is True
        router.mark_provider_degraded("openai", ttl_seconds=60.0)
        assert router._is_usable("gpt4o") is False
        # Limpa
        router.clear_degradation("openai")
        assert router._is_usable("gpt4o") is True

    def test_decomposition_primary_is_claude_sonnet(self):
        """Apos a redistribuicao, decomposition deve ter claude_sonnet como
        primary — wave 1 nao depende mais de Google."""
        from src.config import TASK_TYPES

        assert TASK_TYPES["decomposition"].primary == "claude_sonnet"
        assert TASK_TYPES["decomposition"].fallback == "gemini"

    def test_fallback_chains_have_cross_provider_top2(self):
        """Regra dura pos-2026-05-02: os 2 primeiros slots de cada chain
        sao de providers de FAMILIAS DIFERENTES."""
        from src.config import FALLBACK_CHAINS, LLM_CONFIGS

        for task_type, chain in FALLBACK_CHAINS.items():
            if len(chain) < 2:
                continue
            p0 = LLM_CONFIGS[chain[0]].provider.value
            p1 = LLM_CONFIGS[chain[1]].provider.value
            assert p0 != p1, (
                f"Chain '{task_type}' viola diversidade: "
                f"{chain[0]} ({p0}) e {chain[1]} ({p1}) sao do mesmo provider."
            )


class TestGeminiSplitProFlash:
    """Split gemini (Pro) / gemini_flash adicionado em 2026-05-02 v3 apos
    diagnostico de 503 sustentado no tier Pro do Google."""

    def test_gemini_flash_exists_in_config(self):
        """gemini_flash deve estar registrado em LLM_CONFIGS como provider
        google com modelo gemini-2.5-flash."""
        from src.config import LLM_CONFIGS, Provider

        assert "gemini_flash" in LLM_CONFIGS, (
            "gemini_flash precisa existir em LLM_CONFIGS — adicionado na v3"
        )
        cfg = LLM_CONFIGS["gemini_flash"]
        assert cfg.provider == Provider.GOOGLE
        # Permite override via env GEMINI_FLASH_MODEL; default e gemini-2.5-flash
        assert "flash" in cfg.model.lower(), (
            f"gemini_flash deve apontar para um modelo flash, got {cfg.model}"
        )

    def test_gemini_flash_cheaper_than_gemini_pro(self):
        """Flash deve ter custo de input/output menor que Pro — caso contrario
        nao faz sentido usar Flash em tasks medium-economy."""
        from src.config import LLM_CONFIGS

        pro = LLM_CONFIGS["gemini"]
        flash = LLM_CONFIGS["gemini_flash"]
        assert flash.cost_per_1k_input < pro.cost_per_1k_input
        assert flash.cost_per_1k_output < pro.cost_per_1k_output

    def test_economy_tasks_use_flash_not_pro(self):
        """Tasks medium-economy (analysis, data_processing) viraram primary
        gemini_flash. Tasks premium (code, architecture fallback,
        decomposition fallback, critical_review fallback) mantem Pro."""
        from src.config import TASK_TYPES

        # Migrados para Flash
        assert TASK_TYPES["analysis"].primary == "gemini_flash"
        assert TASK_TYPES["data_processing"].primary == "gemini_flash"
        assert TASK_TYPES["fact_check"].fallback == "gemini_flash"
        assert TASK_TYPES["classification"].fallback == "gemini_flash"
        assert TASK_TYPES["summarization"].fallback == "gemini_flash"
        assert TASK_TYPES["extraction"].fallback == "gemini_flash"
        assert TASK_TYPES["review"].fallback == "gemini_flash"

        # Mantem Pro (raciocinio premium)
        assert TASK_TYPES["code"].primary == "gemini"
        assert TASK_TYPES["architecture"].fallback == "gemini"
        assert TASK_TYPES["critical_review"].fallback == "gemini"
        assert TASK_TYPES["decomposition"].fallback == "gemini"
        assert TASK_TYPES["research"].fallback == "gemini"


class TestGeminiIntraProviderFallback:
    """Quando gemini-2.5-pro retorna 503, _call_google deve retentar
    imediatamente em gemini-2.5-flash em vez de propagar erro."""

    def test_pro_503_falls_back_to_flash(self, monkeypatch):
        """Mock: chamada para gemini-2.5-pro retorna 503, gemini-2.5-flash
        retorna 200. Esperado: query() devolve resposta do Flash sem erro."""
        from src import llm_client
        from src.config import LLM_CONFIGS, Provider
        from src.llm_client import LLMClient
        from src.models import LLMResponse

        # Forca config gemini para apontar para gemini-2.5-pro (caso env
        # GEMINI_MODEL tenha alterado o default). LLMConfig e frozen — usamos
        # dataclasses.replace() para criar uma instancia com Pro fixo.
        from dataclasses import replace
        pro_cfg = replace(LLM_CONFIGS["gemini"], model="gemini-2.5-pro")

        models_called: list[str] = []

        async def fake_call_google_model(self_client, model, prompt, system, max_tokens, fallback_from=None):
            models_called.append(model)
            if model == "gemini-2.5-pro":
                request = httpx.Request("POST", "https://gen-ai.example/api")
                response = httpx.Response(status_code=503, request=request, text="high demand")
                raise httpx.HTTPStatusError(
                    "503", request=request, response=response,
                )
            return LLMResponse(
                text="ok via flash",
                tokens_input=10,
                tokens_output=5,
                cost=0.0001,
                model=model,
                provider=Provider.GOOGLE.value,
            )

        monkeypatch.setattr(llm_client.LLMClient, "_call_google_model", fake_call_google_model)
        client = LLMClient(pro_cfg)
        resp = asyncio.run(client._call_google("test", "", 100))

        assert resp.text == "ok via flash"
        assert resp.model == "gemini-2.5-flash"
        assert models_called == ["gemini-2.5-pro", "gemini-2.5-flash"], (
            f"Esperado tentativa Pro depois Flash, got {models_called}"
        )

    def test_flash_503_propagates_no_loop(self, monkeypatch):
        """Quando o modelo ja e Flash, 503 deve propagar — nao ha fallback
        adicional pra evitar loop."""
        from src import llm_client
        from src.config import LLM_CONFIGS
        from src.llm_client import LLMClient
        from dataclasses import replace

        flash_cfg = replace(LLM_CONFIGS["gemini_flash"], model="gemini-2.5-flash")

        models_called: list[str] = []

        async def fake_call_google_model(self_client, model, prompt, system, max_tokens, fallback_from=None):
            models_called.append(model)
            request = httpx.Request("POST", "https://gen-ai.example/api")
            response = httpx.Response(status_code=503, request=request, text="high demand")
            raise httpx.HTTPStatusError("503", request=request, response=response)

        monkeypatch.setattr(llm_client.LLMClient, "_call_google_model", fake_call_google_model)
        client = LLMClient(flash_cfg)

        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(client._call_google("test", "", 100))

        # 1 unica tentativa (Flash). Sem fallback adicional.
        assert models_called == ["gemini-2.5-flash"], (
            f"Esperado 1 tentativa em Flash sem loop, got {models_called}"
        )

    def test_pro_non_503_does_not_fallback(self, monkeypatch):
        """4xx (auth, payload) NAO devem disparar o fallback intra-provider —
        sao bugs nossos, fallback mascara problema."""
        from src import llm_client
        from src.config import LLM_CONFIGS
        from src.llm_client import LLMClient
        from dataclasses import replace

        pro_cfg = replace(LLM_CONFIGS["gemini"], model="gemini-2.5-pro")

        models_called: list[str] = []

        async def fake_call_google_model(self_client, model, prompt, system, max_tokens, fallback_from=None):
            models_called.append(model)
            request = httpx.Request("POST", "https://gen-ai.example/api")
            response = httpx.Response(status_code=400, request=request, text="bad request")
            raise httpx.HTTPStatusError("400", request=request, response=response)

        monkeypatch.setattr(llm_client.LLMClient, "_call_google_model", fake_call_google_model)
        client = LLMClient(pro_cfg)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            asyncio.run(client._call_google("test", "", 100))

        assert exc_info.value.response.status_code == 400
        assert models_called == ["gemini-2.5-pro"], (
            f"Esperado nao-fallback em 4xx, got {models_called}"
        )

"""
Agente especializado em pesquisa usando Perplexity API.

Otimizado para buscar informações atualizadas com fontes verificáveis,
retornando dados estruturados em JSON.
"""

from __future__ import annotations

import json
import re
import logging
from typing import Any

from .base import BaseAgent, TaskType

logger = logging.getLogger(__name__)

RESEARCHER_SYSTEM_PROMPT = """You are a senior research analyst working for Brasil GEO.
Your task is to research topics thoroughly and return structured findings.

RULES:
1. Always cite your sources with full URLs.
2. Prioritize recent data (last 12 months).
3. Cross-reference claims across multiple sources.
4. Flag low-confidence findings explicitly.
5. Output MUST be in PT-BR (Brazilian Portuguese with full accents).
6. Technical terms may remain in English when appropriate.

OUTPUT FORMAT — Always respond with valid JSON:
{
  "findings": [
    {
      "topic": "string",
      "summary": "string (PT-BR)",
      "details": "string (PT-BR, detailed explanation)",
      "sources": ["url1", "url2"],
      "confidence": "high|medium|low"
    }
  ],
  "key_data": {
    "statistics": [],
    "trends": [],
    "competitors": [],
    "opportunities": []
  },
  "overall_confidence": "high|medium|low",
  "research_gaps": ["areas that need more investigation"]
}

If the topic requires multiple sub-researches, break them down in the findings array.
Never fabricate URLs — only include URLs you actually found during research."""


class ResearcherAgent(BaseAgent):
    """Agente de pesquisa usando Perplexity (sonar-pro ou sonar)."""

    def __init__(
        self,
        llm_client: Any,
        model_name: str = "sonar-pro",
        cost_per_1k_input: float = 0.003,
        cost_per_1k_output: float = 0.015,
    ):
        super().__init__(
            llm_client=llm_client,
            task_type=TaskType.RESEARCH,
            system_prompt=RESEARCHER_SYSTEM_PROMPT,
            model_name=model_name,
            cost_per_1k_input=cost_per_1k_input,
            cost_per_1k_output=cost_per_1k_output,
        )

    async def _call_llm(self, messages: list[dict]) -> dict:
        """Chama a API da Perplexity via httpx."""
        response = await self.llm_client.post(
            "https://api.perplexity.ai/chat/completions",
            json={
                "model": self.model_name,
                "messages": messages,
                "temperature": 0.1,
                "return_citations": True,
            },
        )
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})

        content = choice["message"]["content"]

        # Perplexity pode retornar citações inline [1], [2] etc.
        # Extrair URLs das citações se disponíveis
        citations = data.get("citations", [])
        if citations:
            content = self._inject_citation_urls(content, citations)

        return {
            "content": content,
            "tokens_input": usage.get("prompt_tokens", 0),
            "tokens_output": usage.get("completion_tokens", 0),
        }

    @staticmethod
    def _inject_citation_urls(content: str, citations: list[str]) -> str:
        """Substitui referências [1], [2] pelas URLs reais das citações."""
        for i, url in enumerate(citations, start=1):
            content = content.replace(f"[{i}]", f"[{i}]({url})")
        return content

    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        """Extrai e valida URLs de uma string."""
        url_pattern = re.compile(
            r'https?://[^\s\)\]\},\'"<>]+'
        )
        urls = url_pattern.findall(text)
        # Limpar caracteres trailing comuns
        cleaned = []
        for url in urls:
            url = url.rstrip(".,;:")
            if len(url) > 10:
                cleaned.append(url)
        return list(dict.fromkeys(cleaned))  # deduplica mantendo ordem

    def _post_process(self, raw_content: str) -> dict:
        """Tenta parsear JSON, com fallback para texto estruturado."""
        # Tentar extrair JSON do conteúdo
        try:
            # Procurar bloco JSON na resposta
            json_match = re.search(r'\{[\s\S]*\}', raw_content)
            if json_match:
                parsed = json.loads(json_match.group())
                # Validar estrutura mínima
                if "findings" in parsed:
                    # Extrair URLs adicionais do texto bruto
                    extra_urls = self._extract_urls(raw_content)
                    parsed.setdefault("extracted_urls", extra_urls)
                    return parsed
        except json.JSONDecodeError:
            pass

        # Fallback: retornar como texto estruturado
        logger.warning("Resposta do Perplexity não é JSON válido, usando fallback")
        urls = self._extract_urls(raw_content)
        return {
            "findings": [{
                "topic": "Pesquisa geral",
                "summary": raw_content[:500],
                "details": raw_content,
                "sources": urls[:10],
                "confidence": "medium",
            }],
            "key_data": {
                "statistics": [],
                "trends": [],
                "competitors": [],
                "opportunities": [],
            },
            "overall_confidence": "medium",
            "research_gaps": ["Resposta não estruturada — recomenda-se nova pesquisa"],
            "extracted_urls": urls,
        }

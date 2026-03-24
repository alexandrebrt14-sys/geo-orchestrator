"""
Agente especializado em análise de dados usando Gemini Flash.

Otimizado para processamento rápido de dados, sumarização,
classificação e operações em lote com saída estruturada.
"""

from __future__ import annotations

import json
import os
import re
import logging
from typing import Any

from .base import BaseAgent, TaskType

logger = logging.getLogger(__name__)

ANALYZER_SYSTEM_PROMPT = """You are a data analyst specializing in fast, structured analysis.
You work for Brasil GEO, processing data for GEO (Generative Engine Optimization) research.

CAPABILITIES:
1. Data summarization — condense large datasets into key insights
2. Classification — categorize items by topic, sentiment, relevance
3. Trend detection — identify patterns and anomalies
4. Comparison — benchmark data points against each other
5. Scoring — assign numeric scores based on defined criteria

RULES:
1. Always return valid JSON.
2. Be concise — prioritize signal over noise.
3. Include confidence scores (0.0 to 1.0) for each finding.
4. User-facing text in PT-BR with full accents.
5. When processing lists, maintain the original order.
6. For bulk operations, process all items — never skip.

OUTPUT FORMAT:
{
  "analysis_type": "summarization|classification|trend|comparison|scoring",
  "results": [
    {
      "item": "identifier",
      "result": "the analysis result (PT-BR)",
      "score": 0.85,
      "tags": ["tag1", "tag2"],
      "metadata": {}
    }
  ],
  "summary": "Overall summary in PT-BR",
  "statistics": {
    "total_items": 0,
    "processed": 0,
    "key_metric": 0
  },
  "recommendations": ["actionable recommendation 1", "recommendation 2"]
}

For trend analysis, add:
  "trends": [
    {
      "name": "trend name",
      "direction": "up|down|stable",
      "magnitude": 0.0,
      "evidence": "supporting data"
    }
  ]

Respond ONLY with the JSON object. No preamble, no explanation outside the JSON."""


class AnalyzerAgent(BaseAgent):
    """Agente de análise de dados usando Gemini Flash."""

    def __init__(
        self,
        llm_client: Any,
        model_name: str = "gemini-2.0-flash",
        cost_per_1k_input: float = 0.000075,
        cost_per_1k_output: float = 0.0003,
    ):
        super().__init__(
            llm_client=llm_client,
            task_type=TaskType.ANALYSIS,
            system_prompt=ANALYZER_SYSTEM_PROMPT,
            model_name=model_name,
            cost_per_1k_input=cost_per_1k_input,
            cost_per_1k_output=cost_per_1k_output,
        )

    async def _call_llm(self, messages: list[dict]) -> dict:
        """Chama a API do Google Gemini via httpx."""
        # Converter formato OpenAI-style para Gemini format
        system_instruction = ""
        gemini_contents = []

        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                gemini_contents.append({
                    "role": "user",
                    "parts": [{"text": msg["content"]}],
                })
            elif msg["role"] == "assistant":
                gemini_contents.append({
                    "role": "model",
                    "parts": [{"text": msg["content"]}],
                })

        payload: dict[str, Any] = {
            "contents": gemini_contents,
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096,
                "responseMimeType": "application/json",
            },
        }

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}],
            }

        import asyncio

        api_key = os.getenv("GOOGLE_AI_API_KEY", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"

        # Retry com backoff + fallback para modelos alternativos em caso de 429
        models_to_try = [self.model_name, "gemini-2.5-flash-lite"]
        max_retries = 2
        response = None

        for model in models_to_try:
            model_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            for attempt in range(max_retries):
                response = await self.llm_client.post(
                    model_url,
                    params={"key": api_key},
                    json=payload,
                )
                if response.status_code != 429:
                    if model != self.model_name:
                        logger.info(f"Gemini fallback: usando {model} (modelo primario com quota esgotada)")
                    break
                wait_time = (attempt + 1) * 5
                logger.warning(f"Gemini 429 em {model} (tentativa {attempt + 1}/{max_retries}), aguardando {wait_time}s...")
                await asyncio.sleep(wait_time)
            if response is not None and response.status_code != 429:
                break

        response.raise_for_status()
        data = response.json()

        # Extrair conteúdo da resposta Gemini
        candidates = data.get("candidates", [])
        if not candidates:
            raise ValueError("Gemini retornou resposta vazia")

        content = ""
        for part in candidates[0].get("content", {}).get("parts", []):
            content += part.get("text", "")

        usage = data.get("usageMetadata", {})
        return {
            "content": content,
            "tokens_input": usage.get("promptTokenCount", 0),
            "tokens_output": usage.get("candidatesTokenCount", 0),
        }

    def _post_process(self, raw_content: str) -> dict:
        """Parseia JSON da resposta do Gemini."""
        try:
            # Gemini com responseMimeType=application/json geralmente retorna JSON puro
            parsed = json.loads(raw_content)
            return parsed
        except json.JSONDecodeError:
            pass

        # Fallback: tentar extrair JSON de dentro da resposta
        try:
            json_match = re.search(r'\{[\s\S]*\}', raw_content)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

        # Último fallback
        logger.warning("Resposta do Gemini não é JSON válido, usando fallback")
        return {
            "analysis_type": "raw",
            "results": [{
                "item": "resposta_bruta",
                "result": raw_content,
                "score": 0.5,
                "tags": ["fallback"],
                "metadata": {},
            }],
            "summary": "Análise retornada em formato não estruturado",
            "statistics": {
                "total_items": 1,
                "processed": 1,
                "key_metric": 0,
            },
            "recommendations": ["Reexecutar análise com prompt mais específico"],
        }

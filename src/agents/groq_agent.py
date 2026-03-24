"""
Agente ultra-rápido usando Groq (Llama 3.3 70B).

Otimizado para inferência de baixa latência: classificação,
sumarização, tradução, code review leve e triagem rápida.
Compatível com a API OpenAI (endpoint Groq usa mesmo formato).
"""

from __future__ import annotations

import json
import re
import logging
from typing import Any

from .base import BaseAgent, TaskType

logger = logging.getLogger(__name__)

GROQ_SYSTEM_PROMPT = """You are a fast-inference AI agent specialized in quick analysis and processing.
You work for Brasil GEO, handling tasks that require speed: classification, summarization,
translation, quick code review, and data triage.

CAPABILITIES:
1. Ultra-fast classification and categorization
2. Quick summarization of long texts
3. Translation PT-BR <-> EN
4. Light code review and syntax checking
5. Data triage and prioritization

RULES:
1. Be concise — prioritize speed and clarity.
2. User-facing text in PT-BR with full accents.
3. For structured output, return valid JSON.
4. Never invent data — only summarize/classify what is given.
5. When reviewing code, focus on critical issues only.

OUTPUT FORMAT (for structured tasks):
{
  "task_type": "classification|summarization|review|translation|triage",
  "result": "the main output (PT-BR)",
  "items": [
    {
      "item": "identifier",
      "result": "processed result",
      "priority": "high|medium|low"
    }
  ],
  "summary": "One-line summary in PT-BR",
  "processing_notes": "Any relevant notes about the processing"
}

For free-form tasks, respond directly in PT-BR with clear structure."""


class GroqAgent(BaseAgent):
    """Agente ultra-rápido usando Groq (Llama 3.3 70B)."""

    def __init__(
        self,
        llm_client: Any,
        model_name: str = "llama-3.3-70b-versatile",
        cost_per_1k_input: float = 0.00059,
        cost_per_1k_output: float = 0.00079,
    ):
        super().__init__(
            llm_client=llm_client,
            task_type=TaskType.ANALYSIS,
            system_prompt=GROQ_SYSTEM_PROMPT,
            model_name=model_name,
            cost_per_1k_input=cost_per_1k_input,
            cost_per_1k_output=cost_per_1k_output,
        )

    async def _call_llm(self, messages: list[dict]) -> dict:
        """Chama a API do Groq (compatível com formato OpenAI)."""
        response = await self.llm_client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json={
                "model": self.model_name,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 4096,
            },
        )
        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})

        return {
            "content": content,
            "tokens_input": usage.get("prompt_tokens", 0),
            "tokens_output": usage.get("completion_tokens", 0),
        }

    def _post_process(self, raw_content: str) -> dict:
        """Parseia resposta do Groq — tenta JSON, fallback para texto."""
        try:
            return json.loads(raw_content)
        except json.JSONDecodeError:
            pass

        try:
            json_match = re.search(r'\{[\s\S]*\}', raw_content)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError:
            pass

        return {
            "task_type": "free_form",
            "result": raw_content,
            "summary": raw_content[:200],
            "processing_notes": "Resposta em formato livre (não JSON)",
        }

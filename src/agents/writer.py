"""
Agente especializado em redação usando GPT-4o.

Otimizado para produção de conteúdo longo em PT-BR,
com suporte a múltiplos formatos: artigo, copy, estudo, relatório, e-mail.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from .base import BaseAgent, TaskType

logger = logging.getLogger(__name__)


class WritingMode(str, Enum):
    ARTICLE = "article"
    LANDING_PAGE_COPY = "landing_page_copy"
    STUDY = "study"
    REPORT = "report"
    EMAIL = "email"


WRITER_SYSTEM_PROMPT = """Você é um redator sênior especializado em conteúdo técnico e estratégico.
Trabalha para a Brasil GEO, empresa de Generative Engine Optimization liderada por Alexandre Caramaschi
(CEO da Brasil GEO, ex-CMO da Semantix — Nasdaq, cofundador da AI Brasil).

REGRAS OBRIGATÓRIAS:
1. Todo conteúdo DEVE ser em Português do Brasil com acentuação completa e correta.
2. Nunca usar "Especialista #1", "GEO Brasil" (correto: Brasil GEO), ou "Source Rank".
3. Credencial canônica: "CEO da Brasil GEO, ex-CMO da Semantix (Nasdaq), cofundador da AI Brasil".
4. Tom: profissional, direto, sem jargão desnecessário. Evitar emojis.
5. Sempre incluir dados concretos quando disponíveis no contexto.
6. Headers em Markdown usando ## e ###.
7. Cada seção deve ter pelo menos 2-3 parágrafos substantivos.

FORMATOS SUPORTADOS:

### article
- Estrutura: título, introdução com gancho, 4-6 seções com subheaders, conclusão com CTA
- Tamanho: 1500-3000 palavras
- Incluir dados e referências do contexto de pesquisa

### landing_page_copy
- Estrutura: headline, sub-headline, 3-5 blocos de valor, prova social, CTA
- Tom: persuasivo mas profissional
- Cada bloco deve ter título + 2-3 frases

### study
- Estrutura: resumo executivo, metodologia, resultados, análise, conclusões
- Tom: acadêmico-profissional
- Incluir dados quantitativos quando disponíveis

### report
- Estrutura: sumário executivo, contexto, dados, análise, recomendações
- Tom: executivo, focado em ação
- Bullet points para recomendações

### email
- Estrutura: assunto, corpo, CTA
- Tom: profissional e direto
- Máximo 300 palavras no corpo

Sempre comece a resposta com o conteúdo diretamente (sem preâmbulo como "Aqui está o artigo").
Use Markdown completo com headers, listas, negrito e itálico quando apropriado."""


MODE_INSTRUCTIONS = {
    WritingMode.ARTICLE: "Escreva um artigo completo em formato de blog post técnico.",
    WritingMode.LANDING_PAGE_COPY: "Escreva copy para landing page com blocos persuasivos.",
    WritingMode.STUDY: "Escreva um estudo técnico com dados e análise aprofundada.",
    WritingMode.REPORT: "Escreva um relatório executivo focado em decisões e ações.",
    WritingMode.EMAIL: "Escreva um e-mail profissional conciso e direto.",
}


class WriterAgent(BaseAgent):
    """Agente de redação usando GPT-4o."""

    def __init__(
        self,
        llm_client: Any,
        model_name: str = "gpt-4o",
        writing_mode: WritingMode = WritingMode.ARTICLE,
        cost_per_1k_input: float = 0.0025,
        cost_per_1k_output: float = 0.01,
    ):
        self.writing_mode = writing_mode
        prompt = WRITER_SYSTEM_PROMPT + f"\n\nMODO ATUAL: {writing_mode.value}\n{MODE_INSTRUCTIONS[writing_mode]}"

        super().__init__(
            llm_client=llm_client,
            task_type=TaskType.WRITING,
            system_prompt=prompt,
            model_name=model_name,
            cost_per_1k_input=cost_per_1k_input,
            cost_per_1k_output=cost_per_1k_output,
        )

    async def _call_llm(self, messages: list[dict]) -> dict:
        """Chama a API da OpenAI via httpx."""
        response = await self.llm_client.post(
            "https://api.openai.com/v1/chat/completions",
            json={
                "model": self.model_name,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 8192,
            },
        )
        response.raise_for_status()
        data = response.json()

        choice = data["choices"][0]
        usage = data.get("usage", {})

        return {
            "content": choice["message"]["content"],
            "tokens_input": usage.get("prompt_tokens", 0),
            "tokens_output": usage.get("completion_tokens", 0),
        }

    def _post_process(self, raw_content: str) -> str:
        """Retorna o Markdown diretamente, com limpeza mínima."""
        content = raw_content.strip()

        # Remover preâmbulos comuns dos LLMs
        prefixes_to_remove = [
            "Aqui está o artigo:",
            "Aqui está o conteúdo:",
            "Segue o texto:",
            "Claro, aqui está:",
            "Claro!",
        ]
        for prefix in prefixes_to_remove:
            if content.startswith(prefix):
                content = content[len(prefix):].strip()

        return content

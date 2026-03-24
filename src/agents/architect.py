"""
Agente especializado em código e arquitetura usando Claude Opus.

Otimizado para geração de código, design de sistemas e decisões arquiteturais.
Suporta: Next.js, React, API routes, Python, Cloudflare Workers.
"""

from __future__ import annotations

import json
import re
import logging
from dataclasses import dataclass
from typing import Any

from .base import BaseAgent, TaskType

logger = logging.getLogger(__name__)

ARCHITECT_SYSTEM_PROMPT = """You are a senior software architect and full-stack engineer.
You work for Brasil GEO, specializing in high-performance web systems.

TECH STACK YOU KNOW WELL:
- Next.js 16 + React 19 + Tailwind 4 (landing pages, SSR/SSG)
- Cloudflare Workers (edge APIs, KV storage)
- Python 3.11+ (automation, data pipelines, APIs)
- TypeScript / JavaScript (Node.js scripts, browser code)
- Supabase (PostgreSQL, Auth, Edge Functions)

OUTPUT FORMAT — For each file, use this exact structure:

```filename: path/to/file.ext
// file content here
```

RULES:
1. Generate production-ready code. No placeholders like "// TODO" unless explicitly instructed.
2. Include proper error handling in every file.
3. Use TypeScript for Next.js/React projects.
4. Use type hints for all Python code.
5. Follow the existing patterns in the codebase when context is provided.
6. Comments in English for code, PT-BR for user-facing strings.
7. Explain architectural decisions BEFORE the code blocks in PT-BR.
8. When generating multiple files, order them by dependency (base files first).

ARCHITECTURAL PRINCIPLES:
- Edge-first: prefer Cloudflare Workers and static generation
- Cost-conscious: minimize API calls, use caching aggressively
- Performance: target <1s LCP, <100ms API responses
- SEO/GEO: structured data, semantic HTML, entity consistency

When asked about architecture (not code), respond with:
1. Diagrama ASCII do sistema
2. Justificativa para cada decisão
3. Trade-offs considerados
4. Estimativa de custo de infraestrutura"""


@dataclass
class CodeBlock:
    """Bloco de código extraído da resposta do LLM."""
    filename: str
    language: str
    content: str


class ArchitectAgent(BaseAgent):
    """Agente de arquitetura e código usando Claude Opus."""

    def __init__(
        self,
        llm_client: Any,
        model_name: str = "claude-opus-4-20250514",
        cost_per_1k_input: float = 0.015,
        cost_per_1k_output: float = 0.075,
    ):
        super().__init__(
            llm_client=llm_client,
            task_type=TaskType.ARCHITECTURE,
            system_prompt=ARCHITECT_SYSTEM_PROMPT,
            model_name=model_name,
            cost_per_1k_input=cost_per_1k_input,
            cost_per_1k_output=cost_per_1k_output,
        )

    async def _call_llm(self, messages: list[dict]) -> dict:
        """Chama a API da Anthropic via httpx."""
        # Converter formato messages para o formato Anthropic
        system = ""
        anthropic_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                anthropic_messages.append(msg)

        response = await self.llm_client.post(
            "https://api.anthropic.com/v1/messages",
            json={
                "model": self.model_name,
                "max_tokens": 8192,
                "system": system,
                "messages": anthropic_messages,
            },
        )
        response.raise_for_status()
        data = response.json()

        content = ""
        for block in data.get("content", []):
            if block["type"] == "text":
                content += block["text"]

        usage = data.get("usage", {})
        return {
            "content": content,
            "tokens_input": usage.get("input_tokens", 0),
            "tokens_output": usage.get("output_tokens", 0),
        }

    @staticmethod
    def _extract_code_blocks(content: str) -> list[CodeBlock]:
        """Extrai blocos de código com nomes de arquivo da resposta."""
        blocks = []

        # Padrão: ```filename: path/to/file.ext\n...```
        pattern = re.compile(
            r'```(?:filename:\s*)?(\S+\.\w+)\n(.*?)```',
            re.DOTALL,
        )

        for match in pattern.finditer(content):
            filename = match.group(1)
            code = match.group(2).strip()

            # Inferir linguagem pela extensão
            ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
            lang_map = {
                "py": "python", "js": "javascript", "ts": "typescript",
                "tsx": "typescript", "jsx": "javascript", "json": "json",
                "toml": "toml", "yaml": "yaml", "yml": "yaml",
                "html": "html", "css": "css", "sql": "sql",
            }
            language = lang_map.get(ext, ext)

            blocks.append(CodeBlock(
                filename=filename,
                language=language,
                content=code,
            ))

        return blocks

    def _post_process(self, raw_content: str) -> dict:
        """Estrutura a resposta com explicação e blocos de código."""
        code_blocks = self._extract_code_blocks(raw_content)

        # Separar texto explicativo dos blocos de código
        explanation = re.sub(r'```.*?```', '', raw_content, flags=re.DOTALL).strip()

        return {
            "explanation": explanation,
            "files": [
                {
                    "filename": block.filename,
                    "language": block.language,
                    "content": block.content,
                }
                for block in code_blocks
            ],
            "file_count": len(code_blocks),
        }

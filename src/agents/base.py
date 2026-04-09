"""
Base agent class for the geo-orchestrator multi-LLM system.

All specialized agents (researcher, writer, architect, analyzer)
inherit from BaseAgent and implement their own execute() logic.
"""

from __future__ import annotations

import time
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """Tipos de tarefa suportados pelo orquestrador."""
    RESEARCH = "research"
    WRITING = "writing"
    ARCHITECTURE = "architecture"
    ANALYSIS = "analysis"
    CODE_GENERATION = "code_generation"
    REVIEW = "review"
    DEPLOY = "deploy"
    DATA_PROCESSING = "data_processing"


@dataclass
class TaskResult:
    """Resultado padronizado de execução de uma tarefa por um agente."""
    task_id: str
    task_type: TaskType
    agent_name: str
    model_used: str
    success: bool
    output: Any = None
    error: str | None = None
    raw_response: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    metadata: dict = field(default_factory=dict)

    def to_context_string(self) -> str:
        """Formata o resultado para injeção como contexto em tarefas subsequentes."""
        if not self.success:
            return f"[TAREFA {self.task_id} FALHOU: {self.error}]"

        if isinstance(self.output, dict):
            formatted = json.dumps(self.output, ensure_ascii=False, indent=2)
        elif isinstance(self.output, str):
            formatted = self.output
        else:
            formatted = str(self.output)

        return (
            f"--- Resultado da tarefa {self.task_id} ({self.task_type.value}) ---\n"
            f"{formatted}\n"
            f"--- Fim do resultado {self.task_id} ---"
        )


def format_context_from_results(results: list[TaskResult]) -> str:
    """Monta o bloco de contexto a partir de resultados de tarefas anteriores."""
    if not results:
        return ""

    parts = ["## Contexto de tarefas anteriores\n"]
    for r in results:
        parts.append(r.to_context_string())
    return "\n\n".join(parts)


class BaseAgent(ABC):
    """
    Classe base para todos os agentes especializados.

    Parâmetros:
        llm_client: Cliente HTTP configurado para a API do LLM alvo.
        task_type: Tipo de tarefa que este agente processa.
        system_prompt: Prompt de sistema do agente.
        model_name: Identificador do modelo (ex: "gpt-4o", "claude-opus-4-6").
        cost_per_1k_input: Custo por 1K tokens de entrada (USD).
        cost_per_1k_output: Custo por 1K tokens de saída (USD).
    """

    def __init__(
        self,
        llm_client: Any,
        task_type: TaskType,
        system_prompt: str,
        model_name: str = "unknown",
        cost_per_1k_input: float = 0.0,
        cost_per_1k_output: float = 0.0,
    ):
        self.llm_client = llm_client
        self.task_type = task_type
        self.system_prompt = system_prompt
        self.model_name = model_name
        self.cost_per_1k_input = cost_per_1k_input
        self.cost_per_1k_output = cost_per_1k_output

    @property
    def agent_name(self) -> str:
        return self.__class__.__name__

    def _calculate_cost(self, tokens_in: int, tokens_out: int) -> float:
        """Calcula custo estimado em USD."""
        cost_in = (tokens_in / 1000) * self.cost_per_1k_input
        cost_out = (tokens_out / 1000) * self.cost_per_1k_output
        return round(cost_in + cost_out, 6)

    def _build_messages(self, task: str, context: str = "") -> list[dict]:
        """Monta a lista de mensagens para a chamada à API."""
        messages = [{"role": "system", "content": self.system_prompt}]

        if context:
            messages.append({
                "role": "user",
                "content": f"Contexto disponível:\n\n{context}",
            })

        messages.append({"role": "user", "content": task})
        return messages

    @abstractmethod
    async def _call_llm(self, messages: list[dict]) -> dict:
        """
        Faz a chamada ao LLM e retorna um dicionário com:
        - content: str (resposta)
        - tokens_input: int
        - tokens_output: int
        """
        ...

    @abstractmethod
    def _post_process(self, raw_content: str) -> Any:
        """Processa a resposta bruta do LLM para o formato de saída esperado."""
        ...

    async def execute(self, task: str, context: str = "", task_id: str = "0") -> TaskResult:
        """
        Executa uma tarefa completa: monta prompt, chama LLM, pós-processa.

        Args:
            task: Descrição da tarefa a executar.
            context: Contexto de tarefas anteriores (já formatado).
            task_id: Identificador único da tarefa.

        Returns:
            TaskResult com o resultado da execução.
        """
        start = time.monotonic()

        try:
            messages = self._build_messages(task, context)
            logger.info(
                "Agente %s executando tarefa %s com modelo %s",
                self.agent_name, task_id, self.model_name,
            )

            llm_response = await self._call_llm(messages)

            raw_content = llm_response["content"]
            tokens_in = llm_response.get("tokens_input", 0)
            tokens_out = llm_response.get("tokens_output", 0)

            output = self._post_process(raw_content)
            cost = self._calculate_cost(tokens_in, tokens_out)
            duration = time.monotonic() - start

            return TaskResult(
                task_id=task_id,
                task_type=self.task_type,
                agent_name=self.agent_name,
                model_used=self.model_name,
                success=True,
                output=output,
                raw_response=raw_content,
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                cost_usd=cost,
                duration_seconds=round(duration, 2),
            )

        except Exception as exc:
            duration = time.monotonic() - start
            logger.error(
                "Agente %s falhou na tarefa %s: %s",
                self.agent_name, task_id, exc,
            )
            return TaskResult(
                task_id=task_id,
                task_type=self.task_type,
                agent_name=self.agent_name,
                model_used=self.model_name,
                success=False,
                error=str(exc),
                duration_seconds=round(duration, 2),
            )

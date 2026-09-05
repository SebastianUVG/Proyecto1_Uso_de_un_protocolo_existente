"""Provider-independent conversation and tool-use models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, Sequence, TypeAlias


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


AssistantBlock: TypeAlias = TextBlock | ToolUseBlock
MessageBlock: TypeAlias = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    role: str
    content: str | tuple[MessageBlock, ...]


@dataclass(frozen=True, slots=True)
class LLMTool:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LLMResponse:
    blocks: tuple[AssistantBlock, ...]
    stop_reason: str


class LLMProviderError(Exception):
    """An expected provider configuration or API failure."""


class LLMProvider(Protocol):
    """Interface required by the chatbot's provider-independent tool loop."""

    def generate(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[LLMTool],
        *,
        system_prompt: str,
    ) -> LLMResponse: ...


def mcp_tools_to_llm_tools(tools: Sequence[dict[str, Any]]) -> list[LLMTool]:
    """Translate discovered MCP definitions without duplicating their schemas."""

    translated: list[LLMTool] = []
    for tool in tools:
        name = tool.get("name")
        description = tool.get("description")
        input_schema = tool.get("inputSchema")
        if not isinstance(name, str) or not name:
            raise ValueError("MCP tool has an invalid name")
        if not isinstance(description, str) or not description:
            raise ValueError(f"MCP tool {name} has no description")
        if not isinstance(input_schema, dict):
            raise ValueError(f"MCP tool {name} has no inputSchema")
        translated.append(
            LLMTool(
                name=name,
                description=description,
                input_schema=json.loads(json.dumps(input_schema)),
            )
        )
    return translated


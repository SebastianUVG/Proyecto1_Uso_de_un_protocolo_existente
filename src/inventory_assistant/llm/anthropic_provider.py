"""Anthropic Messages API adapter; it does not implement MCP."""

from __future__ import annotations

from typing import Any, Sequence

from inventory_assistant.config import AnthropicConfig

from .base import (
    ConversationMessage,
    LLMProviderError,
    LLMResponse,
    LLMTool,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class AnthropicLLMProvider:
    """Call Claude and convert provider-specific blocks to local models."""

    def __init__(self, config: AnthropicConfig, *, client: Any | None = None) -> None:
        self._config = config
        if client is not None:
            self._client = client
            return
        try:
            from anthropic import Anthropic
        except ImportError as error:
            raise LLMProviderError(
                "The anthropic package is not installed. Run: python -m pip install -e ."
            ) from error
        self._client = Anthropic(
            api_key=config.api_key,
            timeout=config.timeout_seconds,
        )

    def generate(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[LLMTool],
        *,
        system_prompt: str,
    ) -> LLMResponse:
        try:
            response = self._client.messages.create(
                model=self._config.model,
                max_tokens=self._config.max_tokens,
                system=system_prompt,
                messages=[_message_to_anthropic(message) for message in messages],
                tools=[_tool_to_anthropic(tool) for tool in tools],
            )
        except Exception as error:
            raise LLMProviderError(
                "The Anthropic API request failed. Check the API key, model, "
                "network connection, and account availability."
            ) from error

        blocks: list[TextBlock | ToolUseBlock] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                blocks.append(TextBlock(text=block.text))
            elif block_type == "tool_use":
                arguments = block.input
                if not isinstance(arguments, dict):
                    arguments = {}
                blocks.append(
                    ToolUseBlock(
                        id=block.id,
                        name=block.name,
                        arguments=arguments,
                    )
                )
        if not blocks:
            raise LLMProviderError("Anthropic returned no usable response content")
        return LLMResponse(
            blocks=tuple(blocks),
            stop_reason=response.stop_reason or "unknown",
        )


def _tool_to_anthropic(tool: LLMTool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }


def _message_to_anthropic(message: ConversationMessage) -> dict[str, Any]:
    if isinstance(message.content, str):
        return {"role": message.role, "content": message.content}
    content: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            content.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            content.append(
                {
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.arguments,
                }
            )
        elif isinstance(block, ToolResultBlock):
            content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return {"role": message.role, "content": content}

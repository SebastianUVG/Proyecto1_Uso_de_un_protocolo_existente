"""OpenAI Chat Completions adapter; tool execution remains in the chatbot."""

from __future__ import annotations

import json
from typing import Any, Sequence

from inventory_assistant.config import OpenAIConfig

from .base import (
    ConversationMessage,
    LLMProviderError,
    LLMResponse,
    LLMTool,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)


class OpenAILLMProvider:
    """Translate provider-neutral messages to OpenAI Chat Completions."""

    def __init__(self, config: OpenAIConfig, *, client: Any | None = None) -> None:
        self._config = config
        if client is not None:
            self._client = client
            return
        try:
            from openai import OpenAI
        except ImportError as error:
            raise LLMProviderError(
                "The openai package is not installed. Run: python -m pip install -e ."
            ) from error
        self._client = OpenAI(
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
        request: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                *_messages_to_openai(messages),
            ],
            "max_completion_tokens": self._config.max_output_tokens,
            "store": False,
        }
        if tools:
            request["tools"] = [_tool_to_openai(tool) for tool in tools]
            if self._config.model.startswith("gpt-5.6-luna"):
                request["reasoning_effort"] = "none"
        try:
            response = self._client.chat.completions.create(**request)
        except Exception as error:
            raise LLMProviderError(_request_error_message(error)) from error
        return _response_from_openai(response)


def _tool_to_openai(tool: LLMTool) -> dict[str, Any]:
    parameters = json.loads(json.dumps(tool.input_schema))
    # Chat Completions requires an object at the root and rejects composition
    # keywords there. The MCP server still validates its complete source schema.
    for keyword in ("oneOf", "anyOf", "allOf", "enum", "const", "not"):
        parameters.pop(keyword, None)
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": parameters,
        },
    }


def _request_error_message(error: Exception) -> str:
    status_code = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    error_code = body.get("code") if isinstance(body, dict) else None

    if status_code == 401:
        return "OpenAI rejected the API key (HTTP 401). Check OPENAI_API_KEY."
    if status_code == 403:
        return "OpenAI denied access (HTTP 403). Check project and model permissions."
    if status_code == 404:
        return "OpenAI could not find the configured model (HTTP 404). Check OPENAI_MODEL."
    if status_code == 429 and error_code == "insufficient_quota":
        return "OpenAI reports insufficient API quota. Check billing and project credits."
    if status_code == 429:
        return "OpenAI rate limit reached (HTTP 429). Wait before trying again."
    if status_code == 400:
        suffix = f": {error_code}" if isinstance(error_code, str) else ""
        return f"OpenAI rejected the request (HTTP 400{suffix})."
    if status_code is not None:
        return f"The OpenAI API request failed with HTTP {status_code}."
    return (
        "The OpenAI API request failed. Check the network connection and "
        "OpenAI service availability."
    )


def _messages_to_openai(
    messages: Sequence[ConversationMessage],
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in messages:
        if isinstance(message.content, str):
            converted.append({"role": message.role, "content": message.content})
            continue

        text_parts = [
            block.text for block in message.content if isinstance(block, TextBlock)
        ]
        tool_uses = [
            block for block in message.content if isinstance(block, ToolUseBlock)
        ]
        tool_results = [
            block for block in message.content if isinstance(block, ToolResultBlock)
        ]

        if text_parts or tool_uses:
            assistant_message: dict[str, Any] = {
                "role": message.role,
                "content": "\n".join(text_parts) if text_parts else None,
            }
            if tool_uses:
                assistant_message["tool_calls"] = [
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(
                                block.arguments,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    }
                    for block in tool_uses
                ]
            converted.append(assistant_message)

        converted.extend(
            {
                "role": "tool",
                "tool_call_id": block.tool_use_id,
                "content": block.content,
            }
            for block in tool_results
        )
    return converted


def _response_from_openai(response: Any) -> LLMResponse:
    choices = getattr(response, "choices", ())
    if not choices:
        raise LLMProviderError("OpenAI returned no response choices")

    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None:
        raise LLMProviderError("OpenAI returned no usable response content")

    blocks: list[TextBlock | ToolUseBlock] = []
    content = getattr(message, "content", None)
    if isinstance(content, str) and content:
        blocks.append(TextBlock(text=content))
    for tool_call in getattr(message, "tool_calls", None) or ():
        blocks.append(_function_call_from_openai(tool_call))

    if not blocks:
        refusal = getattr(message, "refusal", None)
        if isinstance(refusal, str) and refusal:
            blocks.append(TextBlock(text=refusal))
        else:
            raise LLMProviderError("OpenAI returned no usable response content")

    has_tool_call = any(isinstance(block, ToolUseBlock) for block in blocks)
    return LLMResponse(
        blocks=tuple(blocks),
        stop_reason=(
            "tool_use" if has_tool_call else getattr(choice, "finish_reason", "stop")
        ),
    )


def _function_call_from_openai(tool_call: Any) -> ToolUseBlock:
    call_id = getattr(tool_call, "id", None)
    function = getattr(tool_call, "function", None)
    name = getattr(function, "name", None)
    serialized_arguments = getattr(function, "arguments", None)
    if not isinstance(call_id, str) or not call_id:
        raise LLMProviderError("OpenAI returned a function call without an id")
    if not isinstance(name, str) or not name:
        raise LLMProviderError("OpenAI returned a function call without a name")
    if not isinstance(serialized_arguments, str):
        raise LLMProviderError("OpenAI returned invalid function arguments")
    try:
        arguments = json.loads(serialized_arguments)
    except json.JSONDecodeError as error:
        raise LLMProviderError("OpenAI returned invalid function arguments") from error
    if not isinstance(arguments, dict):
        raise LLMProviderError("OpenAI function arguments must be a JSON object")
    return ToolUseBlock(id=call_id, name=name, arguments=arguments)

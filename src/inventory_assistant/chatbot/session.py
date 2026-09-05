"""Provider-independent conversation and MCP tool-use loop."""

from __future__ import annotations

import json
from typing import Any, Protocol, Sequence

from inventory_assistant.llm import (
    ConversationMessage,
    LLMProvider,
    LLMTool,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    mcp_tools_to_llm_tools,
)
from inventory_assistant.mcp.client import MCPClientError, MCPRemoteError


SYSTEM_PROMPT = """You are a helpful assistant connected to several MCP servers.
Answer general questions directly from your knowledge. Use the available tools
when a request requires current inventory data, controlled filesystem operations,
or Git operations. Tool names and descriptions identify their source server and
scope. Choose tools through their schemas, never invent tool results, and coordinate
multiple tools when the request requires it. Explain results in the user's language.
Preserve conversational context and use it to understand follow-up references. Do
not claim that an operation succeeded when a tool result reports an error."""


class MCPToolClient(Protocol):
    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


class ChatbotError(Exception):
    """Base class for expected chatbot orchestration errors."""


class ToolLoopLimitError(ChatbotError):
    """Raised when the model repeatedly requests tools without finishing."""


class ChatbotSession:
    """Maintain one conversation and coordinate LLM-requested MCP tools."""

    def __init__(
        self,
        provider: LLMProvider,
        mcp_client: MCPToolClient,
        *,
        max_tool_iterations: int = 5,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        if max_tool_iterations < 1:
            raise ValueError("max_tool_iterations must be at least 1")
        self._provider = provider
        self._mcp_client = mcp_client
        self._max_tool_iterations = max_tool_iterations
        self._system_prompt = system_prompt
        self._tools: list[LLMTool] = mcp_tools_to_llm_tools(
            mcp_client.list_tools()
        )
        self._history: list[ConversationMessage] = []

    @property
    def history(self) -> tuple[ConversationMessage, ...]:
        return tuple(self._history)

    @property
    def tools(self) -> tuple[LLMTool, ...]:
        return tuple(self._tools)

    def ask(self, user_message: str) -> str:
        if not isinstance(user_message, str) or not user_message.strip():
            raise ChatbotError("The message cannot be empty")
        previous_history_length = len(self._history)
        self._history.append(
            ConversationMessage(role="user", content=user_message.strip())
        )
        tool_iterations = 0
        try:
            while True:
                response = self._provider.generate(
                    tuple(self._history),
                    tuple(self._tools),
                    system_prompt=self._system_prompt,
                )
                self._history.append(
                    ConversationMessage(
                        role="assistant",
                        content=tuple(response.blocks),
                    )
                )
                tool_requests = [
                    block
                    for block in response.blocks
                    if isinstance(block, ToolUseBlock)
                ]
                if not tool_requests:
                    answer = "\n".join(
                        block.text
                        for block in response.blocks
                        if isinstance(block, TextBlock) and block.text
                    ).strip()
                    if not answer:
                        raise ChatbotError("The LLM returned no text response")
                    return answer

                if tool_iterations >= self._max_tool_iterations:
                    raise ToolLoopLimitError(
                        "The LLM exceeded the maximum number of tool-use iterations"
                    )
                results = tuple(
                    self._execute_tool(tool_request)
                    for tool_request in tool_requests
                )
                self._history.append(
                    ConversationMessage(role="user", content=results)
                )
                tool_iterations += 1
        except Exception:
            del self._history[previous_history_length:]
            raise

    def _execute_tool(self, request: ToolUseBlock) -> ToolResultBlock:
        try:
            result = self._mcp_client.call_tool(request.name, request.arguments)
        except MCPRemoteError as error:
            content = json.dumps(
                {
                    "error": {
                        "type": "MCP_PROTOCOL_ERROR",
                        "code": error.code,
                        "message": error.message,
                        "data": error.data,
                    }
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return ToolResultBlock(
                tool_use_id=request.id,
                content=content,
                is_error=True,
            )
        except MCPClientError as error:
            raise ChatbotError("An MCP server request failed") from error

        is_error = result.get("isError") is True
        payload = result.get("structuredContent", result)
        return ToolResultBlock(
            tool_use_id=request.id,
            content=json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            is_error=is_error,
        )

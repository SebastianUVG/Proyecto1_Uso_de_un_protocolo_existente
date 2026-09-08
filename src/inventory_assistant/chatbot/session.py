"""Provider-independent conversation and MCP tool-use loop."""

from __future__ import annotations

import json
from dataclasses import dataclass
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
when a request requires data or actions owned by a connected service. Tool names
and descriptions identify their source server and scope. Choose tools through
their schemas, never invent tool results, and coordinate multiple tools and servers
when the request requires it. Explain results in the user's language. Preserve
conversational context and use it to understand follow-up references. Do not claim
that an operation succeeded when a tool result reports an error."""

MUTATING_INVENTORY_TOOLS = frozenset(
    {
        "add_product",
        "record_inventory_entry",
        "record_inventory_exit",
        "update_product",
        "adjust_inventory",
    }
)
_CONFIRMATION_YES = frozenset({"yes", "y", "confirm", "confirmed", "si", "sí"})
_CONFIRMATION_NO = frozenset({"no", "n", "cancel", "cancelled", "cancelar"})


@dataclass(frozen=True, slots=True)
class PendingOperation:
    requests: tuple[ToolUseBlock, ...]
    tool_iterations: int


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
        self._pending_operation: PendingOperation | None = None

    @property
    def history(self) -> tuple[ConversationMessage, ...]:
        return tuple(self._history)

    @property
    def tools(self) -> tuple[LLMTool, ...]:
        return tuple(self._tools)

    @property
    def pending_operation(self) -> PendingOperation | None:
        return self._pending_operation

    def ask(self, user_message: str) -> str:
        if not isinstance(user_message, str) or not user_message.strip():
            raise ChatbotError("The message cannot be empty")
        if self._pending_operation is not None:
            return self._handle_confirmation(user_message)
        previous_history_length = len(self._history)
        self._history.append(
            ConversationMessage(role="user", content=user_message.strip())
        )
        try:
            return self._continue_tool_loop(tool_iterations=0)
        except Exception:
            del self._history[previous_history_length:]
            raise

    def _continue_tool_loop(self, *, tool_iterations: int) -> str:
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
            tool_requests = tuple(
                block
                for block in response.blocks
                if isinstance(block, ToolUseBlock)
            )
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
            if any(_requires_confirmation(request.name) for request in tool_requests):
                preserved_requests = tuple(
                    ToolUseBlock(
                        id=request.id,
                        name=request.name,
                        arguments=json.loads(json.dumps(request.arguments)),
                    )
                    for request in tool_requests
                )
                self._pending_operation = PendingOperation(
                    requests=preserved_requests,
                    tool_iterations=tool_iterations,
                )
                return _confirmation_prompt(preserved_requests)

            results = tuple(self._execute_tool(request) for request in tool_requests)
            self._history.append(ConversationMessage(role="user", content=results))
            tool_iterations += 1

    def _handle_confirmation(self, user_message: str) -> str:
        normalized = user_message.strip().casefold()
        if normalized not in _CONFIRMATION_YES | _CONFIRMATION_NO:
            return "Please answer yes or no. The pending inventory operation has not run."

        pending = self._pending_operation
        assert pending is not None
        self._pending_operation = None
        if normalized in _CONFIRMATION_NO:
            results = tuple(
                ToolResultBlock(
                    tool_use_id=request.id,
                    content=json.dumps(
                        {
                            "error": {
                                "type": "OPERATION_CANCELLED",
                                "message": "The user cancelled the operation.",
                            }
                        },
                        separators=(",", ":"),
                    ),
                    is_error=True,
                )
                for request in pending.requests
            )
            self._history.append(ConversationMessage(role="user", content=results))
            answer = "Operation cancelled. No inventory changes were made."
            self._history.append(
                ConversationMessage(role="assistant", content=(TextBlock(answer),))
            )
            return answer

        results = tuple(self._execute_tool(request) for request in pending.requests)
        self._history.append(ConversationMessage(role="user", content=results))
        return self._continue_tool_loop(
            tool_iterations=pending.tool_iterations + 1
        )

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


def _requires_confirmation(tool_name: str) -> bool:
    if tool_name in MUTATING_INVENTORY_TOOLS:
        return True
    prefix, separator, original_name = tool_name.partition("__")
    return (
        bool(separator)
        and prefix == "inventory"
        and original_name in MUTATING_INVENTORY_TOOLS
    )


def _confirmation_prompt(requests: tuple[ToolUseBlock, ...]) -> str:
    descriptions = [_describe_mutation(request) for request in requests]
    return "\n".join([*descriptions, "Confirm? (yes/no)"])


def _describe_mutation(request: ToolUseBlock) -> str:
    name = request.name.partition("__")[2] or request.name
    arguments = request.arguments
    if name == "add_product":
        return (
            f"This will create {arguments.get('name', 'a product')} "
            f"({arguments.get('sku', 'unknown SKU')}) with initial stock "
            f"{arguments.get('initial_stock', 'unknown')}."
        )
    selector = (
        arguments.get("name")
        or arguments.get("sku")
        or f"product {arguments.get('product_id', 'unknown')}"
    )
    quantity = arguments.get("quantity", "unknown")
    if name == "record_inventory_entry":
        return f"This will add {quantity} units to {selector}."
    if name == "record_inventory_exit":
        return f"This will remove {quantity} units from {selector}."
    if name == "update_product":
        updates = [
            f"{key} -> {value}"
            for key, value in arguments.items()
            if key not in {"product_id", "sku", "name"}
        ]
        details = ", ".join(updates) or "the requested administrative fields"
        return f"This will update {selector}: {details}."
    if name == "adjust_inventory":
        counted_stock = arguments.get("counted_stock", "unknown")
        return f"This will set {selector} to a physical count of {counted_stock} units."
    return f"This will execute the pending inventory operation {request.name}."

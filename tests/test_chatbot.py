"""Tests for the provider-neutral chatbot and tool-use loop."""

from __future__ import annotations

import unittest
from typing import Any, Sequence

from inventory_assistant.chatbot.session import ChatbotSession, ToolLoopLimitError
from inventory_assistant.llm import (
    ConversationMessage,
    LLMResponse,
    LLMTool,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from inventory_assistant.mcp.client import MCPRemoteError


DISCOVERED_TOOLS = [
    {
        "name": "get_low_stock_products",
        "description": "List products at or below minimum stock.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer"}},
        },
    },
    {
        "name": "get_product_stock",
        "description": "Get stock for one exact SKU.",
        "inputSchema": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    },
    {
        "name": "inventory__record_inventory_entry",
        "description": "Record an inventory entry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "quantity": {"type": "integer"},
            },
            "required": ["sku", "quantity"],
        },
    },
    {
        "name": "inventory__list_products",
        "description": "List and filter inventory products without modifying data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "max_stock": {"type": "integer"},
            },
        },
    },
    {
        "name": "inventory__update_product",
        "description": "Update administrative product information.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "target_stock": {"type": "integer"},
            },
        },
    },
    {
        "name": "inventory__adjust_inventory",
        "description": "Set stock to a physical inventory count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "counted_stock": {"type": "integer"},
            },
        },
    },
]


class ScriptedLLMProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[tuple[ConversationMessage, ...], tuple[LLMTool, ...]]] = []

    def generate(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[LLMTool],
        *,
        system_prompt: str,
    ) -> LLMResponse:
        self.calls.append((tuple(messages), tuple(tools)))
        if not self.responses:
            raise AssertionError("Fake LLM has no scripted response")
        return self.responses.pop(0)


class FakeMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.results: dict[str, dict[str, Any] | MCPRemoteError] = {
            "get_low_stock_products": {
                "isError": False,
                "structuredContent": {
                    "count": 2,
                    "products": ["Wireless Mouse", "Mechanical Keyboard"],
                },
            },
            "get_product_stock": {
                "isError": False,
                "structuredContent": {
                    "product": {"sku": "ELEC-001", "current_stock": 0}
                },
            },
            "inventory__record_inventory_entry": {
                "isError": False,
                "structuredContent": {
                    "product": {"sku": "WARE-003", "current_stock": 20},
                    "quantity": 20,
                    "previous_stock": 0,
                    "new_stock": 20,
                },
            },
            "inventory__list_products": {
                "isError": False,
                "structuredContent": {
                    "count": 1,
                    "products": [{"sku": "ELEC-002", "current_stock": 3}],
                },
            },
            "inventory__update_product": {
                "isError": False,
                "structuredContent": {
                    "product": {"sku": "ELEC-002", "target_stock": 30},
                    "changed_fields": ["target_stock"],
                },
            },
            "inventory__adjust_inventory": {
                "isError": False,
                "structuredContent": {
                    "product": {"sku": "ELEC-002", "current_stock": 12},
                    "previous_stock": 3,
                    "counted_stock": 12,
                    "difference": 9,
                    "adjustment_type": "ADJUSTMENT_IN",
                    "resulting_stock": 12,
                },
            },
        }

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        return DISCOVERED_TOOLS

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        result = self.results.get(name)
        if isinstance(result, MCPRemoteError):
            raise result
        if result is None:
            raise MCPRemoteError(-32602, f"Unknown tool: {name}")
        return result


def response(*blocks: TextBlock | ToolUseBlock) -> LLMResponse:
    stop_reason = (
        "tool_use" if any(isinstance(block, ToolUseBlock) for block in blocks) else "end_turn"
    )
    return LLMResponse(blocks=tuple(blocks), stop_reason=stop_reason)


class ChatbotSessionTests(unittest.TestCase):
    def test_general_question_does_not_call_inventory(self) -> None:
        provider = ScriptedLLMProvider(
            [response(TextBlock("Alan Turing was a British mathematician."))]
        )
        mcp = FakeMCPClient()
        session = ChatbotSession(provider, mcp)
        answer = session.ask("Who was Alan Turing?")
        self.assertIn("mathematician", answer)
        self.assertEqual(mcp.calls, [])

    def test_llm_requested_tool_is_called_and_result_returns_to_llm(self) -> None:
        provider = ScriptedLLMProvider(
            [
                response(
                    ToolUseBlock(
                        id="tool-1",
                        name="get_low_stock_products",
                        arguments={"limit": 2},
                    )
                ),
                response(TextBlock("Two products currently need attention.")),
            ]
        )
        mcp = FakeMCPClient()
        session = ChatbotSession(provider, mcp)
        answer = session.ask("Which products have low stock?")
        self.assertEqual(answer, "Two products currently need attention.")
        self.assertEqual(mcp.calls, [("get_low_stock_products", {"limit": 2})])
        tool_result_message = provider.calls[1][0][-1]
        self.assertEqual(tool_result_message.role, "user")
        tool_result = tool_result_message.content[0]
        self.assertIsInstance(tool_result, ToolResultBlock)
        self.assertFalse(tool_result.is_error)
        self.assertIn('"count":2', tool_result.content)

    def test_multiple_tool_calls_in_one_model_response(self) -> None:
        provider = ScriptedLLMProvider(
            [
                response(
                    ToolUseBlock("tool-1", "get_low_stock_products", {"limit": 1}),
                    ToolUseBlock("tool-2", "get_product_stock", {"sku": "ELEC-001"}),
                ),
                response(TextBlock("The mouse is empty and is the most urgent.")),
            ]
        )
        mcp = FakeMCPClient()
        answer = ChatbotSession(provider, mcp).ask("Find the most urgent low item.")
        self.assertIn("most urgent", answer)
        self.assertEqual(len(mcp.calls), 2)
        results = provider.calls[1][0][-1].content
        self.assertEqual(len(results), 2)

    def test_context_is_preserved_between_user_turns(self) -> None:
        provider = ScriptedLLMProvider(
            [
                response(TextBlock("Alan Turing was a mathematician.")),
                response(TextBlock("He was born in 1912.")),
            ]
        )
        session = ChatbotSession(provider, FakeMCPClient())
        session.ask("Who was Alan Turing?")
        answer = session.ask("When was he born?")
        self.assertEqual(answer, "He was born in 1912.")
        second_call_messages = provider.calls[1][0]
        self.assertEqual(
            [message.role for message in second_call_messages],
            ["user", "assistant", "user"],
        )
        self.assertEqual(second_call_messages[0].content, "Who was Alan Turing?")

    def test_tool_iteration_limit_prevents_infinite_loop(self) -> None:
        provider = ScriptedLLMProvider(
            [
                response(ToolUseBlock("tool-1", "get_low_stock_products", {})),
                response(ToolUseBlock("tool-2", "get_low_stock_products", {})),
            ]
        )
        mcp = FakeMCPClient()
        session = ChatbotSession(provider, mcp, max_tool_iterations=1)
        with self.assertRaises(ToolLoopLimitError):
            session.ask("Keep checking forever")
        self.assertEqual(len(mcp.calls), 1)
        self.assertEqual(session.history, ())

    def test_tool_execution_error_is_returned_to_model(self) -> None:
        provider = ScriptedLLMProvider(
            [
                response(ToolUseBlock("tool-1", "get_product_stock", {"sku": "BAD"})),
                response(TextBlock("I could not find that product.")),
            ]
        )
        mcp = FakeMCPClient()
        mcp.results["get_product_stock"] = {
            "isError": True,
            "structuredContent": {
                "error": {"type": "PRODUCT_NOT_FOUND", "message": "product not found"}
            },
        }
        answer = ChatbotSession(provider, mcp).ask("Check BAD")
        self.assertIn("could not find", answer)
        result = provider.calls[1][0][-1].content[0]
        self.assertTrue(result.is_error)

    def test_unknown_tool_error_does_not_crash_tool_loop(self) -> None:
        provider = ScriptedLLMProvider(
            [
                response(ToolUseBlock("tool-1", "unknown_tool", {})),
                response(TextBlock("That tool is not available.")),
            ]
        )
        answer = ChatbotSession(provider, FakeMCPClient()).ask("Use an unknown tool")
        self.assertEqual(answer, "That tool is not available.")
        result = provider.calls[1][0][-1].content[0]
        self.assertTrue(result.is_error)
        self.assertIn("MCP_PROTOCOL_ERROR", result.content)

    def test_tools_are_translated_from_dynamic_mcp_discovery(self) -> None:
        provider = ScriptedLLMProvider([response(TextBlock("Done"))])
        session = ChatbotSession(provider, FakeMCPClient())
        self.assertEqual(
            [tool.name for tool in session.tools],
            [
                "get_low_stock_products",
                "get_product_stock",
                "inventory__record_inventory_entry",
                "inventory__list_products",
                "inventory__update_product",
                "inventory__adjust_inventory",
            ],
        )
        self.assertEqual(
            session.tools[0].input_schema,
            DISCOVERED_TOOLS[0]["inputSchema"],
        )

    def test_write_tool_waits_for_confirmation_and_preserves_arguments(self) -> None:
        arguments = {"sku": "WARE-003", "quantity": 20}
        provider = ScriptedLLMProvider(
            [
                response(
                    ToolUseBlock(
                        "write-1",
                        "inventory__record_inventory_entry",
                        arguments,
                    )
                ),
                response(TextBlock("20 units were added. Current stock: 20.")),
            ]
        )
        mcp = FakeMCPClient()
        session = ChatbotSession(provider, mcp)

        prompt = session.ask("Add 20 units of Safety Gloves")
        self.assertIn("Confirm? (yes/no)", prompt)
        self.assertIn("20", prompt)
        self.assertEqual(mcp.calls, [])
        self.assertEqual(
            session.pending_operation.requests[0].arguments,
            arguments,
        )

        answer = session.ask("yes")
        self.assertEqual(answer, "20 units were added. Current stock: 20.")
        self.assertEqual(
            mcp.calls,
            [("inventory__record_inventory_entry", arguments)],
        )
        self.assertIsNone(session.pending_operation)
        tool_result = provider.calls[1][0][-1].content[0]
        self.assertIn('"new_stock":20', tool_result.content)

    def test_no_cancels_write_without_calling_mcp(self) -> None:
        provider = ScriptedLLMProvider(
            [
                response(
                    ToolUseBlock(
                        "write-1",
                        "inventory__record_inventory_entry",
                        {"sku": "WARE-003", "quantity": 20},
                    )
                )
            ]
        )
        mcp = FakeMCPClient()
        session = ChatbotSession(provider, mcp)
        session.ask("Add stock")

        answer = session.ask("no")
        self.assertIn("cancelled", answer.casefold())
        self.assertEqual(mcp.calls, [])
        self.assertIsNone(session.pending_operation)
        cancellation_result = session.history[-2].content[0]
        self.assertTrue(cancellation_result.is_error)
        self.assertIn("OPERATION_CANCELLED", cancellation_result.content)

    def test_unclear_confirmation_keeps_exact_operation_pending(self) -> None:
        provider = ScriptedLLMProvider(
            [
                response(
                    ToolUseBlock(
                        "write-1",
                        "inventory__record_inventory_entry",
                        {"sku": "WARE-003", "quantity": 20},
                    )
                )
            ]
        )
        mcp = FakeMCPClient()
        session = ChatbotSession(provider, mcp)
        session.ask("Add stock")
        answer = session.ask("maybe later")
        self.assertIn("yes or no", answer)
        self.assertEqual(mcp.calls, [])
        self.assertEqual(
            session.pending_operation.requests[0].arguments,
            {"sku": "WARE-003", "quantity": 20},
        )

    def test_list_products_is_read_only_and_runs_without_confirmation(self) -> None:
        arguments = {"category": "Electronics", "max_stock": 20}
        provider = ScriptedLLMProvider(
            [
                response(
                    ToolUseBlock(
                        "list-1", "inventory__list_products", arguments
                    )
                ),
                response(TextBlock("One Electronics product matched.")),
            ]
        )
        mcp = FakeMCPClient()
        answer = ChatbotSession(provider, mcp).ask("Show filtered products")
        self.assertEqual(answer, "One Electronics product matched.")
        self.assertEqual(mcp.calls, [("inventory__list_products", arguments)])

    def test_update_product_requires_confirmation_and_yes_executes_exact_call(self) -> None:
        arguments = {"sku": "ELEC-002", "target_stock": 30}
        provider = ScriptedLLMProvider(
            [
                response(
                    ToolUseBlock(
                        "update-1", "inventory__update_product", arguments
                    )
                ),
                response(TextBlock("Target stock changed to 30.")),
            ]
        )
        mcp = FakeMCPClient()
        session = ChatbotSession(provider, mcp)
        prompt = session.ask("Change its target stock")
        self.assertIn("update ELEC-002", prompt)
        self.assertIn("target_stock -> 30", prompt)
        self.assertEqual(mcp.calls, [])
        self.assertEqual(session.ask("yes"), "Target stock changed to 30.")
        self.assertEqual(mcp.calls, [("inventory__update_product", arguments)])

    def test_update_product_cancellation_discards_exact_call(self) -> None:
        provider = ScriptedLLMProvider(
            [
                response(
                    ToolUseBlock(
                        "update-1",
                        "inventory__update_product",
                        {"sku": "ELEC-002", "target_stock": 30},
                    )
                )
            ]
        )
        mcp = FakeMCPClient()
        session = ChatbotSession(provider, mcp)
        session.ask("Change target")
        self.assertIn("cancelled", session.ask("no").casefold())
        self.assertEqual(mcp.calls, [])

    def test_adjust_inventory_requires_confirmation_and_yes_executes_exact_call(self) -> None:
        arguments = {"sku": "ELEC-002", "counted_stock": 12}
        provider = ScriptedLLMProvider(
            [
                response(
                    ToolUseBlock(
                        "adjust-1", "inventory__adjust_inventory", arguments
                    )
                ),
                response(TextBlock("Physical count saved at 12 units.")),
            ]
        )
        mcp = FakeMCPClient()
        session = ChatbotSession(provider, mcp)
        prompt = session.ask("We counted 12")
        self.assertIn("physical count of 12", prompt)
        self.assertEqual(mcp.calls, [])
        self.assertEqual(session.ask("yes"), "Physical count saved at 12 units.")
        self.assertEqual(mcp.calls, [("inventory__adjust_inventory", arguments)])

    def test_adjust_inventory_cancellation_makes_no_mcp_call(self) -> None:
        provider = ScriptedLLMProvider(
            [
                response(
                    ToolUseBlock(
                        "adjust-1",
                        "inventory__adjust_inventory",
                        {"sku": "ELEC-002", "counted_stock": 12},
                    )
                )
            ]
        )
        mcp = FakeMCPClient()
        session = ChatbotSession(provider, mcp)
        session.ask("Adjust inventory")
        session.ask("no")
        self.assertEqual(mcp.calls, [])


if __name__ == "__main__":
    unittest.main()

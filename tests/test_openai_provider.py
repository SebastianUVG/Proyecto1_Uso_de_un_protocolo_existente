"""Tests for the OpenAI adapter without making network requests."""

from __future__ import annotations

import types
import unittest

from inventory_assistant.config import OpenAIConfig
from inventory_assistant.llm import (
    ConversationMessage,
    LLMProviderError,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    mcp_tools_to_llm_tools,
)
from inventory_assistant.llm.openai_provider import OpenAILLMProvider


def completion(
    content: str | None = None,
    *,
    tool_calls: list[object] | None = None,
    finish_reason: str = "stop",
) -> object:
    return types.SimpleNamespace(
        choices=[
            types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=content,
                    tool_calls=tool_calls,
                    refusal=None,
                ),
                finish_reason=finish_reason,
            )
        ]
    )


def function_call(call_id: str, name: str, arguments: str) -> object:
    return types.SimpleNamespace(
        type="function",
        id=call_id,
        function=types.SimpleNamespace(name=name, arguments=arguments),
    )


class FakeCompletionsAPI:
    def __init__(self, response: object, *, failure: Exception | None = None) -> None:
        self.response = response
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def create(self, **arguments: object) -> object:
        self.calls.append(arguments)
        if self.failure is not None:
            raise self.failure
        return self.response


class FakeOpenAIClient:
    def __init__(self, completions: FakeCompletionsAPI) -> None:
        self.chat = types.SimpleNamespace(completions=completions)


class OpenAILLMProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = OpenAIConfig(
            api_key="test-key-not-real",
            model="test-model",
            max_output_tokens=256,
            timeout_seconds=2,
        )

    def make_provider(self, api: FakeCompletionsAPI) -> OpenAILLMProvider:
        return OpenAILLMProvider(
            self.config,
            client=FakeOpenAIClient(api),
        )

    def test_returns_normal_text_without_tool_call(self) -> None:
        api = FakeCompletionsAPI(completion("Hello."))
        response = self.make_provider(api).generate(
            [ConversationMessage(role="user", content="Hello")],
            [],
            system_prompt="Be helpful.",
        )
        self.assertEqual(response.blocks, (TextBlock("Hello."),))
        self.assertEqual(response.stop_reason, "stop")
        self.assertNotIn("tools", api.calls[0])

    def test_converts_discovered_mcp_tool_to_openai_function(self) -> None:
        api = FakeCompletionsAPI(completion("Done."))
        provider = self.make_provider(api)
        tools = mcp_tools_to_llm_tools(
            [
                {
                    "name": "inventory__get_product_stock",
                    "description": "Get stock by SKU.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"sku": {"type": "string"}},
                        "required": ["sku"],
                        "oneOf": [{"required": ["sku"]}],
                    },
                }
            ]
        )
        provider.generate(
            [ConversationMessage(role="user", content="Check stock")],
            tools,
            system_prompt="Use tools.",
        )
        call = api.calls[0]
        self.assertEqual(call["model"], "test-model")
        self.assertEqual(
            call["messages"][0],
            {"role": "system", "content": "Use tools."},
        )
        self.assertEqual(call["max_completion_tokens"], 256)
        self.assertFalse(call["store"])
        self.assertEqual(
            call["tools"][0],
            {
                "type": "function",
                "function": {
                    "name": "inventory__get_product_stock",
                    "description": "Get stock by SKU.",
                    "parameters": {
                        "type": "object",
                        "properties": {"sku": {"type": "string"}},
                        "required": ["sku"],
                    },
                },
            },
        )
        self.assertIn("oneOf", tools[0].input_schema)

    def test_receives_function_call_and_parses_arguments(self) -> None:
        api_response = completion(
            tool_calls=[
                function_call(
                    "call-1",
                    "inventory__get_product_stock",
                    '{"sku":"ELEC-001"}',
                )
            ],
            finish_reason="tool_calls",
        )
        response = self.make_provider(FakeCompletionsAPI(api_response)).generate(
            [ConversationMessage(role="user", content="Check the mouse")],
            [],
            system_prompt="Use tools.",
        )
        self.assertEqual(response.stop_reason, "tool_use")
        self.assertEqual(
            response.blocks,
            (
                ToolUseBlock(
                    "call-1",
                    "inventory__get_product_stock",
                    {"sku": "ELEC-001"},
                ),
            ),
        )

    def test_disables_luna_reasoning_when_functions_are_available(self) -> None:
        api = FakeCompletionsAPI(completion("Done."))
        provider = OpenAILLMProvider(
            OpenAIConfig(
                api_key="test-key-not-real",
                model="gpt-5.6-luna",
                max_output_tokens=256,
                timeout_seconds=2,
            ),
            client=FakeOpenAIClient(api),
        )
        tools = mcp_tools_to_llm_tools(
            [
                {
                    "name": "inventory__get_low_stock_products",
                    "description": "Get low-stock products.",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]
        )
        provider.generate(
            [ConversationMessage(role="user", content="Check stock")],
            tools,
            system_prompt="Use tools.",
        )
        self.assertEqual(api.calls[0]["reasoning_effort"], "none")

    def test_receives_multiple_function_calls(self) -> None:
        api_response = completion(
            tool_calls=[
                function_call("call-1", "inventory__get_low_stock_products", "{}"),
                function_call("call-2", "git__git_status", '{"repo_path":"demo"}'),
            ],
            finish_reason="tool_calls",
        )
        response = self.make_provider(FakeCompletionsAPI(api_response)).generate(
            [ConversationMessage(role="user", content="Check both")],
            [],
            system_prompt="Use tools.",
        )
        self.assertEqual(len(response.blocks), 2)
        self.assertEqual(
            [block.id for block in response.blocks if isinstance(block, ToolUseBlock)],
            ["call-1", "call-2"],
        )

    def test_sends_function_call_and_tool_result_back_to_openai(self) -> None:
        api = FakeCompletionsAPI(completion("The mouse has no stock."))
        provider = self.make_provider(api)
        provider.generate(
            [
                ConversationMessage(role="user", content="Check the mouse"),
                ConversationMessage(
                    role="assistant",
                    content=(
                        ToolUseBlock(
                            "call-1",
                            "inventory__get_product_stock",
                            {"sku": "ELEC-001"},
                        ),
                    ),
                ),
                ConversationMessage(
                    role="user",
                    content=(
                        ToolResultBlock(
                            "call-1",
                            '{"current_stock":0}',
                        ),
                    ),
                ),
            ],
            [],
            system_prompt="Use tools.",
        )
        messages = api.calls[0]["messages"]
        function_input = messages[2]
        result_input = messages[3]
        self.assertEqual(function_input["role"], "assistant")
        self.assertIsNone(function_input["content"])
        self.assertEqual(function_input["tool_calls"][0]["id"], "call-1")
        self.assertEqual(
            function_input["tool_calls"][0]["function"]["arguments"],
            '{"sku":"ELEC-001"}',
        )
        self.assertEqual(result_input["role"], "tool")
        self.assertEqual(result_input["tool_call_id"], "call-1")
        self.assertEqual(result_input["content"], '{"current_stock":0}')

    def test_invalid_function_arguments_are_rejected(self) -> None:
        api_response = completion(
            tool_calls=[function_call("call-1", "inventory__tool", "not-json")],
            finish_reason="tool_calls",
        )
        with self.assertRaises(LLMProviderError):
            self.make_provider(FakeCompletionsAPI(api_response)).generate(
                [ConversationMessage(role="user", content="Hello")],
                [],
                system_prompt="Be helpful.",
            )

    def test_api_failure_does_not_expose_original_error(self) -> None:
        api = FakeCompletionsAPI(
            completion("unused"),
            failure=RuntimeError("secret-value"),
        )
        with self.assertRaises(LLMProviderError) as raised:
            self.make_provider(api).generate(
                [ConversationMessage(role="user", content="Hello")],
                [],
                system_prompt="Be helpful.",
            )
        self.assertNotIn("secret-value", str(raised.exception))

    def test_api_failure_reports_safe_status_and_code(self) -> None:
        failure = RuntimeError("sensitive upstream message")
        failure.status_code = 400
        failure.body = {"code": "invalid_function_parameters"}
        api = FakeCompletionsAPI(completion("unused"), failure=failure)
        with self.assertRaises(LLMProviderError) as raised:
            self.make_provider(api).generate(
                [ConversationMessage(role="user", content="Hello")],
                [],
                system_prompt="Be helpful.",
            )
        self.assertEqual(
            str(raised.exception),
            "OpenAI rejected the request (HTTP 400: invalid_function_parameters).",
        )
        self.assertNotIn("sensitive upstream message", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

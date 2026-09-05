"""Tests for the Anthropic adapter without making network requests."""

from __future__ import annotations

import types
import unittest

from inventory_assistant.config import AnthropicConfig
from inventory_assistant.llm import (
    ConversationMessage,
    LLMProviderError,
    LLMTool,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from inventory_assistant.llm.anthropic_provider import AnthropicLLMProvider


class FakeMessagesAPI:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls: list[dict[str, object]] = []

    def create(self, **arguments: object) -> object:
        self.calls.append(arguments)
        if self.failure is not None:
            raise self.failure
        return types.SimpleNamespace(
            content=[
                types.SimpleNamespace(type="text", text="Checking inventory."),
                types.SimpleNamespace(
                    type="tool_use",
                    id="tool-1",
                    name="get_product_stock",
                    input={"sku": "ELEC-001"},
                ),
            ],
            stop_reason="tool_use",
        )


class FakeAnthropicClient:
    def __init__(self, messages: FakeMessagesAPI) -> None:
        self.messages = messages


class AnthropicLLMProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AnthropicConfig(
            api_key="test-key-not-real",
            model="test-model",
            max_tokens=256,
            timeout_seconds=2,
        )

    def test_converts_messages_tools_and_response_blocks(self) -> None:
        messages_api = FakeMessagesAPI()
        provider = AnthropicLLMProvider(
            self.config,
            client=FakeAnthropicClient(messages_api),
        )
        response = provider.generate(
            [
                ConversationMessage(role="user", content="Check the mouse"),
                ConversationMessage(
                    role="assistant",
                    content=(
                        ToolUseBlock(
                            "previous-tool",
                            "get_product_stock",
                            {"sku": "ELEC-002"},
                        ),
                    ),
                ),
                ConversationMessage(
                    role="user",
                    content=(
                        ToolResultBlock(
                            "previous-tool",
                            '{"current_stock":3}',
                        ),
                    ),
                ),
            ],
            [
                LLMTool(
                    name="get_product_stock",
                    description="Get stock by SKU.",
                    input_schema={"type": "object"},
                )
            ],
            system_prompt="Use tools when needed.",
        )

        call = messages_api.calls[0]
        self.assertEqual(call["model"], "test-model")
        self.assertEqual(call["tools"][0]["input_schema"], {"type": "object"})
        self.assertEqual(
            call["messages"][2]["content"][0]["type"],
            "tool_result",
        )
        self.assertEqual(response.stop_reason, "tool_use")
        self.assertIsInstance(response.blocks[0], TextBlock)
        self.assertIsInstance(response.blocks[1], ToolUseBlock)
        self.assertEqual(response.blocks[1].arguments, {"sku": "ELEC-001"})

    def test_api_failure_does_not_expose_original_error(self) -> None:
        messages_api = FakeMessagesAPI(failure=RuntimeError("secret-value"))
        provider = AnthropicLLMProvider(
            self.config,
            client=FakeAnthropicClient(messages_api),
        )
        with self.assertRaises(LLMProviderError) as raised:
            provider.generate(
                [ConversationMessage(role="user", content="Hello")],
                [],
                system_prompt="Be helpful.",
            )
        self.assertNotIn("secret-value", str(raised.exception))


if __name__ == "__main__":
    unittest.main()


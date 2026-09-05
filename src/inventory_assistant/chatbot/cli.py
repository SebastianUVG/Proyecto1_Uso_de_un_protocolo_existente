"""Interactive terminal interface for the local inventory chatbot."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from inventory_assistant.config import (
    AnthropicConfig,
    ChatbotConfig,
    ConfigurationError,
    MCPClientConfig,
)
from inventory_assistant.llm import LLMProviderError
from inventory_assistant.llm.anthropic_provider import AnthropicLLMProvider
from inventory_assistant.mcp.client import LocalMCPClient, MCPClientError

from .session import ChatbotError, ChatbotSession


def main() -> None:
    try:
        anthropic_config = AnthropicConfig.from_env()
        mcp_config = MCPClientConfig.from_env()
        chatbot_config = ChatbotConfig.from_env()
        provider = AnthropicLLMProvider(anthropic_config)
    except (ConfigurationError, LLMProviderError) as error:
        print(f"Configuration error: {error}")
        return

    client = LocalMCPClient(mcp_config)
    try:
        client.connect()
        session = ChatbotSession(
            provider,
            client,
            max_tool_iterations=chatbot_config.max_tool_iterations,
        )
    except (MCPClientError, ValueError) as error:
        client.close()
        print(f"Startup error: {error}")
        return

    print("Inventory Assistant")
    print("Type /exit to quit or /logs to show recent MCP interactions.")
    try:
        while True:
            try:
                user_message = input("\nYou: ").strip()
            except EOFError:
                break
            if user_message.casefold() == "/exit":
                break
            if user_message.casefold() == "/logs":
                _show_recent_logs(mcp_config.log_path)
                continue
            if not user_message:
                continue
            try:
                answer = session.ask(user_message)
                print(f"\nAssistant: {answer}")
            except (ChatbotError, LLMProviderError, MCPClientError) as error:
                print(f"\nError: {error}")
    except KeyboardInterrupt:
        print("\nSession interrupted.")
    finally:
        client.close()
        print("Goodbye.")


def _show_recent_logs(path: Path, limit: int = 20) -> None:
    if not path.exists():
        print("No MCP logs are available yet.")
        return
    try:
        with path.open("r", encoding="utf-8") as log_file:
            recent = deque(log_file, maxlen=limit)
    except OSError:
        print("Unable to read the MCP log file.")
        return
    print(f"Recent MCP interactions ({path}):")
    for line in recent:
        try:
            print(json.dumps(json.loads(line), ensure_ascii=False, indent=2))
        except json.JSONDecodeError:
            print("[Invalid log entry omitted]")


if __name__ == "__main__":
    main()


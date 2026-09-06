"""Interactive terminal interface for the local inventory chatbot."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from inventory_assistant.config import (
    ChatbotConfig,
    ConfigurationError,
    ExternalMCPConfig,
    InventoryMCPConfig,
    MCPClientConfig,
    OpenAIConfig,
)
from inventory_assistant.llm import LLMProviderError
from inventory_assistant.llm.openai_provider import OpenAILLMProvider
from inventory_assistant.mcp.client import MCPClientError
from inventory_assistant.mcp.manager import (
    MCPServerManager,
    configured_server_definitions,
)

from .session import ChatbotError, ChatbotSession


def main() -> None:
    try:
        openai_config = OpenAIConfig.from_env()
        mcp_config = MCPClientConfig.from_env()
        external_mcp_config = ExternalMCPConfig.from_env()
        inventory_mcp_config = InventoryMCPConfig.from_env()
        chatbot_config = ChatbotConfig.from_env()
        provider = OpenAILLMProvider(openai_config)
    except (ConfigurationError, LLMProviderError) as error:
        print(f"Configuration error: {error}")
        return

    client = MCPServerManager(
        mcp_config,
        configured_server_definitions(
            external_mcp_config,
            inventory_config=inventory_mcp_config,
        ),
    )
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

    print("MCP Assistant")
    print("Type /exit to quit, /servers to inspect servers, or /logs for MCP logs.")
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
            if user_message.casefold() == "/servers":
                _show_servers(client)
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


def _show_servers(manager: MCPServerManager) -> None:
    for status in manager.statuses:
        state = "connected" if status.connected else "disconnected"
        print(f"{status.name} ({status.transport}): {state}, {len(status.tools)} tools")


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

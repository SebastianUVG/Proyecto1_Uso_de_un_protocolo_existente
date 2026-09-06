"""Tests for multi-server MCP registration, routing, and chatbot use."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Sequence

from inventory_assistant.chatbot.session import ChatbotSession
from inventory_assistant.config import (
    ExternalMCPConfig,
    InventoryMCPConfig,
    MCPClientConfig,
)
from inventory_assistant.inventory.bootstrap import initialize_database
from inventory_assistant.llm import (
    ConversationMessage,
    LLMResponse,
    LLMTool,
    TextBlock,
    ToolUseBlock,
)
from inventory_assistant.mcp.client import LocalMCPClient, MCPRemoteError
from inventory_assistant.mcp.manager import (
    MCPServerDefinition,
    MCPServerManager,
    MCPServerManagerError,
    configured_server_definitions,
)


def tool(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"Fake {name} tool.",
        "inputSchema": {"type": "object", "properties": {}},
    }


class FakeManagedClient:
    def __init__(self, name: str, tools: list[dict[str, Any]]) -> None:
        self.name = name
        self.tools = tools
        self.is_connected = False
        self.connect_count = 0
        self.close_count = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_connect = False
        self.fail_close = False

    def connect(self) -> None:
        self.connect_count += 1
        if self.fail_connect:
            raise RuntimeError("startup failure")
        self.is_connected = True

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        return self.tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        return {
            "isError": False,
            "structuredContent": {"server": self.name, "tool": name},
        }

    def close(self) -> None:
        self.close_count += 1
        self.is_connected = False
        if self.fail_close:
            raise RuntimeError("shutdown failure")


class ScriptedProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[ConversationMessage, ...]] = []

    def generate(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[LLMTool],
        *,
        system_prompt: str,
    ) -> LLMResponse:
        self.calls.append(tuple(messages))
        return self.responses.pop(0)


class MCPServerManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.config = MCPClientConfig(1.0, root / "mcp.jsonl")
        self.clients = {
            "inventory": FakeManagedClient("inventory", [tool("status")]),
            "filesystem": FakeManagedClient(
                "filesystem", [tool("status"), tool("write_file")]
            ),
            "git": FakeManagedClient("git", [tool("status"), tool("git_commit")]),
        }
        definitions = tuple(
            MCPServerDefinition(
                name=name,
                command=("fake", name),
                working_directory=root,
                instructions=f"Restricted {name} scope",
            )
            for name in self.clients
        )
        self.manager = MCPServerManager(
            self.config,
            definitions,
            client_factory=lambda definition, config: self.clients[definition.name],
        )

    def tearDown(self) -> None:
        self.manager.close()
        self.temporary_directory.cleanup()

    def test_registers_multiple_servers_and_discovers_tools_per_server(self) -> None:
        self.manager.connect()
        statuses = {status.name: status for status in self.manager.statuses}
        self.assertEqual(set(statuses), {"inventory", "filesystem", "git"})
        self.assertTrue(all(status.connected for status in statuses.values()))
        self.assertEqual(statuses["filesystem"].tools, ("status", "write_file"))
        self.assertEqual(len(self.manager.list_tools()), 5)

    def test_inventory_transport_selection_changes_only_its_definition(self) -> None:
        root = Path(self.temporary_directory.name)
        external = ExternalMCPConfig(
            filesystem_enabled=False,
            filesystem_command=("filesystem",),
            git_enabled=False,
            git_command=("git",),
            demo_root=root / "demo",
            git_repository=root / "demo" / "repository",
        )
        inventory = InventoryMCPConfig(
            transport="http",
            url="http://127.0.0.1:8123/mcp",
            http_host="127.0.0.1",
            http_port=8123,
        )
        definitions = configured_server_definitions(
            external,
            project_root=root,
            inventory_config=inventory,
        )
        self.assertEqual(len(definitions), 1)
        self.assertEqual(definitions[0].name, "inventory")
        self.assertEqual(definitions[0].transport, "http")
        self.assertEqual(definitions[0].url, inventory.url)
        self.assertEqual(definitions[0].command, ())

    def test_namespaces_repeated_tool_names_and_routes_to_original_names(self) -> None:
        self.manager.connect()
        names = [item["name"] for item in self.manager.list_tools()]
        self.assertIn("inventory__status", names)
        self.assertIn("filesystem__status", names)
        self.assertIn("git__status", names)

        result = self.manager.call_tool("git__status", {"repo_path": "demo"})
        self.assertEqual(result["structuredContent"]["server"], "git")
        self.assertEqual(
            self.clients["git"].calls,
            [("status", {"repo_path": "demo"})],
        )
        self.assertEqual(self.clients["inventory"].calls, [])

    def test_disconnected_server_is_reported_without_misrouting(self) -> None:
        self.manager.connect()
        self.clients["filesystem"].is_connected = False
        statuses = {status.name: status for status in self.manager.statuses}
        self.assertFalse(statuses["filesystem"].connected)
        with self.assertRaises(MCPRemoteError) as context:
            self.manager.call_tool("filesystem__write_file", {})
        self.assertIn("disconnected", context.exception.message)
        self.assertEqual(self.clients["git"].calls, [])

    def test_close_attempts_every_server_even_if_one_close_fails(self) -> None:
        self.manager.connect()
        self.clients["git"].fail_close = True
        self.manager.close()
        self.assertTrue(all(client.close_count == 1 for client in self.clients.values()))
        self.assertTrue(all(not status.connected for status in self.manager.statuses))

    def test_startup_failure_closes_servers_already_connected(self) -> None:
        self.clients["filesystem"].fail_connect = True
        with self.assertRaises(MCPServerManagerError):
            self.manager.connect()
        self.assertEqual(self.clients["inventory"].close_count, 1)
        self.assertFalse(self.clients["inventory"].is_connected)
        self.assertEqual(self.clients["git"].connect_count, 0)

    def test_fake_llm_coordinates_inventory_filesystem_and_git_tools(self) -> None:
        self.manager.connect()
        provider = ScriptedProvider(
            [
                LLMResponse(
                    blocks=(
                        ToolUseBlock("call-1", "inventory__status", {}),
                        ToolUseBlock("call-2", "filesystem__write_file", {}),
                        ToolUseBlock("call-3", "git__git_commit", {}),
                    ),
                    stop_reason="tool_use",
                ),
                LLMResponse(
                    blocks=(TextBlock("Inventory checked and demo committed."),),
                    stop_reason="end_turn",
                ),
            ]
        )
        answer = ChatbotSession(provider, self.manager).ask("Complete the demo")
        self.assertEqual(answer, "Inventory checked and demo committed.")
        self.assertEqual(self.clients["inventory"].calls[0][0], "status")
        self.assertEqual(self.clients["filesystem"].calls[0][0], "write_file")
        self.assertEqual(self.clients["git"].calls[0][0], "git_commit")

    def test_real_inventory_server_operates_beside_other_servers(self) -> None:
        root = Path(self.temporary_directory.name)
        database_path = root / "inventory.db"
        initialize_database(database_path, include_seed=True)
        definitions = (
            MCPServerDefinition(
                name="inventory",
                command=(sys.executable, "-m", "inventory_assistant.mcp.stdio"),
                working_directory=Path.cwd(),
                environment={
                    "INVENTORY_DB_PATH": str(database_path),
                    "PYTHONPATH": str(Path.cwd() / "src"),
                },
            ),
            MCPServerDefinition(
                name="filesystem",
                command=("fake",),
                working_directory=root,
            ),
            MCPServerDefinition(
                name="git",
                command=("fake",),
                working_directory=root,
            ),
        )

        def factory(definition: MCPServerDefinition, config: MCPClientConfig):
            if definition.name == "inventory":
                return LocalMCPClient(
                    config,
                    server_name=definition.name,
                    command=definition.command,
                    environment={**os.environ, **(definition.environment or {})},
                    working_directory=definition.working_directory,
                )
            return self.clients[definition.name]

        manager = MCPServerManager(self.config, definitions, client_factory=factory)
        with manager:
            result = manager.call_tool(
                "inventory__get_low_stock_products", {"limit": 2}
            )
            manager.call_tool("filesystem__write_file", {})
            manager.call_tool("git__status", {})
        self.assertEqual(result["structuredContent"]["count"], 2)


if __name__ == "__main__":
    unittest.main()

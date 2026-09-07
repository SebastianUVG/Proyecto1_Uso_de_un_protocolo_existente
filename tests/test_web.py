"""Tests for the browser adapter without real OpenAI or MCP processes."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from inventory_assistant.llm import (
    LLMProviderError,
    LLMResponse,
    TextBlock,
    ToolUseBlock,
)
from inventory_assistant.config import MCPClientConfig
from inventory_assistant.inventory.bootstrap import initialize_database
from inventory_assistant.inventory.service import InventoryService
from inventory_assistant.inventory.sqlite import SQLiteInventoryRepository
from inventory_assistant.mcp.manager import (
    MCPServerDefinition,
    MCPServerManager,
    MCPServerStatus,
)
from inventory_assistant.web.app import SESSION_COOKIE, create_app
from inventory_assistant.web.runtime import WebRuntime


READ_TOOL = {
    "name": "inventory__get_low_stock_products",
    "description": "Return products below their minimum stock.",
    "inputSchema": {"type": "object", "properties": {}},
}
WRITE_TOOL = {
    "name": "inventory__record_inventory_entry",
    "description": "Record an inventory entry.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "sku": {"type": "string"},
            "quantity": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["sku", "quantity"],
    },
}


class ScriptedProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.histories = []

    def generate(self, messages, tools, *, system_prompt: str) -> LLMResponse:
        self.histories.append(tuple(messages))
        if not self.responses:
            raise AssertionError("No scripted LLM response remains")
        return self.responses.pop(0)


class ErrorProvider:
    def generate(self, messages, tools, *, system_prompt: str) -> LLMResponse:
        raise LLMProviderError("The configured LLM is temporarily unavailable")


class FakeManager:
    def __init__(self, *, transport: str = "stdio") -> None:
        self.transport = transport
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    @property
    def statuses(self):
        return (
            MCPServerStatus(
                "inventory",
                self.transport,
                True,
                (READ_TOOL["name"], WRITE_TOOL["name"]),
            ),
        )

    def list_tools(self, *, refresh: bool = False):
        return [READ_TOOL, WRITE_TOOL]

    def call_tool(self, name: str, arguments: dict):
        copied = json.loads(json.dumps(arguments))
        self.calls.append((name, copied))
        if name == READ_TOOL["name"]:
            return {
                "structuredContent": {
                    "products": [
                        {"name": "Wireless Mouse", "stock": 3, "minimum": 8}
                    ]
                }
            }
        return {
            "structuredContent": {
                "product": {"sku": arguments.get("sku"), "current_stock": 8}
            }
        }

    def close(self) -> None:
        self.closed = True


def response(*blocks) -> LLMResponse:
    return LLMResponse(tuple(blocks), "stop")


class WebInterfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / "data"
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "test-web-mcp.jsonl"
        self.database_path = self.root / "test-web-integration.db"
        self.log_path.unlink(missing_ok=True)
        self.database_path.unlink(missing_ok=True)

    def tearDown(self) -> None:
        self.log_path.unlink(missing_ok=True)
        self.database_path.unlink(missing_ok=True)

    def make_client(self, provider, *, transport: str = "stdio"):
        manager = FakeManager(transport=transport)
        runtime = WebRuntime(
            provider,
            manager,
            log_path=self.log_path,
            configured_servers=("inventory",),
        )
        client = TestClient(create_app(lambda: runtime))
        return client, runtime, manager

    def initialize(self, client: TestClient):
        result = client.post("/api/session")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["messages"], [])
        self.assertIn(SESSION_COOKIE, client.cookies)
        return result

    def test_static_interface_loads_and_uses_safe_dom_markdown_renderer(self) -> None:
        provider = ScriptedProvider([])
        client, _, _ = self.make_client(provider)
        with client:
            page = client.get("/")
            script = client.get("/static/app.js")
            stylesheet = client.get("/static/styles.css")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Inventory Assistant", page.text)
        self.assertEqual(script.status_code, 200)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertNotIn("innerHTML", script.text)
        self.assertIn('document.createElement("table")', script.text)
        self.assertIn("renderMarkdown", script.text)

    def test_health_endpoint_requires_a_ready_runtime(self) -> None:
        provider = ScriptedProvider([])
        client, _, _ = self.make_client(provider)
        with client:
            result = client.get("/health")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"status": "healthy"})

    def test_session_message_markdown_and_conversation_context(self) -> None:
        markdown = (
            "### Products requiring restocking\n\n"
            "| Product | Stock | Minimum |\n"
            "| --- | ---: | ---: |\n"
            "| Wireless Mouse | 3 | 8 |\n\n"
            "**Wireless Mouse** is the most urgent."
        )
        provider = ScriptedProvider(
            [response(TextBlock(markdown)), response(TextBlock("It is still the priority."))]
        )
        client, _, _ = self.make_client(provider)
        with client:
            self.initialize(client)
            first = client.post("/api/chat", json={"message": "What needs restocking?"})
            second = client.post("/api/chat", json={"message": "Which is most urgent?"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["messages"][-1]["content"], markdown)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(provider.histories), 2)
        self.assertEqual(len(provider.histories[0]), 1)
        self.assertEqual(len(provider.histories[1]), 3)
        self.assertEqual(provider.histories[1][0].content, "What needs restocking?")

    def test_new_chat_replaces_context_but_keeps_mcp_manager(self) -> None:
        provider = ScriptedProvider(
            [response(TextBlock("First answer")), response(TextBlock("Fresh answer"))]
        )
        client, _, manager = self.make_client(provider)
        with client:
            self.initialize(client)
            client.post("/api/chat", json={"message": "First question"})
            old_cookie = client.cookies[SESSION_COOKIE]
            reset = client.post("/api/session/new")
            new_cookie = client.cookies[SESSION_COOKIE]
            fresh = client.post("/api/chat", json={"message": "Fresh question"})
            self.assertFalse(manager.closed)

        self.assertEqual(reset.status_code, 200)
        self.assertEqual(reset.json()["messages"], [])
        self.assertNotEqual(old_cookie, new_cookie)
        self.assertEqual(fresh.status_code, 200)
        self.assertEqual(len(provider.histories[1]), 1)
        self.assertTrue(manager.closed)

    def test_pending_operation_confirm_uses_exact_arguments_once(self) -> None:
        arguments = {"sku": "ELEC-002", "quantity": 5, "reason": "Restock"}
        provider = ScriptedProvider(
            [
                response(ToolUseBlock("write-1", WRITE_TOOL["name"], arguments)),
                response(TextBlock("Entry recorded. Current stock: 8.")),
            ]
        )
        client, _, manager = self.make_client(provider)
        with client:
            self.initialize(client)
            pending = client.post(
                "/api/chat", json={"message": "Add five units to the mouse"}
            )
            self.assertEqual(manager.calls, [])
            confirmed = client.post("/api/confirm")
            repeated = client.post("/api/confirm")

        pending_data = pending.json()["pending_confirmation"]
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending_data["operations"][0]["tool"], "record_inventory_entry")
        self.assertEqual(pending_data["operations"][0]["arguments"], arguments)
        self.assertIn("add 5 units", pending_data["description"].lower())
        self.assertEqual(confirmed.status_code, 200)
        self.assertIsNone(confirmed.json()["pending_confirmation"])
        self.assertEqual(manager.calls, [(WRITE_TOOL["name"], arguments)])
        self.assertEqual(repeated.status_code, 409)

    def test_cancel_clears_pending_operation_without_calling_tool(self) -> None:
        provider = ScriptedProvider(
            [
                response(
                    ToolUseBlock(
                        "write-2",
                        WRITE_TOOL["name"],
                        {"sku": "ELEC-002", "quantity": 5},
                    )
                )
            ]
        )
        client, _, manager = self.make_client(provider)
        with client:
            self.initialize(client)
            client.post("/api/chat", json={"message": "Add five units"})
            cancelled = client.post("/api/cancel")
            repeated = client.post("/api/cancel")

        self.assertEqual(cancelled.status_code, 200)
        self.assertIsNone(cancelled.json()["pending_confirmation"])
        self.assertIn("No inventory changes", cancelled.json()["messages"][-1]["content"])
        self.assertEqual(manager.calls, [])
        self.assertEqual(repeated.status_code, 409)

    def test_mcp_status_reports_transport_and_disabled_servers(self) -> None:
        provider = ScriptedProvider([])
        client, _, _ = self.make_client(provider, transport="http")
        with client:
            self.initialize(client)
            result = client.get("/api/status")

        self.assertEqual(result.status_code, 200)
        statuses = {item["key"]: item for item in result.json()["servers"]}
        self.assertEqual(statuses["inventory"]["state"], "Connected")
        self.assertEqual(statuses["inventory"]["transport"], "http")
        self.assertEqual(statuses["filesystem"]["state"], "Disabled")
        self.assertEqual(statuses["git"]["state"], "Disabled")

    def test_missing_session_and_provider_errors_are_safe(self) -> None:
        client, _, _ = self.make_client(ErrorProvider())
        with client:
            missing = client.post("/api/chat", json={"message": "Hello"})
            self.initialize(client)
            failed = client.post("/api/chat", json={"message": "Hello"})

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(failed.status_code, 502)
        self.assertEqual(
            failed.json()["detail"],
            "The configured LLM is temporarily unavailable",
        )
        self.assertNotIn("Traceback", failed.text)

    def test_chat_is_rejected_while_confirmation_is_pending(self) -> None:
        provider = ScriptedProvider(
            [response(ToolUseBlock("write-3", WRITE_TOOL["name"], {"sku": "X", "quantity": 1}))]
        )
        client, _, manager = self.make_client(provider)
        with client:
            self.initialize(client)
            client.post("/api/chat", json={"message": "Add one"})
            result = client.post("/api/chat", json={"message": "Send twice"})

        self.assertEqual(result.status_code, 409)
        self.assertEqual(manager.calls, [])

    def test_logs_are_limited_and_secrets_are_redacted(self) -> None:
        log_path = self.log_path
        secret = "sk-secret-value"
        records = [
            {
                "timestamp": "2026-09-05T12:00:00Z",
                "server": "inventory",
                "transport": "stdio",
                "direction": "client",
                "method": "tools/list",
                "request_id": 1,
                "message": {
                    "headers": {"Authorization": f"Bearer {secret}"},
                    "OPENAI_API_KEY": secret,
                    "safe": "visible",
                },
            },
            {
                "timestamp": "2026-09-05T12:00:01Z",
                "server": "inventory",
                "transport": "stdio",
                "direction": "server",
                "method": "tools/list",
                "request_id": 1,
                "message": {"result": "ok"},
            },
        ]
        log_path.write_text(
            "\n".join(json.dumps(item) for item in records) + "\n",
            encoding="utf-8",
        )
        provider = ScriptedProvider([])
        manager = FakeManager()
        runtime = WebRuntime(
            provider,
            manager,
            log_path=log_path,
            configured_servers=("inventory",),
        )
        client = TestClient(create_app(lambda: runtime))
        with client:
            self.initialize(client)
            result = client.get("/api/logs?limit=1")
            all_logs = client.get("/api/logs?limit=10")

        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()["logs"]), 1)
        serialized = json.dumps(all_logs.json())
        self.assertNotIn(secret, serialized)
        self.assertIn("[REDACTED]", serialized)
        self.assertIn("visible", serialized)

    def test_real_stdio_inventory_read_cancel_confirm_persistence_and_logs(self) -> None:
        initialize_database(
            self.database_path,
            reset=True,
            include_seed=True,
        )
        definition = MCPServerDefinition(
            name="inventory",
            command=(sys.executable, "-m", "inventory_assistant.mcp.stdio"),
            working_directory=Path.cwd(),
            environment={"INVENTORY_DB_PATH": str(self.database_path)},
            instructions="Use this server for inventory facts and movements",
        )
        manager = MCPServerManager(
            MCPClientConfig(5.0, self.log_path),
            (definition,),
        )
        manager.connect()
        entry_arguments = {
            "sku": "ELEC-002",
            "quantity": 5,
            "reason": "Web integration test",
            "reference": "WEB-INTEGRATION-001",
        }
        provider = ScriptedProvider(
            [
                response(
                    ToolUseBlock(
                        "read-low",
                        "inventory__get_low_stock_products",
                        {"include_out_of_stock": True, "limit": 10},
                    )
                ),
                response(
                    TextBlock(
                        "### Restock priorities\n\n"
                        "| Product | Stock | Minimum |\n"
                        "| --- | ---: | ---: |\n"
                        "| Mechanical Keyboard | 3 | 8 |"
                    )
                ),
                response(
                    ToolUseBlock(
                        "cancel-entry",
                        "inventory__record_inventory_entry",
                        entry_arguments,
                    )
                ),
                response(
                    ToolUseBlock(
                        "confirm-entry",
                        "inventory__record_inventory_entry",
                        entry_arguments,
                    )
                ),
                response(TextBlock("The five-unit entry was recorded.")),
                response(
                    ToolUseBlock(
                        "read-stock",
                        "inventory__get_product_stock",
                        {"sku": "ELEC-002"},
                    )
                ),
                response(TextBlock("Mechanical Keyboard now has **8 units**.")),
            ]
        )
        runtime = WebRuntime(
            provider,
            manager,
            log_path=self.log_path,
            configured_servers=("inventory",),
        )
        service = InventoryService(SQLiteInventoryRepository(self.database_path))

        with TestClient(create_app(lambda: runtime)) as client:
            self.initialize(client)

            read = client.post(
                "/api/chat", json={"message": "What products need restocking?"}
            )
            self.assertEqual(read.status_code, 200)
            self.assertIn("| Product | Stock | Minimum |", read.json()["messages"][-1]["content"])

            pending_cancel = client.post(
                "/api/chat", json={"message": "Add five units to that product"}
            )
            self.assertIsNotNone(pending_cancel.json()["pending_confirmation"])
            cancelled = client.post("/api/cancel")
            self.assertEqual(cancelled.status_code, 200)
            self.assertEqual(
                service.get_product_stock(sku="ELEC-002").product.current_stock,
                3,
            )

            pending_confirm = client.post(
                "/api/chat", json={"message": "Add five units after all"}
            )
            self.assertEqual(
                pending_confirm.json()["pending_confirmation"]["operations"][0]["arguments"],
                entry_arguments,
            )
            confirmed = client.post("/api/confirm")
            self.assertEqual(confirmed.status_code, 200)
            self.assertEqual(
                service.get_product_stock(sku="ELEC-002").product.current_stock,
                8,
            )

            current = client.post(
                "/api/chat", json={"message": "How many units does it have now?"}
            )
            self.assertIn("**8 units**", current.json()["messages"][-1]["content"])
            logs = client.get("/api/logs?limit=200")
            methods = {record["method"] for record in logs.json()["logs"]}

        self.assertIn("tools/list", methods)
        self.assertIn("tools/call", methods)
        self.assertGreaterEqual(len(provider.histories), 7)


if __name__ == "__main__":
    unittest.main()

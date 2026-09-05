"""Tests for MCP lifecycle, tool discovery, and tool dispatch."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from inventory_assistant.inventory.bootstrap import initialize_database
from inventory_assistant.inventory.service import InventoryService
from inventory_assistant.inventory.sqlite import SQLiteInventoryRepository
from inventory_assistant.mcp.jsonrpc import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    SERVER_NOT_INITIALIZED,
)
from inventory_assistant.mcp.logging import MCPInteractionLogger
from inventory_assistant.mcp.server import (
    MCP_PROTOCOL_VERSION,
    InventoryMCPServer,
    ServerState,
)
from inventory_assistant.mcp.tools import InventoryToolDispatcher


class MCPServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        test_data_root = Path.cwd() / "data" / "tests"
        test_data_root.mkdir(parents=True, exist_ok=True)
        cls.temporary_directory = tempfile.TemporaryDirectory(dir=test_data_root)
        cls.database_path = Path(cls.temporary_directory.name) / "mcp.db"
        initialize_database(cls.database_path, include_seed=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def setUp(self) -> None:
        repository = SQLiteInventoryRepository(self.database_path)
        service = InventoryService(repository)
        tools = InventoryToolDispatcher(service)
        self.log_stream = io.StringIO()
        self.server = InventoryMCPServer(
            tools,
            MCPInteractionLogger(self.log_stream),
        )

    def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        serialized = json.dumps(message, separators=(",", ":"))
        response = self.server.handle_line(serialized)
        return json.loads(response) if response is not None else None

    def initialize(self) -> dict[str, Any]:
        response = self.send(
            {
                "jsonrpc": "2.0",
                "id": "init-1",
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0.1.0"},
                },
            }
        )
        assert response is not None
        return response

    def make_ready(self) -> None:
        self.initialize()
        response = self.send(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        self.assertIsNone(response)
        self.assertEqual(self.server.state, ServerState.READY)

    def call_tool(
        self, name: str, arguments: dict[str, Any], request_id: int = 10
    ) -> dict[str, Any]:
        response = self.send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        assert response is not None
        return response

    def test_initialize_negotiates_version_and_tools_capability(self) -> None:
        response = self.initialize()
        self.assertEqual(response["id"], "init-1")
        self.assertEqual(response["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)
        self.assertEqual(
            response["result"]["capabilities"],
            {"tools": {"listChanged": False}},
        )
        self.assertEqual(self.server.state, ServerState.INITIALIZING)

    def test_initialized_notification_completes_lifecycle_without_response(self) -> None:
        self.make_ready()

    def test_ping_returns_empty_result(self) -> None:
        response = self.send({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(response, {"jsonrpc": "2.0", "id": 2, "result": {}})

    def test_tools_require_completed_initialization(self) -> None:
        response = self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(response["error"]["code"], SERVER_NOT_INITIALIZED)

    def test_tools_list_exposes_six_descriptive_schemas(self) -> None:
        self.make_ready()
        response = self.send(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
        )
        tools = response["result"]["tools"]
        self.assertEqual(len(tools), 6)
        self.assertEqual(
            {tool["name"] for tool in tools},
            {
                "get_product_stock",
                "get_low_stock_products",
                "get_restock_recommendations",
                "get_product_movements",
                "get_inactive_products",
                "get_product_movement_ranking",
            },
        )
        for tool in tools:
            self.assertTrue(tool["description"])
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_unknown_method_returns_method_not_found_and_preserves_id(self) -> None:
        response = self.send(
            {"jsonrpc": "2.0", "id": "unknown-4", "method": "missing/method"}
        )
        self.assertEqual(response["id"], "unknown-4")
        self.assertEqual(response["error"]["code"], METHOD_NOT_FOUND)

    def test_get_product_stock(self) -> None:
        self.make_ready()
        response = self.call_tool("get_product_stock", {"sku": "ELEC-001"})
        result = response["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["status"], "OUT_OF_STOCK")
        self.assertEqual(
            result["structuredContent"]["product"]["unit_price"], "24.99"
        )

    def test_get_low_stock_products(self) -> None:
        self.make_ready()
        response = self.call_tool("get_low_stock_products", {})
        self.assertEqual(response["result"]["structuredContent"]["count"], 6)

    def test_get_restock_recommendations(self) -> None:
        self.make_ready()
        response = self.call_tool("get_restock_recommendations", {"limit": 2})
        payload = response["result"]["structuredContent"]
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["recommendations"][0]["product"]["sku"], "WARE-003")

    def test_get_product_movements(self) -> None:
        self.make_ready()
        response = self.call_tool(
            "get_product_movements",
            {
                "sku": "ELEC-002",
                "date_from": "2026-08-01",
                "date_to": "2026-09-01",
                "movement_type": "OUT",
            },
        )
        payload = response["result"]["structuredContent"]
        self.assertEqual(payload["count"], 3)
        self.assertEqual(sum(item["quantity"] for item in payload["movements"]), 95)

    def test_get_inactive_products(self) -> None:
        self.make_ready()
        response = self.call_tool(
            "get_inactive_products",
            {"inactive_days": 90, "as_of": "2026-09-01"},
        )
        products = response["result"]["structuredContent"]["products"]
        self.assertEqual(
            {item["product"]["sku"] for item in products},
            {"OFF-002", "WARE-002", "FURN-001"},
        )

    def test_get_product_movement_ranking(self) -> None:
        self.make_ready()
        response = self.call_tool(
            "get_product_movement_ranking",
            {
                "date_from": "2026-08-01",
                "date_to": "2026-09-01",
                "movement_type": "OUT",
                "direction": "MOST",
                "metric": "UNITS",
                "limit": 3,
            },
        )
        ranking = response["result"]["structuredContent"]["products"]
        self.assertEqual(ranking[0]["product"]["sku"], "ELEC-002")
        self.assertEqual(ranking[0]["total_units"], 95)

    def test_unknown_tool_is_protocol_error(self) -> None:
        self.make_ready()
        response = self.call_tool("run_sql", {"query": "SELECT * FROM products"})
        self.assertEqual(response["error"]["code"], INVALID_PARAMS)
        self.assertEqual(response["error"]["data"]["type"], "TOOL_NOT_FOUND")

    def test_invalid_tool_arguments_are_protocol_error(self) -> None:
        self.make_ready()
        response = self.call_tool(
            "get_product_stock", {"sku": "ELEC-001", "product_id": 1}
        )
        self.assertEqual(response["error"]["code"], INVALID_PARAMS)
        self.assertEqual(
            response["error"]["data"]["type"], "TOOL_ARGUMENT_ERROR"
        )

    def test_missing_product_is_structured_tool_execution_error(self) -> None:
        self.make_ready()
        response = self.call_tool("get_product_stock", {"sku": "DOES-NOT-EXIST"})
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["error"]["type"],
            "PRODUCT_NOT_FOUND",
        )

    def test_invalid_tools_call_params(self) -> None:
        self.make_ready()
        response = self.send(
            {
                "jsonrpc": "2.0",
                "id": 30,
                "method": "tools/call",
                "params": {"name": "get_low_stock_products", "arguments": []},
            }
        )
        self.assertEqual(response["error"]["code"], INVALID_PARAMS)

    def test_logs_requests_and_responses_with_direction_and_id(self) -> None:
        self.send({"jsonrpc": "2.0", "id": 88, "method": "ping"})
        records = [
            json.loads(line) for line in self.log_stream.getvalue().splitlines()
        ]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["direction"], "client -> server")
        self.assertEqual(records[1]["direction"], "server -> client")
        self.assertEqual(records[1]["method"], "ping")
        self.assertEqual(records[1]["request_id"], 88)

    def test_invalid_notification_does_not_produce_a_response(self) -> None:
        response = self.server.handle_line(
            '{"jsonrpc":"2.0","method":"notifications/test","params":[]}'
        )
        self.assertIsNone(response)
        records = [
            json.loads(line) for line in self.log_stream.getvalue().splitlines()
        ]
        self.assertEqual(len(records), 1)
        self.assertEqual(
            records[0]["message"]["method"], "notifications/test"
        )


if __name__ == "__main__":
    unittest.main()

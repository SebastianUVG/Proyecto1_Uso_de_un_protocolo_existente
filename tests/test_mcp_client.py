"""Integration tests for the manual local MCP client."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from inventory_assistant.config import MCPClientConfig
from inventory_assistant.inventory.bootstrap import initialize_database
from inventory_assistant.mcp.client import (
    LocalMCPClient,
    MCPClientError,
    MCPClientTimeoutError,
    MCPServerProcessError,
)


class LocalMCPClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        test_data_root = Path.cwd() / "data" / "tests"
        test_data_root.mkdir(parents=True, exist_ok=True)
        cls.temporary_directory = tempfile.TemporaryDirectory(dir=test_data_root)
        cls.temp_path = Path(cls.temporary_directory.name)
        cls.database_path = cls.temp_path / "client.db"
        initialize_database(cls.database_path, include_seed=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary_directory.cleanup()

    def setUp(self) -> None:
        self.clients: list[LocalMCPClient] = []

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()

    def make_client(
        self,
        *,
        command: list[str] | None = None,
        timeout: float = 2.0,
    ) -> LocalMCPClient:
        log_path = self.temp_path / (
            f"{self._testMethodName}-{len(self.clients)}.jsonl"
        )
        environment = {
            "INVENTORY_DB_PATH": str(self.database_path),
            "PYTHONPATH": str(Path.cwd() / "src"),
        }
        client = LocalMCPClient(
            MCPClientConfig(
                request_timeout_seconds=timeout,
                log_path=log_path,
            ),
            command=command,
            environment=environment,
            working_directory=Path.cwd(),
        )
        self.clients.append(client)
        return client

    def test_connect_initializes_and_discovers_tools(self) -> None:
        client = self.make_client()
        client.connect()
        self.assertTrue(client.is_connected)
        self.assertEqual(client.server_info["name"], "inventory-mcp-server")
        self.assertEqual(len(client.list_tools()), 12)

    def test_ping_and_tools_call_use_real_server(self) -> None:
        client = self.make_client()
        client.connect()
        client.ping()
        result = client.call_tool("get_low_stock_products", {"limit": 2})
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["count"], 2)

    def test_request_ids_are_unique_and_correlated_in_logs(self) -> None:
        client = self.make_client()
        client.connect()
        client.ping()
        client.call_tool("get_product_stock", {"sku": "ELEC-001"})
        client.close()

        records = [
            json.loads(line)
            for line in client.log_path.read_text(encoding="utf-8").splitlines()
        ]
        requests = [
            record
            for record in records
            if record["direction"] == "client -> server"
        ]
        responses = [
            record
            for record in records
            if record["direction"] == "server -> client"
        ]
        self.assertEqual(
            [record["request_id"] for record in requests],
            [1, None, 2, 3, 4],
        )
        self.assertEqual(
            [record["request_id"] for record in responses],
            [1, 2, 3, 4],
        )
        self.assertTrue(all(record["server"] == "inventory-mcp-server" for record in records))

    def test_close_stops_server_and_rejects_new_requests(self) -> None:
        client = self.make_client()
        client.connect()
        client.close()
        self.assertFalse(client.is_connected)
        with self.assertRaises(MCPClientError):
            client.ping()

    def test_reports_server_that_terminates_during_startup(self) -> None:
        client = self.make_client(
            command=[sys.executable, "-c", "raise SystemExit(3)"],
            timeout=1.0,
        )
        with self.assertRaises(MCPServerProcessError):
            client.connect()

    def test_request_timeout_is_bounded(self) -> None:
        client = self.make_client(
            command=[
                sys.executable,
                "-c",
                "import time; time.sleep(5)",
            ],
            timeout=0.1,
        )
        with self.assertRaises(MCPClientTimeoutError):
            client.connect()


if __name__ == "__main__":
    unittest.main()

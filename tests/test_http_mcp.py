"""Integration tests for the manual Streamable HTTP MCP transport."""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import time
import unittest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from unittest.mock import patch

from inventory_assistant.chatbot.session import ChatbotSession
from inventory_assistant.config import MCPClientConfig
from inventory_assistant.inventory.bootstrap import initialize_database
from inventory_assistant.inventory.service import InventoryService
from inventory_assistant.inventory.sqlite import SQLiteInventoryRepository
from inventory_assistant.mcp.client import LocalMCPClient, MCPClientTimeoutError
from inventory_assistant.mcp.http_client import HTTPMCPClient, MCPHTTPTransportError
from inventory_assistant.mcp.http_server import (
    create_http_server,
)
from inventory_assistant.mcp.logging import MCPInteractionLogger
from inventory_assistant.mcp.protocol import (
    MCP_PROTOCOL_VERSION,
    MCP_SESSION_HEADER,
    MCP_VERSION_HEADER,
)
from inventory_assistant.mcp.server import InventoryMCPServer
from inventory_assistant.mcp.tools import InventoryToolDispatcher
from inventory_assistant.llm import LLMResponse, TextBlock, ToolUseBlock


class ScriptedHTTPProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.histories = []

    def generate(self, messages, tools, *, system_prompt: str) -> LLMResponse:
        self.histories.append(tuple(messages))
        return self.responses.pop(0)


class HTTPMCPIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "http.db"
        initialize_database(self.database_path, include_seed=True)
        self.server_log = io.StringIO()

        def server_factory() -> InventoryMCPServer:
            service = InventoryService(SQLiteInventoryRepository(self.database_path))
            return InventoryMCPServer(
                InventoryToolDispatcher(service),
                MCPInteractionLogger(
                    self.server_log,
                    server="inventory",
                    transport="http",
                ),
            )

        self.server = create_http_server(
            "127.0.0.1", 0, server_factory=server_factory
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address[:2]
        self.url = f"http://{host}:{port}/mcp"
        self.clients: list[HTTPMCPClient] = []

    def tearDown(self) -> None:
        for client in self.clients:
            client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def make_client(self, *, timeout: float = 2.0) -> HTTPMCPClient:
        client = HTTPMCPClient(
            MCPClientConfig(timeout, self.root / f"client-{len(self.clients)}.jsonl"),
            self.url,
            server_name="inventory",
        )
        self.clients.append(client)
        return client

    def test_endpoint_uses_post_and_declines_optional_get_stream(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            urlopen(Request(self.url, method="GET"), timeout=2)
        self.assertEqual(raised.exception.code, HTTPStatus.METHOD_NOT_ALLOWED)
        self.assertEqual(raised.exception.headers["Allow"], "POST, DELETE")

    def test_client_initializes_pings_and_discovers_exactly_twelve_tools(self) -> None:
        client = self.make_client()
        client.connect()
        client.ping()
        self.assertTrue(client.is_connected)
        self.assertEqual(client.server_info["name"], "inventory-mcp-server")
        self.assertEqual(len(client.list_tools()), 12)

    def test_lifecycle_rejects_tools_before_initialized_notification(self) -> None:
        status, initialize, headers = self.raw_post(
            {
                "jsonrpc": "2.0",
                "id": "init-1",
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "raw-test", "version": "1.0"},
                },
            }
        )
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(initialize["id"], "init-1")
        session_id = headers[MCP_SESSION_HEADER]
        status, response, _ = self.raw_post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            session_id=session_id,
        )
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(response["error"]["code"], -32002)

    def test_read_and_write_calls_persist_in_sqlite(self) -> None:
        client = self.make_client()
        client.connect()
        low_stock = client.call_tool("get_low_stock_products", {"limit": 2})
        self.assertEqual(low_stock["structuredContent"]["count"], 2)

        created = client.call_tool(
            "add_product",
            {
                "sku": "HTTP-001",
                "name": "HTTP demo product",
                "category": "Networking",
                "initial_stock": 7,
                "minimum_stock": 3,
                "target_stock": 12,
                "unit_price": 25,
            },
        )
        self.assertFalse(created["isError"])
        client.close()

        verification = self.make_client()
        verification.connect()
        stock = verification.call_tool("get_product_stock", {"sku": "HTTP-001"})
        self.assertEqual(stock["structuredContent"]["product"]["current_stock"], 7)

    def test_jsonrpc_errors_remain_jsonrpc_errors_over_http(self) -> None:
        session_id = self.ready_raw_session()
        status, response, _ = self.raw_post(
            {"jsonrpc": "2.0", "id": 77, "method": "unknown/method"},
            session_id=session_id,
        )
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(response["id"], 77)
        self.assertEqual(response["error"]["code"], -32601)

        status, response, _ = self.raw_post_bytes(b'{"jsonrpc":')
        self.assertEqual(status, HTTPStatus.OK)
        self.assertEqual(response["error"]["code"], -32700)

    def test_transport_errors_are_not_jsonrpc_errors(self) -> None:
        status, response, _ = self.raw_post(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        self.assertEqual(status, HTTPStatus.BAD_REQUEST)
        self.assertEqual(response["error"]["type"], "HTTP_TRANSPORT_ERROR")

        status, response, _ = self.raw_post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            },
            origin="https://attacker.example",
        )
        self.assertEqual(status, HTTPStatus.FORBIDDEN)
        self.assertEqual(response["error"]["type"], "HTTP_TRANSPORT_ERROR")

    def test_close_terminates_the_http_session(self) -> None:
        session_id = self.ready_raw_session()
        request = Request(
            self.url,
            method="DELETE",
            headers={
                MCP_SESSION_HEADER: session_id,
                MCP_VERSION_HEADER: MCP_PROTOCOL_VERSION,
            },
        )
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, HTTPStatus.NO_CONTENT)
        status, response, _ = self.raw_post(
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
            session_id=session_id,
        )
        self.assertEqual(status, HTTPStatus.NOT_FOUND)
        self.assertEqual(response["error"]["type"], "HTTP_TRANSPORT_ERROR")

    def test_request_ids_and_http_transport_are_logged(self) -> None:
        client = self.make_client()
        client.connect()
        client.ping()
        client.call_tool("get_product_stock", {"sku": "ELEC-001"})
        records = [
            json.loads(line)
            for line in client.log_path.read_text(encoding="utf-8").splitlines()
        ]
        requests = [r for r in records if r["direction"] == "client -> server"]
        responses = [r for r in records if r["direction"] == "server -> client"]
        self.assertEqual([r["request_id"] for r in requests], [1, None, 2, 3, 4])
        self.assertEqual([r["request_id"] for r in responses], [1, 2, 3, 4])
        self.assertTrue(all(r["server"] == "inventory" for r in records))
        self.assertTrue(all(r["transport"] == "http" for r in records))
        server_records = [
            json.loads(line) for line in self.server_log.getvalue().splitlines()
        ]
        self.assertTrue(all(r["transport"] == "http" for r in server_records))

    def test_stdio_and_http_return_equivalent_structured_results(self) -> None:
        stdio_database = self.root / "stdio.db"
        initialize_database(stdio_database, include_seed=True)
        environment = os.environ.copy()
        environment.update(
            {
                "INVENTORY_DB_PATH": str(stdio_database),
                "PYTHONPATH": str(Path.cwd() / "src"),
            }
        )
        stdio = LocalMCPClient(
            MCPClientConfig(2.0, self.root / "stdio.jsonl"),
            environment=environment,
            working_directory=Path.cwd(),
        )
        with stdio:
            expected_tools = stdio.list_tools()
            expected = stdio.call_tool(
                "get_restock_recommendations", {"limit": 3}
            )
        http = self.make_client()
        http.connect()
        self.assertEqual(http.list_tools(), expected_tools)
        actual = http.call_tool("get_restock_recommendations", {"limit": 3})
        self.assertEqual(actual, expected)

    def test_fake_llm_uses_http_context_confirmation_and_persistence(self) -> None:
        client = self.make_client()
        client.connect()
        provider = ScriptedHTTPProvider(
            [
                LLMResponse(
                    (ToolUseBlock("low-1", "get_low_stock_products", {"limit": 2}),),
                    "tool_use",
                ),
                LLMResponse((TextBlock("Two products need restocking."),), "end_turn"),
                LLMResponse(
                    (
                        ToolUseBlock(
                            "entry-1",
                            "record_inventory_entry",
                            {"sku": "ELEC-002", "quantity": 5},
                        ),
                    ),
                    "tool_use",
                ),
                LLMResponse((TextBlock("The confirmed entry was recorded."),), "end_turn"),
                LLMResponse(
                    (ToolUseBlock("stock-1", "get_product_stock", {"sku": "ELEC-002"}),),
                    "tool_use",
                ),
                LLMResponse((TextBlock("The updated stock was retrieved."),), "end_turn"),
            ]
        )
        session = ChatbotSession(provider, client)
        self.assertEqual(
            session.ask("What products need restocking?"),
            "Two products need restocking.",
        )
        confirmation = session.ask("Add 5 units of ELEC-002.")
        self.assertIn("Confirm?", confirmation)
        self.assertEqual(session.ask("yes"), "The confirmed entry was recorded.")
        self.assertEqual(
            session.ask("How many units does it have now?"),
            "The updated stock was retrieved.",
        )
        stock = client.call_tool("get_product_stock", {"sku": "ELEC-002"})
        self.assertEqual(stock["structuredContent"]["product"]["current_stock"], 8)
        self.assertGreater(len(provider.histories[-1]), len(provider.histories[0]))

    def ready_raw_session(self) -> str:
        _, _, headers = self.raw_post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "raw-test", "version": "1.0"},
                },
            }
        )
        session_id = headers[MCP_SESSION_HEADER]
        status, response, _ = self.raw_post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=session_id,
        )
        self.assertEqual(status, HTTPStatus.ACCEPTED)
        self.assertIsNone(response)
        return session_id

    def raw_post(
        self,
        message: dict,
        *,
        session_id: str | None = None,
        origin: str | None = None,
    ):
        return self.raw_post_bytes(
            json.dumps(message, separators=(",", ":")).encode("utf-8"),
            session_id=session_id,
            origin=origin,
        )

    def raw_post_bytes(
        self,
        body: bytes,
        *,
        session_id: str | None = None,
        origin: str | None = None,
    ):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session_id:
            headers[MCP_SESSION_HEADER] = session_id
            headers[MCP_VERSION_HEADER] = MCP_PROTOCOL_VERSION
        if origin:
            headers["Origin"] = origin
        request = Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=2) as response:
                response_body = response.read()
                return (
                    response.status,
                    json.loads(response_body) if response_body else None,
                    response.headers,
                )
        except HTTPError as error:
            response_body = error.read()
            return (
                error.code,
                json.loads(response_body) if response_body else None,
                error.headers,
            )


class HTTPMCPFailureTests(unittest.TestCase):
    def test_unavailable_server_is_a_transport_error(self) -> None:
        client = HTTPMCPClient(
            MCPClientConfig(0.2, Path("logs/test-http-failure.jsonl")),
            "http://127.0.0.1:1/mcp",
        )
        with patch(
            "inventory_assistant.mcp.http_client.urlopen",
            side_effect=URLError(ConnectionRefusedError()),
        ):
            with self.assertRaises(MCPHTTPTransportError):
                client.connect()

    def test_timeout_is_bounded(self) -> None:
        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                time.sleep(0.3)
                self.send_response(HTTPStatus.ACCEPTED)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        client = HTTPMCPClient(
            MCPClientConfig(0.05, Path("logs/test-http-timeout.jsonl")),
            f"http://127.0.0.1:{server.server_port}/mcp",
        )
        try:
            with self.assertRaises(MCPClientTimeoutError):
                client.connect()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()

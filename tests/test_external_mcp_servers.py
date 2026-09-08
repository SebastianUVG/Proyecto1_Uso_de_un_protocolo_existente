"""Generic end-to-end tests for configured third-party MCP stdio servers."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inventory_assistant.config import (
    ExternalMCPConfig,
    InventoryMCPConfig,
    MCPClientConfig,
)
from inventory_assistant.mcp.manager import (
    MCPServerManager,
    configured_server_definitions,
)


class ExternalMCPServerEndToEndTests(unittest.TestCase):
    def test_config_discovery_routing_partial_failures_and_shutdown(self) -> None:
        project_root = Path.cwd()
        fixture = project_root / "tests" / "fixtures" / "fake_mcp_server.py"
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            config_path = temporary_root / "external.json"
            log_path = temporary_root / "mcp.jsonl"
            config_path.write_text(
                json.dumps(
                    {
                        "servers": [
                            self._server("academic-planner", fixture, ["shared", "plan"]),
                            self._server("hotel", fixture, ["shared", "availability"]),
                            {
                                "name": "broken-process",
                                "enabled": True,
                                "command": "missing-mcp-executable-for-test",
                                "args": [],
                                "cwd": ".",
                            },
                            self._server(
                                "broken-list", fixture, ["unused"], fail_list=True
                            ),
                            {
                                "name": "disabled-server",
                                "enabled": False,
                                "command": "missing-disabled-command",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"EXTERNAL_MCP_SERVERS_CONFIG": str(config_path)},
                clear=True,
            ):
                external = ExternalMCPConfig.from_env(project_root=project_root)
            all_definitions = configured_server_definitions(
                external,
                project_root=project_root,
                inventory_config=InventoryMCPConfig(
                    transport="stdio",
                    url="http://127.0.0.1:8000/mcp",
                    http_host="127.0.0.1",
                    http_port=8000,
                ),
            )
            definitions = tuple(
                definition
                for definition in all_definitions
                if definition.name != "inventory"
            )
            self.assertEqual(
                [definition.name for definition in definitions],
                ["academic-planner", "hotel", "broken-process", "broken-list"],
            )

            manager = MCPServerManager(
                MCPClientConfig(2.0, log_path), definitions
            )
            manager.connect()
            statuses = {status.name: status for status in manager.statuses}
            self.assertTrue(statuses["academic-planner"].connected)
            self.assertTrue(statuses["hotel"].connected)
            self.assertFalse(statuses["broken-process"].connected)
            self.assertIsNotNone(statuses["broken-process"].error)
            self.assertFalse(statuses["broken-list"].connected)
            self.assertIn("tools/list failed", statuses["broken-list"].error)

            names = [tool["name"] for tool in manager.list_tools()]
            self.assertEqual(
                names,
                [
                    "academic-planner__shared",
                    "academic-planner__plan",
                    "hotel__shared",
                    "hotel__availability",
                ],
            )
            result = manager.call_tool("hotel__shared", {"value": "Ada"})
            self.assertEqual(
                result["structuredContent"],
                {
                    "server": "hotel",
                    "tool": "shared",
                    "arguments": {"value": "Ada"},
                },
            )
            routes = {route.public_name: route for route in manager.routes}
            self.assertEqual(routes["hotel__shared"].server_name, "hotel")
            self.assertEqual(routes["hotel__shared"].original_name, "shared")

            manager.close()
            self.assertTrue(all(not status.connected for status in manager.statuses))
            records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(
                any(
                    record["server"] == "hotel"
                    and record["method"] == "tools/call"
                    for record in records
                )
            )

    @staticmethod
    def _server(
        name: str,
        fixture: Path,
        tools: list[str],
        *,
        fail_list: bool = False,
    ) -> dict[str, object]:
        return {
            "name": name,
            "enabled": True,
            "command": sys.executable,
            "args": [str(fixture)],
            "cwd": ".",
            "env": {
                "FAKE_MCP_NAME": name,
                "FAKE_MCP_TOOLS": json.dumps(tools),
                "FAKE_MCP_FAIL_LIST": str(fail_list).lower(),
            },
        }


if __name__ == "__main__":
    unittest.main()

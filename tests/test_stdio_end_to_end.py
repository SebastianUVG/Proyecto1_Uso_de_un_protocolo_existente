"""End-to-end test that launches the real MCP server over stdio."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from inventory_assistant.inventory.bootstrap import initialize_database


class StdioEndToEndTests(unittest.TestCase):
    def test_real_stdio_process_keeps_protocol_and_logs_separate(self) -> None:
        test_data_root = Path.cwd() / "data" / "tests"
        test_data_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_data_root) as temporary_directory:
            database_path = Path(temporary_directory) / "stdio.db"
            initialize_database(database_path, include_seed=True)
            environment = os.environ.copy()
            environment["INVENTORY_DB_PATH"] = str(database_path)
            messages = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "e2e-test", "version": "0.1.0"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "get_low_stock_products",
                        "arguments": {"limit": 1},
                    },
                },
            ]
            process_input = "".join(
                json.dumps(message, separators=(",", ":")) + "\n"
                for message in messages
            )
            completed = subprocess.run(
                [sys.executable, "-m", "inventory_assistant.mcp.stdio"],
                input=process_input,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=environment,
                timeout=10,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            protocol_messages = [
                json.loads(line) for line in completed.stdout.splitlines()
            ]
            log_messages = [
                json.loads(line) for line in completed.stderr.splitlines()
            ]
            self.assertEqual(len(protocol_messages), 3)
            self.assertEqual([item["id"] for item in protocol_messages], [1, 2, 3])
            self.assertEqual(
                protocol_messages[2]["result"]["structuredContent"]["count"], 1
            )
            self.assertEqual(len(log_messages), 7)
            self.assertTrue(
                all("direction" in record for record in log_messages)
            )
            self.assertNotIn("timestamp", protocol_messages[0])


if __name__ == "__main__":
    unittest.main()


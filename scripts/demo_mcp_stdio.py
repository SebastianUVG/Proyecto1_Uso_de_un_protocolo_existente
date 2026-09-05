"""Show a complete MCP handshake and inventory tool call over real stdio."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.setdefault(
        "INVENTORY_DB_PATH", str(project_root / "data" / "inventory.db")
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "inventory_assistant.mcp.stdio"],
        cwd=project_root,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    captured_logs: list[str] = []

    def collect_logs() -> None:
        captured_logs.extend(process.stderr.readlines())

    log_thread = threading.Thread(target=collect_logs, daemon=True)
    log_thread.start()

    def exchange(message: dict[str, Any], expect_response: bool = True) -> None:
        serialized = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        print(f"CLIENT -> SERVER\n{serialized}")
        process.stdin.write(serialized + "\n")
        process.stdin.flush()
        if expect_response:
            response = process.stdout.readline().rstrip("\r\n")
            print(f"SERVER -> CLIENT\n{response}")

    exchange(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "manual-demo", "version": "0.1.0"},
            },
        }
    )
    exchange(
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        expect_response=False,
    )
    exchange({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    exchange(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "get_low_stock_products",
                "arguments": {"limit": 3},
            },
        }
    )

    process.stdin.close()
    exit_code = process.wait(timeout=10)
    log_thread.join(timeout=2)
    print("SERVER STDERR LOGS")
    print("".join(captured_logs), end="")
    if exit_code != 0:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()

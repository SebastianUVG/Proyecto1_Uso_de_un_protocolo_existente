"""Minimal configurable MCP stdio process used only by host integration tests."""

from __future__ import annotations

import json
import os
import sys


PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = os.getenv("FAKE_MCP_NAME", "fake-external")
TOOL_NAMES = json.loads(os.getenv("FAKE_MCP_TOOLS", '["shared"]'))
FAIL_TOOLS_LIST = os.getenv("FAKE_MCP_FAIL_LIST", "false").casefold() == "true"


def _response(request_id: object, *, result=None, error=None) -> None:
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _response(None, error={"code": -32700, "message": "Parse error"})
            continue
        method = request.get("method")
        request_id = request.get("id")
        if method == "notifications/initialized":
            continue
        if method == "initialize":
            _response(
                request_id,
                result={
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": "1.0"},
                },
            )
        elif method == "tools/list":
            if FAIL_TOOLS_LIST:
                _response(
                    request_id,
                    error={"code": -32000, "message": "tools/list failed"},
                )
                continue
            _response(
                request_id,
                result={
                    "tools": [
                        {
                            "name": name,
                            "description": f"Dynamic tool {name} from {SERVER_NAME}.",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "additionalProperties": False,
                            },
                        }
                        for name in TOOL_NAMES
                    ]
                },
            )
        elif method == "tools/call":
            params = request.get("params", {})
            payload = {
                "server": SERVER_NAME,
                "tool": params.get("name"),
                "arguments": params.get("arguments", {}),
            }
            _response(
                request_id,
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(payload, separators=(",", ":")),
                        }
                    ],
                    "structuredContent": payload,
                    "isError": False,
                },
            )
        else:
            _response(
                request_id,
                error={"code": -32601, "message": "Method not found"},
            )


if __name__ == "__main__":
    main()

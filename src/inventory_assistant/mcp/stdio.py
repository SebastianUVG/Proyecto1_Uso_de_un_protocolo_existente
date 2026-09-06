"""Newline-delimited stdio transport for the local Inventory MCP Server."""

from __future__ import annotations

import sys
from typing import TextIO

from .factory import build_inventory_server
from .server import InventoryMCPServer


def build_server(error_stream: TextIO | None = None) -> InventoryMCPServer:
    return build_inventory_server(transport="stdio", log_stream=error_stream)


def run_stdio(
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
) -> None:
    """Read one request per line and write only protocol responses to stdout."""

    source = input_stream or sys.stdin
    destination = output_stream or sys.stdout
    server = build_server(error_stream)
    for line in source:
        serialized = line.rstrip("\r\n")
        if not serialized:
            continue
        response = server.handle_line(serialized)
        if response is not None:
            destination.write(response + "\n")
            destination.flush()


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    run_stdio()


if __name__ == "__main__":
    main()

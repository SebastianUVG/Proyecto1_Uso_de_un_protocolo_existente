"""Manual MCP server implementation built on JSON-RPC 2.0."""

from .server import InventoryMCPServer, MCP_PROTOCOL_VERSION

__all__ = ["InventoryMCPServer", "MCP_PROTOCOL_VERSION"]


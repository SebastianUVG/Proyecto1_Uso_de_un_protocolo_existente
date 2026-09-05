"""Manual MCP client and server implementation built on JSON-RPC 2.0."""

from .client import LocalMCPClient
from .manager import MCPServerDefinition, MCPServerManager
from .server import InventoryMCPServer, MCP_PROTOCOL_VERSION

__all__ = [
    "InventoryMCPServer",
    "LocalMCPClient",
    "MCP_PROTOCOL_VERSION",
    "MCPServerDefinition",
    "MCPServerManager",
]

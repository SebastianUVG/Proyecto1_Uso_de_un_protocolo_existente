"""Composition root shared by the Inventory MCP transports."""

from __future__ import annotations

from typing import TextIO

from inventory_assistant.config import DatabaseConfig
from inventory_assistant.inventory.service import InventoryService
from inventory_assistant.inventory.sqlite import SQLiteInventoryRepository

from .logging import MCPInteractionLogger
from .server import InventoryMCPServer
from .tools import InventoryToolDispatcher


def build_inventory_server(
    *,
    transport: str,
    log_stream: TextIO | None = None,
) -> InventoryMCPServer:
    """Build the same MCP core regardless of the selected wire transport."""

    database = DatabaseConfig.from_env()
    repository = SQLiteInventoryRepository(database.path)
    service = InventoryService(repository)
    tools = InventoryToolDispatcher(service)
    logger = MCPInteractionLogger(
        log_stream,
        server="inventory",
        transport=transport,
    )
    return InventoryMCPServer(tools, logger)

"""Minimal Inventory MCP server core for the 2025-06-18 protocol revision."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from .jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    SERVER_NOT_INITIALIZED,
    JSONRPCProtocolError,
    JSONRPCRequest,
    error_response,
    parse_request,
    serialize_message,
    success_response,
)
from .logging import MCPInteractionLogger
from .protocol import MCP_PROTOCOL_VERSION
from .tools import InventoryToolDispatcher


SERVER_NAME = "inventory-mcp-server"
SERVER_VERSION = "0.1.0"


class ServerState(StrEnum):
    NEW = "NEW"
    INITIALIZING = "INITIALIZING"
    READY = "READY"


class InventoryMCPServer:
    """Process JSON-RPC messages and dispatch the supported MCP methods."""

    def __init__(
        self,
        tool_dispatcher: InventoryToolDispatcher,
        logger: MCPInteractionLogger | None = None,
    ) -> None:
        self._tools = tool_dispatcher
        self._logger = logger or MCPInteractionLogger()
        self.state = ServerState.NEW

    def handle_line(self, serialized_request: str) -> str | None:
        """Handle one JSON-RPC line, returning no line for notifications."""

        try:
            request = parse_request(serialized_request)
        except JSONRPCProtocolError as error:
            if error.raw_message is None:
                self._logger.log_unparseable(
                    "client -> server", len(serialized_request)
                )
            else:
                self._logger.log_message(
                    "client -> server", error.raw_message
                )
            if error.is_notification:
                return None
            response = error_response(
                error.request_id,
                error.code,
                error.message,
                error.data,
            )
            method = None
            if error.raw_message is not None:
                raw_method = error.raw_message.get("method")
                method = raw_method if isinstance(raw_method, str) else None
            self._logger.log_message(
                "server -> client", response, method=method
            )
            return serialize_message(response)

        self._logger.log_message("client -> server", request.raw)
        try:
            result = self._dispatch(request)
            if request.is_notification:
                return None
            response = success_response(request.request_id, result)
        except JSONRPCProtocolError as error:
            if request.is_notification:
                return None
            response = error_response(
                request.request_id,
                error.code,
                error.message,
                error.data,
            )
        except Exception:
            self._logger.log_internal_error(request.method, request.request_id)
            if request.is_notification:
                return None
            response = error_response(
                request.request_id,
                INTERNAL_ERROR,
                "Internal error",
                {"type": "INTERNAL_ERROR"},
            )

        self._logger.log_message(
            "server -> client",
            response,
            method=request.method,
        )
        return serialize_message(response)

    def _dispatch(self, request: JSONRPCRequest) -> dict[str, Any]:
        if request.method == "initialize":
            return self._initialize(request)
        if request.method == "notifications/initialized":
            return self._initialized(request)
        if request.method == "ping":
            return self._ping(request.params)
        if request.method == "tools/list":
            self._require_ready()
            return self._list_tools(request.params)
        if request.method == "tools/call":
            self._require_ready()
            return self._call_tool(request.params)
        raise JSONRPCProtocolError(
            METHOD_NOT_FOUND,
            "Method not found",
            data={"method": request.method},
        )

    def _initialize(self, request: JSONRPCRequest) -> dict[str, Any]:
        if request.is_notification:
            raise JSONRPCProtocolError(
                INVALID_REQUEST,
                "initialize must be a request",
                data={"type": "MCP_LIFECYCLE_ERROR"},
            )
        if self.state is not ServerState.NEW:
            raise JSONRPCProtocolError(
                INVALID_REQUEST,
                "Server is already initialized",
                data={"type": "MCP_LIFECYCLE_ERROR"},
            )
        params = request.params
        required = {"protocolVersion", "capabilities", "clientInfo"}
        missing = required - set(params)
        if missing:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Invalid initialize params",
                data={"missing": sorted(missing)},
            )
        protocol_version = params["protocolVersion"]
        capabilities = params["capabilities"]
        client_info = params["clientInfo"]
        if not isinstance(protocol_version, str):
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Invalid initialize params",
                data={"reason": "protocolVersion must be a string"},
            )
        if not isinstance(capabilities, dict):
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Invalid initialize params",
                data={"reason": "capabilities must be an object"},
            )
        if not _valid_implementation_info(client_info):
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Invalid initialize params",
                data={
                    "reason": "clientInfo must contain string name and version"
                },
            )
        if protocol_version != MCP_PROTOCOL_VERSION:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Unsupported protocol version",
                data={
                    "supported": [MCP_PROTOCOL_VERSION],
                    "requested": protocol_version,
                },
            )

        self.state = ServerState.INITIALIZING
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Use the inventory tools to query product stock and movement data. "
                "This server exposes read-only demonstration inventory operations."
            ),
        }

    def _initialized(self, request: JSONRPCRequest) -> dict[str, Any]:
        if not request.is_notification:
            raise JSONRPCProtocolError(
                INVALID_REQUEST,
                "notifications/initialized must not include an id",
                data={"type": "MCP_LIFECYCLE_ERROR"},
            )
        if request.params:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "notifications/initialized does not accept params",
            )
        if self.state is not ServerState.INITIALIZING:
            raise JSONRPCProtocolError(
                INVALID_REQUEST,
                "initialize must complete before notifications/initialized",
                data={"type": "MCP_LIFECYCLE_ERROR"},
            )
        self.state = ServerState.READY
        return {}

    @staticmethod
    def _ping(params: dict[str, Any]) -> dict[str, Any]:
        if params:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "ping does not accept params",
            )
        return {}

    def _list_tools(self, params: dict[str, Any]) -> dict[str, Any]:
        unknown = set(params) - {"cursor", "_meta"}
        if unknown:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Invalid tools/list params",
                data={"unknown": sorted(unknown)},
            )
        cursor = params.get("cursor")
        if cursor is not None:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Pagination cursor is not supported by this fixed tool catalog",
            )
        return {"tools": self._tools.list_tools()}

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        unknown = set(params) - {"name", "arguments", "_meta"}
        if unknown:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Invalid tools/call params",
                data={"unknown": sorted(unknown)},
            )
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not name:
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Invalid tools/call params",
                data={"reason": "name must be a non-empty string"},
            )
        if not isinstance(arguments, dict):
            raise JSONRPCProtocolError(
                INVALID_PARAMS,
                "Invalid tools/call params",
                data={"reason": "arguments must be an object"},
            )
        return self._tools.call(name, arguments)

    def _require_ready(self) -> None:
        if self.state is not ServerState.READY:
            raise JSONRPCProtocolError(
                SERVER_NOT_INITIALIZED,
                "Server not initialized",
                data={"type": "MCP_LIFECYCLE_ERROR", "state": self.state.value},
            )


def _valid_implementation_info(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    name = value.get("name")
    version = value.get("version")
    return (
        isinstance(name, str)
        and bool(name)
        and isinstance(version, str)
        and bool(version)
    )

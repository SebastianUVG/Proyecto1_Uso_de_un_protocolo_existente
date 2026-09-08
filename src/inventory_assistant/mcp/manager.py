"""Host-side manager for MCP servers using configurable transports."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from inventory_assistant.config import (
    ConfigurationError,
    ExternalMCPConfig,
    InventoryMCPConfig,
    MCPClientConfig,
)

from .client import LocalMCPClient, MCPClientError, MCPRemoteError
from .http_client import HTTPMCPClient


TOOL_NAME_SEPARATOR = "__"
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True, slots=True)
class MCPServerDefinition:
    """How to launch one MCP server and describe its scope to the LLM."""

    name: str
    command: tuple[str, ...]
    working_directory: Path
    transport: str = "stdio"
    environment: Mapping[str, str] | None = None
    instructions: str = ""
    url: str | None = None
    auth_token: str | None = None

    def __post_init__(self) -> None:
        if not _SAFE_NAME.fullmatch(self.name):
            raise ValueError(
                "MCP server names may contain only letters, digits, hyphens, and underscores"
            )
        if len(self.name) > 40:
            raise ValueError("MCP server names must contain at most 40 characters")
        if TOOL_NAME_SEPARATOR in self.name:
            raise ValueError(f"MCP server names cannot contain {TOOL_NAME_SEPARATOR!r}")
        if self.transport not in {"stdio", "http"}:
            raise ValueError("MCP server transport must be 'stdio' or 'http'")
        if self.transport == "stdio" and (
            not self.command
            or not all(isinstance(part, str) and part for part in self.command)
        ):
            raise ValueError("A stdio MCP server command must contain non-empty strings")
        if self.transport == "http" and not self.url:
            raise ValueError("An HTTP MCP server URL is required")


@dataclass(frozen=True, slots=True)
class MCPServerStatus:
    name: str
    transport: str
    connected: bool
    tools: tuple[str, ...]
    error: str | None = None
    url: str | None = None


@dataclass(frozen=True, slots=True)
class MCPToolRoute:
    public_name: str
    server_name: str
    original_name: str


class ManagedMCPClient(Protocol):
    @property
    def is_connected(self) -> bool: ...

    def connect(self) -> None: ...

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    def close(self) -> None: ...


ClientFactory = Callable[[MCPServerDefinition, MCPClientConfig], ManagedMCPClient]


class MCPServerManagerError(MCPClientError):
    """An expected multi-server startup or routing failure."""


class MCPServerManager:
    """Own local MCP clients and expose one namespaced tool catalog."""

    def __init__(
        self,
        client_config: MCPClientConfig,
        servers: Sequence[MCPServerDefinition],
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not servers:
            raise ValueError("At least one MCP server must be configured")
        names = [server.name for server in servers]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique")
        factory = client_factory or _client_factory
        self._definitions = {server.name: server for server in servers}
        self._clients = {
            server.name: factory(server, client_config) for server in servers
        }
        self._tools_by_server: dict[str, list[dict[str, Any]]] = {
            server.name: [] for server in servers
        }
        self._catalog: list[dict[str, Any]] = []
        self._routes: dict[str, MCPToolRoute] = {}
        self._errors: dict[str, str | None] = {
            server.name: None for server in servers
        }
        self._started = False

    @property
    def statuses(self) -> tuple[MCPServerStatus, ...]:
        return tuple(
            MCPServerStatus(
                name=name,
                transport=self._definitions[name].transport,
                connected=self._clients[name].is_connected,
                tools=tuple(
                    tool["name"] for tool in self._tools_by_server[name]
                ),
                error=self._errors[name],
                url=self._definitions[name].url,
            )
            for name in self._definitions
        )

    @property
    def routes(self) -> tuple[MCPToolRoute, ...]:
        return tuple(self._routes.values())

    def connect(self) -> None:
        if self._started:
            raise MCPServerManagerError("MCP server manager has already been started")
        self._started = True
        self._errors = {name: None for name in self._definitions}
        for name, client in self._clients.items():
            try:
                client.connect()
                tools = client.list_tools()
                self._validate_server_tools(name, tools)
                self._tools_by_server[name] = tools
            except Exception as error:
                try:
                    client.close()
                except Exception:
                    pass
                self._tools_by_server[name] = []
                self._errors[name] = _server_error_message(error)
        self._rebuild_catalog()

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        self._ensure_started()
        if refresh:
            refreshed: dict[str, list[dict[str, Any]]] = {
                name: [] for name in self._definitions
            }
            for name, client in self._clients.items():
                if not client.is_connected:
                    continue
                try:
                    tools = client.list_tools(refresh=True)
                    self._validate_server_tools(name, tools)
                    refreshed[name] = tools
                    self._errors[name] = None
                except Exception as error:
                    try:
                        client.close()
                    except Exception:
                        pass
                    self._errors[name] = _server_error_message(error)
            self._tools_by_server = refreshed
            self._rebuild_catalog()
        return json.loads(json.dumps(self._catalog))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._ensure_started()
        route = self._routes.get(name)
        if route is None:
            raise MCPRemoteError(-32602, f"Unknown MCP tool: {name}")
        client = self._clients[route.server_name]
        if not client.is_connected:
            raise MCPRemoteError(
                -32000,
                f"MCP server '{route.server_name}' is disconnected",
            )
        return client.call_tool(route.original_name, arguments)

    def close(self) -> None:
        for client in reversed(tuple(self._clients.values())):
            try:
                client.close()
            except Exception:
                # Continue closing the remaining independently owned processes.
                pass
        self._clear_runtime_state()

    def __enter__(self) -> "MCPServerManager":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _rebuild_catalog(self) -> None:
        catalog: list[dict[str, Any]] = []
        routes: dict[str, MCPToolRoute] = {}
        for server_name, tools in self._tools_by_server.items():
            definition = self._definitions[server_name]
            for tool in tools:
                original_name = tool["name"]
                public_name = _public_tool_name(
                    server_name, original_name, routes
                )
                published = json.loads(json.dumps(tool))
                published["name"] = public_name
                description = published.get("description", "")
                scope = f"Tool from the '{server_name}' MCP server"
                if definition.instructions:
                    scope += f". {definition.instructions}"
                published["description"] = f"{scope}. {description}".strip()
                catalog.append(published)
                routes[public_name] = MCPToolRoute(
                    public_name=public_name,
                    server_name=server_name,
                    original_name=original_name,
                )
        self._catalog = catalog
        self._routes = routes

    @staticmethod
    def _validate_server_tools(
        server_name: str, tools: list[dict[str, Any]]
    ) -> None:
        seen_original_names: set[str] = set()
        for tool in tools:
            original_name = tool.get("name")
            if not isinstance(original_name, str) or not original_name:
                raise MCPServerManagerError(
                    f"MCP server '{server_name}' published an invalid tool name"
                )
            if original_name in seen_original_names:
                raise MCPServerManagerError(
                    f"MCP server '{server_name}' published duplicate tool '{original_name}'"
                )
            seen_original_names.add(original_name)

    def _ensure_started(self) -> None:
        if not self._started:
            raise MCPServerManagerError("MCP server manager is not connected")

    def _clear_runtime_state(self) -> None:
        self._started = False
        self._catalog = []
        self._routes = {}
        self._tools_by_server = {name: [] for name in self._definitions}
        self._errors = {name: None for name in self._definitions}


def configured_server_definitions(
    external_config: ExternalMCPConfig,
    *,
    project_root: Path | None = None,
    inventory_config: InventoryMCPConfig | None = None,
) -> tuple[MCPServerDefinition, ...]:
    """Build server definitions without embedding machine-specific paths."""

    root = (project_root or Path.cwd()).resolve()
    inventory = inventory_config or InventoryMCPConfig(
        transport="stdio",
        url="http://127.0.0.1:8000/mcp",
        http_host="127.0.0.1",
        http_port=8000,
    )
    definitions = [
        MCPServerDefinition(
            name="inventory",
            command=(
                (sys.executable, "-m", "inventory_assistant.mcp.stdio")
                if inventory.transport == "stdio"
                else ()
            ),
            working_directory=root,
            transport=inventory.transport,
            url=inventory.url if inventory.transport == "http" else None,
            auth_token=inventory.auth_token,
            instructions="Use this server for inventory facts and movements",
        )
    ]
    if external_config.filesystem_enabled:
        definitions.append(
            MCPServerDefinition(
                name="filesystem",
                command=external_config.filesystem_command,
                working_directory=root,
                instructions=(
                    "It can access only the allowed root "
                    f"'{external_config.demo_root}'"
                ),
            )
        )
    if external_config.git_enabled:
        definitions.append(
            MCPServerDefinition(
                name="git",
                command=external_config.git_command,
                working_directory=root,
                instructions=(
                    "Always use the configured repo_path "
                    f"'{external_config.git_repository}'"
                ),
            )
        )
    reserved_names = {"inventory", "filesystem", "git"}
    for configured in external_config.servers:
        if configured.name in reserved_names:
            raise ConfigurationError(
                f"MCP server name is already configured: {configured.name}"
            )
        reserved_names.add(configured.name)
        if not configured.enabled:
            continue
        definitions.append(
            MCPServerDefinition(
                name=configured.name,
                command=configured.launch_command,
                working_directory=configured.working_directory,
                environment=configured.environment,
                instructions=configured.instructions,
            )
        )
    return tuple(definitions)


def _client_factory(
    server: MCPServerDefinition,
    client_config: MCPClientConfig,
) -> ManagedMCPClient:
    if server.transport == "http":
        assert server.url is not None
        return HTTPMCPClient(
            client_config,
            server.url,
            server_name=server.name,
            auth_token=server.auth_token,
        )
    return LocalMCPClient(
        client_config,
        server_name=server.name,
        command=server.command,
        environment=server.environment,
        working_directory=server.working_directory,
    )


def _public_tool_name(
    server_name: str,
    original_name: str,
    existing_routes: Mapping[str, MCPToolRoute],
) -> str:
    safe_original = re.sub(r"[^A-Za-z0-9_-]", "_", original_name)
    safe_original = safe_original or "tool"
    candidate = f"{server_name}{TOOL_NAME_SEPARATOR}{safe_original}"
    if len(candidate) <= 64 and candidate not in existing_routes:
        return candidate

    digest = hashlib.sha256(original_name.encode("utf-8")).hexdigest()[:8]
    suffix = f"_{digest}"
    maximum_original_length = (
        64 - len(server_name) - len(TOOL_NAME_SEPARATOR) - len(suffix)
    )
    if maximum_original_length < 1:
        raise MCPServerManagerError(
            f"MCP server name is too long to namespace tools: {server_name}"
        )
    shortened = safe_original[:maximum_original_length]
    candidate = (
        f"{server_name}{TOOL_NAME_SEPARATOR}{shortened}{suffix}"
    )
    if candidate in existing_routes:
        raise MCPServerManagerError(
            f"Unable to create a unique public name for MCP tool '{original_name}'"
        )
    return candidate


def _server_error_message(error: Exception) -> str:
    message = str(error).strip()
    return message or type(error).__name__

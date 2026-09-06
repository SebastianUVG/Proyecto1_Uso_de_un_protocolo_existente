"""Manual MCP client over the 2025-06-18 Streamable HTTP transport."""

from __future__ import annotations

import json
import socket
import threading
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from inventory_assistant.config import MCPClientConfig

from .client import (
    CLIENT_NAME,
    CLIENT_VERSION,
    DEFAULT_SERVER_NAME,
    MCPClientError,
    MCPClientProtocolError,
    MCPClientTimeoutError,
    MCPRemoteError,
)
from .jsonrpc import (
    JSONRPCProtocolError,
    JSONRPCResponse,
    parse_response,
    serialize_message,
)
from .logging import MCPClientFileLogger
from .protocol import MCP_PROTOCOL_VERSION, MCP_SESSION_HEADER, MCP_VERSION_HEADER


class MCPHTTPTransportError(MCPClientError):
    """Raised when HTTP fails before a valid JSON-RPC response is received."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class _SessionNotFound(MCPHTTPTransportError):
    pass


class HTTPMCPClient:
    """Run the existing manual MCP lifecycle over Streamable HTTP POSTs."""

    def __init__(
        self,
        config: MCPClientConfig,
        url: str,
        *,
        server_name: str = DEFAULT_SERVER_NAME,
    ) -> None:
        if not url:
            raise ValueError("url must be a non-empty string")
        if not server_name or not server_name.strip():
            raise ValueError("server_name must be a non-empty string")
        self._config = config
        self._url = url
        self._server_name = server_name.strip()
        self._logger = MCPClientFileLogger(config.log_path, transport="http")
        self._id_lock = threading.Lock()
        self._next_id = 1
        self._started = False
        self._session_id: str | None = None
        self._negotiated_version: str | None = None
        self._tools: list[dict[str, Any]] = []
        self.server_info: dict[str, Any] | None = None

    @property
    def is_connected(self) -> bool:
        return self._started and self.server_info is not None

    @property
    def log_path(self) -> Path:
        return self._config.log_path

    def connect(self) -> None:
        if self._started:
            raise MCPClientError("MCP client has already been started")
        self._started = True
        try:
            self._initialize_session()
            self._tools = self._fetch_tools()
        except Exception:
            self.close()
            raise

    def ping(self) -> None:
        result = self._request("ping")
        if result != {}:
            raise MCPClientProtocolError("Invalid ping result")

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        self._ensure_connected()
        if refresh:
            self._tools = self._fetch_tools()
        return json.loads(json.dumps(self._tools))

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(name, str) or not name:
            raise ValueError("tool name must be a non-empty string")
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise MCPClientProtocolError("Invalid tools/call result")
        return result

    def close(self) -> None:
        session_id = self._session_id
        if self._started and session_id is not None:
            headers = {
                MCP_SESSION_HEADER: session_id,
                MCP_VERSION_HEADER: self._negotiated_version or MCP_PROTOCOL_VERSION,
            }
            request = Request(self._url, method="DELETE", headers=headers)
            try:
                with urlopen(
                    request, timeout=self._config.request_timeout_seconds
                ) as response:
                    response.read()
            except (HTTPError, URLError, OSError, TimeoutError):
                # Closing is best effort and must not mask an earlier failure.
                pass
        self._started = False
        self._session_id = None
        self._negotiated_version = None
        self._tools = []
        self.server_info = None

    def __enter__(self) -> "HTTPMCPClient":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _initialize_session(self) -> None:
        self._session_id = None
        self._negotiated_version = None
        result = self._request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
            retry_expired_session=False,
        )
        if not isinstance(result, dict):
            raise MCPClientProtocolError("Invalid initialize result")
        if result.get("protocolVersion") != MCP_PROTOCOL_VERSION:
            raise MCPClientProtocolError("Unsupported negotiated MCP version")
        if not isinstance(result.get("serverInfo"), dict):
            raise MCPClientProtocolError("Initialize result has no serverInfo")
        self._negotiated_version = MCP_PROTOCOL_VERSION
        self.server_info = result["serverInfo"]
        self._notify("notifications/initialized", retry_expired_session=False)

    def _fetch_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise MCPClientProtocolError("Invalid tools/list result")
        tools = result["tools"]
        if not all(isinstance(tool, dict) for tool in tools):
            raise MCPClientProtocolError("tools/list contains an invalid tool")
        return tools

    def _request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_expired_session: bool = True,
    ) -> Any:
        self._ensure_started()
        request_id = self._new_request_id()
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        try:
            status, response_body = self._post(message, method, request_id)
        except _SessionNotFound:
            if not retry_expired_session or method == "initialize":
                raise
            self._initialize_session()
            status, response_body = self._post(message, method, request_id)
        if status != HTTPStatus.OK or not response_body:
            raise MCPHTTPTransportError(
                f"HTTP MCP request returned status {status} without a response",
                status=int(status),
            )
        response = _parse_http_response(response_body, request_id)
        self._logger.log_message(
            "server -> client",
            self._server_name,
            method,
            response.request_id,
            response.raw,
        )
        if response.error is not None:
            raise MCPRemoteError(
                response.error["code"],
                response.error["message"],
                response.error.get("data"),
            )
        return response.result

    def _notify(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_expired_session: bool = True,
    ) -> None:
        self._ensure_started()
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        try:
            status, body = self._post(message, method, None)
        except _SessionNotFound:
            if not retry_expired_session:
                raise
            self._initialize_session()
            status, body = self._post(message, method, None)
        if status != HTTPStatus.ACCEPTED or body:
            raise MCPHTTPTransportError(
                f"HTTP MCP notification returned unexpected status {status}",
                status=int(status),
            )

    def _post(
        self,
        message: dict[str, Any],
        method: str,
        request_id: int | None,
    ) -> tuple[int, bytes]:
        self._logger.log_message(
            "client -> server",
            self._server_name,
            method,
            request_id,
            message,
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id is not None:
            headers[MCP_SESSION_HEADER] = self._session_id
        if self._negotiated_version is not None:
            headers[MCP_VERSION_HEADER] = self._negotiated_version
        request = Request(
            self._url,
            data=serialize_message(message).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(
                request, timeout=self._config.request_timeout_seconds
            ) as response:
                body = response.read()
                content_type = response.headers.get_content_type()
                session_id = response.headers.get(MCP_SESSION_HEADER)
                if method == "initialize" and session_id:
                    self._session_id = session_id
                if request_id is not None and content_type not in {
                    "application/json",
                    "text/event-stream",
                }:
                    raise MCPClientProtocolError(
                        f"Unsupported HTTP MCP response type: {content_type}"
                    )
                if content_type == "text/event-stream":
                    body = _response_from_sse(body, request_id)
                return response.status, body
        except HTTPError as error:
            error.read()
            if error.code == HTTPStatus.NOT_FOUND and self._session_id is not None:
                self._session_id = None
                self._negotiated_version = None
                self.server_info = None
                raise _SessionNotFound(
                    "The MCP HTTP session expired", status=error.code
                ) from error
            raise MCPHTTPTransportError(
                f"HTTP MCP transport returned status {error.code}",
                status=error.code,
            ) from error
        except (socket.timeout, TimeoutError) as error:
            raise MCPClientTimeoutError(
                f"MCP request timed out: {method}"
            ) from error
        except URLError as error:
            if isinstance(error.reason, (socket.timeout, TimeoutError)):
                raise MCPClientTimeoutError(
                    f"MCP request timed out: {method}"
                ) from error
            raise MCPHTTPTransportError(
                f"Unable to reach MCP HTTP server '{self._server_name}'"
            ) from error
        except OSError as error:
            raise MCPHTTPTransportError(
                f"Unable to communicate with MCP HTTP server '{self._server_name}'"
            ) from error

    def _new_request_id(self) -> int:
        with self._id_lock:
            request_id = self._next_id
            self._next_id += 1
        return request_id

    def _ensure_started(self) -> None:
        if not self._started:
            raise MCPClientError("MCP client is not started")

    def _ensure_connected(self) -> None:
        if not self.is_connected:
            raise MCPClientError("MCP client is not initialized")


def _parse_http_response(body: bytes, request_id: int) -> JSONRPCResponse:
    try:
        response = parse_response(body.decode("utf-8"))
    except (UnicodeDecodeError, JSONRPCProtocolError) as error:
        raise MCPClientProtocolError("Server returned invalid JSON-RPC") from error
    if response.request_id != request_id:
        raise MCPClientProtocolError(
            f"Server returned an unknown request ID: {response.request_id}"
        )
    return response


def _response_from_sse(body: bytes, request_id: int | None) -> bytes:
    """Extract the correlated JSON-RPC response from a finite POST SSE stream."""

    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MCPClientProtocolError("Server returned invalid UTF-8 SSE") from error
    for event in text.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[5:].lstrip()
            for line in event.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        candidate = "\n".join(data_lines).encode("utf-8")
        try:
            parsed = parse_response(candidate.decode("utf-8"))
        except JSONRPCProtocolError:
            continue
        if parsed.request_id == request_id:
            return candidate
    raise MCPClientProtocolError("SSE stream ended without the correlated response")

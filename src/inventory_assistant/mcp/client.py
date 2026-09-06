"""Manual local MCP client using a subprocess and newline-delimited stdio."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from inventory_assistant.config import MCPClientConfig

from .jsonrpc import (
    JSONRPCProtocolError,
    JSONRPCResponse,
    parse_response,
    serialize_message,
)
from .logging import MCPClientFileLogger
from .protocol import MCP_PROTOCOL_VERSION


CLIENT_NAME = "inventory-chatbot-mcp-client"
CLIENT_VERSION = "0.1.0"
DEFAULT_SERVER_NAME = "inventory-mcp-server"


class MCPClientError(Exception):
    """Base class for expected local MCP client errors."""


class MCPClientTimeoutError(MCPClientError):
    """Raised when the server does not respond within the configured timeout."""


class MCPServerProcessError(MCPClientError):
    """Raised when the local server cannot start or terminates unexpectedly."""


class MCPClientProtocolError(MCPClientError):
    """Raised when the server returns malformed or uncorrelated JSON-RPC."""


class MCPRemoteError(MCPClientError):
    """A structured JSON-RPC error returned by the MCP server."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"MCP error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


@dataclass(slots=True)
class _PendingRequest:
    method: str
    responses: queue.Queue[JSONRPCResponse | MCPClientError]


class LocalMCPClient:
    """Launch and communicate with one local MCP server process."""

    def __init__(
        self,
        config: MCPClientConfig,
        *,
        server_name: str = DEFAULT_SERVER_NAME,
        command: Sequence[str] | None = None,
        environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
    ) -> None:
        if not server_name or not server_name.strip():
            raise ValueError("server_name must be a non-empty string")
        self._config = config
        self._server_name = server_name.strip()
        self._command = tuple(command or (
            sys.executable,
            "-m",
            "inventory_assistant.mcp.stdio",
        ))
        self._environment = os.environ.copy()
        if environment is not None:
            self._environment.update(environment)
        self._working_directory = working_directory or Path.cwd()
        self._logger = MCPClientFileLogger(config.log_path, transport="stdio")
        self._process: subprocess.Popen[str] | None = None
        self._pending: dict[int, _PendingRequest] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._next_id = 1
        self._closing = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._recent_server_logs: deque[str] = deque(maxlen=20)
        self._fatal_error: MCPClientError | None = None
        self._tools: list[dict[str, Any]] = []
        self.server_info: dict[str, Any] | None = None

    @property
    def is_connected(self) -> bool:
        return (
            self._process is not None
            and self._process.poll() is None
            and self.server_info is not None
        )

    @property
    def log_path(self) -> Path:
        return self._config.log_path

    def connect(self) -> None:
        if self._process is not None:
            raise MCPClientError("MCP client has already been started")
        try:
            self._process = subprocess.Popen(
                self._command,
                cwd=self._working_directory,
                env=self._environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except (OSError, ValueError) as error:
            raise MCPServerProcessError(
                f"Unable to start MCP server '{self._server_name}'"
            ) from error

        self._reader_thread = threading.Thread(
            target=self._read_stdout,
            name=f"{self._server_name}-mcp-stdout",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            name=f"{self._server_name}-mcp-stderr",
            daemon=True,
        )
        self._reader_thread.start()
        self._stderr_thread.start()
        try:
            initialize_result = self._request(
                "initialize",
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": CLIENT_NAME,
                        "version": CLIENT_VERSION,
                    },
                },
            )
            if not isinstance(initialize_result, dict):
                raise MCPClientProtocolError("Invalid initialize result")
            if initialize_result.get("protocolVersion") != MCP_PROTOCOL_VERSION:
                raise MCPClientProtocolError("Unsupported negotiated MCP version")
            if not isinstance(initialize_result.get("serverInfo"), dict):
                raise MCPClientProtocolError("Initialize result has no serverInfo")
            self.server_info = initialize_result["serverInfo"]
            self._notify("notifications/initialized")
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
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if not isinstance(result, dict):
            raise MCPClientProtocolError("Invalid tools/call result")
        return result

    def close(self) -> None:
        process = self._process
        if process is None:
            return
        self._closing.set()
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self._fail_pending(MCPServerProcessError("MCP client closed"), fatal=False)
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1)
        if self._stderr_thread is not None:
            self._stderr_thread.join(timeout=1)
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()
        self.server_info = None

    def __enter__(self) -> "LocalMCPClient":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _fetch_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            raise MCPClientProtocolError("Invalid tools/list result")
        tools = result["tools"]
        if not all(isinstance(tool, dict) for tool in tools):
            raise MCPClientProtocolError("tools/list contains an invalid tool")
        return tools

    def _request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Any:
        self._ensure_process_running()
        request_id = self._new_request_id()
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        pending = _PendingRequest(method=method, responses=queue.Queue(maxsize=1))
        with self._pending_lock:
            self._pending[request_id] = pending
        try:
            self._send(message, method, request_id)
            try:
                received = pending.responses.get(
                    timeout=self._config.request_timeout_seconds
                )
            except queue.Empty as error:
                with self._pending_lock:
                    self._pending.pop(request_id, None)
                raise MCPClientTimeoutError(
                    f"MCP request timed out: {method}"
                ) from error
        except Exception:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise
        if isinstance(received, MCPClientError):
            raise received
        if received.error is not None:
            raise MCPRemoteError(
                received.error["code"],
                received.error["message"],
                received.error.get("data"),
            )
        return received.result

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._ensure_process_running()
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message, method, None)

    def _send(
        self,
        message: dict[str, Any],
        method: str,
        request_id: int | None,
    ) -> None:
        process = self._ensure_process_running()
        self._logger.log_message(
            "client -> server",
            self._server_name,
            method,
            request_id,
            message,
        )
        serialized = serialize_message(message)
        try:
            with self._write_lock:
                assert process.stdin is not None
                process.stdin.write(serialized + "\n")
                process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise MCPServerProcessError(
                f"MCP server '{self._server_name}' is not accepting requests"
            ) from error

    def _read_stdout(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        for line in process.stdout:
            serialized = line.rstrip("\r\n")
            if not serialized:
                continue
            try:
                response = parse_response(serialized)
            except JSONRPCProtocolError:
                self._fail_pending(
                    MCPClientProtocolError("Server returned invalid JSON-RPC")
                )
                return
            with self._pending_lock:
                pending = self._pending.pop(response.request_id, None)
            if pending is None:
                self._fail_pending(
                    MCPClientProtocolError(
                        f"Server returned an unknown request ID: {response.request_id}"
                    )
                )
                return
            self._logger.log_message(
                "server -> client",
                self._server_name,
                pending.method,
                response.request_id,
                response.raw,
            )
            pending.responses.put(response)
        if not self._closing.is_set():
            self._fail_pending(
                MCPServerProcessError(
                    f"MCP server '{self._server_name}' terminated unexpectedly"
                )
            )

    def _read_stderr(self) -> None:
        process = self._process
        assert process is not None and process.stderr is not None
        for line in process.stderr:
            self._recent_server_logs.append(line.rstrip("\r\n"))

    def _fail_pending(self, error: MCPClientError, *, fatal: bool = True) -> None:
        if fatal:
            self._fatal_error = error
        with self._pending_lock:
            pending_requests = list(self._pending.values())
            self._pending.clear()
        for pending in pending_requests:
            try:
                pending.responses.put_nowait(error)
            except queue.Full:
                pass

    def _ensure_process_running(self) -> subprocess.Popen[str]:
        if self._fatal_error is not None:
            raise self._fatal_error
        process = self._process
        if process is None:
            raise MCPClientError("MCP client is not started")
        if process.poll() is not None:
            raise MCPServerProcessError(
                f"MCP server '{self._server_name}' is not running"
            )
        return process

    def _ensure_connected(self) -> None:
        if not self.is_connected:
            raise MCPClientError("MCP client is not initialized")

    def _new_request_id(self) -> int:
        with self._id_lock:
            request_id = self._next_id
            self._next_id += 1
        return request_id

"""Manual MCP 2025-06-18 Streamable HTTP transport for Inventory MCP."""

from __future__ import annotations

import json
import secrets
import sys
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Mapping
from urllib.parse import urlparse

from inventory_assistant.config import InventoryMCPConfig

from .factory import build_inventory_server
from .protocol import MCP_PROTOCOL_VERSION, MCP_SESSION_HEADER, MCP_VERSION_HEADER
from .server import InventoryMCPServer, ServerState


MCP_ENDPOINT = "/mcp"
MAX_REQUEST_BYTES = 1_048_576


@dataclass(frozen=True, slots=True)
class HTTPResult:
    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class _Session:
    server: InventoryMCPServer
    lock: threading.Lock = field(default_factory=threading.Lock)


class MCPHTTPApplication:
    """Map HTTP requests to isolated, stateful instances of the MCP core."""

    def __init__(self, server_factory: Callable[[], InventoryMCPServer]) -> None:
        self._server_factory = server_factory
        self._sessions: dict[str, _Session] = {}
        self._sessions_lock = threading.Lock()

    def post(self, body: bytes, headers: Mapping[str, str]) -> HTTPResult:
        transport_error = self._validate_post_headers(headers)
        if transport_error is not None:
            return transport_error
        try:
            serialized = body.decode("utf-8")
        except UnicodeDecodeError:
            return _http_error(HTTPStatus.BAD_REQUEST, "Body must be UTF-8 JSON")

        raw_method: object = None
        valid_json_object = False
        try:
            raw_message = json.loads(serialized)
            valid_json_object = isinstance(raw_message, dict)
            if valid_json_object:
                raw_method = raw_message.get("method")
        except json.JSONDecodeError:
            pass

        session_id = _header(headers, MCP_SESSION_HEADER)
        session: _Session | None = None
        creates_session = session_id is None and raw_method == "initialize"

        if session_id is not None:
            with self._sessions_lock:
                session = self._sessions.get(session_id)
            if session is None:
                return _http_error(HTTPStatus.NOT_FOUND, "MCP session not found")
        elif (
            valid_json_object
            and isinstance(raw_method, str)
            and not creates_session
        ):
            return _http_error(
                HTTPStatus.BAD_REQUEST,
                "Mcp-Session-Id is required after initialization",
            )
        else:
            # Malformed JSON still reaches the manual JSON-RPC parser so it can
            # produce the standard -32700 response. Invalid initialization does
            # not create a durable session.
            session = _Session(self._server_factory())

        assert session is not None
        with session.lock:
            response = session.server.handle_line(serialized)

        response_headers: dict[str, str] = {}
        if creates_session and session.server.state is ServerState.INITIALIZING:
            session_id = secrets.token_urlsafe(32)
            with self._sessions_lock:
                self._sessions[session_id] = session
            response_headers[MCP_SESSION_HEADER] = session_id

        if response is None:
            return HTTPResult(HTTPStatus.ACCEPTED, headers=response_headers)
        return HTTPResult(
            HTTPStatus.OK,
            response.encode("utf-8"),
            {"Content-Type": "application/json; charset=utf-8", **response_headers},
        )

    def delete(self, headers: Mapping[str, str]) -> HTTPResult:
        origin_error = self._validate_origin(headers)
        if origin_error is not None:
            return origin_error
        version_error = self._validate_version(headers)
        if version_error is not None:
            return version_error
        session_id = _header(headers, MCP_SESSION_HEADER)
        if not session_id:
            return _http_error(
                HTTPStatus.BAD_REQUEST, "Mcp-Session-Id is required"
            )
        with self._sessions_lock:
            removed = self._sessions.pop(session_id, None)
        if removed is None:
            return _http_error(HTTPStatus.NOT_FOUND, "MCP session not found")
        return HTTPResult(HTTPStatus.NO_CONTENT)

    def _validate_post_headers(
        self, headers: Mapping[str, str]
    ) -> HTTPResult | None:
        origin_error = self._validate_origin(headers)
        if origin_error is not None:
            return origin_error
        content_type = (_header(headers, "Content-Type") or "").casefold()
        if not content_type.startswith("application/json"):
            return _http_error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "Content-Type must be application/json",
            )
        accepted = {
            item.split(";", 1)[0].strip().casefold()
            for item in (_header(headers, "Accept") or "").split(",")
        }
        if not {"application/json", "text/event-stream"}.issubset(accepted):
            return _http_error(
                HTTPStatus.NOT_ACCEPTABLE,
                "Accept must include application/json and text/event-stream",
            )
        return self._validate_version(headers)

    @staticmethod
    def _validate_version(headers: Mapping[str, str]) -> HTTPResult | None:
        version = _header(headers, MCP_VERSION_HEADER)
        if version is not None and version != MCP_PROTOCOL_VERSION:
            return _http_error(
                HTTPStatus.BAD_REQUEST,
                f"Unsupported MCP protocol version: {version}",
            )
        return None

    @staticmethod
    def _validate_origin(headers: Mapping[str, str]) -> HTTPResult | None:
        origin = _header(headers, "Origin")
        if origin is None:
            return None
        try:
            parsed = urlparse(origin)
            parsed.port
        except ValueError:
            parsed = None
        if (
            parsed is None
            or parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return _http_error(HTTPStatus.FORBIDDEN, "Origin is not allowed")
        return None


class InventoryMCPHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_http_server(
    host: str,
    port: int,
    *,
    server_factory: Callable[[], InventoryMCPServer] | None = None,
) -> InventoryMCPHTTPServer:
    """Create a stoppable HTTP server; passing port 0 selects a test port."""

    application = MCPHTTPApplication(
        server_factory
        or (lambda: build_inventory_server(transport="http", log_stream=sys.stderr))
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            if self.path != MCP_ENDPOINT:
                self._write(_http_error(HTTPStatus.NOT_FOUND, "Endpoint not found"))
                return
            content_length = self.headers.get("Content-Length")
            try:
                length = int(content_length) if content_length is not None else -1
            except ValueError:
                length = -1
            if length < 0:
                self._write(
                    _http_error(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
                )
                return
            if length > MAX_REQUEST_BYTES:
                self._write(
                    _http_error(
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        "Request is too large",
                    )
                )
                return
            self._write(application.post(self.rfile.read(length), self.headers))

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            if self.path != MCP_ENDPOINT:
                self._write(_http_error(HTTPStatus.NOT_FOUND, "Endpoint not found"))
                return
            result = application._validate_origin(self.headers)
            if result is None:
                result = HTTPResult(
                    HTTPStatus.METHOD_NOT_ALLOWED,
                    headers={"Allow": "POST, DELETE"},
                )
            self._write(result)

        def do_DELETE(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            if self.path != MCP_ENDPOINT:
                self._write(_http_error(HTTPStatus.NOT_FOUND, "Endpoint not found"))
                return
            self._write(application.delete(self.headers))

        def log_message(self, format: str, *args: object) -> None:
            # MCP interaction logging is structured by the shared core.
            return

        def _write(self, result: HTTPResult) -> None:
            self.send_response(int(result.status))
            for name, value in result.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(result.body)))
            self.end_headers()
            if result.body:
                self.wfile.write(result.body)

    return InventoryMCPHTTPServer((host, port), Handler)


def _header(headers: Mapping[str, str], name: str) -> str | None:
    direct = headers.get(name)
    if direct is not None:
        return direct.strip()
    wanted = name.casefold()
    for candidate, value in headers.items():
        if candidate.casefold() == wanted:
            return value.strip()
    return None


def _http_error(status: int, message: str) -> HTTPResult:
    body = json.dumps(
        {"error": {"type": "HTTP_TRANSPORT_ERROR", "message": message}},
        separators=(",", ":"),
    ).encode("utf-8")
    return HTTPResult(
        status,
        body,
        {"Content-Type": "application/json; charset=utf-8"},
    )


def main() -> None:
    config = InventoryMCPConfig.from_env()
    server = create_http_server(config.http_host, config.http_port)
    print(
        f"Inventory MCP HTTP server listening on "
        f"http://{config.http_host}:{config.http_port}{MCP_ENDPOINT}",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

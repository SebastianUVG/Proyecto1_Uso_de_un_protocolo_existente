"""In-memory browser sessions built around the existing ChatbotSession."""

from __future__ import annotations

import json
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

from inventory_assistant.chatbot.session import ChatbotSession, PendingOperation
from inventory_assistant.config import (
    ChatbotConfig,
    ExternalMCPConfig,
    InventoryMCPConfig,
    MCPClientConfig,
    OpenAIConfig,
)
from inventory_assistant.llm import LLMProvider
from inventory_assistant.llm.openai_provider import OpenAILLMProvider
from inventory_assistant.mcp.logging import redact_secrets
from inventory_assistant.mcp.manager import (
    MCPServerManager,
    MCPServerStatus,
    configured_server_definitions,
)


WEB_SESSION_TTL_SECONDS = 8 * 60 * 60
STANDARD_SERVERS = ("inventory", "filesystem", "git")


class WebRuntimeError(Exception):
    """Safe error that can be returned by the browser-facing API."""


class WebSessionNotFound(WebRuntimeError):
    pass


class WebMCPManager(Protocol):
    @property
    def statuses(self) -> Sequence[MCPServerStatus]: ...

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...

    def close(self) -> None: ...


@dataclass(slots=True)
class WebMessage:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class BrowserSession:
    chatbot: ChatbotSession
    messages: list[WebMessage] = field(default_factory=list)
    last_access: float = field(default_factory=time.monotonic)
    lock: threading.RLock = field(default_factory=threading.RLock)


class BrowserSessionStore:
    """Keep one ChatbotSession per secure, short-lived browser identifier."""

    def __init__(
        self,
        session_factory: Callable[[], ChatbotSession],
        *,
        ttl_seconds: float = WEB_SESSION_TTL_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = threading.Lock()

    def create(self) -> tuple[str, BrowserSession]:
        self.cleanup()
        session_id = secrets.token_urlsafe(32)
        session = BrowserSession(self._session_factory())
        with self._lock:
            self._sessions[session_id] = session
        return session_id, session

    def get(self, session_id: str | None) -> BrowserSession:
        if not session_id:
            raise WebSessionNotFound("Web session not found")
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise WebSessionNotFound("Web session not found")
            if time.monotonic() - session.last_access > self._ttl_seconds:
                del self._sessions[session_id]
                raise WebSessionNotFound("Web session expired")
            session.last_access = time.monotonic()
            return session

    def replace(self, session_id: str | None) -> tuple[str, BrowserSession]:
        previous = self.get(session_id)
        new_id, new_session = self.create()
        with previous.lock:
            with self._lock:
                self._sessions.pop(session_id, None)
        return new_id, new_session

    def cleanup(self) -> None:
        cutoff = time.monotonic() - self._ttl_seconds
        with self._lock:
            stale = [
                session_id
                for session_id, session in self._sessions.items()
                if session.last_access < cutoff
            ]
            for session_id in stale:
                del self._sessions[session_id]

    def close(self) -> None:
        with self._lock:
            self._sessions.clear()


class WebRuntime:
    """Share MCP connections while isolating browser conversation histories."""

    def __init__(
        self,
        provider: LLMProvider,
        manager: WebMCPManager,
        *,
        log_path: Path,
        configured_servers: Sequence[str],
        known_servers: Sequence[str] = STANDARD_SERVERS,
        max_tool_iterations: int = 5,
    ) -> None:
        self.provider = provider
        self.manager = manager
        self.log_path = log_path
        self.configured_servers = tuple(configured_servers)
        self.known_servers = tuple(
            dict.fromkeys((*STANDARD_SERVERS, *known_servers))
        )
        self.sessions = BrowserSessionStore(
            lambda: ChatbotSession(
                self.provider,
                self.manager,
                max_tool_iterations=max_tool_iterations,
            )
        )

    def close(self) -> None:
        self.sessions.close()
        self.manager.close()

    def server_statuses(self) -> list[dict[str, Any]]:
        current = {status.name: status for status in self.manager.statuses}
        result: list[dict[str, Any]] = []
        server_names = tuple(
            dict.fromkeys(
                (*self.known_servers, *self.configured_servers, *current)
            )
        )
        for name in server_names:
            status = current.get(name)
            if name not in self.configured_servers:
                state = "Disabled"
                transport = None
                tool_count = 0
            elif status is not None and status.connected:
                state = "Connected"
                transport = status.transport
                tool_count = len(status.tools)
            else:
                state = "Error"
                transport = status.transport if status is not None else None
                tool_count = len(status.tools) if status is not None else 0
            result.append(
                {
                    "name": _display_server_name(name),
                    "key": name,
                    "state": state,
                    "transport": transport,
                    "tool_count": tool_count,
                    "error": status.error if status is not None else None,
                }
            )
        return result

    def recent_logs(self, limit: int) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        try:
            with self.log_path.open("r", encoding="utf-8") as log_file:
                lines = deque(log_file, maxlen=limit)
        except OSError as error:
            raise WebRuntimeError("Unable to read MCP logs") from error

        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            records.append(
                {
                    "timestamp": _safe_scalar(raw.get("timestamp")),
                    "server": _safe_scalar(raw.get("server")),
                    "transport": _safe_scalar(raw.get("transport")),
                    "direction": _safe_scalar(raw.get("direction")),
                    "method": _safe_scalar(raw.get("method")),
                    "request_id": _safe_scalar(raw.get("request_id")),
                    "message": redact_secrets(raw.get("message")),
                }
            )
        return records


def build_runtime_from_env() -> WebRuntime:
    """Build the same production dependencies used by the terminal CLI."""

    openai_config = OpenAIConfig.from_env()
    mcp_config = MCPClientConfig.from_env()
    external_config = ExternalMCPConfig.from_env()
    inventory_config = InventoryMCPConfig.from_env()
    chatbot_config = ChatbotConfig.from_env()
    definitions = configured_server_definitions(
        external_config,
        inventory_config=inventory_config,
    )
    provider = OpenAILLMProvider(openai_config)
    manager = MCPServerManager(mcp_config, definitions)
    manager.connect()
    return WebRuntime(
        provider,
        manager,
        log_path=mcp_config.log_path,
        configured_servers=[definition.name for definition in definitions],
        known_servers=[server.name for server in external_config.servers],
        max_tool_iterations=chatbot_config.max_tool_iterations,
    )


def session_snapshot(session: BrowserSession) -> dict[str, Any]:
    pending = session.chatbot.pending_operation
    pending_view = _pending_view(pending)
    if pending_view is not None and session.messages:
        description = session.messages[-1].content
        pending_view["description"] = description.rsplit("Confirm?", 1)[0].strip()
    return {
        "messages": [message.as_dict() for message in session.messages],
        "pending_confirmation": pending_view,
    }


def _pending_view(pending: PendingOperation | None) -> dict[str, Any] | None:
    if pending is None:
        return None
    operations = []
    for request in pending.requests:
        original_name = request.name.partition("__")[2] or request.name
        operations.append(
            {
                "tool": original_name,
                "arguments": redact_secrets(request.arguments),
            }
        )
    return {
        "title": "Inventory modification",
        "operations": operations,
    }


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def _display_server_name(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").title()

"""Structured logging for MCP interactions, kept separate from stdout."""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO


SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
}


class MCPInteractionLogger:
    """Write one redacted JSON log record per line to stderr by default."""

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        server: str = "inventory",
        transport: str = "stdio",
    ) -> None:
        self._stream = stream or sys.stderr
        self._server = server
        self._transport = transport

    def log_message(
        self,
        direction: str,
        message: dict[str, Any],
        *,
        method: str | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "server": self._server,
            "transport": self._transport,
            "method": method or message.get("method"),
            "request_id": message.get("id"),
            "message": redact_secrets(message),
        }
        self._write(record)

    def log_unparseable(self, direction: str, character_count: int) -> None:
        self._write(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "direction": direction,
                "server": self._server,
                "transport": self._transport,
                "method": None,
                "request_id": None,
                "message": {
                    "unparseable": True,
                    "character_count": character_count,
                },
            }
        )

    def log_internal_error(self, method: str, request_id: object) -> None:
        self._write(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "direction": "server",
                "server": self._server,
                "transport": self._transport,
                "method": method,
                "request_id": request_id,
                "event": "internal_error",
            }
        )

    def _write(self, record: dict[str, Any]) -> None:
        print(
            json.dumps(record, ensure_ascii=False, separators=(",", ":")),
            file=self._stream,
            flush=True,
        )


class MCPClientFileLogger:
    """Append redacted client-side MCP interactions to a JSONL file."""

    def __init__(self, path: Path, *, transport: str = "stdio") -> None:
        self.path = path
        self._transport = transport
        self._lock = threading.Lock()

    def log_message(
        self,
        direction: str,
        server: str,
        method: str,
        request_id: object,
        message: dict[str, Any],
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "direction": direction,
            "server": server,
            "transport": self._transport,
            "method": method,
            "request_id": request_id,
            "message": redact_secrets(message),
        }
        serialized = json.dumps(
            record, ensure_ascii=False, separators=(",", ":")
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as log_file:
                log_file.write(serialized + "\n")


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if key.casefold() in SENSITIVE_KEYS
                else redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value

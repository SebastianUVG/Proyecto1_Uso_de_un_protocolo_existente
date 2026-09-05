"""Structured logging for MCP interactions, kept separate from stdout."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
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

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stderr

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
            "method": method or message.get("method"),
            "request_id": message.get("id"),
            "message": _redact(message),
        }
        self._write(record)

    def log_unparseable(self, direction: str, character_count: int) -> None:
        self._write(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "direction": direction,
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


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.casefold() in SENSITIVE_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value

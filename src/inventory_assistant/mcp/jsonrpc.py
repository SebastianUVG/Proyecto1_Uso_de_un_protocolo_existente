"""Small, manual JSON-RPC 2.0 request and response implementation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypeAlias


JSONValue: TypeAlias = (
    None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
)
RequestID: TypeAlias = int | str | None

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_NOT_INITIALIZED = -32002


@dataclass(frozen=True, slots=True)
class JSONRPCRequest:
    """A validated JSON-RPC request or notification."""

    method: str
    params: dict[str, Any]
    request_id: RequestID
    is_notification: bool
    raw: dict[str, Any]


class JSONRPCProtocolError(Exception):
    """An error that must be represented as a JSON-RPC error response."""

    def __init__(
        self,
        code: int,
        message: str,
        *,
        request_id: RequestID = None,
        data: dict[str, Any] | None = None,
        is_notification: bool = False,
        raw_message: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id
        self.data = data
        self.is_notification = is_notification
        self.raw_message = raw_message


def parse_request(serialized: str) -> JSONRPCRequest:
    """Parse and validate one JSON-RPC request encoded as JSON text."""

    try:
        message = json.loads(serialized)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise JSONRPCProtocolError(PARSE_ERROR, "Parse error") from error

    if not isinstance(message, dict):
        raise JSONRPCProtocolError(INVALID_REQUEST, "Invalid Request")

    raw_id = message.get("id")
    has_id = "id" in message
    if has_id and not _is_valid_request_id(raw_id):
        raise JSONRPCProtocolError(INVALID_REQUEST, "Invalid Request")
    request_id: RequestID = raw_id if has_id else None

    if message.get("jsonrpc") != "2.0":
        raise JSONRPCProtocolError(
            INVALID_REQUEST,
            "Invalid Request",
            request_id=request_id,
            data={"reason": 'jsonrpc must be "2.0"'},
            raw_message=message,
        )
    method = message.get("method")
    if not isinstance(method, str) or not method:
        raise JSONRPCProtocolError(
            INVALID_REQUEST,
            "Invalid Request",
            request_id=request_id,
            data={"reason": "method must be a non-empty string"},
            raw_message=message,
        )
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise JSONRPCProtocolError(
            INVALID_PARAMS,
            "Invalid params",
            request_id=request_id,
            data={"reason": "MCP params must be an object"},
            is_notification=not has_id,
            raw_message=message,
        )

    return JSONRPCRequest(
        method=method,
        params=params,
        request_id=request_id,
        is_notification=not has_id,
        raw=message,
    )


def success_response(request_id: RequestID, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(
    request_id: RequestID,
    code: int,
    message: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def serialize_message(message: dict[str, Any]) -> str:
    """Serialize a protocol message as one compact UTF-8-compatible JSON line."""

    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def _is_valid_request_id(value: object) -> bool:
    return value is None or isinstance(value, str) or (
        isinstance(value, int) and not isinstance(value, bool)
    )

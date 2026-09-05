"""Tests for the manual JSON-RPC 2.0 implementation."""

from __future__ import annotations

import json
import unittest

from inventory_assistant.mcp.jsonrpc import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    PARSE_ERROR,
    JSONRPCProtocolError,
    error_response,
    parse_request,
    serialize_message,
    success_response,
)


class JSONRPCTests(unittest.TestCase):
    def test_parses_valid_request(self) -> None:
        request = parse_request(
            '{"jsonrpc":"2.0","id":"abc","method":"ping","params":{}}'
        )
        self.assertEqual(request.request_id, "abc")
        self.assertEqual(request.method, "ping")
        self.assertFalse(request.is_notification)

    def test_rejects_invalid_json_as_parse_error(self) -> None:
        with self.assertRaises(JSONRPCProtocolError) as raised:
            parse_request('{"jsonrpc":')
        self.assertEqual(raised.exception.code, PARSE_ERROR)
        self.assertIsNone(raised.exception.request_id)

    def test_rejects_invalid_request(self) -> None:
        with self.assertRaises(JSONRPCProtocolError) as raised:
            parse_request('{"jsonrpc":"1.0","id":7,"method":"ping"}')
        self.assertEqual(raised.exception.code, INVALID_REQUEST)
        self.assertEqual(raised.exception.request_id, 7)

    def test_rejects_non_object_mcp_params(self) -> None:
        with self.assertRaises(JSONRPCProtocolError) as raised:
            parse_request(
                '{"jsonrpc":"2.0","id":9,"method":"ping","params":[]}'
            )
        self.assertEqual(raised.exception.code, INVALID_PARAMS)
        self.assertEqual(raised.exception.request_id, 9)

    def test_identifies_invalid_notification_without_an_id(self) -> None:
        with self.assertRaises(JSONRPCProtocolError) as raised:
            parse_request(
                '{"jsonrpc":"2.0","method":"notifications/test","params":[]}'
            )
        self.assertTrue(raised.exception.is_notification)
        self.assertEqual(
            raised.exception.raw_message["method"], "notifications/test"
        )

    def test_builds_success_and_error_responses_with_request_id(self) -> None:
        success = success_response("request-5", {"ok": True})
        failure = error_response("request-5", -32601, "Method not found")
        self.assertEqual(success["id"], "request-5")
        self.assertEqual(failure["id"], "request-5")
        self.assertEqual(failure["error"]["code"], -32601)

    def test_serialized_response_is_one_compact_json_line(self) -> None:
        serialized = serialize_message(success_response(1, {"value": "á"}))
        self.assertNotIn("\n", serialized)
        self.assertEqual(json.loads(serialized)["result"]["value"], "á")


if __name__ == "__main__":
    unittest.main()

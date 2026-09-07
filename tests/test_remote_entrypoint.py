"""Tests for the standalone container bootstrap entrypoint."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from inventory_assistant.config import DatabaseConfig
from inventory_assistant.mcp.remote import main


class RemoteEntrypointTests(unittest.TestCase):
    def test_bootstraps_seed_idempotently_before_starting_http_server(self) -> None:
        database_path = Path("/app/data/inventory.db")
        summary = {
            "seed": {
                "products_total": 12,
                "movements_total": 22,
            }
        }
        with (
            patch(
                "inventory_assistant.mcp.remote.DatabaseConfig.from_env",
                return_value=DatabaseConfig(database_path),
            ),
            patch(
                "inventory_assistant.mcp.remote.initialize_database",
                return_value=summary,
            ) as initialize,
            patch("inventory_assistant.mcp.remote.run_http_server") as serve,
        ):
            main()

        initialize.assert_called_once_with(database_path, include_seed=True)
        serve.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

"""Bootstrap SQLite and run the standalone Inventory MCP HTTP service."""

from __future__ import annotations

import sys

from inventory_assistant.config import DatabaseConfig
from inventory_assistant.inventory.bootstrap import initialize_database

from .http_server import main as run_http_server


def main() -> None:
    """Prepare an idempotent demo database before serving HTTP requests."""

    result = initialize_database(
        DatabaseConfig.from_env().path,
        include_seed=True,
    )
    seed = result["seed"]
    assert isinstance(seed, dict)
    print(
        "Inventory database ready: "
        f"{seed['products_total']} products, "
        f"{seed['movements_total']} movements",
        file=sys.stderr,
        flush=True,
    )
    run_http_server()


if __name__ == "__main__":
    main()

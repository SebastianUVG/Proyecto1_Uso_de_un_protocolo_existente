"""Command-line database initialization for local development and tests."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from inventory_assistant.config import DatabaseConfig

from .sqlite.connection import SQLiteConnectionFactory
from .sqlite.seed import DEMO_REFERENCE_DATE, seed_database


def initialize_database(
    database_path: Path,
    *,
    reset: bool = False,
    include_seed: bool = False,
    reference_date: date = DEMO_REFERENCE_DATE,
) -> dict[str, object]:
    """Create the database schema and optionally add demonstration data."""

    path = database_path.resolve()
    if reset and path.exists():
        if not path.is_file():
            raise ValueError(f"database path is not a file: {path}")
        path.unlink()

    schema_path = Path(__file__).with_name("sqlite") / "schema.sql"
    schema = schema_path.read_text(encoding="utf-8")
    connections = SQLiteConnectionFactory(path)
    with connections.connect() as connection:
        connection.executescript(schema)
        seed_summary = (
            seed_database(connection, reference_date) if include_seed else None
        )

    return {
        "database_path": str(path),
        "schema_created": True,
        "seed": seed_summary,
        "reference_date": reference_date.isoformat() if include_seed else None,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create the inventory SQLite database and demo data."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        help="Override INVENTORY_DB_PATH for this command.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the configured database before initialization.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Insert the deterministic demonstration dataset.",
    )
    parser.add_argument(
        "--reference-date",
        type=date.fromisoformat,
        default=DEMO_REFERENCE_DATE,
        metavar="YYYY-MM-DD",
        help=f"Anchor date for demo movements (default: {DEMO_REFERENCE_DATE}).",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    configured_path = args.db_path or DatabaseConfig.from_env().path
    result = initialize_database(
        configured_path,
        reset=args.reset,
        include_seed=args.seed,
        reference_date=args.reference_date,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()


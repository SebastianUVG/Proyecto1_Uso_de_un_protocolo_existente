"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DATABASE_PATH = Path("data/inventory.db")


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Configuration required to connect to the inventory database."""

    path: Path

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        configured_path = os.getenv("INVENTORY_DB_PATH")
        path = Path(configured_path) if configured_path else DEFAULT_DATABASE_PATH
        return cls(path=path)


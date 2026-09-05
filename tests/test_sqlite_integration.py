"""Integration tests for database bootstrap, seed, and SQLite repository."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from inventory_assistant.config import DatabaseConfig
from inventory_assistant.inventory.bootstrap import initialize_database
from inventory_assistant.inventory.models import (
    MovementType,
    RankingDirection,
    RankingMetric,
    StockStatus,
)
from inventory_assistant.inventory.service import InventoryService
from inventory_assistant.inventory.sqlite import SQLiteInventoryRepository
from inventory_assistant.inventory.sqlite.seed import DEMO_REFERENCE_DATE


class SQLiteIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        test_data_root = Path.cwd() / "data" / "tests"
        test_data_root.mkdir(parents=True, exist_ok=True)
        self.temporary_directory = tempfile.TemporaryDirectory(dir=test_data_root)
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "inventory.db"
        initialize_database(self.database_path, include_seed=True)
        self.repository = SQLiteInventoryRepository(self.database_path)
        self.service = InventoryService(self.repository)

    def test_bootstrap_creates_schema_and_expected_seed_counts(self) -> None:
        self.assertTrue(self.database_path.is_file())
        self.assertEqual(len(self.repository.list_products()), 12)
        self.assertEqual(len(self.repository.list_movements()), 22)
        with closing(sqlite3.connect(self.database_path)) as connection:
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertTrue({"products", "inventory_movements"} <= table_names)

    def test_seed_is_idempotent(self) -> None:
        second_run = initialize_database(self.database_path, include_seed=True)
        self.assertEqual(second_run["seed"]["products_added"], 0)
        self.assertEqual(second_run["seed"]["movements_added"], 0)
        self.assertEqual(second_run["seed"]["products_total"], 12)
        self.assertEqual(second_run["seed"]["movements_total"], 22)

    def test_reset_recreates_a_working_database(self) -> None:
        initialize_database(self.database_path, reset=True, include_seed=True)
        rebuilt_repository = SQLiteInventoryRepository(self.database_path)
        self.assertEqual(len(rebuilt_repository.list_products()), 12)
        self.assertEqual(len(rebuilt_repository.list_movements()), 22)

    def test_configuration_reads_inventory_database_path(self) -> None:
        configured_path = self.database_path.parent / "configured.db"
        with patch.dict(
            os.environ,
            {"INVENTORY_DB_PATH": str(configured_path)},
            clear=False,
        ):
            self.assertEqual(DatabaseConfig.from_env().path, configured_path)

    def test_repository_finds_products_case_insensitively(self) -> None:
        by_sku = self.repository.get_product_by_sku("elec-001")
        by_name = self.repository.get_product_by_name("wireless mouse")
        self.assertIsNotNone(by_sku)
        self.assertEqual(by_sku, by_name)
        self.assertEqual(str(by_sku.unit_price), "24.99")

    def test_service_exposes_all_stock_scenarios(self) -> None:
        statuses = {
            self.service.get_product_stock(product_id=product.id).status
            for product in self.repository.list_products()
        }
        self.assertEqual(
            statuses,
            {
                StockStatus.OUT_OF_STOCK,
                StockStatus.BELOW_MINIMUM,
                StockStatus.AT_MINIMUM,
                StockStatus.NORMAL,
                StockStatus.EXCESS,
            },
        )

    def test_service_returns_low_stock_and_restock_data(self) -> None:
        low_stock = self.service.get_low_stock_products()
        recommendations = self.service.get_restock_recommendations()
        expected_skus = {
            "ELEC-001",
            "ELEC-002",
            "ELEC-003",
            "STAT-002",
            "OFF-002",
            "WARE-003",
        }
        self.assertEqual({item.product.sku for item in low_stock}, expected_skus)
        self.assertEqual(
            {item.product.sku for item in recommendations}, expected_skus
        )
        mouse = next(
            item for item in recommendations if item.product.sku == "ELEC-001"
        )
        self.assertEqual(mouse.recommended_quantity, 20)

    def test_service_returns_movements_in_a_date_range(self) -> None:
        movements = self.service.get_product_movements(
            sku="ELEC-002",
            date_from=date(2026, 8, 1),
            date_to=DEMO_REFERENCE_DATE,
            movement_type=MovementType.OUT,
        )
        self.assertEqual(len(movements), 3)
        self.assertEqual(sum(item.quantity for item in movements), 95)

    def test_service_finds_stale_and_never_moved_products(self) -> None:
        inactive = self.service.get_inactive_products(
            inactive_days=90,
            as_of=DEMO_REFERENCE_DATE,
        )
        self.assertEqual(
            {item.product.sku for item in inactive},
            {"OFF-002", "WARE-002", "FURN-001"},
        )

    def test_service_ranks_outgoing_units(self) -> None:
        ranking = self.service.get_product_movement_ranking(
            date_from=date(2026, 8, 1),
            date_to=DEMO_REFERENCE_DATE,
            movement_type=MovementType.OUT,
            metric=RankingMetric.UNITS,
            direction=RankingDirection.MOST,
        )
        self.assertEqual(ranking[0].product.sku, "ELEC-002")
        self.assertEqual(ranking[0].total_units, 95)
        self.assertEqual(ranking[0].transaction_count, 3)


if __name__ == "__main__":
    unittest.main()

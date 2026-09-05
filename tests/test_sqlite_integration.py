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

from inventory_assistant.chatbot.session import ChatbotSession
from inventory_assistant.config import DatabaseConfig
from inventory_assistant.inventory.bootstrap import initialize_database
from inventory_assistant.inventory.exceptions import (
    DuplicateSKUError,
    InsufficientStockError,
)
from inventory_assistant.inventory.models import (
    MovementType,
    RankingDirection,
    RankingMetric,
    StockStatus,
)
from inventory_assistant.inventory.service import InventoryService
from inventory_assistant.inventory.sqlite import SQLiteInventoryRepository
from inventory_assistant.inventory.sqlite.seed import DEMO_REFERENCE_DATE
from inventory_assistant.llm import LLMResponse, ToolUseBlock
from inventory_assistant.mcp.tools import InventoryToolDispatcher


class OneShotMutationProvider:
    def __init__(self, tool_name: str, arguments: dict[str, object]) -> None:
        self.tool_name = tool_name
        self.arguments = arguments

    def generate(self, messages, tools, *, system_prompt) -> LLMResponse:
        return LLMResponse(
            blocks=(ToolUseBlock("pending-1", self.tool_name, self.arguments),),
            stop_reason="tool_use",
        )


class DispatcherClient:
    def __init__(self, dispatcher: InventoryToolDispatcher) -> None:
        self.dispatcher = dispatcher
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_tools(self, *, refresh: bool = False):
        return self.dispatcher.list_tools()

    def call_tool(self, name: str, arguments: dict[str, object]):
        self.calls.append((name, arguments))
        return self.dispatcher.call(name, arguments)


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

    def test_add_product_and_initial_movement_are_atomic(self) -> None:
        result = self.service.add_product(
            sku="LAP-005",
            name="Dell Latitude 5550",
            category="Electronics",
            initial_stock=10,
            minimum_stock=5,
            target_stock=15,
            unit_price="850.00",
            movement_date=date(2026, 9, 5),
        )
        self.assertEqual(result.product.current_stock, 10)
        self.assertIsNotNone(result.initial_movement)
        movements = self.repository.list_movements(product_id=result.product.id)
        self.assertEqual(len(movements), 1)
        self.assertEqual(movements[0].movement_type, MovementType.IN)
        self.assertEqual(movements[0].quantity, 10)
        with self.assertRaises(DuplicateSKUError):
            self.service.add_product(
                sku="lap-005",
                name="Duplicate laptop",
                category="Electronics",
                initial_stock=0,
                minimum_stock=0,
                target_stock=1,
                unit_price=1,
            )

    def test_entry_and_exit_update_stock_and_create_movements(self) -> None:
        before = self.service.get_product_stock(sku="OFF-001").product.current_stock
        entry = self.service.record_inventory_entry(
            sku="OFF-001",
            quantity=7,
            reason="Supplier delivery",
            reference="TEST-ENTRY-001",
            movement_date=date(2026, 9, 5),
        )
        self.assertEqual((entry.previous_stock, entry.new_stock), (before, before + 7))
        exit_result = self.service.record_inventory_exit(
            sku="OFF-001",
            quantity=4,
            reason="Customer sale",
            reference="TEST-EXIT-001",
            movement_date=date(2026, 9, 5),
        )
        self.assertEqual(
            (exit_result.previous_stock, exit_result.new_stock),
            (before + 7, before + 3),
        )
        movements = self.repository.list_movements(
            product_id=exit_result.product.id,
            date_from=date(2026, 9, 5),
        )
        self.assertEqual(
            {movement.movement_type for movement in movements},
            {MovementType.IN, MovementType.OUT},
        )

    def test_insufficient_exit_changes_neither_stock_nor_movements(self) -> None:
        product = self.service.get_product_stock(sku="ELEC-002").product
        movement_count = len(self.repository.list_movements(product_id=product.id))
        with self.assertRaises(InsufficientStockError):
            self.service.record_inventory_exit(
                sku="ELEC-002",
                quantity=product.current_stock + 1,
                reason="Impossible sale",
            )
        unchanged = self.service.get_product_stock(sku="ELEC-002").product
        self.assertEqual(unchanged.current_stock, product.current_stock)
        self.assertEqual(
            len(self.repository.list_movements(product_id=product.id)),
            movement_count,
        )

    def test_product_creation_rolls_back_when_initial_movement_fails(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TRIGGER fail_initial_movement
                    BEFORE INSERT ON inventory_movements
                    WHEN NEW.reason = 'Initial stock'
                    BEGIN
                        SELECT RAISE(ABORT, 'forced initial movement failure');
                    END
                    """
                )
        before_products = len(self.repository.list_products())
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.add_product(
                sku="ROLLBACK-001",
                name="Rollback product",
                category="Test",
                initial_stock=5,
                minimum_stock=1,
                target_stock=10,
                unit_price=10,
            )
        self.assertIsNone(self.repository.get_product_by_sku("ROLLBACK-001"))
        self.assertEqual(len(self.repository.list_products()), before_products)

    def test_movement_rolls_back_when_stock_update_fails(self) -> None:
        product = self.service.get_product_stock(sku="OFF-001").product
        movements_before = len(self.repository.list_movements(product_id=product.id))
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                connection.execute(
                    f"""
                    CREATE TRIGGER fail_stock_update
                    BEFORE UPDATE OF current_stock ON products
                    WHEN NEW.id = {product.id}
                    BEGIN
                        SELECT RAISE(ABORT, 'forced stock update failure');
                    END
                    """
                )
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.record_inventory_entry(
                sku="OFF-001",
                quantity=5,
                reason="Rollback test",
                reference="ROLLBACK-MOVEMENT-001",
            )
        unchanged = self.service.get_product_stock(sku="OFF-001").product
        self.assertEqual(unchanged.current_stock, product.current_stock)
        self.assertEqual(
            len(self.repository.list_movements(product_id=product.id)),
            movements_before,
        )

    def test_list_products_combines_parameterized_filters(self) -> None:
        products = self.service.list_products(
            category="Electronics",
            min_stock=1,
            max_stock=20,
            min_price=20,
            max_price=100,
            search="key",
        )
        self.assertEqual([product.sku for product in products], ["ELEC-002"])
        self.assertEqual(self.service.list_products(search="missing-value"), [])

    def test_update_product_persists_only_administrative_fields(self) -> None:
        before = self.service.get_product_stock(sku="ELEC-003").product
        result = self.service.update_product(
            sku="ELEC-003",
            new_name="Updated Keyboard",
            category="Office Technology",
            minimum_stock=6,
            target_stock=30,
            unit_price="99.50",
        )
        after = self.service.get_product_stock(product_id=before.id).product
        self.assertEqual(result.previous_product, before)
        self.assertEqual(after.name, "Updated Keyboard")
        self.assertEqual(after.category, "Office Technology")
        self.assertEqual(after.minimum_stock, 6)
        self.assertEqual(after.target_stock, 30)
        self.assertEqual(after.unit_price_cents, 9950)
        self.assertEqual(after.current_stock, before.current_stock)
        self.assertEqual(after.sku, before.sku)

    def test_adjust_inventory_records_both_directions_and_skips_noop(self) -> None:
        product = self.service.get_product_stock(sku="WARE-003").product
        before_count = len(self.repository.list_movements(product_id=product.id))
        adjustment_in = self.service.adjust_inventory(
            sku="WARE-003",
            counted_stock=12,
            reason="Physical inventory count",
            reference="COUNT-IN-001",
            movement_date=date(2026, 9, 5),
        )
        self.assertEqual(adjustment_in.adjustment_type, MovementType.ADJUSTMENT_IN)
        self.assertEqual(adjustment_in.difference, 12)
        self.assertEqual(adjustment_in.movement.quantity, 12)
        self.assertEqual(adjustment_in.resulting_stock, 12)

        adjustment_out = self.service.adjust_inventory(
            sku="WARE-003",
            counted_stock=7,
            reference="COUNT-OUT-001",
            movement_date=date(2026, 9, 5),
        )
        self.assertEqual(adjustment_out.adjustment_type, MovementType.ADJUSTMENT_OUT)
        self.assertEqual(adjustment_out.difference, -5)
        self.assertEqual(adjustment_out.movement.quantity, 5)
        self.assertEqual(adjustment_out.resulting_stock, 7)

        no_change = self.service.adjust_inventory(
            sku="WARE-003", counted_stock=7
        )
        self.assertEqual(no_change.difference, 0)
        self.assertIsNone(no_change.movement)
        movements = self.repository.list_movements(product_id=product.id)
        self.assertEqual(len(movements), before_count + 2)
        self.assertEqual(
            {movements[0].movement_type, movements[1].movement_type},
            {MovementType.ADJUSTMENT_IN, MovementType.ADJUSTMENT_OUT},
        )
        self.assertEqual(
            self.service.get_product_stock(sku="WARE-003").product.current_stock,
            7,
        )

    def test_adjustment_rolls_back_when_stock_update_fails(self) -> None:
        product = self.service.get_product_stock(sku="OFF-001").product
        movements_before = len(self.repository.list_movements(product_id=product.id))
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                connection.execute(
                    f"""
                    CREATE TRIGGER fail_adjustment_stock_update
                    BEFORE UPDATE OF current_stock ON products
                    WHEN NEW.id = {product.id}
                    BEGIN
                        SELECT RAISE(ABORT, 'forced adjustment update failure');
                    END
                    """
                )
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.adjust_inventory(
                sku="OFF-001",
                counted_stock=product.current_stock + 5,
                reason="Rollback test",
                reference="ROLLBACK-ADJUSTMENT-001",
            )
        unchanged = self.service.get_product_stock(sku="OFF-001").product
        self.assertEqual(unchanged.current_stock, product.current_stock)
        self.assertEqual(
            len(self.repository.list_movements(product_id=product.id)),
            movements_before,
        )

    def test_cancelled_update_and_adjustment_leave_sqlite_unchanged(self) -> None:
        dispatcher = InventoryToolDispatcher(self.service)
        product_before = self.service.get_product_stock(sku="ELEC-002").product
        movements_before = len(
            self.repository.list_movements(product_id=product_before.id)
        )

        update_client = DispatcherClient(dispatcher)
        update_session = ChatbotSession(
            OneShotMutationProvider(
                "update_product", {"sku": "ELEC-002", "target_stock": 40}
            ),
            update_client,
        )
        self.assertIn("Confirm?", update_session.ask("Update the product"))
        update_session.ask("no")

        adjust_client = DispatcherClient(dispatcher)
        adjust_session = ChatbotSession(
            OneShotMutationProvider(
                "adjust_inventory", {"sku": "ELEC-002", "counted_stock": 99}
            ),
            adjust_client,
        )
        self.assertIn("Confirm?", adjust_session.ask("Adjust the stock"))
        adjust_session.ask("no")

        product_after = self.service.get_product_stock(sku="ELEC-002").product
        self.assertEqual(product_after, product_before)
        self.assertEqual(
            len(self.repository.list_movements(product_id=product_before.id)),
            movements_before,
        )
        self.assertEqual(update_client.calls, [])
        self.assertEqual(adjust_client.calls, [])


if __name__ == "__main__":
    unittest.main()

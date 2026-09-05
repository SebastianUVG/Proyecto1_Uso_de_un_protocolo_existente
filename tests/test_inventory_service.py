"""Unit tests for protocol- and database-independent inventory rules."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from inventory_assistant.inventory.exceptions import (
    DuplicateSKUError,
    InsufficientStockError,
    InvalidInventoryQueryError,
    ProductNotFoundError,
)
from inventory_assistant.inventory.models import (
    InventoryAdjustmentResult,
    InventoryMovement,
    MovementType,
    Product,
    ProductCreation,
    ProductUpdateResult,
    RankingDirection,
    RankingMetric,
    StockStatus,
    StockMovementResult,
)
from inventory_assistant.inventory.service import InventoryService


def make_product(
    product_id: int,
    sku: str,
    name: str,
    stock: int,
    minimum: int,
    target: int,
    category: str = "Test",
    unit_price_cents: int = 1000,
) -> Product:
    return Product(
        id=product_id,
        sku=sku,
        name=name,
        category=category,
        current_stock=stock,
        minimum_stock=minimum,
        target_stock=target,
        unit_price_cents=unit_price_cents,
        created_at="2026-01-01T12:00:00+00:00",
        updated_at="2026-01-01T12:00:00+00:00",
    )


def make_movement(
    movement_id: int,
    product_id: int,
    movement_type: MovementType,
    quantity: int,
    movement_date: date,
) -> InventoryMovement:
    return InventoryMovement(
        id=movement_id,
        product_id=product_id,
        movement_type=movement_type,
        quantity=quantity,
        movement_date=movement_date,
        reason=None,
        reference=f"TEST-{movement_id}",
        created_at=f"{movement_date.isoformat()}T12:00:00+00:00",
    )


class FakeInventoryRepository:
    def __init__(
        self,
        products: list[Product],
        movements: list[InventoryMovement] | None = None,
    ) -> None:
        self.products = products
        self.movements = movements or []

    def get_product_by_id(self, product_id: int) -> Product | None:
        return next((item for item in self.products if item.id == product_id), None)

    def get_product_by_sku(self, sku: str) -> Product | None:
        return next(
            (item for item in self.products if item.sku.casefold() == sku.casefold()),
            None,
        )

    def get_product_by_name(self, name: str) -> Product | None:
        return next(
            (item for item in self.products if item.name.casefold() == name.casefold()),
            None,
        )

    def list_products(
        self,
        category: str | None = None,
        *,
        min_stock: int | None = None,
        max_stock: int | None = None,
        min_price_cents: int | None = None,
        max_price_cents: int | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[Product]:
        products = [
            item
            for item in self.products
            if (category is None or item.category.casefold() == category.casefold())
            and (min_stock is None or item.current_stock >= min_stock)
            and (max_stock is None or item.current_stock <= max_stock)
            and (min_price_cents is None or item.unit_price_cents >= min_price_cents)
            and (max_price_cents is None or item.unit_price_cents <= max_price_cents)
            and (
                search is None
                or search.casefold() in item.sku.casefold()
                or search.casefold() in item.name.casefold()
            )
        ]
        return products[:limit]

    def list_movements(
        self,
        *,
        product_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        movement_type: MovementType | None = None,
    ) -> list[InventoryMovement]:
        results = [
            item
            for item in self.movements
            if (product_id is None or item.product_id == product_id)
            and (date_from is None or item.movement_date >= date_from)
            and (date_to is None or item.movement_date <= date_to)
            and (movement_type is None or item.movement_type is movement_type)
        ]
        return sorted(results, key=lambda item: (item.movement_date, item.id), reverse=True)

    def create_product(
        self,
        *,
        sku: str,
        name: str,
        category: str,
        initial_stock: int,
        minimum_stock: int,
        target_stock: int,
        unit_price_cents: int,
        movement_date: date,
    ) -> ProductCreation:
        if self.get_product_by_sku(sku) is not None:
            raise DuplicateSKUError("a product with this SKU already exists")
        product = Product(
            id=max((item.id for item in self.products), default=0) + 1,
            sku=sku,
            name=name,
            category=category,
            current_stock=initial_stock,
            minimum_stock=minimum_stock,
            target_stock=target_stock,
            unit_price_cents=unit_price_cents,
            created_at="2026-09-05T12:00:00+00:00",
            updated_at="2026-09-05T12:00:00+00:00",
        )
        self.products.append(product)
        movement = None
        if initial_stock > 0:
            movement = make_movement(
                len(self.movements) + 1,
                product.id,
                MovementType.IN,
                initial_stock,
                movement_date,
            )
            self.movements.append(movement)
        return ProductCreation(product=product, initial_movement=movement)

    def record_stock_movement(
        self,
        *,
        product_id: int,
        movement_type: MovementType,
        quantity: int,
        movement_date: date,
        reason: str | None,
        reference: str | None,
    ) -> StockMovementResult:
        product = self.get_product_by_id(product_id)
        if product is None:
            raise ProductNotFoundError("product not found")
        previous_stock = product.current_stock
        delta = quantity if movement_type is MovementType.IN else -quantity
        if previous_stock + delta < 0:
            raise InsufficientStockError("insufficient stock")
        updated = replace(product, current_stock=previous_stock + delta)
        self.products[self.products.index(product)] = updated
        movement = InventoryMovement(
            id=len(self.movements) + 1,
            product_id=product.id,
            movement_type=movement_type,
            quantity=quantity,
            movement_date=movement_date,
            reason=reason,
            reference=reference,
            created_at="2026-09-05T12:00:00+00:00",
        )
        self.movements.append(movement)
        return StockMovementResult(
            product=updated,
            quantity=quantity,
            previous_stock=previous_stock,
            new_stock=updated.current_stock,
            movement=movement,
        )

    def update_product(
        self,
        *,
        product_id: int,
        name: str,
        category: str,
        minimum_stock: int,
        target_stock: int,
        unit_price_cents: int,
    ) -> ProductUpdateResult:
        previous = self.get_product_by_id(product_id)
        if previous is None:
            raise ProductNotFoundError("product not found")
        updated = replace(
            previous,
            name=name,
            category=category,
            minimum_stock=minimum_stock,
            target_stock=target_stock,
            unit_price_cents=unit_price_cents,
        )
        self.products[self.products.index(previous)] = updated
        fields = tuple(
            field
            for field, attribute in (
                ("name", "name"),
                ("category", "category"),
                ("minimum_stock", "minimum_stock"),
                ("target_stock", "target_stock"),
                ("unit_price", "unit_price_cents"),
            )
            if getattr(previous, attribute) != getattr(updated, attribute)
        )
        return ProductUpdateResult(previous, updated, fields)

    def adjust_inventory(
        self,
        *,
        product_id: int,
        counted_stock: int,
        movement_date: date,
        reason: str | None,
        reference: str | None,
    ) -> InventoryAdjustmentResult:
        previous = self.get_product_by_id(product_id)
        if previous is None:
            raise ProductNotFoundError("product not found")
        difference = counted_stock - previous.current_stock
        updated = replace(previous, current_stock=counted_stock)
        movement = None
        movement_type = None
        if difference:
            movement_type = (
                MovementType.ADJUSTMENT_IN
                if difference > 0
                else MovementType.ADJUSTMENT_OUT
            )
            movement = InventoryMovement(
                id=len(self.movements) + 1,
                product_id=product_id,
                movement_type=movement_type,
                quantity=abs(difference),
                movement_date=movement_date,
                reason=reason,
                reference=reference,
                created_at="2026-09-05T12:00:00+00:00",
            )
            self.movements.append(movement)
            self.products[self.products.index(previous)] = updated
        return InventoryAdjustmentResult(
            product=updated,
            previous_stock=previous.current_stock,
            counted_stock=counted_stock,
            difference=difference,
            adjustment_type=movement_type,
            movement=movement,
        )


class InventoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.products = [
            make_product(1, "OUT", "Empty", 0, 5, 20, unit_price_cents=500),
            make_product(2, "LOW", "Low", 2, 5, 15, unit_price_cents=1500),
            make_product(3, "MIN", "At minimum", 5, 5, 15, unit_price_cents=2500),
            make_product(4, "OK", "Normal", 8, 5, 15, unit_price_cents=5000),
            make_product(5, "HIGH", "Excess", 20, 5, 15, unit_price_cents=10000),
            make_product(
                6, "NEVER", "Never moved", 7, 2, 10, "Other", 20000
            ),
        ]
        self.movements = [
            make_movement(1, 1, MovementType.OUT, 5, date(2026, 8, 25)),
            make_movement(2, 2, MovementType.OUT, 20, date(2026, 8, 20)),
            make_movement(3, 2, MovementType.OUT, 10, date(2026, 8, 21)),
            make_movement(4, 3, MovementType.OUT, 2, date(2025, 1, 1)),
            make_movement(5, 4, MovementType.IN, 50, date(2026, 8, 22)),
        ]
        self.service = InventoryService(
            FakeInventoryRepository(self.products, self.movements)
        )

    def test_classifies_all_stock_states(self) -> None:
        expected = {
            "OUT": StockStatus.OUT_OF_STOCK,
            "LOW": StockStatus.BELOW_MINIMUM,
            "MIN": StockStatus.AT_MINIMUM,
            "OK": StockStatus.NORMAL,
            "HIGH": StockStatus.EXCESS,
        }
        for sku, status in expected.items():
            with self.subTest(sku=sku):
                self.assertEqual(self.service.get_product_stock(sku=sku).status, status)

    def test_low_stock_can_exclude_empty_products(self) -> None:
        results = self.service.get_low_stock_products(include_out_of_stock=False)
        self.assertEqual({item.product.sku for item in results}, {"LOW", "MIN"})

    def test_restock_recommends_target_stock_difference(self) -> None:
        recommendations = self.service.get_restock_recommendations()
        quantities = {
            item.product.sku: item.recommended_quantity for item in recommendations
        }
        self.assertEqual(quantities, {"OUT": 20, "LOW": 13, "MIN": 10})

    def test_product_movements_are_filtered(self) -> None:
        movements = self.service.get_product_movements(
            sku="LOW",
            date_from=date(2026, 8, 21),
            movement_type=MovementType.OUT,
        )
        self.assertEqual([movement.quantity for movement in movements], [10])

    def test_inactive_products_include_never_moved(self) -> None:
        activities = self.service.get_inactive_products(
            inactive_days=90,
            as_of=date(2026, 9, 1),
        )
        self.assertEqual(
            {item.product.sku for item in activities},
            {"MIN", "HIGH", "NEVER"},
        )
        never = next(item for item in activities if item.product.sku == "NEVER")
        self.assertIsNone(never.last_movement_date)

    def test_ranking_supports_units_and_transaction_count(self) -> None:
        by_units = self.service.get_product_movement_ranking(
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 31),
            movement_type=MovementType.OUT,
            metric=RankingMetric.UNITS,
            direction=RankingDirection.MOST,
        )
        self.assertEqual([item.product.sku for item in by_units], ["LOW", "OUT"])

        by_transactions = self.service.get_product_movement_ranking(
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 31),
            movement_type=MovementType.OUT,
            metric=RankingMetric.TRANSACTIONS,
            direction=RankingDirection.MOST,
        )
        self.assertEqual(by_transactions[0].product.sku, "LOW")
        self.assertEqual(by_transactions[0].transaction_count, 2)

    def test_requires_exactly_one_product_identifier(self) -> None:
        with self.assertRaises(InvalidInventoryQueryError):
            self.service.get_product_stock()
        with self.assertRaises(InvalidInventoryQueryError):
            self.service.get_product_stock(product_id=1, sku="OUT")

    def test_reports_missing_product(self) -> None:
        with self.assertRaises(ProductNotFoundError):
            self.service.get_product_stock(sku="UNKNOWN")

    def test_rejects_invalid_ranges_and_limits(self) -> None:
        with self.assertRaises(InvalidInventoryQueryError):
            self.service.get_product_movements(
                sku="OUT",
                date_from=date(2026, 9, 1),
                date_to=date(2026, 8, 1),
            )
        with self.assertRaises(InvalidInventoryQueryError):
            self.service.get_low_stock_products(limit=0)
        with self.assertRaises(InvalidInventoryQueryError):
            self.service.get_inactive_products(inactive_days=0)

    def test_adds_product_with_auditable_initial_stock(self) -> None:
        result = self.service.add_product(
            sku="LAP-005",
            name="Dell Latitude 5550",
            category="Electronics",
            initial_stock=10,
            minimum_stock=5,
            target_stock=15,
            unit_price=Decimal("850.00"),
            movement_date=date(2026, 9, 5),
        )
        self.assertEqual(result.product.current_stock, 10)
        self.assertEqual(result.product.unit_price_cents, 85000)
        self.assertIsNotNone(result.initial_movement)
        self.assertEqual(result.initial_movement.movement_type, MovementType.IN)
        self.assertEqual(result.initial_movement.quantity, 10)

    def test_rejects_duplicate_sku_and_invalid_product_data(self) -> None:
        with self.assertRaises(DuplicateSKUError):
            self.service.add_product(
                sku="OUT",
                name="Another",
                category="Test",
                initial_stock=0,
                minimum_stock=0,
                target_stock=1,
                unit_price=1,
            )
        invalid_cases = [
            {"sku": "", "initial_stock": 0, "minimum_stock": 0, "target_stock": 1},
            {"sku": "NEW", "initial_stock": -1, "minimum_stock": 0, "target_stock": 1},
            {"sku": "NEW", "initial_stock": 0, "minimum_stock": 2, "target_stock": 1},
        ]
        for case in invalid_cases:
            with self.subTest(case=case), self.assertRaises(InvalidInventoryQueryError):
                self.service.add_product(
                    name="New product",
                    category="Test",
                    unit_price=1,
                    **case,
                )

    def test_records_entry_and_exit_with_structured_stock_changes(self) -> None:
        entry = self.service.record_inventory_entry(
            sku="LOW",
            quantity=4,
            reason="Delivery",
            movement_date=date(2026, 9, 5),
        )
        self.assertEqual((entry.previous_stock, entry.new_stock), (2, 6))
        self.assertEqual(entry.movement.movement_type, MovementType.IN)
        exit_result = self.service.record_inventory_exit(
            sku="LOW",
            quantity=3,
            reason="Sale",
            movement_date=date(2026, 9, 5),
        )
        self.assertEqual((exit_result.previous_stock, exit_result.new_stock), (6, 3))
        self.assertEqual(exit_result.movement.movement_type, MovementType.OUT)

    def test_rejects_invalid_movements_and_never_allows_negative_stock(self) -> None:
        for quantity in (0, -1):
            with self.subTest(quantity=quantity), self.assertRaises(
                InvalidInventoryQueryError
            ):
                self.service.record_inventory_entry(sku="LOW", quantity=quantity)
        with self.assertRaises(ProductNotFoundError):
            self.service.record_inventory_entry(sku="UNKNOWN", quantity=1)
        with self.assertRaises(InsufficientStockError):
            self.service.record_inventory_exit(sku="LOW", quantity=3)
        self.assertEqual(self.service.get_product_stock(sku="LOW").product.current_stock, 2)

    def test_lists_products_with_combined_filters(self) -> None:
        self.assertEqual(len(self.service.list_products()), 6)
        self.assertEqual(
            [item.sku for item in self.service.list_products(category="Other")],
            ["NEVER"],
        )
        self.assertEqual(
            {item.sku for item in self.service.list_products(min_stock=5, max_stock=8)},
            {"MIN", "OK", "NEVER"},
        )
        self.assertEqual(
            {item.sku for item in self.service.list_products(min_price=20, max_price=60)},
            {"MIN", "OK"},
        )
        self.assertEqual(
            [
                item.sku
                for item in self.service.list_products(
                    category="Test",
                    min_stock=5,
                    max_stock=8,
                    min_price=20,
                    max_price=60,
                    search="minimum",
                )
            ],
            ["MIN"],
        )
        self.assertEqual(self.service.list_products(search="not-present"), [])

    def test_list_products_rejects_invalid_ranges_and_limits(self) -> None:
        invalid_arguments = (
            {"min_stock": -1},
            {"min_stock": 5, "max_stock": 4},
            {"min_price": -1},
            {"min_price": 10, "max_price": 9},
            {"limit": 0},
            {"limit": 101},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(
                InvalidInventoryQueryError
            ):
                self.service.list_products(**arguments)

    def test_updates_one_or_multiple_administrative_fields(self) -> None:
        one_field = self.service.update_product(sku="LOW", minimum_stock=1)
        self.assertEqual(one_field.previous_product.minimum_stock, 5)
        self.assertEqual(one_field.product.minimum_stock, 1)
        self.assertEqual(one_field.changed_fields, ("minimum_stock",))

        multiple = self.service.update_product(
            sku="LOW",
            new_name="Low stock item",
            category="Updated",
            minimum_stock=3,
            target_stock=25,
            unit_price="19.95",
        )
        self.assertEqual(multiple.product.name, "Low stock item")
        self.assertEqual(multiple.product.category, "Updated")
        self.assertEqual(multiple.product.minimum_stock, 3)
        self.assertEqual(multiple.product.target_stock, 25)
        self.assertEqual(multiple.product.unit_price_cents, 1995)
        self.assertEqual(multiple.product.current_stock, 2)
        self.assertEqual(multiple.product.sku, "LOW")

    def test_update_product_validates_complete_result(self) -> None:
        invalid_arguments = (
            {},
            {"minimum_stock": -1},
            {"minimum_stock": 20},
            {"target_stock": 4},
            {"unit_price": -1},
            {"new_name": " "},
        )
        for updates in invalid_arguments:
            with self.subTest(updates=updates), self.assertRaises(
                InvalidInventoryQueryError
            ):
                self.service.update_product(sku="LOW", **updates)
        with self.assertRaises(ProductNotFoundError):
            self.service.update_product(sku="UNKNOWN", category="Test")

    def test_adjusts_inventory_in_out_or_not_at_all(self) -> None:
        adjustment_in = self.service.adjust_inventory(
            sku="LOW", counted_stock=6, reason="Physical count"
        )
        self.assertEqual(adjustment_in.previous_stock, 2)
        self.assertEqual(adjustment_in.difference, 4)
        self.assertEqual(adjustment_in.adjustment_type, MovementType.ADJUSTMENT_IN)
        self.assertEqual(adjustment_in.movement.quantity, 4)
        self.assertEqual(adjustment_in.resulting_stock, 6)

        adjustment_out = self.service.adjust_inventory(sku="LOW", counted_stock=1)
        self.assertEqual(adjustment_out.difference, -5)
        self.assertEqual(adjustment_out.adjustment_type, MovementType.ADJUSTMENT_OUT)
        self.assertEqual(adjustment_out.movement.quantity, 5)
        self.assertEqual(adjustment_out.resulting_stock, 1)

        movement_count = len(self.movements)
        unchanged = self.service.adjust_inventory(sku="LOW", counted_stock=1)
        self.assertEqual(unchanged.difference, 0)
        self.assertIsNone(unchanged.adjustment_type)
        self.assertIsNone(unchanged.movement)
        self.assertEqual(len(self.movements), movement_count)

    def test_adjust_inventory_rejects_invalid_arguments(self) -> None:
        with self.assertRaises(InvalidInventoryQueryError):
            self.service.adjust_inventory(sku="LOW", counted_stock=-1)
        with self.assertRaises(InvalidInventoryQueryError):
            self.service.adjust_inventory(
                sku="LOW", counted_stock=2, reason="x" * 201
            )
        with self.assertRaises(ProductNotFoundError):
            self.service.adjust_inventory(sku="UNKNOWN", counted_stock=2)


if __name__ == "__main__":
    unittest.main()

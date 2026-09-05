"""Persistence contract used by the inventory business service."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from .models import (
    InventoryAdjustmentResult,
    InventoryMovement,
    MovementType,
    Product,
    ProductCreation,
    ProductUpdateResult,
    StockMovementResult,
)


class InventoryRepository(Protocol):
    """Repository interface independent from any database technology."""

    def get_product_by_id(self, product_id: int) -> Product | None: ...

    def get_product_by_sku(self, sku: str) -> Product | None: ...

    def get_product_by_name(self, name: str) -> Product | None: ...

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
    ) -> list[Product]: ...

    def list_movements(
        self,
        *,
        product_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        movement_type: MovementType | None = None,
    ) -> list[InventoryMovement]: ...

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
    ) -> ProductCreation: ...

    def record_stock_movement(
        self,
        *,
        product_id: int,
        movement_type: MovementType,
        quantity: int,
        movement_date: date,
        reason: str | None,
        reference: str | None,
    ) -> StockMovementResult: ...

    def update_product(
        self,
        *,
        product_id: int,
        name: str,
        category: str,
        minimum_stock: int,
        target_stock: int,
        unit_price_cents: int,
    ) -> ProductUpdateResult: ...

    def adjust_inventory(
        self,
        *,
        product_id: int,
        counted_stock: int,
        movement_date: date,
        reason: str | None,
        reference: str | None,
    ) -> InventoryAdjustmentResult: ...

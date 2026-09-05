"""Protocol-independent domain models for inventory operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class MovementType(StrEnum):
    IN = "IN"
    OUT = "OUT"
    ADJUSTMENT_IN = "ADJUSTMENT_IN"
    ADJUSTMENT_OUT = "ADJUSTMENT_OUT"


class StockStatus(StrEnum):
    OUT_OF_STOCK = "OUT_OF_STOCK"
    BELOW_MINIMUM = "BELOW_MINIMUM"
    AT_MINIMUM = "AT_MINIMUM"
    NORMAL = "NORMAL"
    EXCESS = "EXCESS"


class RankingDirection(StrEnum):
    MOST = "MOST"
    LEAST = "LEAST"


class RankingMetric(StrEnum):
    UNITS = "UNITS"
    TRANSACTIONS = "TRANSACTIONS"


@dataclass(frozen=True, slots=True)
class Product:
    id: int
    sku: str
    name: str
    category: str
    current_stock: int
    minimum_stock: int
    target_stock: int
    unit_price_cents: int
    created_at: str
    updated_at: str

    @property
    def unit_price(self) -> Decimal:
        """Return the unit price without using binary floating-point arithmetic."""

        return Decimal(self.unit_price_cents) / Decimal(100)


@dataclass(frozen=True, slots=True)
class InventoryMovement:
    id: int
    product_id: int
    movement_type: MovementType
    quantity: int
    movement_date: date
    reason: str | None
    reference: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class ProductStock:
    product: Product
    status: StockStatus


@dataclass(frozen=True, slots=True)
class RestockRecommendation:
    product: Product
    status: StockStatus
    recommended_quantity: int


@dataclass(frozen=True, slots=True)
class ProductActivity:
    product: Product
    last_movement_date: date | None
    inactive_days: int | None


@dataclass(frozen=True, slots=True)
class MovementRanking:
    product: Product
    total_units: int
    transaction_count: int


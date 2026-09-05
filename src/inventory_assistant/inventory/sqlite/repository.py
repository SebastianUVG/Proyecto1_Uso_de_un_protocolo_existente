"""SQLite implementation of the inventory repository contract."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from ..models import InventoryMovement, MovementType, Product
from .connection import SQLiteConnectionFactory


class SQLiteInventoryRepository:
    """Read inventory domain objects from SQLite using controlled queries."""

    def __init__(self, database_path: Path) -> None:
        self._connections = SQLiteConnectionFactory(database_path)

    def get_product_by_id(self, product_id: int) -> Product | None:
        return self._get_product("id = ?", product_id)

    def get_product_by_sku(self, sku: str) -> Product | None:
        return self._get_product("sku = ? COLLATE NOCASE", sku)

    def get_product_by_name(self, name: str) -> Product | None:
        return self._get_product("name = ? COLLATE NOCASE", name)

    def list_products(self, category: str | None = None) -> list[Product]:
        query = "SELECT * FROM products"
        parameters: tuple[object, ...] = ()
        if category is not None:
            query += " WHERE category = ? COLLATE NOCASE"
            parameters = (category,)
        query += " ORDER BY id"
        with self._connections.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._product_from_row(row) for row in rows]

    def list_movements(
        self,
        *,
        product_id: int | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        movement_type: MovementType | None = None,
    ) -> list[InventoryMovement]:
        clauses: list[str] = []
        parameters: list[object] = []
        if product_id is not None:
            clauses.append("product_id = ?")
            parameters.append(product_id)
        if date_from is not None:
            clauses.append("movement_date >= ?")
            parameters.append(date_from.isoformat())
        if date_to is not None:
            clauses.append("movement_date <= ?")
            parameters.append(date_to.isoformat())
        if movement_type is not None:
            clauses.append("movement_type = ?")
            parameters.append(movement_type.value)

        query = "SELECT * FROM inventory_movements"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY movement_date DESC, id DESC"
        with self._connections.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._movement_from_row(row) for row in rows]

    def _get_product(self, condition: str, value: object) -> Product | None:
        # The condition is supplied only by the three private, fixed query paths above.
        query = f"SELECT * FROM products WHERE {condition}"
        with self._connections.connect() as connection:
            row = connection.execute(query, (value,)).fetchone()
        return self._product_from_row(row) if row is not None else None

    @staticmethod
    def _product_from_row(row: sqlite3.Row) -> Product:
        return Product(
            id=row["id"],
            sku=row["sku"],
            name=row["name"],
            category=row["category"],
            current_stock=row["current_stock"],
            minimum_stock=row["minimum_stock"],
            target_stock=row["target_stock"],
            unit_price_cents=row["unit_price_cents"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _movement_from_row(row: sqlite3.Row) -> InventoryMovement:
        return InventoryMovement(
            id=row["id"],
            product_id=row["product_id"],
            movement_type=MovementType(row["movement_type"]),
            quantity=row["quantity"],
            movement_date=date.fromisoformat(row["movement_date"]),
            reason=row["reason"],
            reference=row["reference"],
            created_at=row["created_at"],
        )


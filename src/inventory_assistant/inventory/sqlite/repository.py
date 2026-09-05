"""SQLite implementation of the inventory repository contract."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from ..exceptions import (
    DuplicateMovementReferenceError,
    DuplicateProductNameError,
    DuplicateSKUError,
    InsufficientStockError,
    ProductNotFoundError,
)
from ..models import (
    InventoryAdjustmentResult,
    InventoryMovement,
    MovementType,
    Product,
    ProductCreation,
    ProductUpdateResult,
    StockMovementResult,
)
from .connection import SQLiteConnectionFactory


class SQLiteInventoryRepository:
    """Persist inventory domain objects with controlled SQLite transactions."""

    def __init__(self, database_path: Path) -> None:
        self._connections = SQLiteConnectionFactory(database_path)

    def get_product_by_id(self, product_id: int) -> Product | None:
        return self._get_product("id = ?", product_id)

    def get_product_by_sku(self, sku: str) -> Product | None:
        return self._get_product("sku = ? COLLATE NOCASE", sku)

    def get_product_by_name(self, name: str) -> Product | None:
        return self._get_product("name = ? COLLATE NOCASE", name)

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
        query = "SELECT * FROM products"
        clauses: list[str] = []
        parameters: list[object] = []
        if category is not None:
            clauses.append("category = ? COLLATE NOCASE")
            parameters.append(category)
        if min_stock is not None:
            clauses.append("current_stock >= ?")
            parameters.append(min_stock)
        if max_stock is not None:
            clauses.append("current_stock <= ?")
            parameters.append(max_stock)
        if min_price_cents is not None:
            clauses.append("unit_price_cents >= ?")
            parameters.append(min_price_cents)
        if max_price_cents is not None:
            clauses.append("unit_price_cents <= ?")
            parameters.append(max_price_cents)
        if search is not None:
            escaped = (
                search.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            clauses.append(
                "(sku LIKE ? ESCAPE '\\' COLLATE NOCASE "
                "OR name LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            parameters.extend((pattern, pattern))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY id"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
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
        timestamp = _utc_timestamp()
        try:
            with self._connections.connect() as connection:
                with connection:
                    cursor = connection.execute(
                        """
                        INSERT INTO products (
                            sku, name, category, current_stock, minimum_stock,
                            target_stock, unit_price_cents, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sku,
                            name,
                            category,
                            initial_stock,
                            minimum_stock,
                            target_stock,
                            unit_price_cents,
                            timestamp,
                            timestamp,
                        ),
                    )
                    product_id = int(cursor.lastrowid)
                    movement = None
                    if initial_stock > 0:
                        movement_cursor = connection.execute(
                            """
                            INSERT INTO inventory_movements (
                                product_id, movement_type, quantity,
                                movement_date, reason, reference, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                product_id,
                                MovementType.IN.value,
                                initial_stock,
                                movement_date.isoformat(),
                                "Initial stock",
                                f"INITIAL-STOCK-{product_id}",
                                timestamp,
                            ),
                        )
                        movement_row = connection.execute(
                            "SELECT * FROM inventory_movements WHERE id = ?",
                            (movement_cursor.lastrowid,),
                        ).fetchone()
                        movement = self._movement_from_row(movement_row)
                    product_row = connection.execute(
                        "SELECT * FROM products WHERE id = ?", (product_id,)
                    ).fetchone()
                    product = self._product_from_row(product_row)
        except sqlite3.IntegrityError as error:
            self._raise_integrity_error(error)
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
        timestamp = _utc_timestamp()
        try:
            with self._connections.connect() as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    product_row = connection.execute(
                        "SELECT * FROM products WHERE id = ?", (product_id,)
                    ).fetchone()
                    if product_row is None:
                        raise ProductNotFoundError("product not found")
                    previous_stock = int(product_row["current_stock"])
                    incoming_types = {
                        MovementType.IN,
                        MovementType.ADJUSTMENT_IN,
                    }
                    delta = quantity if movement_type in incoming_types else -quantity
                    new_stock = previous_stock + delta
                    if new_stock < 0:
                        raise InsufficientStockError(
                            f"insufficient stock: available {previous_stock}, "
                            f"requested {quantity}"
                        )

                    movement_cursor = connection.execute(
                        """
                        INSERT INTO inventory_movements (
                            product_id, movement_type, quantity,
                            movement_date, reason, reference, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            product_id,
                            movement_type.value,
                            quantity,
                            movement_date.isoformat(),
                            reason,
                            reference,
                            timestamp,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE products
                        SET current_stock = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (new_stock, timestamp, product_id),
                    )
                    updated_row = connection.execute(
                        "SELECT * FROM products WHERE id = ?", (product_id,)
                    ).fetchone()
                    movement_row = connection.execute(
                        "SELECT * FROM inventory_movements WHERE id = ?",
                        (movement_cursor.lastrowid,),
                    ).fetchone()
                    product = self._product_from_row(updated_row)
                    movement = self._movement_from_row(movement_row)
        except sqlite3.IntegrityError as error:
            self._raise_integrity_error(error)
        return StockMovementResult(
            product=product,
            quantity=quantity,
            previous_stock=previous_stock,
            new_stock=new_stock,
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
        timestamp = _utc_timestamp()
        try:
            with self._connections.connect() as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    previous_row = connection.execute(
                        "SELECT * FROM products WHERE id = ?", (product_id,)
                    ).fetchone()
                    if previous_row is None:
                        raise ProductNotFoundError("product not found")
                    previous_product = self._product_from_row(previous_row)
                    connection.execute(
                        """
                        UPDATE products
                        SET name = ?, category = ?, minimum_stock = ?,
                            target_stock = ?, unit_price_cents = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            name,
                            category,
                            minimum_stock,
                            target_stock,
                            unit_price_cents,
                            timestamp,
                            product_id,
                        ),
                    )
                    updated_row = connection.execute(
                        "SELECT * FROM products WHERE id = ?", (product_id,)
                    ).fetchone()
                    product = self._product_from_row(updated_row)
        except sqlite3.IntegrityError as error:
            self._raise_integrity_error(error)

        comparable_fields = (
            ("name", "name"),
            ("category", "category"),
            ("minimum_stock", "minimum_stock"),
            ("target_stock", "target_stock"),
            ("unit_price", "unit_price_cents"),
        )
        changed_fields = tuple(
            public_name
            for public_name, attribute in comparable_fields
            if getattr(previous_product, attribute) != getattr(product, attribute)
        )
        return ProductUpdateResult(
            previous_product=previous_product,
            product=product,
            changed_fields=changed_fields,
        )

    def adjust_inventory(
        self,
        *,
        product_id: int,
        counted_stock: int,
        movement_date: date,
        reason: str | None,
        reference: str | None,
    ) -> InventoryAdjustmentResult:
        timestamp = _utc_timestamp()
        try:
            with self._connections.connect() as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    product_row = connection.execute(
                        "SELECT * FROM products WHERE id = ?", (product_id,)
                    ).fetchone()
                    if product_row is None:
                        raise ProductNotFoundError("product not found")
                    previous_stock = int(product_row["current_stock"])
                    difference = counted_stock - previous_stock
                    movement = None
                    adjustment_type = None
                    if difference != 0:
                        adjustment_type = (
                            MovementType.ADJUSTMENT_IN
                            if difference > 0
                            else MovementType.ADJUSTMENT_OUT
                        )
                        movement_cursor = connection.execute(
                            """
                            INSERT INTO inventory_movements (
                                product_id, movement_type, quantity,
                                movement_date, reason, reference, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                product_id,
                                adjustment_type.value,
                                abs(difference),
                                movement_date.isoformat(),
                                reason,
                                reference,
                                timestamp,
                            ),
                        )
                        connection.execute(
                            """
                            UPDATE products
                            SET current_stock = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (counted_stock, timestamp, product_id),
                        )
                        movement_row = connection.execute(
                            "SELECT * FROM inventory_movements WHERE id = ?",
                            (movement_cursor.lastrowid,),
                        ).fetchone()
                        movement = self._movement_from_row(movement_row)
                    updated_row = connection.execute(
                        "SELECT * FROM products WHERE id = ?", (product_id,)
                    ).fetchone()
                    product = self._product_from_row(updated_row)
        except sqlite3.IntegrityError as error:
            self._raise_integrity_error(error)

        return InventoryAdjustmentResult(
            product=product,
            previous_stock=previous_stock,
            counted_stock=counted_stock,
            difference=difference,
            adjustment_type=adjustment_type,
            movement=movement,
        )

    def _get_product(self, condition: str, value: object) -> Product | None:
        # The condition is supplied only by the three private, fixed query paths above.
        query = f"SELECT * FROM products WHERE {condition}"
        with self._connections.connect() as connection:
            row = connection.execute(query, (value,)).fetchone()
        return self._product_from_row(row) if row is not None else None

    @staticmethod
    def _raise_integrity_error(error: sqlite3.IntegrityError) -> None:
        detail = str(error)
        if "products.sku" in detail:
            raise DuplicateSKUError("a product with this SKU already exists") from error
        if "products.name" in detail:
            raise DuplicateProductNameError(
                "a product with this name already exists"
            ) from error
        if "inventory_movements.reference" in detail:
            raise DuplicateMovementReferenceError(
                "an inventory movement with this reference already exists"
            ) from error
        raise error

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


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()

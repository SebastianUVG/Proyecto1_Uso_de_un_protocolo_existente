"""Deterministic demonstration data for the inventory database."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone


DEMO_REFERENCE_DATE = date(2026, 9, 1)

# sku, name, category, current stock, minimum stock, target stock, price cents
DEMO_PRODUCTS: tuple[tuple[object, ...], ...] = (
    ("ELEC-001", "Wireless Mouse", "Electronics", 0, 5, 20, 2499),
    ("ELEC-002", "Mechanical Keyboard", "Electronics", 3, 8, 25, 7999),
    ("ELEC-003", "USB-C Hub", "Electronics", 10, 10, 30, 4599),
    ("ELEC-004", "27-inch Monitor", "Electronics", 18, 6, 15, 22999),
    ("OFF-001", "Laptop Stand", "Office", 15, 5, 20, 3499),
    ("STAT-001", "A5 Notebook", "Stationery", 80, 20, 100, 599),
    ("STAT-002", "Printer Paper Box", "Stationery", 20, 20, 80, 3899),
    ("OFF-002", "Black Toner Cartridge", "Office", 2, 4, 12, 6599),
    ("WARE-001", "Barcode Scanner", "Warehouse", 12, 3, 10, 11999),
    ("WARE-002", "Storage Bin", "Warehouse", 25, 8, 30, 1899),
    ("WARE-003", "Safety Gloves", "Warehouse", 0, 10, 50, 1299),
    ("FURN-001", "Ergonomic Desk Chair", "Furniture", 6, 2, 8, 18999),
)

# reference, sku, movement type, quantity, date offset in days, reason
DEMO_MOVEMENTS: tuple[tuple[object, ...], ...] = (
    ("DEMO-MOV-001", "ELEC-001", "OUT", 4, -2, "Customer order"),
    ("DEMO-MOV-002", "ELEC-001", "OUT", 8, -18, "Customer order"),
    ("DEMO-MOV-003", "ELEC-002", "OUT", 40, -4, "Corporate sale"),
    ("DEMO-MOV-004", "ELEC-002", "OUT", 25, -8, "Online sales"),
    ("DEMO-MOV-005", "ELEC-002", "OUT", 30, -12, "Corporate sale"),
    ("DEMO-MOV-006", "ELEC-002", "IN", 50, -20, "Supplier delivery"),
    ("DEMO-MOV-007", "ELEC-003", "OUT", 2, -6, "Customer order"),
    ("DEMO-MOV-008", "ELEC-003", "IN", 12, -22, "Supplier delivery"),
    ("DEMO-MOV-009", "ELEC-004", "OUT", 15, -3, "Corporate sale"),
    ("DEMO-MOV-010", "ELEC-004", "IN", 20, -16, "Supplier delivery"),
    ("DEMO-MOV-011", "OFF-001", "OUT", 6, -10, "Office order"),
    ("DEMO-MOV-012", "OFF-001", "IN", 10, -25, "Supplier delivery"),
    ("DEMO-MOV-013", "STAT-001", "OUT", 18, -1, "Retail sales"),
    ("DEMO-MOV-014", "STAT-001", "OUT", 12, -15, "Retail sales"),
    ("DEMO-MOV-015", "STAT-001", "IN", 50, -27, "Supplier delivery"),
    ("DEMO-MOV-016", "STAT-002", "OUT", 10, -7, "Office order"),
    ("DEMO-MOV-017", "OFF-002", "OUT", 2, -210, "Office order"),
    ("DEMO-MOV-018", "WARE-001", "IN", 5, -11, "Supplier delivery"),
    ("DEMO-MOV-019", "WARE-001", "OUT", 1, -5, "Warehouse issue"),
    ("DEMO-MOV-020", "WARE-003", "OUT", 22, -9, "Safety equipment issue"),
    ("DEMO-MOV-021", "WARE-003", "OUT", 18, -14, "Safety equipment issue"),
    ("DEMO-MOV-022", "FURN-001", "OUT", 1, -420, "Office setup"),
)


def seed_database(
    connection: sqlite3.Connection,
    reference_date: date = DEMO_REFERENCE_DATE,
) -> dict[str, int]:
    """Insert demo rows without duplicating data when called repeatedly."""

    timestamp = _utc_timestamp(reference_date)
    with connection:
        before_products = connection.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]
        before_movements = connection.execute(
            "SELECT COUNT(*) FROM inventory_movements"
        ).fetchone()[0]

        connection.executemany(
            """
            INSERT INTO products (
                sku, name, category, current_stock, minimum_stock,
                target_stock, unit_price_cents, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sku) DO NOTHING
            """,
            [(*product, timestamp, timestamp) for product in DEMO_PRODUCTS],
        )

        product_ids = {
            row["sku"]: row["id"]
            for row in connection.execute("SELECT id, sku FROM products")
        }
        movement_rows = []
        for reference, sku, movement_type, quantity, offset, reason in DEMO_MOVEMENTS:
            movement_date = reference_date + timedelta(days=int(offset))
            movement_rows.append(
                (
                    product_ids[str(sku)],
                    movement_type,
                    quantity,
                    movement_date.isoformat(),
                    reason,
                    reference,
                    _utc_timestamp(movement_date),
                )
            )
        connection.executemany(
            """
            INSERT INTO inventory_movements (
                product_id, movement_type, quantity, movement_date,
                reason, reference, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(reference) DO NOTHING
            """,
            movement_rows,
        )

        after_products = connection.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]
        after_movements = connection.execute(
            "SELECT COUNT(*) FROM inventory_movements"
        ).fetchone()[0]

    return {
        "products_added": after_products - before_products,
        "movements_added": after_movements - before_movements,
        "products_total": after_products,
        "movements_total": after_movements,
    }


def _utc_timestamp(value: date) -> str:
    return datetime.combine(value, time(hour=12), tzinfo=timezone.utc).isoformat()


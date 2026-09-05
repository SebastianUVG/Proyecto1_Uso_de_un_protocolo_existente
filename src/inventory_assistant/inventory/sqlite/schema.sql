CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE COLLATE NOCASE,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    category TEXT NOT NULL COLLATE NOCASE,
    current_stock INTEGER NOT NULL CHECK (current_stock >= 0),
    minimum_stock INTEGER NOT NULL CHECK (minimum_stock >= 0),
    target_stock INTEGER NOT NULL CHECK (target_stock >= minimum_stock),
    unit_price_cents INTEGER NOT NULL CHECK (unit_price_cents >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    movement_type TEXT NOT NULL CHECK (
        movement_type IN ('IN', 'OUT', 'ADJUSTMENT_IN', 'ADJUSTMENT_OUT')
    ),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    movement_date TEXT NOT NULL,
    reason TEXT,
    reference TEXT UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_products_category
    ON products(category);

CREATE INDEX IF NOT EXISTS idx_inventory_movements_product_date
    ON inventory_movements(product_id, movement_date);

CREATE INDEX IF NOT EXISTS idx_inventory_movements_date
    ON inventory_movements(movement_date);


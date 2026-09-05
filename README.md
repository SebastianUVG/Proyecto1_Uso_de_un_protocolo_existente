# Inventory Assistant

This repository contains the incremental implementation of an inventory assistant for a university networking project. The current stage includes only the inventory domain, business service, repository abstraction, and a reproducible SQLite database. It does **not** include MCP, JSON-RPC, an LLM client, or network transports yet.

## Requirements

- Python 3.11 or newer
- No third-party runtime dependencies

## Project setup

Create and activate a virtual environment, then install the project in editable mode:

```bash
python -m venv .venv
```

PowerShell:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

macOS or Linux:

```bash
source .venv/bin/activate
python -m pip install -e .
```

The application reads the database path from `INVENTORY_DB_PATH`. If the variable is not set, it uses `data/inventory.db`.

PowerShell example:

```powershell
$env:INVENTORY_DB_PATH = "data/inventory.db"
```

macOS or Linux example:

```bash
export INVENTORY_DB_PATH="data/inventory.db"
```

The `.env.example` file documents the available setting. The project does not automatically load `.env` files, which keeps this stage free of external dependencies.

## Create the demonstration database

Create the schema and insert the deterministic demonstration dataset:

```bash
python -m inventory_assistant.inventory.bootstrap --seed
```

Delete and rebuild the configured database:

```bash
python -m inventory_assistant.inventory.bootstrap --reset --seed
```

The seed is anchored to `2026-09-01`. This fixed reference date makes movement dates and test results reproducible. Running the seed repeatedly is safe: products are identified by SKU and demonstration movements by a unique reference, so rows are not duplicated.

Use a different reference date only when intentionally creating another demonstration timeline:

```bash
python -m inventory_assistant.inventory.bootstrap --reset --seed --reference-date 2026-09-01
```

`--reset` deletes the configured SQLite file before rebuilding it and should only be used for local development or tests.

## Inspect the demonstration data

Run the small read-only demonstration script:

```bash
python scripts/demo_inventory.py
```

It shows product counts, low-stock products, restock recommendations, inactive products, and the products with the most outgoing units. Time-based examples use the same reference date as the deterministic seed.

## Run the tests

```bash
python -m unittest discover -s tests -v
```

The unit tests exercise `InventoryService` with an in-memory fake repository. The integration tests create temporary SQLite files, initialize and seed them, query them through `SQLiteInventoryRepository`, and verify that seeding twice does not duplicate data.

## Current architecture

```text
InventoryService
        ↓
InventoryRepository
        ↓
SQLiteInventoryRepository
        ↓
SQLite
```

Only `SQLiteInventoryRepository` and the database initialization modules contain SQLite-specific code. `InventoryService` contains business rules and depends on the repository interface.

## Current scope

Implemented:

- Product and inventory movement domain models
- Repository abstraction and SQLite implementation
- Reproducible schema and demonstration seed
- Stock, low-stock, restock, movement history, inactivity, and movement-ranking operations
- Unit and integration tests

Not implemented yet:

- MCP server or client
- JSON-RPC
- LLM integration
- stdio or HTTP transports
- Remote deployment
- Filesystem MCP or Git MCP integrations


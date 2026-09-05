# Inventory Assistant

This repository contains the incremental implementation of an inventory assistant for a university networking project. The current stage includes the inventory domain, a reproducible SQLite database, and a local Inventory MCP Server implemented manually on top of JSON-RPC 2.0. It does **not** use an MCP SDK or a JSON-RPC framework.

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

## Run the local MCP server

Prepare the database first, then start the newline-delimited stdio server:

```bash
python -m inventory_assistant.inventory.bootstrap --seed
python -m inventory_assistant.mcp.stdio
```

The server implements the `2025-06-18` MCP protocol revision and supports only the methods required in the current stage:

- `initialize`
- `notifications/initialized`
- `ping`
- `tools/list`
- `tools/call`

Send exactly one JSON-RPC object per input line. Protocol responses are written to `stdout`; structured interaction logs are written to `stderr`. The server writes no banners, debugging messages, or other non-protocol output to `stdout`.

The client must complete this lifecycle before calling tools:

```text
initialize request
    -> initialize response
notifications/initialized notification
    -> no response
tools/list or tools/call
```

Close the server's standard input to stop it cleanly.

## Run the MCP stdio demonstration

The demonstration launches the real server as a subprocess and shows the messages exchanged by a small manual test driver:

```bash
python scripts/demo_mcp_stdio.py
```

It performs `initialize`, sends `notifications/initialized`, discovers the six tools with `tools/list`, and calls `get_low_stock_products`. The final section displays the server logs captured separately from protocol output.

Exposed inventory tools:

- `get_product_stock`
- `get_low_stock_products`
- `get_restock_recommendations`
- `get_product_movements`
- `get_inactive_products`
- `get_product_movement_ranking`

## Run the tests

```bash
python -m unittest discover -s tests -v
```

The suite covers the domain service, SQLite integration, manual JSON-RPC parsing and error responses, MCP lifecycle, all six tools, structured logging, and a real stdio subprocess. Temporary database files are created under the ignored `data/` directory.

## Current architecture

```text
stdio transport
    -> Inventory MCP Server
    -> Inventory Tool Dispatcher
    -> InventoryService
    -> InventoryRepository
    -> SQLiteInventoryRepository
    -> SQLite
```

JSON-RPC parsing, MCP lifecycle, tool adapters, business rules, and SQLite access are separate modules. Tool handlers call `InventoryService` and never execute SQL. Only `SQLiteInventoryRepository` and database initialization modules contain SQLite-specific code.

## Current scope

Implemented:

- Product and inventory movement domain models
- Repository abstraction and SQLite implementation
- Reproducible schema and demonstration seed
- Stock, low-stock, restock, movement history, inactivity, and movement-ranking operations
- Manual JSON-RPC 2.0 parsing, validation, responses, and standard errors
- MCP initialization, ping, tool discovery, and tool invocation
- Six validated inventory tool definitions with structured results
- Newline-delimited local stdio transport
- Redacted interaction logging to stderr
- Unit, integration, protocol, and end-to-end tests

Not implemented yet:

- MCP client for the chatbot
- LLM integration
- Conversation context and final chatbot
- Streamable HTTP transport
- Remote deployment
- Filesystem MCP or Git MCP integrations

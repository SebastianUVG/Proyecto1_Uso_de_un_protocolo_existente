# Inventory Assistant

This repository contains the incremental implementation of an inventory assistant for a university networking project. It includes a terminal chatbot, an Anthropic LLM adapter, a manual local MCP client and server, and a reproducible SQLite inventory database. MCP and JSON-RPC are implemented without an MCP SDK or a JSON-RPC framework.

## Requirements

- Python 3.11 or newer
- An Anthropic API key for the real chatbot
- The official `anthropic` Python package, installed by the setup command below

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

The `.env.example` file documents all available settings. The project does not automatically load `.env` files; set variables in the shell before running a command.

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

## Configure Anthropic

Set the API key in the current shell. Never write a real key in source code or commit it to the repository.

PowerShell:

```powershell
$env:ANTHROPIC_API_KEY = "your-key"
```

macOS or Linux:

```bash
export ANTHROPIC_API_KEY="your-key"
```

Optional configuration:

```powershell
$env:ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
$env:ANTHROPIC_MAX_TOKENS = "1024"
$env:ANTHROPIC_TIMEOUT_SECONDS = "60"
$env:MCP_REQUEST_TIMEOUT_SECONDS = "10"
$env:MCP_LOG_PATH = "logs/mcp.jsonl"
$env:MAX_TOOL_ITERATIONS = "5"
```

The API key is required. The other settings have the defaults shown above. The default model favors low cost and latency for this academic demonstration and can be replaced without changing the MCP client.

## Run the chatbot

Prepare SQLite and start the terminal chatbot from the repository root:

```powershell
python -m inventory_assistant.inventory.bootstrap --seed
python -m inventory_assistant.chatbot.cli
```

Example prompts:

```text
You: Who was Alan Turing?
You: What products have low stock?
You: Which of those is the most urgent?
```

The LLM receives the inventory tools dynamically discovered from `tools/list`. It decides whether a question requires a tool; the chatbot contains no keyword rules for selecting tools.

Chatbot commands:

- `/logs` shows up to 20 recent MCP interactions from the JSONL log.
- `/exit` closes the MCP server and ends the session.
- `Ctrl+C` also closes the session cleanly.

MCP logs are stored separately from normal conversational output. Each entry contains timestamp, direction, server, method, request ID, and the redacted JSON-RPC message. API keys and authorization values are never logged.

## Run the tests

```bash
python -m unittest discover -s tests -v
```

The suite covers the domain service, SQLite integration, JSON-RPC, MCP lifecycle, all six tools, the real local MCP client and subprocess, request correlation, timeouts, clean shutdown, tool-use loops, multiple tool calls, context, and tool errors. It uses a fake LLM provider and never consumes Anthropic API credits.

## Current architecture

```text
Terminal chatbot
    -> LLMProvider
    -> Anthropic Messages API
    -> LocalMCPClient
    -> stdio transport
    -> Inventory MCP Server
    -> Inventory Tool Dispatcher
    -> InventoryService
    -> InventoryRepository
    -> SQLiteInventoryRepository
    -> SQLite
```

The chatbot, provider adapter, manual MCP client, JSON-RPC implementation, MCP server, business rules, and SQLite access are separate modules. The Inventory MCP Server remains the source of truth for tool names, descriptions, and schemas.

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
- Manual local MCP client with IDs, response correlation, timeout, and shutdown
- Dynamic conversion of discovered MCP tools to Anthropic tool definitions
- Anthropic Messages API adapter
- Multi-tool loop with a configurable iteration limit
- In-memory conversation context for one terminal session
- Terminal chatbot with `/logs` and `/exit`
- Unit, integration, protocol, and end-to-end tests

Not implemented yet:

- Streamable HTTP transport
- Remote deployment
- Filesystem MCP or Git MCP integrations

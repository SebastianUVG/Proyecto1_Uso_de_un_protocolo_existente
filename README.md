# Inventory Assistant

This repository contains the incremental implementation of a multi-server assistant for a university networking project. It includes a terminal chatbot, an OpenAI LLM adapter, a manual local MCP client, an Inventory MCP Server, integrations with the existing Filesystem and Git MCP servers, and a reproducible SQLite inventory database. The host-side MCP client and JSON-RPC lifecycle are implemented without an MCP SDK or a JSON-RPC framework.

## Requirements

- Python 3.11 or newer
- An OpenAI API key for the real chatbot
- The official `openai` Python package, installed by the setup command below
- Node.js and `npx` for the Filesystem MCP Server
- Git and the external `mcp-server-git` Python package for the Git MCP Server

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

## Configure multiple local MCP servers

Inventory is always enabled. Filesystem and Git are opt-in so the original inventory-only setup continues to work without external packages.

Install the existing servers:

```powershell
# Filesystem needs no project installation; verify Node.js and npx are available.
node --version
npx.cmd --version

# Install the optional Git MCP server dependency.
python -m pip install -e ".[external-mcp]"
```

On macOS or Linux, use `npx` instead of `npx.cmd`. The Git package is an external MCP server and brings its own MCP SDK dependency; the assistant's manual client does not import or use that SDK.

Prepare the controlled demonstration area:

```powershell
python -m inventory_assistant.mcp.demo_workspace
```

This idempotent command creates:

```text
demo_workspace/             Filesystem MCP allowed root
└── repository/             Git MCP allowed repository
    └── .git/
```

It also sets `user.name` and `user.email` only in the demonstration repository, allowing Git MCP to create commits without changing global Git configuration. `demo_workspace/` is ignored by the main repository.

Enable the servers in the current PowerShell session:

```powershell
$env:FILESYSTEM_MCP_ENABLED = "true"
$env:GIT_MCP_ENABLED = "true"
```

macOS or Linux:

```bash
export FILESYSTEM_MCP_ENABLED=true
export GIT_MCP_ENABLED=true
```

The defaults launch:

```text
Filesystem: npx -y @modelcontextprotocol/server-filesystem <demo root>
Git:        current-python -m mcp_server_git --repository <demo repository>
```

`MCP_DEMO_ROOT` changes the common sandbox. `GIT_MCP_REPOSITORY` is relative to that root and is rejected if it escapes the sandbox. Commands can be overridden without machine-specific paths:

```powershell
$env:FILESYSTEM_MCP_COMMAND = "npx.cmd"
$env:FILESYSTEM_MCP_ARGS = '["-y","@modelcontextprotocol/server-filesystem"]'
$env:GIT_MCP_COMMAND = "python"
$env:GIT_MCP_ARGS = '["-m","mcp_server_git"]'
```

At startup, the manager independently performs `initialize`, `notifications/initialized`, and `tools/list` on every configured server. The returned definitions are the source of truth. Public names sent to the LLM are namespaced, for example:

```text
inventory__get_low_stock_products
filesystem__read_text_file
git__git_status
```

The manager keeps the mapping back to the original server and tool name. Consequently, equal tool names from different servers cannot collide, and no keyword routing is needed.

The versions used during validation exposed these tools dynamically:

- Inventory: `get_product_stock`, `get_low_stock_products`, `get_restock_recommendations`, `get_product_movements`, `get_inactive_products`, `get_product_movement_ranking`.
- Filesystem: `read_file`, `read_text_file`, `read_media_file`, `read_multiple_files`, `write_file`, `edit_file`, `create_directory`, `list_directory`, `list_directory_with_sizes`, `directory_tree`, `move_file`, `search_files`, `get_file_info`, `list_allowed_directories`.
- Git: `git_status`, `git_diff_unstaged`, `git_diff_staged`, `git_diff`, `git_commit`, `git_add`, `git_reset`, `git_log`, `git_create_branch`, `git_checkout`, `git_show`, `git_branch`.

Because external server releases can change their catalogs, use `/servers` in the chatbot or run the deterministic demonstration to see the tools actually discovered on another installation.

## Run the README and Git demonstration

After preparing the inventory database and demo workspace and enabling both external servers, run:

```powershell
python scripts/demo_multiple_mcp.py
```

This no-API demonstration sends the natural-language request through the normal `ChatbotSession`. A deterministic fake LLM emits structured tool-use blocks that cause the assistant to:

1. create `repository/README.md` through Filesystem MCP;
2. read it back through Filesystem MCP;
3. stage it through Git MCP;
4. commit it through Git MCP;
5. inspect Git status and history;
6. query low inventory in the same multi-server session.

The scripted provider exists only to make the demonstration free and reproducible. The production chatbot sends the same dynamically discovered tools to OpenAI, which decides the sequence. Production code does not match keywords or hardcode this workflow.

## Configure OpenAI

Create `.env` in the repository root (beside `pyproject.toml`) and add your API key. The application loads this file automatically. `.env` is ignored by Git; never add the real key to `.env.example`, source code, or a commit.

```env
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-5.6-luna
```

Values already defined in the shell take precedence over `.env`. If you prefer a temporary shell variable instead, use the syntax for the shell you actually opened. See the [official OpenAI quickstart](https://developers.openai.com/api/docs/quickstart) for API-key setup guidance.

PowerShell:

```powershell
$env:OPENAI_API_KEY = "your-key"
```

macOS or Linux:

```bash
export OPENAI_API_KEY="your-key"
```

Git Bash on Windows uses the same `export` syntax as macOS and Linux. `$env:OPENAI_API_KEY = ...` works only in PowerShell.

Optional configuration:

```powershell
$env:OPENAI_MODEL = "gpt-5.6-luna"
$env:OPENAI_MAX_OUTPUT_TOKENS = "1024"
$env:OPENAI_TIMEOUT_SECONDS = "60"
$env:MCP_REQUEST_TIMEOUT_SECONDS = "10"
$env:MCP_LOG_PATH = "logs/mcp.jsonl"
$env:MAX_TOOL_ITERATIONS = "5"
$env:FILESYSTEM_MCP_ENABLED = "true"
$env:GIT_MCP_ENABLED = "true"
```

The API key is required. The other settings have the defaults shown above. `gpt-5.6-luna` is the default because OpenAI documents it as the GPT-5.6 option for cost-sensitive workloads and lists function calling among its supported tools. Change `OPENAI_MODEL` to use another compatible model without modifying code. See the [official model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna).

The provider uses the OpenAI Chat Completions API. Dynamically discovered MCP definitions are translated as follows:

```text
MCP name        -> OpenAI function name
MCP description -> OpenAI function description
MCP inputSchema -> OpenAI function parameters
```

When OpenAI returns an assistant `tool_call`, the provider converts it to the existing neutral `ToolUseBlock`. `ChatbotSession` routes and executes it through the manual MCP client. The resulting `ToolResultBlock` is sent back as a `tool` role message using the same `tool_call_id`. `OpenAILLMProvider` never calls MCP, InventoryService, or SQLite directly. This follows the [official OpenAI function calling flow](https://developers.openai.com/api/docs/guides/function-calling).

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
You: Create a README for the demo project, stage it, and commit it.
You: Read that README and show the latest commit.
```

The LLM receives the inventory tools dynamically discovered from `tools/list`. It decides whether a question requires a tool; the chatbot contains no keyword rules for selecting tools.

Chatbot commands:

- `/logs` shows up to 20 recent MCP interactions from the JSONL log.
- `/servers` shows every configured server, its connection state, transport, and discovered tool count.
- `/exit` closes all MCP servers and ends the session.
- `Ctrl+C` also closes the session cleanly.

MCP logs are stored separately from normal conversational output. Each entry contains timestamp, direction, server, method, request ID, and the redacted JSON-RPC message. API keys and authorization values are never logged.

## Run the tests

```bash
python -m unittest discover -s tests -v
```

The suite covers the domain service, SQLite integration, JSON-RPC, MCP lifecycle, all six inventory tools, the real local MCP client and subprocess, request correlation, timeouts, multi-server registration, discovery, duplicate tool names, routing, disconnection, clean shutdown, tool-use loops, multiple tool calls, context, OpenAI response conversion, and tool errors. It uses fake API clients and never consumes OpenAI API credits. Filesystem and Git tests use temporary or fake clients and never modify the main repository.

## Current architecture

```text
Terminal chatbot
    -> LLMProvider
    -> OpenAILLMProvider
    -> OpenAI Chat Completions API
    -> MCPServerManager
       -> LocalMCPClient -> Inventory MCP Server
       -> LocalMCPClient -> Filesystem MCP Server
       -> LocalMCPClient -> Git MCP Server
    -> newline-delimited stdio transport
    -> Inventory Tool Dispatcher
    -> InventoryService
    -> InventoryRepository
    -> SQLiteInventoryRepository
    -> SQLite
```

The chatbot, provider adapter, server manager, manual MCP client, JSON-RPC implementation, Inventory MCP Server, business rules, and SQLite access are separate modules. Every MCP server remains the source of truth for its tool names, descriptions, and schemas.

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
- Dynamic conversion of discovered MCP tools to OpenAI function definitions
- OpenAI Chat Completions API adapter
- Multi-tool loop with a configurable iteration limit
- In-memory conversation context for one terminal session
- Terminal chatbot with `/logs` and `/exit`
- Multiple independent local MCP stdio connections
- Dynamic cross-server tool discovery and namespaced routing
- Sandboxed Filesystem MCP integration
- Repository-restricted Git MCP integration
- Reproducible README and Git demonstration with a fake LLM
- Terminal `/servers` diagnostics
- Unit, integration, protocol, and end-to-end tests

Not implemented yet:

- Streamable HTTP transport
- Remote deployment
- Web interface
- Wireshark analysis (explicitly outside this development scope)

# Inventory Assistant

This repository contains the incremental implementation of a multi-server assistant for a university networking project. It includes a terminal chatbot, an OpenAI LLM adapter, a manual MCP client with stdio and Streamable HTTP transports, an Inventory MCP Server, integrations with the existing Filesystem and Git MCP servers, and a reproducible SQLite inventory database. The host-side MCP client, JSON-RPC lifecycle, and Inventory MCP Server are implemented without an MCP SDK or a JSON-RPC framework.

## Requirements

- Python 3.11 or newer
- An OpenAI API key for the real chatbot
- The official `openai` Python package, `python-dotenv`, FastAPI, and Uvicorn, installed by the setup command below
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

The `.env.example` file documents all available settings. A local `.env` in the repository root is loaded automatically and is ignored by Git; variables already set in the shell take precedence.

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

## Inventory MCP locally through stdio

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

Close the server's standard input to stop it cleanly. The chatbot uses this
transport by default. In `.env`, keep:

```env
INVENTORY_MCP_TRANSPORT=stdio
```

## Run the MCP stdio demonstration

The demonstration launches the real server as a subprocess and shows the messages exchanged by a small manual test driver:

```bash
python scripts/demo_mcp_stdio.py
```

It performs `initialize`, sends `notifications/initialized`, discovers the twelve tools with `tools/list`, and calls `get_low_stock_products`. The final section displays the server logs captured separately from protocol output.

Exposed inventory tools:

- `get_product_stock`
- `get_low_stock_products`
- `get_restock_recommendations`
- `get_product_movements`
- `get_inactive_products`
- `get_product_movement_ranking`
- `list_products`
- `add_product`
- `record_inventory_entry`
- `record_inventory_exit`
- `update_product`
- `adjust_inventory`

The five write tools are executed only after host-side user confirmation. `list_products` is read-only and combines optional category, stock range, price range, partial name/SKU search, and limit filters. All filter values are passed to parameterized SQLite queries.

`update_product` changes only administrative fields: `new_name`, `category`, `minimum_stock`, `target_stock`, and `unit_price`. `new_name` is used because `name` remains available as a product selector. SKU, ID, and `current_stock` are intentionally immutable through this operation; stock changes must remain auditable.

`adjust_inventory` receives a physical `counted_stock`, calculates its difference from the stored stock, and creates either `ADJUSTMENT_IN` or `ADJUSTMENT_OUT`. An equal count returns a no-change result without creating a movement. Movement creation and stock update share one SQLite transaction, so a failure rolls back both.

Positive initial stock is stored as an `IN` movement named `Initial stock` in the same SQLite transaction that creates the product. Regular entries and exits also create their movement and update `current_stock` in one transaction.

## Inventory MCP locally through HTTP

HTTP localhost is the preparation stage for a later remote deployment. It still
uses the same SQLite repository, inventory service, MCP lifecycle, tool dispatcher,
and twelve tool definitions as stdio.

The implementation follows MCP `2025-06-18` Streamable HTTP and uses Python's
standard HTTP library, so no HTTP dependency was added. The selected subset is:

- one `/mcp` endpoint;
- one JSON-RPC message per HTTP `POST`;
- `application/json` responses for requests;
- HTTP `202 Accepted` with an empty body for accepted notifications;
- an `Mcp-Session-Id` created by a successful `initialize` response and required afterward;
- `MCP-Protocol-Version: 2025-06-18` after negotiation;
- HTTP `DELETE` to close a session;
- HTTP `405 Method Not Allowed` for `GET`, because this server does not need an optional standalone SSE channel;
- validation of any supplied `Origin` header, accepting only loopback origins during this localhost stage.

The client accepts both JSON responses and finite SSE responses to `POST`, as
required by Streamable HTTP. The server chooses direct JSON because its current
tools do not send server-initiated requests, progress streams, or notifications.

The responsibilities remain separate:

```text
HTTP: endpoint, headers, status codes, sessions, and UTF-8 bodies
JSON-RPC: message validation, IDs, results, and protocol error objects
MCP: initialize lifecycle, capabilities, ping, tools/list, and tools/call
Inventory: tool dispatcher -> InventoryService -> repository -> SQLite
```

Prepare SQLite and start the HTTP server in the first terminal:

```powershell
python -m inventory_assistant.inventory.bootstrap --seed
python -m inventory_assistant.mcp.http_server
```

The safe defaults are `127.0.0.1`, port `8000`, and endpoint `/mcp`. To change
the local bind address or port, configure the server before starting it:

```env
INVENTORY_MCP_HTTP_HOST=127.0.0.1
INVENTORY_MCP_HTTP_PORT=8000
```

In a second terminal, select HTTP for Inventory and start the normal chatbot.
Filesystem and Git keep their independent stdio connections if enabled:

```powershell
$env:INVENTORY_MCP_TRANSPORT = "http"
$env:INVENTORY_MCP_URL = "http://127.0.0.1:8000/mcp"
python -m inventory_assistant.chatbot.cli
```

Alternatively, store those two settings in the local `.env` file. Shell values
take precedence over `.env`. To return to the original mode:

```powershell
$env:INVENTORY_MCP_TRANSPORT = "stdio"
python -m inventory_assistant.chatbot.cli
```

If the transport variable is stored in `.env`, change its value there instead.
Stop the HTTP server with `Ctrl+C` when it is no longer needed. Authentication and
HTTPS are intentionally deferred until the cloud-deployment stage.

Protocol references used for this implementation:

- [MCP 2025-06-18 Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [MCP 2025-06-18 lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)

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
$env:INVENTORY_MCP_TRANSPORT = "stdio"
$env:INVENTORY_MCP_URL = "http://127.0.0.1:8000/mcp"
$env:INVENTORY_MCP_HTTP_HOST = "127.0.0.1"
$env:INVENTORY_MCP_HTTP_PORT = "8000"
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

## Web Interface

The browser interface is a lightweight FastAPI adapter with static HTML, CSS,
and JavaScript. It does not implement MCP and it does not contain a second
chatbot. Every browser conversation owns an in-memory `ChatbotSession`, while
the configured `OpenAILLMProvider` and `MCPServerManager` are reused underneath:

```text
Browser -> Web API -> ChatbotSession -> OpenAI -> MCPServerManager -> MCP servers
```

FastAPI was selected only for the browser-facing HTTP routes and static files.
The existing manual MCP client, JSON-RPC implementation, stdio transport, and
Streamable HTTP transport remain unchanged. The Web API uses `/api/...` routes;
the Inventory MCP HTTP server continues to use its separate `/mcp` endpoint.

Install the project and its Web dependencies with the normal setup command:

```powershell
python -m pip install -e .
python -m inventory_assistant.inventory.bootstrap --seed
```

Place the OpenAI configuration in the repository's local `.env` file:

```env
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-5.6-luna
WEB_HOST=127.0.0.1
WEB_PORT=8080
```

The default Web address is `http://127.0.0.1:8080/`. It intentionally uses a
different port from Inventory MCP HTTP and binds only to localhost by default.

### Web UI with Inventory MCP stdio

Keep this setting in `.env`:

```env
INVENTORY_MCP_TRANSPORT=stdio
```

Only one terminal is needed after SQLite has been prepared. The Web backend
starts and owns the existing Inventory MCP stdio subprocess:

```powershell
python -m inventory_assistant.inventory.bootstrap --seed
python -m inventory_assistant.web.app
```

Open `http://127.0.0.1:8080/` in a browser. Stop the Web backend with `Ctrl+C`;
it also closes the MCP connections cleanly.

### Web UI with Inventory MCP HTTP

Use these settings in `.env`:

```env
INVENTORY_MCP_TRANSPORT=http
INVENTORY_MCP_URL=http://127.0.0.1:8000/mcp
INVENTORY_MCP_HTTP_HOST=127.0.0.1
INVENTORY_MCP_HTTP_PORT=8000
```

Keep two terminals open. Start Inventory MCP HTTP in terminal 1:

```powershell
python -m inventory_assistant.inventory.bootstrap --seed
python -m inventory_assistant.mcp.http_server
```

Start the Web UI in terminal 2:

```powershell
python -m inventory_assistant.web.app
```

Filesystem and Git require no Web-specific setup. When enabled through the
existing environment variables, their tools are available to the same
conversation and their connection states appear in the sidebar. When disabled,
the interface labels them `Disabled` and does not create extra connections for
status checks.

Assistant responses support headings, bold and italic text, lists, inline code,
code blocks, and tables. Markdown is converted to DOM nodes with `textContent`;
LLM output is never assigned to `innerHTML`, so generated HTML or scripts are
displayed as text instead of being executed. The page also applies a restrictive
Content Security Policy and loads no frontend code from a CDN.

When the existing `ChatbotSession` pauses a mutable inventory tool, the page
shows a confirmation card with the preserved tool arguments. **Confirm
operation** submits `yes` to that pending operation; **Cancel** submits `no` and
does not call the tool. Buttons are disabled during processing to prevent a
duplicate action. The confirmation decision remains entirely in the backend.

Use **View MCP logs** to open the technical drawer. It displays timestamp,
server, transport, direction, method, request ID, and an expandable redacted
JSON-RPC message. API keys, authorization values, tokens, passwords, and session
identifiers are removed before data reaches the browser. Use **New chat** to
discard the current conversational context and start a clean in-memory session;
the existing MCP connections remain available. Sessions are not persisted after
the Web process stops.

## Run the tests

```bash
python -m pip install -e ".[test]"
python -m unittest discover -s tests -v
```

The suite covers the domain service, SQLite integration, JSON-RPC, MCP lifecycle, all twelve inventory tools, filtered listings, administrative updates, physical inventory adjustments, transaction rollback, stdio and HTTP clients and servers, request correlation, HTTP sessions, unavailable servers, timeouts, transport parity, multi-server registration, discovery, duplicate tool names, routing, disconnection, clean shutdown, write confirmations and cancellations, tool-use loops, multiple tool calls, context, OpenAI response conversion, tool errors, Web sessions, browser API routes, Markdown delivery, visual confirmation state, and log redaction. It uses fake API clients and never consumes OpenAI API credits. Filesystem and Git tests use temporary or fake clients and never modify the main repository.

## Current architecture

```text
Terminal chatbot or Web UI
    -> LLMProvider
    -> OpenAILLMProvider
    -> OpenAI Chat Completions API
    -> MCPServerManager
       |-> LocalMCPClient --stdio----------> Inventory MCP core
       |-> HTTPMCPClient --HTTP--> HTTP Server -> Inventory MCP core
       |-> LocalMCPClient --stdio----------> Filesystem MCP Server
       `-> LocalMCPClient --stdio----------> Git MCP Server
Inventory MCP core (shared by the stdio and HTTP routes above)
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
- Twelve validated inventory tool definitions with structured results
- Newline-delimited local stdio transport
- MCP 2025-06-18 Streamable HTTP transport on a configurable localhost endpoint
- Stateful HTTP initialization sessions and explicit session shutdown
- Manual HTTP MCP client with JSON/SSE response handling and transport errors
- Configuration-only Inventory transport selection between stdio and HTTP
- Redacted interaction logging to stderr
- Manual local MCP client with IDs, response correlation, timeout, and shutdown
- Dynamic conversion of discovered MCP tools to OpenAI function definitions
- OpenAI Chat Completions API adapter
- Multi-tool loop with a configurable iteration limit
- Isolated in-memory conversation context for terminal and browser sessions
- Terminal chatbot with `/logs` and `/exit`
- FastAPI Web backend that reuses `ChatbotSession` and `MCPServerManager`
- Responsive static HTML/CSS/JavaScript chat interface
- Safe DOM-based Markdown rendering for headings, emphasis, lists, code, and tables
- Visual confirmation and cancellation for existing pending inventory operations
- Browser MCP status and redacted interaction-log views
- Multiple independent MCP connections, with Inventory selectable as stdio or HTTP
- Dynamic cross-server tool discovery and namespaced routing
- Sandboxed Filesystem MCP integration
- Repository-restricted Git MCP integration
- Reproducible README and Git demonstration with a fake LLM
- Terminal `/servers` diagnostics
- Unit, integration, protocol, and end-to-end tests

Not implemented yet:

- Remote deployment
- HTTPS and remote authentication
- Managed remote database
- Wireshark analysis (explicitly outside this development scope)

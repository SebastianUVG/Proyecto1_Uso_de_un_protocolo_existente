"""Application configuration loaded from environment variables."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from dotenv import load_dotenv


def _load_environment_file(path: Path | None = None) -> None:
    """Load a local .env without replacing variables set by the shell."""

    env_path = path if path is not None else Path.cwd() / ".env"
    load_dotenv(dotenv_path=env_path, override=False)


_load_environment_file()


DEFAULT_DATABASE_PATH = Path("data/inventory.db")
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"
DEFAULT_MCP_LOG_PATH = Path("logs/mcp.jsonl")
DEFAULT_MCP_DEMO_ROOT = Path("demo_workspace")
DEFAULT_EXTERNAL_MCP_SERVERS_CONFIG = Path(
    "config/external_mcp_servers.json"
)
DEFAULT_INVENTORY_MCP_HTTP_HOST = "127.0.0.1"
DEFAULT_INVENTORY_MCP_HTTP_PORT = 8000
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8080


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """Configuration required to connect to the inventory database."""

    path: Path

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        configured_path = os.getenv("INVENTORY_DB_PATH")
        path = Path(configured_path) if configured_path else DEFAULT_DATABASE_PATH
        return cls(path=path)


class ConfigurationError(ValueError):
    """Raised when required environment configuration is missing or invalid."""


@dataclass(frozen=True, slots=True)
class AnthropicConfig:
    api_key: str
    model: str
    max_tokens: int
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "AnthropicConfig":
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is not configured. Set it before starting the chatbot."
            )
        model = os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL).strip()
        if not model:
            raise ConfigurationError("ANTHROPIC_MODEL cannot be empty")
        max_tokens = _positive_int_from_env("ANTHROPIC_MAX_TOKENS", 1024)
        timeout = _positive_float_from_env("ANTHROPIC_TIMEOUT_SECONDS", 60.0)
        return cls(
            api_key=api_key,
            model=model,
            max_tokens=max_tokens,
            timeout_seconds=timeout,
        )


@dataclass(frozen=True, slots=True)
class OpenAIConfig:
    api_key: str
    model: str
    max_output_tokens: int
    timeout_seconds: float

    @classmethod
    def from_env(cls) -> "OpenAIConfig":
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError(
                "OPENAI_API_KEY is not configured. In PowerShell, set it with: "
                '$env:OPENAI_API_KEY = "your-key"'
            )
        model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()
        if not model:
            raise ConfigurationError("OPENAI_MODEL cannot be empty")
        return cls(
            api_key=api_key,
            model=model,
            max_output_tokens=_positive_int_from_env(
                "OPENAI_MAX_OUTPUT_TOKENS", 1024
            ),
            timeout_seconds=_positive_float_from_env(
                "OPENAI_TIMEOUT_SECONDS", 60.0
            ),
        )


@dataclass(frozen=True, slots=True)
class MCPClientConfig:
    request_timeout_seconds: float
    log_path: Path

    @classmethod
    def from_env(cls) -> "MCPClientConfig":
        timeout = _positive_float_from_env("MCP_REQUEST_TIMEOUT_SECONDS", 10.0)
        configured_log_path = os.getenv("MCP_LOG_PATH")
        log_path = (
            Path(configured_log_path) if configured_log_path else DEFAULT_MCP_LOG_PATH
        )
        return cls(request_timeout_seconds=timeout, log_path=log_path)


@dataclass(frozen=True, slots=True)
class InventoryMCPConfig:
    """Transport selection shared by the Inventory MCP host and HTTP server."""

    transport: str
    url: str
    http_host: str
    http_port: int
    allowed_origins: tuple[str, ...] | None = None
    auth_token: str | None = None

    @classmethod
    def from_env(cls) -> "InventoryMCPConfig":
        transport = (
            os.getenv("INVENTORY_MCP_TRANSPORT", "stdio").strip().casefold()
        )
        if transport not in {"stdio", "http"}:
            raise ConfigurationError(
                "INVENTORY_MCP_TRANSPORT must be 'stdio' or 'http'"
            )
        platform_port_is_present = os.getenv("PORT") is not None
        render_environment = _boolean_from_env("RENDER", False)
        default_host = (
            "0.0.0.0"
            if render_environment or platform_port_is_present
            else DEFAULT_INVENTORY_MCP_HTTP_HOST
        )
        host = os.getenv("INVENTORY_MCP_HTTP_HOST", default_host).strip()
        if not host:
            raise ConfigurationError("INVENTORY_MCP_HTTP_HOST cannot be empty")
        if os.getenv("INVENTORY_MCP_HTTP_PORT") is not None:
            port = _port_from_env(
                "INVENTORY_MCP_HTTP_PORT", DEFAULT_INVENTORY_MCP_HTTP_PORT
            )
        elif platform_port_is_present:
            port = _port_from_env("PORT", DEFAULT_INVENTORY_MCP_HTTP_PORT)
        else:
            port = DEFAULT_INVENTORY_MCP_HTTP_PORT
        url = os.getenv(
            "INVENTORY_MCP_URL", f"http://{host}:{port}/mcp"
        ).strip()
        _validate_http_url(url)
        return cls(
            transport=transport,
            url=url,
            http_host=host,
            http_port=port,
            allowed_origins=_allowed_origins_from_env(),
            auth_token=_optional_secret_from_env("INVENTORY_MCP_AUTH_TOKEN"),
        )


@dataclass(frozen=True, slots=True)
class ConfiguredMCPServer:
    """One third-party stdio server loaded from local JSON configuration."""

    name: str
    enabled: bool
    command: str
    args: tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]
    instructions: str = ""

    @property
    def launch_command(self) -> tuple[str, ...]:
        return (self.command, *self.args)


@dataclass(frozen=True, slots=True)
class ExternalMCPConfig:
    """Configuration for built-in and arbitrary local stdio MCP servers."""

    filesystem_enabled: bool
    filesystem_command: tuple[str, ...]
    git_enabled: bool
    git_command: tuple[str, ...]
    demo_root: Path
    git_repository: Path
    servers: tuple[ConfiguredMCPServer, ...] = ()
    servers_config_path: Path | None = None

    @classmethod
    def from_env(cls, *, project_root: Path | None = None) -> "ExternalMCPConfig":
        root = (project_root or Path.cwd()).resolve()
        configured_servers_path = os.getenv("EXTERNAL_MCP_SERVERS_CONFIG")
        servers_config_path = _resolve_from(
            root,
            Path(
                configured_servers_path
                or DEFAULT_EXTERNAL_MCP_SERVERS_CONFIG
            ),
        )
        servers = _load_external_mcp_servers(
            servers_config_path,
            project_root=root,
            required=configured_servers_path is not None,
        )
        configured_demo_root = Path(
            os.getenv("MCP_DEMO_ROOT", str(DEFAULT_MCP_DEMO_ROOT))
        )
        demo_root = _resolve_from(root, configured_demo_root)
        configured_repository = Path(
            os.getenv("GIT_MCP_REPOSITORY", "repository")
        )
        git_repository = (
            _resolve_from(demo_root, configured_repository)
            if not configured_repository.is_absolute()
            else configured_repository.resolve()
        )
        _validate_demo_paths(root, demo_root, git_repository)

        filesystem_executable = os.getenv(
            "FILESYSTEM_MCP_COMMAND",
            "npx.cmd" if os.name == "nt" else "npx",
        ).strip()
        git_executable = os.getenv("GIT_MCP_COMMAND", sys.executable).strip()
        if not filesystem_executable:
            raise ConfigurationError("FILESYSTEM_MCP_COMMAND cannot be empty")
        if not git_executable:
            raise ConfigurationError("GIT_MCP_COMMAND cannot be empty")

        filesystem_args = _command_arguments_from_env(
            "FILESYSTEM_MCP_ARGS",
            ("-y", "@modelcontextprotocol/server-filesystem"),
        )
        git_args = _command_arguments_from_env(
            "GIT_MCP_ARGS",
            ("-m", "mcp_server_git"),
        )
        return cls(
            filesystem_enabled=_boolean_from_env("FILESYSTEM_MCP_ENABLED", False),
            filesystem_command=(
                filesystem_executable,
                *filesystem_args,
                str(demo_root),
            ),
            git_enabled=_boolean_from_env("GIT_MCP_ENABLED", False),
            git_command=(
                git_executable,
                *git_args,
                "--repository",
                str(git_repository),
            ),
            demo_root=demo_root,
            git_repository=git_repository,
            servers=servers,
            servers_config_path=servers_config_path,
        )


@dataclass(frozen=True, slots=True)
class ChatbotConfig:
    max_tool_iterations: int

    @classmethod
    def from_env(cls) -> "ChatbotConfig":
        return cls(
            max_tool_iterations=_positive_int_from_env("MAX_TOOL_ITERATIONS", 5)
        )


@dataclass(frozen=True, slots=True)
class WebConfig:
    """Local bind configuration for the browser-facing Web UI."""

    host: str
    port: int

    @classmethod
    def from_env(cls) -> "WebConfig":
        host = os.getenv("WEB_HOST", DEFAULT_WEB_HOST).strip()
        if not host:
            raise ConfigurationError("WEB_HOST cannot be empty")
        return cls(host=host, port=_port_from_env("WEB_PORT", DEFAULT_WEB_PORT))


def _positive_int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value < 1:
        raise ConfigurationError(f"{name} must be at least 1")
    return value


def _positive_float_from_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a number") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than 0")
    return value


def _port_from_env(name: str, default: int) -> int:
    value = _positive_int_from_env(name, default)
    if value > 65535:
        raise ConfigurationError(f"{name} must be at most 65535")
    return value


def _boolean_from_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _command_arguments_from_env(
    name: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        arguments = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise ConfigurationError(f"{name} must be a JSON array of strings") from error
    if not isinstance(arguments, list) or not all(
        isinstance(argument, str) and argument for argument in arguments
    ):
        raise ConfigurationError(f"{name} must be a JSON array of strings")
    return tuple(arguments)


_EXTERNAL_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_CONFIG_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EXTERNAL_SERVER_FIELDS = frozenset(
    {
        "name",
        "enabled",
        "command",
        "args",
        "cwd",
        "env",
        "instructions",
    }
)


def _load_external_mcp_servers(
    path: Path,
    *,
    project_root: Path,
    required: bool,
) -> tuple[ConfiguredMCPServer, ...]:
    if not path.exists():
        if required:
            raise ConfigurationError(
                f"EXTERNAL_MCP_SERVERS_CONFIG does not exist: {path}"
            )
        return ()
    if not path.is_file():
        raise ConfigurationError(
            f"External MCP server configuration is not a file: {path}"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(
            f"Unable to read external MCP server configuration: {path}"
        ) from error
    if not isinstance(document, dict) or set(document) != {"servers"}:
        raise ConfigurationError(
            "External MCP configuration must contain only a 'servers' array"
        )
    raw_servers = document["servers"]
    if not isinstance(raw_servers, list):
        raise ConfigurationError(
            "External MCP configuration 'servers' must be an array"
        )

    context = {**os.environ, "PROJECT_ROOT": str(project_root)}
    servers: list[ConfiguredMCPServer] = []
    names: set[str] = set()
    for index, raw_server in enumerate(raw_servers):
        label = f"external MCP server at index {index}"
        if not isinstance(raw_server, dict):
            raise ConfigurationError(f"{label} must be an object")
        unknown = set(raw_server) - _EXTERNAL_SERVER_FIELDS
        if unknown:
            raise ConfigurationError(
                f"{label} contains unknown fields: {', '.join(sorted(unknown))}"
            )
        name = raw_server.get("name")
        if (
            not isinstance(name, str)
            or not _EXTERNAL_SERVER_NAME.fullmatch(name)
            or len(name) > 40
            or "__" in name
        ):
            raise ConfigurationError(
                f"{label} has an invalid name; use letters, digits, '-' or '_'"
            )
        if name in names:
            raise ConfigurationError(
                f"External MCP server name is duplicated: {name}"
            )
        names.add(name)
        enabled = raw_server.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigurationError(f"{label} enabled must be a boolean")
        command = raw_server.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ConfigurationError(f"{label} command must be a non-empty string")
        args = raw_server.get("args", [])
        if not isinstance(args, list) or not all(
            isinstance(argument, str) for argument in args
        ):
            raise ConfigurationError(f"{label} args must be an array of strings")
        cwd = raw_server.get("cwd", ".")
        if not isinstance(cwd, str) or not cwd.strip():
            raise ConfigurationError(f"{label} cwd must be a non-empty string")
        environment = raw_server.get("env", {})
        if not isinstance(environment, dict) or not all(
            isinstance(key, str)
            and bool(key)
            and "=" not in key
            and isinstance(value, str)
            for key, value in environment.items()
        ):
            raise ConfigurationError(
                f"{label} env must be an object containing string values"
            )
        instructions = raw_server.get("instructions", "")
        if not isinstance(instructions, str):
            raise ConfigurationError(f"{label} instructions must be a string")

        expanded_command = _expand_config_variables(
            command.strip(), context, label, require_values=enabled
        )
        if _looks_like_path(expanded_command):
            expanded_command = str(
                _resolve_from(project_root, Path(expanded_command))
            )
        expanded_cwd = _expand_config_variables(
            cwd.strip(), context, label, require_values=enabled
        )
        servers.append(
            ConfiguredMCPServer(
                name=name,
                enabled=enabled,
                command=expanded_command,
                args=tuple(
                    _expand_config_variables(
                        argument, context, label, require_values=enabled
                    )
                    for argument in args
                ),
                working_directory=_resolve_from(
                    project_root, Path(expanded_cwd)
                ),
                environment={
                    key: _expand_config_variables(
                        value, context, label, require_values=enabled
                    )
                    for key, value in environment.items()
                },
                instructions=instructions.strip(),
            )
        )
    return tuple(servers)


def _expand_config_variables(
    value: str,
    context: Mapping[str, str],
    label: str,
    *,
    require_values: bool,
) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        replacement = context.get(name)
        if replacement is None:
            if not require_values:
                return match.group(0)
            raise ConfigurationError(
                f"{label} references missing environment variable {name}"
            )
        return replacement

    return _CONFIG_VARIABLE.sub(replace, value)


def _looks_like_path(command: str) -> bool:
    return (
        Path(command).is_absolute()
        or command.startswith(".")
        or "/" in command
        or "\\" in command
    )


def _optional_secret_from_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _allowed_origins_from_env() -> tuple[str, ...] | None:
    raw_value = os.getenv("MCP_ALLOWED_ORIGINS")
    if raw_value is None or not raw_value.strip():
        return None
    origins: list[str] = []
    for item in raw_value.split(","):
        origin = item.strip()
        if not origin:
            raise ConfigurationError(
                "MCP_ALLOWED_ORIGINS must be a comma-separated list of origins"
            )
        normalized = _normalize_origin(origin)
        if normalized not in origins:
            origins.append(normalized)
    return tuple(origins)


def normalize_mcp_origin(origin: str) -> str:
    """Validate and canonicalize one HTTP Origin value."""

    return _normalize_origin(origin)


def _normalize_origin(origin: str) -> str:
    if origin == "*":
        raise ConfigurationError("MCP_ALLOWED_ORIGINS does not accept '*'")
    try:
        parsed = urlparse(origin)
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError(
            f"Invalid origin in MCP_ALLOWED_ORIGINS: {origin}"
        ) from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            f"Invalid origin in MCP_ALLOWED_ORIGINS: {origin}"
        )
    effective_port = port or (443 if parsed.scheme == "https" else 80)
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{parsed.scheme}://{host}:{effective_port}"


def _resolve_from(base: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _validate_demo_paths(
    project_root: Path,
    demo_root: Path,
    git_repository: Path,
) -> None:
    if demo_root == project_root or project_root.is_relative_to(demo_root):
        raise ConfigurationError(
            "MCP_DEMO_ROOT must not expose the project root or one of its parents"
        )
    if not git_repository.is_relative_to(demo_root):
        raise ConfigurationError(
            "GIT_MCP_REPOSITORY must be inside MCP_DEMO_ROOT"
        )


def _validate_http_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as error:
        raise ConfigurationError("INVENTORY_MCP_URL is not a valid URL") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/mcp"
        or port is not None and not 1 <= port <= 65535
    ):
        raise ConfigurationError(
            "INVENTORY_MCP_URL must be an http(s) URL ending in /mcp "
            "without credentials, query, or fragment"
        )

"""Application configuration loaded from environment variables."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
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
DEFAULT_INVENTORY_MCP_HTTP_HOST = "127.0.0.1"
DEFAULT_INVENTORY_MCP_HTTP_PORT = 8000


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

    @classmethod
    def from_env(cls) -> "InventoryMCPConfig":
        transport = (
            os.getenv("INVENTORY_MCP_TRANSPORT", "stdio").strip().casefold()
        )
        if transport not in {"stdio", "http"}:
            raise ConfigurationError(
                "INVENTORY_MCP_TRANSPORT must be 'stdio' or 'http'"
            )
        host = os.getenv(
            "INVENTORY_MCP_HTTP_HOST", DEFAULT_INVENTORY_MCP_HTTP_HOST
        ).strip()
        if not host:
            raise ConfigurationError("INVENTORY_MCP_HTTP_HOST cannot be empty")
        port = _port_from_env(
            "INVENTORY_MCP_HTTP_PORT", DEFAULT_INVENTORY_MCP_HTTP_PORT
        )
        url = os.getenv(
            "INVENTORY_MCP_URL", f"http://{host}:{port}/mcp"
        ).strip()
        _validate_http_url(url)
        return cls(
            transport=transport,
            url=url,
            http_host=host,
            http_port=port,
        )


@dataclass(frozen=True, slots=True)
class ExternalMCPConfig:
    """Configuration for the optional local Filesystem and Git MCP servers."""

    filesystem_enabled: bool
    filesystem_command: tuple[str, ...]
    git_enabled: bool
    git_command: tuple[str, ...]
    demo_root: Path
    git_repository: Path

    @classmethod
    def from_env(cls, *, project_root: Path | None = None) -> "ExternalMCPConfig":
        root = (project_root or Path.cwd()).resolve()
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
        )


@dataclass(frozen=True, slots=True)
class ChatbotConfig:
    max_tool_iterations: int

    @classmethod
    def from_env(cls) -> "ChatbotConfig":
        return cls(
            max_tool_iterations=_positive_int_from_env("MAX_TOOL_ITERATIONS", 5)
        )


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

"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DATABASE_PATH = Path("data/inventory.db")
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MCP_LOG_PATH = Path("logs/mcp.jsonl")


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

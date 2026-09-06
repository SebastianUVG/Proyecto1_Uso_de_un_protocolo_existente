"""Tests for chatbot and provider environment configuration."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from inventory_assistant.config import (
    AnthropicConfig,
    ConfigurationError,
    ExternalMCPConfig,
    InventoryMCPConfig,
    OpenAIConfig,
    _load_environment_file,
)


class AnthropicConfigurationTests(unittest.TestCase):
    def test_api_key_is_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError):
                AnthropicConfig.from_env()

    def test_model_and_limits_are_configurable(self) -> None:
        environment = {
            "ANTHROPIC_API_KEY": "test-key-not-real",
            "ANTHROPIC_MODEL": "test-model",
            "ANTHROPIC_MAX_TOKENS": "500",
            "ANTHROPIC_TIMEOUT_SECONDS": "12.5",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = AnthropicConfig.from_env()
        self.assertEqual(config.model, "test-model")
        self.assertEqual(config.max_tokens, 500)
        self.assertEqual(config.timeout_seconds, 12.5)


class OpenAIConfigurationTests(unittest.TestCase):
    def test_loads_configuration_from_dotenv_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            env_path = Path(temporary_directory) / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=dotenv-test-key-not-real\n"
                "OPENAI_MODEL=dotenv-test-model\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                _load_environment_file(env_path)
                config = OpenAIConfig.from_env()
        self.assertEqual(config.api_key, "dotenv-test-key-not-real")
        self.assertEqual(config.model, "dotenv-test-model")

    def test_api_key_is_required_with_powershell_guidance(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigurationError) as raised:
                OpenAIConfig.from_env()
        self.assertIn("OPENAI_API_KEY", str(raised.exception))
        self.assertIn("PowerShell", str(raised.exception))
        self.assertNotIn("Traceback", str(raised.exception))

    def test_model_and_limits_are_configurable(self) -> None:
        environment = {
            "OPENAI_API_KEY": "test-key-not-real",
            "OPENAI_MODEL": "test-model",
            "OPENAI_MAX_OUTPUT_TOKENS": "500",
            "OPENAI_TIMEOUT_SECONDS": "12.5",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = OpenAIConfig.from_env()
        self.assertEqual(config.api_key, "test-key-not-real")
        self.assertEqual(config.model, "test-model")
        self.assertEqual(config.max_output_tokens, 500)
        self.assertEqual(config.timeout_seconds, 12.5)


class ExternalMCPConfigurationTests(unittest.TestCase):
    def test_external_servers_are_opt_in_and_use_project_demo_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            with patch.dict(os.environ, {}, clear=True):
                config = ExternalMCPConfig.from_env(project_root=project_root)
        self.assertFalse(config.filesystem_enabled)
        self.assertFalse(config.git_enabled)
        self.assertEqual(config.demo_root, project_root / "demo_workspace")
        self.assertEqual(config.git_repository, config.demo_root / "repository")
        self.assertEqual(config.filesystem_command[-1], str(config.demo_root))
        self.assertEqual(config.git_command[-2:], ("--repository", str(config.git_repository)))

    def test_external_commands_and_arguments_are_configurable(self) -> None:
        environment = {
            "FILESYSTEM_MCP_ENABLED": "true",
            "FILESYSTEM_MCP_COMMAND": "filesystem-command",
            "FILESYSTEM_MCP_ARGS": '["serve"]',
            "GIT_MCP_ENABLED": "1",
            "GIT_MCP_COMMAND": "git-command",
            "GIT_MCP_ARGS": '["serve-git"]',
            "MCP_DEMO_ROOT": "safe-demo",
            "GIT_MCP_REPOSITORY": "repo",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            with patch.dict(os.environ, environment, clear=True):
                config = ExternalMCPConfig.from_env(project_root=project_root)
        self.assertTrue(config.filesystem_enabled)
        self.assertTrue(config.git_enabled)
        self.assertEqual(config.filesystem_command[:2], ("filesystem-command", "serve"))
        self.assertEqual(config.git_command[:2], ("git-command", "serve-git"))

    def test_git_repository_cannot_escape_demo_root(self) -> None:
        environment = {"GIT_MCP_REPOSITORY": "../outside"}
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(ConfigurationError):
                    ExternalMCPConfig.from_env(
                        project_root=Path(temporary_directory)
                    )


class InventoryMCPConfigurationTests(unittest.TestCase):
    def test_stdio_and_localhost_http_are_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = InventoryMCPConfig.from_env()
        self.assertEqual(config.transport, "stdio")
        self.assertEqual(config.http_host, "127.0.0.1")
        self.assertEqual(config.http_port, 8000)
        self.assertEqual(config.url, "http://127.0.0.1:8000/mcp")

    def test_http_transport_host_port_and_url_are_configurable(self) -> None:
        environment = {
            "INVENTORY_MCP_TRANSPORT": "HTTP",
            "INVENTORY_MCP_HTTP_HOST": "localhost",
            "INVENTORY_MCP_HTTP_PORT": "8123",
            "INVENTORY_MCP_URL": "https://inventory.example/mcp",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = InventoryMCPConfig.from_env()
        self.assertEqual(config.transport, "http")
        self.assertEqual(config.http_host, "localhost")
        self.assertEqual(config.http_port, 8123)
        self.assertEqual(config.url, "https://inventory.example/mcp")

    def test_rejects_unknown_transport_invalid_port_and_non_mcp_url(self) -> None:
        invalid_environments = (
            {"INVENTORY_MCP_TRANSPORT": "websocket"},
            {"INVENTORY_MCP_HTTP_PORT": "70000"},
            {"INVENTORY_MCP_URL": "http://localhost:8000/not-mcp"},
            {"INVENTORY_MCP_URL": "file:///tmp/mcp"},
        )
        for environment in invalid_environments:
            with self.subTest(environment=environment):
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(ConfigurationError):
                        InventoryMCPConfig.from_env()


if __name__ == "__main__":
    unittest.main()

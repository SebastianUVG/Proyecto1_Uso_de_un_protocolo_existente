"""Tests for chatbot and provider environment configuration."""

from __future__ import annotations

import json
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
    WebConfig,
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

    def test_loads_arbitrary_stdio_servers_from_versionable_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            config_directory = project_root / "config"
            config_directory.mkdir()
            config_path = config_directory / "servers.json"
            config_path.write_text(
                json.dumps(
                    {
                        "servers": [
                            {
                                "name": "academic-planner",
                                "enabled": True,
                                "command": "${ACADEMIC_PYTHON}",
                                "args": ["-m", "src.server"],
                                "cwd": "vendor/academic",
                                "env": {"DATA_PATH": "${PROJECT_ROOT}/data/academic.db"},
                                "instructions": "Academic planning",
                            },
                            {
                                "name": "hotel",
                                "enabled": False,
                                "command": "${MISSING_WHILE_DISABLED}",
                                "args": ["-m", "hotel_mcp"],
                                "cwd": "vendor/hotel",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            environment = {
                "EXTERNAL_MCP_SERVERS_CONFIG": "config/servers.json",
                "ACADEMIC_PYTHON": "./venvs/academic/python",
            }
            with patch.dict(os.environ, environment, clear=True):
                config = ExternalMCPConfig.from_env(project_root=project_root)

        self.assertEqual(
            [server.name for server in config.servers],
            ["academic-planner", "hotel"],
        )
        academic = config.servers[0]
        self.assertTrue(academic.enabled)
        self.assertEqual(
            academic.command,
            str((project_root / "venvs/academic/python").resolve()),
        )
        self.assertEqual(academic.args, ("-m", "src.server"))
        self.assertEqual(
            academic.working_directory,
            (project_root / "vendor/academic").resolve(),
        )
        self.assertEqual(
            Path(academic.environment["DATA_PATH"]),
            project_root / "data/academic.db",
        )
        self.assertFalse(config.servers[1].enabled)

    def test_explicit_missing_or_invalid_external_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            with patch.dict(
                os.environ,
                {"EXTERNAL_MCP_SERVERS_CONFIG": "missing.json"},
                clear=True,
            ):
                with self.assertRaises(ConfigurationError):
                    ExternalMCPConfig.from_env(project_root=project_root)

            invalid_path = project_root / "invalid.json"
            invalid_path.write_text(
                '{"servers":[{"name":"bad__name","command":"python"}]}',
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"EXTERNAL_MCP_SERVERS_CONFIG": str(invalid_path)},
                clear=True,
            ):
                with self.assertRaises(ConfigurationError):
                    ExternalMCPConfig.from_env(project_root=project_root)


class InventoryMCPConfigurationTests(unittest.TestCase):
    def test_stdio_and_localhost_http_are_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = InventoryMCPConfig.from_env()
        self.assertEqual(config.transport, "stdio")
        self.assertEqual(config.http_host, "127.0.0.1")
        self.assertEqual(config.http_port, 8000)
        self.assertEqual(config.url, "http://127.0.0.1:8000/mcp")
        self.assertIsNone(config.allowed_origins)
        self.assertIsNone(config.auth_token)

    def test_render_port_is_used_without_changing_local_defaults(self) -> None:
        with patch.dict(os.environ, {"PORT": "9090"}, clear=True):
            config = InventoryMCPConfig.from_env()
        self.assertEqual(config.http_host, "0.0.0.0")
        self.assertEqual(config.http_port, 9090)
        self.assertEqual(config.url, "http://0.0.0.0:9090/mcp")

    def test_explicit_inventory_port_has_priority_over_cloud_port(self) -> None:
        environment = {
            "PORT": "9090",
            "INVENTORY_MCP_HTTP_HOST": "localhost",
            "INVENTORY_MCP_HTTP_PORT": "8123",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = InventoryMCPConfig.from_env()
        self.assertEqual(config.http_host, "localhost")
        self.assertEqual(config.http_port, 8123)

    def test_render_environment_uses_public_bind_with_local_port_fallback(self) -> None:
        with patch.dict(os.environ, {"RENDER": "true"}, clear=True):
            config = InventoryMCPConfig.from_env()
        self.assertEqual(config.http_host, "0.0.0.0")
        self.assertEqual(config.http_port, 8000)

    def test_render_host_can_be_explicitly_overridden(self) -> None:
        environment = {
            "RENDER": "true",
            "PORT": "10000",
            "INVENTORY_MCP_HTTP_HOST": "127.0.0.1",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = InventoryMCPConfig.from_env()
        self.assertEqual(config.http_host, "127.0.0.1")
        self.assertEqual(config.http_port, 10000)

    def test_allowed_origins_and_optional_auth_token_are_configurable(self) -> None:
        environment = {
            "MCP_ALLOWED_ORIGINS": (
                "https://client.example, http://localhost:8080/, "
                "https://client.example"
            ),
            "INVENTORY_MCP_AUTH_TOKEN": " test-token-not-real ",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = InventoryMCPConfig.from_env()
        self.assertEqual(
            config.allowed_origins,
            ("https://client.example:443", "http://localhost:8080"),
        )
        self.assertEqual(config.auth_token, "test-token-not-real")

    def test_invalid_or_wildcard_allowed_origins_are_rejected(self) -> None:
        invalid_values = (
            "*",
            "https://trusted.example/path",
            "https://trusted.example,",
            "not-an-origin",
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with patch.dict(
                    os.environ,
                    {"MCP_ALLOWED_ORIGINS": value},
                    clear=True,
                ):
                    with self.assertRaises(ConfigurationError):
                        InventoryMCPConfig.from_env()

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


class WebConfigurationTests(unittest.TestCase):
    def test_localhost_and_non_conflicting_port_are_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = WebConfig.from_env()
        self.assertEqual(config.host, "127.0.0.1")
        self.assertEqual(config.port, 8080)

    def test_host_and_port_are_configurable_and_validated(self) -> None:
        with patch.dict(
            os.environ,
            {"WEB_HOST": "localhost", "WEB_PORT": "8124"},
            clear=True,
        ):
            config = WebConfig.from_env()
        self.assertEqual(config.host, "localhost")
        self.assertEqual(config.port, 8124)

        for environment in ({"WEB_HOST": "  "}, {"WEB_PORT": "70000"}):
            with self.subTest(environment=environment):
                with patch.dict(os.environ, environment, clear=True):
                    with self.assertRaises(ConfigurationError):
                        WebConfig.from_env()


if __name__ == "__main__":
    unittest.main()

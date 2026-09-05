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


if __name__ == "__main__":
    unittest.main()

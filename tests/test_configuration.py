"""Tests for chatbot and provider environment configuration."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from inventory_assistant.config import AnthropicConfig, ConfigurationError


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


if __name__ == "__main__":
    unittest.main()


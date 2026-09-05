"""Tests for the isolated Filesystem and Git MCP demonstration workspace."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from inventory_assistant.config import ExternalMCPConfig
from inventory_assistant.mcp.demo_workspace import prepare_demo_workspace


class DemoWorkspaceTests(unittest.TestCase):
    def test_prepare_is_idempotent_and_sets_repository_local_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            demo_root = Path(temporary_directory) / "sandbox"
            repository = demo_root / "repository"
            config = ExternalMCPConfig(
                filesystem_enabled=True,
                filesystem_command=("fake-filesystem", str(demo_root)),
                git_enabled=True,
                git_command=("fake-git", "--repository", str(repository)),
                demo_root=demo_root,
                git_repository=repository,
            )
            prepare_demo_workspace(config)
            prepare_demo_workspace(config)

            self.assertTrue((repository / ".git").is_dir())
            name = subprocess.run(
                ("git", "-C", str(repository), "config", "user.name"),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            email = subprocess.run(
                ("git", "-C", str(repository), "config", "user.email"),
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(name, "MCP Demo")
            self.assertEqual(email, "mcp-demo@example.invalid")


if __name__ == "__main__":
    unittest.main()

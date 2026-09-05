"""Prepare the isolated workspace used by Filesystem and Git MCP demos."""

from __future__ import annotations

import subprocess

from inventory_assistant.config import ConfigurationError, ExternalMCPConfig


def prepare_demo_workspace(config: ExternalMCPConfig) -> None:
    """Create the allowed root and initialize only its configured Git repository."""

    config.demo_root.mkdir(parents=True, exist_ok=True)
    config.git_repository.mkdir(parents=True, exist_ok=True)
    _run_git("init", str(config.git_repository))
    _run_git("-C", str(config.git_repository), "config", "user.name", "MCP Demo")
    _run_git(
        "-C",
        str(config.git_repository),
        "config",
        "user.email",
        "mcp-demo@example.invalid",
    )


def _run_git(*arguments: str) -> None:
    try:
        subprocess.run(
            ("git", *arguments),
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise ConfigurationError("Git is required to prepare the demo workspace") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or "Git command failed").strip()
        raise ConfigurationError(detail) from error


def main() -> None:
    try:
        config = ExternalMCPConfig.from_env()
        prepare_demo_workspace(config)
    except (ConfigurationError, OSError) as error:
        print(f"Unable to prepare MCP demo workspace: {error}")
        raise SystemExit(1) from error
    print(f"Filesystem MCP root: {config.demo_root}")
    print(f"Git MCP repository: {config.git_repository}")


if __name__ == "__main__":
    main()

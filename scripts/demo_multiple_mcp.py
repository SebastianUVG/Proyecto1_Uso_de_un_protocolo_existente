"""Run a deterministic no-API demonstration against all configured MCP servers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from inventory_assistant.chatbot.session import ChatbotSession
from inventory_assistant.config import (
    ExternalMCPConfig,
    MCPClientConfig,
)
from inventory_assistant.llm import (
    ConversationMessage,
    LLMResponse,
    LLMTool,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from inventory_assistant.mcp.manager import (
    MCPServerManager,
    configured_server_definitions,
)


class DemonstrationLLMProvider:
    """Script tool-use responses for a repeatable demo without API credits."""

    def __init__(self, repository: str) -> None:
        generated_at = datetime.now(timezone.utc).isoformat()
        readme = (
            "# MCP Multi-Server Demo\n\n"
            "Created through Filesystem MCP and committed through Git MCP.\n\n"
            f"Demonstration run: {generated_at}\n"
        )
        readme_path = f"{repository}/README.md"
        self._responses = [
            LLMResponse(
                blocks=(
                    ToolUseBlock(
                        "demo-write",
                        "filesystem__write_file",
                        {"path": readme_path, "content": readme},
                    ),
                    ToolUseBlock(
                        "demo-inventory",
                        "inventory__get_low_stock_products",
                        {"limit": 2},
                    ),
                ),
                stop_reason="tool_use",
            ),
            LLMResponse(
                blocks=(
                    ToolUseBlock(
                        "demo-read",
                        "filesystem__read_text_file",
                        {"path": readme_path},
                    ),
                    ToolUseBlock(
                        "demo-add",
                        "git__git_add",
                        {"repo_path": repository, "files": ["README.md"]},
                    ),
                ),
                stop_reason="tool_use",
            ),
            LLMResponse(
                blocks=(
                    ToolUseBlock(
                        "demo-commit",
                        "git__git_commit",
                        {
                            "repo_path": repository,
                            "message": "docs: add MCP demonstration README",
                        },
                    ),
                ),
                stop_reason="tool_use",
            ),
            LLMResponse(
                blocks=(
                    ToolUseBlock(
                        "demo-status",
                        "git__git_status",
                        {"repo_path": repository},
                    ),
                    ToolUseBlock(
                        "demo-log",
                        "git__git_log",
                        {"repo_path": repository, "max_count": 1},
                    ),
                ),
                stop_reason="tool_use",
            ),
            LLMResponse(
                blocks=(
                    TextBlock(
                        "The deterministic multi-server demonstration is complete."
                    ),
                ),
                stop_reason="end_turn",
            ),
        ]

    def generate(
        self,
        messages: Sequence[ConversationMessage],
        tools: Sequence[LLMTool],
        *,
        system_prompt: str,
    ) -> LLMResponse:
        if not self._responses:
            raise RuntimeError("The demonstration tool-use sequence is exhausted")
        if messages and isinstance(messages[-1].content, tuple):
            failed = [
                block
                for block in messages[-1].content
                if isinstance(block, ToolResultBlock) and block.is_error
            ]
            if failed:
                raise RuntimeError(
                    "An MCP tool failed during the deterministic demonstration: "
                    f"{failed[0].content}"
                )
        return self._responses.pop(0)


def main() -> None:
    external = ExternalMCPConfig.from_env()
    if not external.filesystem_enabled or not external.git_enabled:
        raise SystemExit(
            "Enable FILESYSTEM_MCP_ENABLED and GIT_MCP_ENABLED before this demo."
        )
    manager = MCPServerManager(
        MCPClientConfig.from_env(),
        configured_server_definitions(external),
    )
    with manager:
        print("Discovered MCP tools:")
        for status in manager.statuses:
            print(f"- {status.name}: {', '.join(status.tools)}")
        available = {tool["name"] for tool in manager.list_tools()}
        required = {
            "filesystem__write_file",
            "filesystem__read_text_file",
            "git__git_add",
            "git__git_commit",
            "git__git_status",
            "git__git_log",
            "inventory__get_low_stock_products",
        }
        missing = sorted(required - available)
        if missing:
            raise SystemExit(f"Required tools were not discovered: {', '.join(missing)}")
        provider = DemonstrationLLMProvider(str(external.git_repository))
        session = ChatbotSession(provider, manager)
        answer = session.ask(
            "Create a small demo project with a README, stage it, and commit it."
        )
        print(f"\nAssistant: {answer}")


if __name__ == "__main__":
    main()

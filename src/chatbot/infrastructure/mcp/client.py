from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import Tool as MCPTool

from ...domain.models import ToolDefinition
from ...config import get_settings


@dataclass
class MCPServerConfig:
    """MCP server configuration."""
    name: str
    command: str
    args: List[str]
    env: Optional[Dict[str, str]] = None


class MCPClientManager:
    """
    Manages connections to MCP (Model Context Protocol) servers.
    Handles connection lifecycle, tool discovery, and tool calling.
    """

    def __init__(self, config_path: Optional[str] = None):
        settings = get_settings()
        self.config_path = config_path or settings.mcp_config_path
        self.sessions: Dict[str, ClientSession] = {}
        self.exit_stacks: Dict[str, AsyncExitStack] = {}
        self.tools_cache: Dict[str, List[MCPTool]] = {}
        self.configs: Dict[str, MCPServerConfig] = {}

    def load_config(self) -> Dict[str, MCPServerConfig]:
        """Load MCP server configurations from JSON file."""
        if not os.path.exists(self.config_path):
            return {}

        with open(self.config_path, "r") as f:
            data = json.load(f)

        configs = {}
        for name, server_config in data.get("mcpServers", {}).items():
            command = server_config.get("command", "")
            args = server_config.get("args", [])

            # Security: Validate command is in allowed list
            # Only npx/npx.cmd, uvx/uv, bun are allowed (no python, node, etc.)
            if command not in {"npx", "npx.cmd", "uvx", "uv", "bun"}:
                print(f"Warning: Skipping server '{name}' - command '{command}' not in allowed list")
                continue

            # Security: Validate args
            validated_args = []
            for arg in args:
                if isinstance(arg, str):
                    # Block path traversal attempts
                    if ".." in arg:
                        print(f"Warning: Skipping suspicious arg '{arg}' for server '{name}' (path traversal)")
                        continue
                    # Allow common safe patterns
                    if arg.startswith("@") or arg.startswith("-") or arg.isalnum() or "/" in arg:
                        validated_args.append(arg)
                    else:
                        print(f"Warning: Skipping suspicious arg '{arg}' for server '{name}'")
                else:
                    print(f"Warning: Skipping non-string arg for server '{name}'")
                    continue

            configs[name] = MCPServerConfig(
                name=name,
                command=command,
                args=validated_args,
                env=server_config.get("env")
            )
        self.configs = configs
        return configs

    async def connect_all(self, timeout: float = 30.0) -> Dict[str, List[MCPTool]]:
        """Connect to all configured MCP servers."""
        configs = self.load_config()
        results = {}

        async def connect_one(name: str, config: MCPServerConfig) -> tuple[str, List[MCPTool]]:
            try:
                tools = await self.connect_server(name, config, timeout)
                return name, tools
            except Exception as e:
                print(f"Failed to connect to MCP server {name}: {e}")
                return name, []

        # Connect in parallel
        tasks = [connect_one(name, config) for name, config in configs.items()]
        completed = await asyncio.gather(*tasks)

        for name, tools in completed:
            results[name] = tools

        return results

    async def connect_server(
        self,
        name: str,
        config: MCPServerConfig,
        timeout: float = 30.0
    ) -> List[MCPTool]:
        """Connect to a single MCP server."""
        # Clean up existing connection if any
        await self.disconnect_server(name)

        # Handle Windows npx
        command = config.command
        if os.name == 'nt' and command == 'npx':
            command = 'npx.cmd'

        # Prepare environment
        env = os.environ.copy()
        if config.env:
            env.update(config.env)

        server_params = StdioServerParameters(
            command=command,
            args=config.args,
            env=env
        )

        stack = AsyncExitStack()
        self.exit_stacks[name] = stack

        read, write = await stack.enter_async_context(stdio_client(server_params))
        session = await stack.enter_async_context(ClientSession(read, write))

        # Initialize with timeout
        await asyncio.wait_for(session.initialize(), timeout=timeout)

        self.sessions[name] = session

        # Fetch tools
        tools_response = await asyncio.wait_for(session.list_tools(), timeout=timeout)
        self.tools_cache[name] = tools_response.tools

        print(f"Connected to MCP Server: {name} (Tools: {len(self.tools_cache[name])})")
        return self.tools_cache[name]

    async def disconnect_server(self, name: str) -> bool:
        """Disconnect from a specific MCP server."""
        if name in self.exit_stacks:
            try:
                await self.exit_stacks[name].aclose()
            except Exception:
                pass
            del self.exit_stacks[name]

        self.sessions.pop(name, None)
        self.tools_cache.pop(name, None)
        return True

    async def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool on an MCP server."""
        if server_name not in self.sessions:
            return f"Error: MCP Server '{server_name}' not connected"

        session = self.sessions[server_name]

        try:
            result = await session.call_tool(tool_name, arguments)
            if result.isError:
                return f"Error from {server_name}: {result.content}"
            else:
                # Extract text content
                texts = []
                for content in result.content:
                    if hasattr(content, 'text'):
                        texts.append(content.text)
                return "\n".join(texts) if texts else "Tool executed successfully (no output)"
        except Exception as e:
            return f"Exception calling tool {tool_name} on {server_name}: {e}"

    async def get_tools(self) -> List[ToolDefinition]:
        """Get all tools from all connected MCP servers as ToolDefinitions."""
        tools = []
        for server_name, mcp_tools in self.tools_cache.items():
            for tool in mcp_tools:
                # Convert MCP tool to our ToolDefinition
                tools.append(ToolDefinition(
                    name=f"mcp_{server_name}__{tool.name}",
                    description=tool.description or f"MCP tool {tool.name} from {server_name}",
                    parameters=tool.inputSchema
                ))
        return tools

    def get_connected_servers(self) -> Dict[str, int]:
        """Get dict of connected server names to tool counts."""
        return {name: len(tools) for name, tools in self.tools_cache.items()}

    async def cleanup(self) -> None:
        """Clean up all connections."""
        for name in list(self.exit_stacks.keys()):
            await self.disconnect_server(name)
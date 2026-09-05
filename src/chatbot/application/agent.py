from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from ..domain.models import Message, Role, Session, ToolDefinition, ToolProvider
from ..domain.workspace import Workspace
from ..infrastructure.llm.nim import NIMLLMProvider
from ..infrastructure.mcp.client import MCPClientManager
from ..config import get_settings, Settings


@dataclass
class AgentEvent:
    """Events emitted by the agent during execution."""
    role: str  # assistant, tool_executing, tool_result, error
    content: Optional[str] = None
    name: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class AgentConfig:
    """Configuration for the agent."""
    api_key: str
    model: str = "meta/llama-3.1-70b-instruct"
    temperature: float = 0.2
    max_tokens: Optional[int] = None
    system_prompt: str = (
        "You are a helpful coding agent. You have access to local file operations "
        "and MCP (Model Context Protocol) tools. Use these tools to accomplish the user's task."
    )
    workspace_root: Optional[str] = None
    mcp_config_path: Optional[str] = None


class ToolExecutor:
    """Executes tools from various providers."""

    def __init__(self, providers: List[ToolProvider]):
        self.providers = providers
        self._tool_map: Dict[str, ToolProvider] = {}
        self._build_tool_map()

    def _build_tool_map(self):
        """Build mapping of tool names to providers."""
        for provider in self.providers:
            # Note: This is called after tools are fetched
            pass

    def register_tools(self, tools: List[ToolDefinition], provider: ToolProvider):
        """Register tools from a provider."""
        for tool in tools:
            self._tool_map[tool.name] = provider

    async def execute(self, name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool by name."""
        provider = self._tool_map.get(name)
        if not provider:
            return f"Error: Tool '{name}' not found in any provider"
        try:
            return await provider.call_tool(name, arguments)
        except Exception as e:
            return f"Error executing tool '{name}': {e}"


class NIMAgent:
    """
    NVIDIA NIM Coding Agent.
    Orchestrates LLM calls, tool execution, and conversation management.
    """

    def __init__(self, config: AgentConfig, mcp_manager: Optional[MCPClientManager] = None):
        self.config = config
        self.settings = get_settings()

        # Initialize workspace
        self.workspace = Workspace(config.workspace_root or self.settings.workspace_root)

        # Initialize LLM provider
        self.llm = NIMLLMProvider(
            api_key=config.api_key,
            model=config.model,
            base_url=self.settings.nim_base_url,
            timeout=self.settings.request_timeout
        )

        # Initialize tool providers
        self._owns_mcp = False
        if mcp_manager:
            self.mcp_manager = mcp_manager
        else:
            self._owns_mcp = True
            self.mcp_manager = MCPClientManager(
                self.settings.mcp_config_path
                if config.mcp_config_path is None
                else Path(config.mcp_config_path)
            )

        # Initialize executor
        self.executor = ToolExecutor([self.workspace, self.mcp_manager])

        # Register local tools
        self.executor.register_tools(self.workspace.get_tools(), self.workspace)

        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the agent (connect to MCP servers)."""
        if self._initialized:
            return

        # Connect to MCP servers only if we own the manager
        if getattr(self, '_owns_mcp', True):
            await self.mcp_manager.connect_all()

        # Register MCP tools
        mcp_tools = await self.mcp_manager.get_tools()
        self.executor.register_tools(mcp_tools, self.mcp_manager)

        self._initialized = True

    async def close(self) -> None:
        """Clean up resources."""
        if getattr(self, '_owns_mcp', False):
            await self.mcp_manager.cleanup()
        await self.llm.close()

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def run(
        self,
        messages: List[Message],
        yield_callback: Optional[Callable[[AgentEvent], Any]] = None
    ) -> List[Message]:
        """
        Run the agent loop until completion.
        Yields events via callback for UI updates.
        """
        if not self._initialized:
            await self.initialize()

        # Build initial messages with system prompt
        system_msg = Message.system(self.config.system_prompt)
        current_messages = [system_msg] + messages

        # Get all available tools
        all_tools = self.workspace.get_tools()
        mcp_tools = await self.mcp_manager.get_tools()
        all_tools.extend(mcp_tools)

        # Event emitter
        async def emit(event: AgentEvent):
            if yield_callback:
                if asyncio.iscoroutinefunction(yield_callback):
                    await yield_callback(event)
                else:
                    yield_callback(event)

        while True:
            try:
                # Get LLM response
                response = await self.llm.chat_completion(
                    messages=current_messages,
                    tools=all_tools,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    tool_choice="auto"
                )
            except Exception as e:
                error_msg = f"Error calling NVIDIA NIM API: {e}"
                await emit(AgentEvent(role="error", error=error_msg))
                current_messages.append(Message.assistant(content=error_msg))
                return current_messages

            current_messages.append(response)

            # Emit assistant content
            if response.content:
                await emit(AgentEvent(role="assistant", content=response.content))

            # No tool calls = done
            if not response.tool_calls:
                return current_messages

            # Execute tool calls
            for tool_call in response.tool_calls:
                name = tool_call["name"]
                arguments = tool_call["arguments"]
                tool_call_id = tool_call["id"]

                await emit(AgentEvent(
                    role="tool_executing",
                    name=name,
                    arguments=arguments
                ))

                try:
                    result = await self.executor.execute(name, arguments)
                except Exception as e:
                    result = f"Error executing tool {name}: {e}"

                await emit(AgentEvent(
                    role="tool_result",
                    name=name,
                    result=result
                ))

                current_messages.append(Message.tool_result(
                    tool_call_id=tool_call_id,
                    name=name,
                    content=str(result)
                ))

    async def run_stream(
        self,
        messages: List[Message],
        yield_callback: Optional[Callable[[AgentEvent], Any]] = None
    ) -> List[Message]:
        """
        Run the agent loop with streaming support.
        Yields content deltas via callback for real-time UI updates.
        """
        if not self._initialized:
            await self.initialize()

        # Build initial messages with system prompt
        system_msg = Message.system(self.config.system_prompt)
        current_messages = [system_msg] + messages

        # Get all available tools
        all_tools = self.workspace.get_tools()
        mcp_tools = await self.mcp_manager.get_tools()
        all_tools.extend(mcp_tools)

        # Event emitter
        async def emit(event: AgentEvent):
            if yield_callback:
                if asyncio.iscoroutinefunction(yield_callback):
                    await yield_callback(event)
                else:
                    yield_callback(event)

        while True:
            try:
                # Get streaming LLM response
                full_content = ""
                tool_calls_buffer = {}

                async for delta in self.llm.chat_completion_stream(
                    messages=current_messages,
                    tools=all_tools,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    tool_choice="auto"
                ):
                    if delta["type"] == "content":
                        content = delta["content"]
                        full_content += content
                        await emit(AgentEvent(role="assistant", content=content))

                    elif delta["type"] == "tool_call":
                        index = delta["index"]
                        tool_call = delta["tool_call"]
                        if index not in tool_calls_buffer:
                            tool_calls_buffer[index] = {"id": "", "name": "", "arguments": ""}
                        # Update buffer
                        for key, value in tool_call.items():
                            if value:
                                tool_calls_buffer[index][key] = value

                    elif delta["type"] == "done":
                        finish_reason = delta.get("finish_reason")
                        completed_tool_calls = delta.get("tool_calls")

                        # Create the assistant message
                        if full_content or completed_tool_calls:
                            assistant_msg = Message(
                                role=Role.ASSISTANT,
                                content=full_content if full_content else None,
                                tool_calls=completed_tool_calls if completed_tool_calls else []
                            )
                            current_messages.append(assistant_msg)

                        if finish_reason == "tool_calls" and completed_tool_calls:
                            # Execute tool calls
                            for tool_call in completed_tool_calls:
                                name = tool_call["name"]
                                arguments = tool_call.get("arguments", {})
                                # Parse arguments if it's a string
                                if isinstance(arguments, str):
                                    try:
                                        arguments = json.loads(arguments)
                                    except json.JSONDecodeError:
                                        arguments = {}

                                await emit(AgentEvent(
                                    role="tool_executing",
                                    name=name,
                                    arguments=arguments
                                ))

                                try:
                                    result = await self.executor.execute(name, arguments)
                                except Exception as e:
                                    result = f"Error executing tool {name}: {e}"

                                await emit(AgentEvent(
                                    role="tool_result",
                                    name=name,
                                    result=result
                                ))

                                current_messages.append(Message.tool_result(
                                    tool_call_id=tool_call.get("id", ""),
                                    name=name,
                                    content=str(result)
                                ))

                        if finish_reason == "stop" or not completed_tool_calls:
                            return current_messages

                        # Continue the loop for tool calls
                        break

            except Exception as e:
                error_msg = f"Error calling NVIDIA NIM API: {e}"
                await emit(AgentEvent(role="error", error=error_msg))
                current_messages.append(Message.assistant(content=error_msg))
                return current_messages


async def run_agent(
    api_key: str,
    model: str,
    messages: List[Message],
    yield_callback: Optional[Callable[[AgentEvent], Any]] = None,
    **config_kwargs
) -> List[Message]:
    """
    Convenience function to run agent without manual lifecycle management.
    Creates and cleans up agent automatically.
    """
    config = AgentConfig(api_key=api_key, model=model, **config_kwargs)
    async with NIMAgent(config) as agent:
        return await agent.run_stream(messages, yield_callback)
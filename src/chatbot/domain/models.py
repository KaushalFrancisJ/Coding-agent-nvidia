from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol
from uuid import uuid4


class Role(str, Enum):
    """Message roles."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """Chat message."""
    role: Role
    content: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    name: Optional[str] = None  # For tool messages

    def to_openai_dict(self) -> Dict[str, Any]:
        """Convert to OpenAI message format."""
        d = {"role": self.role.value}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role=Role.USER, content=content)

    @classmethod
    def assistant(cls, content: Optional[str] = None, tool_calls: Optional[List[Dict]] = None) -> "Message":
        return cls(role=Role.ASSISTANT, content=content, tool_calls=tool_calls or [])

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role=Role.SYSTEM, content=content)

    @classmethod
    def tool_result(cls, tool_call_id: str, name: str, content: str) -> "Message":
        return cls(role=Role.TOOL, content=content, tool_call_id=tool_call_id, name=name)


@dataclass
class ToolDefinition:
    """Tool definition for LLM."""
    name: str
    description: str
    parameters: Dict[str, Any]

    def to_openai_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }


@dataclass
class Session:
    """Chat session metadata."""
    id: str = field(default_factory=lambda: str(uuid4()))
    title: str = "New Chat"
    created_at: str = field(default_factory=lambda: __import__('datetime').datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: __import__('datetime').datetime.now().isoformat())
    model: str = ""
    message_count: int = 0


class ToolProvider(Protocol):
    """Protocol for tool providers (local, MCP, etc.)."""

    async def get_tools(self) -> List[ToolDefinition]:
        """Get all available tools."""
        ...

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        """Call a tool by name."""
        ...


class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    async def chat_completion(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        tool_choice: str = "auto"
    ) -> Message:
        """Get a chat completion."""
        ...

    async def close(self) -> None:
        """Close the provider."""
        ...


class SessionStore(Protocol):
    """Protocol for session storage."""

    async def save(self, session_id: str, messages: List[Message], metadata: Optional[Dict] = None) -> None:
        ...

    async def load(self, session_id: str) -> Optional[List[Message]]:
        ...

    async def list_sessions(self) -> List[Session]:
        ...

    async def delete(self, session_id: str) -> bool:
        ...
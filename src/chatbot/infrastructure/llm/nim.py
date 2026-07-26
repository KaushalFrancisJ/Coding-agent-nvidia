from __future__ import annotations

import os
from typing import Any, AsyncGenerator, Dict, List, Optional

from openai import AsyncOpenAI

from ...domain.models import Message, ToolDefinition, LLMProvider, Role
from ...config import get_settings


class NIMLLMProvider(LLMProvider):
    """
    NVIDIA NIM LLM Provider.
    Uses OpenAI-compatible API via NVIDIA's inference endpoints.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "meta/llama-3.1-70b-instruct",
        base_url: Optional[str] = None,
        timeout: float = 60.0
    ):
        settings = get_settings()
        self.client = AsyncOpenAI(
            base_url=base_url or settings.nim_base_url,
            api_key=api_key,
            timeout=timeout
        )
        self.model = model
        self.default_temperature = settings.nim_temperature
        self.default_max_tokens = settings.nim_max_tokens

    async def chat_completion(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        tool_choice: str = "auto"
    ) -> Message:
        """
        Get a chat completion from the NVIDIA NIM API (non-streaming).
        """
        openai_messages = [msg.to_openai_dict() for msg in messages]
        openai_tools = [tool.to_openai_schema() for tool in tools]

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            tools=openai_tools if openai_tools else None,
            tool_choice=tool_choice if openai_tools else "none",
            temperature=temperature,
            max_tokens=max_tokens or self.default_max_tokens,
            parallel_tool_calls=False
        )

        choice = response.choices[0]
        message = choice.message

        return Message(
            role=Role(message.role),
            content=message.content,
            tool_calls=[
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or {}
                }
                for tc in (message.tool_calls or [])
            ]
        )

    async def chat_completion_stream(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        tool_choice: str = "auto"
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Get a streaming chat completion from the NVIDIA NIM API.

        Yields dicts with:
        - "content": str (delta content)
        - "tool_calls": list (delta tool calls, if any)
        - "finish_reason": str (when complete)
        """
        openai_messages = [msg.to_openai_dict() for msg in messages]
        openai_tools = [tool.to_openai_schema() for tool in tools]

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            tools=openai_tools if openai_tools else None,
            tool_choice=tool_choice if openai_tools else "none",
            temperature=temperature,
            max_tokens=max_tokens or self.default_max_tokens,
            parallel_tool_calls=False,
            stream=True
        )

        tool_calls_buffer = {}

        async for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Yield content delta
            if delta.content:
                yield {
                    "type": "content",
                    "content": delta.content
                }

            # Handle tool calls streaming
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    index = tc.index
                    if index not in tool_calls_buffer:
                        tool_calls_buffer[index] = {"id": "", "name": "", "arguments": ""}

                    if tc.id:
                        tool_calls_buffer[index]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            tool_calls_buffer[index]["name"] = tc.function.name
                        if tc.function.arguments:
                            tool_calls_buffer[index]["arguments"] += tc.function.arguments

                    yield {
                        "type": "tool_call",
                        "index": index,
                        "tool_call": tool_calls_buffer[index].copy()
                    }

            # Check if done
            if choice.finish_reason:
                yield {
                    "type": "done",
                    "finish_reason": choice.finish_reason,
                    "tool_calls": list(tool_calls_buffer.values()) if tool_calls_buffer else None
                }

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.close()
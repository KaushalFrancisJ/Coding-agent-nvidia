from __future__ import annotations

import json
from typing import List, Optional, AsyncIterator

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

from .models import Message, ToolDefinition, LLMProvider, Role


class NIMLLMProvider(LLMProvider):
    """NVIDIA NIM API provider using OpenAI-compatible client."""

    def __init__(
        self,
        api_key: str,
        model: str = "meta/llama-3.1-70b-instruct",
        base_url: str = "https://integrate.api.nvidia.com/v1",
        timeout: float = 60.0
    ):
        self.model = model
        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout
        )

    async def chat_completion(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        tool_choice: str = "auto"
    ) -> Message:
        """Get a chat completion from NVIDIA NIM."""
        openai_messages = [m.to_openai_dict() for m in messages]
        openai_tools = [t.to_openai_schema() for t in tools]

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            tools=openai_tools or None,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
            parallel_tool_calls=False
        )

        choice = response.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": args
                })

        return Message(
            role=Role.ASSISTANT,
            content=msg.content,
            tool_calls=tool_calls
        )

    async def close(self) -> None:
        """Close the client."""
        await self.client.close()
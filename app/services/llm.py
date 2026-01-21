"""
Doubao/Volcano LLM (Large Language Model) Service

Based on Volcano Engine Ark API (OpenAI-compatible interface).
"""

import asyncio
import httpx
import json
import logging
import inspect
from typing import AsyncGenerator, Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Message:
    """Chat message"""
    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class LLMResponse:
    """LLM response"""
    content: str
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None


@dataclass
class StreamChunk:
    """Streaming response chunk"""
    content: str
    is_final: bool = False
    finish_reason: Optional[str] = None


class LLMService:
    """Doubao/Volcano LLM Service"""

    def __init__(self):
        self.api_key = settings.doubao_api_key
        self.model_id = settings.doubao_model_id
        self.base_url = settings.doubao_api_base_url

    def _get_headers(self) -> dict:
        """Get API headers"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float = 0.9,
    ) -> Optional[LLMResponse]:
        """
        Send chat completion request.

        Args:
            messages: List of messages
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response
            top_p: Top-p sampling parameter

        Returns:
            LLM response or None on error
        """
        if not self.api_key:
            logger.error("LLM service not configured")
            return None

        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": self.model_id,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    url,
                    headers=self._get_headers(),
                    json=payload,
                )
                response.raise_for_status()

                data = response.json()
                choice = data.get("choices", [{}])[0]

                return LLMResponse(
                    content=choice.get("message", {}).get("content", ""),
                    finish_reason=choice.get("finish_reason"),
                    usage=data.get("usage"),
                )

        except Exception as e:
            logger.error(f"LLM chat error: {e}")
            return None

    async def chat_stream(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float = 0.9,
        interrupt_check: Optional[Callable[[], bool]] = None,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        Stream chat completion response.

        Args:
            messages: List of messages
            temperature: Sampling temperature (0-2)
            max_tokens: Maximum tokens in response
            top_p: Top-p sampling parameter
            interrupt_check: Optional function to check if generation should be interrupted

        Yields:
            Stream chunks with content
        """
        if not self.api_key:
            logger.error("LLM service not configured")
            return

        url = f"{self.base_url}/chat/completions"

        payload = {
            "model": self.model_id,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": top_p,
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=self._get_headers(),
                    json=payload,
                ) as response:
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        # Check for interrupt (sync or async)
                        if interrupt_check:
                            try:
                                should_interrupt = interrupt_check()
                                if inspect.isawaitable(should_interrupt):
                                    should_interrupt = await should_interrupt
                                if should_interrupt:
                                    logger.info("LLM generation interrupted")
                                    break
                            except Exception as exc:
                                logger.warning(f"LLM interrupt check failed: {exc}")

                        if not line:
                            continue

                        # Parse SSE format
                        if line.startswith("data: "):
                            data_str = line[6:]

                            if data_str == "[DONE]":
                                yield StreamChunk(content="", is_final=True)
                                break

                            try:
                                data = json.loads(data_str)
                                choice = data.get("choices", [{}])[0]
                                delta = choice.get("delta", {})
                                content = delta.get("content", "")
                                finish_reason = choice.get("finish_reason")

                                if content:
                                    yield StreamChunk(
                                        content=content,
                                        is_final=finish_reason is not None,
                                        finish_reason=finish_reason,
                                    )
                                elif finish_reason:
                                    yield StreamChunk(
                                        content="",
                                        is_final=True,
                                        finish_reason=finish_reason,
                                    )

                            except json.JSONDecodeError:
                                continue

        except Exception as e:
            logger.error(f"LLM stream error: {e}")
            yield StreamChunk(content="", is_final=True, finish_reason="error")

    async def generate_with_context(
        self,
        system_prompt: str,
        user_message: str,
        history: Optional[List[Message]] = None,
        context_vars: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        Generate response with context and history.

        Args:
            system_prompt: System prompt (can contain {variable} placeholders)
            user_message: Current user message
            history: Conversation history
            context_vars: Variables to substitute in system prompt
            **kwargs: Additional parameters for chat_stream

        Yields:
            Stream chunks with content
        """
        # Substitute context variables in system prompt
        if context_vars:
            for key, value in context_vars.items():
                system_prompt = system_prompt.replace(f"{{{key}}}", value)

        # Build message list
        messages = [Message(role="system", content=system_prompt)]

        # Add history
        if history:
            messages.extend(history)

        # Add current user message
        messages.append(Message(role="user", content=user_message))

        async for chunk in self.chat_stream(messages, **kwargs):
            yield chunk


# Singleton instance
llm_service = LLMService()

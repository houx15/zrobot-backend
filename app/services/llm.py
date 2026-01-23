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
from openai import AsyncOpenAI

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
    response_id: Optional[str] = None


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
        self._client: Optional[AsyncOpenAI] = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def _get_headers(self) -> dict:
        """Get API headers"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _response_to_dict(response: Any) -> dict:
        if isinstance(response, dict):
            return response
        if hasattr(response, "model_dump"):
            return response.model_dump()
        if hasattr(response, "to_dict"):
            return response.to_dict()
        return {}

    @classmethod
    def _extract_response_text(cls, response: Any) -> str:
        data = cls._response_to_dict(response)
        output = data.get("output", [])
        texts: List[str] = []
        for item in output:
            if item.get("type") != "message":
                continue
            for part in item.get("content", []):
                if part.get("type") in ("output_text", "text"):
                    texts.append(part.get("text", ""))
        if not texts and data.get("text"):
            texts.append(data.get("text", ""))
        return "".join(texts)

    async def _responses_create(
        self,
        messages: List[Message],
        previous_response_id: Optional[str] = None,
    ) -> LLMResponse:
        if not self.api_key:
            logger.error("LLM service not configured")
            return None

        client = self._get_client()
        input_payload = [{"role": m.role, "content": m.content} for m in messages]

        response = await client.responses.create(
            model=self.model_id,
            input=input_payload,
            previous_response_id=previous_response_id,
            thinking={"type": "disabled"},
        )
        data = self._response_to_dict(response)
        return LLMResponse(
            content=self._extract_response_text(response),
            usage=data.get("usage"),
            response_id=data.get("id"),
        )

    async def chat(
        self,
        messages: List[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_p: float = 0.9,
        previous_response_id: Optional[str] = None,
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
        try:
            return await self._responses_create(messages, previous_response_id=previous_response_id)

        except Exception as e:
            logger.error(f"LLM chat error: {e}")
            return None

    async def chat_stream(
        self,
        messages: List[Message],
        # temperature: float = 0.7,
        max_tokens: int = 16384,
        # top_p: float = 0.9,
        interrupt_check: Optional[Callable[[], bool]] = None,
        previous_response_id: Optional[str] = None,
        on_response_id: Optional[Callable[[str], Any]] = None,
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

        url = f"{self.base_url}/responses"
        payload = {
            "model": self.model_id,
            "input": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "previous_response_id": previous_response_id,
            "max_output_tokens": max_tokens,
            "thinking": {"type": "disabled"},
        }

        response_id_sent = False
        any_text = False

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

                        if not line.startswith("data: "):
                            continue

                        data_str = line[6:]
                        if data_str == "[DONE]":
                            yield StreamChunk(content="", is_final=True, finish_reason="stop")
                            break

                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        event_type = event.get("type")
                        if event_type in ("response.created", "response.in_progress", "response.completed"):
                            resp_obj = event.get("response") or {}
                            rid = resp_obj.get("id")
                            if rid and on_response_id and not response_id_sent:
                                maybe = on_response_id(rid)
                                if inspect.isawaitable(maybe):
                                    await maybe
                                response_id_sent = True
                            if event_type == "response.completed":
                                yield StreamChunk(content="", is_final=True, finish_reason="stop")
                                break

                        elif event_type == "response.failed":
                            yield StreamChunk(content="", is_final=True, finish_reason="error")
                            break

                        elif event_type == "response.incomplete":
                            yield StreamChunk(content="", is_final=True, finish_reason="incomplete")
                            break

                        elif event_type == "response.output_text.delta":
                            delta = event.get("delta") or ""
                            if delta:
                                any_text = True
                                yield StreamChunk(content=delta, is_final=False, finish_reason=None)

                        elif event_type == "response.output_text.done":
                            text = event.get("text") or ""
                            if text and not any_text:
                                yield StreamChunk(content=text, is_final=False, finish_reason=None)

        except Exception as e:
            logger.error(f"LLM stream error: {e}")
            yield StreamChunk(content="", is_final=True, finish_reason="error")

    async def generate_with_context(
        self,
        system_prompt: str,
        user_message: str,
        history: Optional[List[Message]] = None,
        context_vars: Optional[Dict[str, str]] = None,
        previous_response_id: Optional[str] = None,
        on_response_id: Optional[Callable[[str], Any]] = None,
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

        async for chunk in self.chat_stream(
            messages,
            previous_response_id=previous_response_id,
            on_response_id=on_response_id,
            **kwargs,
        ):
            yield chunk


# Singleton instance
llm_service = LLMService()

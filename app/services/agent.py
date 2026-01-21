"""
AI Conversation Agent

Orchestrates the ASR -> LLM -> TTS pipeline for real-time AI conversation.
"""

import asyncio
import json
import logging
import base64
from typing import AsyncGenerator, Optional, List, Dict, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.asr import asr_service, TranscriptionResult
from app.services.tts import tts_service
from app.services.llm import llm_service, Message, StreamChunk
from app.services.prompts import render_prompt, build_question_context
from app.redis_client import redis_client

logger = logging.getLogger(__name__)


@dataclass
class ConversationContext:
    """Conversation context and state"""
    conversation_id: int
    user_id: int
    conversation_type: str  # "solving" or "chat"
    student_name: str = "同学"
    grade: str = "初中"
    subject: str = ""
    question_text: str = ""
    question_image_url: str = ""
    user_answer: str = ""
    correct_answer: str = ""
    history: List[Message] = field(default_factory=list)
    is_interrupted: bool = False


@dataclass
class AgentResponse:
    """Agent response data"""
    text: str
    audio_base64: Optional[str] = None
    is_final: bool = False


class AIAgent:
    """
    AI Conversation Agent

    Handles the full pipeline:
    1. ASR: Audio -> Text
    2. LLM: Text -> Response
    3. TTS: Response -> Audio
    """

    def __init__(self):
        self.asr = asr_service
        self.tts = tts_service
        self.llm = llm_service

    async def get_conversation_context(
        self,
        conversation_id: int,
    ) -> Optional[ConversationContext]:
        """Load conversation context from Redis"""
        # Get session data
        session = await redis_client.hgetall(f"conv:session:{conversation_id}")
        if not session:
            return None

        # Get context vars
        vars_data = await redis_client.hgetall(f"conv:vars:{conversation_id}")

        # Get message history
        messages_raw = await redis_client.lrange(f"conv:messages:{conversation_id}", 0, -1)
        history = []
        for msg_str in messages_raw:
            try:
                msg = json.loads(msg_str)
                if msg.get("type") == "text":
                    history.append(Message(
                        role=msg.get("role", "user"),
                        content=msg.get("content", ""),
                    ))
            except Exception:
                pass

        # Build context
        return ConversationContext(
            conversation_id=conversation_id,
            user_id=int(session.get("user_id", 0)),
            conversation_type=session.get("type", "chat"),
            student_name=vars_data.get("student_name", "同学"),
            grade=vars_data.get("grade", "初中"),
            subject=vars_data.get("subject", ""),
            question_text=vars_data.get("context_text", ""),
            question_image_url=vars_data.get("context_image_url", ""),
            user_answer=vars_data.get("user_answer", ""),
            correct_answer=vars_data.get("correct_answer", ""),
            history=history[-10:],  # Keep last 10 messages for context
        )

    async def check_interrupt(self, conversation_id: int) -> bool:
        """Check if conversation has been interrupted"""
        flag = await redis_client.get(f"conv:interrupt:{conversation_id}")
        return flag == "1"

    async def clear_interrupt(self, conversation_id: int) -> None:
        """Clear interrupt flag"""
        await redis_client.delete(f"conv:interrupt:{conversation_id}")

    async def store_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        msg_type: str = "text",
    ) -> None:
        """Store message in Redis"""
        message = {
            "role": role,
            "type": msg_type,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await redis_client.rpush(
            f"conv:messages:{conversation_id}",
            json.dumps(message, ensure_ascii=False),
        )

    async def process_audio(
        self,
        conversation_id: int,
        audio_chunks: AsyncGenerator[bytes, None],
        on_transcript: Optional[Callable[[str, bool], None]] = None,
    ) -> Optional[str]:
        """
        Process audio input through ASR.

        Args:
            conversation_id: Conversation ID
            audio_chunks: Async generator of audio data chunks
            on_transcript: Callback for transcription results (text, is_final)

        Returns:
            Final transcribed text or None
        """
        final_text = ""

        def interrupt_check():
            # This is a sync check, we'll need to handle this differently
            # For now, just return False
            return False

        try:
            async for result in self.asr.transcribe_stream(
                audio_chunks,
                interrupt_check=interrupt_check,
            ):
                if on_transcript:
                    on_transcript(result.text, result.is_final)

                if result.is_final:
                    final_text = result.text

            return final_text if final_text else None

        except Exception as e:
            logger.error(f"ASR processing error: {e}")
            return None

    async def generate_response(
        self,
        conversation_id: int,
        user_text: str,
        on_text: Optional[Callable[[str, bool], None]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Generate LLM response for user input.

        Args:
            conversation_id: Conversation ID
            user_text: User's text input
            on_text: Callback for text chunks (text, is_final)

        Yields:
            Response text chunks
        """
        # Load context
        context = await self.get_conversation_context(conversation_id)
        if not context:
            yield "抱歉，会话已过期，请重新开始。"
            return

        # Build system prompt
        question_context = build_question_context(
            question_text=context.question_text,
            question_image_url=context.question_image_url,
            user_answer=context.user_answer,
            correct_answer=context.correct_answer,
        )

        context_vars = {
            "student_name": context.student_name,
            "grade": context.grade,
            "subject": context.subject,
            "question_context": question_context,
        }

        system_prompt = render_prompt(context.conversation_type, context_vars)

        # Store user message
        await self.store_message(conversation_id, "user", user_text)

        # Generate response
        full_response = ""

        try:
            async for chunk in self.llm.generate_with_context(
                system_prompt=system_prompt,
                user_message=user_text,
                history=context.history,
                interrupt_check=lambda: self.check_interrupt(conversation_id),
            ):
                if chunk.content:
                    full_response += chunk.content
                    if on_text:
                        on_text(chunk.content, chunk.is_final)
                    yield chunk.content

                if chunk.is_final:
                    break

            # Store assistant message
            if full_response:
                await self.store_message(conversation_id, "assistant", full_response)

        except Exception as e:
            logger.error(f"LLM generation error: {e}")
            error_msg = "抱歉，我遇到了一些问题，请稍后再试。"
            yield error_msg

    async def synthesize_speech(
        self,
        conversation_id: int,
        text: str,
        on_audio: Optional[Callable[[bytes], None]] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Synthesize speech from text.

        Args:
            conversation_id: Conversation ID
            text: Text to synthesize
            on_audio: Callback for audio chunks

        Yields:
            Audio data chunks
        """
        async def interrupt_check():
            return await self.check_interrupt(conversation_id)

        try:
            async for chunk in self.tts.synthesize_stream(
                text,
                interrupt_check=lambda: False,  # Sync check not supported
            ):
                # Check interrupt
                if await self.check_interrupt(conversation_id):
                    logger.info("TTS interrupted")
                    break

                if on_audio:
                    on_audio(chunk)
                yield chunk

        except Exception as e:
            logger.error(f"TTS synthesis error: {e}")

    async def process_text_input(
        self,
        conversation_id: int,
        text: str,
        on_reply_text: Optional[Callable[[str, bool], None]] = None,
        on_reply_audio: Optional[Callable[[str], None]] = None,
    ) -> AsyncGenerator[AgentResponse, None]:
        """
        Process text input and generate response with audio.

        Args:
            conversation_id: Conversation ID
            text: User's text input
            on_reply_text: Callback for text chunks
            on_reply_audio: Callback for audio chunks (base64)

        Yields:
            AgentResponse objects
        """
        # Clear any previous interrupt
        await self.clear_interrupt(conversation_id)

        # Collect response text in sentence-sized chunks for TTS
        sentence_buffer = ""
        sentence_delimiters = {"。", "！", "？", "，", "；", "：", "\n"}
        full_response = ""

        async for text_chunk in self.generate_response(
            conversation_id,
            text,
            on_text=on_reply_text,
        ):
            full_response += text_chunk
            sentence_buffer += text_chunk

            # Check if we have a complete sentence
            for delimiter in sentence_delimiters:
                if delimiter in sentence_buffer:
                    # Split at delimiter
                    parts = sentence_buffer.split(delimiter, 1)
                    sentence = parts[0] + delimiter
                    sentence_buffer = parts[1] if len(parts) > 1 else ""

                    # Check for interrupt
                    if await self.check_interrupt(conversation_id):
                        yield AgentResponse(text="", is_final=True)
                        return

                    # Synthesize and yield audio
                    if sentence.strip():
                        try:
                            audio_data = await self.tts.synthesize(sentence)
                            if audio_data:
                                audio_b64 = base64.b64encode(audio_data).decode("utf-8")
                                if on_reply_audio:
                                    on_reply_audio(audio_b64)
                                yield AgentResponse(
                                    text=sentence,
                                    audio_base64=audio_b64,
                                    is_final=False,
                                )
                        except Exception as e:
                            logger.error(f"TTS error: {e}")
                    break

        # Handle remaining buffer
        if sentence_buffer.strip():
            if not await self.check_interrupt(conversation_id):
                try:
                    audio_data = await self.tts.synthesize(sentence_buffer)
                    if audio_data:
                        audio_b64 = base64.b64encode(audio_data).decode("utf-8")
                        if on_reply_audio:
                            on_reply_audio(audio_b64)
                        yield AgentResponse(
                            text=sentence_buffer,
                            audio_base64=audio_b64,
                            is_final=True,
                        )
                except Exception as e:
                    logger.error(f"TTS error: {e}")

        yield AgentResponse(text="", is_final=True)


# Singleton instance
ai_agent = AIAgent()

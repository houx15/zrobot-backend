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
    analysis: str = ""
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


@dataclass
class Segment:
    """A segment containing paired speech and board content"""
    segment_id: int
    speech: str
    board: str
    audio_base64: Optional[str] = None


class SegmentParser:
    """
    Parser for LLM output in [S]...[/S][B]...[/B] format.

    Handles streaming input and emits complete segments.
    """

    def __init__(self):
        self.buffer = ""
        self.current_segment_id = 0
        self.current_speech = ""
        self.current_board = ""
        self.in_speech = False
        self.in_board = False

    def reset(self):
        """Reset parser state"""
        self.buffer = ""
        self.current_segment_id = 0
        self.current_speech = ""
        self.current_board = ""
        self.in_speech = False
        self.in_board = False

    def feed(self, chunk: str) -> List[Segment]:
        """
        Feed a chunk of text and return any complete segments.

        Args:
            chunk: Text chunk from LLM stream

        Returns:
            List of complete segments (may be empty)
        """
        self.buffer += chunk
        segments = []

        while True:
            # Look for [S] start tag
            if not self.in_speech and not self.in_board:
                s_start = self.buffer.find("[S]")
                if s_start != -1:
                    self.buffer = self.buffer[s_start + 3:]
                    self.in_speech = True
                    self.current_speech = ""
                else:
                    break

            # Look for [/S] end tag
            if self.in_speech:
                s_end = self.buffer.find("[/S]")
                if s_end != -1:
                    self.current_speech = self.buffer[:s_end].strip()
                    self.buffer = self.buffer[s_end + 4:]
                    self.in_speech = False
                else:
                    break

            # Look for [B] start tag
            if not self.in_speech and not self.in_board and self.current_speech:
                b_start = self.buffer.find("[B]")
                if b_start != -1:
                    self.buffer = self.buffer[b_start + 3:]
                    self.in_board = True
                    self.current_board = ""
                else:
                    break

            # Look for [/B] end tag
            if self.in_board:
                b_end = self.buffer.find("[/B]")
                if b_end != -1:
                    self.current_board = self.buffer[:b_end].strip()
                    self.buffer = self.buffer[b_end + 4:]
                    self.in_board = False

                    # Emit complete segment
                    segment = Segment(
                        segment_id=self.current_segment_id,
                        speech=self.current_speech,
                        board=self.current_board,
                    )
                    segments.append(segment)

                    self.current_segment_id += 1
                    self.current_speech = ""
                    self.current_board = ""
                else:
                    break

        return segments

    def get_partial_speech(self) -> Optional[str]:
        """Get current partial speech content (for early TTS)"""
        if self.in_speech and self.buffer:
            return self.buffer.strip()
        return None

    def finalize(self) -> Optional[Segment]:
        """
        Finalize parsing and return any remaining partial segment.

        Should be called when LLM stream ends.
        """
        # If we have partial content, try to create a segment
        if self.current_speech and self.current_board:
            segment = Segment(
                segment_id=self.current_segment_id,
                speech=self.current_speech,
                board=self.current_board,
            )
            self.reset()
            return segment

        # If we have speech but no board yet, check buffer for board content
        if self.current_speech and self.in_board and self.buffer:
            segment = Segment(
                segment_id=self.current_segment_id,
                speech=self.current_speech,
                board=self.buffer.strip(),
            )
            self.reset()
            return segment

        self.reset()
        return None


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
            analysis=vars_data.get("analysis", ""),
            user_answer=vars_data.get("user_answer", ""),
            correct_answer=vars_data.get("correct_answer", ""),
            history=history[-10:],  # Keep last 10 messages for context
        )

    async def get_system_prompt(
        self,
        conversation_id: int,
        conversation_type: str,
        context_vars: Dict[str, str],
    ) -> str:
        """Get or create a stable system prompt for the conversation."""
        prompt_key = f"conv:prompt:{conversation_id}"
        cached = await redis_client.get(prompt_key)
        if cached:
            return cached

        system_prompt = render_prompt(conversation_type, context_vars)
        await redis_client.set(prompt_key, system_prompt, ex=7200)
        return system_prompt

    async def check_interrupt(self, conversation_id: int) -> bool:
        """Check if conversation has been interrupted"""
        flag = await redis_client.get(f"conv:interrupt:{conversation_id}")
        return flag == "1"

    async def clear_interrupt(self, conversation_id: int) -> None:
        """Clear interrupt flag"""
        await redis_client.delete(f"conv:interrupt:{conversation_id}")

    async def _get_previous_response_id(self, conversation_id: int) -> Optional[str]:
        return await redis_client.get(f"conv:llm:resp_id:{conversation_id}")

    async def _set_previous_response_id(self, conversation_id: int, response_id: str) -> None:
        if not response_id:
            return
        await redis_client.set(
            f"conv:llm:resp_id:{conversation_id}",
            response_id,
            ex=7200,
        )

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
            analysis=context.analysis,
        )

        context_vars = {
            "student_name": context.student_name,
            "grade": context.grade,
            "subject": context.subject,
            "question_context": question_context,
        }

        system_prompt = await self.get_system_prompt(
            conversation_id,
            context.conversation_type,
            context_vars,
        )

        # Store user message
        await self.store_message(conversation_id, "user", user_text)

        # Generate response
        full_response = ""
        previous_response_id = await self._get_previous_response_id(conversation_id)

        try:
            async for chunk in self.llm.generate_with_context(
                system_prompt=system_prompt,
                user_message=user_text,
                history=context.history,
                previous_response_id=previous_response_id,
                on_response_id=lambda rid: self._set_previous_response_id(conversation_id, rid),
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

    async def process_text_with_segments(
        self,
        conversation_id: int,
        text: str,
        on_segment: Optional[Callable[[Segment], Any]] = None,
    ) -> AsyncGenerator[Segment, None]:
        """
        Process text input and generate segments with speech + board content.

        This is the new segment-based pipeline that:
        1. Sends user text to LLM
        2. Parses LLM output in [S]...[/S][B]...[/B] format
        3. Generates TTS for each segment's speech
        4. Yields complete segments with audio

        Args:
            conversation_id: Conversation ID
            text: User's text input
            on_segment: Callback for each complete segment

        Yields:
            Segment objects with speech, board, and audio
        """
        # Clear any previous interrupt
        await self.clear_interrupt(conversation_id)

        # Create segment parser
        parser = SegmentParser()

        # Load context
        context = await self.get_conversation_context(conversation_id)
        if not context:
            # Fallback for expired session
            yield Segment(
                segment_id=0,
                speech="抱歉，会话已过期，请重新开始。",
                board=":::note{color=yellow}\n会话已过期\n:::",
            )
            return

        # Build system prompt
        question_context = build_question_context(
            question_text=context.question_text,
            question_image_url=context.question_image_url,
            user_answer=context.user_answer,
            correct_answer=context.correct_answer,
            analysis=context.analysis,
        )

        context_vars = {
            "student_name": context.student_name,
            "grade": context.grade,
            "subject": context.subject,
            "question_context": question_context,
        }

        system_prompt = await self.get_system_prompt(
            conversation_id,
            context.conversation_type,
            context_vars,
        )

        # Store user message
        await self.store_message(conversation_id, "user", text)

        # Generate response and parse segments
        full_response = ""
        previous_response_id = await self._get_previous_response_id(conversation_id)

        try:
            async for chunk in self.llm.generate_with_context(
                system_prompt=system_prompt,
                user_message=text,
                history=context.history,
                previous_response_id=previous_response_id,
                on_response_id=lambda rid: self._set_previous_response_id(conversation_id, rid),
                interrupt_check=lambda: self.check_interrupt(conversation_id),
            ):
                if chunk.content:
                    full_response += chunk.content

                    # Feed chunk to parser
                    segments = parser.feed(chunk.content)

                    # Process complete segments
                    for segment in segments:
                        # Check for interrupt
                        if await self.check_interrupt(conversation_id):
                            logger.info(f"Segment generation interrupted for {conversation_id}")
                            return

                        # Generate TTS for speech
                        if segment.speech:
                            try:
                                audio_data = await self.tts.synthesize(segment.speech)
                                if audio_data:
                                    segment.audio_base64 = base64.b64encode(audio_data).decode("utf-8")
                            except Exception as e:
                                logger.error(f"TTS error for segment {segment.segment_id}: {e}")

                        # Emit segment
                        if on_segment:
                            on_segment(segment)
                        yield segment

                if chunk.is_final:
                    break

            # Handle any remaining partial segment
            final_segment = parser.finalize()
            if final_segment:
                if not await self.check_interrupt(conversation_id):
                    # Generate TTS for final segment
                    if final_segment.speech:
                        try:
                            audio_data = await self.tts.synthesize(final_segment.speech)
                            if audio_data:
                                final_segment.audio_base64 = base64.b64encode(audio_data).decode("utf-8")
                        except Exception as e:
                            logger.error(f"TTS error for final segment: {e}")

                    if on_segment:
                        on_segment(final_segment)
                    yield final_segment

            # Store assistant message (full response)
            if full_response:
                await self.store_message(conversation_id, "assistant", full_response)

        except Exception as e:
            logger.error(f"Segment generation error: {e}")
            error_segment = Segment(
                segment_id=0,
                speech="抱歉，我遇到了一些问题，请稍后再试。",
                board=":::note{color=yellow}\n处理出错，请重试\n:::",
            )
            yield error_segment


# Singleton instance
ai_agent = AIAgent()

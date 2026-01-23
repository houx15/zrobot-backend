"""
Zhipu AI Service

Provides integration with Zhipu AI for:
- Homework Correction (作业批改)
- Problem Solving (拍照解题)

Documentation: https://docs.bigmodel.cn
"""

import time
import json
import re
from typing import Optional, List, Dict, Any, AsyncGenerator
from dataclasses import dataclass

import httpx
import jwt

from app.config import settings


@dataclass
class CorrectionResult:
    """Result for a single question in homework correction"""

    index: int
    uuid: str
    question_text: Optional[str]
    question_type: Optional[int]
    user_answer: Optional[str]
    correct_answer: Optional[str]
    is_correct: bool
    is_finish: bool
    question_bbox: Optional[List[int]]
    answer_bbox: Optional[List[int]]
    correct_source: Optional[int]
    analysis: Optional[str] = None


@dataclass
class CorrectionResponse:
    """Response from homework correction API"""

    trace_id: str
    image_id: str
    subject: Optional[str]
    processed_image_url: Optional[str]
    total_questions: int
    correct_count: int
    wrong_count: int
    correcting_count: int
    results: List[CorrectionResult]
    raw_response: dict


@dataclass
class SolvingResponse:
    """Response from problem solving API"""

    answer: str
    course: Optional[str]
    knowledge_points: List[str]
    raw_response: dict
    question_text: Optional[str] = None
    analysis_text: Optional[str] = None
    final_answer: Optional[str] = None


class ZhipuService:
    """Zhipu AI API Service"""

    BASE_URL = "https://open.bigmodel.cn/api/v1"

    # Agent IDs
    CORRECTION_AGENT = "intelligent_education_correction_agent"
    CORRECTION_POLLING_AGENT = "intelligent_education_correction_polling"
    SOLVING_AGENT = "multimodal_edu_solve_agent"

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    def _get_auth_token(self) -> str:
        """Generate JWT token for authentication"""
        api_key = settings.zhipu_api_key
        if not api_key:
            raise ValueError("ZHIPU_API_KEY not configured")

        try:
            id_part, secret = api_key.split(".")
        except ValueError:
            raise ValueError("Invalid API key format. Expected: {id}.{secret}")

        payload = {
            "api_key": id_part,
            "exp": int(time.time()) + 3600,
            "timestamp": int(time.time() * 1000),
        }

        token = jwt.encode(
            payload,
            secret,
            algorithm="HS256",
            headers={"alg": "HS256", "sign_type": "SIGN"},
        )
        return token

    def _get_headers(self) -> dict:
        """Get request headers with authentication"""
        token = self._get_auth_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def close(self):
        """Close the HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ========== Homework Correction ==========

    async def correct_homework(self, image_url: str) -> CorrectionResponse:
        """
        Submit homework image for correction (Step 1)

        Args:
            image_url: URL of the homework image

        Returns:
            CorrectionResponse with results
        """
        client = await self._get_client()
        headers = self._get_headers()

        data = {
            "agent_id": self.CORRECTION_AGENT,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": image_url}],
                }
            ],
        }

        response = await client.post(
            f"{self.BASE_URL}/agents",
            headers=headers,
            json=data,
        )
        response.raise_for_status()
        result = response.json()

        # Check for errors
        if (result.get("code") != 200) and (result.get("status") != "success"):
            raise Exception(f"Zhipu API error: {result}")

        try:
            llm_result = result["choices"][0]["messages"][0]["content"]["object"]
        except Exception as e:
            raise Exception(f"Zhipu API error: {e}")
        trace_id = llm_result.get("trace_id", "")
        raw_response = llm_result.get("image_results", [None])[0]

        processed_image_url = raw_response.get("processed_image_url", "")
        subject = raw_response.get("paper_subject", "")

        correct_count = raw_response.get("stat_result", {}).get("right", 0)
        wrong_count = raw_response.get("stat_result", {}).get("wrong", 0)
        correcting_count = raw_response.get("stat_result", {}).get("correcting", 0)

        raw_results = raw_response.get("results", [])

        results = []

        for item in raw_results:
            user_answer = item.get("answers", [])
            if user_answer:
                user_answer = user_answer[0]
            else:
                user_answer = None
            is_correct = item.get("correct_result") == 1
            is_finish = item.get("is_finish") == 1

            results.append(
                CorrectionResult(
                    index=item.get("index", 0),
                    uuid=item.get("uuid", ""),
                    question_text=item.get("question") or item.get("text"),
                    question_type=item.get("type"),
                    user_answer=user_answer.get("text") if user_answer else None,
                    correct_answer=item.get("answer"),
                    is_correct=is_correct,
                    is_finish=is_finish,
                    question_bbox=item.get("bbox"),
                    answer_bbox=user_answer.get("bbox") if user_answer else None,
                    correct_source=item.get("correct_source"),
                )
            )

        return CorrectionResponse(
            trace_id=trace_id,
            image_id=llm_result.get("agent_id"),
            subject=subject,
            processed_image_url=processed_image_url,
            total_questions=len(results),
            correct_count=correct_count,
            wrong_count=wrong_count,
            correcting_count=correcting_count,
            results=results,
            raw_response=raw_results,
        )

    @staticmethod
    def _parse_solution_sections(text: str) -> Dict[str, str]:
        """Parse markdown-like sections split by ### headers."""
        sections: Dict[str, List[str]] = {}
        current_title: Optional[str] = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("###"):
                title = line.lstrip("#").strip()
                current_title = title
                sections[current_title] = []
                continue
            if current_title is not None:
                sections[current_title].append(raw_line)

        return {k: "\n".join(v).strip() for k, v in sections.items()}

    @staticmethod
    def _parse_knowledge_points(text: str) -> List[str]:
        """Parse knowledge points list from a section."""
        points = []
        for line in text.splitlines():
            clean = line.strip()
            if not clean:
                continue
            clean = re.sub(r"^(\d+)[\.\、]\s*", "", clean)
            clean = re.sub(r"^[-*]\s*", "", clean)
            if clean:
                points.append(clean)
        return points

    async def correct_homework_polling(
        self,
        trace_id: str,
        image_id: str,
        uuids: List[str],
    ) -> dict:
        """
        Get correction results for unfinished questions (Step 2)

        Args:
            trace_id: Trace ID from step 1
            image_id: Image ID from step 1
            uuids: List of question UUIDs to process

        Returns:
            API response dict
        """
        client = await self._get_client()
        headers = self._get_headers()

        data = {
            "agent_id": self.CORRECTION_POLLING_AGENT,
            "custom_variables": {
                "trace_id": trace_id,
                "image_id": image_id,
                "uuids": uuids,
                "images": [],
            },
        }

        response = await client.post(
            f"{self.BASE_URL}/agents/async-result",
            headers=headers,
            json=data,
        )
        response.raise_for_status()
        return response.json()

    async def get_question_analysis(
        self,
        question: str,
        image_id: str,
        uuid: str,
        trace_id: str,
    ) -> AsyncGenerator[str, None]:
        """
        Get detailed analysis for a question (Step 3, streaming)

        Args:
            question: Question text
            image_id: Image ID
            uuid: Question UUID
            trace_id: Trace ID

        Yields:
            Streamed analysis text chunks
        """
        client = await self._get_client()
        headers = self._get_headers()

        data = {
            "agent_id": self.CORRECTION_AGENT,
            "custom_variables": {
                "question": question,
                "image_id": image_id,
                "uuid": uuid,
                "trace_id": trace_id,
            },
        }

        async with client.stream(
            "POST",
            f"{self.BASE_URL}/agents",
            headers=headers,
            json=data,
        ) as response:
            async for line in response.aiter_lines():
                if line and line.startswith("data: "):
                    json_str = line[6:]
                    if json_str != "[DONE]":
                        try:
                            chunk = json.loads(json_str)
                            # Extract text content
                            choices = chunk.get("choices", [])
                            for choice in choices:
                                messages = choice.get("messages", [])
                                for msg in messages:
                                    content = msg.get("content", {})
                                    if (
                                        isinstance(content, dict)
                                        and content.get("type") == "text"
                                    ):
                                        yield content.get("text", "")
                        except json.JSONDecodeError:
                            pass

    # ========== Problem Solving ==========

    async def solve_problem(
        self,
        image_url: Optional[str] = None,
        text: Optional[str] = None,
    ) -> SolvingResponse:
        """
        Solve a problem using image and/or text

        Args:
            image_url: URL of the problem image (optional)
            text: Problem text (optional)

        Returns:
            SolvingResponse with answer and metadata
        """
        if not image_url and not text:
            raise ValueError("Either image_url or text must be provided")

        client = await self._get_client()
        headers = self._get_headers()

        # Build content array
        content = []
        if text:
            content.append({"type": "text", "text": text})
        if image_url:
            content.append({"type": "image_url", "image_url": image_url})

        data = {
            "agent_id": self.SOLVING_AGENT,
            "stream": False,
            "messages": [{"role": "user", "content": content}],
        }

        # The API returns SSE format even for non-streaming
        async with client.stream(
            "POST",
            f"{self.BASE_URL}/agents",
            headers=headers,
            json=data,
        ) as response:
            full_content = ""
            course = None
            knowledge_points = []
            last_chunk = None

            async for line in response.aiter_lines():
                if line and line.startswith("data: "):
                    json_str = line[6:]
                    if json_str != "[DONE]":
                        try:
                            chunk = json.loads(json_str)
                            last_chunk = chunk

                            choices = chunk.get("choices", [])
                            for choice in choices:
                                messages = choice.get("messages", [])
                                for msg in messages:
                                    content = msg.get("content", {})
                                    if isinstance(content, dict):
                                        if content.get("type") == "text":
                                            full_content += content.get("text", "")
                                        elif content.get("type") == "object":
                                            obj = content.get("object", {})
                                            course = obj.get("course")
                                            knowledge_points = obj.get("knowledges", [])
                        except json.JSONDecodeError:
                            pass

            sections = self._parse_solution_sections(full_content)
            knowledge_from_text = []
            for key in ("知识点总结", "知识点"):
                if key in sections:
                    knowledge_from_text = self._parse_knowledge_points(sections[key])
                    break

            return SolvingResponse(
                answer=full_content,
                course=course,
                knowledge_points=knowledge_points or knowledge_from_text,
                question_text=sections.get("题目"),
                analysis_text=sections.get("解析"),
                final_answer=sections.get("答案"),
                raw_response=last_chunk or {},
            )

    async def solve_problem_stream(
        self,
        image_url: Optional[str] = None,
        text: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        Solve a problem with streaming response

        Args:
            image_url: URL of the problem image (optional)
            text: Problem text (optional)

        Yields:
            Streamed answer text chunks
        """
        if not image_url and not text:
            raise ValueError("Either image_url or text must be provided")

        client = await self._get_client()
        headers = self._get_headers()

        content = []
        if text:
            content.append({"type": "text", "text": text})
        if image_url:
            content.append({"type": "image_url", "image_url": image_url})

        data = {
            "agent_id": self.SOLVING_AGENT,
            "stream": True,
            "messages": [{"role": "user", "content": content}],
        }

        async with client.stream(
            "POST",
            f"{self.BASE_URL}/agents",
            headers=headers,
            json=data,
        ) as response:
            async for line in response.aiter_lines():
                if line and line.startswith("data: "):
                    json_str = line[6:]
                    if json_str != "[DONE]":
                        try:
                            chunk = json.loads(json_str)
                            choices = chunk.get("choices", [])
                            for choice in choices:
                                messages = choice.get("messages", [])
                                for msg in messages:
                                    content = msg.get("content", {})
                                    if (
                                        isinstance(content, dict)
                                        and content.get("type") == "text"
                                    ):
                                        yield content.get("text", "")
                        except json.JSONDecodeError:
                            pass


# Global service instance
zhipu_service = ZhipuService()

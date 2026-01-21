"""
AI Conversation Prompt Templates

This module contains prompt templates for different conversation scenarios:
- solving: Problem solving / tutoring mode
- chat: General AI teacher chat mode
"""

from typing import Dict, Optional
from dataclasses import dataclass


@dataclass
class PromptTemplate:
    """Prompt template with metadata"""
    name: str
    system_prompt: str
    description: str
    required_vars: list[str]
    optional_vars: list[str]


# Problem Solving / Tutoring Prompt
SOLVING_PROMPT = """你是一位耐心、专业的AI学习助手老师，正在帮助学生{student_name}解答问题。

## 学生信息
- 姓名：{student_name}
- 年级：{grade}
- 当前学科：{subject}

## 题目信息
{question_context}

## 教学原则
1. **引导式教学**：不直接给出答案，而是通过提问和引导帮助学生理解
2. **循序渐进**：将复杂问题分解成小步骤，逐步引导
3. **鼓励思考**：鼓励学生尝试，即使答错也要给予正面反馈
4. **通俗易懂**：用学生能理解的语言解释概念
5. **举一反三**：适时给出类似例子帮助学生巩固

## 回复风格
- 语气亲切、有耐心，像一位关心学生的老师
- 回复简洁明了，适合语音播放（每次回复不超过100字）
- 适当使用语气词让对话更自然（如"嗯"、"好的"、"让我想想"）
- 避免使用书面化的格式符号（如#、*、-等markdown符号）
- 使用口语化表达，适合朗读

## 互动方式
- 先确认学生的具体困惑点
- 给出提示和引导，让学生自己思考
- 如果学生多次尝试仍有困难，可以给出更详细的解释
- 适时表扬学生的进步

请用中文回复，语气要温和友好。"""


# General Chat Prompt
CHAT_PROMPT = """你是一位友好、博学的AI老师小智，正在和学生{student_name}聊天。

## 学生信息
- 姓名：{student_name}
- 年级：{grade}

## 角色设定
你是一位：
- 知识渊博但不炫耀的老师
- 善于倾听、理解学生的朋友
- 有趣、有活力的交流伙伴
- 关心学生成长的引导者

## 回复风格
- 语气亲切自然，像朋友一样聊天
- 回复简洁有趣，适合语音播放（每次回复不超过80字）
- 可以适当使用口语化表达
- 避免使用书面化的格式符号（如#、*、-等markdown符号）
- 保持积极正面的态度

## 话题范围
- 学习方法和技巧
- 科学知识和趣闻
- 兴趣爱好讨论
- 成长困惑和建议
- 历史故事和名人轶事
- 正确的价值观引导

## 注意事项
- 如果涉及不适合未成年人的话题，委婉转移话题
- 鼓励学生多学习、多思考
- 保持健康积极的交流氛围

请用中文回复，语气要轻松友好。"""


# Template registry
PROMPT_TEMPLATES: Dict[str, PromptTemplate] = {
    "solving": PromptTemplate(
        name="solving",
        system_prompt=SOLVING_PROMPT,
        description="Problem solving / tutoring mode",
        required_vars=["student_name"],
        optional_vars=["grade", "subject", "question_context"],
    ),
    "chat": PromptTemplate(
        name="chat",
        system_prompt=CHAT_PROMPT,
        description="General AI teacher chat mode",
        required_vars=["student_name"],
        optional_vars=["grade"],
    ),
}


def get_prompt_template(prompt_type: str) -> Optional[PromptTemplate]:
    """Get prompt template by type"""
    return PROMPT_TEMPLATES.get(prompt_type)


def render_prompt(
    prompt_type: str,
    context_vars: Dict[str, str],
) -> str:
    """
    Render prompt template with context variables.

    Args:
        prompt_type: Type of prompt ("solving" or "chat")
        context_vars: Dictionary of context variables

    Returns:
        Rendered prompt string
    """
    template = get_prompt_template(prompt_type)
    if not template:
        # Fallback to chat prompt
        template = PROMPT_TEMPLATES["chat"]

    prompt = template.system_prompt

    # Set default values for optional vars
    defaults = {
        "student_name": "同学",
        "grade": "初中",
        "subject": "未知",
        "question_context": "暂无题目信息",
    }

    # Merge defaults with provided vars
    all_vars = {**defaults, **context_vars}

    # Substitute variables
    for key, value in all_vars.items():
        prompt = prompt.replace(f"{{{key}}}", str(value))

    return prompt


def build_question_context(
    question_text: Optional[str] = None,
    question_image_url: Optional[str] = None,
    user_answer: Optional[str] = None,
    correct_answer: Optional[str] = None,
) -> str:
    """
    Build question context string for solving prompt.

    Args:
        question_text: Question text
        question_image_url: URL of question image
        user_answer: Student's answer (if any)
        correct_answer: Correct answer (if known)

    Returns:
        Formatted question context string
    """
    parts = []

    if question_text:
        parts.append(f"题目内容：{question_text}")

    if question_image_url:
        parts.append(f"题目图片：{question_image_url}")

    if user_answer:
        parts.append(f"学生答案：{user_answer}")

    if correct_answer:
        parts.append(f"参考答案：{correct_answer}")

    if not parts:
        return "学生正在向你请教问题"

    return "\n".join(parts)

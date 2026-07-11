from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .models import Conversation, Message, MessagePartType, Role


VISIBLE_ROLES = {Role.USER, Role.ASSISTANT}

# WorkBuddy 和部分 Agent 运行时会把机器生成的上下文塞进用户消息。
# 这些内容对模型有用，但不是用户希望阅读的真实对话。
_INTERNAL_BLOCK_TAGS = (
    "system-reminder",
    "user_references",
    "user_info",
    "identity_context",
    "workspace_context",
    "project_context",
    "environment_context",
    "runtime_context",
    "system_context",
    "tool_context",
    "agent_context",
    "cb_summary",
    "conversation_history_summary",
    "memory_and_skills_reminder",
    "additional_data",
    "current_time",
)
_INTERNAL_BLOCK_PATTERNS = tuple(
    re.compile(
        rf"<{re.escape(tag)}(?:\s[^>]*)?>.*?</{re.escape(tag)}\s*>",
        re.IGNORECASE | re.DOTALL,
    )
    for tag in _INTERNAL_BLOCK_TAGS
)

_USER_QUERY_PATTERN = re.compile(
    r"<user_query(?:\s[^>]*)?>(.*?)</user_query\s*>",
    re.IGNORECASE | re.DOTALL,
)
# 只移除已知的内部上下文标签行，不误杀用户消息中的 XML/HTML 内容
_INTERNAL_TAG_LINE = re.compile(
    rf"^\s*</?(?:{'|'.join(re.escape(t) for t in _INTERNAL_BLOCK_TAGS)})(?:\s[^>]*)?>\s*$",
    re.IGNORECASE,
)
_USER_QUERY_TAG_LINE = re.compile(r"^\s*</?user_query(?:\s[^>]*)?>\s*$", re.IGNORECASE)
_MULTI_BLANKS = re.compile(r"\n{3,}")
_INTERNAL_LINE_PATTERNS = (
    re.compile(r"^\s*(OS Version|Shell|IDE Theme|Current working directory)\s*:", re.I),
    re.compile(r"^\s*Note:\s*(These references|Use read tool|Prefer using absolute paths)", re.I),
    re.compile(r"^\s*(Injected workspace identity files|The following identity files are included)\s*:?\s*$", re.I),
    re.compile(r"^\s*If BOOTSTRAP\.md is present", re.I),
    re.compile(r"^\s*Follow it, figure out who you are", re.I),
    re.compile(r"^\s*Keep the conversation natural and human", re.I),
    re.compile(r"^\s*Path:\s*[A-Za-z]:\\", re.I),
)

# TRAE SOLO CN 任务消息会把 UI 状态标签混入正文文本：
# "查看 N 个步骤"（可折叠工具调用区标题）、"处理中..."（状态指示）、
# "创建"/"已执行命令"/"正在执行命令"/"深度思考"/"已读取"（操作标签）、
# "已允许高危操作"（权限提示）。这些不是真实对话内容，应过滤。
_TRAE_UI_LINE_PATTERNS = (
    re.compile(r"^查看\s*\d+\s*个步骤$"),
    re.compile(r"^已允许高危操作$"),
    re.compile(r"^处理中\.{2,}$"),
    re.compile(r"^已执行命令$"),
    re.compile(r"^正在执行命令$"),
    re.compile(r"^深度思考$"),
    re.compile(r"^已读取$"),
    re.compile(r"^创建$"),
)

_TOOL_PLACEHOLDER_PATTERNS = (
    re.compile(r"^\s*\[(?:工具调用|tool call)\]", re.I),
    re.compile(r"^\s*(?:工具调用|tool call)\s*[:：·]", re.I),
)
_THINKING_PLACEHOLDER_PATTERNS = (
    re.compile(r"^\s*\[(?:思考过程|thinking|reasoning)\]", re.I),
)


def _clean(text: str) -> str:
    value = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\x00", "")
    value = "\n".join(line.rstrip() for line in value.splitlines())
    return _MULTI_BLANKS.sub("\n\n", value).strip()


def strip_internal_context(text: str, source_app: str = "") -> str:
    """移除运行时注入上下文，同时保留用户真正输入的正文。"""

    value = _clean(text)
    if not value:
        return ""

    # 在移除外层 system-reminder 前先保留显式 user_query。
    queries = [_clean(match) for match in _USER_QUERY_PATTERN.findall(value)]
    queries = [item for item in queries if item]

    for pattern in _INTERNAL_BLOCK_PATTERNS:
        value = pattern.sub("\n", value)

    value = re.sub(r"</?user_query(?:\s[^>]*)?>", "\n", value, flags=re.I)

    kept: List[str] = []
    skip_until: Optional[str] = None
    for line in value.splitlines():
        stripped = line.strip()
        lower = stripped.casefold()

        # 兼容被截断、没有完整闭合标签的内部上下文块。
        if skip_until:
            if lower.startswith(f"</{skip_until}"):
                skip_until = None
            continue

        opened_internal = None
        for tag in _INTERNAL_BLOCK_TAGS:
            if lower.startswith(f"<{tag}") and not lower.startswith(f"</{tag}"):
                opened_internal = tag
                break
        if opened_internal:
            skip_until = opened_internal
            continue

        if _INTERNAL_TAG_LINE.match(stripped):
            continue
        if _USER_QUERY_TAG_LINE.match(stripped):
            continue
        if any(pattern.search(stripped) for pattern in _INTERNAL_LINE_PATTERNS):
            continue
        # TRAE UI 行标签仅对 TRAE 源应用生效，避免误删其他应用中同名的正常文本行。
        if source_app.casefold().startswith("trae") and any(pattern.match(stripped) for pattern in _TRAE_UI_LINE_PATTERNS):
            continue
        kept.append(line)

    cleaned = _clean("\n".join(kept))
    if queries:
        for query in queries:
            if query.casefold() not in cleaned.casefold():
                cleaned = _clean(f"{cleaned}\n\n{query}" if cleaned else query)

    if source_app.casefold().startswith("workbuddy"):
        lines = cleaned.splitlines()
        while lines and lines[-1].strip() in {"---", "```", "Injected workspace identity files:"}:
            lines.pop()
        cleaned = _clean("\n".join(lines))

    return cleaned


def role_from_hint(raw_role: str) -> Optional[Role]:
    """兼容不同客户端和版本产生的松散角色名。"""

    normalized = re.sub(r"[^a-z0-9]+", "", (raw_role or "").casefold())
    if not normalized:
        return None

    if any(token in normalized for token in ("tool", "function", "system", "reasoning", "thinking")):
        return None
    if any(token in normalized for token in ("user", "human", "client", "prompt", "question", "input")):
        return Role.USER
    if any(token in normalized for token in ("assistant", "agent", "model", "bot", "output", "answer")):
        return Role.ASSISTANT
    if normalized == "ai" or normalized.startswith("ai"):
        return Role.ASSISTANT
    return None


def effective_role(message: Message) -> Optional[Role]:
    if message.role in VISIBLE_ROLES:
        return message.role

    # SYSTEM 消息如果只有 thinking parts，实际上是 AI 的思考过程
    if message.role == Role.SYSTEM:
        has_thinking = any(
            (p.type.value if hasattr(p.type, "value") else str(p.type)) == MessagePartType.THINKING.value
            for p in message.parts
        )
        if has_thinking:
            return Role.ASSISTANT

    # TOOL 消息如果包含 TOOL_RESULT parts，是 AI 调用工具后的返回结果，
    # 应作为 AI 回复的一部分展示（WorkBuddy function_call_result 等）。
    if message.role == Role.TOOL:
        has_tool_result = any(
            (p.type.value if hasattr(p.type, "value") else str(p.type)) == MessagePartType.TOOL_RESULT.value
            for p in message.parts
        )
        if has_tool_result:
            return Role.ASSISTANT

    metadata = message.metadata or {}
    for key in ("raw_role", "role", "sender_role", "author_role", "speaker", "sender"):
        hinted = role_from_hint(str(metadata.get(key, "")))
        if hinted:
            return hinted
    return None


def _looks_like_tool_placeholder(text: str, message: Message) -> bool:
    has_tool_part = any(
        (part.type.value if hasattr(part.type, "value") else str(part.type))
        in (MessagePartType.TOOL_CALL.value, MessagePartType.TOOL_RESULT.value)
        for part in message.parts
    )
    return has_tool_part and any(pattern.search(text or "") for pattern in _TOOL_PLACEHOLDER_PATTERNS)


def _looks_like_thinking_placeholder(text: str, message: Message) -> bool:
    has_thinking = any(
        (part.type.value if hasattr(part.type, "value") else str(part.type))
        == MessagePartType.THINKING.value
        for part in message.parts
    )
    return has_thinking and any(pattern.search(text or "") for pattern in _THINKING_PLACEHOLDER_PATTERNS)


def message_preview_text(message: Message, source_app: str = "") -> str:
    """提取适合阅读和全文检索的用户/AI正文。

    思考过程、工具调用、工具结果和系统上下文仍保留给 Markdown 完整导出，
    但不会污染预览界面。
    """

    role = effective_role(message)
    if role not in VISIBLE_ROLES:
        return ""

    chunks: List[str] = []
    seen = set()

    def append(text: str) -> None:
        value = strip_internal_context(text, source_app=source_app)
        if not value:
            # 与导出器一致：strip_internal_context 清空时用原始文本兜底，
            # 避免预览丢失消息（导出有 _clean_content 兜底，预览也应有）。
            value = _clean(text)
            if not value:
                return
        if _looks_like_tool_placeholder(value, message):
            return
        if _looks_like_thinking_placeholder(value, message):
            return
        key = value.casefold()
        if key in seen:
            return
        seen.add(key)
        chunks.append(value)

    # 与导出器一致：优先从 parts 的 TEXT parts 提取正文。
    has_text_part = False
    for part in message.parts:
        part_type = part.type.value if hasattr(part.type, "value") else str(part.type)
        if part_type == MessagePartType.TEXT.value:
            has_text_part = True
            append(part.content)
        elif part_type == MessagePartType.CODE.value:
            language = _clean(part.language or "")
            code = _clean(part.content)
            if code:
                append(f"```{language}\n{code}\n```")
        elif part_type in (MessagePartType.FILE.value, MessagePartType.IMAGE.value):
            name = _clean(part.file_name or part.content or "附件")
            append(f"[附件] {name}")

    # 回退：parts 中没有 TEXT parts 时，用 content 作为正文来源。
    if not has_text_part and message.content and message.content.strip():
        append(message.content)

    # 回退：如果 ASSISTANT 消息没有任何可见文本，但有 thinking parts，
    # 从 thinking 中提取摘要，避免 AI 回复在预览中完全消失
    if not chunks and role == Role.ASSISTANT:
        for part in message.parts:
            part_type = part.type.value if hasattr(part.type, "value") else str(part.type)
            if part_type == MessagePartType.THINKING.value:
                thinking_text = _clean(part.content)
                if thinking_text:
                    lines = [l for l in thinking_text.splitlines() if l.strip()]
                    if lines:
                        summary = "\n".join(lines[:8])
                        if len(summary) > 600:
                            summary = summary[:600] + "…"
                        chunks.append(f"[AI 思考摘要]\n{summary}")
                        break

    return "\n\n".join(chunks)


def visible_messages(conversation: Conversation) -> List[Tuple[Message, Role, str]]:
    """返回 (message, effective_role, preview_text) 三元组。

    不修改原始 message.role，避免共享对象变异副作用。
    """
    result: List[Tuple[Message, Role, str]] = []
    for message in conversation.messages:
        role = effective_role(message)
        text = message_preview_text(message, source_app=conversation.source_app)
        if not text or role not in VISIBLE_ROLES:
            continue
        result.append((message, role, text))
    return result


def conversation_search_text(conversation: Conversation) -> str:
    """构建用于本机全文检索的标准化文本。"""

    parts: List[str] = [_clean(conversation.title)]
    for _message, _role, text in visible_messages(conversation):
        parts.append(text)
    return "\n".join(parts).casefold()


def plain_preview_text(conversation: Conversation) -> str:
    """生成可复制的用户/AI纯文本对话。"""

    blocks: List[str] = []
    for message, role, text in visible_messages(conversation):
        role_label = "用户" if role == Role.USER else "AI 助手"
        timestamp = message.timestamp.strftime("%Y-%m-%d %H:%M:%S") if message.timestamp else ""
        header = role_label if not timestamp else f"{role_label} · {timestamp}"
        blocks.append(f"{header}\n{text}")
    return "\n\n".join(blocks)

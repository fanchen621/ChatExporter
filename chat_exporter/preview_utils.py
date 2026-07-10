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
_XML_TAG_LINE = re.compile(r"^\s*</?[\w:-]+(?:\s[^>]*)?>\s*$")
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

        if _XML_TAG_LINE.match(stripped):
            continue
        if any(pattern.search(stripped) for pattern in _INTERNAL_LINE_PATTERNS):
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

    if effective_role(message) not in VISIBLE_ROLES:
        return ""

    chunks: List[str] = []
    seen = set()

    def append(text: str) -> None:
        value = strip_internal_context(text, source_app=source_app)
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

    append(message.content)

    for part in message.parts:
        part_type = part.type.value if hasattr(part.type, "value") else str(part.type)
        if part_type == MessagePartType.TEXT.value:
            append(part.content)
        elif part_type == MessagePartType.CODE.value:
            language = _clean(part.language or "")
            code = _clean(part.content)
            if code:
                append(f"```{language}\n{code}\n```")
        elif part_type in (MessagePartType.FILE.value, MessagePartType.IMAGE.value):
            name = _clean(part.file_name or part.content or "附件")
            append(f"[附件] {name}")
        # THINKING / TOOL_CALL / TOOL_RESULT 故意不进入预览。

    return "\n\n".join(chunks)


def visible_messages(conversation: Conversation) -> List[Tuple[Message, str]]:
    result: List[Tuple[Message, str]] = []
    for message in conversation.messages:
        role = effective_role(message)
        text = message_preview_text(message, source_app=conversation.source_app)
        if not text or role not in VISIBLE_ROLES:
            continue
        if message.role != role:
            # 只修复内存中的显示模型，原始角色仍保留在 metadata 中。
            message.role = role
        result.append((message, text))
    return result


def conversation_search_text(conversation: Conversation) -> str:
    """构建用于本机全文检索的标准化文本。"""

    parts: List[str] = [_clean(conversation.title)]
    for _message, text in visible_messages(conversation):
        parts.append(text)
    return "\n".join(parts).casefold()


def plain_preview_text(conversation: Conversation) -> str:
    """生成可复制的用户/AI纯文本对话。"""

    blocks: List[str] = []
    for message, text in visible_messages(conversation):
        role = "用户" if effective_role(message) == Role.USER else "AI 助手"
        timestamp = message.timestamp.strftime("%Y-%m-%d %H:%M:%S") if message.timestamp else ""
        header = role if not timestamp else f"{role} · {timestamp}"
        blocks.append(f"{header}\n{text}")
    return "\n\n".join(blocks)

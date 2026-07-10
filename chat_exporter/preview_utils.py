from __future__ import annotations

from typing import Iterable, List, Tuple

from .models import Conversation, Message, MessagePartType, Role


VISIBLE_ROLES = {Role.USER, Role.ASSISTANT}


def _clean(text: str) -> str:
    return (text or "").replace("\r\n", "\n").strip()


def message_preview_text(message: Message) -> str:
    """提取适合预览的用户/AI 正文。

    预览刻意忽略系统消息、工具调用、工具结果和思考过程；完整导出仍由
    ``MarkdownExporter`` 保留这些细节。代码和附件属于用户可见正文的一部分，
    因此会保留。
    """
    if message.role not in VISIBLE_ROLES:
        return ""

    chunks: List[str] = []
    seen = set()

    def append(text: str) -> None:
        value = _clean(text)
        if not value:
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
        text = message_preview_text(message)
        if text:
            result.append((message, text))
    return result


def conversation_search_text(conversation: Conversation) -> str:
    """构建用于本地全文检索的标准化文本。"""
    parts: List[str] = [_clean(conversation.title)]
    for _message, text in visible_messages(conversation):
        parts.append(text)
    return "\n".join(parts).casefold()


def plain_preview_text(conversation: Conversation) -> str:
    """生成可复制的纯文本预览。"""
    blocks: List[str] = []
    for message, text in visible_messages(conversation):
        role = "用户" if message.role == Role.USER else "AI 助手"
        timestamp = message.timestamp.strftime("%Y-%m-%d %H:%M:%S") if message.timestamp else ""
        header = role if not timestamp else f"{role} · {timestamp}"
        blocks.append(f"{header}\n{text}")
    return "\n\n".join(blocks)

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
_INTERNAL_TAG_LINE = re.compile(
    rf"^\s*</?(?:{'|'.join(re.escape(t) for t in _INTERNAL_BLOCK_TAGS)})(?:\s[^>]*)?>\s*$",
    re.IGNORECASE,
)
# 开标签必须整词匹配：<current_time> 是内部标签，<current_timezone> 不是。
# 早期版本用 startswith 前缀判断，导致同名前缀的普通标签让整条消息被吞掉。
_INTERNAL_OPEN_TAG = re.compile(
    rf"^<({'|'.join(re.escape(t) for t in _INTERNAL_BLOCK_TAGS)})(?=[\s/>])",
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


# 代码块里的 <system-reminder> 之类是用户正在讨论的内容，不是运行时注入的上下文。
# 清洗前先把围栏块挖出来占位，清洗后原样放回。占位符用私有区字符，_clean 不会动它。
_FENCED_BLOCK = re.compile(r"(?m)^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)", re.DOTALL)
_FENCE_SLOT = "FENCE{}"
_FENCE_SLOT_RE = re.compile(r"FENCE(\d+)")


def _mask_fenced_blocks(text: str) -> Tuple[str, List[str]]:
    blocks: List[str] = []

    def take(match: "re.Match[str]") -> str:
        blocks.append(match.group(0))
        return _FENCE_SLOT.format(len(blocks) - 1)

    return _FENCED_BLOCK.sub(take, text), blocks


def _restore_fenced_blocks(text: str, blocks: List[str]) -> str:
    if not blocks:
        return text

    def put(match: "re.Match[str]") -> str:
        index = int(match.group(1))
        return blocks[index] if 0 <= index < len(blocks) else ""

    return _FENCE_SLOT_RE.sub(put, text)


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

    value, fenced_blocks = _mask_fenced_blocks(value)

    queries = [_clean(match) for match in _USER_QUERY_PATTERN.findall(value)]
    queries = [item for item in queries if item]

    injected_block_removed = False
    for pattern in _INTERNAL_BLOCK_PATTERNS:
        value, hits = pattern.subn("\n", value)
        injected_block_removed = injected_block_removed or bool(hits)
    value = re.sub(r"</?user_query(?:\s[^>]*)?>", "\n", value, flags=re.I)

    lines = value.splitlines()
    # 行级环境噪声（OS Version:/Shell:/Path: …）只在这条消息确实带了注入上下文时才清理。
    # 否则用户自己敲的 "Shell: 我该用哪个？" 会被整行删掉。
    has_injected_context = injected_block_removed or any(
        _INTERNAL_TAG_LINE.match(line.strip()) or _INTERNAL_OPEN_TAG.match(line.strip())
        for line in lines
    )

    kept: List[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        opened = _INTERNAL_OPEN_TAG.match(stripped)
        if opened:
            tag = opened.group(1).casefold()
            # 只有找得到配对的闭合行才跳过整块。找不到就说明这是残缺/伪造的标签，
            # 此时吞掉剩余全部内容会静默删除真实正文（v1.1.3 及之前的行为）。
            close = None
            for probe in range(index + 1, len(lines)):
                if lines[probe].strip().casefold().startswith(f"</{tag}"):
                    close = probe
                    break
            if close is not None:
                index = close + 1
                continue

        if _INTERNAL_TAG_LINE.match(stripped):
            index += 1
            continue
        if _USER_QUERY_TAG_LINE.match(stripped):
            index += 1
            continue
        if has_injected_context and any(
            pattern.search(stripped) for pattern in _INTERNAL_LINE_PATTERNS
        ):
            index += 1
            continue
        if source_app.casefold().startswith("trae") and any(
            pattern.match(stripped) for pattern in _TRAE_UI_LINE_PATTERNS
        ):
            index += 1
            continue
        kept.append(line)
        index += 1

    cleaned = _clean("\n".join(kept))
    if queries:
        for query in queries:
            if query.casefold() not in cleaned.casefold():
                cleaned = _clean(f"{cleaned}\n\n{query}" if cleaned else query)

    if source_app.casefold().startswith("workbuddy"):
        lines = cleaned.splitlines()
        # 注意：真正的代码块此时已被占位符替换，这里 pop 掉的只会是注入块残留的
        # 孤立分隔符，不会再吃掉 AI 回答结尾那条闭合围栏。
        while lines and lines[-1].strip() in {"---", "```", "Injected workspace identity files:"}:
            lines.pop()
        cleaned = _clean("\n".join(lines))

    return _restore_fenced_blocks(cleaned, fenced_blocks)


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


def _part_type(part) -> str:
    return part.type.value if hasattr(part.type, "value") else str(part.type)


def effective_role(message: Message) -> Optional[Role]:
    if message.role in VISIBLE_ROLES:
        return message.role

    if message.role == Role.SYSTEM and any(
        _part_type(part) == MessagePartType.THINKING.value for part in message.parts
    ):
        return Role.ASSISTANT

    if message.role == Role.TOOL and any(
        _part_type(part) == MessagePartType.TOOL_RESULT.value for part in message.parts
    ):
        return Role.ASSISTANT

    metadata = message.metadata or {}
    for key in ("raw_role", "role", "sender_role", "author_role", "speaker", "sender"):
        hinted = role_from_hint(str(metadata.get(key, "")))
        if hinted:
            return hinted
    return None


def _looks_like_tool_placeholder(text: str, message: Message) -> bool:
    has_tool_part = any(
        _part_type(part) in (MessagePartType.TOOL_CALL.value, MessagePartType.TOOL_RESULT.value)
        for part in message.parts
    )
    return has_tool_part and any(pattern.search(text or "") for pattern in _TOOL_PLACEHOLDER_PATTERNS)


def _looks_like_thinking_placeholder(text: str, message: Message) -> bool:
    has_thinking = any(
        _part_type(part) == MessagePartType.THINKING.value for part in message.parts
    )
    return has_thinking and any(pattern.search(text or "") for pattern in _THINKING_PLACEHOLDER_PATTERNS)


def _fallback_visible_text(message: Message, source_app: str, role: Role) -> str:
    """Readable fallback for messages whose final body is not a TEXT part."""
    if role != Role.ASSISTANT:
        return ""

    thinking = [
        _clean(part.content)
        for part in message.parts
        if _part_type(part) == MessagePartType.THINKING.value and _clean(part.content)
    ]
    if thinking:
        if (source_app or "").casefold().startswith("trae"):
            # A TRAE task can split the final delivery across several plan_item
            # rows. Keep every block instead of stopping after the first one.
            return "\n\n---\n\n".join(thinking)

        lines = [line for line in thinking[0].splitlines() if line.strip()]
        if lines:
            summary = "\n".join(lines[:8])
            if len(summary) > 600:
                summary = summary[:600] + "…"
            return f"[AI 思考摘要]\n{summary}"

    for part in reversed(message.parts):
        if _part_type(part) != MessagePartType.TOOL_RESULT.value:
            continue
        output = _clean(part.tool_output or part.content or "")
        if output:
            return f"[工具结果]\n{output}"
    return ""


def message_preview_text(message: Message, source_app: str = "", include_fallback: bool = True) -> str:
    """提取适合阅读和全文检索的用户/AI正文。

    正常回复只展示用户/AI正文、代码和附件。若客户端把最终交付仅存入
    thinking 或 tool_result，则使用完整性回退，避免整条消息在预览中消失。

    include_fallback=False 就是"只看对话"模式：不做这层回退，思考和工具
    结果一律不进阅读视图。代价是那些把最终交付只写进 thinking 的消息会
    整条消失——所以它是一个显式开关，不是默认值。导出永远不受影响。
    """

    role = effective_role(message)
    if role not in VISIBLE_ROLES:
        return ""

    chunks: List[str] = []
    seen = set()

    def append(text: str) -> bool:
        value = strip_internal_context(text, source_app=source_app)
        if not value:
            value = _clean(text)
            if not value:
                return False
        if _looks_like_tool_placeholder(value, message):
            return False
        if _looks_like_thinking_placeholder(value, message):
            return False
        key = value.casefold()
        if key in seen:
            return False
        seen.add(key)
        chunks.append(value)
        return True

    has_text_part = False
    has_primary_body = False
    for part in message.parts:
        part_type = _part_type(part)
        if part_type == MessagePartType.TEXT.value:
            has_text_part = True
            has_primary_body = append(part.content) or has_primary_body
        elif part_type == MessagePartType.CODE.value:
            language = _clean(part.language or "")
            code = _clean(part.content)
            if code:
                has_primary_body = append(f"```{language}\n{code}\n```") or has_primary_body
        elif part_type in (MessagePartType.FILE.value, MessagePartType.IMAGE.value):
            name = _clean(part.file_name or part.content or "附件")
            append(f"[附件] {name}")

    if not has_text_part and message.content and message.content.strip():
        has_primary_body = append(message.content) or has_primary_body

    # Attachments do not count as the answer body. A message containing only an
    # attachment plus reasoning/tool output still needs its actual delivery.
    if not has_primary_body and include_fallback:
        fallback = _fallback_visible_text(message, source_app, role)
        if fallback:
            append(fallback)

    return "\n\n".join(chunks)


def visible_messages(conversation: Conversation, include_fallback: bool = True) -> List[Tuple[Message, Role, str]]:
    """返回 (message, effective_role, preview_text) 三元组，不修改原对象。"""
    result: List[Tuple[Message, Role, str]] = []
    for message in conversation.messages:
        role = effective_role(message)
        text = message_preview_text(
            message, source_app=conversation.source_app, include_fallback=include_fallback
        )
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


def plain_preview_text(conversation: Conversation, include_fallback: bool = True) -> str:
    """生成可复制的用户/AI纯文本对话。"""
    blocks: List[str] = []
    for message, role, text in visible_messages(conversation, include_fallback=include_fallback):
        role_label = "用户" if role == Role.USER else "AI 助手"
        timestamp = message.timestamp.strftime("%Y-%m-%d %H:%M:%S") if message.timestamp else ""
        header = role_label if not timestamp else f"{role_label} · {timestamp}"
        blocks.append(f"{header}\n{text}")
    return "\n\n".join(blocks)

"""Virtualized preview pages for very large conversations.

The preview is a reading surface, not an export surface.  Rendering fourteen
thousand messages into one Tk Text widget is expensive and unnecessary.  The
runtime below produces small pages while export remains complete and lossless.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from . import markdown_render
from .models import Conversation, Message, Role
from .preview_utils import (
    PREVIEW_CLEAN,
    PREVIEW_FULL,
    effective_role,
    message_preview_text,
    visible_messages,
)
from .task_runtime import TaskContext


PREVIEW_MESSAGE_MAX_CHARS = 80_000
PREVIEW_PAGE_MAX_CHARS = 360_000


@dataclass(frozen=True, slots=True)
class PreviewEntry:
    message: Message
    role: Role
    text: str


@dataclass(frozen=True, slots=True)
class PreviewPage:
    entries: Tuple[PreviewEntry, ...]
    source_start: int
    source_end: int
    total_source_messages: int
    has_older: bool
    has_newer: bool
    mode: str
    label: str = ""

    @property
    def visible_count(self) -> int:
        return len(self.entries)


@dataclass(frozen=True, slots=True)
class PreviewPayload:
    conversation: Conversation
    page: PreviewPage
    segments: Tuple[Tuple[str, object], ...]
    plain_text: str


@dataclass(frozen=True, slots=True)
class PreviewWindow:
    """Adapter-provided partial conversation with opaque paging cursors."""

    conversation: Conversation
    cursor_before: Optional[int]
    cursor_after: Optional[int]
    has_older: bool
    has_newer: bool
    total_source_messages: int = 0
    label: str = ""


def _bounded_text(text: str, limit: int = PREVIEW_MESSAGE_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    # Preserve both the beginning and the final answer/error.  Preview is
    # intentionally bounded; export remains complete and lossless.
    tail = max(8_000, limit // 4)
    head = max(8_000, limit - tail)
    omitted = len(text) - head - tail
    return (
        text[:head]
        + f"\n\n[… 该条消息过长，预览省略 {omitted:,} 个字符；完整内容仍会导出 …]\n\n"
        + text[-tail:]
    )


def _entry(message: Message, source_app: str, mode: str) -> Optional[PreviewEntry]:
    role = effective_role(message)
    if role not in (Role.USER, Role.ASSISTANT):
        return None
    text = message_preview_text(message, source_app=source_app, mode=mode)
    if not text:
        return None
    return PreviewEntry(message=message, role=role, text=_bounded_text(text))


def _clean_entries(conversation: Conversation) -> Tuple[Tuple[int, PreviewEntry], ...]:
    visible = visible_messages(conversation, mode=PREVIEW_CLEAN)
    positions = {id(message): index for index, message in enumerate(conversation.messages)}
    return tuple(
        (positions.get(id(message), 0), PreviewEntry(message, role, text))
        for message, role, text in visible
    )


def format_page_label(
    *,
    anchor: str,
    start: int,
    end: int,
    total: int,
    visible_count: int,
) -> str:
    """Human-readable preview position for the pager status line."""
    if visible_count <= 0:
        return "本页无可见正文"
    if total > 0:
        if anchor == "latest" and end >= total:
            return f"最近 {visible_count} 条 · 共 {total:,}"
        if anchor == "earliest" and start <= 0:
            return f"最早 {visible_count} 条 · 共 {total:,}"
        return f"{start + 1}–{end} · 共 {total:,}"
    if anchor == "latest":
        return f"最近 {visible_count} 条"
    if anchor == "earliest":
        return f"最早 {visible_count} 条"
    return f"本页 {visible_count} 条"


def build_preview_page(
    conversation: Conversation,
    *,
    mode: str = PREVIEW_FULL,
    page_size: int = 160,
    anchor: str = "latest",
    cursor: Optional[int] = None,
    context: Optional[TaskContext] = None,
) -> PreviewPage:
    """Build one chronological preview page around a raw-message cursor."""
    page_size = max(20, min(500, int(page_size)))
    messages = conversation.messages
    total = len(messages)
    if not messages:
        return PreviewPage((), 0, 0, 0, False, False, mode)

    if mode == PREVIEW_CLEAN:
        pairs = _clean_entries(conversation)
        if context:
            context.check_cancelled()
        if anchor == "earliest":
            selected = pairs[:page_size]
        elif anchor == "older":
            boundary = total if cursor is None else cursor
            candidates = [pair for pair in pairs if pair[0] < boundary]
            selected = candidates[-page_size:]
        elif anchor == "newer":
            boundary = -1 if cursor is None else cursor
            candidates = [pair for pair in pairs if pair[0] >= boundary]
            selected = candidates[:page_size]
        else:
            selected = pairs[-page_size:]
        if not selected:
            return PreviewPage((), 0, 0, total, False, False, mode, label="本页无可见正文")
        start = selected[0][0]
        end = selected[-1][0] + 1
        entries = tuple(pair[1] for pair in selected)
        label = format_page_label(
            anchor=anchor,
            start=start,
            end=end,
            total=total,
            visible_count=len(entries),
        )
        return PreviewPage(entries, start, end, total, start > 0, end < total, mode, label=label)

    def checked(indices: Iterable[int]) -> Tuple[Tuple[int, PreviewEntry], ...]:
        found = []
        char_count = 0
        for iteration, index in enumerate(indices, start=1):
            if context and iteration % 64 == 0:
                context.check_cancelled()
            item = _entry(messages[index], conversation.source_app, PREVIEW_FULL)
            if item is not None:
                projected = char_count + len(item.text)
                if found and projected > PREVIEW_PAGE_MAX_CHARS:
                    break
                found.append((index, item))
                char_count = projected
                if len(found) >= page_size:
                    break
        return tuple(found)

    if anchor == "earliest":
        start_search = 0
        found = checked(range(start_search, total))
        if not found:
            return PreviewPage((), 0, total, total, False, False, mode)
        start, end = found[0][0], found[-1][0] + 1
        entries = tuple(item for _index, item in found)
    elif anchor == "newer":
        start_search = max(0, cursor or 0)
        found = checked(range(start_search, total))
        if not found:
            return PreviewPage((), start_search, total, total, start_search > 0, False, mode)
        start, end = found[0][0], found[-1][0] + 1
        entries = tuple(item for _index, item in found)
    else:
        end_search = total if cursor is None else max(0, min(total, cursor))
        reverse_found = checked(range(end_search - 1, -1, -1))
        if not reverse_found:
            return PreviewPage((), 0, end_search, total, False, end_search < total, mode)
        chronological = tuple(reversed(reverse_found))
        start, end = chronological[0][0], chronological[-1][0] + 1
        entries = tuple(item for _index, item in chronological)

    return PreviewPage(
        entries=entries,
        source_start=start,
        source_end=end,
        total_source_messages=total,
        has_older=start > 0,
        has_newer=end < total,
        mode=mode,
        label=format_page_label(
            anchor=anchor,
            start=start,
            end=end,
            total=total,
            visible_count=len(entries),
        ),
    )


def build_preview_segments(entries: Sequence[PreviewEntry]) -> Tuple[Tuple[str, object], ...]:
    segments = []
    for index, entry in enumerate(entries):
        role_name = "用户" if entry.role == Role.USER else "AI 助手"
        header_tag = "user_header" if entry.role == Role.USER else "assistant_header"
        body_tag = "user_body" if entry.role == Role.USER else "assistant_body"
        dot_tag = "user_dot" if entry.role == Role.USER else "ai_dot"
        if index:
            segments.append(("\n", "message_gap"))
        segments.append(("● ", dot_tag))
        segments.append((role_name, header_tag))
        meta = []
        if entry.message.timestamp:
            meta.append(entry.message.timestamp.strftime("%Y-%m-%d %H:%M"))
        if entry.message.model and entry.role == Role.ASSISTANT:
            meta.append(entry.message.model)
        if meta:
            segments.append((f"   {' · '.join(meta)}", "header_meta"))
        segments.append(("\n", header_tag))
        segments.extend(markdown_render.render_tk(entry.text, body_tag))
    return tuple(segments)


def page_plain_text(entries: Sequence[PreviewEntry]) -> str:
    blocks = []
    for entry in entries:
        role = "用户" if entry.role == Role.USER else "AI 助手"
        timestamp = entry.message.timestamp.strftime("%Y-%m-%d %H:%M:%S") if entry.message.timestamp else ""
        header = role if not timestamp else f"{role} · {timestamp}"
        blocks.append(f"{header}\n{entry.text}")
    return "\n\n".join(blocks)


def build_preview_payload(
    conversation: Conversation,
    *,
    mode: str = PREVIEW_FULL,
    page_size: int = 160,
    anchor: str = "latest",
    cursor: Optional[int] = None,
    context: Optional[TaskContext] = None,
) -> PreviewPayload:
    page = build_preview_page(
        conversation,
        mode=mode,
        page_size=page_size,
        anchor=anchor,
        cursor=cursor,
        context=context,
    )
    if context:
        context.check_cancelled()
    segments = build_preview_segments(page.entries)
    plain = page_plain_text(page.entries)
    return PreviewPayload(conversation, page, segments, plain)

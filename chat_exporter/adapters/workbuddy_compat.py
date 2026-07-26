from __future__ import annotations

import re
from typing import Optional

from .workbuddy import WorkBuddyAdapter as BaseWorkBuddyAdapter
from ..models import Message, MessagePart, MessagePartType, Role
from ..preview_utils import strip_internal_context

_BALANCED_TAG_BLOCK = re.compile(r"<([A-Za-z_][\w.:-]*)(?:\s[^>]*)?>.*?</\1\s*>", re.DOTALL)


def _is_pure_injected_block(text: str) -> bool:
    """整条正文是否只由成对标签块组成（<system-reminder>…</system-reminder>）。

    这种记录本来就不是用户说的话，清空后丢弃是对的；但只要标签块之外还有
    一句真实正文，清洗把它清成空就属于误伤，必须回退原文。
    """
    residue = text
    for _ in range(5):
        residue, hits = _BALANCED_TAG_BLOCK.subn(" ", residue)
        if not hits:
            break
    return not residue.strip()


class WorkBuddyAdapter(BaseWorkBuddyAdapter):
    """WorkBuddy 兼容层：从可见正文中移除运行时注入上下文。

    Reasoning、工具调用和工具结果仍作为独立消息保留，完整 Markdown 导出不会
    丢失这些信息；只清理并非真实用户对话的 system-reminder / identity context。
    """

    def _parse_record(self, record: dict) -> Optional[Message]:
        message = super()._parse_record(record)
        if not message:
            return None

        metadata = dict(message.metadata or {})
        metadata["raw_role"] = str(record.get("role", ""))
        metadata["record_type"] = str(record.get("type", ""))
        message.metadata = metadata

        if message.role not in (Role.USER, Role.ASSISTANT):
            return message

        original_content = message.content or ""
        cleaned_content = strip_internal_context(original_content, source_app=self.display_name)

        cleaned_parts = []
        for part in message.parts:
            if part.type == MessagePartType.TEXT:
                cleaned = strip_internal_context(part.content or "", source_app=self.display_name)
                if cleaned:
                    cleaned_parts.append(
                        MessagePart(
                            type=MessagePartType.TEXT,
                            content=cleaned,
                            metadata=dict(part.metadata or {}),
                        )
                    )
            else:
                cleaned_parts.append(part)

        if cleaned_content != original_content:
            message.metadata["internal_context_removed"] = True
            message.metadata["removed_char_count"] = max(0, len(original_content) - len(cleaned_content))

        if not cleaned_content:
            visible_text = [
                part.content
                for part in cleaned_parts
                if part.type == MessagePartType.TEXT and (part.content or "").strip()
            ]
            cleaned_content = "\n\n".join(visible_text).strip()

        message.content = cleaned_content
        message.parts = cleaned_parts

        if not message.content and not any(
            part.type in (MessagePartType.CODE, MessagePartType.FILE, MessagePartType.IMAGE)
            for part in message.parts
        ):
            # 只有"整条正文都是成对注入块"才允许丢弃。清洗把非纯注入的原文清成
            # 空属于误伤（下游导出/预览本来都是 fail-open 的），必须回退原文。
            if original_content.strip() and not _is_pure_injected_block(original_content):
                message.content = original_content.strip()
                message.parts = [
                    MessagePart(type=MessagePartType.TEXT, content=message.content)
                ]
                message.metadata["internal_context_removed"] = False
                return message
            return None

        return message

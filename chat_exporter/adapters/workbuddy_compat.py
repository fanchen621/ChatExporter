from __future__ import annotations

from typing import Optional

from .workbuddy import WorkBuddyAdapter as BaseWorkBuddyAdapter
from ..models import Message, MessagePart, MessagePartType, Role
from ..preview_utils import strip_internal_context


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

        # 纯内部注入记录不再伪装成用户消息。
        if not message.content and not any(
            part.type in (MessagePartType.CODE, MessagePartType.FILE, MessagePartType.IMAGE)
            for part in message.parts
        ):
            return None

        return message

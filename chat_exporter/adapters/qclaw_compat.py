from __future__ import annotations

import re
from typing import Optional

from .qclaw import QClawAdapter as BaseQClawAdapter
from ..models import Message, Role
from ..preview_utils import role_from_hint


class QClawAdapter(BaseQClawAdapter):
    """QClaw 兼容层：恢复不同版本产生的用户/AI角色名。"""

    @staticmethod
    def _parse_role(role_str: str) -> Role:
        hinted = role_from_hint(role_str)
        if hinted:
            return hinted

        normalized = re.sub(r"[^a-z0-9]+", "", (role_str or "").casefold())
        if any(token in normalized for token in ("tool", "function")):
            return Role.TOOL
        if any(token in normalized for token in ("system", "reasoning", "thinking", "developer")):
            return Role.SYSTEM
        return Role.SYSTEM

    def _parse_message(self, msg_row, part_rows) -> Optional[Message]:
        message = super()._parse_message(msg_row, part_rows)
        if not message:
            return None

        raw_role = str(msg_row["role"] or "")
        part_types = [str((row["part_type"] or "")).casefold() for row in part_rows]
        inferred = role_from_hint(raw_role)

        if inferred is None:
            if any(
                any(token in part_type for token in ("input", "user", "human", "prompt"))
                for part_type in part_types
            ):
                inferred = Role.USER
            elif any(
                any(token in part_type for token in ("output", "assistant", "agent", "answer"))
                for part_type in part_types
            ):
                inferred = Role.ASSISTANT

        content = (message.content or "").lstrip().casefold()
        if inferred is None:
            if content.startswith(("user:", "human:", "用户：", "用户:")):
                inferred = Role.USER
            elif content.startswith(("assistant:", "ai:", "助手：", "助手:")):
                inferred = Role.ASSISTANT

        if inferred:
            message.role = inferred

        metadata = dict(message.metadata or {})
        metadata.update(
            {
                "raw_role": raw_role,
                "seq": msg_row["seq"],
                "part_types": part_types,
            }
        )
        message.metadata = metadata
        return message

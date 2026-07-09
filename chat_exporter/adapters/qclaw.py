import os
import re
from pathlib import Path
from typing import List, Optional

from .base import BaseAdapter
from ..models import AppInfo, Conversation, Message, MessagePart, MessagePartType, Role


class QClawAdapter(BaseAdapter):
    name = "qclaw"
    display_name = "QClaw"

    def __init__(self):
        super().__init__()
        self.db_path = os.path.join(str(self.user_home), ".qclaw", "memory", "lossless", "lcm.db")
        self._cached_conversations = None

    def detect(self) -> bool:
        return os.path.exists(self.db_path)

    def get_app_info(self) -> AppInfo:
        available = self.detect()
        conv_count = 0
        if available:
            try:
                convs = self.list_conversations()
                conv_count = len(convs)
            except Exception:
                pass
        return AppInfo(
            name=self.name,
            display_name=self.display_name,
            is_available=available,
            data_path=self.db_path if available else None,
            conversation_count=conv_count
        )

    def list_conversations(self) -> List[Conversation]:
        if self._cached_conversations is not None:
            return self._cached_conversations

        if not self.detect():
            return []

        conn = self._connect_db(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                c.conversation_id,
                c.session_id,
                c.title,
                c.created_at,
                c.updated_at,
                c.archived_at,
                COUNT(m.message_id) AS msg_count,
                (SELECT content FROM messages
                 WHERE conversation_id = c.conversation_id AND role = 'user'
                 ORDER BY seq ASC LIMIT 1) AS first_user_msg,
                (SELECT content FROM messages
                 WHERE conversation_id = c.conversation_id
                 ORDER BY seq ASC LIMIT 1) AS first_msg
            FROM conversations c
            LEFT JOIN messages m ON c.conversation_id = m.conversation_id
            GROUP BY c.conversation_id
            ORDER BY c.updated_at DESC
        """)

        conversations = []
        for row in cursor.fetchall():
            title = self._clean_title(row["title"] or "")
            if not title:
                title = self._clean_title(row["first_user_msg"] or row["first_msg"] or "")
            if not title and row["session_id"]:
                title = f"对话 {row['session_id'][:8]}..."
            if not title:
                title = f"对话 #{row['conversation_id']}"

            conv = Conversation(
                id=str(row["conversation_id"]),
                title=title,
                created_at=self._parse_dt(row["created_at"]),
                updated_at=self._parse_dt(row["updated_at"]),
                source_app=self.display_name,
                metadata={
                    "session_id": row["session_id"],
                    "archived": row["archived_at"] is not None,
                    "msg_count": row["msg_count"] or 0,
                }
            )
            conversations.append(conv)

        conn.close()
        self._cached_conversations = conversations
        return conversations

    def get_conversation(self, conv_id: str) -> Optional[Conversation]:
        if not self.detect():
            return None

        conn = self._connect_db(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT conversation_id, session_id, title, created_at, updated_at
            FROM conversations
            WHERE conversation_id = ?
        """, (int(conv_id),))
        conv_row = cursor.fetchone()
        if not conv_row:
            conn.close()
            return None

        cursor.execute("""
            SELECT message_id, seq, role, content, created_at, token_count
            FROM messages
            WHERE conversation_id = ?
            ORDER BY seq ASC
        """, (int(conv_id),))
        msg_rows = cursor.fetchall()

        messages = []
        for msg_row in msg_rows:
            message_id = msg_row["message_id"]

            cursor.execute("""
                SELECT part_type, text_content, tool_name, tool_input, tool_output,
                       tool_error, file_name, ordinal, metadata
                FROM message_parts
                WHERE message_id = ?
                ORDER BY ordinal ASC
            """, (message_id,))
            part_rows = cursor.fetchall()

            msg = self._parse_message(msg_row, part_rows)
            if msg:
                messages.append(msg)

        conn.close()

        title = self._clean_title(conv_row["title"] or "")
        if not title and conv_row["session_id"]:
            title = f"对话 {conv_row['session_id'][:8]}..."
        if not title:
            title = "(无标题对话)"

        conv = Conversation(
            id=str(conv_row["conversation_id"]),
            title=title,
            created_at=self._parse_dt(conv_row["created_at"]),
            updated_at=self._parse_dt(conv_row["updated_at"]),
            messages=messages,
            source_app=self.display_name,
            metadata={"session_id": conv_row["session_id"]}
        )
        return conv

    def _parse_message(self, msg_row, part_rows):
        role_str = msg_row["role"]
        try:
            role = Role(role_str)
        except ValueError:
            role = Role.USER

        parts = []
        text_content = msg_row["content"] or ""

        for prow in part_rows:
            ptype = prow["part_type"]
            txt = prow["text_content"] or ""

            if ptype == "text":
                if txt:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=txt))
            elif ptype == "tool_call":
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_CALL,
                    tool_name=prow["tool_name"],
                    tool_input=prow["tool_input"] or "",
                    content=txt
                ))
            elif ptype == "tool_result":
                output = prow["tool_output"] or prow["tool_error"] or ""
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_RESULT,
                    tool_name=prow["tool_name"],
                    tool_output=output
                ))
            elif ptype == "file":
                parts.append(MessagePart(
                    type=MessagePartType.FILE,
                    file_name=prow["file_name"],
                    content=txt
                ))
            elif ptype in ("thinking", "reasoning"):
                parts.append(MessagePart(type=MessagePartType.THINKING, content=txt))
            else:
                if txt:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=txt))

        return Message(
            role=role,
            content=text_content,
            timestamp=self._parse_dt(msg_row["created_at"]),
            message_id=str(msg_row["message_id"]),
            parts=parts,
            token_usage={"total_tokens": msg_row["token_count"]} if msg_row["token_count"] else None
        )

    @staticmethod
    def _clean_title(raw: str) -> str:
        """Clean a raw message string to produce a usable conversation title."""
        if not raw:
            return ""

        # Split into lines and process each one
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue

            # Strip markdown code fences
            if line.startswith("```"):
                continue

            # Strip JSON/metadata prefixes like "Sender (untrusted metadata):", "Sender:", etc.
            line = re.sub(r'^Sender\s*(?:\([^)]*\))?\s*:\s*', '', line)

            # Strip timestamp prefixes like "[Wed 2026-06-24 23:06 GMT+8]"
            line = re.sub(r'^\[[\w\s:+\-]+\]\s*', '', line)

            line = line.strip()
            if not line:
                continue

            # Skip lines that are pure JSON (start with { or [)
            if line.startswith(('{', '[')):
                continue

            # Skip lines that look like system log entries (all remaining after stripping)
            if re.match(r'^\[[\w\s:+\-]+\]', line):
                continue

            # This is a meaningful text line — truncate and return
            if len(line) > 60:
                line = line[:60]
            return line

        return ""

    @staticmethod
    def _parse_dt(dt_str):
        from datetime import datetime
        if not dt_str:
            return None
        for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]:
            try:
                return datetime.strptime(dt_str, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(dt_str)
        except Exception:
            return None

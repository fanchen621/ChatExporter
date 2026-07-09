import json
import os
from typing import List, Optional

from .base import BaseAdapter
from ..models import AppInfo, Conversation, Message, MessagePart, MessagePartType, Role


class QoderWorkAdapter(BaseAdapter):
    name = "qoderwork"
    display_name = "QoderWork CN"

    def __init__(self):
        super().__init__()
        self.db_path = os.path.join(self.appdata_roaming, "QoderWork CN", "data", "agents.db")
        self._cached_conversations = None

    def detect(self) -> bool:
        return os.path.exists(self.db_path)

    def get_app_info(self) -> AppInfo:
        available = self.detect()
        return AppInfo(
            name=self.name,
            display_name=self.display_name,
            is_available=available,
            data_path=self.db_path if available else None,
            conversation_count=0,
        )

    def list_conversations(self) -> List[Conversation]:
        if self._cached_conversations is not None:
            return self._cached_conversations

        conversations: List[Conversation] = []
        if not self.detect():
            self._cached_conversations = conversations
            return conversations

        conn = None
        try:
            conn = self._connect_db(self.db_path)
            cursor = conn.cursor()
            if not self._table_exists(cursor, "chats"):
                return conversations

            cursor.execute("""
                SELECT
                    c.id,
                    c.name,
                    c.created_at,
                    c.updated_at,
                    c.chat_type,
                    COUNT(DISTINCT sc.id) AS sub_count,
                    COUNT(m.id) AS msg_count
                FROM chats c
                LEFT JOIN sub_chats sc ON c.id = sc.chat_id
                LEFT JOIN messages m ON sc.id = m.sub_chat_id
                WHERE c.deleted_at IS NULL
                GROUP BY c.id
                ORDER BY c.updated_at DESC
            """)

            for row in cursor.fetchall():
                conversations.append(Conversation(
                    id=row["id"],
                    title=row["name"] or "(无标题对话)",
                    created_at=self._ts_to_dt(row["created_at"], ms=False),
                    updated_at=self._ts_to_dt(row["updated_at"], ms=False),
                    source_app=self.display_name,
                    metadata={
                        "chat_type": row["chat_type"],
                        "msg_count": row["msg_count"] or 0,
                    },
                ))
        except Exception:
            conversations = []
        finally:
            if conn:
                conn.close()

        self._cached_conversations = conversations
        return conversations

    def get_conversation(self, conv_id: str) -> Optional[Conversation]:
        if not self.detect():
            return None

        conn = None
        try:
            conn = self._connect_db(self.db_path)
            cursor = conn.cursor()
            if not self._table_exists(cursor, "chats"):
                return None

            cursor.execute("""
                SELECT id, name, created_at, updated_at
                FROM chats
                WHERE id = ?
            """, (conv_id,))
            chat_row = cursor.fetchone()
            if not chat_row:
                return None

            messages = []
            if self._table_exists(cursor, "sub_chats") and self._table_exists(cursor, "messages"):
                cursor.execute("""
                    SELECT id, name, session_id, model_level, created_at
                    FROM sub_chats
                    WHERE chat_id = ?
                    ORDER BY created_at ASC
                """, (conv_id,))
                sub_chats = cursor.fetchall()

                for sub_chat in sub_chats:
                    cursor.execute("""
                        SELECT id, message_id, role, parts, created_at, metadata
                        FROM messages
                        WHERE sub_chat_id = ?
                        ORDER BY sequence ASC
                    """, (sub_chat["id"],))

                    for msg_row in cursor.fetchall():
                        msg = self._parse_message(msg_row, sub_chat["model_level"])
                        if msg:
                            messages.append(msg)

            return Conversation(
                id=chat_row["id"],
                title=chat_row["name"] or "(无标题对话)",
                created_at=self._ts_to_dt(chat_row["created_at"], ms=False),
                updated_at=self._ts_to_dt(chat_row["updated_at"], ms=False),
                messages=messages,
                source_app=self.display_name,
                metadata={"msg_count": len(messages)},
            )
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    @staticmethod
    def _table_exists(cursor, table_name: str) -> bool:
        try:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            return cursor.fetchone() is not None
        except Exception:
            return False

    def _parse_message(self, row, model_level: Optional[str]) -> Optional[Message]:
        role_str = (row["role"] or "").lower()
        role = {
            "user": Role.USER,
            "human": Role.USER,
            "assistant": Role.ASSISTANT,
            "ai": Role.ASSISTANT,
            "system": Role.SYSTEM,
            "tool": Role.TOOL,
        }.get(role_str, Role.USER)

        parts_data = []
        try:
            raw_parts = json.loads(row["parts"]) if row["parts"] else []
            if isinstance(raw_parts, list):
                parts_data = raw_parts
            elif isinstance(raw_parts, dict):
                parts_data = raw_parts.get("parts", []) if isinstance(raw_parts.get("parts"), list) else [raw_parts]
        except (json.JSONDecodeError, TypeError):
            pass

        parts = []
        text_content = ""

        for part in parts_data:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type", "")

            if ptype == "text":
                text = part.get("text", "")
                if text:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=text))
                    text_content += text
            elif ptype == "tool-Thinking":
                thinking = part.get("input", {}).get("text", "") if isinstance(part.get("input"), dict) else ""
                if thinking:
                    parts.append(MessagePart(type=MessagePartType.THINKING, content=thinking))
            elif ptype.startswith("tool-") and "CallId" in part:
                tool_name = part.get("toolName", ptype.replace("tool-", ""))
                tool_input = json.dumps(part.get("input", {}), ensure_ascii=False, indent=2)
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_CALL,
                    tool_name=tool_name,
                    tool_input=tool_input,
                ))
            elif ptype == "tool-result":
                tool_output = json.dumps(part.get("result", ""), ensure_ascii=False, indent=2)
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_RESULT,
                    tool_output=tool_output,
                ))
            elif ptype == "code":
                code = part.get("text", "")
                lang = part.get("language", "")
                parts.append(MessagePart(
                    type=MessagePartType.CODE,
                    content=code,
                    language=lang,
                ))
                text_content += f"\n```{lang}\n{code}\n```\n"

        token_usage = None
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            if "usage" in meta:
                token_usage = meta["usage"]
        except Exception:
            pass

        return Message(
            role=role,
            content=text_content,
            timestamp=self._ts_to_dt(row["created_at"], ms=False),
            message_id=row["message_id"],
            parts=parts,
            model=model_level,
            token_usage=token_usage,
        )

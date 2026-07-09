import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import List, Optional

from .base import BaseAdapter
from ..models import AppInfo, Conversation, Message, MessagePart, MessagePartType, Role


class MarvisAdapter(BaseAdapter):
    name = "marvis"
    display_name = "腾讯 Marvis"

    def __init__(self):
        super().__init__()
        self.base_dir = os.path.join(self.appdata_roaming, "Tencent", "Marvis", "User")
        self._db_path = None
        self._cached_conversations = None

    def _find_db(self) -> Optional[str]:
        if not os.path.exists(self.base_dir):
            return None
        best_db = None
        best_count = -1
        for user_dir in os.listdir(self.base_dir):
            user_path = os.path.join(self.base_dir, user_dir)
            db_path = os.path.join(user_path, "database", "data.db")
            if not os.path.exists(db_path):
                continue
            try:
                tmp_dir = tempfile.mkdtemp(prefix="marvis_check_")
                tmp_db = os.path.join(tmp_dir, "data.db")
                shutil.copy2(db_path, tmp_db)
                conn = sqlite3.connect(tmp_db)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM conversations")
                count = cursor.fetchone()[0]
                conn.close()
                try:
                    os.unlink(tmp_db)
                    os.rmdir(tmp_dir)
                except Exception:
                    pass
                if count > best_count:
                    best_count = count
                    best_db = db_path
            except Exception:
                if best_db is None:
                    best_db = db_path
        return best_db

    def detect(self) -> bool:
        if not os.path.exists(self.base_dir):
            return False
        for user_dir in os.listdir(self.base_dir):
            user_path = os.path.join(self.base_dir, user_dir)
            db_path = os.path.join(user_path, "database", "data.db")
            if os.path.exists(db_path):
                self._db_path = db_path
                return True
        return False

    def get_app_info(self) -> AppInfo:
        available = self.detect()
        return AppInfo(
            name=self.name,
            display_name=self.display_name,
            is_available=available,
            data_path=self._db_path if available else None,
            conversation_count=0
        )

    def list_conversations(self) -> List[Conversation]:
        if self._cached_conversations is not None:
            return self._cached_conversations

        if not self.detect():
            return []

        conn = self._connect_db(self._db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
            SELECT
                c.conversation_id,
                c.title,
                c.created_at,
                c.updated_at,
                c.status,
                c.metadata,
                COUNT(m.message_id) AS msg_count
            FROM conversations c
            LEFT JOIN messages m ON c.conversation_id = m.conversation_id
            GROUP BY c.conversation_id
            ORDER BY c.updated_at DESC
        """)
        except Exception:
            conn.close()
            return []

        conversations = []
        for row in cursor.fetchall():
            title = row["title"] or "(无标题对话)"

            conv = Conversation(
                id=row["conversation_id"],
                title=title,
                created_at=self._ts_to_dt(row["created_at"], ms=False),
                updated_at=self._ts_to_dt(row["updated_at"], ms=False),
                source_app=self.display_name,
                metadata={
                    "status": row["status"],
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

        conn = self._connect_db(self._db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT conversation_id, title, created_at, updated_at, status
            FROM conversations
            WHERE conversation_id = ?
        """, (conv_id,))
        conv_row = cursor.fetchone()
        if not conv_row:
            conn.close()
            return None

        cursor.execute("""
            SELECT message_id, conversation_id, role, content, tool_calls, created_at,
                   response_id, metadata, model_id
            FROM messages
            WHERE conversation_id = ?
            ORDER BY message_seq ASC, created_at ASC
        """, (conv_id,))

        messages = []
        for msg_row in cursor.fetchall():
            msg = self._parse_message(msg_row)
            if msg:
                messages.append(msg)

        conn.close()

        conv = Conversation(
            id=conv_row["conversation_id"],
            title=conv_row["title"] or "(无标题对话)",
            created_at=self._ts_to_dt(conv_row["created_at"], ms=False),
            updated_at=self._ts_to_dt(conv_row["updated_at"], ms=False),
            messages=messages,
            source_app=self.display_name
        )
        return conv

    def _parse_message(self, row) -> Optional[Message]:
        role_str = row["role"]
        try:
            role = Role(role_str)
        except ValueError:
            role = Role.USER

        parts = []
        content = row["content"] or ""

        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    text_parts = []
                    for item in parsed:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                        elif isinstance(item, str):
                            text_parts.append(item)
                    content = "\n".join(text_parts)
                elif isinstance(parsed, dict):
                    content = parsed.get("text", content)
            except (json.JSONDecodeError, TypeError):
                pass

        if content:
            parts.append(MessagePart(type=MessagePartType.TEXT, content=content))

        tool_calls = row["tool_calls"]
        if tool_calls:
            try:
                calls = json.loads(tool_calls) if isinstance(tool_calls, str) else tool_calls
                if isinstance(calls, list):
                    for call in calls:
                        if isinstance(call, dict):
                            parts.append(MessagePart(
                                type=MessagePartType.TOOL_CALL,
                                tool_name=call.get("name", "unknown"),
                                tool_input=json.dumps(call.get("arguments", call.get("input", {})), ensure_ascii=False, indent=2)
                            ))
            except Exception:
                pass

        token_usage = None
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            if "usage" in meta:
                token_usage = meta["usage"]
        except Exception:
            pass

        return Message(
            role=role,
            content=content,
            timestamp=self._ts_to_dt(row["created_at"], ms=False),
            message_id=row["message_id"],
            parts=parts,
            model=row["model_id"],
            token_usage=token_usage
        )

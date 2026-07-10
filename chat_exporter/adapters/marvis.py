import json
import os
import sqlite3
from typing import Dict, List, Optional, Set

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
        """在所有 Marvis 用户库中选择对话数最多的数据库。"""
        if not os.path.exists(self.base_dir):
            return None

        best_db = None
        best_count = -1
        fallback_db = None

        try:
            user_dirs = os.listdir(self.base_dir)
        except OSError:
            return None

        for user_dir in user_dirs:
            db_path = os.path.join(self.base_dir, user_dir, "database", "data.db")
            if not os.path.exists(db_path):
                continue
            if fallback_db is None:
                fallback_db = db_path

            conn = None
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
                conn.execute("PRAGMA busy_timeout=3000")
                cursor = conn.cursor()
                if not self._table_exists(cursor, "conversations"):
                    continue
                cursor.execute("SELECT COUNT(*) FROM conversations")
                count = int(cursor.fetchone()[0] or 0)
                prefer_real_user = (
                    count == best_count
                    and best_count >= 0
                    and "default_user" not in db_path
                    and (best_db is None or "default_user" in best_db)
                )
                if count > best_count or prefer_real_user:
                    best_count = count
                    best_db = db_path
            except Exception:
                continue
            finally:
                if conn:
                    conn.close()

        return best_db if best_db is not None else fallback_db

    def detect(self) -> bool:
        if not os.path.exists(self.base_dir):
            return False
        if not self._db_path or not os.path.exists(self._db_path):
            self._db_path = self._find_db()
        return self._db_path is not None

    def get_app_info(self) -> AppInfo:
        available = self.detect()
        return AppInfo(
            name=self.name,
            display_name=self.display_name,
            is_available=available,
            data_path=self._db_path if available else None,
            conversation_count=len(self._cached_conversations or []),
        )

    def list_conversations(self) -> List[Conversation]:
        if self._cached_conversations is not None:
            return self._cached_conversations
        if not self.detect():
            return []

        conversations: List[Conversation] = []
        conn = None
        try:
            conn = self._connect_db(self._db_path)
            cursor = conn.cursor()
            if not self._table_exists(cursor, "conversations"):
                return []

            has_messages = self._table_exists(cursor, "messages")
            count_expr = "COUNT(m.message_id)" if has_messages else "0"
            join_sql = "LEFT JOIN messages m ON c.conversation_id = m.conversation_id" if has_messages else ""
            cursor.execute(f"""
                SELECT
                    c.conversation_id,
                    c.title,
                    c.created_at,
                    c.updated_at,
                    c.status,
                    {count_expr} AS msg_count
                FROM conversations c
                {join_sql}
                GROUP BY c.conversation_id
                ORDER BY c.updated_at DESC
            """)

            for row in cursor.fetchall():
                try:
                    conversations.append(Conversation(
                        id=str(row["conversation_id"]),
                        title=row["title"] or "（无标题对话）",
                        created_at=self._ts_to_dt(row["created_at"], ms=False),
                        updated_at=self._ts_to_dt(row["updated_at"], ms=False),
                        source_app=self.display_name,
                        metadata={
                            "status": row["status"],
                            "msg_count": row["msg_count"] or 0,
                        },
                    ))
                except Exception:
                    continue
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
            conn = self._connect_db(self._db_path)
            cursor = conn.cursor()
            if not self._table_exists(cursor, "conversations"):
                return None

            cursor.execute("""
                SELECT conversation_id, title, created_at, updated_at, status
                FROM conversations
                WHERE conversation_id = ?
            """, (conv_id,))
            conv_row = cursor.fetchone()
            if not conv_row:
                return None

            messages: List[Message] = []
            if self._table_exists(cursor, "messages"):
                columns = self._table_columns(cursor, "messages")
                select_sql = self._message_select_sql(columns)
                order_sql = self._message_order_sql(columns)
                cursor.execute(
                    f"SELECT {select_sql} FROM messages WHERE conversation_id = ? ORDER BY {order_sql}",
                    (conv_id,),
                )
                for msg_row in cursor.fetchall():
                    msg = self._parse_message(msg_row)
                    if msg:
                        messages.append(msg)

            return Conversation(
                id=str(conv_row["conversation_id"]),
                title=conv_row["title"] or "（无标题对话）",
                created_at=self._ts_to_dt(conv_row["created_at"], ms=False),
                updated_at=self._ts_to_dt(conv_row["updated_at"], ms=False),
                messages=messages,
                source_app=self.display_name,
                metadata={"status": conv_row["status"], "msg_count": len(messages)},
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

    @staticmethod
    def _table_columns(cursor, table_name: str) -> Set[str]:
        try:
            cursor.execute(f"PRAGMA table_info({table_name})")
            return {str(row[1]) for row in cursor.fetchall()}
        except Exception:
            return set()

    @staticmethod
    def _message_select_sql(columns: Set[str]) -> str:
        required = ["message_id", "conversation_id", "role", "content", "created_at"]
        optional = ["tool_calls", "response_id", "metadata", "model_id", "message_seq"]
        parts = []
        for name in required + optional:
            if name in columns:
                parts.append(name)
            else:
                parts.append(f"NULL AS {name}")
        return ", ".join(parts)

    @staticmethod
    def _message_order_sql(columns: Set[str]) -> str:
        if "message_seq" in columns and "created_at" in columns:
            return "message_seq ASC, created_at ASC"
        if "created_at" in columns:
            return "created_at ASC"
        if "message_id" in columns:
            return "message_id ASC"
        return "rowid ASC"

    def _parse_message(self, row) -> Optional[Message]:
        role_str = (row["role"] or "").lower()
        role = {
            "user": Role.USER,
            "human": Role.USER,
            "assistant": Role.ASSISTANT,
            "ai": Role.ASSISTANT,
            "system": Role.SYSTEM,
            "tool": Role.TOOL,
        }.get(role_str, Role.USER)

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
                    content = parsed.get("text", parsed.get("content", content))
            except (json.JSONDecodeError, TypeError):
                pass

        if content:
            parts.append(MessagePart(type=MessagePartType.TEXT, content=str(content)))

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
                                tool_input=json.dumps(
                                    call.get("arguments", call.get("input", {})),
                                    ensure_ascii=False,
                                    indent=2,
                                ),
                            ))
            except Exception:
                pass

        token_usage = None
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            if isinstance(meta, dict) and "usage" in meta:
                token_usage = meta["usage"]
        except Exception:
            pass

        return Message(
            role=role,
            content=str(content),
            timestamp=self._ts_to_dt(row["created_at"], ms=False),
            message_id=str(row["message_id"]),
            parts=parts,
            model=row["model_id"] or None,
            token_usage=token_usage,
        )

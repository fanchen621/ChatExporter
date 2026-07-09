import json
import os
import sqlite3
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
        """在所有 Marvis 用户库里选对话数最多的那个。

        直接以只读方式打开真实路径（带 busy_timeout），避免在 Marvis 运行占用文件时
        用 shutil.copy2 拷贝失败、从而把真正有数据的账号库静默丢弃、只剩空占位库
        (default_user) 导致界面显示 0 个对话的问题。
        """
        if not os.path.exists(self.base_dir):
            return None
        best_db = None          # 对话数最多的库
        best_count = -1
        fallback_db = None      # 当且仅当所有库都打不开时的兜底
        for user_dir in os.listdir(self.base_dir):
            user_path = os.path.join(self.base_dir, user_dir)
            db_path = os.path.join(user_path, "database", "data.db")
            if not os.path.exists(db_path):
                continue
            try:
                conn = sqlite3.connect(
                    f"file:{db_path}?mode=ro", uri=True, timeout=8
                )
                conn.execute("PRAGMA busy_timeout=8000")
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM conversations")
                count = cursor.fetchone()[0]
                conn.close()
                # 对话数相同时，优先选非 default_user 的账号库
                if count > best_count or (
                    count == best_count and best_count >= 0
                    and "default_user" not in db_path
                    and (best_db is None or "default_user" in best_db)
                ):
                    best_count = count
                    best_db = db_path
                if fallback_db is None:
                    fallback_db = db_path
            except Exception:
                # 打不开/被锁：不要让它覆盖已找到的有数据库
                if fallback_db is None:
                    fallback_db = db_path
                continue
        return best_db if best_db is not None else fallback_db

    def detect(self) -> bool:
        if not os.path.exists(self.base_dir):
            return False
        self._db_path = self._find_db()
        return self._db_path is not None

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
            data_path=self._db_path if available else None,
            conversation_count=conv_count
        )

    def list_conversations(self) -> List[Conversation]:
        if self._cached_conversations is not None:
            return self._cached_conversations

        if not self.detect():
            return []

        conn = None
        try:
            conn = self._connect_db(self._db_path)
            cursor = conn.cursor()

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

            conversations = []
            for row in cursor.fetchall():
                # 容错：单行解析失败不影响其他对话展示
                try:
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
                except Exception:
                    continue
        except Exception:
            return []
        finally:
            if conn:
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

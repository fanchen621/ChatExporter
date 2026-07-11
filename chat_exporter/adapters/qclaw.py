import json
import os
import re
from typing import Dict, List, Optional

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
        return AppInfo(
            name=self.name,
            display_name=self.display_name,
            is_available=available,
            data_path=self.db_path if available else None,
            conversation_count=0
        )

    def list_conversations(self) -> List[Conversation]:
        if self._cached_conversations is not None:
            return self._cached_conversations

        if not self.detect():
            return []

        conn = None
        conversations: List[Conversation] = []
        try:
            conn = self._connect_db(self.db_path)
            cursor = conn.cursor()
            if not self._table_exists(cursor, "conversations") or not self._table_exists(cursor, "messages"):
                return []

            # 只取标题候选的前 500 字符，避免 QClaw 大消息在列表阶段拖垮 UI。
            cursor.execute("""
                SELECT
                    c.conversation_id,
                    c.session_id,
                    c.title,
                    c.created_at,
                    c.updated_at,
                    c.archived_at,
                    COUNT(m.message_id) AS msg_count,
                    (SELECT substr(content, 1, 500) FROM messages
                     WHERE conversation_id = c.conversation_id AND role = 'user'
                     ORDER BY seq ASC LIMIT 1) AS first_user_msg,
                    (SELECT substr(content, 1, 500) FROM messages
                     WHERE conversation_id = c.conversation_id
                     ORDER BY seq ASC LIMIT 1) AS first_msg
                FROM conversations c
                LEFT JOIN messages m ON c.conversation_id = m.conversation_id
                GROUP BY c.conversation_id
                ORDER BY c.updated_at DESC
            """)

            for row in cursor.fetchall():
                msg_count = row["msg_count"] or 0
                raw_candidates = [row["title"] or "", row["first_user_msg"] or "", row["first_msg"] or ""]

                # QClaw 的 lossless memory 里会混入内部 dream diary / 控制 UI 记录。
                # 小体量内部记录对导出价值很低，列表中隐藏，减少“假对话”。
                if msg_count <= 3 and self._looks_like_internal_memory("\n".join(raw_candidates)):
                    continue

                title = ""
                for raw in raw_candidates:
                    title = self._clean_title(raw)
                    if title:
                        break
                if not title and row["session_id"]:
                    title = f"对话 {str(row['session_id'])[:8]}..."
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
                        "msg_count": msg_count,
                    }
                )
                conversations.append(conv)
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

            cursor.execute("""
                SELECT conversation_id, session_id, title, created_at, updated_at
                FROM conversations
                WHERE conversation_id = ?
            """, (self._coerce_conv_id(conv_id),))
            conv_row = cursor.fetchone()
            if not conv_row:
                return None

            cursor.execute("""
                SELECT message_id, seq, role, content, created_at, token_count
                FROM messages
                WHERE conversation_id = ?
                ORDER BY seq ASC
            """, (self._coerce_conv_id(conv_id),))
            msg_rows = cursor.fetchall()

            parts_by_message = self._fetch_message_parts(cursor, [row["message_id"] for row in msg_rows])

            messages = []
            for msg_row in msg_rows:
                msg = self._parse_message(msg_row, parts_by_message.get(msg_row["message_id"], []))
                if msg:
                    messages.append(msg)

            title = self._clean_title(conv_row["title"] or "")
            if not title and messages:
                title = self._clean_title(messages[0].content or "")
            if not title and conv_row["session_id"]:
                title = f"对话 {str(conv_row['session_id'])[:8]}..."
            if not title:
                title = "(无标题对话)"

            conv = Conversation(
                id=str(conv_row["conversation_id"]),
                title=title,
                created_at=self._parse_dt(conv_row["created_at"]),
                updated_at=self._parse_dt(conv_row["updated_at"]),
                messages=messages,
                source_app=self.display_name,
                metadata={"session_id": conv_row["session_id"], "msg_count": len(messages)}
            )
            return conv
        except Exception:
            return None
        finally:
            if conn:
                conn.close()

    @staticmethod
    def _coerce_conv_id(conv_id: str):
        try:
            return int(conv_id)
        except (TypeError, ValueError):
            return conv_id

    @staticmethod
    def _table_exists(cursor, table_name: str) -> bool:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        return cursor.fetchone() is not None

    def _fetch_message_parts(self, cursor, message_ids) -> Dict[object, List[object]]:
        if not message_ids or not self._table_exists(cursor, "message_parts"):
            return {}

        parts_by_message: Dict[object, List[object]] = {mid: [] for mid in message_ids}
        chunk_size = 500
        for start in range(0, len(message_ids), chunk_size):
            chunk = message_ids[start:start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            cursor.execute(f"""
                SELECT message_id, part_type, text_content, tool_name, tool_input, tool_output,
                       tool_error, file_name, ordinal, metadata
                FROM message_parts
                WHERE message_id IN ({placeholders})
                ORDER BY message_id, ordinal ASC
            """, chunk)
            for row in cursor.fetchall():
                parts_by_message.setdefault(row["message_id"], []).append(row)
        return parts_by_message

    def _parse_message(self, msg_row, part_rows):
        role_str = (msg_row["role"] or "").lower()
        role = self._parse_role(role_str)

        parts = []
        text_content = msg_row["content"] or ""

        for prow in part_rows:
            ptype = (prow["part_type"] or "").lower()
            txt = prow["text_content"] or ""

            if ptype == "text":
                if txt:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=txt))
            elif ptype == "tool_call":
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_CALL,
                    tool_name=prow["tool_name"],
                    tool_input=prow["tool_input"] or txt or "",
                    content=txt
                ))
            elif ptype == "tool_result":
                output = prow["tool_output"] or prow["tool_error"] or txt or ""
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_RESULT,
                    tool_name=prow["tool_name"],
                    tool_output=output,
                    content=output
                ))
            elif ptype == "file":
                parts.append(MessagePart(
                    type=MessagePartType.FILE,
                    file_name=prow["file_name"],
                    content=txt
                ))
            elif ptype in ("thinking", "reasoning"):
                parts.append(MessagePart(type=MessagePartType.THINKING, content=txt))
            elif ptype == "code":
                parts.append(MessagePart(type=MessagePartType.CODE, content=txt))
            else:
                if txt:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=txt))

        # 有些版本没有 message_parts 或 parts 不完整，至少保留 messages.content。
        if text_content and not any(p.type == MessagePartType.TEXT and p.content == text_content for p in parts):
            parts.insert(0, MessagePart(type=MessagePartType.TEXT, content=text_content))

        return Message(
            role=role,
            content=text_content,
            timestamp=self._parse_dt(msg_row["created_at"]),
            message_id=str(msg_row["message_id"]),
            parts=parts,
            token_usage={"total_tokens": msg_row["token_count"]} if msg_row["token_count"] else None
        )

    @staticmethod
    def _parse_role(role_str: str) -> Role:
        aliases = {
            "human": Role.USER,
            "user": Role.USER,
            "assistant": Role.ASSISTANT,
            "ai": Role.ASSISTANT,
            "agent": Role.ASSISTANT,
            "tool": Role.TOOL,
            "system": Role.SYSTEM,
        }
        return aliases.get(role_str, Role.SYSTEM if role_str else Role.USER)

    @staticmethod
    def _looks_like_internal_memory(raw: str) -> bool:
        if not raw:
            return False
        lower = raw.lower()
        patterns = [
            "write a dream diary entry from",
            '"label": "openclaw-control-ui"',
            "openclaw-control-ui",
            "dream diary entry",
        ]
        return any(p in lower for p in patterns)

    @staticmethod
    def _clean_title(raw: str) -> str:
        """Clean a raw message string to produce a usable conversation title."""
        if not raw:
            return ""

        for line in str(raw).splitlines():
            line = line.strip().strip(",")
            if not line:
                continue

            # Strip markdown code fences
            if line.startswith("```"):
                continue

            # Strip JSON/metadata prefixes like "Sender (untrusted metadata):", "Sender:", etc.
            line = re.sub(r'^Sender\s*(?:\([^)]*\))?\s*:\s*', '', line)

            # Strip timestamp prefixes like "[Wed 2026-06-24 23:06 GMT+8]"
            line = re.sub(r'^\[[\w\s:+\-]+\]\s*', '', line)
            line = line.strip().strip(",")
            if not line:
                continue

            lower = line.lower()

            # Skip pure JSON / JSON fragments / internal metadata titles.
            if line.startswith(("{", "[", "}", "]")):
                continue
            if re.match(r'^"?[\w.-]+"?\s*:', line):
                continue
            if lower in {"openclaw-control-ui", "qclaw", "memory", "lossless"}:
                continue
            if "write a dream diary entry from" in lower:
                continue

            # Skip lines that look like system log entries
            if re.match(r'^\[[\w\s:+\-]+\]', line):
                continue

            if len(line) > 60:
                line = line[:60]
            return line

        return ""

    @staticmethod
    def _parse_dt(value):
        from datetime import datetime
        if value is None or value == "":
            return None
        if isinstance(value, (int, float)):
            # 兼容秒级/毫秒级时间戳
            try:
                return datetime.fromtimestamp(value / 1000 if value > 10_000_000_000 else value)
            except Exception:
                return None
        value = str(value)
        for fmt in ["%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]:
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None

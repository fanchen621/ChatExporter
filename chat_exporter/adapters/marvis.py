import json
import os
import sqlite3
from typing import Dict, List, Optional, Set

from .base import BaseAdapter
from ..models import AppInfo, Conversation, Message, MessagePart, MessagePartType, Role
from ..preview_utils import role_from_hint


class MarvisAdapter(BaseAdapter):
    name = "marvis"
    display_name = "腾讯 Marvis"

    # 命名空间分隔符：不同账号库里的 conversation_id 会重号，
    # 导出侧必须能区分，否则 A 账号的对话会被 B 账号的同号对话顶掉。
    ID_SEPARATOR = "::"

    def __init__(self):
        super().__init__()
        self.base_dir = os.path.join(self.appdata_roaming, "Tencent", "Marvis", "User")
        self._db_path = None
        self._db_paths: List[str] = []
        self._cached_conversations = None
        self.last_error: Optional[str] = None

    def _find_dbs(self) -> List[str]:
        """列出全部 Marvis 账号库（每个 User/<uid>/database/data.db 一个）。

        旧实现只挑“对话数最多”的那一个库，其余账号的对话在 ChatExporter 里
        既看不见也导不出——这是静默丢数据，不是筛选。这里返回全部库，排序
        只影响展示顺序（对话多的在前，同数时真实账号优先于空壳 default_user）。
        """
        if not os.path.exists(self.base_dir):
            return []

        try:
            user_dirs = sorted(os.listdir(self.base_dir))
        except OSError:
            return []

        scored = []
        for user_dir in user_dirs:
            db_path = os.path.join(self.base_dir, user_dir, "database", "data.db")
            if not os.path.exists(db_path):
                continue

            count = -1
            conn = None
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=3)
                conn.execute("PRAGMA busy_timeout=3000")
                cursor = conn.cursor()
                if self._table_exists(cursor, "conversations"):
                    cursor.execute("SELECT COUNT(*) FROM conversations")
                    count = int(cursor.fetchone()[0] or 0)
            except Exception:
                # 打不开（正在写入 / 临时锁）也保留：读取阶段再试一次，
                # 总比这一轮直接把整个账号丢掉强。
                count = -1
            finally:
                if conn:
                    conn.close()

            scored.append((count, user_dir, db_path))

        scored.sort(key=lambda item: (-item[0], "default_user" in item[2], item[1]))
        return [db_path for _count, _user_dir, db_path in scored]

    def _find_db(self) -> Optional[str]:
        """保留旧接口语义：返回首选库（排序后的第一个）。"""
        dbs = self._find_dbs()
        return dbs[0] if dbs else None

    @classmethod
    def _db_key(cls, db_path: str) -> str:
        """用账号目录名当库标识（User/<uid>/database/data.db → <uid>）。"""
        return os.path.basename(os.path.dirname(os.path.dirname(db_path))) or "db"

    @classmethod
    def _namespaced_id(cls, db_path: str, conv_id) -> str:
        return f"{cls._db_key(db_path)}{cls.ID_SEPARATOR}{conv_id}"

    @classmethod
    def _split_conv_id(cls, conv_id: str):
        """拆出 (账号标识, 原始 conversation_id)；裸 id 返回 (None, id)。"""
        raw = str(conv_id)
        if cls.ID_SEPARATOR in raw:
            key, _, rest = raw.partition(cls.ID_SEPARATOR)
            return key, rest
        return None, raw

    def detect(self) -> bool:
        if not os.path.exists(self.base_dir):
            return False
        if not self._db_paths or not all(os.path.exists(p) for p in self._db_paths):
            self._db_paths = self._find_dbs()
            self._db_path = self._db_paths[0] if self._db_paths else None
        return bool(self._db_paths)

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
        # 逐库读取：单个库读失败不能连累其他账号（旧实现一异常就整体清空）。
        for db_path in self._db_paths:
            conversations.extend(self._list_conversations_in_db(db_path))

        self._cached_conversations = conversations
        return conversations

    def _list_conversations_in_db(self, db_path: str) -> List[Conversation]:
        conversations: List[Conversation] = []
        conn = None
        try:
            conn = self._connect_db(db_path)
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
                        id=self._namespaced_id(db_path, row["conversation_id"]),
                        title=row["title"] or "（无标题对话）",
                        created_at=self._ts_to_dt(row["created_at"], ms=False),
                        updated_at=self._ts_to_dt(row["updated_at"], ms=False),
                        source_app=self.display_name,
                        metadata={
                            "status": row["status"],
                            "msg_count": row["msg_count"] or 0,
                            "db_path": db_path,
                            "account": self._db_key(db_path),
                            "raw_conversation_id": str(row["conversation_id"]),
                        },
                    ))
                except Exception:
                    continue
        except Exception as exc:
            self.last_error = f"{os.path.basename(os.path.dirname(os.path.dirname(db_path)))}: {exc}"
            conversations = []
        finally:
            if conn:
                conn.close()
        return conversations

    def get_conversation(self, conv_id: str) -> Optional[Conversation]:
        if not self.detect():
            return None

        key, raw_id = self._split_conv_id(conv_id)
        # 带命名空间的 id 直查对应账号库；裸 id（旧数据 / 外部调用）全库回退查找。
        preferred = [p for p in self._db_paths if key and self._db_key(p) == key]
        others = [p for p in self._db_paths if p not in preferred]
        for db_path in preferred + others:
            conv = self._load_conversation(db_path, raw_id)
            if conv is not None:
                return conv
        return None

    def _load_conversation(self, db_path: str, conv_id: str) -> Optional[Conversation]:
        conn = None
        try:
            conn = self._connect_db(db_path)
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
                id=self._namespaced_id(db_path, conv_row["conversation_id"]),
                title=conv_row["title"] or "（无标题对话）",
                created_at=self._ts_to_dt(conv_row["created_at"], ms=False),
                updated_at=self._ts_to_dt(conv_row["updated_at"], ms=False),
                messages=messages,
                source_app=self.display_name,
                metadata={
                    "status": conv_row["status"],
                    "msg_count": len(messages),
                    "db_path": db_path,
                    "account": self._db_key(db_path),
                    "raw_conversation_id": str(conv_row["conversation_id"]),
                },
            )
        except Exception as exc:
            # 单库读失败不代表这条对话不存在：记下原因，继续查其他账号库。
            self.last_error = f"{self._db_key(db_path)}: {exc}"
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

    # 文本可能出现的字段名（不同版本 / 不同 item 形状各写各的）
    _TEXT_FIELDS = ("text", "content", "value", "reasoning", "reasoning_content", "thinking", "output")

    @classmethod
    def _flatten_content_items(cls, items) -> str:
        """把 JSON 数组正文摊平成可读文本。

        旧实现只认 type=='text'，input_text / output_text / thinking / 纯文本
        字段等同级形状会被整条过滤成空正文——正文没了，用户还以为这条消息
        本来就是空的。这里按字段而非按 type 白名单取文本，认不出的形状原样
        保留 JSON，宁可多显示也不静默丢正文。
        """
        texts = []
        for item in items:
            if isinstance(item, str):
                texts.append(item)
                continue
            if not isinstance(item, dict):
                texts.append(str(item))
                continue

            itype = str(item.get("type", "")).casefold().replace("_", "-")
            value = None
            has_text_field = False
            for field in cls._TEXT_FIELDS:
                if field in item:
                    has_text_field = True
                    if item.get(field) not in (None, "", [], {}):
                        value = item[field]
                        break
            if isinstance(value, list):
                value = cls._flatten_content_items(value)
            elif isinstance(value, dict):
                value = cls._flatten_content_items([value])

            if value not in (None, ""):
                texts.append(str(value))
            elif "image" in itype:
                name = (
                    item.get("original_filename") or item.get("name")
                    or item.get("image_url") or item.get("image") or "image"
                )
                texts.append(f"[图片: {name}]")
            elif "file" in itype or "document" in itype:
                texts.append(f"[文件: {item.get('original_filename') or item.get('name') or 'file'}]")
            elif has_text_field:
                # 来源明确写了空文本，跳过即可（不是识别失败）
                continue
            else:
                try:
                    texts.append(json.dumps(item, ensure_ascii=False))
                except (TypeError, ValueError):
                    texts.append(str(item))
        return "\n".join(t for t in texts if t)

    def _parse_message(self, row) -> Optional[Message]:
        role_str = (row["role"] or "").lower()
        role = {
            "user": Role.USER,
            "human": Role.USER,
            "assistant": Role.ASSISTANT,
            "ai": Role.ASSISTANT,
            "system": Role.SYSTEM,
            "tool": Role.TOOL,
        }.get(role_str)
        if role is None:
            # 陌生角色名先问 role_from_hint（assistant_message / agent-output …），
            # 直接兜底成 USER 会把 AI 回复标成用户提问。
            role = role_from_hint(role_str) or Role.USER

        parts = []
        content = row["content"] or ""
        if isinstance(content, str):
            raw_content = content
            try:
                parsed = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            if isinstance(parsed, list):
                flattened = self._flatten_content_items(parsed)
                # 源数据非空却摊平成空 → 形状没认出来，退回原始文本，绝不吐空正文
                content = flattened if (flattened or not parsed) else raw_content
            elif isinstance(parsed, dict):
                content = self._flatten_content_items([parsed]) or raw_content

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
        metadata = {"raw_role": role_str}
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            if isinstance(meta, dict):
                metadata.update(meta)
                # Marvis 实机写的是 token_usage，旧代码只读 usage → 用量恒为空
                token_usage = self._normalize_token_usage(
                    meta.get("usage", meta.get("token_usage"))
                )
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
            metadata=metadata,
        )

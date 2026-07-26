import json
import os
from typing import List, Optional

from .base import BaseAdapter
from ..models import AppInfo, Conversation, Message, MessagePart, MessagePartType, Role
from ..preview_utils import role_from_hint


class QoderWorkAdapter(BaseAdapter):
    name = "qoderwork"
    display_name = "QoderWork CN"

    def __init__(self):
        super().__init__()
        self.db_path = os.path.join(self.appdata_roaming, "QoderWork CN", "data", "agents.db")
        self._cached_conversations = None
        # 最近一次读取失败的原因，供 GUI 在“读不出来”时显示具体诊断
        self.last_error: Optional[str] = None

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
        self.last_error = None
        if not self.detect():
            return None

        conn = None
        try:
            conn = self._connect_db(self.db_path)
            cursor = conn.cursor()
            if not self._table_exists(cursor, "chats"):
                # 库在、表没了 = schema 变动，不是“这条对话不存在”，必须报出来
                raise RuntimeError(f"QoderWork 数据库缺少 chats 表：{self.db_path}")

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
        except Exception as exc:
            # None 只保留给“这条对话确实不存在”（chats 里查不到这行）。
            # schema 变动 / sqlite 被锁这类真实故障过去也被压成 None，
            # 调用方只能显示“找不到对话”，用户永远看不到真正的原因。
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
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
    def _stringify_part_value(value) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(value)

    @classmethod
    def _tool_outputs(cls, part: dict) -> List[str]:
        """取出工具输出。

        QoderWork 真机数据里 output 与 result 几乎总是同值（993 个 tool part
        全部相等），去重后只留一份；两者不一致时都保留，宁可多写也不丢。
        output-error 状态只有 errorText，同样算输出。
        """
        values: List[str] = []
        for key in ("output", "result", "errorText", "error"):
            raw = part.get(key)
            if raw in (None, "", {}, []):
                continue
            text = cls._stringify_part_value(raw)
            if text and text not in values:
                values.append(text)
        return values

    def _parse_message(self, row, model_level: Optional[str]) -> Optional[Message]:
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
        text_parts_list = []

        for part in parts_data:
            if not isinstance(part, dict):
                continue

            ptype = str(part.get("type", ""))
            ptype_normalized = ptype.casefold().replace("_", "-")

            if ptype_normalized in ("text", "input-text", "output-text"):
                text = self._stringify_part_value(part.get("text", part.get("content", "")))
                if text:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=text))
                    text_parts_list.append(text)
            elif ptype_normalized in ("tool-thinking", "thinking", "reasoning"):
                raw_input = part.get("input", {})
                if isinstance(raw_input, dict):
                    thinking_value = raw_input.get("text", raw_input.get("content", ""))
                else:
                    thinking_value = raw_input
                thinking = self._stringify_part_value(
                    thinking_value or part.get("text", part.get("content", ""))
                )
                if thinking:
                    parts.append(MessagePart(type=MessagePartType.THINKING, content=thinking))
            # Must be checked before the generic tool-* branch: result parts often
            # also carry toolCallId and were previously misclassified as calls.
            elif ptype_normalized in ("tool-result", "toolresult", "function-result"):
                raw_output = part.get(
                    "result",
                    part.get("output", part.get("content", part.get("error", ""))),
                )
                tool_output = self._stringify_part_value(raw_output)
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_RESULT,
                    tool_name=part.get("toolName") or part.get("name"),
                    tool_output=tool_output,
                    content=tool_output,
                ))
            # AI SDK 的 tool-invocation 形状：调用参数和结果都嵌在 toolInvocation
            # 里，外层没有 toolCallId，旧实现整块丢弃——工具调用和输出一起消失。
            elif ptype_normalized in ("tool-invocation", "toolinvocation"):
                invocation = part.get("toolInvocation", part.get("tool_invocation"))
                if not isinstance(invocation, dict):
                    invocation = part
                tool_name = (
                    invocation.get("toolName") or invocation.get("name")
                    or part.get("toolName") or part.get("name") or "unknown"
                )
                tool_input = self._stringify_part_value(
                    invocation.get("args", invocation.get("input", invocation.get("arguments", {})))
                )
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_CALL,
                    tool_name=tool_name,
                    tool_input=tool_input,
                ))
                for tool_output in self._tool_outputs(invocation):
                    parts.append(MessagePart(
                        type=MessagePartType.TOOL_RESULT,
                        tool_name=tool_name,
                        tool_output=tool_output,
                        content=tool_output,
                    ))
            elif ptype_normalized.startswith("tool-") and (
                "toolCallId" in part or "tool_call_id" in part or "callId" in part
                or "toolName" in part or "name" in part
            ):
                tool_name = part.get("toolName") or part.get("name") or ptype.replace("tool-", "")
                tool_input = self._stringify_part_value(part.get("input", part.get("arguments", {})))
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_CALL,
                    tool_name=tool_name,
                    tool_input=tool_input,
                ))
                # 工具输出过去被整段丢弃：本机 QoderWork 库里 993 个 tool part、
                # 285 万字符的 output/result 一个字都没进导出。
                for tool_output in self._tool_outputs(part):
                    parts.append(MessagePart(
                        type=MessagePartType.TOOL_RESULT,
                        tool_name=tool_name,
                        tool_output=tool_output,
                        content=tool_output,
                    ))
            elif ptype_normalized == "code":
                code = self._stringify_part_value(part.get("text", part.get("content", "")))
                lang = str(part.get("language", "") or "")
                if code:
                    parts.append(MessagePart(
                        type=MessagePartType.CODE,
                        content=code,
                        language=lang,
                    ))
            else:
                # Fail open for new QoderWork part types. Unknown parts often
                # contain the final delivery in text/content/output fields; silently
                # dropping them is worse than preserving them as readable text.
                fallback = self._stringify_part_value(
                    part.get("text", part.get("content", part.get("output", "")))
                )
                if fallback:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=fallback))
                    text_parts_list.append(fallback)

        text_content = "\n".join(text_parts_list) if text_parts_list else ""

        token_usage = None
        metadata = {"raw_role": role_str}
        try:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            if isinstance(meta, dict):
                metadata.update(meta)
                if "usage" in meta or "token_usage" in meta:
                    # 形状必须收口：markdown_exporter 直接 .get()，
                    # 来源给出非 dict 会让整批导出崩在这一行。
                    token_usage = self._normalize_token_usage(
                        meta.get("usage", meta.get("token_usage"))
                    )
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
            metadata=metadata,
        )

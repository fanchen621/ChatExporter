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
        self.last_error = None
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
        except Exception as exc:
            # None 只保留给“这条对话确实不存在”。schema 变动、数据库被锁这类
            # 真实故障过去也被压成 None，调用方只能显示“找不到对话”，
            # 用户永远看不到真正的原因。现在把异常抛给调用方（GUI 的预览/
            # 批量导出两条路径都已 try/except 并展示错误文案）。
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
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
        db_content = msg_row["content"] or ""

        for prow in part_rows:
            ptype = (prow["part_type"] or "").lower()
            txt = prow["text_content"] or ""

            if ptype == "text":
                if txt:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=txt))
            elif ptype in ("tool_call", "tool"):
                # 实测本机库里调用行的 part_type 是 'tool'（14k+ 行）而不是
                # 'tool_call'——旧代码把它们全落进 else 分支静默丢弃，
                # 工具调用连同输入参数整个不进导出。两种拼法都接。
                if prow["tool_input"]:
                    parts.append(MessagePart(
                        type=MessagePartType.TOOL_CALL,
                        tool_name=prow["tool_name"],
                        tool_input=prow["tool_input"],
                        content=txt
                    ))
                elif txt:
                    # tool_input 为空、只有 text_content 的 'tool' 行（实测 42 条，
                    # 全挂在 role=tool 消息上，内容是结果占位文本）是结果不是调用，
                    # 标成 TOOL_CALL 会把输出当"调用输入"展示。
                    parts.append(MessagePart(
                        type=MessagePartType.TOOL_RESULT,
                        tool_name=prow["tool_name"],
                        tool_output=txt,
                        content=txt
                    ))
                combined_output = prow["tool_output"] or prow["tool_error"] or ""
                if combined_output:
                    parts.append(MessagePart(
                        type=MessagePartType.TOOL_RESULT,
                        tool_name=prow["tool_name"],
                        tool_output=combined_output,
                        content=combined_output
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
            # 注意：不要把 'compaction'（压缩摘要，挂在 role=system 上）映射成
            # THINKING——SYSTEM+THINKING 会被 effective_role 提升为 ASSISTANT，
            # 实测 60 条压缩日志以"AI 助手"身份混进阅读视图。走 else 保持 TEXT，
            # system 消息在预览里天然隐藏，content 列的原文一个字不丢。
            elif ptype == "code":
                parts.append(MessagePart(type=MessagePartType.CODE, content=txt))
            else:
                if txt:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=txt))

        # content 统一为 parts 中 TEXT parts 的换行连接。
        # 若 parts 中没有 TEXT part 但数据库 content 有值，则用 content 补齐。
        # 实测 420 行 content 存的是内容块 JSON 数组（[{"type":"thinking",...}]），
        # 直接回填成 TEXT 会让原始 JSON 裸奔进预览——先尝试展开成结构化 part。
        text_parts = [p.content for p in parts if p.type == MessagePartType.TEXT and p.content]
        if text_parts:
            content = "\n".join(text_parts)
        elif db_content:
            expanded = self._expand_content_blocks(db_content)
            if expanded is not None:
                parts.extend(expanded)
                expanded_text = [
                    p.content for p in expanded
                    if p.type == MessagePartType.TEXT and p.content
                ]
                content = "\n".join(expanded_text)
            else:
                parts.insert(0, MessagePart(type=MessagePartType.TEXT, content=db_content))
                content = db_content
        else:
            content = ""

        return Message(
            role=role,
            content=content,
            timestamp=self._parse_dt(msg_row["created_at"]),
            message_id=str(msg_row["message_id"]),
            parts=parts,
            token_usage={"total_tokens": msg_row["token_count"]} if msg_row["token_count"] else None
        )

    @staticmethod
    def _expand_content_blocks(raw: str):
        """把内容块 JSON 数组展开成结构化 part；解析不动就返回 None 原样回退。

        实测块形态：thinking（字段 thinking）、toolCall（name/arguments）、
        text（字段 text）。遇到未知块类型整体放弃展开——宁可显示原始 JSON
        也不静默丢块。
        """
        if not raw or not raw.lstrip().startswith("[{"):
            return None
        try:
            blocks = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(blocks, list):
            return None
        parts = []
        for block in blocks:
            if not isinstance(block, dict):
                return None
            btype = str(block.get("type") or "").lower()
            if btype == "text":
                value = block.get("text") or ""
                if value:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=value))
            elif btype in ("thinking", "reasoning"):
                value = block.get("thinking") or block.get("text") or ""
                if value:
                    parts.append(MessagePart(type=MessagePartType.THINKING, content=value))
            elif btype in ("toolcall", "tool_use", "tool_call"):
                arguments = block.get("arguments") if block.get("arguments") is not None else block.get("input")
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_CALL,
                    tool_name=str(block.get("name") or "") or None,
                    tool_input=arguments if isinstance(arguments, str)
                    else json.dumps(arguments, ensure_ascii=False) if arguments is not None else "",
                ))
            elif btype in ("toolresult", "tool_result"):
                value = block.get("content")
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_RESULT,
                    tool_name=str(block.get("name") or "") or None,
                    tool_output=text,
                    content=text,
                ))
            else:
                return None
        # 空列表 ≠ 解析失败：[{"type":"text","text":""}] 是合法的空消息，
        # 返回 None 会让原始 JSON 裸奔回预览。
        return parts

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

import json
import os
import re
from pathlib import Path
from typing import List, Optional

from .base import BaseAdapter
from ..models import AppInfo, Conversation, Message, MessagePart, MessagePartType, Role


class WorkBuddyAdapter(BaseAdapter):
    name = "workbuddy"
    display_name = "WorkBuddy"

    def __init__(self):
        super().__init__()
        self._user_id = None
        self._projects_dir = None
        self._db_path = None
        self._cached_conversations = None
        self._find_paths()

    def _find_paths(self):
        users_dir = os.path.join(self.program_data, "WorkBuddy", "users")
        if not os.path.exists(users_dir):
            return

        for uid in os.listdir(users_dir):
            wb_dir = os.path.join(users_dir, uid, ".workbuddy")
            db_path = os.path.join(wb_dir, "workbuddy.db")
            projects_dir = os.path.join(wb_dir, "projects")
            if os.path.exists(db_path):
                self._user_id = uid
                self._db_path = db_path
                self._projects_dir = projects_dir
                return

    def _cwd_to_slug(self, cwd: str) -> str:
        slug = cwd.replace("\\", "-").replace(":", "").replace("/", "-")
        slug = re.sub(r'[^a-zA-Z0-9_-]', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        return slug.strip('-')

    def detect(self) -> bool:
        if not self._db_path:
            self._find_paths()
        return self._db_path is not None and os.path.exists(self._db_path)

    def get_app_info(self) -> AppInfo:
        available = self.detect()
        return AppInfo(
            name=self.name,
            display_name=self.display_name,
            is_available=available,
            data_path=self._projects_dir if available else None,
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
            SELECT id, title, cwd, model, status, created_at, updated_at
            FROM sessions
            ORDER BY updated_at DESC
        """)
        except Exception:
            conn.close()
            return []

        conversations = []
        for row in cursor.fetchall():
            # 估算消息数：jsonl 文件中的非空行数
            jsonl_path = self._find_jsonl_path(row["id"], row["cwd"])
            msg_count = self._count_jsonl_messages(jsonl_path)

            conv = Conversation(
                id=row["id"],
                title=row["title"] or "(无标题对话)",
                created_at=self._ts_to_dt(row["created_at"], ms=True),
                updated_at=self._ts_to_dt(row["updated_at"], ms=True),
                source_app=self.display_name,
                metadata={
                    "cwd": row["cwd"],
                    "model": row["model"],
                    "status": row["status"],
                    "msg_count": msg_count,
                }
            )
            conversations.append(conv)

        conn.close()
        self._cached_conversations = conversations
        return conversations

    def _count_jsonl_messages(self, jsonl_path: Optional[str]) -> int:
        """快速统计 jsonl 文件中的消息行数（只数非空行，不解析 JSON）"""
        if not jsonl_path or not os.path.exists(jsonl_path):
            return 0
        try:
            with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
                return sum(1 for line in f if line.strip())
        except Exception:
            return 0

    def _find_jsonl_path(self, session_id: str, cwd: str) -> Optional[str]:
        if not self._projects_dir or not cwd:
            return None

        slug = self._cwd_to_slug(cwd)
        candidate = os.path.join(self._projects_dir, slug, f"{session_id}.jsonl")
        if os.path.exists(candidate):
            return candidate

        if os.path.exists(self._projects_dir):
            for dirname in os.listdir(self._projects_dir):
                dirpath = os.path.join(self._projects_dir, dirname)
                if os.path.isdir(dirpath):
                    cand = os.path.join(dirpath, f"{session_id}.jsonl")
                    if os.path.exists(cand):
                        return cand

        return None

    def get_conversation(self, conv_id: str) -> Optional[Conversation]:
        if not self.detect():
            return None

        conn = self._connect_db(self._db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, title, cwd, model, created_at, updated_at
            FROM sessions
            WHERE id = ?
        """, (conv_id,))
        sess_row = cursor.fetchone()
        conn.close()

        if not sess_row:
            return None

        jsonl_path = self._find_jsonl_path(conv_id, sess_row["cwd"])
        messages = []
        if jsonl_path and os.path.exists(jsonl_path):
            messages = self._parse_jsonl(jsonl_path)

        conv = Conversation(
            id=sess_row["id"],
            title=sess_row["title"] or "(无标题对话)",
            created_at=self._ts_to_dt(sess_row["created_at"], ms=True),
            updated_at=self._ts_to_dt(sess_row["updated_at"], ms=True),
            messages=messages,
            model=sess_row["model"],
            source_app=self.display_name,
            metadata={"cwd": sess_row["cwd"], "jsonl_path": jsonl_path}
        )
        return conv

    def _parse_jsonl(self, path: str) -> List[Message]:
        messages = []
        current_msg = None

        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = self._parse_record(record)
                if msg:
                    messages.append(msg)

        return messages

    def _parse_record(self, record: dict) -> Optional[Message]:
        rec_type = record.get("type", "")
        role_str = record.get("role", "")

        if rec_type == "reasoning":
            return Message(
                role=Role.SYSTEM,
                content=record.get("content", "") if isinstance(record.get("content"), str) else "",
                timestamp=self._ts_to_dt(record.get("timestamp"), ms=True),
                message_id=record.get("id"),
                parent_id=record.get("parentId"),
                parts=[MessagePart(type=MessagePartType.THINKING, content=self._extract_text(record.get("content", "")))],
                metadata={"type": "reasoning"}
            )

        if rec_type == "function_call":
            return Message(
                role=Role.ASSISTANT,
                content=f"[工具调用] {record.get('name', 'unknown')}",
                timestamp=self._ts_to_dt(record.get("timestamp"), ms=True),
                message_id=record.get("id"),
                parts=[MessagePart(
                    type=MessagePartType.TOOL_CALL,
                    tool_name=record.get("name", "unknown"),
                    tool_input=json.dumps(record.get("arguments", record.get("input", {})), ensure_ascii=False, indent=2)
                )]
            )

        if rec_type == "function_call_result":
            return Message(
                role=Role.TOOL,
                content=self._extract_text(record.get("content", "")),
                timestamp=self._ts_to_dt(record.get("timestamp"), ms=True),
                message_id=record.get("id"),
                parts=[MessagePart(
                    type=MessagePartType.TOOL_RESULT,
                    tool_name=record.get("name"),
                    tool_output=self._extract_text(record.get("content", ""))
                )]
            )

        if rec_type != "message":
            return None

        try:
            role = Role(role_str) if role_str else Role.USER
        except ValueError:
            role = Role.USER

        content_items = record.get("content", [])
        if isinstance(content_items, str):
            content_items = [{"type": "text", "text": content_items}]

        text_parts = []
        parts = []
        images = []

        for item in content_items:
            if not isinstance(item, dict):
                continue
            itype = item.get("type", "")
            if itype in ("input_text", "output_text", "text"):
                txt = item.get("text", "")
                if txt:
                    text_parts.append(txt)
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=txt))
            elif itype in ("image", "image_blob_ref"):
                img_path = item.get("blob_path", item.get("url", ""))
                img_name = item.get("original_filename", "image.png")
                images.append({"path": img_path, "name": img_name})
                parts.append(MessagePart(
                    type=MessagePartType.IMAGE,
                    content=f"[图片: {img_name}]",
                    file_name=img_name,
                    metadata={"path": img_path}
                ))

        content = "\n".join(text_parts)
        if images:
            content += "\n\n[附件图片]"
            for img in images:
                content += f"\n- {img['name']}"

        provider_data = record.get("providerData", {})
        model = provider_data.get("model") if isinstance(provider_data, dict) else None
        raw_usage = provider_data.get("rawUsage", {}) if isinstance(provider_data, dict) else {}
        token_usage = None
        if raw_usage:
            token_usage = {
                "input_tokens": raw_usage.get("prompt_tokens", 0),
                "output_tokens": raw_usage.get("completion_tokens", 0),
                "total_tokens": raw_usage.get("total_tokens", 0)
            }

        return Message(
            role=role,
            content=content,
            timestamp=self._ts_to_dt(record.get("timestamp"), ms=True),
            message_id=record.get("id"),
            parent_id=record.get("parentId"),
            parts=parts,
            model=model,
            token_usage=token_usage,
            metadata={"status": record.get("status")}
        )

    @staticmethod
    def _extract_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            texts = []
            for item in content:
                if isinstance(item, dict):
                    texts.append(item.get("text", ""))
                elif isinstance(item, str):
                    texts.append(item)
            return "\n".join(texts)
        if isinstance(content, dict):
            return content.get("text", json.dumps(content, ensure_ascii=False))
        return str(content)

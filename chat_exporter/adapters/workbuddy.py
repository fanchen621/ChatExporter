import json
import os
import re
from datetime import datetime
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
        self._sessions_json_path = None
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
            sessions_json = os.path.join(wb_dir, "app", "sessions.json")
            if os.path.exists(db_path):
                self._user_id = uid
                self._db_path = db_path
                self._projects_dir = projects_dir
                self._sessions_json_path = sessions_json
                return

    def _cwd_to_slug(self, cwd: str) -> str:
        slug = cwd.replace("\\", "-").replace(":", "").replace("/", "-")
        slug = re.sub(r'[^a-zA-Z0-9_-]', '-', slug)
        slug = re.sub(r'-+', '-', slug)
        return slug.strip('-')

    @staticmethod
    def _clean_title(title: str) -> str:
        """清洗对话标题：去除控制字符、折叠空白、截断过长标题。"""
        if not title:
            return "(无标题对话)"
        # 去除 \r \n \t 等控制字符
        title = re.sub(r'[\r\n\t]+', ' ', title)
        # 折叠连续空白
        title = re.sub(r'\s+', ' ', title).strip()
        # 截断过长标题（保留前 80 字符）
        if len(title) > 80:
            title = title[:80] + "..."
        return title or "(无标题对话)"

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

        # 读取 app/sessions.json 元数据缓存（WorkBuddy 的 SessionMetaWriter 写入，
        # 包含 DB 中可能缺失的会话，如 dd9b8415）
        sessions_meta = self._load_sessions_json()

        conn = self._connect_db(self._db_path)
        cursor = conn.cursor()

        try:
            cursor.execute("""
            SELECT id, title, cwd, model, status, created_at, updated_at
            FROM sessions
            WHERE deleted_at IS NULL
            ORDER BY updated_at DESC
        """)
        except Exception:
            conn.close()
            return []

        conversations = []
        seen_ids = set()
        for row in cursor.fetchall():
            seen_ids.add(row["id"])
            jsonl_path = self._find_jsonl_path(row["id"], row["cwd"])
            msg_count = self._count_jsonl_messages(jsonl_path)

            conv = Conversation(
                id=row["id"],
                title=self._clean_title(row["title"]),
                created_at=self._ts_to_dt(row["created_at"], ms=True),
                updated_at=self._ts_to_dt(row["updated_at"], ms=True),
                source_app=self.display_name,
                metadata={
                    "cwd": row["cwd"],
                    "model": row["model"],
                    "status": row["status"],
                    "msg_count": msg_count,
                    "jsonl_path": jsonl_path,
                }
            )
            conversations.append(conv)

        conn.close()

        # 补充：app/sessions.json 中有但 DB 中缺失的会话
        for sid, meta in sessions_meta.items():
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            cwd = meta.get("workDir", "")
            jsonl_path = self._find_jsonl_path(sid, cwd)
            if not jsonl_path:
                continue
            msg_count = self._count_jsonl_messages(jsonl_path)
            title = self._extract_title_from_jsonl(jsonl_path)
            conv = Conversation(
                id=sid,
                title=self._clean_title(title),
                created_at=self._parse_iso_dt(meta.get("startedAt")),
                updated_at=self._parse_iso_dt(meta.get("resumedAt")),
                source_app=self.display_name,
                metadata={
                    "cwd": cwd,
                    "model": "",
                    "status": "unknown",
                    "msg_count": msg_count,
                    "jsonl_path": jsonl_path,
                    "from_sessions_json": True,
                }
            )
            conversations.append(conv)

        # 最终补充：projects 目录中有 jsonl 但既不在 DB 也不在 sessions.json 的对话
        if self._projects_dir and os.path.isdir(self._projects_dir):
            self._scan_projects_dir(conversations, seen_ids)

        # 按更新时间降序排列
        conversations.sort(key=lambda c: c.updated_at or datetime.min, reverse=True)

        self._cached_conversations = conversations
        return conversations

    def _load_sessions_json(self) -> dict:
        """读取 app/sessions.json 元数据缓存。"""
        if not self._sessions_json_path or not os.path.exists(self._sessions_json_path):
            return {}
        try:
            with open(self._sessions_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # sessions.json 可能是 list 或 dict
            if isinstance(data, list):
                return {item.get("conversationId", ""): item for item in data if isinstance(item, dict)}
            if isinstance(data, dict):
                # 可能是 {conversationId: meta} 或含 sessions key
                if "sessions" in data and isinstance(data["sessions"], list):
                    return {item.get("conversationId", ""): item for item in data["sessions"] if isinstance(item, dict)}
                return data
        except Exception:
            pass
        return {}

    @staticmethod
    def _parse_iso_dt(s: Optional[str]) -> Optional[datetime]:
        """解析 ISO 8601 时间字符串（如 2026-06-20T12:10:51.000Z），返回 naive datetime。"""
        if not s or not isinstance(s, str):
            return None
        try:
            clean = s.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            # 统一返回 naive datetime（去掉时区信息），与 DB 的 _ts_to_dt 保持一致
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return dt
        except (ValueError, TypeError):
            return None

    def _scan_projects_dir(self, conversations: List[Conversation], db_ids: set):
        """扫描 projects 目录，补充 DB 中缺失的对话。"""
        for dirname in os.listdir(self._projects_dir):
            dirpath = os.path.join(self._projects_dir, dirname)
            if not os.path.isdir(dirpath):
                continue
            for fname in os.listdir(dirpath):
                if not fname.endswith(".jsonl"):
                    continue
                sid = fname[:-6]  # strip .jsonl
                if sid in db_ids:
                    continue
                fpath = os.path.join(dirpath, fname)
                title = self._extract_title_from_jsonl(fpath)
                msg_count = self._count_jsonl_messages(fpath)
                st = os.stat(fpath)
                conv = Conversation(
                    id=sid,
                    title=self._clean_title(title),
                    created_at=datetime.fromtimestamp(st.st_ctime),
                    updated_at=datetime.fromtimestamp(st.st_mtime),
                    source_app=self.display_name,
                    metadata={
                        "cwd": dirname,
                        "model": "",
                        "status": "unknown",
                        "msg_count": msg_count,
                        "jsonl_path": fpath,
                        "from_scan": True,
                    }
                )
                conversations.append(conv)

    @staticmethod
    def _extract_title_from_jsonl(path: str) -> str:
        """从 jsonl 文件中提取对话标题。

        取第一条用户消息（role=user），去除 <system-reminder> 块和
        <user_query> 包装标签，取实际用户问题作为标题。
        """
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # 跳过系统记录
                    rec_type = rec.get("type", "")
                    if rec_type in ("system", "system-reminder"):
                        continue

                    # 优先取 title 字段
                    title = rec.get("title")
                    if title:
                        return str(title)

                    # 只取用户消息作为标题（跳过 AI 回复）
                    role = rec.get("role", "")
                    if role != "user":
                        continue

                    # 从 content 中提取文本
                    content = rec.get("content", "")
                    if isinstance(content, list):
                        parts = []
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "input_text":
                                parts.append(item.get("text", ""))
                        content = "\n".join(parts)
                    if not isinstance(content, str) or not content.strip():
                        continue

                    # 去除 <system-reminder>...</system-reminder> 块
                    content = re.sub(
                        r'<system-reminder[^>]*>.*?</system-reminder>',
                        '', content, flags=re.DOTALL
                    )
                    # 去除 <user_query>...</user_query> 包装
                    content = re.sub(r'<user_query>', '', content)
                    content = re.sub(r'</user_query>', '', content)
                    content = content.strip()
                    if content:
                        first_line = content.split("\n")[0].strip()
                        if first_line:
                            return first_line
        except Exception:
            pass
        return "(无标题对话)"

    def _count_jsonl_messages(self, jsonl_path: Optional[str]) -> int:
        """统计 jsonl 文件中的有效消息记录数。

        使用快速字符串匹配，只计数 type 为 message/reasoning/function_call/
        function_call_result 的行，与 _parse_jsonl 的实际输出保持一致。
        """
        if not jsonl_path or not os.path.exists(jsonl_path):
            return 0
        # 已知的记录类型（与 _parse_record 中处理的类型一致）
        known_types = ('"type":"message"', '"type": "message"',
                       '"type":"reasoning"', '"type": "reasoning"',
                       '"type":"function_call"', '"type": "function_call"',
                       '"type":"function_call_result"', '"type": "function_call_result"')
        try:
            count = 0
            with open(jsonl_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    # 快速匹配已知类型，避免完整 JSON 解析
                    if any(t in line for t in known_types):
                        count += 1
            return count
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

        # 优先从 DB 获取元数据
        conn = self._connect_db(self._db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, cwd, model, created_at, updated_at
            FROM sessions
            WHERE id = ? AND deleted_at IS NULL
        """, (conv_id,))
        sess_row = cursor.fetchone()
        conn.close()

        if sess_row:
            # DB 中有记录 → 用 DB 元数据 + jsonl 内容
            jsonl_path = self._find_jsonl_path(conv_id, sess_row["cwd"])
            messages = self._parse_jsonl(jsonl_path) if jsonl_path and os.path.exists(jsonl_path) else []
            return Conversation(
                id=sess_row["id"],
                title=self._clean_title(sess_row["title"]),
                created_at=self._ts_to_dt(sess_row["created_at"], ms=True),
                updated_at=self._ts_to_dt(sess_row["updated_at"], ms=True),
                messages=messages,
                model=sess_row["model"],
                source_app=self.display_name,
                metadata={"cwd": sess_row["cwd"], "jsonl_path": jsonl_path}
            )

        # DB 中没有 → 从 sessions.json + projects 目录加载
        sessions_meta = self._load_sessions_json()
        meta = sessions_meta.get(conv_id, {})
        cwd = meta.get("workDir", "")

        # 搜索 jsonl 文件
        jsonl_path = self._find_jsonl_path(conv_id, cwd)
        if not jsonl_path or not os.path.exists(jsonl_path):
            return None

        messages = self._parse_jsonl(jsonl_path)
        title = self._extract_title_from_jsonl(jsonl_path)
        st = os.stat(jsonl_path)

        return Conversation(
            id=conv_id,
            title=self._clean_title(title),
            created_at=self._parse_iso_dt(meta.get("startedAt")) or datetime.fromtimestamp(st.st_ctime),
            updated_at=self._parse_iso_dt(meta.get("resumedAt")) or datetime.fromtimestamp(st.st_mtime),
            messages=messages,
            model="",
            source_app=self.display_name,
            metadata={"cwd": cwd, "jsonl_path": jsonl_path, "from_sessions_json": True}
        )

    def _parse_jsonl(self, path: str) -> List[Message]:
        messages = []

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
                    # 跳过完全空的消息（content 和 parts 都为空）
                    if not msg.content and not msg.parts:
                        continue
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

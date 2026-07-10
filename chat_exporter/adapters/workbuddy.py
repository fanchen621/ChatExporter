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

    def _probe_root(self, wb_dir: str):
        """Given a candidate .workbuddy dir, return (db_path, projects_dir,
        sessions_json, uid) if it is a usable WorkBuddy data root, else None.

        Accepts BOTH on-disk layouts:
          - <root>/workbuddy.db                        (real root: ~/WorkBuddy
            or ~/.workbuddy configDir, may itself be a symlink)
          - <root>/users/<uid>/.workbuddy/workbuddy.db (legacy
            %PROGRAMDATA%\\WorkBuddy\\users layout)
        """
        if not wb_dir or not os.path.exists(wb_dir):
            return None
        # layout A: db directly under root
        db_a = os.path.join(wb_dir, "workbuddy.db")
        # layout B: nested users/<uid>/.workbuddy
        db_b = None
        uid_b = None
        users_dir = os.path.join(wb_dir, "users")
        if os.path.isdir(users_dir):
            for uid in os.listdir(users_dir):
                cand = os.path.join(users_dir, uid, ".workbuddy", "workbuddy.db")
                if os.path.exists(cand):
                    db_b = cand
                    uid_b = uid
                    break
        db_path = db_a if os.path.exists(db_a) else db_b
        if not db_path:
            return None
        # resolve projects dir relative to the chosen db's .workbuddy root
        root_for_db = os.path.dirname(db_path)  # .../.workbuddy
        projects_dir = os.path.join(root_for_db, "projects")
        sessions_json = os.path.join(root_for_db, "app", "sessions.json")
        uid = uid_b  # (None for layout A)
        return (db_path, projects_dir, sessions_json, uid)

    def _find_paths(self):
        """Locate the REAL WorkBuddy data root.

        ROOT CAUSE OF EVERY PRIOR MISMATCH
        ---------------------------------
        The previous implementation hardcoded:
            users_dir = PROGRAMDATA + "\\WorkBuddy\\users"
        and only ever looked there. That path is a *stale secondary
        copy* of WorkBuddy's data. The authoritative data directory is
        the one WorkBuddy itself reports as its `configDir`:
            C:\\Users\\<user>\\.workbuddy
        (on this machine ~/.workbuddy resolves to the live store;
        WorkBuddy's own repair-helper log prints exactly
        `configDir=C:\\Users\\黄振飞\\.workbuddy`). The ProgramData copy
        does NOT contain today's live sessions, so ChatExporter's list
        could never match the WorkBuddy UI — that is why "问题依旧".

        FIX
        ---
        We now enumerate candidate roots in a STRICT PREFERENCE order
        (most-authoritative first) and stop at the FIRST one that
        actually contains a workbuddy.db. We deliberately do NOT pick by
        "which db has more sessions" — the stale ProgramData copy can
        legitimately have MORE rows (e.g. it still carries an orphan
        session) while being the WRONG store. Preference order:

          1. <HOME>/WorkBuddy          (WorkBuddy's real default data dir)
          2. <HOME>/.workbuddy       (WorkBuddy configDir; may be a
                                          symlink to the live store)
          3. %PROGRAMDATA%\\WorkBuddy  (legacy fallback only)

        Each candidate is probed for both the modern flat layout and the
        legacy users/<uid> nested layout.
        """
        home = str(Path.home())
        # strict preference: real roots first, legacy last
        candidates = [
            os.path.join(home, "WorkBuddy"),
            os.path.join(home, ".workbuddy"),
            os.path.join(self.program_data, "WorkBuddy"),
        ]
        for cand in candidates:
            if not os.path.exists(cand):
                continue
            # cand may be the .workbuddy root itself, or a parent dir
            probes = []
            if os.path.basename(cand) == ".workbuddy":
                probes.append(cand)
            else:
                probes.append(os.path.join(cand, ".workbuddy"))
                users_dir = os.path.join(cand, "users")
                if os.path.isdir(users_dir):
                    for uid in os.listdir(users_dir):
                        probes.append(os.path.join(users_dir, uid, ".workbuddy"))
            for wb_dir in probes:
                res = self._probe_root(wb_dir)
                if not res:
                    continue
                db_path, projects_dir, sessions_json, uid = res
                # FIRST valid root in preference order wins — stop immediately
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
        """列出 WorkBuddy 全部对话。

        与 WorkBuddy 自身 UI 保持完全一致：数据源为 workbuddy.db 的
        `sessions` 表（deleted_at IS NULL），按 updated_at DESC 排序。
        WorkBuddy 渲染端通过 client.sessions.list() -> UnifiedDB.getSessions
        读取的正是这张表，因此 ChatExporter 直接复用同一规则即可保证两边
        显示完全相同。修复前旧逻辑把 sessions.json / projects 目录里残留的
        孤儿会话（如迁移时掉落的 dd9b8415）也并入，反而导致 ChatExporter
        比 WorkBuddy 多出一条、两边对不上。
        """
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
            WHERE deleted_at IS NULL
            ORDER BY updated_at DESC
        """)
        except Exception:
            conn.close()
            return []

        conversations = []
        for row in cursor.fetchall():
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

        # 按更新时间降序排列
        conversations.sort(key=lambda c: c.updated_at or datetime.min, reverse=True)

        self._cached_conversations = conversations
        return conversations

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

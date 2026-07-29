import json
import hashlib
import mmap
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .base import BaseAdapter
from ..models import AppInfo, Conversation, Message, MessagePart, MessagePartType, Role
from ..preview_runtime import PreviewWindow
from ..preview_utils import PREVIEW_FULL, effective_role, message_preview_text, role_from_hint
from ..task_runtime import TaskCancelled, TaskContext


class WorkBuddyAdapter(BaseAdapter):
    name = "workbuddy"
    display_name = "WorkBuddy"
    _COUNT_CACHE_VERSION = 1
    _COUNT_MARKERS = (
        b'"type":"message"',
        b'"type": "message"',
        b'"type":"reasoning"',
        b'"type": "reasoning"',
        b'"type":"function_call"',
        b'"type": "function_call"',
        b'"type":"function_call_result"',
        b'"type": "function_call_result"',
    )

    def __init__(self):
        super().__init__()
        self._user_id = None
        self._projects_dir = None
        self._db_path = None
        self._sessions_json_path = None
        self._cached_conversations = None
        self._jsonl_path_cache = {}
        self._fallback_jsonl_index = None
        # The library opens immediately from SQLite metadata. Counts are reused
        # from a fingerprinted local cache, and only missing/stale files are
        # counted in a cancellable background task started by the product UI.
        self._message_count_cache = {}
        self._message_count_signatures = {}
        self._message_count_lock = threading.RLock()
        self._message_count_cache_path = os.path.join(
            self.appdata_local,
            "ChatExporter",
            "workbuddy_message_counts.json",
        )
        self._persistent_message_counts = {}
        self._active_message_count_keys = None
        self._load_message_count_cache()
        self._find_paths()

    @staticmethod
    def _message_count_key(path: str) -> str:
        normalized = os.path.normcase(os.path.abspath(path))
        return hashlib.sha256(normalized.encode("utf-8", errors="surrogatepass")).hexdigest()

    @staticmethod
    def _message_count_signature(path: str):
        try:
            stat = os.stat(path)
        except OSError:
            return None
        return int(stat.st_size), int(stat.st_mtime_ns)

    def _load_message_count_cache(self) -> None:
        try:
            with open(self._message_count_cache_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError, TypeError):
            return
        if payload.get("version") != self._COUNT_CACHE_VERSION:
            return
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return
        clean = {}
        for key, record in entries.items():
            if not isinstance(key, str) or not isinstance(record, dict):
                continue
            try:
                clean[key] = {
                    "size": int(record["size"]),
                    "mtime_ns": int(record["mtime_ns"]),
                    "count": max(0, int(record["count"])),
                }
            except (KeyError, TypeError, ValueError):
                continue
        self._persistent_message_counts = clean

    def _save_message_count_cache(self) -> None:
        with self._message_count_lock:
            if self._active_message_count_keys is not None:
                self._persistent_message_counts = {
                    key: record
                    for key, record in self._persistent_message_counts.items()
                    if key in self._active_message_count_keys
                }
            payload = {
                "version": self._COUNT_CACHE_VERSION,
                "entries": dict(self._persistent_message_counts),
            }
            folder = os.path.dirname(self._message_count_cache_path)
            os.makedirs(folder, exist_ok=True)
            temp_path = (
                f"{self._message_count_cache_path}.{os.getpid()}."
                f"{threading.get_ident()}.tmp"
            )
            try:
                with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                os.replace(temp_path, self._message_count_cache_path)
            finally:
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def _cached_message_count(self, path: Optional[str]) -> Optional[int]:
        if not path:
            return None
        signature = self._message_count_signature(path)
        if signature is None:
            return None
        with self._message_count_lock:
            if self._message_count_signatures.get(path) == signature:
                cached = self._message_count_cache.get(path)
                if cached is not None:
                    return int(cached)
            key = self._message_count_key(path)
            record = self._persistent_message_counts.get(key)
            if record and (record.get("size"), record.get("mtime_ns")) == signature:
                count = int(record["count"])
                self._message_count_cache[path] = count
                self._message_count_signatures[path] = signature
                return count
        return None

    def _remember_message_count(self, path: str, count: int, persist: bool = False) -> int:
        signature = self._message_count_signature(path)
        value = max(0, int(count))
        if signature is None:
            return value
        key = self._message_count_key(path)
        with self._message_count_lock:
            self._message_count_cache[path] = value
            self._message_count_signatures[path] = signature
            self._persistent_message_counts[key] = {
                "size": signature[0],
                "mtime_ns": signature[1],
                "count": value,
            }
        if persist:
            try:
                self._save_message_count_cache()
            except OSError:
                # Count caching is an acceleration layer, never a prerequisite
                # for reading or exporting the user's conversation.
                pass
        return value

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
        `configDir=C:\\Users\\用户名\\.workbuddy`). The ProgramData copy
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
        """Convert a WorkBuddy session cwd into the projects/<slug> dir name.

        Must match WorkBuddy's OWN slug rule (reverse-engineered from
        the on-disk `projects/` directory names). WorkBuddy keeps the
        drive letter, lowercases it, drops the colon, and replaces
        path separators with '-':
            C:\\Users\\用户名\\WorkBuddy\\2026-07-10  ->  c-Users-用户名-WorkBuddy-2026-07-10
            C:\\ProgramData\\...                          ->  c-ProgramData-...
        A wrong slug makes the primary jsonl lookup miss (the file is only
        ever found via the slow full-scan fallback), so getting this right
        is what keeps ChatExporter's exports in lock-step with the UI.
        """
        if not cwd:
            return ""
        cwd = cwd.replace("/", "\\")
        # cwd[0] = drive letter, cwd[1] = ':', cwd[2] = '\\'
        slug = cwd[0].lower() + "-" + cwd[3:].replace("\\", "-")
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

    def reset_runtime_cache(self) -> None:
        """Refresh library metadata while retaining valid fingerprinted counts."""
        self._cached_conversations = None
        self._jsonl_path_cache.clear()
        self._fallback_jsonl_index = None
        self._active_message_count_keys = None

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
        try:
            rows = conn.execute("""
                SELECT id, title, cwd, model, status, created_at, updated_at
                FROM sessions
                WHERE deleted_at IS NULL
                ORDER BY updated_at DESC
            """).fetchall()
        except Exception:
            return []
        finally:
            conn.close()

        conversations = []
        active_count_keys = set()
        for row in rows:
            jsonl_path = self._find_jsonl_path(row["id"], row["cwd"])
            if jsonl_path:
                active_count_keys.add(self._message_count_key(jsonl_path))
            cached_count = self._cached_message_count(jsonl_path)
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
                    "msg_count": cached_count or 0,
                    "msg_count_known": cached_count is not None,
                    "jsonl_path": jsonl_path,
                }
            )
            conversations.append(conv)
        self._active_message_count_keys = active_count_keys

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

    def _count_jsonl_messages(
        self,
        jsonl_path: Optional[str],
        context: Optional[TaskContext] = None,
    ) -> int:
        """统计 jsonl 文件中的有效消息记录数。

        使用快速字符串匹配，只计数 type 为 message/reasoning/function_call/
        function_call_result 的行，与 _parse_jsonl 的实际输出保持一致。
        """
        if not jsonl_path or not os.path.exists(jsonl_path):
            return 0
        count = 0
        try:
            with open(jsonl_path, "rb") as f:
                for line_number, line in enumerate(f, start=1):
                    if context and line_number % 2048 == 0:
                        context.check_cancelled()
                    if not line:
                        continue
                    # 二进制匹配避免 60MB+ 文件逐行 UTF-8 解码与 JSON 解析。
                    if any(marker in line for marker in self._COUNT_MARKERS):
                        count += 1
        except TaskCancelled:
            raise
        except OSError as exc:
            raise RuntimeError(f"无法统计 WorkBuddy 消息数：{exc}") from exc
        return count

    def get_message_count(
        self,
        conversation: Conversation,
        context: Optional[TaskContext] = None,
    ) -> int:
        """Return an exact source-record count without blocking library load."""
        metadata = conversation.metadata or {}
        path = metadata.get("jsonl_path")
        if not path:
            path = self._find_jsonl_path(str(conversation.id), metadata.get("cwd", ""))
            metadata["jsonl_path"] = path
            conversation.metadata = metadata

        cached = self._cached_message_count(path)
        if cached is None:
            if context:
                context.check_cancelled()
            cached = self._count_jsonl_messages(path, context=context)
            if path:
                self._remember_message_count(path, cached)

        metadata["msg_count"] = int(cached)
        metadata["msg_count_known"] = True
        conversation.metadata = metadata
        return int(cached)

    def flush_message_count_cache(self) -> None:
        """Persist all counts learned by the background hydration pass."""
        try:
            self._save_message_count_cache()
        except OSError:
            pass

    def _find_jsonl_path(self, session_id: str, cwd: str) -> Optional[str]:
        if not self._projects_dir or not cwd:
            return None

        cached = self._jsonl_path_cache.get(str(session_id))
        if cached and os.path.exists(cached):
            return cached

        slug = self._cwd_to_slug(cwd)
        candidate = os.path.join(self._projects_dir, slug, f"{session_id}.jsonl")
        if os.path.exists(candidate):
            self._jsonl_path_cache[str(session_id)] = candidate
            return candidate

        if self._fallback_jsonl_index is None:
            index = {}
            try:
                project_dirs = tuple(os.scandir(self._projects_dir))
            except OSError:
                project_dirs = ()
            for directory in project_dirs:
                if not directory.is_dir():
                    continue
                try:
                    files = os.scandir(directory.path)
                except OSError:
                    continue
                with files:
                    for item in files:
                        if item.is_file() and item.name.endswith(".jsonl"):
                            index.setdefault(item.name[:-6], item.path)
            self._fallback_jsonl_index = index

        fallback = self._fallback_jsonl_index.get(str(session_id))
        if fallback:
            self._jsonl_path_cache[str(session_id)] = fallback
            return fallback

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
            source_count = self._cached_message_count(jsonl_path)
            if source_count is None:
                try:
                    source_count = self._count_jsonl_messages(jsonl_path)
                except RuntimeError:
                    # Full conversation access must remain available even if
                    # the optional count pass loses a race with file rotation.
                    source_count = len(messages)
                if jsonl_path:
                    self._remember_message_count(jsonl_path, source_count, persist=True)
            loaded = Conversation(
                id=sess_row["id"],
                title=self._clean_title(sess_row["title"]),
                created_at=self._ts_to_dt(sess_row["created_at"], ms=True),
                updated_at=self._ts_to_dt(sess_row["updated_at"], ms=True),
                messages=messages,
                model=sess_row["model"],
                source_app=self.display_name,
                metadata={
                    "cwd": sess_row["cwd"],
                    "jsonl_path": jsonl_path,
                    "msg_count": source_count,
                    "msg_count_known": True,
                }
            )
            if self._cached_conversations:
                for stub in self._cached_conversations:
                    if str(stub.id) == str(conv_id):
                        stub.metadata["msg_count"] = source_count
                        stub.metadata["msg_count_known"] = True
                        break
            return loaded

        # DB 中没有该会话：在"单一真实数据根"设计下，这表示该会话
        # 对 WorkBuddy 自身也不可见（已删除 / 孤儿残留），不应再像旧版
        # "三源合并"那样去 sessions.json 里复活它——那正是之前 dd9b8415
        # 孤儿会话导致 ChatExporter 与 UI 对不上的根因。直接返回 None，
        # 保持与 WorkBuddy UI 严格一致。
        #
        # 注：旧实现此处调用了未定义的 _load_sessions_json() / _parse_iso_dt()，
        # 一旦命中会抛 AttributeError。现已移除该死代码。
        return None

    @staticmethod
    def _iter_jsonl_reverse(path: str, end_offset: Optional[int] = None):
        """Yield complete JSONL records backwards without reading the whole file."""
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size <= 0:
                return
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                position = size if end_offset is None else max(0, min(size, int(end_offset)))
                while position > 0:
                    while position > 0 and mapped[position - 1] in (10, 13):
                        position -= 1
                    if position <= 0:
                        break
                    line_end = position
                    newline = mapped.rfind(b"\n", 0, position)
                    line_start = newline + 1
                    raw = mapped[line_start:line_end].rstrip(b"\r")
                    position = line_start
                    if raw:
                        yield line_start, line_end, raw

    @staticmethod
    def _iter_jsonl_forward(path: str, start_offset: int = 0):
        """Yield complete JSONL records forwards from a known line boundary."""
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            position = max(0, min(size, int(start_offset or 0)))
            handle.seek(position)
            while True:
                line_start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                line_end = handle.tell()
                raw = raw.rstrip(b"\r\n")
                if raw:
                    yield line_start, line_end, raw

    def get_preview_window(
        self,
        conv_id: str,
        *,
        limit: int = 180,
        anchor: str = "latest",
        cursor: Optional[int] = None,
        mode: str = PREVIEW_FULL,
        context: Optional[TaskContext] = None,
    ) -> PreviewWindow:
        """Read one preview window from JSONL without parsing the full archive.

        WorkBuddy sessions can exceed tens of megabytes.  The library and
        preview should become usable immediately; full parsing is deferred to
        export or full-text indexing, where completeness actually matters.
        """
        if not self.detect():
            raise RuntimeError("WorkBuddy 数据目录不可用")
        conn = self._connect_db(self._db_path)
        try:
            row = conn.execute(
                """
                SELECT id, title, cwd, model, created_at, updated_at
                FROM sessions
                WHERE id = ? AND deleted_at IS NULL
                """,
                (conv_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            raise RuntimeError("WorkBuddy 中已找不到这条对话")
        path = self._find_jsonl_path(conv_id, row["cwd"])
        if not path or not os.path.exists(path):
            raise RuntimeError("WorkBuddy 对话文件不存在")

        file_size = os.path.getsize(path)
        limit = max(20, min(500, int(limit)))
        messages = []
        visible_count = 0
        scanned = 0

        def accept(raw: bytes) -> Optional[Message]:
            nonlocal scanned, visible_count
            scanned += 1
            if context and scanned % 64 == 0:
                context.check_cancelled()
            try:
                record = json.loads(raw.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, UnicodeError):
                return None
            message = self._parse_record(record)
            if not message or (not message.content and not message.parts):
                return None
            role = effective_role(message)
            if role in (Role.USER, Role.ASSISTANT) and message_preview_text(
                message,
                source_app=self.display_name,
                mode=PREVIEW_FULL,
            ):
                visible_count += 1
            return message

        if anchor in ("earliest", "newer"):
            start_offset = 0 if anchor == "earliest" else int(cursor or 0)
            cursor_before = start_offset
            cursor_after = start_offset
            for _start, end, raw in self._iter_jsonl_forward(path, start_offset):
                message = accept(raw)
                cursor_after = end
                if message is not None:
                    messages.append(message)
                if visible_count >= limit:
                    break
            has_older = cursor_before > 0
            has_newer = cursor_after < file_size
            total_hint = int(self._cached_message_count(path) or 0)
            if anchor == "earliest":
                label = (
                    f"最早 {visible_count} 条 · 共 {total_hint:,}"
                    if total_hint
                    else f"最早 {visible_count} 条"
                )
            else:
                label = (
                    f"{visible_count} 条 · 共 {total_hint:,}"
                    if total_hint
                    else f"{visible_count} 条"
                )
        else:
            end_offset = file_size if anchor == "latest" else int(cursor or file_size)
            cursor_after = end_offset
            cursor_before = end_offset
            reverse_messages = []
            for start, _end, raw in self._iter_jsonl_reverse(path, end_offset):
                message = accept(raw)
                cursor_before = start
                if message is not None:
                    reverse_messages.append(message)
                if visible_count >= limit:
                    break
            messages = list(reversed(reverse_messages))
            has_older = cursor_before > 0
            has_newer = cursor_after < file_size
            total_hint = int(self._cached_message_count(path) or 0)
            if anchor == "latest":
                label = (
                    f"最近 {visible_count} 条 · 共 {total_hint:,}"
                    if total_hint
                    else f"最近 {visible_count} 条"
                )
            else:
                label = (
                    f"{visible_count} 条 · 共 {total_hint:,}"
                    if total_hint
                    else f"{visible_count} 条"
                )

        conversation = Conversation(
            id=row["id"],
            title=self._clean_title(row["title"]),
            created_at=self._ts_to_dt(row["created_at"], ms=True),
            updated_at=self._ts_to_dt(row["updated_at"], ms=True),
            messages=messages,
            model=row["model"],
            source_app=self.display_name,
            metadata={
                "cwd": row["cwd"],
                "jsonl_path": path,
                "preview_partial": True,
                "preview_scanned_records": scanned,
            },
        )
        total_hint = int(self._cached_message_count(path) or 0)
        return PreviewWindow(
            conversation=conversation,
            cursor_before=cursor_before,
            cursor_after=cursor_after,
            has_older=has_older,
            has_newer=has_newer,
            total_source_messages=total_hint,
            label=label,
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
            # content 留空：reasoning 是思考过程，不应作为 AI 正文显示在预览中。
            # 思考内容仅存于 THINKING part，导出时折叠在 <details> 中，
            # 预览时由 message_preview_text 的 THINKING 摘要回退逻辑处理。
            return Message(
                role=Role.SYSTEM,
                content="",
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
            # WorkBuddy 的 function_call_result 结果在 output 字段，不在 content 字段。
            # output 可能是 dict({"type":"text","text":"..."}) 或 list([{...}])，
            # _extract_text 已兼容这两种格式。
            raw_output = record.get("output", record.get("content", ""))
            text = self._extract_text(raw_output)
            return Message(
                role=Role.TOOL,
                content=text,
                timestamp=self._ts_to_dt(record.get("timestamp"), ms=True),
                message_id=record.get("id"),
                parts=[MessagePart(
                    type=MessagePartType.TOOL_RESULT,
                    tool_name=record.get("name"),
                    tool_output=text
                )]
            )

        if rec_type != "message":
            return None

        try:
            role = Role(role_str) if role_str else Role.USER
        except ValueError:
            # 陌生角色名先问 role_from_hint（assistant_message / agent-output …）。
            # 无脑兜底成 USER 会把 AI 回复标成用户提问，导出里问答对整段错位。
            role = role_from_hint(role_str) or Role.USER

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
            # input_image / input_file 是用户粘贴的截图和附件，旧实现不认这两种
            # 类型：附件既不出现在正文也没有占位符，只带附件的消息更是被整条丢掉。
            elif itype in ("image", "image_blob_ref", "input_image", "output_image", "image_url"):
                img_path = item.get("blob_path") or item.get("url") or item.get("image") or item.get("image_url") or ""
                if isinstance(img_path, dict):
                    img_path = img_path.get("url", "")
                img_name = item.get("original_filename") or item.get("filename") or "image.png"
                images.append({"path": img_path, "name": img_name})
                parts.append(MessagePart(
                    type=MessagePartType.IMAGE,
                    content=f"[图片: {img_name}]",
                    file_name=img_name,
                    metadata={"path": img_path}
                ))
            elif itype in ("input_file", "output_file", "file", "file_ref", "input_document"):
                file_path = item.get("blob_path") or item.get("url") or item.get("file_url") or item.get("file") or ""
                if isinstance(file_path, dict):
                    file_path = file_path.get("url", "")
                file_name = (
                    item.get("original_filename") or item.get("filename")
                    or item.get("name") or "file"
                )
                parts.append(MessagePart(
                    type=MessagePartType.FILE,
                    content=f"[文件: {file_name}]",
                    file_name=file_name,
                    metadata={"path": file_path}
                ))
            else:
                # 认不出的 item 类型也要留痕：静默丢弃会让消息（甚至整条对话）
                # 在导出里凭空消失，看得见的原始内容至少还能人工恢复。
                txt = item.get("text", item.get("content", ""))
                if isinstance(txt, (dict, list)):
                    txt = json.dumps(txt, ensure_ascii=False)
                if not txt:
                    try:
                        txt = json.dumps(item, ensure_ascii=False)
                    except (TypeError, ValueError):
                        txt = str(item)
                if txt:
                    text_parts.append(str(txt))
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=str(txt)))

        # content 仅包含文本部分，图片信息由 IMAGE parts 结构化携带。
        # 避免预览/导出时 content 兜底与 IMAGE parts 重复展示图片描述。
        content = "\n".join(text_parts)

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
            token_usage=self._normalize_token_usage(token_usage),
            metadata={"status": record.get("status"), "raw_role": role_str}
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

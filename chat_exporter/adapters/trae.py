import json
import os
import re
import sqlite3
import struct
import tempfile
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from .base import BaseAdapter
from ..models import AppInfo, Conversation, Message, MessagePart, MessagePartType, Role


# SQLCipher key — 优先从环境变量读取，回退到进程内存提取
# 设置方式: set TRAE_SQLCIPHER_KEY=<your_key_hex>
# 密钥获取: 启动 TRAE SOLO CN 后，程序会自动从进程内存中提取
SQLCIPHER_KEY_HEX = os.environ.get("TRAE_SQLCIPHER_KEY", "")

# SQLCipher 页面布局常量
PAGE_SIZE = 4096
RESERVE = 80  # IV(16) + HMAC(64)
USABLE_SIZE = PAGE_SIZE - RESERVE  # 4016
IV_OFFSET = USABLE_SIZE
SQLITE_HEADER = b'SQLite format 3\x00'


class TraeAdapter(BaseAdapter):
    name = "trae"
    display_name = "TRAE SOLO CN"

    def __init__(self):
        super().__init__()
        self.app_dir = os.path.join(self.appdata_roaming, "TRAE SOLO CN")
        self.logs_dir = os.path.join(self.app_dir, "logs")
        self.modular_dir = os.path.join(self.app_dir, "ModularData")
        self.state_db_path = os.path.join(self.modular_dir, "ai-agent", "state.vscdb")
        self.encrypted_db_path = os.path.join(self.modular_dir, "ai-agent", "database.db")
        self._cached_conversations = None
        self._decrypted_db_path = None
        self._decryption_failed = False
        self._conversations_from_logs: Dict[str, List[Message]] = {}

    def detect(self) -> bool:
        return os.path.exists(self.app_dir)

    def get_app_info(self) -> AppInfo:
        available = self.detect()
        conv_count = 0
        note = ""
        if available:
            try:
                convs = self.list_conversations()
                conv_count = len(convs)
                if self._decrypted_db_path:
                    note = " (数据库解密)"
                elif self._decryption_failed:
                    note = " (从日志解析)"
            except Exception:
                pass
        return AppInfo(
            name=self.name,
            display_name=self.display_name + note,
            is_available=available,
            data_path=self.app_dir if available else None,
            conversation_count=conv_count
        )

    # ========== SQLCipher 解密 ==========

    def _get_decrypted_db_path(self) -> Optional[str]:
        """解密 SQLCipher 数据库到临时文件"""
        if self._decrypted_db_path and os.path.exists(self._decrypted_db_path):
            return self._decrypted_db_path
        if self._decryption_failed:
            return None
        if not os.path.exists(self.encrypted_db_path):
            return None

        # 尝试硬编码密钥
        key = bytes.fromhex(SQLCIPHER_KEY_HEX)
        result = self._try_decrypt(key)

        if not result:
            # 尝试从运行中的 TRAE 进程提取密钥
            runtime_key = self._extract_key_from_memory()
            if runtime_key:
                result = self._try_decrypt(runtime_key)

        if result:
            self._decrypted_db_path = result
            return result
        else:
            self._decryption_failed = True
            return None

    def _try_decrypt(self, key: bytes) -> Optional[str]:
        """用给定密钥解密数据库，返回临时文件路径"""
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            return None

        db_size = os.path.getsize(self.encrypted_db_path)
        num_pages = db_size // PAGE_SIZE

        # 先验证密钥：解密第1页并检查 SQLite 头部
        with open(self.encrypted_db_path, 'rb') as f:
            page1 = f.read(PAGE_SIZE)

        iv = page1[IV_OFFSET:IV_OFFSET + 16]
        ciphertext = page1[16:USABLE_SIZE]  # 4000 bytes

        try:
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
            dec = cipher.decryptor()
            decrypted = dec.update(ciphertext) + dec.finalize()
        except Exception:
            return None

        # SQLite 头部检查
        ps = struct.unpack('>H', decrypted[:2])[0]
        if ps == 1:
            ps = 65536
        if ps != PAGE_SIZE:
            return None
        if decrypted[4] != RESERVE:
            return None
        if decrypted[5] != 64 or decrypted[6] != 32 or decrypted[7] != 32:
            return None

        # 密钥有效，解密所有页
        tmp_dir = tempfile.mkdtemp(prefix="trae_decrypt_")
        tmp_path = os.path.join(tmp_dir, "database_decrypted.db")

        with open(self.encrypted_db_path, 'rb') as fin, open(tmp_path, 'wb') as fout:
            for page_num in range(1, num_pages + 1):
                page_data = fin.read(PAGE_SIZE)
                if len(page_data) < PAGE_SIZE:
                    break

                iv = page_data[IV_OFFSET:IV_OFFSET + 16]

                if page_num == 1:
                    ct = page_data[16:USABLE_SIZE]
                    c = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
                    d = c.decryptor()
                    decrypted = d.update(ct) + d.finalize()
                    out_page = SQLITE_HEADER + decrypted + b'\x00' * RESERVE
                else:
                    ct = page_data[:USABLE_SIZE]
                    c = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
                    d = c.decryptor()
                    decrypted = d.update(ct) + d.finalize()
                    out_page = decrypted + b'\x00' * RESERVE

                fout.write(out_page)

        return tmp_path

    def _extract_key_from_memory(self) -> Optional[bytes]:
        """从运行中的 TRAE 进程内存中提取 SQLCipher 密钥"""
        try:
            import ctypes
            import ctypes.wintypes as wt
        except ImportError:
            return None

        if os.name != 'nt':
            return None

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        MEM_COMMIT = 0x1000
        PAGE_READWRITE = 0x04
        PAGE_WRITECOPY = 0x08
        PAGE_EXECUTE_READWRITE = 0x40
        READABLE = {PAGE_READWRITE, PAGE_WRITECOPY, PAGE_EXECUTE_READWRITE}

        class MEMORY_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BaseAddress", ctypes.c_void_p),
                ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wt.DWORD),
                ("RegionSize", ctypes.c_size_t),
                ("State", wt.DWORD),
                ("Protect", wt.DWORD),
                ("Type", wt.DWORD),
            ]

        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
        k32.VirtualQueryEx.restype = ctypes.c_size_t
        k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_size_t,
                                           ctypes.POINTER(ctypes.c_size_t)]
        k32.ReadProcessMemory.restype = ctypes.c_int

        def read_mem(h, addr, size):
            buf = (ctypes.c_ubyte * size)()
            br = ctypes.c_size_t(0)
            if k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(br)):
                return bytes(buf[:br.value])
            return None

        # 获取 TRAE 进程 PID
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260),
            ]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        pids = []
        if k32.Process32First(snap, ctypes.byref(pe)):
            while True:
                name = pe.szExeFile.decode('utf-8', errors='replace')
                if 'trae' in name.lower():
                    pids.append(pe.th32ProcessID)
                if not k32.Process32Next(snap, ctypes.byref(pe)):
                    break
        k32.CloseHandle(snap)

        if not pids:
            return None

        # 读取数据库第1页用于验证
        try:
            with open(self.encrypted_db_path, 'rb') as f:
                page1 = f.read(PAGE_SIZE)
        except Exception:
            return None

        enc_data_1024 = page1[16:16 + 1024]
        iv = page1[IV_OFFSET:IV_OFFSET + 16]

        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend
        except ImportError:
            return None

        def test_key(candidate):
            try:
                c = Cipher(algorithms.AES(candidate), modes.CBC(iv), backend=default_backend())
                d = c.decryptor()
                dec = d.update(enc_data_1024) + d.finalize()
                ps = struct.unpack('>H', dec[:2])[0]
                if ps == 1:
                    ps = 65536
                if ps != PAGE_SIZE:
                    return False
                if dec[4] != RESERVE or dec[5] != 64 or dec[6] != 32 or dec[7] != 32:
                    return False
                return True
            except Exception:
                return False

        def is_high_entropy(data):
            if data[0] == data[1] == data[2] == data[3]:
                return False
            if all(0x20 <= b <= 0x7e for b in data[:16]):
                return False
            if data.count(0) > 28 or data.count(0xFF) > 28:
                return False
            return len(set(data[:16])) >= 8

        # 扫描每个进程的内存
        mbi = MEMORY_BASIC_INFORMATION()
        mbi_size = ctypes.sizeof(mbi)

        for pid in pids:
            hProcess = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            if not hProcess:
                continue

            address = 0
            while address < 0x7FFFFFFFFFFF:
                ret = k32.VirtualQueryEx(hProcess, ctypes.c_void_p(address), ctypes.byref(mbi), mbi_size)
                if ret == 0:
                    break

                base = mbi.BaseAddress or 0
                size = mbi.RegionSize or 0
                if size == 0:
                    break

                if mbi.State == MEM_COMMIT and mbi.Protect in READABLE:
                    chunk_max = 16 * 1024 * 1024
                    for off in range(0, size, chunk_max):
                        rs = min(chunk_max, size - off)
                        data = read_mem(hProcess, base + off, rs)
                        if data:
                            for o in range(0, len(data) - 32, 16):
                                cand = data[o:o + 32]
                                if not is_high_entropy(cand):
                                    continue
                                if test_key(cand):
                                    k32.CloseHandle(hProcess)
                                    return cand

                nxt = base + size
                if nxt <= address:
                    break
                address = nxt

            k32.CloseHandle(hProcess)

        return None

    # ========== 数据库查询 ==========

    def list_conversations(self) -> List[Conversation]:
        if self._cached_conversations is not None:
            return self._cached_conversations

        if not self.detect():
            return []

        # 优先尝试数据库
        db_path = self._get_decrypted_db_path()
        if db_path:
            conversations = self._get_conversations_from_db(db_path)
            if conversations:
                self._cached_conversations = conversations
                return conversations

        # 回退到日志解析
        conversations = self._list_conversations_from_logs()
        self._cached_conversations = conversations
        return conversations

    def _get_conversations_from_db(self, db_path: str) -> List[Conversation]:
        """从解密数据库查询对话列表"""
        conversations = []
        conn = None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    s.session_id,
                    s.session_title,
                    s.session_type,
                    s.work_mode,
                    s.created_at,
                    s.updated_at,
                    s.project_id,
                    COUNT(m.message_id) AS msg_count
                FROM chat_session s
                LEFT JOIN chat_message m ON s.session_id = m.session_id AND (m.deleted_at = 0 OR m.deleted_at IS NULL)
                WHERE (s.deleted_at = 0 OR s.deleted_at IS NULL)
                GROUP BY s.session_id
                ORDER BY s.updated_at DESC
            """)

            for row in cursor.fetchall():
                conv = Conversation(
                    id=row["session_id"],
                    title=row["session_title"] or f"对话 {row['session_id'][:8]}...",
                    created_at=self._ts_to_dt(row["created_at"], ms=False),
                    updated_at=self._ts_to_dt(row["updated_at"], ms=False),
                    source_app=self.display_name,
                    metadata={
                        "session_type": row["session_type"],
                        "work_mode": row["work_mode"],
                        "project_id": row["project_id"],
                        "msg_count": row["msg_count"] or 0,
                    }
                )
                conversations.append(conv)
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

        return conversations

    def get_conversation(self, conv_id: str) -> Optional[Conversation]:
        convs = self.list_conversations()
        for c in convs:
            if c.id == conv_id:
                if not c.messages:
                    db_path = self._get_decrypted_db_path()
                    if db_path:
                        c.messages = self._get_messages_from_db(db_path, conv_id)
                    else:
                        self._parse_logs_for_messages()
                        c.messages = self._conversations_from_logs.get(conv_id, [])
                return c
        return None

    def _get_messages_from_db(self, db_path: str, session_id: str) -> List[Message]:
        """从解密数据库查询指定对话的消息"""
        messages = []
        conn = None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT message_id, message_type, message_role, message_index,
                       created_at, user_message_context
                FROM chat_message
                WHERE session_id = ? AND (deleted_at = 0 OR deleted_at IS NULL)
                ORDER BY message_index
            """, (session_id,))

            for row in cursor.fetchall():
                msg_id = row["message_id"]
                msg_type = row["message_type"]
                msg_role = row["message_role"]
                ts = self._ts_to_dt(row["created_at"], ms=False)

                # 解析模型信息
                model = None
                if row["user_message_context"]:
                    try:
                        ctx = json.loads(row["user_message_context"])
                        model = ctx.get("model_info", {}).get("model_name")
                    except Exception:
                        pass

                role = Role.USER if msg_role == "user" else Role.ASSISTANT

                if msg_type == "general":
                    cursor.execute("""
                        SELECT content FROM chat_message_general
                        WHERE message_id = ? AND (deleted_at = 0 OR deleted_at IS NULL)
                    """, (msg_id,))
                    gen_row = cursor.fetchone()
                    if gen_row:
                        parts = self._parse_general_content(gen_row["content"])
                        text = " ".join(p.content for p in parts if p.type == MessagePartType.TEXT)
                        messages.append(Message(
                            role=role, content=text, timestamp=ts,
                            message_id=msg_id, parts=parts, model=model,
                        ))

                elif msg_type == "task":
                    cursor.execute("""
                        SELECT content, summary FROM chat_message_task
                        WHERE message_id = ? AND (deleted_at = 0 OR deleted_at IS NULL)
                    """, (msg_id,))
                    task_row = cursor.fetchone()
                    if task_row:
                        parts = self._parse_task_content(task_row["content"])
                        # task 消息的主内容在 THINKING/TOOL_CALL parts 里，不只 TEXT
                        # 摘要优先；否则用第一个 THINKING part；否则汇总所有 parts
                        summary = (task_row["summary"] or "").strip() if task_row["summary"] else ""
                        if summary:
                            text = summary
                        else:
                            thinking = next((p.content for p in parts
                                             if p.type == MessagePartType.THINKING and p.content), "")
                            if thinking:
                                text = thinking[:500]
                            else:
                                text = " ".join(p.content for p in parts if p.content)[:500]
                        messages.append(Message(
                            role=role, content=text, timestamp=ts,
                            message_id=msg_id, parts=parts, model=model,
                        ))
                else:
                    # Fallback for unknown message types - try to find content in any subtable
                    for subtable in ["chat_message_general", "chat_message_task"]:
                        cursor.execute(f"""
                            SELECT content FROM {subtable}
                            WHERE message_id = ? AND (deleted_at = 0 OR deleted_at IS NULL)
                        """, (msg_id,))
                        sub_row = cursor.fetchone()
                        if sub_row:
                            parts = [MessagePart(type=MessagePartType.TEXT, content=sub_row["content"][:5000])]
                            messages.append(Message(
                                role=role, content=sub_row["content"][:500], timestamp=ts,
                                message_id=msg_id, parts=parts, model=model,
                            ))
                            break
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

        return messages

    def _parse_general_content(self, content_json: str) -> List[MessagePart]:
        """解析 chat_message_general.content JSON"""
        parts = []
        try:
            data = json.loads(content_json)
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        text = item.get("text_content") or item.get("text") or ""
                        if text:
                            parts.append(MessagePart(type=MessagePartType.TEXT, content=text))
            elif isinstance(data, dict):
                text = data.get("text_content") or data.get("text") or data.get("content") or ""
                if text:
                    parts.append(MessagePart(type=MessagePartType.TEXT, content=text))
            elif isinstance(data, str) and data:
                parts.append(MessagePart(type=MessagePartType.TEXT, content=data))
        except Exception:
            if content_json:
                parts.append(MessagePart(type=MessagePartType.TEXT, content=content_json))
        return parts

    def _parse_task_content(self, content_json: str) -> List[MessagePart]:
        """解析 chat_message_task.content JSON — AI 任务消息

        实际结构：
        - plan_item (主要): {plan_item: {thought, reasoning_content, tool_call_info, agent_status}}
        - append_input: {append_input: {payload: {data: {parsed_query: [...]}}}}
        """
        parts = []
        try:
            data = json.loads(content_json)
            msgs = data.get("messages", [])
            for msg in msgs:
                mtype = msg.get("type", "")

                if mtype == "plan_item":
                    plan = msg.get("plan_item", {}) or {}
                    self._extract_plan_item_parts(plan, parts)

                elif mtype == "append_input":
                    ai = msg.get("append_input", {}) or {}
                    payload = ai.get("payload", {}) or {}
                    pdata = payload.get("data", {}) or {}
                    query = pdata.get("parsed_query") or []
                    if isinstance(query, list) and query:
                        text = query[0] if isinstance(query[0], str) else str(query[0])
                        if text:
                            parts.append(MessagePart(type=MessagePartType.TEXT, content=text))

                elif mtype in ("text", "assistant_message"):
                    text = msg.get("text", "") or msg.get("content", "")
                    if text:
                        parts.append(MessagePart(type=MessagePartType.TEXT, content=text))

                elif mtype in ("thinking", "reasoning"):
                    text = msg.get("content", "") or msg.get("text", "")
                    if text:
                        parts.append(MessagePart(type=MessagePartType.THINKING, content=text))

                elif mtype == "tool_call":
                    tname = msg.get("tool_name", "") or msg.get("name", "")
                    tinput = msg.get("input", msg.get("params", {}))
                    if isinstance(tinput, dict):
                        tinput = json.dumps(tinput, ensure_ascii=False, indent=2)
                    parts.append(MessagePart(
                        type=MessagePartType.TOOL_CALL,
                        content=f"调用工具: {tname}",
                        tool_name=tname,
                        tool_input=str(tinput),
                    ))

                elif mtype in ("tool_result", "tool_call_result"):
                    tout = msg.get("output", "") or msg.get("result", "")
                    if isinstance(tout, dict):
                        tout = json.dumps(tout, ensure_ascii=False, indent=2)
                    parts.append(MessagePart(
                        type=MessagePartType.TOOL_RESULT,
                        content=str(tout)[:5000],
                        tool_output=str(tout),
                    ))
        except Exception:
            if content_json:
                parts.append(MessagePart(type=MessagePartType.TEXT, content=content_json[:2000]))
        return parts

    def _extract_plan_item_parts(self, plan: dict, parts: List[MessagePart]):
        """从 plan_item 结构提取 parts: thought/reasoning -> THINKING, tool_call_info -> TOOL_CALL/TOOL_RESULT"""
        # 1. 思考内容
        thought = plan.get("thought", "") or ""
        reasoning = plan.get("reasoning_content", "") or ""
        if reasoning:
            parts.append(MessagePart(type=MessagePartType.THINKING, content=reasoning))
        elif thought:
            parts.append(MessagePart(type=MessagePartType.THINKING, content=thought))

        # 2. 工具调用
        tci = plan.get("tool_call_info")
        if isinstance(tci, dict):
            tname = tci.get("name", "") or ""
            tparams = tci.get("params", {})
            if isinstance(tparams, dict):
                tparams_str = json.dumps(tparams, ensure_ascii=False, indent=2)
            else:
                tparams_str = str(tparams)
            if tname:
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_CALL,
                    content=f"调用工具: {tname}",
                    tool_name=tname,
                    tool_input=tparams_str,
                ))

            # 3. 工具结果
            tresult = tci.get("result")
            if isinstance(tresult, dict):
                status = tresult.get("status", "")
                rdata = tresult.get("data", {})
                err = tresult.get("error_message", "")
                if err:
                    content = f"[{status}] {err}"
                elif isinstance(rdata, dict) and rdata:
                    content = json.dumps(rdata, ensure_ascii=False, indent=2)[:5000]
                elif isinstance(rdata, list) and rdata:
                    content = json.dumps(rdata, ensure_ascii=False, indent=2)[:5000]
                else:
                    content = f"[{status}]" if status else ""
                if content:
                    parts.append(MessagePart(
                        type=MessagePartType.TOOL_RESULT,
                        content=content,
                        tool_output=json.dumps(tresult, ensure_ascii=False)[:10000],
                    ))

    # ========== 日志解析（回退方案） ==========

    def _list_conversations_from_logs(self) -> List[Conversation]:
        """从 state.vscdb 和日志解析对话（回退方案）"""
        conversations = []
        convs_from_state = self._get_conversations_from_state()
        self._parse_logs_for_messages()

        seen_ids = set()
        for c in convs_from_state:
            seen_ids.add(c.id)
            c.messages = self._conversations_from_logs.get(c.id, [])
            c.metadata = c.metadata or {}
            c.metadata["msg_count"] = len(c.messages)
            conversations.append(c)

        for cid, msgs in self._conversations_from_logs.items():
            if cid not in seen_ids:
                conversations.append(Conversation(
                    id=cid,
                    title=f"对话 {cid[:8]}...",
                    messages=msgs,
                    source_app=self.display_name,
                    updated_at=msgs[-1].timestamp if msgs else None,
                    metadata={"msg_count": len(msgs)}
                ))

        conversations.sort(key=lambda c: c.updated_at or datetime.min, reverse=True)
        return conversations

    def _get_conversations_from_state(self) -> List[Conversation]:
        conversations = []
        if not os.path.exists(self.state_db_path):
            return conversations

        conn = None
        try:
            conn = sqlite3.connect(self.state_db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r["name"] for r in cursor.fetchall()]

            if "ItemTable" in tables:
                cursor.execute(
                    "SELECT key, value FROM ItemTable WHERE key LIKE '%session%' OR key LIKE '%chat%' OR key LIKE '%conversation%' LIMIT 5000")
                for row in cursor.fetchall():
                    key = row["key"]
                    value = row["value"]
                    if not value:
                        continue
                    try:
                        data = json.loads(value)
                        if isinstance(data, dict):
                            conv = self._extract_conv_from_item(key, data)
                            if conv:
                                conversations.append(conv)
                        elif isinstance(data, list):
                            for item in data:
                                if isinstance(item, dict):
                                    conv = self._extract_conv_from_item(key, item)
                                    if conv:
                                        conversations.append(conv)
                    except (json.JSONDecodeError, TypeError):
                        pass
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

        return conversations

    def _extract_conv_from_item(self, key: str, data: dict) -> Optional[Conversation]:
        conv_id = data.get("id") or data.get("sessionId") or data.get("conversationId") or data.get("chat_id")
        if not conv_id:
            return None

        title = data.get("title") or data.get("name") or ""
        created = data.get("createdAt") or data.get("created_at") or data.get("timestamp")
        updated = data.get("updatedAt") or data.get("updated_at") or created

        created_dt = self._parse_iso_dt(created) if isinstance(created, str) else (
            self._ts_to_dt(created, ms=True) if isinstance(created, (int, float)) else None)
        updated_dt = self._parse_iso_dt(updated) if isinstance(updated, str) else (
            self._ts_to_dt(updated, ms=True) if isinstance(updated, (int, float)) else None)

        return Conversation(
            id=str(conv_id),
            title=title or f"对话 {str(conv_id)[:8]}...",
            created_at=created_dt,
            updated_at=updated_dt,
            source_app=self.display_name,
            metadata={"state_key": key}
        )

    def _parse_logs_for_messages(self):
        if self._conversations_from_logs:
            return

        log_files = []
        if os.path.exists(self.logs_dir):
            for root, dirs, files in os.walk(self.logs_dir):
                for f in files:
                    if "stdout" in f.lower() and f.endswith(".log"):
                        log_files.append(os.path.join(root, f))

        log_files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)

        session_messages: Dict[str, List[Message]] = {}

        for log_path in log_files[:20]:
            try:
                self._parse_single_log(log_path, session_messages)
            except Exception:
                continue

        self._conversations_from_logs = session_messages

    def _parse_single_log(self, log_path: str, session_messages: Dict[str, List[Message]]):
        session_id = None
        current_role = None
        current_content_lines = []
        current_timestamp = None

        def flush():
            nonlocal session_id, current_role, current_content_lines, current_timestamp
            if session_id and current_role and current_content_lines:
                content = "\n".join(current_content_lines).strip()
                if content:
                    role = Role.USER if current_role == "user" else Role.ASSISTANT
                    msg = Message(
                        role=role,
                        content=content,
                        timestamp=current_timestamp,
                        parts=[MessagePart(type=MessagePartType.TEXT, content=content)]
                    )
                    if session_id not in session_messages:
                        session_messages[session_id] = []
                    session_messages[session_id].append(msg)
            current_role = None
            current_content_lines = []

        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.rstrip("\n\r")

                ts_match = re.search(r'"timestamp"\s*:\s*(\d+)', line)
                sid_match = re.search(r'"(?:session_id|sessionId|conversation_id)"\s*:\s*"([^"]+)"', line)
                role_match = re.search(r'"role"\s*:\s*"(user|assistant|system)"', line)

                if sid_match:
                    flush()
                    session_id = sid_match.group(1)
                if role_match:
                    flush()
                    current_role = role_match.group(1)
                if ts_match and not current_timestamp:
                    current_timestamp = self._ts_to_dt(int(ts_match.group(1)), ms=True)

                if "[User" in line or "user message" in line.lower():
                    flush()
                    current_role = "user"
                elif "[Assistant" in line or "assistant message" in line.lower():
                    flush()
                    current_role = "assistant"

                if current_role and not line.strip().startswith("{") and "INSERT" not in line and "PRAGMA" not in line:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("[") and "sqlcipher" not in stripped.lower():
                        current_content_lines.append(line)

        flush()

    @staticmethod
    def _parse_iso_dt(s: str) -> Optional[datetime]:
        if not s:
            return None
        for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%f",
                     "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"]:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

import ctypes
import hashlib
import json
import os
import re
import sqlite3
import struct
import tempfile
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .base import BaseAdapter
from ..models import AppInfo, Conversation, Message, MessagePart, MessagePartType, Role


# SQLCipher key — 优先从环境变量读取。
# 设置方式: setx TRAE_SQLCIPHER_KEY <your_key_hex>
SQLCIPHER_KEY_ENV = "TRAE_SQLCIPHER_KEY"

# 默认自动扫进程内存：换到别的电脑时，环境变量/缓存里没有 key，
# 只要 TRAE SOLO CN 正在运行，就自动从进程内存提取本机 SQLCipher 密钥
# （有界 8s、仅扫描 TRAE 进程、不存在进程则立即返回，不会卡顿）。
# 若希望完全禁止自动扫描（仅通过 GUI 按钮或环境变量手动提供 key），
# 可手动设置: setx TRAE_ENABLE_MEMORY_SCAN 0
MEMORY_SCAN_ENV = "TRAE_ENABLE_MEMORY_SCAN"

# 缓存策略：默认允许读取/写入本工具缓存，但缓存会按数据库指纹校验，避免旧 key 误用。
KEY_CACHE_ENV = "TRAE_ENABLE_KEY_CACHE"

# SQLCipher 页面布局常量
PAGE_SIZE = 4096
RESERVE = 80  # IV(16) + HMAC(64)
USABLE_SIZE = PAGE_SIZE - RESERVE  # 4016
IV_OFFSET = USABLE_SIZE
SQLITE_HEADER = b"SQLite format 3\x00"

# 性能限制参数
MEMORY_SCAN_TIMEOUT_SEC = 8
MEMORY_SCAN_MAX_BYTES = 300 * 1024 * 1024
LOG_MAX_FILES = 5
LOG_TAIL_BYTES = 5 * 1024 * 1024
ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


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
        self._decryption_attempted = False
        self._conversations_from_logs: Dict[str, List[Message]] = {}

    def detect(self) -> bool:
        return os.path.exists(self.app_dir)

    def get_app_info(self) -> AppInfo:
        available = self.detect()
        return AppInfo(
            name=self.name,
            display_name=self.display_name,
            is_available=available,
            data_path=self.app_dir if available else None,
            conversation_count=0,
        )

    # ========== 用户显式密钥助手 ==========

    def extract_key_for_user(self, progress_callback: ProgressCallback = None) -> Dict[str, Any]:
        """显式为 GUI 按钮服务的 TRAE 密钥提取流程。

        这个方法只在用户点击“提取 TRAE 密钥”后调用。它会先复用环境变量/缓存，
        再进行有界内存扫描；成功后只写入本地缓存，不上传、不写仓库。
        """
        started = time.monotonic()

        def report(stage: str, message: str, **extra):
            if not progress_callback:
                return
            payload = {"stage": stage, "message": message}
            payload.update(extra)
            try:
                progress_callback(payload)
            except Exception:
                pass

        if not os.path.exists(self.encrypted_db_path):
            return {
                "ok": False,
                "reason": "TRAE 加密数据库不存在",
                "hint": self.encrypted_db_path,
                "elapsed": round(time.monotonic() - started, 3),
            }

        env_key = self._load_env_key()
        if env_key and self._validate_key(env_key):
            self._save_key_cache(env_key)
            return self._key_success(env_key, "环境变量", started)

        cached_key = self._load_cached_key()
        if cached_key and self._validate_key(cached_key):
            return self._key_success(cached_key, "本地缓存", started)

        if cached_key:
            self._delete_key_cache()

        if os.name != "nt":
            return {
                "ok": False,
                "reason": "自动内存扫描目前只支持 Windows",
                "elapsed": round(time.monotonic() - started, 3),
            }

        report("prepare", "准备扫描 TRAE 进程内存，请保持 TRAE 正在运行...")
        runtime_key = self._extract_key_from_memory(progress_callback=progress_callback)
        if runtime_key and self._validate_key(runtime_key):
            self._save_key_cache(runtime_key)
            return self._key_success(runtime_key, "内存扫描", started)

        return {
            "ok": False,
            "reason": "没有在 TRAE 进程内存中找到可用密钥",
            "hint": "请确认 TRAE SOLO CN 已启动并打开过至少一个对话；如仍失败，可手动设置 TRAE_SQLCIPHER_KEY。",
            "elapsed": round(time.monotonic() - started, 3),
        }

    @staticmethod
    def _key_success(key: bytes, source: str, started: float) -> Dict[str, Any]:
        return {
            "ok": True,
            "key_hex": key.hex(),
            "source": source,
            "elapsed": round(time.monotonic() - started, 3),
        }

    def reset_runtime_cache(self):
        """密钥变化后让下一次读取重新走解密路径。"""
        self._cached_conversations = None
        self._decrypted_db_path = None
        self._decryption_attempted = False
        self._conversations_from_logs = {}

    # ========== SQLCipher 解密 ==========

    def _get_decrypted_db_path(self) -> Optional[str]:
        """解密 SQLCipher 数据库到临时文件。

        优先级：
        1. 环境变量密钥：最快、最稳定。
        2. 指纹匹配的本地缓存密钥：用于用户明确开启过扫描后的秒级复用。
        3. 可选内存扫描：默认关闭，避免无密钥机器卡顿。
        4. 回退日志解析。
        """
        if self._decrypted_db_path and os.path.exists(self._decrypted_db_path):
            return self._decrypted_db_path
        if self._decryption_attempted:
            return None
        if not os.path.exists(self.encrypted_db_path):
            return None

        self._decryption_attempted = True

        env_key = self._load_env_key()
        if env_key:
            result = self._try_decrypt(env_key)
            if result:
                self._decrypted_db_path = result
                return result

        cached_key = self._load_cached_key()
        if cached_key:
            result = self._try_decrypt(cached_key)
            if result:
                self._decrypted_db_path = result
                return result
            self._delete_key_cache()

        if self._memory_scan_enabled():
            runtime_key = self._extract_key_from_memory()
            if runtime_key:
                result = self._try_decrypt(runtime_key)
                if result:
                    self._save_key_cache(runtime_key)
                    self._decrypted_db_path = result
                    return result

        return None

    @staticmethod
    def _parse_key_hex(raw: Optional[str]) -> Optional[bytes]:
        if not raw:
            return None
        text = raw.strip().strip('"').strip("'")
        if text.lower().startswith("0x"):
            text = text[2:]
        if not re.fullmatch(r"[0-9a-fA-F]{64}", text):
            return None
        try:
            key = bytes.fromhex(text)
        except ValueError:
            return None
        return key if len(key) == 32 else None

    def _load_env_key(self) -> Optional[bytes]:
        return self._parse_key_hex(os.environ.get(SQLCIPHER_KEY_ENV, ""))

    @staticmethod
    def _env_truthy(name: str, default: bool = False) -> bool:
        raw = os.environ.get(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

    def _memory_scan_enabled(self) -> bool:
        return self._env_truthy(MEMORY_SCAN_ENV, default=True)

    def _key_cache_enabled(self) -> bool:
        return self._env_truthy(KEY_CACHE_ENV, default=True)

    def _validate_key(self, key: bytes) -> bool:
        """只解密第一页快速校验 key，避免为了“获取 key”而先全库解密。"""
        if not key or len(key) != 32:
            return False

        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            return False

        try:
            with open(self.encrypted_db_path, "rb") as f:
                page1 = f.read(PAGE_SIZE)
        except OSError:
            return False
        if len(page1) < PAGE_SIZE:
            return False

        iv = page1[IV_OFFSET:IV_OFFSET + 16]
        ciphertext = page1[16:USABLE_SIZE]

        try:
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
            dec = cipher.decryptor()
            decrypted = dec.update(ciphertext) + dec.finalize()
            ps = struct.unpack(">H", decrypted[:2])[0]
        except Exception:
            return False

        if ps == 1:
            ps = 65536
        return (
            ps == PAGE_SIZE
            and decrypted[4] == RESERVE
            and decrypted[5] == 64
            and decrypted[6] == 32
            and decrypted[7] == 32
        )

    def _try_decrypt(self, key: bytes) -> Optional[str]:
        if not self._validate_key(key):
            return None

        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            return None

        try:
            db_size = os.path.getsize(self.encrypted_db_path)
        except OSError:
            return None
        if db_size < PAGE_SIZE:
            return None

        num_pages = db_size // PAGE_SIZE
        tmp_dir = tempfile.mkdtemp(prefix="trae_decrypt_")
        tmp_path = os.path.join(tmp_dir, "database_decrypted.db")

        try:
            with open(self.encrypted_db_path, "rb") as fin, open(tmp_path, "wb") as fout:
                for page_num in range(1, num_pages + 1):
                    page_data = fin.read(PAGE_SIZE)
                    if len(page_data) < PAGE_SIZE:
                        break

                    iv = page_data[IV_OFFSET:IV_OFFSET + 16]
                    ct = page_data[16:USABLE_SIZE] if page_num == 1 else page_data[:USABLE_SIZE]

                    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
                    dec = cipher.decryptor()
                    decrypted = dec.update(ct) + dec.finalize()

                    if page_num == 1:
                        out_page = SQLITE_HEADER + decrypted + b"\x00" * RESERVE
                    else:
                        out_page = decrypted + b"\x00" * RESERVE
                    fout.write(out_page)
        except Exception:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            return None

        return tmp_path

    # ========== 密钥缓存 ==========

    def _db_fingerprint(self) -> Optional[str]:
        try:
            st = os.stat(self.encrypted_db_path)
            with open(self.encrypted_db_path, "rb") as f:
                head = f.read(PAGE_SIZE)
            digest = hashlib.sha256(head).hexdigest()
            return f"size={st.st_size};mtime={int(st.st_mtime)};sha256_page1={digest}"
        except OSError:
            return None

    def _get_key_cache_path(self) -> str:
        base_dir = os.path.join(self.appdata_local, "ChatExporter")
        try:
            os.makedirs(base_dir, exist_ok=True)
        except OSError:
            base_dir = tempfile.gettempdir()
        return os.path.join(base_dir, "trae_sqlcipher_key.cache")

    def _load_cached_key(self) -> Optional[bytes]:
        if not self._key_cache_enabled():
            return None
        cache_path = self._get_key_cache_path()
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None

        if data.get("version") != 1:
            return None
        if data.get("db_fingerprint") != self._db_fingerprint():
            return None
        return self._parse_key_hex(data.get("key_hex"))

    def _save_key_cache(self, key: bytes):
        if not self._key_cache_enabled() or not key or len(key) != 32:
            return
        fingerprint = self._db_fingerprint()
        if not fingerprint:
            return
        cache_path = self._get_key_cache_path()
        data = {
            "version": 1,
            "db_fingerprint": fingerprint,
            "key_hex": key.hex(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            try:
                os.chmod(cache_path, 0o600)
            except OSError:
                pass
        except Exception:
            pass

    def _delete_key_cache(self):
        try:
            os.remove(self._get_key_cache_path())
        except OSError:
            pass

    # ========== 可选内存扫描 ==========

    def _extract_key_from_memory(self, progress_callback: ProgressCallback = None) -> Optional[bytes]:
        try:
            import ctypes.wintypes as wt
        except ImportError:
            return None

        if os.name != "nt":
            return None

        def report(stage: str, message: str, **extra):
            if not progress_callback:
                return
            payload = {"stage": stage, "message": message}
            payload.update(extra)
            try:
                progress_callback(payload)
            except Exception:
                pass

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        MEM_COMMIT = 0x1000
        MEM_PRIVATE = 0x20000
        PAGE_READONLY = 0x02
        PAGE_READWRITE = 0x04
        PAGE_WRITECOPY = 0x08
        PAGE_EXECUTE_READ = 0x20
        PAGE_EXECUTE_READWRITE = 0x40
        PAGE_EXECUTE_WRITECOPY = 0x80
        PAGE_GUARD = 0x100
        READABLE = {
            PAGE_READONLY,
            PAGE_READWRITE,
            PAGE_WRITECOPY,
            PAGE_EXECUTE_READ,
            PAGE_EXECUTE_READWRITE,
            PAGE_EXECUTE_WRITECOPY,
        }

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

        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD),
                ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        k32.Process32First.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        k32.Process32First.restype = wt.BOOL
        k32.Process32Next.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        k32.Process32Next.restype = wt.BOOL
        k32.CloseHandle.argtypes = [wt.HANDLE]
        k32.CloseHandle.restype = wt.BOOL
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.OpenProcess.restype = wt.HANDLE
        k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                       ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
        k32.VirtualQueryEx.restype = ctypes.c_size_t
        k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p,
                                          ctypes.c_void_p, ctypes.c_size_t,
                                          ctypes.POINTER(ctypes.c_size_t)]
        k32.ReadProcessMemory.restype = wt.BOOL

        def read_mem(h, addr, size):
            buf = (ctypes.c_ubyte * size)()
            br = ctypes.c_size_t(0)
            if k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(br)):
                return bytes(buf[:br.value])
            return None

        pids = self._find_trae_pids(k32, PROCESSENTRY32)
        if not pids:
            report("no_process", "没有找到正在运行的 TRAE 进程")
            return None

        report("processes", f"发现 {len(pids)} 个 TRAE 相关进程，开始有界扫描...", pids=pids)

        try:
            with open(self.encrypted_db_path, "rb") as f:
                page1 = f.read(PAGE_SIZE)
        except OSError:
            return None
        if len(page1) < PAGE_SIZE:
            return None

        enc_data_1024 = page1[16:16 + 1024]
        iv = page1[IV_OFFSET:IV_OFFSET + 16]

        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            return None

        def test_key(candidate: bytes) -> bool:
            if not candidate or len(candidate) != 32:
                return False
            try:
                c = Cipher(algorithms.AES(candidate), modes.CBC(iv), backend=default_backend())
                d = c.decryptor()
                dec = d.update(enc_data_1024) + d.finalize()
                ps = struct.unpack(">H", dec[:2])[0]
                if ps == 1:
                    ps = 65536
                return ps == PAGE_SIZE and dec[4] == RESERVE and dec[5] == 64 and dec[6] == 32 and dec[7] == 32
            except Exception:
                return False

        def is_high_entropy(data: bytes) -> bool:
            if len(data) != 32:
                return False
            if data[0] == data[1] == data[2] == data[3]:
                return False
            if all(0x20 <= b <= 0x7E for b in data[:16]):
                return False
            if data.count(0) > 28 or data.count(0xFF) > 28:
                return False
            return len(set(data[:16])) >= 8

        mbi = MEMORY_BASIC_INFORMATION()
        mbi_size = ctypes.sizeof(mbi)
        start_time = time.monotonic()
        total_scanned = 0
        last_report_mb = -1
        min_addr = 0x10000
        chunk_size = 1 * 1024 * 1024

        for pid in pids:
            if time.monotonic() - start_time > MEMORY_SCAN_TIMEOUT_SEC or total_scanned >= MEMORY_SCAN_MAX_BYTES:
                break

            report("scan_pid", f"正在扫描进程 PID {pid}...", pid=pid)
            h_process = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
            if not h_process:
                continue

            try:
                address = min_addr
                while address < 0x7FFFFFFFFFFF:
                    if time.monotonic() - start_time > MEMORY_SCAN_TIMEOUT_SEC or total_scanned >= MEMORY_SCAN_MAX_BYTES:
                        break

                    ret = k32.VirtualQueryEx(h_process, ctypes.c_void_p(address), ctypes.byref(mbi), mbi_size)
                    if ret == 0:
                        break

                    base = mbi.BaseAddress or 0
                    size = mbi.RegionSize or 0
                    if size == 0:
                        break

                    protect = int(mbi.Protect) & 0xFF
                    is_guarded = bool(int(mbi.Protect) & PAGE_GUARD)
                    is_private = int(mbi.Type) == MEM_PRIVATE
                    is_committed = int(mbi.State) == MEM_COMMIT
                    is_readable = protect in READABLE

                    if is_committed and is_readable and is_private and not is_guarded:
                        for off in range(0, size, chunk_size):
                            if time.monotonic() - start_time > MEMORY_SCAN_TIMEOUT_SEC or total_scanned >= MEMORY_SCAN_MAX_BYTES:
                                break

                            rs = min(chunk_size, size - off)
                            data = read_mem(h_process, base + off, rs)
                            if not data:
                                continue
                            total_scanned += len(data)
                            scanned_mb = total_scanned // (1024 * 1024)
                            if scanned_mb // 16 != last_report_mb // 16:
                                last_report_mb = scanned_mb
                                report("progress", f"已扫描约 {scanned_mb}MB，继续查找密钥...", scanned_mb=scanned_mb)

                            # 兼容 64 字符 hex key。
                            for match in re.finditer(rb"[0-9a-fA-F]{64}", data):
                                candidate = self._parse_key_hex(match.group(0).decode("ascii", errors="ignore"))
                                if candidate and test_key(candidate):
                                    report("found", "已找到 TRAE 密钥")
                                    return candidate

                            # 兼容 32B raw key。
                            for o in range(0, len(data) - 32, 16):
                                candidate = data[o:o + 32]
                                if is_high_entropy(candidate) and test_key(candidate):
                                    report("found", "已找到 TRAE 密钥")
                                    return candidate

                    nxt = base + size
                    if nxt <= address:
                        break
                    address = nxt
            finally:
                k32.CloseHandle(h_process)

        report("not_found", "扫描结束，未找到可用密钥")
        return None

    @staticmethod
    def _find_trae_pids(k32, process_entry_type) -> List[int]:
        TH32CS_SNAPPROCESS = 0x00000002
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return []

        pe = process_entry_type()
        pe.dwSize = ctypes.sizeof(process_entry_type)
        pids: List[int] = []
        try:
            if k32.Process32First(snap, ctypes.byref(pe)):
                while True:
                    name = pe.szExeFile.decode("utf-8", errors="replace").lower()
                    if "trae" in name:
                        pids.append(int(pe.th32ProcessID))
                    if not k32.Process32Next(snap, ctypes.byref(pe)):
                        break
        finally:
            k32.CloseHandle(snap)
        return pids

    # ========== 数据库查询 ==========

    def list_conversations(self) -> List[Conversation]:
        if self._cached_conversations is not None:
            return self._cached_conversations

        if not self.detect():
            return []

        db_path = self._get_decrypted_db_path()
        if db_path:
            conversations = self._get_conversations_from_db(db_path)
            if conversations:
                self._cached_conversations = conversations
                return conversations

        conversations = self._list_conversations_from_logs()
        self._cached_conversations = conversations
        return conversations

    def _get_conversations_from_db(self, db_path: str) -> List[Conversation]:
        conversations: List[Conversation] = []
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
                LEFT JOIN chat_message m ON s.session_id = m.session_id
                    AND (m.deleted_at = 0 OR m.deleted_at IS NULL)
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
                    },
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
                    c.metadata = c.metadata or {}
                    c.metadata["msg_count"] = len(c.messages)
                return c
        return None

    def _get_messages_from_db(self, db_path: str, session_id: str) -> List[Message]:
        messages: List[Message] = []
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

                model = None
                if row["user_message_context"]:
                    try:
                        ctx = json.loads(row["user_message_context"])
                        model = ctx.get("model_info", {}).get("model_name")
                    except Exception:
                        pass

                # 完整角色映射，不再简单二元化（避免 system 消息被误判为 AI 回复）。
                if msg_role == "user":
                    role = Role.USER
                elif msg_role in ("assistant", "ai", "bot", "model"):
                    role = Role.ASSISTANT
                elif msg_role in ("system", "developer"):
                    role = Role.SYSTEM
                else:
                    role = Role.ASSISTANT

                if msg_type == "general":
                    cursor.execute("""
                        SELECT content FROM chat_message_general
                        WHERE message_id = ? AND (deleted_at = 0 OR deleted_at IS NULL)
                    """, (msg_id,))
                    gen_row = cursor.fetchone()
                    if gen_row:
                        parts = self._parse_general_content(gen_row["content"])
                        text = "\n".join(p.content for p in parts if p.type == MessagePartType.TEXT and p.content)
                        messages.append(Message(
                            role=role,
                            content=text,
                            timestamp=ts,
                            message_id=msg_id,
                            parts=parts,
                            model=model,
                        ))

                elif msg_type == "task":
                    cursor.execute("""
                        SELECT content, summary FROM chat_message_task
                        WHERE message_id = ? AND (deleted_at = 0 OR deleted_at IS NULL)
                    """, (msg_id,))
                    task_row = cursor.fetchone()
                    if task_row:
                        parts = self._parse_task_content(task_row["content"])
                        # content 统一为 parts 中 TEXT parts 的换行连接，不再用 summary 替代。
                        # summary 仅用于对话列表标题候选，不进入 Message.content。
                        text_parts = [p.content for p in parts if p.type == MessagePartType.TEXT and p.content]
                        text = "\n".join(text_parts) if text_parts else ""
                        messages.append(Message(
                            role=role,
                            content=text,
                            timestamp=ts,
                            message_id=msg_id,
                            parts=parts,
                            model=model,
                        ))
                else:
                    for subtable in ["chat_message_general", "chat_message_task"]:
                        cursor.execute(f"""
                            SELECT content FROM {subtable}
                            WHERE message_id = ? AND (deleted_at = 0 OR deleted_at IS NULL)
                        """, (msg_id,))
                        sub_row = cursor.fetchone()
                        if sub_row:
                            raw = sub_row["content"] or ""
                            parts = [MessagePart(type=MessagePartType.TEXT, content=raw)]
                            messages.append(Message(
                                role=role,
                                content=raw,
                                timestamp=ts,
                                message_id=msg_id,
                                parts=parts,
                                model=model,
                            ))
                            break
        except Exception:
            pass
        finally:
            if conn:
                conn.close()
        return messages

    def _parse_general_content(self, content_json: str) -> List[MessagePart]:
        parts: List[MessagePart] = []
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
        parts: List[MessagePart] = []
        try:
            data = json.loads(content_json)
            msgs = data.get("messages", []) if isinstance(data, dict) else []
            for msg in msgs:
                if not isinstance(msg, dict):
                    continue
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
                        # 合并所有 query 元素，不仅取第一个（避免丢失多段输入）。
                        text = "\n".join(str(q) for q in query if q)
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
                        content=str(tout),
                        tool_output=str(tout),
                    ))
        except Exception:
            if content_json:
                parts.append(MessagePart(type=MessagePartType.TEXT, content=content_json))
        return parts

    def _extract_plan_item_parts(self, plan: dict, parts: List[MessagePart]):
        thought = plan.get("thought", "") or ""
        reasoning = plan.get("reasoning_content", "") or ""
        if reasoning:
            parts.append(MessagePart(type=MessagePartType.THINKING, content=reasoning))
        elif thought:
            parts.append(MessagePart(type=MessagePartType.THINKING, content=thought))

        tci = plan.get("tool_call_info")
        if isinstance(tci, dict):
            tname = tci.get("name", "") or ""
            tparams = tci.get("params", {})
            tparams_str = json.dumps(tparams, ensure_ascii=False, indent=2) if isinstance(tparams, dict) else str(tparams)
            if tname:
                parts.append(MessagePart(
                    type=MessagePartType.TOOL_CALL,
                    content=f"调用工具: {tname}",
                    tool_name=tname,
                    tool_input=tparams_str,
                ))

            tresult = tci.get("result")
            if isinstance(tresult, dict):
                status = tresult.get("status", "")
                rdata = tresult.get("data", {})
                err = tresult.get("error_message", "")
                if err:
                    content = f"[{status}] {err}"
                elif isinstance(rdata, (dict, list)) and rdata:
                    content = json.dumps(rdata, ensure_ascii=False, indent=2)
                else:
                    content = f"[{status}]" if status else ""
                if content:
                    parts.append(MessagePart(
                        type=MessagePartType.TOOL_RESULT,
                        content=content,
                        tool_output=json.dumps(tresult, ensure_ascii=False),
                    ))

    # ========== 日志解析（快速回退方案） ==========

    def _list_conversations_from_logs(self) -> List[Conversation]:
        conversations: List[Conversation] = []
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
                    metadata={"msg_count": len(msgs)},
                ))

        conversations.sort(key=lambda c: c.updated_at or datetime.min, reverse=True)
        return conversations

    def _get_conversations_from_state(self) -> List[Conversation]:
        conversations: List[Conversation] = []
        if not os.path.exists(self.state_db_path):
            return conversations

        conn = None
        try:
            conn = sqlite3.connect(f"file:{self.state_db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r["name"] for r in cursor.fetchall()]

            if "ItemTable" in tables:
                cursor.execute(
                    "SELECT key, value FROM ItemTable "
                    "WHERE key LIKE '%session%' OR key LIKE '%chat%' OR key LIKE '%conversation%' LIMIT 5000"
                )
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
            self._ts_to_dt(created, ms=True) if isinstance(created, (int, float)) else None
        )
        updated_dt = self._parse_iso_dt(updated) if isinstance(updated, str) else (
            self._ts_to_dt(updated, ms=True) if isinstance(updated, (int, float)) else None
        )

        return Conversation(
            id=str(conv_id),
            title=title or f"对话 {str(conv_id)[:8]}...",
            created_at=created_dt,
            updated_at=updated_dt,
            source_app=self.display_name,
            metadata={"state_key": key},
        )

    def _parse_logs_for_messages(self):
        if self._conversations_from_logs:
            return

        log_files = []
        if os.path.exists(self.logs_dir):
            for root, _dirs, files in os.walk(self.logs_dir):
                for f in files:
                    if "stdout" in f.lower() and f.endswith(".log"):
                        log_files.append(os.path.join(root, f))

        log_files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
        session_messages: Dict[str, List[Message]] = {}

        for log_path in log_files[:LOG_MAX_FILES]:
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
                        parts=[MessagePart(type=MessagePartType.TEXT, content=content)],
                    )
                    session_messages.setdefault(session_id, []).append(msg)
            current_role = None
            current_content_lines = []
            current_timestamp = None

        file_size = os.path.getsize(log_path)
        read_start = max(0, file_size - LOG_TAIL_BYTES)

        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            if read_start > 0:
                f.seek(read_start)
                f.readline()

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

                lower = line.lower()
                if "[user" in lower or "user message" in lower:
                    flush()
                    current_role = "user"
                elif "[assistant" in lower or "assistant message" in lower:
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

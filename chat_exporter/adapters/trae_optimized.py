"""Hardened TRAE adapter used by the modern GUI.

It keeps the proven database parser from ``trae.py`` and replaces only the
sensitive/key-discovery layer: explicit-by-default scanning, stable encrypted
cache, faster candidate validation, cancellation and safer environment writes.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import struct
import threading
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .trae import (
    IV_OFFSET,
    MEMORY_SCAN_ENV,
    MEMORY_SCAN_MAX_BYTES,
    MEMORY_SCAN_TIMEOUT_SEC,
    PAGE_SIZE,
    RESERVE,
    SQLCIPHER_KEY_ENV,
    TraeAdapter as BaseTraeAdapter,
)

ProgressCallback = Optional[Callable[[Dict[str, Any]], None]]


class TraeAdapter(BaseTraeAdapter):
    """Drop-in replacement with a safer and faster key assistant."""

    CACHE_VERSION = 2
    RAW_SCAN_MAX_BYTES = 64 * 1024 * 1024
    HEX_PATTERN = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")
    UTF16_HEX_PATTERN = re.compile(rb"(?:(?:[0-9A-Fa-f])\x00){64}")
    CONTEXT_WORDS: Sequence[bytes] = (
        b"sqlcipher",
        b"cipher_key",
        b"database.db",
        b"encryptionkey",
        b"encryption_key",
    )

    def _memory_scan_enabled(self) -> bool:
        # Explicit GUI button is the default. Command-line users may opt in.
        return self._env_truthy(MEMORY_SCAN_ENV, default=False)

    # ========== Stable local cache ==========

    def _cache_scope(self) -> str:
        normalized = os.path.normcase(os.path.abspath(self.encrypted_db_path))
        return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()

    @staticmethod
    def _dpapi_protect(data: bytes) -> Optional[bytes]:
        if os.name != "nt" or not data:
            return None
        try:
            import ctypes.wintypes as wt

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

            crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            crypt32.CryptProtectData.argtypes = [
                ctypes.POINTER(DATA_BLOB), ctypes.c_wchar_p, ctypes.POINTER(DATA_BLOB),
                ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB),
            ]
            crypt32.CryptProtectData.restype = wt.BOOL
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p

            raw = ctypes.create_string_buffer(data)
            in_blob = DATA_BLOB(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_byte)))
            out_blob = DATA_BLOB()
            ok = crypt32.CryptProtectData(
                ctypes.byref(in_blob), "ChatExporter TRAE key", None,
                None, None, 0, ctypes.byref(out_blob),
            )
            if not ok:
                return None
            try:
                return ctypes.string_at(out_blob.pbData, out_blob.cbData)
            finally:
                kernel32.LocalFree(out_blob.pbData)
        except Exception:
            return None

    @staticmethod
    def _dpapi_unprotect(data: bytes) -> Optional[bytes]:
        if os.name != "nt" or not data:
            return None
        try:
            import ctypes.wintypes as wt

            class DATA_BLOB(ctypes.Structure):
                _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

            crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            crypt32.CryptUnprotectData.argtypes = [
                ctypes.POINTER(DATA_BLOB), ctypes.POINTER(ctypes.c_wchar_p), ctypes.POINTER(DATA_BLOB),
                ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(DATA_BLOB),
            ]
            crypt32.CryptUnprotectData.restype = wt.BOOL
            kernel32.LocalFree.argtypes = [ctypes.c_void_p]
            kernel32.LocalFree.restype = ctypes.c_void_p

            raw = ctypes.create_string_buffer(data)
            in_blob = DATA_BLOB(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_byte)))
            out_blob = DATA_BLOB()
            description = ctypes.c_wchar_p()
            ok = crypt32.CryptUnprotectData(
                ctypes.byref(in_blob), ctypes.byref(description), None,
                None, None, 0, ctypes.byref(out_blob),
            )
            if not ok:
                return None
            try:
                return ctypes.string_at(out_blob.pbData, out_blob.cbData)
            finally:
                kernel32.LocalFree(out_blob.pbData)
                if description:
                    kernel32.LocalFree(description)
        except Exception:
            return None

    def _load_cached_key(self) -> Optional[bytes]:
        if not self._key_cache_enabled():
            return None
        path = self._get_key_cache_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return None

        version = payload.get("version")
        if version == self.CACHE_VERSION:
            if payload.get("scope") != self._cache_scope():
                return None
            protected = payload.get("protected_key")
            if protected:
                try:
                    cipher = base64.b64decode(protected, validate=True)
                except Exception:
                    return None
                key = self._dpapi_unprotect(cipher)
                return key if key and len(key) == 32 else None
            return self._parse_key_hex(payload.get("key_hex"))

        # Backward-compatible migration from v1. The caller validates the key
        # against the current encrypted DB before it is trusted.
        if version == 1:
            return self._parse_key_hex(payload.get("key_hex"))
        return None

    def _save_key_cache(self, key: bytes):
        if not self._key_cache_enabled() or not key or len(key) != 32:
            return
        path = self._get_key_cache_path()
        payload: Dict[str, Any] = {
            "version": self.CACHE_VERSION,
            "scope": self._cache_scope(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        protected = self._dpapi_protect(key)
        if protected:
            payload["protected_key"] = base64.b64encode(protected).decode("ascii")
            payload["protection"] = "windows-dpapi"
        else:
            payload["key_hex"] = key.hex()
            payload["protection"] = "filesystem-permissions"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        except Exception:
            pass

    def persist_key_to_user_environment(self, key_hex: str) -> Tuple[bool, str]:
        key = self._parse_key_hex(key_hex)
        if not key:
            return False, "密钥格式无效"
        os.environ[SQLCIPHER_KEY_ENV] = key.hex()
        if os.name != "nt":
            return True, "已写入当前进程；请手动加入 shell profile 以便下次启动生效。"
        try:
            import winreg
            import ctypes.wintypes as wt

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as reg:
                winreg.SetValueEx(reg, SQLCIPHER_KEY_ENV, 0, winreg.REG_SZ, key.hex())

            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            result = wt.DWORD()
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
                SMTO_ABORTIFHUNG, 3000, ctypes.byref(result),
            )
            return True, "已安全写入当前用户环境变量。新启动的程序会自动读取。"
        except Exception as exc:
            return False, f"写入用户环境变量失败：{exc}"

    # ========== Explicit assistant ==========

    def extract_key_for_user(
        self,
        progress_callback: ProgressCallback = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Dict[str, Any]:
        started = time.monotonic()

        def report(stage: str, message: str, **extra):
            if progress_callback:
                payload = {"stage": stage, "message": message, **extra}
                try:
                    progress_callback(payload)
                except Exception:
                    pass

        if not os.path.exists(self.encrypted_db_path):
            return self._failure("TRAE 加密数据库不存在", self.encrypted_db_path, started)

        env_key = self._load_env_key()
        if env_key and self._validate_key(env_key):
            self._save_key_cache(env_key)
            return self._success(env_key, "环境变量", started)

        cached = self._load_cached_key()
        if cached and self._validate_key(cached):
            # Migrates old plaintext v1 caches to the new protected format.
            self._save_key_cache(cached)
            return self._success(cached, "安全缓存", started)
        if cached:
            self._delete_key_cache()

        if os.name != "nt":
            return self._failure("自动内存扫描目前只支持 Windows", "可手动设置 TRAE_SQLCIPHER_KEY。", started)

        report("prepare", "正在检查 TRAE 进程与数据库状态...")
        key = self._extract_key_from_memory(progress_callback, cancel_event)
        if cancel_event and cancel_event.is_set():
            return self._failure("扫描已取消", "未保存任何新密钥。", started, cancelled=True)
        if key and self._validate_key(key):
            self._save_key_cache(key)
            return self._success(key, "进程内存", started)
        return self._failure(
            "未找到可用密钥",
            "请让 TRAE SOLO CN 保持运行并打开一个对话，然后重试。也可以手动设置 TRAE_SQLCIPHER_KEY。",
            started,
        )

    @staticmethod
    def _success(key: bytes, source: str, started: float) -> Dict[str, Any]:
        return {
            "ok": True,
            "key_hex": key.hex(),
            "source": source,
            "elapsed": round(time.monotonic() - started, 3),
        }

    @staticmethod
    def _failure(reason: str, hint: str, started: float, cancelled: bool = False) -> Dict[str, Any]:
        return {
            "ok": False,
            "reason": reason,
            "hint": hint,
            "cancelled": cancelled,
            "elapsed": round(time.monotonic() - started, 3),
        }

    # ========== Faster two-pass memory scan ==========

    @staticmethod
    def _fast_key_validator(page1: bytes):
        first_block = page1[16:32]
        iv = page1[IV_OFFSET:IV_OFFSET + 16]
        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        except ImportError:
            return lambda _candidate: False

        tested = set()

        def validate(candidate: bytes) -> bool:
            if not candidate or len(candidate) != 32 or candidate in tested:
                return False
            tested.add(candidate)
            try:
                cipher = Cipher(algorithms.AES(candidate), modes.CBC(iv), backend=default_backend())
                dec = cipher.decryptor().update(first_block)
                ps = struct.unpack(">H", dec[:2])[0]
                if ps == 1:
                    ps = 65536
                return (
                    ps == PAGE_SIZE
                    and dec[4] == RESERVE
                    and dec[5] == 64
                    and dec[6] == 32
                    and dec[7] == 32
                )
            except Exception:
                return False

        return validate

    def _extract_key_from_memory(
        self,
        progress_callback: ProgressCallback = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> Optional[bytes]:
        if os.name != "nt":
            return None
        try:
            import ctypes.wintypes as wt
        except ImportError:
            return None

        def cancelled() -> bool:
            return bool(cancel_event and cancel_event.is_set())

        def report(stage: str, message: str, **extra):
            if progress_callback:
                try:
                    progress_callback({"stage": stage, "message": message, **extra})
                except Exception:
                    pass

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        MEM_COMMIT = 0x1000
        MEM_PRIVATE = 0x20000
        PAGE_READWRITE = 0x04
        PAGE_WRITECOPY = 0x08
        PAGE_EXECUTE_READWRITE = 0x40
        PAGE_EXECUTE_WRITECOPY = 0x80
        PAGE_GUARD = 0x100
        WRITABLE = {PAGE_READWRITE, PAGE_WRITECOPY, PAGE_EXECUTE_READWRITE, PAGE_EXECUTE_WRITECOPY}

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
                ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        k32.Process32First.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        k32.Process32First.restype = wt.BOOL
        k32.Process32Next.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
        k32.Process32Next.restype = wt.BOOL
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.OpenProcess.restype = wt.HANDLE
        k32.CloseHandle.argtypes = [wt.HANDLE]
        k32.CloseHandle.restype = wt.BOOL
        k32.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]
        k32.VirtualQueryEx.restype = ctypes.c_size_t
        k32.ReadProcessMemory.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        k32.ReadProcessMemory.restype = wt.BOOL

        processes = self._find_prioritized_processes(k32, PROCESSENTRY32)
        if not processes:
            report("no_process", "没有找到正在运行的 TRAE 进程")
            return None
        report("processes", f"发现 {len(processes)} 个 TRAE 进程，优先扫描主进程", processes=processes)

        try:
            with open(self.encrypted_db_path, "rb") as f:
                page1 = f.read(PAGE_SIZE)
        except OSError:
            return None
        if len(page1) < PAGE_SIZE:
            return None
        validate = self._fast_key_validator(page1)

        start = time.monotonic()
        string_scanned = 0
        raw_scanned = 0
        chunk_size = 1024 * 1024
        mbi = MEMORY_BASIC_INFORMATION()
        mbi_size = ctypes.sizeof(mbi)

        def read_mem(handle, address: int, size: int) -> Optional[bytes]:
            buf = (ctypes.c_ubyte * size)()
            read = ctypes.c_size_t()
            if k32.ReadProcessMemory(handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)):
                return bytes(buf[:read.value])
            return None

        def iter_regions(handle):
            address = 0x10000
            while address < 0x7FFFFFFFFFFF:
                if cancelled() or time.monotonic() - start >= MEMORY_SCAN_TIMEOUT_SEC:
                    return
                if not k32.VirtualQueryEx(handle, ctypes.c_void_p(address), ctypes.byref(mbi), mbi_size):
                    return
                base = int(mbi.BaseAddress or 0)
                size = int(mbi.RegionSize or 0)
                if size <= 0:
                    return
                protect = int(mbi.Protect)
                if (
                    int(mbi.State) == MEM_COMMIT
                    and int(mbi.Type) == MEM_PRIVATE
                    and (protect & 0xFF) in WRITABLE
                    and not (protect & PAGE_GUARD)
                ):
                    yield base, size
                next_address = base + size
                if next_address <= address:
                    return
                address = next_address

        def read_process_memory(handle, base: int, size: int) -> Optional[bytes]:
            return read_mem(handle, base, size)

        # Pass 1: strings first. It is dramatically faster and matches the
        # common Electron/Node representation (ASCII or UTF-16 hex string).
        report("fast_pass", "快速扫描：查找 ASCII / UTF-16 密钥字符串...")
        for proc in processes:
            if cancelled() or time.monotonic() - start >= MEMORY_SCAN_TIMEOUT_SEC:
                break
            handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, proc["pid"])
            if not handle:
                continue
            try:
                for base, size in iter_regions(handle):
                    for offset in range(0, size, chunk_size):
                        if cancelled() or time.monotonic() - start >= MEMORY_SCAN_TIMEOUT_SEC:
                            break
                        if string_scanned >= MEMORY_SCAN_MAX_BYTES:
                            break
                        data = read_process_memory(handle, base + offset, min(chunk_size, size - offset))
                        if not data:
                            continue
                        string_scanned += len(data)
                        for match in self.HEX_PATTERN.finditer(data):
                            candidate = self._parse_key_hex(match.group(0).decode("ascii"))
                            if candidate and validate(candidate):
                                report("found", "快速扫描已找到 TRAE 密钥", source="ascii-hex")
                                return candidate
                        for match in self.UTF16_HEX_PATTERN.finditer(data):
                            compact = match.group(0)[::2].decode("ascii", errors="ignore")
                            candidate = self._parse_key_hex(compact)
                            if candidate and validate(candidate):
                                report("found", "快速扫描已找到 TRAE 密钥", source="utf16-hex")
                                return candidate
                        if string_scanned % (32 * 1024 * 1024) < len(data):
                            report("progress", f"快速扫描 {string_scanned // 1024 // 1024}MB", scanned_mb=string_scanned // 1024 // 1024)
            finally:
                k32.CloseHandle(handle)

        if cancelled():
            return None

        # Pass 2: targeted raw-buffer fallback. Context windows are tested
        # first; a bounded aligned scan is used only for smaller private areas.
        report("raw_pass", "深度扫描：检查可能的 32 字节原始密钥缓冲区...")
        for proc in processes:
            if cancelled() or time.monotonic() - start >= MEMORY_SCAN_TIMEOUT_SEC:
                break
            handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, proc["pid"])
            if not handle:
                continue
            try:
                for base, size in iter_regions(handle):
                    if raw_scanned >= self.RAW_SCAN_MAX_BYTES:
                        break
                    for offset in range(0, size, chunk_size):
                        if cancelled() or time.monotonic() - start >= MEMORY_SCAN_TIMEOUT_SEC:
                            break
                        if raw_scanned >= self.RAW_SCAN_MAX_BYTES:
                            break
                        data = read_process_memory(handle, base + offset, min(chunk_size, size - offset))
                        if not data:
                            continue
                        raw_scanned += len(data)

                        windows: List[Tuple[int, int]] = []
                        lower = data.lower()
                        for word in self.CONTEXT_WORDS:
                            cursor = 0
                            while True:
                                pos = lower.find(word, cursor)
                                if pos < 0:
                                    break
                                windows.append((max(0, pos - 512), min(len(data), pos + len(word) + 512)))
                                cursor = pos + len(word)

                        # Context candidates may be unaligned.
                        for lo, hi in windows:
                            for index in range(lo, max(lo, hi - 31), 4):
                                candidate = data[index:index + 32]
                                if validate(candidate):
                                    report("found", "深度扫描已找到 TRAE 密钥", source="context-buffer")
                                    return candidate

                        # Bounded aligned fallback. First-block validation is
                        # cheap enough here and total raw bytes are capped.
                        for index in range(0, len(data) - 31, 16):
                            candidate = data[index:index + 32]
                            if candidate.count(0) > 24 or candidate.count(0xFF) > 24:
                                continue
                            if validate(candidate):
                                report("found", "深度扫描已找到 TRAE 密钥", source="aligned-buffer")
                                return candidate

                        if raw_scanned % (16 * 1024 * 1024) < len(data):
                            report("progress", f"深度扫描 {raw_scanned // 1024 // 1024}MB", scanned_mb=raw_scanned // 1024 // 1024)
            finally:
                k32.CloseHandle(handle)

        report("not_found", "扫描完成，没有找到可验证的密钥")
        return None

    @staticmethod
    def _find_prioritized_processes(k32, process_entry_type) -> List[Dict[str, Any]]:
        TH32CS_SNAPPROCESS = 0x00000002
        snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            return []
        entry = process_entry_type()
        entry.dwSize = ctypes.sizeof(process_entry_type)
        items: List[Dict[str, Any]] = []
        try:
            if k32.Process32First(snapshot, ctypes.byref(entry)):
                while True:
                    name = entry.szExeFile.decode("utf-8", errors="replace")
                    lower = name.lower()
                    if "trae" in lower:
                        exact = lower in {"trae.exe", "trae solo cn.exe"}
                        items.append({"pid": int(entry.th32ProcessID), "name": name, "priority": 0 if exact else 1})
                    if not k32.Process32Next(snapshot, ctypes.byref(entry)):
                        break
        finally:
            k32.CloseHandle(snapshot)
        items.sort(key=lambda item: (item["priority"], item["name"].lower(), item["pid"]))
        return items

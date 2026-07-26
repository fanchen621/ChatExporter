"""用户偏好持久化（%LOCALAPPDATA%/ChatExporter/settings.json）。

三条硬约束：
1. 只存界面偏好，绝不存对话内容或任何凭据——这个文件是明文的，
   而本工具读的是用户全机的聊天记录。因此 load/save 都按白名单过滤，
   未知 key 直接丢弃，字符串超长直接拒绝。
2. 写入用「临时文件 + os.replace」，断电或崩溃不会留下半截 JSON
   让下次启动读不出来。
3. 任何 IO 或解析失败都只降级为默认值，绝不抛异常。偏好数据不值得
   让 GUI 崩在启动那一步。

刻意不 import 本包的其他模块：它是叶子模块，GUI 启动最早就要用它，
任何额外依赖都可能把「读偏好」变成一条会失败的启动路径。
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

APP_DIR_NAME = "ChatExporter"
SETTINGS_FILENAME = "settings.json"

#: 全部可持久化的 key 与默认值。不在这里的 key 不会被读也不会被写。
DEFAULTS: Dict[str, Any] = {
    "window_geometry": "",              # "1280x800+120+60"
    "last_source": "",                  # 上次选中的来源程序名
    "last_export_dir": "",              # 上次的导出目录
    "export_format": "markdown",        # exporters.EXPORTERS 的 key
    "theme": "system",                  # light / dark / system
    "open_folder_after_export": True,
    "include_thinking": True,
    "include_tool_messages": True,
}

KNOWN_KEYS = frozenset(DEFAULTS)
THEMES = ("light", "dark", "system")

# 偏好值不该有长文本。这条上限同时是「别把对话内容写进来」的兜底。
MAX_STRING_LENGTH = 4096

# tk 的 geometry() 拿到畸形字符串会抛 TclError 直接崩在启动阶段，
# 所以宁可判定非法回退成空串，也不要把脏值原样交给 tk。
_GEOMETRY_RE = re.compile(r"^\d{1,5}x\d{1,5}([+-]-?\d{1,5}[+-]-?\d{1,5})?$")


def storage_dir() -> Path:
    """偏好与缓存的公共目录。每次调用都重新读环境变量，测试可 monkeypatch。"""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )
    return Path(base) / APP_DIR_NAME


def settings_path() -> Path:
    return storage_dir() / SETTINGS_FILENAME


def _coerce(key: str, value: Any) -> Optional[Any]:
    """把外部值收敛成合法偏好值；不合法返回 None（调用方用默认值）。"""
    default = DEFAULTS.get(key)

    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        return None

    if key == "theme":
        return value if isinstance(value, str) and value in THEMES else None

    if key == "window_geometry":
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return ""
        return value if _GEOMETRY_RE.match(value) else None

    if isinstance(default, str):
        if not isinstance(value, str):
            return None
        return value if len(value) <= MAX_STRING_LENGTH else None

    return None


def _sanitize(raw: Mapping[str, Any]) -> Dict[str, Any]:
    values = dict(DEFAULTS)
    if not isinstance(raw, Mapping):
        return values
    for key in KNOWN_KEYS:
        if key not in raw:
            continue
        coerced = _coerce(key, raw[key])
        if coerced is not None:
            values[key] = coerced
    return values


class Settings:
    """一份偏好。默认单例走 %LOCALAPPDATA%，测试传 path 即可隔离。"""

    def __init__(self, path: Optional[os.PathLike | str] = None):
        self._explicit_path = Path(path) if path else None
        self._lock = threading.RLock()
        self._values: Optional[Dict[str, Any]] = None

    @property
    def path(self) -> Path:
        # 不缓存：单例在 import 时就建好了，而测试要在之后才 monkeypatch 环境变量。
        return self._explicit_path if self._explicit_path else settings_path()

    # ---- 读写 ----

    def load(self, force: bool = False) -> Dict[str, Any]:
        with self._lock:
            if self._values is not None and not force:
                return dict(self._values)
            self._values = self._read()
            return dict(self._values)

    def invalidate(self) -> None:
        """丢掉内存里的副本，下次 load 重新读盘。"""
        with self._lock:
            self._values = None

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return dict(DEFAULTS)
        except (OSError, ValueError, UnicodeDecodeError):
            # 文件损坏（半截 JSON / 编码错乱 / 权限）不该阻断启动，用默认值继续。
            return dict(DEFAULTS)
        return _sanitize(raw if isinstance(raw, Mapping) else {})

    def save(self) -> bool:
        """原子写盘，成功返回 True；任何失败返回 False 且不抛。"""
        with self._lock:
            values = _sanitize(self._values if self._values is not None else DEFAULTS)
            self._values = values
            payload = json.dumps(values, ensure_ascii=False, indent=2, sort_keys=True)

        target = self.path
        tmp = target.parent / f"{target.name}.{os.getpid()}.tmp"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(tmp, target)
            return True
        except Exception:
            try:
                if tmp.exists():
                    os.remove(tmp)
            except OSError:
                pass
            return False

    # ---- 访问 ----

    def get(self, key: str, default: Any = None) -> Any:
        values = self.load()
        if key in values:
            return values[key]
        return default if default is not None else DEFAULTS.get(key)

    def set(self, key: str, value: Any, autosave: bool = True) -> bool:
        """写入一个已知 key。未知 key 或非法值返回 False，不抛也不落盘。"""
        if key not in KNOWN_KEYS:
            return False
        coerced = _coerce(key, value)
        if coerced is None:
            return False
        with self._lock:
            if self._values is None:
                self._values = self._read()
            self._values[key] = coerced
        if autosave:
            self.save()
        return True

    def update(self, values: Mapping[str, Any], autosave: bool = True) -> int:
        """批量写入，返回真正被接受的条数。窗口关闭时一次性落盘用这个。"""
        accepted = 0
        for key, value in dict(values).items():
            if self.set(key, value, autosave=False):
                accepted += 1
        if autosave and accepted:
            self.save()
        return accepted

    def reset(self, autosave: bool = True) -> None:
        with self._lock:
            self._values = dict(DEFAULTS)
        if autosave:
            self.save()

    def as_dict(self) -> Dict[str, Any]:
        return self.load()


_default_settings = Settings()


def get_settings() -> Settings:
    return _default_settings


def load(force: bool = False) -> Dict[str, Any]:
    return _default_settings.load(force=force)


def save() -> bool:
    return _default_settings.save()


def get(key: str, default: Any = None) -> Any:
    return _default_settings.get(key, default)


def set(key: str, value: Any, autosave: bool = True) -> bool:  # noqa: A001 - GUI 侧要的就是这个名字
    return _default_settings.set(key, value, autosave=autosave)


def update(values: Mapping[str, Any], autosave: bool = True) -> int:
    return _default_settings.update(values, autosave=autosave)


def reset(autosave: bool = True) -> None:
    _default_settings.reset(autosave=autosave)

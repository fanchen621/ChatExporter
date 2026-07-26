"""全文检索的磁盘缓存（%LOCALAPPDATA%/ChatExporter/search_index.db）。

为什么需要它：本机实测一个 TRAE 源就是 21 条对话、约 40MB 正文，
单条对话可达 1250 万字符。现在每次启动搜索都要把这些从各家数据库里
重新读出来、重新清洗、再整份常驻内存——第一次搜索要等十几秒，
之后内存里躺着几十 MB 永不释放的字符串。

这里用 sqlite（标准库，无新依赖）把清洗后的检索文本落盘：
- 键 = (source, conversation_id)，附一个新鲜度戳；戳不一致就当没命中，
  重新构建再覆盖，绝不把旧文本当现状交出去。
- 总量上限 200MB：本机最大的来源约 40MB，200MB 能同时装下四五个来源
  的全量正文，又不至于在系统盘上无声膨胀。超限按写入时间淘汰最旧的。
- 缓存永远是可选项：数据库打不开、磁盘满、表损坏，一律降级成空操作，
  搜索退回「每次重算」的老路径，绝不因为缓存故障让搜索报错。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .models import Conversation
from .preview_utils import conversation_search_text
from .settings import storage_dir

INDEX_FILENAME = "search_index.db"

#: 缓存总量上限（字节）。见模块 docstring 里 200MB 的取舍依据。
MAX_TOTAL_BYTES = 200 * 1024 * 1024

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    source     TEXT NOT NULL,
    conv_id    TEXT NOT NULL,
    stamp      TEXT NOT NULL,
    text       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (source, conv_id)
);
CREATE INDEX IF NOT EXISTS idx_entries_updated ON entries(updated_at);
"""


def index_path() -> Path:
    return storage_dir() / INDEX_FILENAME


def conversation_stamp(conv: Conversation) -> str:
    """新鲜度戳。

    必须能只用「列表页元数据」算出来——如果算它就得先把整条对话读进内存，
    缓存就白建了。所以优先用 metadata['message_count']（列表页通常带），
    没有才退回 len(messages)。

    调用方注意：一次搜索里 get 和 put 要用同一个戳。GUI 会在读到完整对话后
    把 messages 挂回列表对象上，此时再算戳就变了，会白白多写一次。
    """
    updated = conv.updated_at.isoformat() if conv.updated_at else ""
    created = conv.created_at.isoformat() if conv.created_at else ""
    count: Any = None
    if isinstance(conv.metadata, dict):
        count = conv.metadata.get("message_count")
    if not count:
        count = len(conv.messages)
    return f"{updated}|{created}|{count}"


class SearchIndex:
    """线程安全的磁盘缓存。每个线程一条 sqlite 连接，写入串行化。"""

    def __init__(
        self,
        path: Optional[os.PathLike | str] = None,
        max_total_bytes: int = MAX_TOTAL_BYTES,
    ):
        self._explicit_path = Path(path) if path else None
        self._max_total_bytes = max(0, int(max_total_bytes))
        self._local = threading.local()
        self._lock = threading.RLock()
        self._connections: List[sqlite3.Connection] = []
        self._disabled = False

    @property
    def path(self) -> Path:
        return self._explicit_path if self._explicit_path else index_path()

    @property
    def available(self) -> bool:
        """None 表示还没试过；调用一次 _conn 才知道能不能用。"""
        return self._conn() is not None

    # ---- 连接 ----

    def _conn(self) -> Optional[sqlite3.Connection]:
        if self._disabled:
            return None
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # sqlite 连接不能跨线程共享，所以按线程建；WAL 让读写不互相阻塞。
            conn = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
        except Exception:
            # 打不开就永久降级：后续每次调用都重试只会让搜索一路卡在异常上。
            self._disabled = True
            return None
        self._local.conn = conn
        with self._lock:
            self._connections.append(conn)
        return conn

    # ---- API ----

    def get(self, source: str, conv_id: str, stamp: str) -> Optional[str]:
        """命中且新鲜才返回文本；陈旧或未命中都返回 None。"""
        conn = self._conn()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT stamp, text FROM entries WHERE source=? AND conv_id=?",
                (str(source), str(conv_id)),
            ).fetchone()
        except Exception:
            return None
        if row is None or row[0] != str(stamp):
            return None
        return row[1]

    def put(self, source: str, conv_id: str, stamp: str, text: str) -> bool:
        conn = self._conn()
        if conn is None:
            return False
        text = text or ""
        size = len(text.encode("utf-8", errors="ignore"))
        if self._max_total_bytes and size > self._max_total_bytes:
            # 单条就超过总上限：存进去只会立刻把别人全挤掉再被删，没意义。
            return False
        try:
            with self._lock:
                conn.execute(
                    "INSERT OR REPLACE INTO entries"
                    " (source, conv_id, stamp, text, size, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (str(source), str(conv_id), str(stamp), text, size, time.time()),
                )
                self._evict(conn)
            return True
        except Exception:
            return False

    def get_or_build(
        self,
        source: str,
        conv: Conversation,
        stamp: Optional[str] = None,
        builder: Optional[Callable[[Conversation], str]] = None,
    ) -> str:
        """命中就用缓存，否则调 builder 现算并写回。

        builder 默认是 conversation_search_text。GUI 的用法是传一个
        「先按 id 读完整对话、再算检索文本」的闭包，这样未命中时才读盘。
        builder 自己抛的异常照常向上传——那是数据源的问题，不是缓存的。
        """
        stamp = stamp if stamp is not None else conversation_stamp(conv)
        cached = self.get(source, str(conv.id), stamp)
        if cached is not None:
            return cached
        text = (builder or conversation_search_text)(conv)
        self.put(source, str(conv.id), stamp, text)
        return text

    def clear(self, source: Optional[str] = None) -> bool:
        conn = self._conn()
        if conn is None:
            return False
        try:
            with self._lock:
                if source is None:
                    conn.execute("DELETE FROM entries")
                    try:
                        # 整体清空时顺手把文件收回去，用户点「清空缓存」是想让磁盘变小。
                        conn.execute("VACUUM")
                    except Exception:
                        pass
                else:
                    conn.execute("DELETE FROM entries WHERE source=?", (str(source),))
            return True
        except Exception:
            return False

    def stats(self) -> Dict[str, Any]:
        conn = self._conn()
        info: Dict[str, Any] = {
            "path": str(self.path),
            "available": conn is not None,
            "entries": 0,
            "bytes": 0,
            "max_bytes": self._max_total_bytes,
        }
        if conn is None:
            return info
        try:
            row = conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM entries").fetchone()
            info["entries"] = int(row[0] or 0)
            info["bytes"] = int(row[1] or 0)
        except Exception:
            pass
        return info

    def close(self) -> None:
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    # 连接属于别的线程时 sqlite 会拒绝关闭，这里不值得纠缠。
                    pass
            self._connections.clear()
        self._local = threading.local()

    # ---- 淘汰 ----

    def _evict(self, conn: sqlite3.Connection) -> None:
        """总量超限时按写入时间从旧到新删，直到回到上限内。"""
        if not self._max_total_bytes:
            return
        total = conn.execute("SELECT COALESCE(SUM(size), 0) FROM entries").fetchone()[0] or 0
        if total <= self._max_total_bytes:
            return
        rows = conn.execute(
            "SELECT rowid, size FROM entries ORDER BY updated_at ASC, rowid ASC"
        ).fetchall()
        for rowid, size in rows:
            if total <= self._max_total_bytes:
                break
            conn.execute("DELETE FROM entries WHERE rowid=?", (rowid,))
            total -= size or 0


_default_index: Optional[SearchIndex] = None
_default_lock = threading.Lock()


def get_index() -> SearchIndex:
    global _default_index
    with _default_lock:
        if _default_index is None:
            _default_index = SearchIndex()
        return _default_index


def reset_index() -> None:
    """丢弃全局实例（换缓存目录、测试隔离时用）。"""
    global _default_index
    with _default_lock:
        if _default_index is not None:
            _default_index.close()
        _default_index = None

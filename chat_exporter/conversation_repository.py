"""Thread-safe loaded-conversation cache shared by preview, search and export."""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional, Tuple

from .models import Conversation
from .preview_runtime import PreviewPayload, build_preview_payload
from .preview_utils import PREVIEW_FULL
from .task_runtime import TaskContext


class ConversationLoadError(RuntimeError):
    pass


class ConversationRepository:
    """LRU cache around adapter.get_conversation without changing adapters."""

    def __init__(self, max_items: int = 4):
        self.max_items = max(1, max_items)
        self._lock = threading.RLock()
        self._cache: "OrderedDict[Tuple[str, str, str], Conversation]" = OrderedDict()
        self._key_locks: dict[Tuple[str, str, str], threading.Lock] = {}
        self._key_users: dict[Tuple[str, str, str], int] = {}

    @staticmethod
    def _key(adapter, conversation: Conversation) -> Tuple[str, str, str]:
        source = str(getattr(adapter, "name", "") or conversation.source_app or "unknown")
        stamp = conversation.updated_at.isoformat() if conversation.updated_at else ""
        return source, str(conversation.id), stamp

    def get(
        self,
        adapter,
        conversation: Conversation,
        context: Optional[TaskContext] = None,
        force: bool = False,
    ) -> Conversation:
        if context:
            context.check_cancelled()
        if conversation.messages and not force:
            return conversation

        key = self._key(adapter, conversation)
        with self._lock:
            key_lock = self._key_locks.setdefault(key, threading.Lock())
            self._key_users[key] = self._key_users.get(key, 0) + 1

        try:
            with key_lock:
                if not force:
                    with self._lock:
                        cached = self._cache.get(key)
                        if cached is not None:
                            self._cache.move_to_end(key)
                            return cached

                if adapter is None:
                    raise ConversationLoadError("没有可用的数据来源")
                try:
                    loaded = adapter.get_conversation(conversation.id)
                except Exception as exc:
                    raise ConversationLoadError(f"读取本机对话失败：{exc}") from exc
                if context:
                    context.check_cancelled()
                if loaded is None:
                    raise ConversationLoadError("数据来源返回空记录")
                if not loaded.messages:
                    raise ConversationLoadError("这条记录没有可读取的消息")

                with self._lock:
                    self._cache[key] = loaded
                    self._cache.move_to_end(key)
                    while len(self._cache) > self.max_items:
                        evicted_key, _value = self._cache.popitem(last=False)
                        if self._key_users.get(evicted_key, 0) == 0:
                            self._key_locks.pop(evicted_key, None)
                            self._key_users.pop(evicted_key, None)
                return loaded
        finally:
            with self._lock:
                remaining = self._key_users.get(key, 1) - 1
                if remaining > 0:
                    self._key_users[key] = remaining
                else:
                    self._key_users.pop(key, None)
                    if key not in self._cache:
                        self._key_locks.pop(key, None)

    def preview_payload(
        self,
        adapter,
        conversation: Conversation,
        *,
        mode: str = PREVIEW_FULL,
        page_size: int = 160,
        anchor: str = "latest",
        cursor: Optional[int] = None,
        context: Optional[TaskContext] = None,
    ) -> PreviewPayload:
        """Use an adapter's fast preview window when available, else LRU full load."""
        preview_loader = getattr(adapter, "get_preview_window", None)
        if callable(preview_loader):
            window = preview_loader(
                conversation.id,
                limit=page_size,
                anchor=anchor,
                cursor=cursor,
                mode=mode,
                context=context,
            )
            if context:
                context.check_cancelled()
            payload = build_preview_payload(
                window.conversation,
                mode=mode,
                page_size=page_size,
                anchor="earliest",
                context=context,
            )
            page = payload.page
            page = type(page)(
                entries=page.entries,
                source_start=window.cursor_before or 0,
                source_end=window.cursor_after or 0,
                total_source_messages=window.total_source_messages,
                has_older=window.has_older,
                has_newer=window.has_newer,
                mode=page.mode,
                label=window.label,
            )
            return PreviewPayload(window.conversation, page, payload.segments, payload.plain_text)

        full = self.get(adapter, conversation, context=context)
        return build_preview_payload(
            full,
            mode=mode,
            page_size=page_size,
            anchor=anchor,
            cursor=cursor,
            context=context,
        )

    def invalidate(self, source: Optional[str] = None) -> None:
        with self._lock:
            if not source:
                self._cache.clear()
                self._key_locks.clear()
                self._key_users.clear()
                return
            folded = source.casefold()
            self._cache = OrderedDict(
                (key, value)
                for key, value in self._cache.items()
                if key[0].casefold() != folded
            )
            self._key_locks = {
                key: lock for key, lock in self._key_locks.items() if key[0].casefold() != folded
            }
            self._key_users = {
                key: users for key, users in self._key_users.items() if key[0].casefold() != folded
            }

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

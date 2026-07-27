from __future__ import annotations

from datetime import datetime
import threading
import time

import pytest

from chat_exporter.conversation_repository import ConversationLoadError, ConversationRepository
from chat_exporter.models import Conversation, Message, Role


class Adapter:
    name = "stub"

    def __init__(self):
        self.calls = 0

    def get_conversation(self, conv_id):
        self.calls += 1
        return Conversation(
            id=conv_id,
            title="loaded",
            updated_at=datetime(2026, 7, 27),
            messages=[Message(role=Role.USER, content="hello")],
        )


def test_repository_caches_loaded_conversation():
    adapter = Adapter()
    stub = Conversation(id="1", title="stub", updated_at=datetime(2026, 7, 27))
    repo = ConversationRepository(max_items=2)

    first = repo.get(adapter, stub)
    second = repo.get(adapter, stub)

    assert first is second
    assert adapter.calls == 1


def test_repository_rejects_empty_source_result():
    class EmptyAdapter(Adapter):
        def get_conversation(self, _conv_id):
            return None

    with pytest.raises(ConversationLoadError, match="返回空"):
        ConversationRepository().get(EmptyAdapter(), Conversation(id="1"))


def test_repository_coalesces_concurrent_loads():
    class SlowAdapter(Adapter):
        def get_conversation(self, conv_id):
            self.calls += 1
            time.sleep(0.05)
            return Conversation(
                id=conv_id,
                messages=[Message(role=Role.USER, content="loaded")],
            )

    adapter = SlowAdapter()
    repo = ConversationRepository()
    stub = Conversation(id="same")
    results = []

    threads = [threading.Thread(target=lambda: results.append(repo.get(adapter, stub))) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert adapter.calls == 1
    assert len(results) == 3
    assert results[0] is results[1] is results[2]


def test_repository_does_not_retain_locks_for_evicted_items():
    adapter = Adapter()
    repo = ConversationRepository(max_items=2)

    for index in range(20):
        repo.get(adapter, Conversation(id=str(index), updated_at=datetime(2026, 7, 27)))

    assert len(repo) == 2
    assert len(repo._key_locks) <= 2
    assert repo._key_users == {}

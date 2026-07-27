from __future__ import annotations

import json
import os

from chat_exporter.adapters.workbuddy import WorkBuddyAdapter
from chat_exporter.models import Conversation


def test_list_conversations_does_not_scan_jsonl_for_counts(monkeypatch):
    adapter = WorkBuddyAdapter()
    adapter._cached_conversations = None
    adapter._db_path = "workbuddy.db"
    monkeypatch.setattr(adapter, "detect", lambda: True)
    monkeypatch.setattr(adapter, "_find_jsonl_path", lambda _sid, _cwd: "large.jsonl")
    monkeypatch.setattr(
        adapter,
        "_count_jsonl_messages",
        lambda _path: (_ for _ in ()).throw(AssertionError("must stay lazy")),
    )

    class Cursor:
        def execute(self, _query):
            return None

        def fetchall(self):
            return [
                {
                    "id": "1",
                    "title": "large",
                    "cwd": "C:/x",
                    "model": "m",
                    "status": "done",
                    "created_at": 0,
                    "updated_at": 0,
                }
            ]

    class Connection:
        def execute(self, _query):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(adapter, "_connect_db", lambda _path: Connection())

    conversations = adapter.list_conversations()

    assert conversations[0].metadata["msg_count"] == 0
    assert conversations[0].metadata["msg_count_known"] is False


def test_message_count_cache_survives_restart_and_invalidates_on_change(tmp_path):
    jsonl = tmp_path / "conversation.jsonl"
    jsonl.write_text(
        '{"type":"message","role":"user","content":"a"}\n'
        '{"type":"reasoning","content":"b"}\n',
        encoding="utf-8",
    )
    cache = tmp_path / "counts.json"

    first = WorkBuddyAdapter()
    first._message_count_cache_path = str(cache)
    first._persistent_message_counts = {}
    assert first._remember_message_count(str(jsonl), 2, persist=True) == 2

    second = WorkBuddyAdapter()
    second._message_count_cache_path = str(cache)
    second._persistent_message_counts = {}
    second._load_message_count_cache()
    assert second._cached_message_count(str(jsonl)) == 2

    with jsonl.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"function_call","name":"x"}\n')
    os.utime(jsonl, None)
    assert second._cached_message_count(str(jsonl)) is None


def test_background_message_count_updates_stub_and_persists(tmp_path):
    jsonl = tmp_path / "conversation.jsonl"
    jsonl.write_text(
        '{"type":"message","role":"user","content":"a"}\n'
        '{"type":"function_call_result","output":"b"}\n'
        '{"type":"file-history-snapshot","content":"ignored"}\n',
        encoding="utf-8",
    )
    adapter = WorkBuddyAdapter()
    adapter._message_count_cache_path = str(tmp_path / "counts.json")
    adapter._persistent_message_counts = {}
    conv = Conversation(
        id="c1",
        title="count me",
        metadata={"jsonl_path": str(jsonl), "msg_count_known": False},
    )

    assert adapter.get_message_count(conv) == 2
    assert conv.metadata["msg_count"] == 2
    assert conv.metadata["msg_count_known"] is True
    adapter.flush_message_count_cache()
    assert os.path.exists(adapter._message_count_cache_path)
    assert str(jsonl) not in open(adapter._message_count_cache_path, encoding="utf-8").read()


def test_count_cache_prunes_sessions_no_longer_in_library(tmp_path):
    active = tmp_path / "active.jsonl"
    stale = tmp_path / "stale.jsonl"
    active.write_text('{"type":"message"}\n', encoding="utf-8")
    stale.write_text('{"type":"message"}\n', encoding="utf-8")

    adapter = WorkBuddyAdapter()
    adapter._message_count_cache_path = str(tmp_path / "counts.json")
    adapter._persistent_message_counts = {}
    adapter._remember_message_count(str(active), 1)
    adapter._remember_message_count(str(stale), 1)
    adapter._active_message_count_keys = {adapter._message_count_key(str(active))}

    adapter.flush_message_count_cache()

    payload = json.loads((tmp_path / "counts.json").read_text(encoding="utf-8"))
    assert list(payload["entries"]) == [adapter._message_count_key(str(active))]


def test_jsonl_fallback_directory_is_indexed_once(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    first = projects / "one"
    second = projects / "two"
    first.mkdir(parents=True)
    second.mkdir()
    (first / "session-a.jsonl").write_text("{}\n", encoding="utf-8")
    (second / "session-b.jsonl").write_text("{}\n", encoding="utf-8")

    adapter = WorkBuddyAdapter()
    adapter._projects_dir = str(projects)
    calls = 0
    original_scandir = os.scandir

    def counted_scandir(path):
        nonlocal calls
        calls += 1
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", counted_scandir)

    assert adapter._find_jsonl_path("session-a", "C:/wrong") == str(first / "session-a.jsonl")
    first_pass_calls = calls
    assert adapter._find_jsonl_path("session-b", "C:/wrong") == str(second / "session-b.jsonl")
    assert calls == first_pass_calls


def test_workbuddy_preview_window_reads_tail_without_full_parse(tmp_path, monkeypatch):
    path = tmp_path / "large.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for index in range(500):
            handle.write(
                json.dumps(
                    {
                        "type": "message",
                        "role": "user" if index % 2 == 0 else "assistant",
                        "content": [{"type": "text", "text": f"message-{index}"}],
                        "timestamp": index,
                    }
                )
                + "\n"
            )

    adapter = WorkBuddyAdapter()
    adapter._db_path = "workbuddy.db"
    monkeypatch.setattr(adapter, "detect", lambda: True)
    monkeypatch.setattr(adapter, "_find_jsonl_path", lambda _sid, _cwd: str(path))

    row = {
        "id": "1",
        "title": "large",
        "cwd": "C:/x",
        "model": "m",
        "created_at": 0,
        "updated_at": 0,
    }

    class Result:
        def fetchone(self):
            return row

    class Connection:
        def execute(self, _query, _params):
            return Result()

        def close(self):
            return None

    monkeypatch.setattr(adapter, "_connect_db", lambda _path: Connection())

    latest = adapter.get_preview_window("1", limit=30, anchor="latest")
    older = adapter.get_preview_window(
        "1",
        limit=30,
        anchor="older",
        cursor=latest.cursor_before,
    )

    assert len(latest.conversation.messages) == 30
    assert latest.conversation.messages[-1].content == "message-499"
    assert latest.has_older is True
    assert latest.has_newer is False
    assert older.conversation.messages[-1].content == "message-469"
    assert older.has_newer is True

from __future__ import annotations

from chat_exporter.models import Conversation, Message, Role
from chat_exporter.preview_runtime import (
    PREVIEW_MESSAGE_MAX_CHARS,
    PREVIEW_PAGE_MAX_CHARS,
    build_preview_page,
    build_preview_payload,
)


def _conversation(count=1000):
    return Conversation(
        id="big",
        title="large",
        source_app="WorkBuddy",
        messages=[
            Message(role=Role.USER if i % 2 == 0 else Role.ASSISTANT, content=f"message-{i}")
            for i in range(count)
        ],
    )


def test_latest_preview_page_is_bounded_and_keeps_tail():
    page = build_preview_page(_conversation(15000), page_size=160, anchor="latest")

    assert page.visible_count == 160
    assert page.entries[0].text == "message-14840"
    assert page.entries[-1].text == "message-14999"
    assert page.has_older is True
    assert page.has_newer is False


def test_preview_page_navigation_uses_raw_cursor():
    conversation = _conversation(500)
    latest = build_preview_page(conversation, page_size=100, anchor="latest")
    older = build_preview_page(
        conversation,
        page_size=100,
        anchor="older",
        cursor=latest.source_start,
    )

    assert latest.entries[0].text == "message-400"
    assert older.entries[0].text == "message-300"
    assert older.entries[-1].text == "message-399"
    assert older.has_newer is True


def test_preview_payload_builds_segments_off_the_ui_thread_contract():
    payload = build_preview_payload(_conversation(20), page_size=20, anchor="earliest")

    rendered = "".join(text for text, _tag in payload.segments)
    assert "message-0" in rendered
    assert "message-19" in rendered
    assert "message-19" in payload.plain_text


def test_preview_bounds_single_huge_message_but_keeps_tail():
    huge = "start-" + ("x" * (PREVIEW_MESSAGE_MAX_CHARS + 50_000)) + "-tail"
    conversation = Conversation(
        id="huge",
        source_app="WorkBuddy",
        messages=[Message(role=Role.ASSISTANT, content=huge)],
    )

    page = build_preview_page(conversation, page_size=20, anchor="latest")

    assert len(page.entries[0].text) < len(huge)
    assert "预览省略" in page.entries[0].text
    assert page.entries[0].text.endswith("-tail")


def test_preview_page_has_total_character_budget():
    conversation = Conversation(
        id="many-large",
        source_app="WorkBuddy",
        messages=[Message(role=Role.USER, content="x" * 70_000) for _ in range(20)],
    )

    page = build_preview_page(conversation, page_size=20, anchor="latest")

    assert sum(len(entry.text) for entry in page.entries) <= PREVIEW_PAGE_MAX_CHARS
    assert page.visible_count < 20

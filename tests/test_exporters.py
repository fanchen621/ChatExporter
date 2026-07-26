"""多格式导出的回归测试。

重点不在"能不能跑通"，而在三件会真正伤到用户的事：
HTML 转义（导出的是别人的聊天记录，正文里带 <script> 是常态）、
JSON 无损（缺字段的结构化导出比没有更糟）、
批量导出的失败会计（静默写空壳文件曾经让人以为导全了）。
"""

import json
import os
from datetime import datetime

import pytest

from chat_exporter import exporters
from chat_exporter.exporters import (
    EXPORTERS,
    FORMAT_CHOICES,
    HtmlExporter,
    JsonExporter,
    MarkdownFormatExporter,
    TextExporter,
    batch_export,
    conversation_from_dict,
    export_conversation,
    get_exporter,
    unique_path,
)
from chat_exporter.markdown_exporter import MarkdownExporter
from chat_exporter.models import Conversation, Message, MessagePart, MessagePartType, Role


def make_conv(title="测试对话", source_app="TRAE SOLO CN"):
    return Conversation(
        id="conv-1",
        title=title,
        created_at=datetime(2026, 7, 1, 9, 0, 0),
        updated_at=datetime(2026, 7, 20, 18, 30, 0),
        model="claude-opus-5",
        source_app=source_app,
        metadata={"workspace": "D:/proj"},
        messages=[
            Message(
                role=Role.USER,
                content="帮我看看这个函数",
                timestamp=datetime(2026, 7, 20, 18, 0, 0),
                message_id="m1",
                parts=[MessagePart(type=MessagePartType.TEXT, content="帮我看看这个函数")],
            ),
            Message(
                role=Role.ASSISTANT,
                timestamp=datetime(2026, 7, 20, 18, 1, 0),
                message_id="m2",
                parent_id="m1",
                model="claude-opus-5",
                token_usage={"total_tokens": 1234},
                metadata={"finish": "stop"},
                parts=[
                    MessagePart(type=MessagePartType.THINKING, content="先读一遍实现"),
                    MessagePart(type=MessagePartType.TEXT, content="问题在这里"),
                    MessagePart(type=MessagePartType.CODE, language="python", content="def f():\n    return 1"),
                    MessagePart(
                        type=MessagePartType.TOOL_CALL,
                        tool_name="read_file",
                        tool_input='{"path": "a.py"}',
                    ),
                    MessagePart(
                        type=MessagePartType.TOOL_RESULT,
                        tool_output="SECRET_TOOL_OUTPUT 工具内部输出",
                    ),
                    MessagePart(type=MessagePartType.FILE, file_name="report.md"),
                ],
            ),
        ],
    )


# ============================================================================
# HTML：转义与自包含
# ============================================================================


def test_html_escapes_script_and_entities():
    """正文里的 <script> 必须变成文本，不能进入 DOM 执行。"""
    conv = make_conv()
    conv.messages[0].parts = [
        MessagePart(
            type=MessagePartType.TEXT,
            content="<script>alert('xss')</script> A & B < C \"quoted\"",
        )
    ]
    conv.messages[0].content = "<script>alert('xss')</script>"
    conv.title = "<img src=x onerror=alert(1)>"

    out = HtmlExporter().render(conv)

    assert "<script>" not in out
    assert "alert('xss')" not in out
    assert "&lt;script&gt;" in out
    assert "&amp; B &lt; C" in out
    # "<" 一经转义，onerror 属性就只是惰性文本；要防的是真实标签，不是子串。
    assert "<img" not in out
    assert "&lt;img src=x onerror=alert(1)&gt;" in out


def test_html_escapes_closing_tags_inside_tool_output():
    """工具输出里带 </details> 不能提前关掉折叠块。"""
    conv = make_conv()
    conv.messages[1].parts = [
        MessagePart(type=MessagePartType.TEXT, content="见下"),
        MessagePart(type=MessagePartType.TOOL_RESULT, tool_output="</details></body><script>x()</script>"),
    ]
    out = HtmlExporter().render(conv)

    assert out.count("</details>") == out.count("<details")
    assert "<script>x()</script>" not in out
    assert "&lt;/details&gt;" in out


def test_html_is_self_contained_and_theme_aware():
    out = HtmlExporter().render(make_conv())

    assert "<style>" in out
    assert "prefers-color-scheme" in out
    # 零外部请求：不引脚本、不引样式表、不引远程资源。
    for token in ("<script", "<link", "<iframe", "@import", "http://", "https://", "src="):
        assert token not in out


def test_html_separates_roles_and_folds_noise():
    out = HtmlExporter().render(make_conv())

    assert "msg-user" in out
    assert "msg-assistant" in out
    assert "💭 思考过程" in out
    assert "📎 工具返回结果" in out
    assert "🔧 调用工具" in out
    # 正文在折叠块外，工具输出在折叠块内。
    assert "问题在这里" in out
    body_of_details = out.split("<details", 1)[1]
    assert "SECRET_TOOL_OUTPUT" in body_of_details


def test_html_respects_include_flags():
    conv = make_conv()
    out = HtmlExporter(include_thinking=False).render(conv)
    assert "💭 思考过程" not in out
    assert "先读一遍实现" not in out


def test_html_keeps_standalone_tool_messages_collapsed():
    conv = make_conv()
    conv.messages.append(
        Message(
            role=Role.TOOL,
            parts=[MessagePart(type=MessagePartType.TEXT, content="QClaw 独立工具消息正文")],
        )
    )
    out = HtmlExporter().render(conv)
    assert "QClaw 独立工具消息正文" in out
    assert "msg-tool" in out

    out_off = HtmlExporter(include_tool_messages=False).render(conv)
    assert "QClaw 独立工具消息正文" not in out_off


# ============================================================================
# JSON：无损
# ============================================================================


def test_json_roundtrip_preserves_every_message_and_part():
    conv = make_conv()
    raw = JsonExporter().render(conv)
    data = json.loads(raw)

    payload = data["conversation"]
    assert payload["id"] == conv.id
    assert payload["source_app"] == conv.source_app
    assert payload["metadata"] == {"workspace": "D:/proj"}
    assert payload["created_at"] == "2026-07-01T09:00:00"
    assert len(payload["messages"]) == len(conv.messages)

    second = payload["messages"][1]
    assert second["role"] == "assistant"
    assert second["token_usage"] == {"total_tokens": 1234}
    assert second["metadata"] == {"finish": "stop"}
    assert second["parent_id"] == "m1"
    assert second["timestamp"] == "2026-07-20T18:01:00"
    assert len(second["parts"]) == len(conv.messages[1].parts)

    types = [part["type"] for part in second["parts"]]
    assert types == ["thinking", "text", "code", "tool_call", "tool_result", "file"]
    assert second["parts"][3]["tool_name"] == "read_file"
    assert second["parts"][4]["tool_output"] == "SECRET_TOOL_OUTPUT 工具内部输出"
    assert second["parts"][5]["file_name"] == "report.md"

    # 结构能原样读回 Conversation 对象。
    restored = conversation_from_dict(data)
    assert restored.id == conv.id
    assert restored.created_at == conv.created_at
    assert len(restored.messages) == len(conv.messages)
    assert restored.messages[1].role is Role.ASSISTANT
    assert [p.type for p in restored.messages[1].parts] == [p.type for p in conv.messages[1].parts]
    assert restored.messages[1].parts[2].language == "python"


def test_json_ignores_export_filters():
    """JSON 的契约是无损；过滤过的 JSON 看着完整其实缺正文，最难排查。"""
    conv = make_conv()
    data = json.loads(JsonExporter(include_thinking=False, include_tool_messages=False).render(conv))
    types = [part["type"] for part in data["conversation"]["messages"][1]["parts"]]
    assert "thinking" in types
    assert "tool_result" in types


def test_json_survives_unserializable_metadata():
    conv = make_conv()
    conv.metadata = {"when": datetime(2026, 1, 1), "blob": b"\xff\xfe", "tags": {"a"}, "obj": object()}
    data = json.loads(JsonExporter().render(conv))
    meta = data["conversation"]["metadata"]
    assert meta["when"] == "2026-01-01T00:00:00"
    assert isinstance(meta["tags"], list)
    assert isinstance(meta["obj"], str)


def test_json_keeps_chinese_readable():
    out = JsonExporter().render(make_conv())
    assert "测试对话" in out


# ============================================================================
# 纯文本 / Markdown
# ============================================================================


def test_txt_contains_only_reading_view():
    out = TextExporter().render(make_conv())
    assert "帮我看看这个函数" in out
    assert "问题在这里" in out
    # 工具与思考噪声不进纯文本。
    assert "SECRET_TOOL_OUTPUT" not in out
    assert "read_file" not in out
    assert "先读一遍实现" not in out


def test_markdown_delegates_to_markdown_exporter():
    """Markdown 分支必须是同一份实现，不是复制品。"""
    conv = make_conv()
    mine = MarkdownFormatExporter().render(conv)
    theirs = MarkdownExporter().export(conv)
    marker = "*导出时间"
    assert mine.split(marker)[0] == theirs.split(marker)[0]


# ============================================================================
# 注册表
# ============================================================================


def test_registry_shape():
    assert set(EXPORTERS) == {"markdown", "html", "json", "txt"}
    assert [fid for fid, _label in FORMAT_CHOICES] == ["markdown", "html", "json", "txt"]
    for format_id, exporter in EXPORTERS.items():
        assert exporter.format_id == format_id
        assert exporter.label and any(ord(ch) > 127 for ch in exporter.label)
        assert exporter.extension.startswith(".")
        assert callable(exporter.export)


def test_get_exporter_rejects_unknown_format():
    with pytest.raises(ValueError):
        get_exporter("pdf")


@pytest.mark.parametrize("format_id,ext", [("markdown", ".md"), ("html", ".html"), ("json", ".json"), ("txt", ".txt")])
def test_export_writes_file(tmp_path, format_id, ext):
    conv = make_conv()
    path = tmp_path / f"out{ext}"
    content = export_conversation(conv, str(path), format_id)
    assert path.exists()
    assert path.read_text(encoding="utf-8") == content
    assert EXPORTERS[format_id].extension == ext


def test_export_creates_missing_directories(tmp_path):
    path = tmp_path / "a" / "b" / "out.html"
    export_conversation(make_conv(), str(path), "html")
    assert path.exists()


# ============================================================================
# 批量导出
# ============================================================================


def test_batch_export_writes_one_file_per_conversation(tmp_path):
    convs = [make_conv(title=f"对话{i}") for i in range(3)]
    seen = []
    count, failures = batch_export(
        convs, str(tmp_path), "html", progress_callback=lambda i, t, p: seen.append((i, t, p))
    )
    assert count == 3
    assert failures == []
    assert len(list(tmp_path.glob("*.html"))) == 3
    assert [item[0] for item in seen] == [1, 2, 3]
    assert all(item[1] == 3 for item in seen)


def test_batch_export_never_writes_empty_conversation(tmp_path):
    empty = Conversation(id="empty", title="空对话", updated_at=datetime(2026, 7, 20, 1, 2, 3))
    count, failures = batch_export([empty], str(tmp_path), "markdown")
    assert count == 0
    assert failures == [("空对话", "没有可导出的消息")]
    assert list(tmp_path.iterdir()) == []


def test_batch_export_does_not_overwrite_existing_files(tmp_path):
    conv = make_conv(title="重名对话")
    target = tmp_path / "重名对话_20260720_183000.md"
    target.write_text("请勿覆盖", encoding="utf-8")

    count, failures = batch_export([conv, conv], str(tmp_path), "markdown")

    assert count == 2
    assert failures == []
    assert target.read_text(encoding="utf-8") == "请勿覆盖"
    assert (tmp_path / "重名对话_20260720_183000_1.md").exists()
    assert (tmp_path / "重名对话_20260720_183000_2.md").exists()


def test_batch_export_records_write_failures(tmp_path, monkeypatch):
    def boom(self, conv):
        raise OSError("磁盘满了")

    monkeypatch.setattr(exporters.TextExporter, "render", boom)
    convs = [make_conv(title="甲"), make_conv(title="乙")]
    count, failures = batch_export(convs, str(tmp_path), "txt")

    assert count == 0
    assert [title for title, _reason in failures] == ["甲", "乙"]
    assert all("磁盘满了" in reason for _title, reason in failures)
    assert list(tmp_path.iterdir()) == []


def test_batch_export_loader_failures_are_accounted(tmp_path):
    stub_ok = Conversation(id="a", title="能读出来的")
    stub_none = Conversation(id="b", title="读不出来的")
    stub_raise = Conversation(id="c", title="读崩了的")

    def loader(conv):
        if conv.id == "a":
            return make_conv(title="能读出来的")
        if conv.id == "b":
            return None
        raise RuntimeError("数据库锁住了")

    count, failures = batch_export(
        [stub_ok, stub_none, stub_raise], str(tmp_path), "json", loader=loader
    )

    assert count == 1
    assert failures == [
        ("读不出来的", "读取失败：来源返回空"),
        ("读崩了的", "读取失败：数据库锁住了"),
    ]
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_batch_export_progress_callback_reports_every_item(tmp_path):
    convs = [make_conv(title="有内容"), Conversation(id="x", title="没内容")]
    calls = []
    count, failures = batch_export(
        convs, str(tmp_path), "txt", progress_callback=lambda i, t, p: calls.append((i, p))
    )
    assert count == 1
    assert len(failures) == 1
    assert len(calls) == 2
    assert calls[1][1] is None


def test_batch_export_survives_broken_progress_callback(tmp_path):
    def bad_callback(*_args):
        raise RuntimeError("GUI 挂了")

    count, failures = batch_export([make_conv()], str(tmp_path), "html", progress_callback=bad_callback)
    assert count == 1
    assert failures == []


def test_unique_path_is_stable(tmp_path):
    path = tmp_path / "a.md"
    assert unique_path(str(path)) == str(path)
    path.write_text("x", encoding="utf-8")
    assert unique_path(str(path)) == str(tmp_path / "a_1.md")

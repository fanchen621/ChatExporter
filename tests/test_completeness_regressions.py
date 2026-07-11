import json
import os
import sqlite3
import tempfile
import unittest

from chat_exporter.adapters.base import BaseAdapter
from chat_exporter.adapters.qclaw_compat import QClawAdapter as QClawCompatAdapter
from chat_exporter.adapters.qoderwork import QoderWorkAdapter
from chat_exporter.markdown_exporter import MarkdownExporter
from chat_exporter.models import Conversation, Message, MessagePart, MessagePartType, Role
from chat_exporter.preview_utils import message_preview_text


class _DummyAdapter(BaseAdapter):
    def detect(self):
        return False

    def get_app_info(self):
        return None

    def list_conversations(self):
        return []

    def get_conversation(self, conv_id):
        return None


class CompletenessRegressionTests(unittest.TestCase):
    def test_sqlite_snapshot_contains_committed_wal_rows(self):
        """A copied snapshot must include rows committed only to the WAL."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "live.db")
            writer = sqlite3.connect(db_path)
            try:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("PRAGMA wal_autocheckpoint=0")
                writer.execute("CREATE TABLE items(value TEXT)")
                writer.commit()
                writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")

                writer.execute("INSERT INTO items(value) VALUES ('latest-row')")
                writer.commit()

                snapshot = _DummyAdapter()._safe_copy_db(db_path)
                with sqlite3.connect(snapshot) as reader:
                    values = [row[0] for row in reader.execute("SELECT value FROM items")]
                self.assertEqual(values, ["latest-row"])
            finally:
                writer.close()

    def test_qoderwork_tool_result_with_call_id_is_not_misclassified(self):
        adapter = QoderWorkAdapter()
        row = {
            "role": "assistant",
            "parts": json.dumps([
                {
                    "type": "tool-result",
                    "toolCallId": "call-1",
                    "toolName": "shell",
                    "result": {"text": "最终交付结果"},
                }
            ], ensure_ascii=False),
            "metadata": "{}",
            "created_at": 0,
            "message_id": "m1",
        }

        message = adapter._parse_message(row, "qmodel")
        self.assertIsNotNone(message)
        self.assertEqual(len(message.parts), 1)
        self.assertEqual(message.parts[0].type, MessagePartType.TOOL_RESULT)
        self.assertIn("最终交付结果", message.parts[0].tool_output)
        self.assertIn("最终交付结果", message_preview_text(message, source_app=adapter.display_name))

    def test_qoderwork_unknown_text_part_is_preserved(self):
        adapter = QoderWorkAdapter()
        row = {
            "role": "assistant",
            "parts": json.dumps([
                {"type": "final-delivery-v2", "content": "新版最终回复"}
            ], ensure_ascii=False),
            "metadata": "{}",
            "created_at": 0,
            "message_id": "m2",
        }
        message = adapter._parse_message(row, None)
        self.assertEqual(message.content, "新版最终回复")
        self.assertEqual(message.parts[0].type, MessagePartType.TEXT)

    def test_trae_multiple_thinking_parts_are_complete_in_preview_and_export(self):
        message = Message(
            role=Role.ASSISTANT,
            content="",
            parts=[
                MessagePart(type=MessagePartType.THINKING, content="第一段最终交付"),
                MessagePart(type=MessagePartType.THINKING, content="第二段最终交付"),
            ],
        )
        conv = Conversation(
            id="trae-thinking",
            title="TRAE 完整性",
            messages=[message],
            source_app="TRAE SOLO CN",
        )

        preview = message_preview_text(message, source_app=conv.source_app)
        markdown = MarkdownExporter().export(conv)
        visible_body = markdown.split("<details>", 1)[0]

        for text in ("第一段最终交付", "第二段最终交付"):
            self.assertIn(text, preview)
            self.assertIn(text, visible_body)
            self.assertIn(text, markdown)

    def test_tool_result_only_message_is_visible_in_preview_and_export(self):
        message = Message(
            role=Role.TOOL,
            content="",
            parts=[
                MessagePart(
                    type=MessagePartType.TOOL_RESULT,
                    tool_name="builder",
                    tool_output="已生成完整文件和校验结果",
                )
            ],
        )
        conv = Conversation(
            id="tool-result",
            title="工具结果完整性",
            messages=[message],
            source_app="QoderWork CN",
        )

        preview = message_preview_text(message, source_app=conv.source_app)
        markdown = MarkdownExporter().export(conv)
        visible_body = markdown.split("<details>", 1)[0]

        self.assertIn("已生成完整文件和校验结果", preview)
        self.assertIn("已生成完整文件和校验结果", visible_body)
        self.assertIn("📎 工具返回结果", markdown)

    def test_non_trae_thinking_gets_readable_summary_and_full_details(self):
        message = Message(
            role=Role.SYSTEM,
            content="",
            parts=[
                MessagePart(type=MessagePartType.THINKING, content="内部推理第一行\n内部推理第二行")
            ],
        )
        conv = Conversation(
            id="thinking-summary",
            title="WorkBuddy 思考",
            messages=[message],
            source_app="WorkBuddy",
        )

        preview = message_preview_text(message, source_app=conv.source_app)
        markdown = MarkdownExporter().export(conv)
        visible_body = markdown.split("<details>", 1)[0]

        self.assertIn("[AI 思考摘要]", preview)
        self.assertIn("[AI 思考摘要]", visible_body)
        self.assertIn("内部推理第二行", markdown)

    def test_qclaw_unknown_nonempty_role_does_not_disappear(self):
        self.assertEqual(QClawCompatAdapter._parse_role("custom-speaker-v9"), Role.ASSISTANT)


if __name__ == "__main__":
    unittest.main()

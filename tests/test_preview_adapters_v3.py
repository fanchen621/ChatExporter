import unittest

from chat_exporter.adapters.qclaw_compat import QClawAdapter
from chat_exporter.adapters.workbuddy_compat import WorkBuddyAdapter
from chat_exporter.models import Conversation, Message, MessagePart, MessagePartType, Role
from chat_exporter.preview_utils import (
    message_preview_text,
    plain_preview_text,
    strip_internal_context,
    visible_messages,
)


class PreviewSanitizerV3Tests(unittest.TestCase):
    def test_workbuddy_injected_context_is_removed_but_query_remains(self):
        raw = """
<system-reminder data-role="user-context">
<user_references>
Note: These references are the files the user explicitly referenced.
"D:\\Desktop\\report.txt"
</user_references>
<user_info>
OS Version: win32
Shell: bash
IDE Theme: light
</user_info>
<identity_context>
Injected workspace identity files:
## SOUL.md
Path: C:\\ProgramData\\WorkBuddy\\SOUL.md
</identity_context>
</system-reminder>
<user_query>请帮我总结这份报告，并列出三个风险。</user_query>
"""
        cleaned = strip_internal_context(raw, source_app="WorkBuddy")
        self.assertEqual(cleaned, "请帮我总结这份报告，并列出三个风险。")
        self.assertNotIn("OS Version", cleaned)
        self.assertNotIn("SOUL.md", cleaned)

    def test_tool_placeholder_is_not_shown_as_assistant_reply(self):
        message = Message(
            role=Role.ASSISTANT,
            content="[工具调用] read_file",
            parts=[
                MessagePart(
                    type=MessagePartType.TOOL_CALL,
                    tool_name="read_file",
                    tool_input='{"path":"a.txt"}',
                )
            ],
        )
        self.assertEqual(message_preview_text(message, source_app="WorkBuddy"), "")

    def test_qclaw_loose_user_role_is_recovered(self):
        message = Message(
            role=Role.SYSTEM,
            content="这是用户的问题",
            metadata={"raw_role": "human_message"},
        )
        conversation = Conversation(
            id="1",
            title="测试",
            source_app="QClaw",
            messages=[message],
        )
        visible = visible_messages(conversation)
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0][0].role, Role.USER)

    def test_plain_preview_contains_only_user_and_assistant(self):
        conversation = Conversation(
            id="1",
            title="测试",
            source_app="WorkBuddy",
            messages=[
                Message(role=Role.SYSTEM, content="系统提示"),
                Message(role=Role.USER, content="<user_query>你好</user_query>"),
                Message(
                    role=Role.ASSISTANT,
                    content="你好，有什么可以帮你？",
                    parts=[MessagePart(type=MessagePartType.THINKING, content="内部思考")],
                ),
                Message(role=Role.TOOL, content="工具结果"),
            ],
        )
        text = plain_preview_text(conversation)
        self.assertIn("用户\n你好", text)
        self.assertIn("AI 助手\n你好，有什么可以帮你？", text)
        self.assertNotIn("系统提示", text)
        self.assertNotIn("内部思考", text)
        self.assertNotIn("工具结果", text)


class AdapterCompatibilityV3Tests(unittest.TestCase):
    def test_qclaw_role_aliases(self):
        self.assertEqual(QClawAdapter._parse_role("user_message"), Role.USER)
        self.assertEqual(QClawAdapter._parse_role("human-input"), Role.USER)
        self.assertEqual(QClawAdapter._parse_role("assistant_message"), Role.ASSISTANT)
        self.assertEqual(QClawAdapter._parse_role("agent-output"), Role.ASSISTANT)

    def test_workbuddy_record_removes_injected_context(self):
        adapter = WorkBuddyAdapter()
        record = {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "<system-reminder><user_info>OS Version: win32</user_info>"
                        "</system-reminder><user_query>真正的问题</user_query>"
                    ),
                }
            ],
            "timestamp": None,
            "id": "m1",
        }
        message = adapter._parse_record(record)
        self.assertIsNotNone(message)
        self.assertEqual(message.content, "真正的问题")
        self.assertTrue(message.metadata.get("internal_context_removed"))


if __name__ == "__main__":
    unittest.main()

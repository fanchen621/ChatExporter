import unittest

from chat_exporter.models import Conversation, Message, MessagePart, MessagePartType, Role
from chat_exporter.preview_utils import conversation_search_text, message_preview_text, visible_messages


class PreviewUtilsTests(unittest.TestCase):
    def test_preview_ignores_thinking_and_tools(self):
        message = Message(
            role=Role.ASSISTANT,
            content="这是最终回答",
            parts=[
                MessagePart(type=MessagePartType.THINKING, content="内部思考"),
                MessagePart(type=MessagePartType.TOOL_CALL, tool_name="shell", tool_input="dir"),
                MessagePart(type=MessagePartType.TOOL_RESULT, tool_output="secret result"),
                MessagePart(type=MessagePartType.CODE, content="print('ok')", language="python"),
            ],
        )
        preview = message_preview_text(message)
        self.assertIn("这是最终回答", preview)
        self.assertIn("print('ok')", preview)
        self.assertNotIn("内部思考", preview)
        self.assertNotIn("secret result", preview)
        self.assertNotIn("dir", preview)

    def test_system_and_tool_messages_are_hidden(self):
        conversation = Conversation(
            id="1",
            title="测试",
            messages=[
                Message(role=Role.SYSTEM, content="系统提示"),
                Message(role=Role.TOOL, content="工具输出"),
                Message(role=Role.USER, content="用户问题"),
                Message(role=Role.ASSISTANT, content="AI回答"),
            ],
        )
        items = visible_messages(conversation)
        self.assertEqual([text for _message, text in items], ["用户问题", "AI回答"])

    def test_fulltext_search_uses_user_and_ai_content(self):
        conversation = Conversation(
            id="1",
            title="网络问题",
            messages=[
                Message(role=Role.USER, content="Clash DNS 配置失败"),
                Message(role=Role.ASSISTANT, content="请检查 nameserver 配置"),
                Message(role=Role.TOOL, content="不应进入索引的工具输出"),
            ],
        )
        searchable = conversation_search_text(conversation)
        self.assertIn("clash dns", searchable)
        self.assertIn("nameserver", searchable)
        self.assertNotIn("工具输出", searchable)


if __name__ == "__main__":
    unittest.main()

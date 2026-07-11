import unittest

from chat_exporter.models import Conversation, Message, MessagePart, MessagePartType, Role
from chat_exporter.preview_utils import conversation_search_text, message_preview_text, strip_internal_context, visible_messages


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

    def test_trae_ui_noise_lines_are_filtered(self):
        """TRAE 任务消息中的 UI 状态标签行应被过滤。"""
        raw = "查看 3 个步骤\n实际AI回复\n处理中...\n创建\n已执行命令\n深度思考\n已读取\n已允许高危操作\n正在执行命令"
        cleaned = strip_internal_context(raw, source_app="TRAE SOLO CN")
        self.assertIn("实际AI回复", cleaned)
        self.assertNotIn("查看 3 个步骤", cleaned)
        self.assertNotIn("处理中", cleaned)
        self.assertNotIn("已执行命令", cleaned)
        self.assertNotIn("深度思考", cleaned)
        self.assertNotIn("已读取", cleaned)
        self.assertNotIn("已允许高危操作", cleaned)
        self.assertNotIn("正在执行命令", cleaned)

    def test_normal_text_with_create_not_filtered(self):
        """普通用户消息中的'创建'不应被过滤（只过滤独占一行的 UI 标签）。"""
        raw = "帮我创建一个新文件"
        cleaned = strip_internal_context(raw, source_app="WorkBuddy")
        self.assertIn("创建", cleaned)

    def test_preview_filters_trae_ui_noise(self):
        """预览应过滤 TRAE UI 噪声行。"""
        message = Message(
            role=Role.ASSISTANT,
            content="查看 5 个步骤\n这是AI正文\n处理中...\n创建",
            parts=[],
        )
        preview = message_preview_text(message, source_app="TRAE SOLO CN")
        self.assertIn("这是AI正文", preview)
        self.assertNotIn("查看 5 个步骤", preview)
        self.assertNotIn("处理中", preview)


class MarkdownExporterTests(unittest.TestCase):
    def test_thinking_blocks_are_merged(self):
        """多个 thinking part 应合并为一个 <details> 块。"""
        from chat_exporter.markdown_exporter import MarkdownExporter

        msg = Message(
            role=Role.ASSISTANT,
            content="最终回答",
            parts=[
                MessagePart(type=MessagePartType.THINKING, content="思考1"),
                MessagePart(type=MessagePartType.THINKING, content="思考2"),
                MessagePart(type=MessagePartType.THINKING, content="思考3"),
                MessagePart(type=MessagePartType.TEXT, content="最终回答"),
            ],
        )
        conv = Conversation(id="1", title="test", messages=[msg], source_app="TRAE SOLO CN")
        md = MarkdownExporter().export(conv)
        self.assertEqual(md.count("<details>"), 1)
        self.assertIn("思考1", md)
        self.assertIn("思考2", md)
        self.assertIn("思考3", md)
        self.assertIn("最终回答", md)

    def test_export_filters_trae_ui_noise(self):
        """导出 markdown 也应过滤 TRAE UI 噪声行。"""
        from chat_exporter.markdown_exporter import MarkdownExporter

        msg = Message(
            role=Role.ASSISTANT,
            content="",
            parts=[
                MessagePart(type=MessagePartType.TEXT, content="查看 2 个步骤\n这是AI正文"),
            ],
        )
        conv = Conversation(id="2", title="test2", messages=[msg], source_app="TRAE SOLO CN")
        md = MarkdownExporter().export(conv)
        self.assertIn("这是AI正文", md)
        self.assertNotIn("查看 2 个步骤", md)

    def test_thinking_code_fence_no_conflict(self):
        """思考内容中的 ``` 不应与外层 ~~~ 围栏冲突。"""
        from chat_exporter.markdown_exporter import MarkdownExporter

        msg = Message(
            role=Role.ASSISTANT,
            content="回答",
            parts=[
                MessagePart(type=MessagePartType.THINKING, content="思考\n```python\ncode\n```"),
                MessagePart(type=MessagePartType.TEXT, content="回答"),
            ],
        )
        conv = Conversation(id="3", title="test3", messages=[msg], source_app="TRAE SOLO CN")
        md = MarkdownExporter().export(conv)
        self.assertIn("~~~", md)
        self.assertIn("```python", md)
        self.assertEqual(md.count("<details>"), 1)

    def test_system_thinking_labeled_as_ai(self):
        """SYSTEM + THINKING 消息应标记为 AI 助手，而非系统。"""
        from chat_exporter.markdown_exporter import MarkdownExporter

        msg = Message(
            role=Role.SYSTEM,
            content="",
            parts=[
                MessagePart(type=MessagePartType.THINKING, content="内部推理"),
            ],
        )
        conv = Conversation(id="4", title="test4", messages=[msg], source_app="WorkBuddy")
        md = MarkdownExporter().export(conv)
        self.assertIn("🤖 AI助手", md)
        self.assertNotIn("⚙️ 系统", md)

    def test_empty_messages_are_skipped(self):
        """空内容消息应被跳过，不产生空章节。"""
        from chat_exporter.markdown_exporter import MarkdownExporter

        msgs = [
            Message(role=Role.USER, content="", parts=[]),
            Message(role=Role.ASSISTANT, content="实际回复", parts=[]),
        ]
        conv = Conversation(id="5", title="test5", messages=msgs, source_app="TRAE SOLO CN")
        md = MarkdownExporter().export(conv)
        self.assertIn("实际回复", md)
        # 只有一个消息章节（空消息被跳过）
        self.assertEqual(md.count("## "), 1)

    def test_tool_only_messages_are_skipped(self):
        """纯 TOOL 角色消息应被跳过（effective_role 返回 None）。"""
        from chat_exporter.markdown_exporter import MarkdownExporter

        msgs = [
            Message(role=Role.USER, content="用户问题", parts=[]),
            Message(role=Role.TOOL, content="工具输出", parts=[]),
            Message(role=Role.ASSISTANT, content="AI回答", parts=[]),
        ]
        conv = Conversation(id="6", title="test6", messages=msgs, source_app="QClaw")
        md = MarkdownExporter().export(conv)
        self.assertIn("用户问题", md)
        self.assertIn("AI回答", md)
        self.assertNotIn("工具输出", md)
        self.assertEqual(md.count("## "), 2)


if __name__ == "__main__":
    unittest.main()

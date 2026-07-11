import json
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
        self.assertEqual([text for _message, _role, text in items], ["用户问题", "AI回答"])

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

    def test_mro_show_preview_resolves_to_v2(self):
        """_show_preview 应解析到 gui_cn_v2 而非 gui_cn（死代码已清理）。"""
        import chat_exporter.gui_cn_v3 as m
        cls = m.ChatExporterGUI
        defining_class = None
        for klass in cls.__mro__:
            if "_show_preview" in klass.__dict__:
                defining_class = klass
                break
        self.assertIsNotNone(defining_class, "_show_preview 未在 MRO 中找到")
        self.assertEqual(defining_class.__module__, "chat_exporter.gui_cn_v2",
                         "_show_preview 应定义在 gui_cn_v2 中")

    def test_large_conversation_export_tail_complete(self):
        """100+ 条消息的导出应完整包含尾部消息。"""
        from chat_exporter.markdown_exporter import MarkdownExporter

        msgs = []
        for i in range(120):
            msgs.append(Message(role=Role.USER, content=f"用户问题{i}"))
            msgs.append(Message(role=Role.ASSISTANT, content=f"AI回答{i}"))
        conv = Conversation(id="big", title="大对话", messages=msgs, source_app="TRAE SOLO CN")
        md = MarkdownExporter().export(conv)
        # 尾部消息必须存在
        self.assertIn("用户问题119", md)
        self.assertIn("AI回答119", md)
        # 首部消息也应存在
        self.assertIn("用户问题0", md)
        self.assertIn("AI回答0", md)

    def test_source_app_passed_to_strip_internal_context(self):
        """strip_internal_context 应正确接收 source_app 参数。"""
        # TRAE UI 噪声只在 source_app="TRAE SOLO CN" 时过滤
        raw = "查看 3 个步骤\n实际内容"
        cleaned_trae = strip_internal_context(raw, source_app="TRAE SOLO CN")
        self.assertNotIn("查看 3 个步骤", cleaned_trae)
        # 其他应用的相同文本不应被过滤（不是独占一行的 UI 标签时）
        cleaned_other = strip_internal_context("帮我查看 3 个步骤", source_app="WorkBuddy")
        self.assertIn("查看 3 个步骤", cleaned_other)


class ContentPartsConsistencyTests(unittest.TestCase):
    """验证各适配器 content 与 parts TEXT 的一致性，以及预览/导出文本来源一致。"""

    def test_trae_general_multi_text_parts_content_is_newline_joined(self):
        """TRAE general 消息多 text part：content 等于 TEXT parts 的 \\n 连接（不是空格）。"""
        from chat_exporter.adapters.trae import TraeAdapter

        adapter = TraeAdapter()
        # 模拟 general content JSON：多个 text_content 条目
        content_json = '[{"text_content": "第一段"}, {"text_content": "第二段"}]'
        parts = adapter._parse_general_content(content_json)
        text_parts = [p.content for p in parts if p.type == MessagePartType.TEXT and p.content]
        expected_content = "\n".join(text_parts)
        # 验证不是空格连接
        self.assertNotIn("第一段 第二段", expected_content)
        self.assertEqual(expected_content, "第一段\n第二段")

    def test_trae_task_summary_does_not_replace_content(self):
        """TRAE task 消息有 summary：content 等于 TEXT parts 的换行连接，不是 summary。"""
        from chat_exporter.adapters.trae import TraeAdapter

        adapter = TraeAdapter()
        # 模拟 task content JSON：包含 text 消息和 thinking 消息
        task_content = json.dumps({
            "messages": [
                {"type": "text", "text": "实际正文"},
                {"type": "thinking", "content": "内部思考"},
            ]
        }, ensure_ascii=False)
        parts = adapter._parse_task_content(task_content)
        text_parts = [p.content for p in parts if p.type == MessagePartType.TEXT and p.content]
        content = "\n".join(text_parts)
        # content 应为实际正文，不是 summary
        self.assertEqual(content, "实际正文")
        self.assertNotIn("内部思考", content)

    def test_qoderwork_multi_text_parts_not_concatenated_without_separator(self):
        """QoderWork 多 text part：content 用 \\n 连接，不是无分隔符拼接。"""
        from chat_exporter.adapters.qoderwork import QoderWorkAdapter

        # 模拟 parts JSON：多个 type="text" 条目
        parts_json = json.dumps([
            {"type": "text", "text": "段落A"},
            {"type": "text", "text": "段落B"},
        ], ensure_ascii=False)

        # 构造一个类似数据库行的对象
        class FakeRow:
            def __getitem__(self, key):
                data = {
                    "role": "user",
                    "parts": parts_json,
                    "created_at": 0,
                    "message_id": "test1",
                    "metadata": "",
                }
                return data[key]

        adapter = QoderWorkAdapter()
        msg = adapter._parse_message(FakeRow(), model_level=None)
        # content 应为换行连接，不是 "段落A段落B"
        self.assertEqual(msg.content, "段落A\n段落B")
        self.assertNotEqual(msg.content, "段落A段落B")

    def test_qoderwork_code_parts_not_in_content(self):
        """QoderWork code parts 不混入 content。"""
        from chat_exporter.adapters.qoderwork import QoderWorkAdapter

        parts_json = json.dumps([
            {"type": "text", "text": "正文"},
            {"type": "code", "text": "print(1)", "language": "python"},
        ], ensure_ascii=False)

        class FakeRow:
            def __getitem__(self, key):
                data = {
                    "role": "assistant",
                    "parts": parts_json,
                    "created_at": 0,
                    "message_id": "test2",
                    "metadata": "",
                }
                return data[key]

        adapter = QoderWorkAdapter()
        msg = adapter._parse_message(FakeRow(), model_level=None)
        # content 仅含 TEXT parts，不含代码块
        self.assertEqual(msg.content, "正文")
        self.assertNotIn("print(1)", msg.content)
        self.assertNotIn("```", msg.content)

    def test_qclaw_content_equals_parts_text_newline_join(self):
        """QClaw content 与 parts TEXT parts 的换行连接一致。"""
        from chat_exporter.adapters.qclaw import QClawAdapter

        # 模拟 message_parts 行
        class FakePartRow:
            def __init__(self, part_type, text_content):
                self._data = {
                    "part_type": part_type,
                    "text_content": text_content,
                    "tool_name": None,
                    "tool_input": None,
                    "tool_output": None,
                    "tool_error": None,
                    "file_name": None,
                }
            def __getitem__(self, key):
                return self._data[key]

        class FakeMsgRow:
            def __init__(self, content):
                self._data = {
                    "role": "user",
                    "content": content,
                    "created_at": "",
                    "message_id": "test3",
                    "token_count": 0,
                }
            def __getitem__(self, key):
                return self._data[key]

        adapter = QClawAdapter()
        part_rows = [
            FakePartRow("text", "文本一"),
            FakePartRow("text", "文本二"),
        ]
        msg = adapter._parse_message(FakeMsgRow("db原始内容"), part_rows)
        # content 应为 parts TEXT 的换行连接，不是 db 原始内容
        self.assertEqual(msg.content, "文本一\n文本二")
        self.assertNotEqual(msg.content, "db原始内容")

    def test_workbuddy_reasoning_content_is_empty(self):
        """WorkBuddy reasoning 消息的 content 为空字符串。"""
        from chat_exporter.adapters.workbuddy import WorkBuddyAdapter

        adapter = WorkBuddyAdapter()
        record = {
            "type": "reasoning",
            "content": "这是内部推理过程",
            "timestamp": 0,
            "id": "r1",
        }
        msg = adapter._parse_record(record)
        self.assertEqual(msg.content, "")
        # THINKING part 仍保留思考内容
        self.assertEqual(len(msg.parts), 1)
        self.assertEqual(msg.parts[0].type, MessagePartType.THINKING)
        self.assertIn("内部推理", msg.parts[0].content)

    def test_workbuddy_reasoning_not_leaked_in_preview(self):
        """WorkBuddy reasoning 消息不在预览中作为裸 AI 正文显示。

        reasoning 的 content 留空，思考内容仅存于 THINKING part。
        预览时由 message_preview_text 的 THINKING 摘要回退逻辑处理：
        当 ASSISTANT 消息没有任何可见正文时，才会从 thinking 中提取摘要，
        并显式标注 ``[AI 思考摘要]`` 以与正文区分。因此原始思考内容不会
        作为裸正文出现。
        """
        from chat_exporter.adapters.workbuddy import WorkBuddyAdapter

        adapter = WorkBuddyAdapter()
        record = {
            "type": "reasoning",
            "content": "这是非常机密的内部推理",
            "timestamp": 0,
            "id": "r2",
        }
        msg = adapter._parse_record(record)
        preview = message_preview_text(msg, source_app="WorkBuddy")
        # 预览不应把原始思考内容当作裸 AI 正文显示（不能等于原始思考内容）
        self.assertNotEqual(preview, "这是非常机密的内部推理")
        # 若思考内容出现在预览中，必须带 [AI 思考摘要] 标签与正文区分
        if "非常机密的内部推理" in preview:
            self.assertIn("[AI 思考摘要]", preview)

    def test_preview_and_export_text_consistent(self):
        """预览文本和导出正文段落应从同一来源提取，内容一致。"""
        from chat_exporter.markdown_exporter import MarkdownExporter

        msg = Message(
            role=Role.ASSISTANT,
            content="",
            parts=[
                MessagePart(type=MessagePartType.TEXT, content="正文第一段"),
                MessagePart(type=MessagePartType.TEXT, content="正文第二段"),
                MessagePart(type=MessagePartType.THINKING, content="思考过程"),
                MessagePart(type=MessagePartType.TOOL_CALL, tool_name="shell", tool_input="ls"),
                MessagePart(type=MessagePartType.TOOL_RESULT, tool_output="file.txt"),
            ],
        )
        # 确保 content 等于 TEXT parts 的换行连接
        msg.content = "\n".join(p.content for p in msg.parts if p.type == MessagePartType.TEXT and p.content)

        conv = Conversation(id="test", title="一致性测试", messages=[msg], source_app="TRAE SOLO CN")

        # 预览文本
        preview = message_preview_text(msg, source_app="TRAE SOLO CN")
        # 导出 markdown
        md = MarkdownExporter().export(conv)

        # 预览和导出都应包含正文
        self.assertIn("正文第一段", preview)
        self.assertIn("正文第二段", md)
        self.assertIn("正文第一段", md)
        # 预览不含思考/工具
        self.assertNotIn("思考过程", preview)
        self.assertNotIn("file.txt", preview)
        # 导出含思考/工具
        self.assertIn("思考过程", md)
        self.assertIn("file.txt", md)

    def test_content_empty_but_parts_have_text(self):
        """content 为空但 parts 有 TEXT 时，预览和导出都从 parts 提取。"""
        from chat_exporter.markdown_exporter import MarkdownExporter

        msg = Message(
            role=Role.USER,
            content="",  # content 为空
            parts=[
                MessagePart(type=MessagePartType.TEXT, content="从parts提取的正文"),
            ],
        )
        conv = Conversation(id="test2", title="空content测试", messages=[msg], source_app="QClaw")

        preview = message_preview_text(msg, source_app="QClaw")
        md = MarkdownExporter().export(conv)

        self.assertIn("从parts提取的正文", preview)
        self.assertIn("从parts提取的正文", md)


if __name__ == "__main__":
    unittest.main()

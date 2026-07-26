"""v2 深度优化回归测试。

每条用例对应一个在真实本机数据或可复现脚本上验证过的缺陷，
既固定修复结果，也防止后续版本把同样的坑再挖一遍。
"""

import unittest

from chat_exporter.markdown_exporter import MarkdownExporter
from chat_exporter.models import Conversation, Message, MessagePart, MessagePartType, Role
from chat_exporter.preview_utils import message_preview_text, strip_internal_context, visible_messages


class OverStrippingTests(unittest.TestCase):
    """strip_internal_context 过度清洗导致的静默删正文。"""

    def test_unclosed_internal_tag_does_not_eat_rest_of_message(self):
        raw = "帮我看看这个注入例子：\n<system-reminder>\n真实用户内容第一行\n真实用户内容第二行"
        cleaned = strip_internal_context(raw, source_app="WorkBuddy")
        self.assertIn("真实用户内容第一行", cleaned)
        self.assertIn("真实用户内容第二行", cleaned)

    def test_tag_with_internal_prefix_is_not_treated_as_internal(self):
        """<current_timezone> 只是和内部标签 current_time 撞了前缀，不该清掉整条消息。"""
        raw = "<current_timezone>UTC+8</current_timezone>\n用户真正想问的问题在这里"
        cleaned = strip_internal_context(raw, source_app="WorkBuddy")
        self.assertIn("用户真正想问的问题在这里", cleaned)

    def test_environment_looking_lines_survive_without_injected_context(self):
        """没有注入上下文时，用户自己敲的这些行是正文，不是环境噪声。"""
        for raw in (
            "Shell: 我该用哪个？",
            "OS Version: 我的系统是 Win11，兼容吗？",
            "Current working directory: 这个概念是什么意思",
        ):
            self.assertEqual(strip_internal_context(raw, source_app="WorkBuddy"), raw)

    def test_environment_lines_still_stripped_inside_injected_context(self):
        """但同样的行出现在注入块残留里时仍要清掉。"""
        raw = (
            "<user_info>\nOS Version: win32\nShell: bash\n</user_info>\n"
            "<system-reminder>x</system-reminder>\nOS Version: win32\n真正的问题"
        )
        cleaned = strip_internal_context(raw, source_app="WorkBuddy")
        self.assertIn("真正的问题", cleaned)
        self.assertNotIn("win32", cleaned)


class FencedContentTests(unittest.TestCase):
    """围栏代码块里的内容是用户在讨论的正文，不是运行时注入。"""

    def test_internal_tag_inside_code_block_is_preserved(self):
        raw = "看这段配置：\n\n```xml\n<current_time>2026-07-26</current_time>\n```\n\n这是什么意思？"
        cleaned = strip_internal_context(raw, source_app="WorkBuddy")
        self.assertIn("current_time", cleaned)
        self.assertIn("这是什么意思", cleaned)

    def test_workbuddy_trailing_cleanup_keeps_closing_fence(self):
        """WorkBuddy 的尾部清理曾经把回答结尾的闭合围栏 pop 掉，导出的 Markdown 就此破损。"""
        raw = "可以这样写：\n\n```python\nprint('ok')\n```"
        cleaned = strip_internal_context(raw, source_app="WorkBuddy")
        self.assertEqual(cleaned.count("```"), 2)

    def test_injected_context_outside_fence_still_removed(self):
        raw = "<system-reminder>\nOS Version: win32\n</system-reminder>\n真正的问题"
        self.assertEqual(strip_internal_context(raw, source_app="WorkBuddy"), "真正的问题")


class FenceCollisionTests(unittest.TestCase):
    """内容自带围栏时，固定长度的 ``` / ~~~ 会提前闭合。"""

    def test_code_part_containing_backtick_fence(self):
        msg = Message(
            role=Role.ASSISTANT,
            parts=[MessagePart(type=MessagePartType.CODE, language="md", content="示例:\n```\ninner\n```")],
        )
        conv = Conversation(id="c", title="t", messages=[msg], source_app="TRAE SOLO CN")
        md = MarkdownExporter().export(conv)
        self.assertIn("````md", md)
        self.assertIn("inner", md)

    def test_thinking_containing_tilde_fence_stays_inside_details(self):
        msg = Message(
            role=Role.ASSISTANT,
            parts=[MessagePart(type=MessagePartType.THINKING, content="思考\n~~~\n不该泄漏的内容\n~~~")],
        )
        conv = Conversation(id="c", title="t", messages=[msg], source_app="WorkBuddy")
        md = MarkdownExporter().export(conv)
        body = md.split("<details>", 1)[1].split("</details>", 1)[0]
        self.assertIn("不该泄漏的内容", body)

    def test_tool_result_containing_tilde_fence(self):
        msg = Message(
            role=Role.ASSISTANT,
            parts=[
                MessagePart(type=MessagePartType.TEXT, content="结果如下"),
                MessagePart(type=MessagePartType.TOOL_RESULT, tool_output="~~~\nfetched doc\n~~~"),
            ],
        )
        conv = Conversation(id="c", title="t", messages=[msg], source_app="WorkBuddy")
        md = MarkdownExporter().export(conv)
        body = md.split("<details>", 1)[1].split("</details>", 1)[0]
        self.assertIn("fetched doc", body)


class PartOrderTests(unittest.TestCase):
    def test_text_and_code_keep_original_interleaving(self):
        msg = Message(
            role=Role.ASSISTANT,
            parts=[
                MessagePart(type=MessagePartType.TEXT, content="步骤一如下"),
                MessagePart(type=MessagePartType.CODE, language="py", content="step_one()"),
                MessagePart(type=MessagePartType.TEXT, content="步骤二如下"),
                MessagePart(type=MessagePartType.CODE, language="py", content="step_two()"),
            ],
        )
        conv = Conversation(id="c", title="t", messages=[msg], source_app="QoderWork CN")
        md = MarkdownExporter().export(conv)
        self.assertLess(md.index("步骤一如下"), md.index("step_one()"))
        self.assertLess(md.index("step_one()"), md.index("步骤二如下"))
        self.assertLess(md.index("步骤二如下"), md.index("step_two()"))


class FallbackBodyTests(unittest.TestCase):
    def test_whitespace_only_text_part_does_not_suppress_fallback(self):
        """空白 TEXT part 曾经算作"有正文"，最终交付因此从导出里消失。"""
        msg = Message(
            role=Role.ASSISTANT,
            parts=[
                MessagePart(type=MessagePartType.TEXT, content="   \n  "),
                MessagePart(type=MessagePartType.THINKING, content="这才是最终交付内容"),
            ],
        )
        conv = Conversation(id="c", title="t", messages=[msg], source_app="TRAE SOLO CN")
        md = MarkdownExporter().export(conv)
        self.assertIn("这才是最终交付内容", md)
        self.assertTrue(message_preview_text(msg, source_app="TRAE SOLO CN"))


class CleanPreviewModeTests(unittest.TestCase):
    """“只看对话”开关：预览可以筛，导出必须完整。"""

    def _conv(self):
        answer = Message(
            role=Role.ASSISTANT,
            parts=[
                MessagePart(type=MessagePartType.THINKING, content="我先想想该怎么回答"),
                MessagePart(type=MessagePartType.TOOL_RESULT, tool_output="工具跑出来的一堆日志"),
            ],
        )
        return Conversation(
            id="c",
            title="t",
            source_app="TRAE SOLO CN",
            messages=[
                Message(role=Role.USER, content="帮我看看"),
                answer,
                Message(role=Role.ASSISTANT, content="这是最终回答"),
            ],
        )

    def test_default_preview_falls_back_to_thinking(self):
        visible = visible_messages(self._conv())
        self.assertEqual(len(visible), 3)
        self.assertTrue(any("我先想想" in text for _m, _r, text in visible))

    def test_clean_mode_hides_thinking_and_tool_only_messages(self):
        visible = visible_messages(self._conv(), include_fallback=False)
        self.assertEqual(len(visible), 2)
        joined = "\n".join(text for _m, _r, text in visible)
        self.assertNotIn("我先想想", joined)
        self.assertNotIn("工具跑出来", joined)
        self.assertIn("这是最终回答", joined)

    def test_clean_mode_never_affects_export(self):
        md = MarkdownExporter().export(self._conv())
        self.assertIn("我先想想", md)
        self.assertIn("工具跑出来", md)
        self.assertIn("这是最终回答", md)

    def test_plain_text_copy_follows_the_mode(self):
        from chat_exporter.preview_utils import plain_preview_text

        self.assertIn("我先想想", plain_preview_text(self._conv()))
        self.assertNotIn("我先想想", plain_preview_text(self._conv(), include_fallback=False))


class PreviewSegmentTests(unittest.TestCase):
    """预览渲染分段：行内代码与围栏代码块。"""

    @staticmethod
    def _segments(text, tag="assistant_body"):
        from chat_exporter.gui_cn_v2 import ChatExporterGUI

        return ChatExporterGUI._body_segments(text, tag)

    def test_inline_code_gets_its_own_tag(self):
        segs = self._segments("运行 `pip install -r requirements.txt` 就行")
        tags = [tag for _text, tag in segs]
        self.assertIn("inline_code", tags)
        code = [t for t, tag in segs if tag == "inline_code"]
        self.assertEqual(code, ["pip install -r requirements.txt"])

    def test_inline_code_does_not_lose_surrounding_text(self):
        segs = self._segments("先 `a` 再 `b` 最后收尾")
        joined = "".join(text for text, _tag in segs)
        self.assertIn("先 ", joined)
        self.assertIn("再 ", joined)
        self.assertIn("最后收尾", joined)
        self.assertEqual(joined.count("`"), 0)

    def test_fenced_block_is_tagged_as_code_block(self):
        segs = self._segments("看这段：\n```python\nprint(1)\n```\n完事")
        self.assertIn("code_block", [tag for _t, tag in segs])
        block = [t for t, tag in segs if tag == "code_block"]
        self.assertTrue(any("print(1)" in b for b in block))

    def test_unclosed_fence_still_keeps_all_text(self):
        segs = self._segments("开头\n```python\nprint(1)\n没闭合就断了")
        joined = "".join(text for text, _tag in segs)
        self.assertIn("开头", joined)
        self.assertIn("print(1)", joined)
        self.assertIn("没闭合就断了", joined)


if __name__ == "__main__":
    unittest.main()

"""共享 Markdown 解析器的两个后端。

最重要的一条是 test_tk_output_matches_golden：解析器是从 GUI 里抽出来的，
抽取**不允许**改变预览面板已有的渲染结果。md_golden.json 是抽取前用旧代码
生成的快照，它跑绿才说明这次是纯重构。
"""
from __future__ import annotations

import json
import os
import unittest
from html.parser import HTMLParser

from chat_exporter.markdown_render import (
    Span,
    parse,
    parse_inline,
    render_html,
    render_tk,
)

from md_corpus import CORPUS

_HERE = os.path.dirname(os.path.abspath(__file__))
_VOID = {"br", "hr", "img", "input", "meta", "link", "col", "area", "base"}


def _normalize(segments):
    return [[text, list(tag) if isinstance(tag, tuple) else tag] for text, tag in segments]


class _Balance(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in _VOID:
            return
        if not self.stack:
            self.errors.append(f"多余的 </{tag}>")
        elif self.stack[-1] != tag:
            self.errors.append(f"交叉嵌套：期望 </{self.stack[-1]}>，得到 </{tag}>")
        else:
            self.stack.pop()


class TkBackendGoldenTests(unittest.TestCase):
    """抽取解析器不得改变预览面板的输出。"""

    def test_tk_output_matches_golden(self):
        with open(os.path.join(_HERE, "md_golden.json"), encoding="utf-8") as handle:
            golden = json.load(handle)
        self.assertEqual(set(golden), set(CORPUS), "金样与语料不同步，请重新生成")
        for name, text in CORPUS.items():
            with self.subTest(case=name):
                self.assertEqual(_normalize(render_tk(text, "assistant_body")), golden[name])

    def test_gui_entry_point_delegates(self):
        """GUI 的 _body_segments 必须与共享实现同源，别再各写一份。"""
        from chat_exporter.gui_cn_v2 import ChatExporterGUI

        for name, text in CORPUS.items():
            with self.subTest(case=name):
                self.assertEqual(
                    _normalize(ChatExporterGUI._body_segments(text, "assistant_body")),
                    _normalize(render_tk(text, "assistant_body")),
                )

    def test_never_drops_content(self):
        """任何输入都不许把正文吞掉。"""
        for name, text in CORPUS.items():
            if not text.strip():
                continue
            with self.subTest(case=name):
                rendered = "".join(t for t, _ in render_tk(text, "assistant_body"))
                self.assertTrue(rendered.strip(), f"{name} 渲染成了空")


class InlineParseTests(unittest.TestCase):
    def test_plain_text_is_single_span(self):
        self.assertEqual(parse_inline("普通文字"), (Span("text", "普通文字"),))

    def test_code_bold_link(self):
        spans = parse_inline("装 `foo`，看 **重点**，读 [文档](https://e.com)")
        kinds = [s.kind for s in spans]
        self.assertIn("code", kinds)
        self.assertIn("bold", kinds)
        self.assertIn("link", kinds)
        link = next(s for s in spans if s.kind == "link")
        self.assertEqual(link.text, "文档")
        self.assertEqual(link.href, "https://e.com")

    def test_code_wins_over_bold_when_overlapping(self):
        """INLINE_MD 里代码在前，`**a**` 整体算代码——这是既有行为。"""
        spans = parse_inline("`**a**`")
        self.assertEqual(spans[0].kind, "code")
        self.assertEqual(spans[0].text, "**a**")

    def test_link_requires_non_empty_href(self):
        spans = parse_inline("[文字]()")
        self.assertEqual([s.kind for s in spans], ["text"])

    def test_spans_roundtrip_all_characters(self):
        text = "前 `c` 中 **b** 后 [l](https://e.com) 尾"
        joined = "".join(s.text for s in parse_inline(text))
        for token in ("前", "c", "中", "b", "后", "l", "尾"):
            self.assertIn(token, joined)


class FenceTests(unittest.TestCase):
    def test_info_string_fence_stays_inside_code(self):
        """CommonMark：闭合围栏不许带 info string。"""
        blocks = parse("```markdown\n先写围栏：\n```python\nprint(1)\n```\n之后")
        code = [b for b in blocks if b.kind == "code"]
        self.assertTrue(code)
        self.assertIn("```python", code[0].text)

    def test_unclosed_fence_is_kept(self):
        blocks = parse("```python\nprint(1)\n没有闭合")
        code = [b for b in blocks if b.kind == "code"]
        self.assertTrue(code)
        self.assertIn("print(1)", code[0].text)
        self.assertIn("没有闭合", code[0].text)

    def test_language_is_captured(self):
        blocks = parse("```python\nx=1\n```")
        self.assertEqual(blocks[0].lang, "python")


class HtmlBackendTests(unittest.TestCase):
    def test_all_corpus_produces_balanced_html(self):
        for name, text in CORPUS.items():
            with self.subTest(case=name):
                checker = _Balance()
                checker.feed(render_html(text))
                checker.close()
                self.assertFalse(checker.errors, f"{name}: {checker.errors}")
                self.assertFalse(checker.stack, f"{name}: 未闭合 {checker.stack}")

    def test_markdown_is_actually_rendered(self):
        html = render_html(
            "# 标题\n\n**粗** 与 `码` 与 [链](https://e.com)\n\n"
            "- 项\n\n1. 步\n\n> 引\n\n---\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        )
        for tag in ("<h1>", "<strong>", "<code>", "<a href=", "<ul>", "<ol>",
                    "<blockquote", "<hr>", "<table>", "<thead>", "<td>"):
            self.assertIn(tag, html, f"缺少 {tag}")

    def test_no_literal_markdown_leaks(self):
        html = render_html("**粗**\n\n# 标题\n\n| A |\n|---|\n| 1 |")
        self.assertNotIn("**粗**", html)
        self.assertNotIn("# 标题", html)
        self.assertNotIn("| A |", html)

    def test_html_in_content_is_escaped(self):
        html = render_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_html_inside_code_fence_is_escaped(self):
        html = render_html("```\n<script>alert(1)</script>\n```")
        self.assertNotIn("<script>", html)

    def test_dangerous_schemes_are_not_linked(self):
        for bad in ("javascript:alert(1)", "data:text/html,<b>x</b>", "vbscript:x"):
            with self.subTest(href=bad):
                html = render_html(f"[点我]({bad})")
                self.assertNotIn('href="' + bad.split(":")[0], html)
                self.assertIn("点我", html)  # 内容不能被吞掉

    def test_scheme_relative_authority_is_not_linked(self):
        for bad in ("//evil.com/x", "/\\evil.com/x", "///evil.com/x"):
            with self.subTest(href=bad):
                html = render_html(f"[点我]({bad})")
                self.assertNotIn("<a ", html)
                self.assertIn("点我", html)

    def test_ordered_list_preserves_start_and_gaps(self):
        started = render_html("3. 甲\n4. 乙")
        self.assertIn('<ol start="3">', started)
        self.assertNotIn('value="4"', started)

        gapped = render_html("1. 一\n5. 五\n9. 九")
        self.assertIn("<ol>", gapped)
        self.assertIn('<li value="5">五</li>', gapped)
        self.assertIn('<li value="9">九</li>', gapped)

    def test_nested_ordered_list_preserves_start(self):
        html = render_html("1. 顶层\n   3. 嵌套")
        self.assertIn('<ol><li>顶层<ol start="3"><li>嵌套</li></ol></li></ol>', html)

    def test_indented_ordered_list_without_parent_is_repaired(self):
        """脏数据里只有缩进项时也应生成合法 HTML，不能让整条导出崩掉。"""
        html = render_html("   3. 孤立项\n   4. 下一项")
        self.assertEqual(
            html,
            '<ol><li><ol start="3"><li>孤立项</li><li>下一项</li></ol></li></ol>',
        )

    def test_empty_fence_language_survives_between_paragraphs(self):
        html = render_html("说明如下。\n\n```python\n```\n\n结束。")
        self.assertIn('<span class="lang">python</span>', html)
        self.assertIn("<code></code>", html)
        self.assertIn("说明如下。", html)
        self.assertIn("结束。", html)

    def test_single_dash_table_separator_is_header(self):
        html = render_html("| A | B |\n|-|-|\n| 1 | 2 |")
        self.assertIn("<thead>", html)
        self.assertNotIn("<td>-</td>", html)

    def test_blank_table_separator_is_not_header(self):
        html = render_html("| A | B |\n| | |\n| 1 | 2 |")
        self.assertNotIn("<thead>", html)
        self.assertIn("<td>A</td>", html)

    def test_heading_trailing_hash_without_space_is_preserved(self):
        self.assertEqual(render_html("## C#"), "<h2>C#</h2>")
        self.assertEqual(render_html("## 标题 ##"), "<h2>标题</h2>")

    def test_safe_schemes_are_linked(self):
        for good in ("https://e.com/a", "http://e.com", "mailto:a@e.com", "./rel.html", "#anchor"):
            with self.subTest(href=good):
                self.assertIn(f'href="{good}"', render_html(f"[文字]({good})"))

    def test_link_href_is_attribute_escaped(self):
        html = render_html('[x](https://e.com/a?b=1&c=2)')
        self.assertIn("b=1&amp;c=2", html)
        self.assertNotIn('?b=1&c=2"', html)

    def test_table_header_detected(self):
        html = render_html("| A | B |\n|---|---|\n| 1 | 2 |")
        self.assertIn("<thead>", html)
        self.assertIn("<th>A</th>", html)
        self.assertIn("<td>1</td>", html)

    def test_table_without_separator_has_no_header(self):
        html = render_html("| 只有 | 一行 |")
        self.assertNotIn("<thead>", html)
        self.assertIn("<td>只有</td>", html)

    def test_nested_list_is_inside_parent_item(self):
        """子列表必须在 <li> 内部，不能是 <ul> 的直接兄弟。"""
        html = render_html("- 顶层\n  - 嵌套")
        self.assertIn("<li>顶层<ul><li>嵌套</li></ul></li>", html)

    def test_numbered_list_uses_ol(self):
        html = render_html("1. 一\n2. 二")
        self.assertIn("<ol>", html)
        self.assertIn("<li>一</li>", html)

    def test_code_block_language_label(self):
        html = render_html("```python\nx=1\n```")
        self.assertIn('<span class="lang">python</span>', html)
        self.assertIn("<pre>", html)

    def test_empty_input_produces_nothing(self):
        self.assertEqual(render_html(""), "")
        self.assertEqual(render_html("   \n  "), "")

    def test_plain_text_still_wrapped_in_para(self):
        self.assertEqual(render_html("就一句话"), '<div class="para">就一句话</div>')

    def test_paragraph_line_breaks_preserved(self):
        """.para 是 pre-wrap，换行靠真实换行符保留。"""
        html = render_html("第一行\n第二行")
        self.assertIn("第一行\n第二行", html)


class ContentPreservationTests(unittest.TestCase):
    """导出必须全量——这是本项目唯一一条不可让步的约束。"""

    @staticmethod
    def _visible(html: str) -> str:
        import html as html_mod
        import re

        return html_mod.unescape(re.sub(r"<[^>]+>", "", html))

    @staticmethod
    def _words(text: str):
        import re

        return set(re.findall(r"[一-鿿]{4,}|[A-Za-z_][A-Za-z0-9_]{3,}", text))

    def test_html_keeps_every_content_word(self):
        """链接地址会进 href 属性，所以在整段 HTML（含属性）里找。"""
        for name, text in CORPUS.items():
            if not text.strip():
                continue
            with self.subTest(case=name):
                html = render_html(text)
                for word in self._words(text):
                    self.assertIn(word, html, f"{name}: 丢了 {word!r}")

    def test_html_preserves_at_least_as_much_as_tk(self):
        """HTML 后端不能比预览面板丢得更多。"""
        for name, text in CORPUS.items():
            if not text.strip():
                continue
            with self.subTest(case=name):
                tk_text = "".join(t for t, _ in render_tk(text, "b"))
                html = render_html(text)
                tk_only = {w for w in self._words(text) if w in tk_text} - self._words(html)
                self.assertFalse(tk_only, f"{name}: 预览有但 HTML 没有 {tk_only}")

    def test_other_exporters_untouched_by_markdown_change(self):
        """只有 HTML 分支改了渲染；Markdown/JSON/纯文本仍是逐字存档。"""
        from chat_exporter.exporters import (
            JsonExporter,
            MarkdownFormatExporter,
            TextExporter,
        )
        from chat_exporter.models import (
            Conversation,
            Message,
            MessagePart,
            MessagePartType,
            Role,
        )

        body = "# 标题\n\n**粗** 与 `码`\n\n| A | B |\n|---|---|\n| 1 | 2 |"
        conv = Conversation(
            id="t", title="标题", source_app="trae",
            messages=[Message(role=Role.ASSISTANT,
                              parts=[MessagePart(type=MessagePartType.TEXT, content=body)])],
        )
        for exporter in (MarkdownFormatExporter(), JsonExporter(), TextExporter()):
            with self.subTest(fmt=exporter.format_id):
                out = exporter.render(conv)
                self.assertIn("**粗**", out, "标记源码必须原样保留")
                self.assertIn("# 标题", out)


class ExporterIntegrationTests(unittest.TestCase):
    def _render(self, body: str) -> str:
        from chat_exporter.exporters import HtmlExporter
        from chat_exporter.models import (
            Conversation,
            Message,
            MessagePart,
            MessagePartType,
            Role,
        )

        conv = Conversation(
            id="t", title="标题", source_app="trae",
            messages=[Message(role=Role.ASSISTANT,
                              parts=[MessagePart(type=MessagePartType.TEXT, content=body)])],
        )
        return HtmlExporter().render(conv)

    def test_export_renders_markdown(self):
        html = self._render("## 小标题\n\n**重点**\n\n| A |\n|---|\n| 1 |")
        self.assertIn("<h2>小标题</h2>", html)
        self.assertIn("<strong>重点</strong>", html)
        self.assertIn("<table>", html)

    def test_export_still_escapes_hostile_content(self):
        html = self._render("<img src=x onerror=alert(1)>")
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img", html)

    def test_conversation_title_heading_not_affected(self):
        """正文里的 h1 样式作用域收在 .msg 内，不能影响标题区。"""
        html = self._render("正文")
        self.assertIn('<header class="conv-head"><h1>标题</h1>', html)
        # 标题区的 h1 由 .conv-head h1 单独控制，与 .msg h1 互不干扰
        self.assertIn(".conv-head h1 {", html)
        self.assertIn(".msg h1 {", html)

    def test_body_first_child_margin_reset_targets_real_container(self):
        """正文包在 .msg-body 里，选择器写成 .msg > :first-child 会落空。"""
        html = self._render("# 标题\n\n正文")
        self.assertIn('<div class="msg-body">', html)
        self.assertIn(".msg-body > :first-child { margin-top: 0; }", html)

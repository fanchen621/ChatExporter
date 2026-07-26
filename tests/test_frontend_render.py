# -*- coding: utf-8 -*-
"""「预览 = 前端渲染结果」的回归测试。

真实数据形态（0726 探针实测）：
- TRAE：assistant 消息 100% 没有 text part，最终回答在 finish 工具调用的
  summary 参数里（8 条对话 380 次 finish）——前端渲染的就是它。
- QClaw：调用行的 part_type 是 'tool'（14k+ 行）而不是 'tool_call'，
  旧解析全部静默丢弃；另有 'compaction' 压缩摘要行。
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_exporter.adapters.qclaw_compat import QClawAdapter
from chat_exporter.models import Conversation, Message, MessagePart, MessagePartType, Role
from chat_exporter.preview_utils import (
    PREVIEW_CLEAN,
    PREVIEW_CLEAN_CONCISE,
    PREVIEW_CLEAN_STRICT,
    message_preview_text,
    resolve_preview_mode,
    visible_messages,
)


def _finish_call(summary):
    return MessagePart(
        type=MessagePartType.TOOL_CALL,
        tool_name="finish",
        tool_input=json.dumps({"summary": summary}, ensure_ascii=False),
    )


def _trae_turn(summary=None, thinking="推理：先看配置再改代码。"):
    parts = [
        MessagePart(type=MessagePartType.THINKING, content=thinking),
        MessagePart(type=MessagePartType.TOOL_CALL, tool_name="RunCommand", tool_input='{"cmd":"ls"}'),
        MessagePart(type=MessagePartType.TOOL_RESULT, tool_output="ok"),
    ]
    if summary is not None:
        parts.append(_finish_call(summary))
    return Message(role=Role.ASSISTANT, parts=parts)


def _conv(messages):
    return Conversation(id="c1", title="t", source_app="TRAE SOLO CN", messages=messages)


class FinishSummaryTests(unittest.TestCase):
    def test_finish_summary_is_the_visible_body(self):
        msg = _trae_turn(summary="## 修复完成\n\n改了 3 个文件，测试全过。")
        text = message_preview_text(msg, source_app="TRAE SOLO CN")
        assert "修复完成" in text
        assert "推理：" not in text  # 有 finish 时不再用推理当正文

    def test_finish_makes_clean_mode_strict(self):
        conv = _conv([
            Message(role=Role.USER, content="修一下这个 bug"),
            _trae_turn(summary="修好了。"),
            _trae_turn(summary=None),  # 纯机器轮次
        ])
        assert resolve_preview_mode(conv, PREVIEW_CLEAN) == PREVIEW_CLEAN_STRICT
        vis = visible_messages(conv, mode=PREVIEW_CLEAN)
        texts = [t for _m, _r, t in vis]
        assert any("修好了" in t for t in texts)
        # strict 下纯机器轮次整条隐藏
        assert len(vis) == 2

    def test_no_finish_anywhere_falls_back_to_concise(self):
        conv = _conv([
            Message(role=Role.USER, content="问题"),
            _trae_turn(summary=None, thinking="结论：应该用方案 B。"),
        ])
        assert resolve_preview_mode(conv, PREVIEW_CLEAN) == PREVIEW_CLEAN_CONCISE
        vis = visible_messages(conv, mode=PREVIEW_CLEAN)
        assert any("方案 B" in t for _m, _r, t in vis)

    def test_empty_summary_does_not_count_as_answer(self):
        msg = _trae_turn(summary="")
        text = message_preview_text(msg, source_app="TRAE SOLO CN")
        # 空 summary 不算正文，full 模式回退到推理
        assert "推理：" in text

    def test_truncated_json_input_is_skipped_not_shown(self):
        msg = Message(role=Role.ASSISTANT, parts=[
            MessagePart(type=MessagePartType.TOOL_CALL, tool_name="finish",
                        tool_input='{"summary": "半截'),
            MessagePart(type=MessagePartType.THINKING, content="备用推理"),
        ])
        text = message_preview_text(msg, source_app="TRAE SOLO CN")
        assert "半截" not in text

    def test_plain_text_input_variant(self):
        msg = Message(role=Role.ASSISTANT, parts=[
            MessagePart(type=MessagePartType.TOOL_CALL, tool_name="attempt_completion",
                        tool_input="任务完成，产物在 dist/ 下。"),
        ])
        text = message_preview_text(msg)
        assert "任务完成" in text

    def test_unknown_tools_stay_machinery(self):
        msg = Message(role=Role.ASSISTANT, parts=[
            MessagePart(type=MessagePartType.TOOL_CALL, tool_name="RunCommand",
                        tool_input='{"summary": "这是命令参数不是回答"}'),
        ])
        conv = _conv([msg])
        assert resolve_preview_mode(conv, PREVIEW_CLEAN) == PREVIEW_CLEAN_CONCISE
        assert "命令参数" not in message_preview_text(msg)

    def test_text_part_still_leads_when_both_exist(self):
        msg = Message(role=Role.ASSISTANT, parts=[
            MessagePart(type=MessagePartType.TEXT, content="正文回答。"),
            _finish_call("完成摘要。"),
        ])
        text = message_preview_text(msg)
        assert text.startswith("正文回答。")
        assert "完成摘要。" in text


class QClawToolPartTests(unittest.TestCase):
    def _parse(self, part_rows, role="assistant", content=""):
        adapter = QClawAdapter.__new__(QClawAdapter)  # 不触发 __init__ 的磁盘探测
        msg_row = {"role": role, "content": content, "created_at": None,
                   "message_id": 1, "token_count": 0, "seq": 1}
        return adapter._parse_message(msg_row, part_rows)

    @staticmethod
    def _row(**kw):
        base = {"part_type": "", "text_content": "", "tool_name": None,
                "tool_input": None, "tool_output": None, "tool_error": None,
                "file_name": None, "ordinal": 0, "metadata": None}
        base.update(kw)
        return base

    def test_tool_part_type_becomes_tool_call(self):
        msg = self._parse([self._row(part_type="tool", tool_name="exec",
                                     tool_input='{"command":"ls"}')])
        kinds = [p.type for p in msg.parts]
        assert MessagePartType.TOOL_CALL in kinds
        call = next(p for p in msg.parts if p.type == MessagePartType.TOOL_CALL)
        assert call.tool_name == "exec"
        assert "ls" in call.tool_input

    def test_tool_part_with_output_also_emits_result(self):
        msg = self._parse([self._row(part_type="tool", tool_name="exec",
                                     tool_input='{"command":"ls"}', tool_output="file.txt")])
        kinds = [p.type for p in msg.parts]
        assert MessagePartType.TOOL_CALL in kinds
        assert MessagePartType.TOOL_RESULT in kinds

    def test_compaction_stays_out_of_reading_view(self):
        """压缩日志挂在 role=system 上；映射成 THINKING 会触发
        SYSTEM+THINKING→ASSISTANT 角色提升，60 条压缩日志伪装成 AI 发言。"""
        from chat_exporter.preview_utils import effective_role

        msg = self._parse(
            [self._row(part_type="compaction", text_content="LCM compaction leaf pass: 35654 -> 16738")],
            role="system",
            content="LCM compaction leaf pass: 35654 -> 16738",
        )
        assert MessagePartType.THINKING not in [p.type for p in msg.parts]
        assert effective_role(msg) is None  # 预览隐藏
        # 原文不丢：仍以 TEXT 形式保留在消息里
        assert any("compaction" in (p.content or "") for p in msg.parts)

    def test_json_block_content_expands_instead_of_leaking_raw(self):
        """420 行 messages.content 是内容块 JSON 数组，不能裸奔进预览。"""
        raw = json.dumps([
            {"type": "thinking", "thinking": "先分析一下", "thinkingSignature": "x"},
            {"type": "toolCall", "id": "t1", "name": "exec", "arguments": {"command": "ls"}},
            {"type": "text", "text": "这是真正的回答。"},
        ], ensure_ascii=False)
        msg = self._parse([], content=raw)
        kinds = [p.type for p in msg.parts]
        assert MessagePartType.THINKING in kinds
        assert MessagePartType.TOOL_CALL in kinds
        assert MessagePartType.TEXT in kinds
        assert msg.content == "这是真正的回答。"
        assert '[{"type"' not in msg.content

    def test_empty_text_block_array_yields_empty_not_raw_json(self):
        msg = self._parse([], content='[{"type":"text","text":""}]')
        assert msg.content == ""
        assert not any('[{"type"' in (p.content or "") for p in msg.parts)

    def test_unparseable_json_content_falls_back_verbatim(self):
        raw = '[{"type": "mystery_block", "data": 1}]'
        msg = self._parse([], content=raw)
        # 未知块类型：整体放弃展开，原文回退，绝不丢字
        assert msg.content == raw

    def test_result_only_tool_row_becomes_tool_result(self):
        """tool_input 为空、text_content 是结果占位文本的 'tool' 行是结果不是调用。"""
        msg = self._parse([self._row(
            part_type="tool", tool_name="exec",
            text_content="[LCM Tool Output: file_xxx | tool=exec | 230,665 bytes]",
        )], role="tool")
        kinds = [p.type for p in msg.parts]
        assert MessagePartType.TOOL_RESULT in kinds
        assert MessagePartType.TOOL_CALL not in kinds


class TurnLevelCleanTests(unittest.TestCase):
    """「只看对话」按轮裁决：一次 finish 不该让被中止任务的 AI 轮次整体消失。"""

    def test_aborted_turn_keeps_its_thinking_conclusion(self):
        conv = _conv([
            Message(role=Role.USER, content="任务一"),
            _trae_turn(summary="任务一完成。"),
            Message(role=Role.USER, content="任务二"),
            _trae_turn(summary=None, thinking="任务二推进到一半被中止，结论：需要先解决权限。"),
        ])
        vis = visible_messages(conv, mode=PREVIEW_CLEAN)
        texts = [t for _m, _r, t in vis]
        assert any("任务一完成" in t for t in texts)
        # 被中止的轮次保住末块推理，用户提问后 AI 不会凭空消失
        assert any("先解决权限" in t for t in texts)

    def test_machinery_in_finished_turn_still_hidden(self):
        conv = _conv([
            Message(role=Role.USER, content="任务"),
            _trae_turn(summary=None),      # 同轮机器动作
            _trae_turn(summary="完成。"),
        ])
        vis = visible_messages(conv, mode=PREVIEW_CLEAN)
        assert len(vis) == 2  # 用户 + 带 finish 的那条


class SearchCoverageTests(unittest.TestCase):
    def test_thinking_stays_searchable_even_with_finish(self):
        from chat_exporter.preview_utils import conversation_search_text

        conv = _conv([
            Message(role=Role.USER, content="问题"),
            _trae_turn(summary="最终交付。", thinking="中间报错 ECONNRESET 已定位"),
        ])
        text = conversation_search_text(conv)
        assert "最终交付" in text
        assert "econnreset" in text  # 推理内容仍可搜（casefold 后）

    def test_stamp_carries_logic_version(self):
        from chat_exporter.search_index import conversation_stamp

        conv = _conv([])
        assert conversation_stamp(conv).startswith("v")


class RendererHardeningTests(unittest.TestCase):
    @staticmethod
    def _segments(text, tag="assistant_body"):
        from chat_exporter.gui_cn_v2 import ChatExporterGUI

        return ChatExporterGUI._body_segments(text, tag)

    def test_header_regex_is_linear_on_hash_floods(self):
        import time

        line = "# abc" + "#" * 30000 + "x"
        start = time.perf_counter()
        self._segments(line)
        assert time.perf_counter() - start < 0.5  # 旧惰性正则在此输入上要跑数十秒

    def test_info_string_fence_inside_block_stays_code(self):
        """CommonMark：闭合围栏不允许带 info string，```python 行属于内容。"""
        text = (
            "教程如下：\n"
            "```markdown\n"
            "先写围栏：\n"
            "```python\n"
            "print(1)\n"
            "```\n"
            "真正文结束"
        )
        segs = self._segments(text)
        code = "".join(t for t, tag in segs if tag == "code_block")
        assert "```python" in code  # 内层围栏行留在代码块里
        assert "print(1)" in code
        joined = "".join(t for t, _tag in segs)
        assert "真正文结束" in joined

    def test_header_link_reduced_to_label(self):
        segs = self._segments("## 参考 [文档](https://example.com) 一节")
        headers = [t for t, tag in segs if tag == "md_h2"]
        assert headers and "文档" in headers[0]
        assert "example.com" not in headers[0]

    def test_trailing_hashes_stripped_from_header(self):
        segs = self._segments("## 标题 ##")
        headers = [t for t, tag in segs if tag == "md_h2"]
        assert headers and headers[0].strip() == "标题"

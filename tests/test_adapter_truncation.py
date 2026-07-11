"""全面回归测试：验证各应用适配器与 Markdown 导出都不会截断真实内容。

覆盖"预览/导出尾部被砍"这类问题的所有已知根因：
  1. TRAE task 消息：优先取 TEXT 正文，不把思考过程当正文、不截断。
  2. TRAE 工具结果 / plan_item 工具结果：content 与 tool_output 均完整，无 [:5000]/[:10000] 上限。
  3. TRAE 解析兜底（JSON 损坏）：原始内容完整，不再 [:2000]。
  4. QClaw 工具结果：content 完整，不再 [:5000]。
  5. QoderWork / Marvis / WorkBuddy：用户/AI 正文完整，无长度上限。
  6. Markdown 导出：工具返回结果不再在 2000 字符处硬截断。

运行：
    python tests/test_adapter_truncation.py
或（仓库已配置 pytest）：
    pytest tests/test_adapter_truncation.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from chat_exporter.adapters.trae import TraeAdapter
from chat_exporter.adapters.qclaw import QClawAdapter
from chat_exporter.adapters.qoderwork import QoderWorkAdapter
from chat_exporter.adapters.marvis import MarvisAdapter
from chat_exporter.adapters.workbuddy import WorkBuddyAdapter
from chat_exporter.markdown_exporter import MarkdownExporter
from chat_exporter.models import Message, MessagePart, MessagePartType, Role


def test_trae_general_content_full():
    a = TraeAdapter()
    long = "A" * 12345
    parts = a._parse_general_content(json.dumps([{"text_content": long}]))
    assert parts and parts[0].content == long, "general 文本应完整，不应被截断"


def test_trae_task_prefers_text_over_thinking():
    a = TraeAdapter()
    real = "REAL ANSWER " * 500
    think = "think " * 500
    content = json.dumps({"messages": [
        {"type": "text", "text": real},
        {"type": "thinking", "content": think},
    ]})
    parts = a._parse_task_content(content)
    text = "".join(p.content for p in parts if p.type == MessagePartType.TEXT)
    assert real in text, "task 消息应取真实文本正文而非思考过程"


def test_trae_task_tool_result_full():
    a = TraeAdapter()
    big = "X" * 20000
    content = json.dumps({"messages": [{"type": "tool_result", "output": big}]})
    parts = a._parse_task_content(content)
    tr = [p for p in parts if p.type == MessagePartType.TOOL_RESULT][0]
    assert tr.content == big, "task 工具结果 content 不应被 [:5000] 截断"
    assert tr.tool_output == big, "task 工具结果 tool_output 应完整"


def test_trae_task_plan_item_tool_output_full():
    a = TraeAdapter()
    big = "Y" * 20000
    content = json.dumps({"messages": [{"type": "plan_item", "plan_item": {
        "tool_call_info": {"name": "t", "result": {"status": "ok", "data": big}}
    }}]})
    parts = a._parse_task_content(content)
    tr = [p for p in parts if p.type == MessagePartType.TOOL_RESULT]
    assert tr, "plan_item 工具结果应被解析"
    assert big in tr[0].tool_output, "plan_item tool_output 不应被 [:10000] 截断"


def test_trae_parse_fallback_full():
    a = TraeAdapter()
    big = "Z" * 20000  # 非法 JSON：触发 except 兜底分支
    parts = a._parse_task_content(big)
    assert parts and parts[0].content == big, "JSON 损坏兜底分支内容应完整，不再 [:2000]"


def test_qclaw_tool_result_full():
    a = QClawAdapter()
    big = "Q" * 20000
    msg_row = {"role": "assistant", "content": "", "created_at": None,
               "message_id": "m1", "token_count": None}
    part_rows = [{
        "part_type": "tool_result", "tool_output": big, "tool_error": "",
        "text_content": "", "tool_name": "t", "tool_input": "", "file_name": "",
        "ordinal": 0, "metadata": None,
    }]
    msg = a._parse_message(msg_row, part_rows)
    tr = [p for p in msg.parts if p.type == MessagePartType.TOOL_RESULT][0]
    assert tr.content == big, "QClaw 工具结果 content 不应被 [:5000] 截断"
    assert tr.tool_output == big


def test_qoderwork_text_full():
    a = QoderWorkAdapter()
    big = "O" * 12345
    row = {
        "role": "assistant",
        "parts": json.dumps([{"type": "text", "text": big}]),
        "message_id": "m1", "created_at": 0, "metadata": None,
    }
    msg = a._parse_message(row, "model-x")
    assert msg.content == big, "QoderWork 正文应完整"


def test_marvis_text_full():
    a = MarvisAdapter()
    big = "M" * 12345
    row = {
        "role": "assistant",
        "content": json.dumps([{"type": "text", "text": big}]),
        "created_at": 0, "message_id": "m1", "tool_calls": None,
        "model_id": None, "metadata": None,
    }
    msg = a._parse_message(row)
    assert msg.content == big, "Marvis 正文应完整"


def test_workbuddy_text_full():
    a = WorkBuddyAdapter()
    big = "W" * 12345
    rec = {
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": big}],
        "timestamp": 0, "id": "m1",
    }
    msg = a._parse_record(rec)
    assert msg.content == big, "WorkBuddy 正文应完整"


def test_markdown_tool_result_not_truncated():
    exp = MarkdownExporter()
    big = "T" * 5000  # 超过旧 2000 上限
    msg = Message(
        role=Role.TOOL,
        parts=[MessagePart(type=MessagePartType.TOOL_RESULT, tool_output=big, content=big)],
    )
    out = exp._format_message(msg)
    assert big in out, "Markdown 导出不应在 2000 字符处截断工具结果"
    assert "输出已截断" not in out, "导出中不应出现截断提示"


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, total {len(tests)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())

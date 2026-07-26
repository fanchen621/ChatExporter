"""适配器加固回归测试（synthetic fixtures，不碰真机数据）。

对应 v2 深度优化中的适配器缺陷：错误传播、token_usage 形状、
陌生角色名、QoderWork 工具输出、WorkBuddy compat 误删兜底、Marvis 多库。
"""

import json
import os
import sqlite3
import unittest
import tempfile
from pathlib import Path

from chat_exporter.adapters.base import BaseAdapter
from chat_exporter.adapters.marvis import MarvisAdapter
from chat_exporter.adapters.qoderwork import QoderWorkAdapter
from chat_exporter.adapters.workbuddy import WorkBuddyAdapter
from chat_exporter.adapters.workbuddy_compat import WorkBuddyAdapter as WBCompat
from chat_exporter.models import MessagePartType, Role


class TokenUsageNormalizationTests(unittest.TestCase):
    def test_shapes(self):
        norm = BaseAdapter._normalize_token_usage
        self.assertIsNone(norm(None))
        self.assertIsNone(norm(True))
        self.assertEqual(norm(5), {"total_tokens": 5})
        self.assertEqual(norm("300"), {"total_tokens": 300})
        self.assertIsNone(norm("abc"))
        self.assertEqual(norm({"total_tokens": 7, "junk": "x"}), {"total_tokens": 7})
        self.assertIsNone(norm([1, 2]))


class RoleHintTests(unittest.TestCase):
    def test_workbuddy_unknown_role_uses_hint(self):
        ad = WorkBuddyAdapter()
        msg = ad._parse_record({
            "type": "message",
            "role": "assistant_message",
            "id": "m1",
            "timestamp": 0,
            "content": [{"type": "output_text", "text": "回答正文"}],
        })
        self.assertIsNotNone(msg)
        self.assertEqual(msg.role, Role.ASSISTANT)

    def test_qoderwork_unknown_role_uses_hint(self):
        msg = QoderWorkAdapter()._parse_message(_qoder_row(role="agent-output"), None)
        self.assertEqual(msg.role, Role.ASSISTANT)


def _qoder_row(parts=None, role="assistant", metadata=None):
    """messages 表真实列形状：parts 存 JSON，content 列不存在。"""
    return {
        "message_id": "m1",
        "role": role,
        "parts": json.dumps(parts if parts is not None else [{"type": "text", "text": "hi"}]),
        "created_at": None,
        "metadata": json.dumps(metadata) if metadata is not None else None,
    }


class QoderWorkToolPartsTests(unittest.TestCase):
    def _msg(self, parts):
        return QoderWorkAdapter()._parse_message(_qoder_row(parts), None)

    def test_tool_invocation_shape_is_parsed(self):
        msg = self._msg([{
            "type": "tool-invocation",
            "toolInvocation": {"toolName": "read_file", "args": {"path": "a.py"},
                               "result": "文件内容在此"},
        }])
        types = [p.type for p in msg.parts]
        self.assertIn(MessagePartType.TOOL_CALL, types)
        self.assertIn(MessagePartType.TOOL_RESULT, types)
        outputs = [p.tool_output for p in msg.parts if p.type == MessagePartType.TOOL_RESULT]
        self.assertIn("文件内容在此", outputs)

    def test_tool_dash_parts_keep_output(self):
        msg = self._msg([{
            "type": "tool-read_file", "toolCallId": "c1", "toolName": "read_file",
            "input": {"path": "a.py"}, "output": "OUTPUT_TEXT", "result": "OUTPUT_TEXT",
        }])
        outputs = [p.tool_output for p in msg.parts if p.type == MessagePartType.TOOL_RESULT]
        self.assertEqual(outputs, ["OUTPUT_TEXT"])  # output==result 去重成一份

    def test_bad_usage_shape_does_not_crash(self):
        msg = QoderWorkAdapter()._parse_message(
            _qoder_row(metadata={"usage": [1, 2]}), None
        )
        self.assertIsNone(msg.token_usage)


class WorkBuddyCompatFailOpenTests(unittest.TestCase):
    def _rec(self, text, role="user"):
        return {"type": "message", "role": role, "id": "x", "timestamp": 0,
                "content": [{"type": "input_text", "text": text}]}

    def test_pure_injected_record_is_dropped(self):
        msg = WBCompat()._parse_record(self._rec(
            "<system-reminder>\nAlways answer in English.\n</system-reminder>"))
        self.assertIsNone(msg)

    def test_real_text_that_cleans_empty_falls_back_to_original(self):
        # 整条消息只有一个未闭合标签行：不是成对注入块，必须保留原文而不是删掉。
        msg = WBCompat()._parse_record(self._rec("<user_info>"))
        self.assertIsNotNone(msg)
        self.assertIn("user_info", msg.content)

    def test_attachment_only_message_survives(self):
        msg = WBCompat()._parse_record({
            "type": "message", "role": "user", "id": "y", "timestamp": 0,
            "content": [{"type": "input_image", "image_url": "blob://1"}],
        })
        self.assertIsNotNone(msg)
        self.assertTrue(any(p.type == MessagePartType.IMAGE for p in msg.parts))


class MarvisMultiDbTests(unittest.TestCase):
    def _make_db(self, path: Path, titles):
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.executescript(
            "CREATE TABLE conversations (conversation_id TEXT PRIMARY KEY, title TEXT, "
            "created_at INTEGER, updated_at INTEGER, status TEXT);"
            "CREATE TABLE messages (message_id TEXT, conversation_id TEXT, role TEXT, "
            "content TEXT, created_at INTEGER);"
        )
        for index, title in enumerate(titles):
            cid = f"c{index}"
            conn.execute("INSERT INTO conversations VALUES (?,?,?,?,?)", (cid, title, 1, 1, "done"))
            conn.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?)",
                (f"m{index}", cid, "user", f"内容 {title}", 1),
            )
        conn.commit()
        conn.close()

    def test_all_account_dbs_are_listed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self._make_db(base / "User" / "acc_a" / "database" / "data.db", ["A1", "A2"])
            self._make_db(base / "User" / "acc_b" / "database" / "data.db", ["B1"])

            ad = MarvisAdapter()
            ad.base_dir = str(base / "User")
            ad._db_paths = []
            ad._db_path = None
            self.assertTrue(ad.detect())
            convs = ad.list_conversations()
            titles = sorted(c.title for c in convs)
            self.assertEqual(titles, ["A1", "A2", "B1"])
            # 跨库 id 必须携带账号命名空间，且能按命名空间取回
            namespaced = [c for c in convs if MarvisAdapter.ID_SEPARATOR in str(c.id)]
            self.assertEqual(len(namespaced), 3)
            full = ad.get_conversation(convs[0].id)
            self.assertIsNotNone(full)
            self.assertTrue(full.messages)


if __name__ == "__main__":
    unittest.main()

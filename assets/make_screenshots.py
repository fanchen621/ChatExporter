"""用合成演示数据生成 README 截图。

绝不使用本机真实对话：截图会进公开仓库，真实标题和正文属于隐私。
运行：python assets/make_screenshots.py（需要 pillow）
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chat_exporter.adapters.base import BaseAdapter
from chat_exporter.models import AppInfo, Conversation, Message, MessagePart, MessagePartType, Role

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "images")

NOW = datetime(2026, 3, 14, 15, 9, 26)


def _demo_conversations():
    def conv(index, title, days_ago, messages):
        return Conversation(
            id=f"demo-{index}",
            title=title,
            created_at=NOW - timedelta(days=days_ago, hours=2),
            updated_at=NOW - timedelta(days=days_ago),
            source_app="Demo Assistant",
            messages=messages,
            metadata={"msg_count": len(messages)},
        )

    answer = (
        "## 根因\n\n"
        "问题出在缓存键上：它只用了 `user_id`，没有把 `locale` 算进去，"
        "所以切换语言后仍然命中上一份缓存。\n\n"
        "```python\n"
        "def cache_key(user_id: str, locale: str) -> str:\n"
        "    return f\"profile:{user_id}:{locale}\"\n"
        "```\n\n"
        "### 修复步骤\n\n"
        "1. 把 `locale` 并入缓存键（上面的实现）\n"
        "2. 上线后**按前缀批量清一次旧键**，否则老数据还会再撑一个 TTL\n"
        "3. 在集成测试里补一个双语言切换的用例\n\n"
        "> 注意：清键放在低峰期做，瞬时回源压力可以降一个数量级。\n\n"
        "| 方案 | 改动量 | 风险 |\n"
        "|---|---|---|\n"
        "| 键里并入 locale | 一行 | 低 |\n"
        "| 切语言时主动失效 | 中 | 中 |\n\n"
        "---\n\n"
        "细节可以参考 [缓存设计准则](https://example.com/cache-rules)。"
    )

    return [
        conv(1, "修复多语言缓存串味的问题", 0, [
            Message(role=Role.USER, content="切换语言之后个人资料页还是显示旧的翻译，帮我看看",
                    timestamp=NOW - timedelta(hours=3)),
            Message(role=Role.ASSISTANT, timestamp=NOW - timedelta(hours=3), model="gpt-5", parts=[
                MessagePart(type=MessagePartType.THINKING, content="先确认缓存层的键是怎么拼的。"),
                MessagePart(type=MessagePartType.TOOL_CALL, tool_name="read_file",
                            tool_input='{"path": "cache.py"}'),
                MessagePart(type=MessagePartType.TOOL_RESULT, tool_output="def cache_key(user_id): ..."),
                MessagePart(type=MessagePartType.TEXT, content=answer),
            ]),
            Message(role=Role.USER, content="清键要不要停服务？", timestamp=NOW - timedelta(hours=2)),
            Message(role=Role.ASSISTANT, timestamp=NOW - timedelta(hours=2), model="gpt-5", parts=[
                MessagePart(type=MessagePartType.TEXT, content=(
                    "不用。旧键不会再被写入，直接按前缀 `profile:*` 批量删除即可，"
                    "读到空就会回源重建。建议在低峰期做，避免瞬时回源压力。"
                )),
            ]),
        ]),
        conv(2, "给部署脚本补上回滚步骤", 1, [
            Message(role=Role.USER, content="发布脚本没有回滚，帮我补一个", timestamp=NOW - timedelta(days=1)),
            Message(role=Role.ASSISTANT, timestamp=NOW - timedelta(days=1), parts=[
                MessagePart(type=MessagePartType.TEXT, content="先记录上一个版本号，失败时切回去即可。"),
            ]),
        ]),
        conv(3, "梳理季度技术债清单", 3, [
            Message(role=Role.USER, content="把这个季度的技术债列一下优先级", timestamp=NOW - timedelta(days=3)),
            Message(role=Role.ASSISTANT, timestamp=NOW - timedelta(days=3), parts=[
                MessagePart(type=MessagePartType.TEXT, content="按影响面排序，前三项是缓存、日志和构建时长。"),
            ]),
        ]),
        conv(4, "排查构建缓慢的原因", 8, [
            Message(role=Role.USER, content="CI 构建从 3 分钟涨到 11 分钟", timestamp=NOW - timedelta(days=8)),
            Message(role=Role.ASSISTANT, timestamp=NOW - timedelta(days=8), parts=[
                MessagePart(type=MessagePartType.TEXT, content="依赖缓存失效了，锁文件每次都在变。"),
            ]),
        ]),
        conv(5, "整理接口文档结构", 15, [
            Message(role=Role.USER, content="接口文档太乱了，重新组织一下", timestamp=NOW - timedelta(days=15)),
            Message(role=Role.ASSISTANT, timestamp=NOW - timedelta(days=15), parts=[
                MessagePart(type=MessagePartType.TEXT, content="按资源分组，把鉴权单独抽一节。"),
            ]),
        ]),
        conv(6, "为搜索加上拼音匹配", 22, [
            Message(role=Role.USER, content="中文搜索想支持拼音首字母", timestamp=NOW - timedelta(days=22)),
            Message(role=Role.ASSISTANT, timestamp=NOW - timedelta(days=22), parts=[
                MessagePart(type=MessagePartType.TEXT, content="建索引时同时存拼音串，查询时并行匹配。"),
            ]),
        ]),
    ]


class DemoAdapter(BaseAdapter):
    """只为截图存在的假来源，不读取本机任何数据。"""

    name = "trae"  # 复用侧栏配色
    display_name = "Demo Assistant"

    def __init__(self):
        super().__init__()
        self._conversations = _demo_conversations()

    def detect(self) -> bool:
        return True

    def get_app_info(self) -> AppInfo:
        return AppInfo(
            name=self.name, display_name=self.display_name, is_available=True,
            data_path="(demo)", conversation_count=len(self._conversations),
        )

    def list_conversations(self):
        return list(self._conversations)

    def get_conversation(self, conv_id):
        return next((c for c in self._conversations if c.id == conv_id), None)


def _grab(app, name):
    from PIL import ImageGrab

    for _ in range(4):
        app.root.attributes("-topmost", True)
        app.root.lift()
        app.root.focus_force()
        for _ in range(20):
            app.root.update_idletasks()
            app.root.update()
        time.sleep(0.4)
        app.root.update()
        x, y = app.root.winfo_rootx(), app.root.winfo_rooty()
        w, h = app.root.winfo_width(), app.root.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        if img.convert("L").getextrema()[1] > 30:
            os.makedirs(OUT_DIR, exist_ok=True)
            path = os.path.join(OUT_DIR, f"{name}.png")
            img.save(path)
            print("saved", path)
            return
    print("failed to capture", name)


def _pump(app, times=40):
    for _ in range(times):
        app.root.update_idletasks()
        app.root.update()


def _wait(app, predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.root.update_idletasks()
        app.root.update()
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def main():
    from chat_exporter.gui_cn_v3 import ChatExporterGUI

    # 合成截图不需要双缓冲；开着会让抓屏拿到黑帧。
    ChatExporterGUI._enable_double_buffering = lambda self: None

    # 截图必须可复现：忽略本机保存的窗口/分栏偏好，也不要把截图跑出来的状态写回去。
    class _NoSettings:
        def get(self, _key, default=None):
            return default

        def set(self, _key, _value, autosave=True):
            return None

        def save(self):
            return None

    import chat_exporter.gui_cn_v3 as v3

    v3._load_settings = _NoSettings
    app = ChatExporterGUI()
    demo = DemoAdapter()
    app.adapters = [demo]
    app.root.geometry("1760x980+40+30")
    _pump(app, 60)
    app._detect_apps()
    _wait(app, lambda: app._nav_rows)
    app._select_app(demo)
    _wait(app, lambda: [r for r in app.conv_tree.get_children() if not r.startswith("__")])

    # 分栏摆到能同时看清标题、时间、消息数的位置
    app.workspace_panes.sashpos(0, 640)
    _pump(app, 20)

    rows = [r for r in app.conv_tree.get_children() if not r.startswith("__")]
    app.conv_tree.selection_set(rows[0])
    app.conv_tree.event_generate("<<TreeviewSelect>>")
    _wait(app, lambda: len(app.preview_text.get("1.0", "end")) > 200)
    _pump(app, 40)
    _grab(app, "screenshot-light")

    app._toggle_theme()
    _pump(app, 40)
    _grab(app, "screenshot-dark")
    app._toggle_theme()
    _pump(app, 40)

    app.clean_preview_var.set(True)
    app._on_clean_preview_toggled()
    _pump(app, 60)
    _grab(app, "screenshot-clean")

    app.root.destroy()


if __name__ == "__main__":
    main()

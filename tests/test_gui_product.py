from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import tkinter as tk

import main
import pytest

from chat_exporter import gui_product
from chat_exporter.exporters import FORMAT_CHOICES
from chat_exporter.models import Conversation


def test_main_entry_uses_product_shell():
    assert main.run is gui_product.run


def test_product_shell_bounds_preview_pages():
    assert 20 <= gui_product.ChatExporterGUI.PREVIEW_PAGE_SIZE <= 500
    assert gui_product.ChatExporterGUI.SIDEBAR_WIDTH <= 240
    assert gui_product.ChatExporterGUI.MIN_LIBRARY_WIDTH >= 380
    assert gui_product.ChatExporterGUI.MIN_PREVIEW_WIDTH >= 480
    assert "_request_preview_page" in gui_product.ChatExporterGUI.__dict__
    assert "_start_content_search" in gui_product.ChatExporterGUI.__dict__
    assert "_export_all" in gui_product.ChatExporterGUI.__dict__
    assert "_fit_tree_columns" in gui_product.ChatExporterGUI.__dict__


def test_product_shell_preserves_core_capabilities():
    cls = gui_product.ChatExporterGUI
    for method in (
        "_start_content_search",
        "_request_preview_page",
        "_goto_preview_hit",
        "_copy_preview_text",
        "_export_selected",
        "_export_all",
        "_toggle_theme",
        "_open_key_assistant",
        "_reload_current_source",
    ):
        assert callable(getattr(cls, method, None)), method
    assert [format_id for format_id, _label in FORMAT_CHOICES] == [
        "markdown",
        "html",
        "json",
        "txt",
    ]


def test_message_count_progress_updates_visible_row():
    class Tree:
        def __init__(self):
            self.values = {"conv_0": ["title", "date", "…"]}

        def exists(self, item_id):
            return item_id in self.values

        def item(self, item_id, option=None, **kwargs):
            if "values" in kwargs:
                self.values[item_id] = list(kwargs["values"])
                return None
            if option == "values":
                return tuple(self.values[item_id])
            return {"values": tuple(self.values[item_id])}

    class Var:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    adapter = object()
    conv = Conversation(id="c1", title="demo", metadata={"msg_count_known": False})
    gui = object.__new__(gui_product.ChatExporterGUI)
    gui.current_adapter = adapter
    gui._load_generation = 4
    gui._message_count_generation = 7
    gui.current_conversations = [conv]
    gui._tree_conv_map = {"conv_0": conv}
    gui.conv_tree = Tree()
    gui.library_footer_var = Var()

    gui._apply_message_count(adapter, 4, 7, "c1", 15024, 1, 1)

    assert conv.metadata["msg_count"] == 15024
    assert conv.metadata["msg_count_known"] is True
    assert gui.conv_tree.values["conv_0"][2] == "15024"
    assert gui.library_footer_var.value == "正在统计消息数 1/1"


def test_product_format_labels_are_compact_without_changing_formats():
    class Var:
        def get(self):
            return "纯文本"

    gui = object.__new__(gui_product.ChatExporterGUI)
    gui.export_format_var = Var()

    assert gui._selected_format_id() == "txt"
    assert [format_id for format_id, _label in gui.FORMAT_UI_CHOICES] == [
        "markdown",
        "html",
        "json",
        "txt",
    ]


def test_product_uses_compact_dates_in_the_library():
    current_year = datetime.now().year
    recent = Conversation(id="recent", updated_at=datetime(current_year, 7, 28, 12, 30))
    older = Conversation(id="older", updated_at=datetime(current_year - 1, 12, 31, 23, 59))

    assert gui_product.ChatExporterGUI._compact_updated_at(recent) == "07-28"
    assert gui_product.ChatExporterGUI._compact_updated_at(older) == f"{(current_year - 1) % 100:02d}-12-31"


def test_selection_helpers_prefer_the_focused_visible_row():
    class Tree:
        def selection(self):
            return ("__loading__", "conv_0", "conv_1")

        def focus(self):
            return "conv_1"

    gui = object.__new__(gui_product.ChatExporterGUI)
    gui.conv_tree = Tree()
    gui._tree_conv_map = {"conv_0": object(), "conv_1": object()}

    assert gui._real_tree_selection() == ["conv_0", "conv_1"]
    assert gui._active_tree_item() == "conv_1"


def test_double_click_focuses_preview_without_exporting():
    class Tree:
        def __init__(self):
            self.selected = ""
            self.focused = ""

        def identify_row(self, _y):
            return "conv_0"

        def selection_set(self, item):
            self.selected = item

        def focus(self, item=None):
            if item is not None:
                self.focused = item
            return self.focused

    gui = object.__new__(gui_product.ChatExporterGUI)
    gui.conv_tree = Tree()
    gui._tree_conv_map = {"conv_0": object()}
    calls = {"selection": 0, "focus": 0, "export": 0}
    gui._on_tree_selection_changed = lambda *_: calls.__setitem__("selection", calls["selection"] + 1)
    gui._focus_selected_preview = lambda *_: calls.__setitem__("focus", calls["focus"] + 1) or "break"
    gui._export_selected = lambda *_: calls.__setitem__("export", calls["export"] + 1)

    assert gui._on_tree_double_click(SimpleNamespace(y=12)) == "break"
    assert gui.conv_tree.selected == "conv_0"
    assert gui.conv_tree.focused == "conv_0"
    assert calls == {"selection": 1, "focus": 1, "export": 0}


def test_fit_tree_columns_keeps_compact_dates_readable():
    try:
        app = gui_product.ChatExporterGUI()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    try:
        app.root.geometry("1100x700+0+0")
        app.root.update()
        app.workspace_panes.sashpos(0, 280)
        app.root.update_idletasks()
        app._fit_tree_columns()
        date_w = int(app.conv_tree.column("date", "width"))
        msg_w = int(app.conv_tree.column("messages", "width"))
        needed = app._tree_cell_font.measure("07-18")
        assert date_w >= needed
        assert msg_w >= app._tree_cell_font.measure("192")
        assert str(app.conv_tree.heading("date", "anchor")) == "center"
        assert str(app.conv_tree.column("messages", "anchor")) == "e"
    finally:
        if app._tasks:
            app._tasks.shutdown(wait=False)
        app.root.destroy()


def test_product_shell_constructs_real_tk_widgets():
    try:
        app = gui_product.ChatExporterGUI()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    try:
        app.root.geometry("1080x680+0+0")
        app.root.deiconify()
        app.root.update()
        app._enforce_pane_limits()
        app.root.update_idletasks()
        app.root.update()
        try:
            app.workspace_panes.sashpos(0, gui_product.ChatExporterGUI.MIN_LIBRARY_WIDTH)
        except tk.TclError:
            pass
        app.root.update_idletasks()
        app._fit_tree_columns()
        app.root.update_idletasks()
        root_left = app.root.winfo_rootx()
        root_width = app.root.winfo_width()
        for widget in (
            app.refresh_button,
            app.theme_button,
            app.batch_button,
            app.export_button,
        ):
            left = widget.winfo_rootx() - root_left
            right = left + widget.winfo_width()
            assert widget.winfo_ismapped()
            assert 0 <= left < right <= root_width
        assert int(app.preview_text.cget("width")) == 1
        assert int(app.preview_text.cget("height")) == 1
        assert int(app.search_entry.cget("width")) == 1
        assert int(app.preview_find_entry.cget("width")) == 1
        assert app.preview_find_entry.winfo_ismapped()
        assert all(
            widget.winfo_ismapped()
            for widget in (
                app.preview_first_button,
                app.preview_older_button,
                app.preview_newer_button,
                app.preview_latest_button,
            )
        )
        tree_width = max(1, app.conv_tree.winfo_width())
        columns_width = sum(
            int(app.conv_tree.column(name, "width"))
            for name in ("title", "date", "messages")
        )
        assert columns_width <= tree_width + 8
        assert app.style.configure("Pager.TButton")
        assert app.style.configure("Compact.TCombobox")
        assert app._tree_context_menu is not None

        first = Conversation(id="first", title="第一条")
        second = Conversation(id="second", title="第二条")
        app.current_conversations = [first, second]
        app._tree_conv_map = {"conv_0": first, "conv_1": second}
        for item in app.conv_tree.get_children():
            app.conv_tree.delete(item)
        app.conv_tree.insert("", tk.END, iid="conv_0", values=("第一条", "07-28", "1"))
        app.conv_tree.insert("", tk.END, iid="conv_1", values=("第二条", "07-28", "1"))

        app.conv_tree.selection_set("conv_0")
        app.conv_tree.focus("conv_0")
        app._sync_action_states()
        assert app.batch_button.cget("text") == "导出全部 2 条"
        assert app.selection_summary_var.get() == "双击阅读 · Ctrl+E 导出"

        app.conv_tree.selection_set("conv_0", "conv_1")
        app.conv_tree.focus("conv_1")
        app._sync_action_states()
        app.root.update_idletasks()
        assert app.batch_button.cget("text") == "导出选中 2 条"
        assert app.selection_summary_var.get() == "已选 2 条 · Esc 取消"
        assert app.selection_bar.winfo_ismapped()
        selection_right = max(
            child.winfo_x() + child.winfo_width()
            for child in app.selection_bar.winfo_children()
            if child.winfo_ismapped()
        )
        assert selection_right <= app.selection_bar.winfo_width()

        app.search_entry.focus_set()
        app.root.update()
        assert str(app.search_field_frame.cget("highlightbackground")) == gui_product.Palette.ACCENT
        app.preview_find_entry.focus_set()
        app.root.update()
        assert str(app.preview_find_field_frame.cget("highlightbackground")) == gui_product.Palette.ACCENT

        app._show_toast("测试提示", "不应抢走焦点", tone="success")
        app.root.update_idletasks()
        assert app._toast_frame is not None and app._toast_frame.winfo_ismapped()
        assert app.root.focus_get() is app.preview_find_entry

        invoked = []
        app._show_preview_state("读取失败", "可安全重试", "重新加载", lambda: invoked.append(True))
        app.root.update_idletasks()
        assert app.preview_state_layer.winfo_ismapped()
        assert app.preview_state_button.winfo_ismapped()
        app.preview_state_button.invoke()
        assert invoked == [True]

        app.conv_tree.selection_remove("conv_0", "conv_1")
        app.preview_title_var.set("旧标题")
        app.preview_meta_var.set("旧说明")
        app._show_preview_placeholder()
        assert app.preview_title_var.get() == "选择一条对话"
        assert "导出始终" in app.preview_meta_var.get()
        assert app.preview_page_var.get() == "尚未打开对话"
        app._fit_tree_columns()
        date_width = int(app.conv_tree.column("date", "width"))
        assert date_width >= app._tree_cell_font.measure("07-28")
        assert str(app.conv_tree.column("date", "anchor")) in {"center", "e", "w"}
        assert str(app.conv_tree.heading("date", "anchor")) in {"center", "e", "w"}
    finally:
        if app._tasks:
            app._tasks.shutdown(wait=False)
        app.root.destroy()

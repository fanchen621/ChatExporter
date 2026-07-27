from __future__ import annotations

import main

from chat_exporter import gui_product
from chat_exporter.exporters import FORMAT_CHOICES
from chat_exporter.models import Conversation


def test_main_entry_uses_product_shell():
    assert main.run is gui_product.run


def test_product_shell_bounds_preview_pages():
    assert 20 <= gui_product.ChatExporterGUI.PREVIEW_PAGE_SIZE <= 500
    assert "_request_preview_page" in gui_product.ChatExporterGUI.__dict__
    assert "_start_content_search" in gui_product.ChatExporterGUI.__dict__
    assert "_export_all" in gui_product.ChatExporterGUI.__dict__


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

from __future__ import annotations

from chat_exporter.exporters import format_label
from chat_exporter.gui_cn_v3 import ChatExporterGUI


class _Var:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value


def _gui_with_format(format_id: str) -> ChatExporterGUI:
    gui = object.__new__(ChatExporterGUI)
    gui.export_format_var = _Var(format_label(format_id))
    return gui


def test_export_action_label_tracks_selected_format():
    assert _gui_with_format("markdown")._selected_format_short_label() == "Markdown"
    assert _gui_with_format("html")._selected_format_short_label() == "HTML"
    assert _gui_with_format("json")._selected_format_short_label() == "JSON"
    assert _gui_with_format("txt")._selected_format_short_label() == "纯文本"


def test_human_file_size_is_readable():
    assert ChatExporterGUI._human_file_size(0) == "0 B"
    assert ChatExporterGUI._human_file_size(1024) == "1.0 KB"
    assert ChatExporterGUI._human_file_size(23 * 1024 * 1024) == "23.0 MB"

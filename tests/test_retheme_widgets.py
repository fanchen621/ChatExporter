"""换肤时对真实 tk 控件的重新染色。

这些用例需要一个真实的 Tk 环境（Windows 上一定有；无显示的 CI 会自动跳过）。
纯数据层的断言在 test_theme_invariants.py 里。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import pytest

from chat_exporter.ui_theme import (
    DARK_THEME,
    LIGHT_THEME,
    Palette,
    apply_theme,
    retheme_combobox_popdowns,
    retheme_widgets,
    theme_color_map,
)


@pytest.fixture(scope="module")
def tk_root():
    """整个模块共用一个 Tk root。

    反复 Tk() / destroy() 会偶发 "tk wasn't installed properly" —— 解释器里
    重复初始化 Tcl 解释器本来就不稳。共用一个 root，每个用例在自己的容器里干活。
    """
    try:
        window = tk.Tk()
    except tk.TclError as exc:  # 无显示环境
        pytest.skip(f"没有可用的 Tk 显示: {exc}")
    window.withdraw()
    try:
        yield window
    finally:
        try:
            window.destroy()
        except tk.TclError:
            pass


@pytest.fixture
def root(tk_root):
    """每个用例拿一个干净的容器，免得上一个用例残留的控件跟着一起换肤。"""
    apply_theme("light")
    container = tk.Frame(tk_root)
    try:
        yield container
    finally:
        try:
            container.destroy()
        except tk.TclError:
            pass
        apply_theme("light")


def _switch(root_widget, target: str):
    source = "light" if target == "dark" else "dark"
    mapping = theme_color_map(source, target)
    apply_theme(target)
    retheme_widgets(root_widget, mapping)
    return mapping


def test_bg_alias_does_not_double_map(root):
    """-bg 是 -background 的别名，边读边写会把新色当旧色再映射一次。

    浅色 SURFACE #FFFFFF 应当变成深色 SURFACE #262624。但 #262624 又正好是
    浅色 SIDEBAR 的值，所以第二遍会把它再映射成 #141312——每张白卡片都染成侧栏色。
    这是本用例守的东西。
    """
    card = tk.Frame(root, bg=Palette.SURFACE)
    _switch(root, "dark")
    assert card.cget("bg").upper() == DARK_THEME["SURFACE"], (
        f"卡片底色落到了 {card.cget('bg')}，而不是深色 SURFACE {DARK_THEME['SURFACE']}"
    )


def test_fg_alias_does_not_double_map(root):
    label = tk.Label(root, bg=Palette.SURFACE, fg=Palette.TEXT)
    _switch(root, "dark")
    assert label.cget("fg").upper() == DARK_THEME["TEXT"]
    assert label.cget("bg").upper() == DARK_THEME["SURFACE"]


def test_sidebar_widgets_land_on_dark_sidebar(root):
    bar = tk.Frame(root, bg=Palette.SIDEBAR)
    text = tk.Label(root, bg=Palette.SIDEBAR, fg=Palette.TEXT_ON_DARK_MUTED)
    _switch(root, "dark")
    assert bar.cget("bg").upper() == DARK_THEME["SIDEBAR"]
    assert text.cget("bg").upper() == DARK_THEME["SIDEBAR"]


def test_checkbutton_indicator_follows_theme(root):
    """深色模式下勾选框中间不能留一块白方块。"""
    chk = tk.Checkbutton(
        root,
        text="只看对话",
        bg=Palette.SURFACE,
        fg=Palette.TEXT_SECONDARY,
        selectcolor=Palette.SURFACE,
    )
    assert chk.cget("selectcolor").upper() == LIGHT_THEME["SURFACE"]
    _switch(root, "dark")
    assert chk.cget("selectcolor").upper() == DARK_THEME["SURFACE"]


def test_round_trip_returns_widgets_to_light(root):
    card = tk.Frame(root, bg=Palette.SURFACE)
    label = tk.Label(root, bg=Palette.SURFACE_ALT, fg=Palette.TEXT_MUTED)
    dot = tk.Frame(root, bg=Palette.SUCCESS)

    _switch(root, "dark")
    _switch(root, "light")

    assert card.cget("bg").upper() == LIGHT_THEME["SURFACE"]
    assert label.cget("bg").upper() == LIGHT_THEME["SURFACE_ALT"]
    assert label.cget("fg").upper() == LIGHT_THEME["TEXT_MUTED"]
    assert dot.cget("bg").upper() == LIGHT_THEME["SUCCESS"]


def test_dual_role_foreground_is_retheme_d(root):
    """fg=Palette.SUCCESS 的"可用"标签换肤后不能停在浅色主题的绿。"""
    tag = tk.Label(root, bg=Palette.SIDEBAR, fg=Palette.SUCCESS)
    _switch(root, "dark")
    assert tag.cget("fg").upper() == DARK_THEME["SUCCESS"]


def test_combobox_popdown_is_rethemed(root):
    """下拉列表是 Tcl 建的，不在 winfo_children() 里，得单独刷。"""
    box = ttk.Combobox(root, values=["a", "b"], state="readonly")
    box.pack()
    root.update_idletasks()

    _switch(root, "dark")
    retheme_combobox_popdowns(root)

    try:
        popdown = box.tk.call("ttk::combobox::PopdownWindow", box)
        actual = str(box.tk.call(f"{popdown}.f.l", "cget", "-background"))
    except tk.TclError as exc:
        pytest.skip(f"这个 Tk 版本拿不到下拉窗口: {exc}")
    assert actual.upper() == DARK_THEME["SURFACE"]


def test_retheme_is_noop_for_empty_mapping(root):
    card = tk.Frame(root, bg=Palette.SURFACE)
    before = card.cget("bg")
    retheme_widgets(root, {"fg": {}, "bg": {}})
    assert card.cget("bg") == before


def test_unmapped_colors_are_left_alone(root):
    """不属于主题的颜色（例如来源身份色）不能被顺手改掉。"""
    from chat_exporter.gui_modern import ChatExporterGUI

    accent = ChatExporterGUI.APP_ACCENTS["trae"]
    bar = tk.Frame(root, bg=accent)
    _switch(root, "dark")
    assert bar.cget("bg").upper() == accent.upper()

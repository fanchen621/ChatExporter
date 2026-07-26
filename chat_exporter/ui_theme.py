"""Modern visual system for ChatExporter.

The module intentionally uses only Tk/Ttk so the single-file Windows build keeps
its zero-extra-runtime-dependency promise.
"""
from __future__ import annotations

import ctypes
import os
import tkinter as tk
from tkinter import ttk


class Palette:
    WINDOW = "#F4F6FA"
    SIDEBAR = "#0F172A"
    SIDEBAR_RAISED = "#172033"
    SIDEBAR_HOVER = "#1E293B"
    SURFACE = "#FFFFFF"
    SURFACE_ALT = "#F8FAFC"
    BORDER = "#E4E7EC"
    BORDER_STRONG = "#D0D5DD"
    TEXT = "#101828"
    TEXT_SECONDARY = "#475467"
    TEXT_MUTED = "#667085"
    TEXT_DISABLED = "#98A2B3"
    TEXT_ON_DARK = "#F8FAFC"
    TEXT_ON_DARK_MUTED = "#94A3B8"
    ACCENT = "#635BFF"
    ACCENT_HOVER = "#5147E5"
    ACCENT_PRESSED = "#4338CA"
    ACCENT_SOFT = "#EEF0FF"
    ACCENT_SOFT_HOVER = "#E4E7FF"
    SUCCESS = "#12B76A"
    SUCCESS_SOFT = "#ECFDF3"
    WARNING = "#F79009"
    WARNING_SOFT = "#FFFAEB"
    DANGER = "#F04438"
    DANGER_SOFT = "#FEF3F2"
    INFO = "#2E90FA"
    INFO_SOFT = "#EFF8FF"
    CODE_BG = "#F2F4F7"
    SELECTION = "#EEF0FF"


LIGHT_THEME = {name: value for name, value in vars(Palette).items() if not name.startswith("_")}

DARK_THEME = {
    "WINDOW": "#0B1120",
    "SIDEBAR": "#070B14",
    "SIDEBAR_RAISED": "#151E31",
    "SIDEBAR_HOVER": "#1D283D",
    "SURFACE": "#111A2B",
    "SURFACE_ALT": "#18233A",
    "BORDER": "#26334D",
    "BORDER_STRONG": "#35435F",
    "TEXT": "#E9EEF8",
    "TEXT_SECONDARY": "#C2CCDE",
    "TEXT_MUTED": "#94A3B8",
    "TEXT_DISABLED": "#64748B",
    "TEXT_ON_DARK": "#F8FAFC",
    "TEXT_ON_DARK_MUTED": "#94A3B8",
    "ACCENT": "#8B85FF",
    "ACCENT_HOVER": "#9D98FF",
    "ACCENT_PRESSED": "#B4B0FF",
    "ACCENT_SOFT": "#232048",
    "ACCENT_SOFT_HOVER": "#2C2857",
    "SUCCESS": "#3DD68C",
    "SUCCESS_SOFT": "#11291F",
    "WARNING": "#FDB022",
    "WARNING_SOFT": "#2A1F0B",
    "DANGER": "#FF7A70",
    "DANGER_SOFT": "#2E1512",
    "INFO": "#63A9FF",
    "INFO_SOFT": "#0F2138",
    "CODE_BG": "#18233A",
    "SELECTION": "#232048",
}

THEMES = {"light": LIGHT_THEME, "dark": DARK_THEME}


def apply_theme(name: str) -> str:
    """把主题色写回 Palette。

    全部界面代码都在构建控件时直接读 Palette.XXX，所以换肤 = 改这些类属性，
    再重建样式并把已存在控件的旧色值映射成新色值（见 retheme_widgets）。
    """
    theme = THEMES.get(name) or LIGHT_THEME
    for key, value in theme.items():
        setattr(Palette, key, value)
    return name if name in THEMES else "light"


_FG_KEYS = ("TEXT", "TEXT_SECONDARY", "TEXT_MUTED", "TEXT_DISABLED", "TEXT_ON_DARK", "TEXT_ON_DARK_MUTED")


def theme_color_map(source: str, target: str) -> dict:
    """旧主题色 -> 新主题色查找表，前景/背景分开。

    同一个色值可能同时是浅色主题的某个背景和某个前景（例：#F8FAFC 既是
    SURFACE_ALT 又是 TEXT_ON_DARK），一张表按值映射必然选错其中一边——
    v2.0 初版就把深色模式下的侧栏文字染成了背景色。
    """
    old = THEMES.get(source) or LIGHT_THEME
    new = THEMES.get(target) or LIGHT_THEME
    fg_map: dict = {}
    bg_map: dict = {}
    for key, value in old.items():
        replacement = new.get(key)
        if not replacement or value == replacement:
            continue
        bucket = fg_map if key in _FG_KEYS else bg_map
        bucket[value.casefold()] = replacement
    return {"fg": fg_map, "bg": bg_map}


_FG_OPTIONS = ("foreground", "fg", "activeforeground", "disabledforeground", "insertbackground", "selectforeground")
_BG_OPTIONS = (
    "background", "bg", "activebackground", "highlightbackground", "highlightcolor",
    "selectbackground", "troughcolor",
)


def retheme_widgets(widget: tk.Misc, mapping: dict) -> None:
    """递归把控件上出现的旧主题色替换成新主题色。

    tk 经典控件的颜色是构建时写死的，ttk 样式重配不会影响它们；
    没有这一步，换肤后侧栏和预览区会停在上一个主题。
    前景选项只查前景表，背景选项优先背景表，互不串染。
    """
    fg_map = mapping.get("fg", {})
    bg_map = mapping.get("bg", {})
    if not fg_map and not bg_map:
        return
    for option in _FG_OPTIONS + _BG_OPTIONS:
        try:
            current = str(widget.cget(option)).casefold()
        except Exception:
            continue
        replacement = fg_map.get(current) if option in _FG_OPTIONS else bg_map.get(current)
        if replacement:
            try:
                widget.configure(**{option: replacement})
            except Exception:
                pass
    for child in widget.winfo_children():
        retheme_widgets(child, mapping)


class Metrics:
    SIDEBAR_WIDTH = 238
    HEADER_HEIGHT = 76
    STATUS_HEIGHT = 36
    PAD_X = 22
    PAD_Y = 18
    CARD_PAD = 18


FONT_UI = "Microsoft YaHei UI"
FONT_LATIN = "Segoe UI"
FONT_MONO = "Cascadia Mono"


def resource_path(*relative: str) -> str:
    """资源定位：源码运行取仓库根目录，PyInstaller onefile 取 _MEIPASS 解包目录。"""
    import sys

    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *relative)


def apply_app_icon(root: tk.Tk) -> None:
    """给主窗口装应用图标；资产缺失时静默跳过（图标绝不值得让程序起不来）。"""
    try:
        ico = resource_path("assets", "app.ico")
        if os.path.exists(ico):
            root.iconbitmap(default=ico)
            return
        png = resource_path("assets", "window.png")
        if os.path.exists(png):
            root.iconphoto(True, tk.PhotoImage(file=png))
    except Exception:
        pass


def enable_windows_dpi_awareness() -> None:
    """Best-effort high-DPI support before the first Tk window is painted."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def configure_styles(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    root.configure(bg=Palette.WINDOW)
    root.option_add("*Font", (FONT_UI, 10))
    root.option_add("*TCombobox*Listbox.font", (FONT_UI, 10))

    style.configure("App.TFrame", background=Palette.WINDOW)
    style.configure("Surface.TFrame", background=Palette.SURFACE)
    style.configure("Sidebar.TFrame", background=Palette.SIDEBAR)
    style.configure("SidebarRaised.TFrame", background=Palette.SIDEBAR_RAISED)
    style.configure("Toolbar.TFrame", background=Palette.SURFACE)
    style.configure("Card.TFrame", background=Palette.SURFACE, relief="flat")
    style.configure("CardAlt.TFrame", background=Palette.SURFACE_ALT, relief="flat")

    style.configure(
        "Brand.TLabel",
        background=Palette.SIDEBAR,
        foreground=Palette.TEXT_ON_DARK,
        font=(FONT_LATIN, 15, "bold"),
    )
    style.configure(
        "BrandSub.TLabel",
        background=Palette.SIDEBAR,
        foreground=Palette.TEXT_ON_DARK_MUTED,
        font=(FONT_UI, 9),
    )
    style.configure(
        "SidebarSection.TLabel",
        background=Palette.SIDEBAR,
        foreground=Palette.TEXT_ON_DARK_MUTED,
        font=(FONT_LATIN, 8, "bold"),
    )
    style.configure(
        "PageTitle.TLabel",
        background=Palette.SURFACE,
        foreground=Palette.TEXT,
        font=(FONT_UI, 17, "bold"),
    )
    style.configure(
        "PageSub.TLabel",
        background=Palette.SURFACE,
        foreground=Palette.TEXT_MUTED,
        font=(FONT_UI, 9),
    )
    style.configure(
        "CardTitle.TLabel",
        background=Palette.SURFACE,
        foreground=Palette.TEXT,
        font=(FONT_UI, 12, "bold"),
    )
    style.configure(
        "Body.TLabel",
        background=Palette.SURFACE,
        foreground=Palette.TEXT_SECONDARY,
        font=(FONT_UI, 10),
    )
    style.configure(
        "Muted.TLabel",
        background=Palette.SURFACE,
        foreground=Palette.TEXT_MUTED,
        font=(FONT_UI, 9),
    )
    style.configure(
        "StatusBar.TLabel",
        background=Palette.SURFACE,
        foreground=Palette.TEXT_MUTED,
        font=(FONT_UI, 9),
    )

    common_button = dict(
        font=(FONT_UI, 9),
        padding=(13, 8),
        relief="flat",
        focusthickness=0,
        focuscolor="none",
    )
    style.configure(
        "Primary.TButton",
        **common_button,
        borderwidth=0,
        background=Palette.ACCENT,
        foreground="#FFFFFF",
    )
    style.map(
        "Primary.TButton",
        background=[("pressed", Palette.ACCENT_PRESSED), ("active", Palette.ACCENT_HOVER), ("disabled", "#C7C5FF")],
        foreground=[("disabled", "#F7F7FF")],
    )
    style.configure(
        "Secondary.TButton",
        **common_button,
        borderwidth=1,
        background=Palette.SURFACE_ALT,
        foreground=Palette.TEXT_SECONDARY,
    )
    style.map(
        "Secondary.TButton",
        background=[("pressed", "#EAECF0"), ("active", "#F2F4F7"), ("disabled", Palette.SURFACE_ALT)],
        foreground=[("disabled", Palette.TEXT_DISABLED)],
    )
    style.configure(
        "AccentSoft.TButton",
        **common_button,
        background=Palette.ACCENT_SOFT,
        foreground=Palette.ACCENT_PRESSED,
    )
    style.map(
        "AccentSoft.TButton",
        background=[("pressed", "#D9DDFF"), ("active", Palette.ACCENT_SOFT_HOVER), ("disabled", Palette.SURFACE_ALT)],
        foreground=[("disabled", Palette.TEXT_DISABLED)],
    )
    style.configure(
        "Ghost.TButton",
        **common_button,
        background=Palette.SURFACE,
        foreground=Palette.TEXT_SECONDARY,
    )
    style.map(
        "Ghost.TButton",
        background=[("pressed", "#EAECF0"), ("active", Palette.SURFACE_ALT)],
    )
    style.configure(
        "Danger.TButton",
        **common_button,
        background=Palette.DANGER_SOFT,
        foreground=Palette.DANGER,
    )
    style.map("Danger.TButton", background=[("active", "#FEE4E2"), ("pressed", "#FECDCA")])

    style.configure(
        "Modern.TEntry",
        fieldbackground=Palette.SURFACE_ALT,
        foreground=Palette.TEXT,
        insertcolor=Palette.TEXT,
        bordercolor=Palette.BORDER,
        lightcolor=Palette.BORDER,
        darkcolor=Palette.BORDER,
        padding=(12, 9),
        relief="flat",
    )
    style.map(
        "Modern.TEntry",
        bordercolor=[("focus", Palette.ACCENT), ("!focus", Palette.BORDER)],
        lightcolor=[("focus", Palette.ACCENT), ("!focus", Palette.BORDER)],
        darkcolor=[("focus", Palette.ACCENT), ("!focus", Palette.BORDER)],
    )

    style.configure(
        "Modern.Treeview",
        font=(FONT_UI, 10),
        rowheight=42,
        background=Palette.SURFACE,
        foreground=Palette.TEXT_SECONDARY,
        fieldbackground=Palette.SURFACE,
        borderwidth=0,
        relief="flat",
    )
    style.configure(
        "Modern.Treeview.Heading",
        font=(FONT_UI, 9, "bold"),
        background=Palette.SURFACE_ALT,
        foreground=Palette.TEXT_MUTED,
        relief="flat",
        borderwidth=0,
        padding=(10, 9),
    )
    style.map(
        "Modern.Treeview",
        background=[("selected", Palette.SELECTION)],
        foreground=[("selected", Palette.ACCENT_PRESSED)],
    )
    style.map("Modern.Treeview.Heading", background=[("active", "#F2F4F7")])

    style.configure(
        "Modern.Vertical.TScrollbar",
        gripcount=0,
        background=Palette.BORDER_STRONG,
        troughcolor=Palette.SURFACE,
        bordercolor=Palette.SURFACE,
        lightcolor=Palette.BORDER_STRONG,
        darkcolor=Palette.BORDER_STRONG,
        arrowsize=0,
        width=10,
    )
    style.configure(
        "Modern.Horizontal.TScrollbar",
        gripcount=0,
        background=Palette.BORDER_STRONG,
        troughcolor=Palette.SURFACE,
        bordercolor=Palette.SURFACE,
        lightcolor=Palette.BORDER_STRONG,
        darkcolor=Palette.BORDER_STRONG,
        arrowsize=0,
        width=10,
    )
    style.configure(
        "Brand.Horizontal.TProgressbar",
        troughcolor=Palette.SURFACE_ALT,
        background=Palette.ACCENT,
        bordercolor=Palette.SURFACE_ALT,
        lightcolor=Palette.ACCENT,
        darkcolor=Palette.ACCENT,
        thickness=5,
    )
    style.configure("Modern.TPanedwindow", background=Palette.WINDOW, sashwidth=8)
    style.configure("Sash", sashthickness=8, gripcount=0)

    # Combobox 是 ttk 里少数不吃 style.configure 背景的控件，必须逐项 map，
    # 否则深色主题下会留下一块刺眼的白底黑字下拉框。
    style.configure(
        "TCombobox",
        fieldbackground=Palette.SURFACE_ALT,
        background=Palette.SURFACE_ALT,
        foreground=Palette.TEXT,
        arrowcolor=Palette.TEXT_MUTED,
        bordercolor=Palette.BORDER,
        lightcolor=Palette.BORDER,
        darkcolor=Palette.BORDER,
        selectbackground=Palette.SURFACE_ALT,
        selectforeground=Palette.TEXT,
        padding=(8, 5),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", Palette.SURFACE_ALT), ("disabled", Palette.SURFACE)],
        foreground=[("readonly", Palette.TEXT), ("disabled", Palette.TEXT_DISABLED)],
        background=[("readonly", Palette.SURFACE_ALT), ("active", Palette.SURFACE_ALT)],
        arrowcolor=[("active", Palette.ACCENT)],
        bordercolor=[("focus", Palette.ACCENT)],
    )
    # 下拉弹出列表是 Tk 原生 Listbox，只能通过 option database 上色。
    root.option_add("*TCombobox*Listbox.background", Palette.SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", Palette.TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", Palette.ACCENT_SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", Palette.ACCENT_PRESSED)
    style.configure(
        "Modern.TCheckbutton",
        background=Palette.SURFACE,
        foreground=Palette.TEXT_SECONDARY,
        font=(FONT_UI, 9),
    )
    style.map("Modern.TCheckbutton", background=[("active", Palette.SURFACE)])
    return style


def place_centered(window: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    parent.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")

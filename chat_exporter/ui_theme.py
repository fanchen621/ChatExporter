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
    """Claude 风格：温暖的燕麦纸底 + 珊瑚陶土主色 + 石板墨色文字。

    刻意避开饱和的蓝紫/荧光绿——整套色相收在暖区，靠明度而不是彩度拉开层次。
    带文字的填充色（按钮）走 ACCENT，对比度 ≥ 4.5:1；纯图形（圆点、图标）走
    更亮的 AI_ACCENT，图形对比只需 3:1。
    """

    WINDOW = "#F0EEE6"          # 燕麦纸：整个窗口的底
    SIDEBAR = "#262624"         # 暖近黑，不是冷灰
    SIDEBAR_RAISED = "#333331"
    SIDEBAR_HOVER = "#3D3D3A"
    SURFACE = "#FFFFFF"
    SURFACE_ALT = "#FAF9F5"     # 象牙
    SURFACE_HOVER = "#F3F1EA"   # 与 WINDOW 刻意错开：换肤查找表按色值建，同值会互相顶掉
    SURFACE_PRESSED = "#E7E4D9"
    BORDER = "#E5E2D9"
    BORDER_STRONG = "#D3CFC2"
    TEXT = "#191919"            # 石板墨
    TEXT_SECONDARY = "#3D3D3A"
    TEXT_MUTED = "#6E6B62"
    TEXT_DISABLED = "#9B978C"
    TEXT_ON_DARK = "#FAF9F5"
    TEXT_ON_DARK_MUTED = "#A8A49A"
    SIDEBAR_TEXT_OFF = "#908C81"     # 侧栏里"未检测到"的来源，仍是正文级 4.5:1
    SIDEBAR_DOT_OFF = "#4A4A47"
    ACCENT = "#B5583A"          # 主填充：白字上去是 4.7:1，过 AA
    ACCENT_HOVER = "#A34E33"    # 悬停压深一档：亮珊瑚上的白字只有 3.9:1，过不了 AA
    ACCENT_PRESSED = "#9C4A2F"
    ACCENT_SOFT = "#F7EBE5"
    ACCENT_SOFT_HOVER = "#F0DDD3"
    ACCENT_DISABLED = "#E3C8BC"
    ON_ACCENT = "#FFFFFF"       # 主色填充上的文字
    ON_ACCENT_MUTED = "#F3DED5"
    # 阅读视图角色色：AI 用 Claude 珊瑚，用户用暖石中性色——克制才像 Claude
    AI_ACCENT = "#C96442"
    USER_ACCENT = "#78736A"
    SUCCESS = "#477655"         # 苔绿，不是荧光绿
    SUCCESS_SOFT = "#EAF0EA"
    WARNING = "#8F6525"
    WARNING_SOFT = "#F7EFE0"
    DANGER = "#B4432F"
    DANGER_SOFT = "#F8E9E5"
    INFO = "#526F8C"            # 唯一的冷色，只用于状态点
    INFO_SOFT = "#EAEFF4"
    # 来源徽章：它标的是"这段内容来自哪个平台"，是元信息不是状态，
    # 所以走主色的柔和档，不借用 SUCCESS 的绿——绿色在这套暖色里格格不入。
    BADGE_BG = "#F7EBE5"
    BADGE_FG = "#8F4229"
    CODE_BG = "#F5F3ED"
    SELECTION = "#F7EBE5"
    SCROLLBAR = "#CFCABC"
    SCROLLBAR_TROUGH = "#F6F4EC"  # 同上，且提亮后滑块更清楚
    SCROLLBAR_ACTIVE = "#A9A395"


LIGHT_THEME = {name: value for name, value in vars(Palette).items() if not name.startswith("_")}

DARK_THEME = {
    # Claude 深色：暖炭底，不是蓝黑。主色提亮一档，按钮改用深字，
    # 亮珊瑚配近黑文字是 5.4:1，比白字的 3.1:1 干净得多。
    "WINDOW": "#1B1A18",
    "SIDEBAR": "#141312",
    "SIDEBAR_RAISED": "#2A2A27",
    "SIDEBAR_HOVER": "#373733",
    "SURFACE": "#262624",
    "SURFACE_ALT": "#30302E",
    "SURFACE_HOVER": "#3A3A37",
    "SURFACE_PRESSED": "#454541",
    "BORDER": "#3A3936",
    "BORDER_STRONG": "#524F49",
    "TEXT": "#F5F4EF",
    "TEXT_SECONDARY": "#D6D3CA",
    "TEXT_MUTED": "#A19D93",
    "TEXT_DISABLED": "#726E66",
    "TEXT_ON_DARK": "#FAF9F5",
    "TEXT_ON_DARK_MUTED": "#A8A49A",
    "SIDEBAR_TEXT_OFF": "#817D72",
    "SIDEBAR_DOT_OFF": "#4A4A47",
    "ACCENT": "#D97757",
    "ACCENT_HOVER": "#E38A6C",
    "ACCENT_PRESSED": "#EFA087",
    "ACCENT_SOFT": "#3A251C",
    "ACCENT_SOFT_HOVER": "#4A3025",
    "ACCENT_DISABLED": "#5C3F33",
    "ON_ACCENT": "#1B1A18",
    "ON_ACCENT_MUTED": "#4A3025",
    "AI_ACCENT": "#E08A6B",
    "USER_ACCENT": "#A8A296",
    "SUCCESS": "#7FB08C",
    "SUCCESS_SOFT": "#1E2A21",
    "WARNING": "#DDA84E",
    "WARNING_SOFT": "#2E2517",
    "DANGER": "#E38270",
    "DANGER_SOFT": "#331C18",
    "INFO": "#8AA9C4",
    "INFO_SOFT": "#1C2530",
    "BADGE_BG": "#3A251C",
    "BADGE_FG": "#E9A184",
    "CODE_BG": "#2C2C2A",
    "SELECTION": "#3A251C",
    "SCROLLBAR": "#4E4D48",
    "SCROLLBAR_TROUGH": "#242321",
    "SCROLLBAR_ACTIVE": "#6E6B63",
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


_FG_KEYS = (
    "TEXT", "TEXT_SECONDARY", "TEXT_MUTED", "TEXT_DISABLED",
    "TEXT_ON_DARK", "TEXT_ON_DARK_MUTED", "SIDEBAR_TEXT_OFF",
    "AI_ACCENT", "USER_ACCENT", "ON_ACCENT", "ON_ACCENT_MUTED",
    "BADGE_FG",
)


# 双重身份的 token：既当填充色又当文字色。
# 例：SUCCESS 在侧栏是状态圆点的 bg（gui_cn_v2.py:1181），在"可用"标签上是 fg
# （gui_cn_v2.py:1177）。只登记进 bg 表的话，那些文字换肤后会停在旧色。
_DUAL_KEYS = (
    "ACCENT", "ACCENT_HOVER", "ACCENT_PRESSED",
    "SUCCESS", "WARNING", "DANGER", "INFO",
)


def theme_color_map(source: str, target: str) -> dict:
    """旧主题色 -> 新主题色查找表，前景/背景分开。

    同一个色值可能同时是浅色主题的某个背景和某个前景（例：#F8FAFC 既是
    SURFACE_ALT 又是 TEXT_ON_DARK），一张表按值映射必然选错其中一边——
    v2.0 初版就把深色模式下的侧栏文字染成了背景色。

    _DUAL_KEYS 里的 token 两张表都登记：它们两种身份都真实存在，
    而且色值不与任何单一身份的 token 冲突（见 test_theme_invariants）。
    """
    old = THEMES.get(source) or LIGHT_THEME
    new = THEMES.get(target) or LIGHT_THEME
    fg_map: dict = {}
    bg_map: dict = {}
    for key, value in old.items():
        replacement = new.get(key)
        if not replacement or value == replacement:
            continue
        folded = value.casefold()
        if key in _FG_KEYS:
            fg_map[folded] = replacement
        elif key in _DUAL_KEYS:
            fg_map[folded] = replacement
            bg_map[folded] = replacement
        else:
            bg_map[folded] = replacement
    return {"fg": fg_map, "bg": bg_map}


_FG_OPTIONS = ("foreground", "fg", "activeforeground", "disabledforeground", "insertbackground", "selectforeground")
_BG_OPTIONS = (
    "background", "bg", "activebackground", "highlightbackground", "highlightcolor",
    "selectbackground", "troughcolor",
    # 勾选框中间那个小方块。漏了它，深色模式下"只看对话"的方块会一直是白的。
    "selectcolor",
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

    # 先整体快照、再统一写回。tk 里 -bg 就是 -background 的别名（-fg 同理），
    # 边读边写会把刚写进去的新颜色当成"旧颜色"再映射一次：
    # 卡片 #FFFFFF 先正确变成深色 SURFACE #262624，紧接着 #262624 又被当成
    # 浅色 SIDEBAR 命中，最终落到 #141312——每张白卡片都会染成侧栏色。
    updates: dict = {}
    for option in _FG_OPTIONS:
        try:
            current = str(widget.cget(option)).casefold()
        except Exception:
            continue
        replacement = fg_map.get(current)
        if replacement:
            updates[option] = replacement
    for option in _BG_OPTIONS:
        try:
            current = str(widget.cget(option)).casefold()
        except Exception:
            continue
        replacement = bg_map.get(current)
        if replacement:
            updates[option] = replacement

    for option, value in updates.items():
        try:
            widget.configure(**{option: value})
        except Exception:
            pass
    for child in widget.winfo_children():
        retheme_widgets(child, mapping)


def retheme_combobox_popdowns(widget: tk.Misc) -> None:
    """把 ttk 下拉框的弹出列表也刷成当前主题。

    弹出列表是 Tcl 层建的，没有 Python 包装对象；Tkinter 的 winfo_children()
    会静默跳过这类子控件（拿不到就 pass），所以 retheme_widgets 永远走不到它——
    下拉过一次之后，那个列表就永久停在打开它时的主题。
    """
    stack = [widget]
    while stack:
        node = stack.pop()
        if isinstance(node, ttk.Combobox):
            try:
                popdown = node.tk.call("ttk::combobox::PopdownWindow", node)
                node.tk.call(
                    f"{popdown}.f.l", "configure",
                    "-background", Palette.SURFACE,
                    "-foreground", Palette.TEXT,
                    "-selectbackground", Palette.ACCENT_SOFT,
                    "-selectforeground", Palette.ACCENT_PRESSED,
                )
            except Exception:
                pass
        try:
            stack.extend(node.winfo_children())
        except Exception:
            pass


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
    style.configure("CommandBar.TFrame", background=Palette.SURFACE_ALT)
    style.configure(
        "Card.TFrame",
        background=Palette.SURFACE,
        relief="solid",
        borderwidth=1,
        bordercolor=Palette.BORDER,
        lightcolor=Palette.BORDER,
        darkcolor=Palette.BORDER,
    )
    style.configure("CardAlt.TFrame", background=Palette.SURFACE_ALT, relief="flat")
    style.configure("SelectionBar.TFrame", background=Palette.ACCENT_SOFT, relief="flat")
    style.configure("Overlay.TFrame", background=Palette.SURFACE)

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
        font=(FONT_UI, 18, "bold"),
    )
    style.configure(
        "PageSub.TLabel",
        background=Palette.SURFACE,
        foreground=Palette.TEXT_MUTED,
        font=(FONT_UI, 10),
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
        "CommandHint.TLabel",
        background=Palette.SURFACE_ALT,
        foreground=Palette.TEXT_MUTED,
        font=(FONT_UI, 9),
    )
    style.configure(
        "Chip.TLabel",
        background=Palette.ACCENT_SOFT,
        foreground=Palette.ACCENT_PRESSED,
        font=(FONT_UI, 8, "bold"),
        padding=(8, 3),
    )
    style.configure(
        "SelectionBar.TLabel",
        background=Palette.ACCENT_SOFT,
        foreground=Palette.ACCENT_PRESSED,
        font=(FONT_UI, 9, "bold"),
    )
    style.configure(
        "OverlayTitle.TLabel",
        background=Palette.SURFACE,
        foreground=Palette.TEXT,
        font=(FONT_UI, 15, "bold"),
    )
    style.configure(
        "OverlayBody.TLabel",
        background=Palette.SURFACE,
        foreground=Palette.TEXT_MUTED,
        font=(FONT_UI, 10),
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
        focusthickness=1,
        focuscolor=Palette.ACCENT_SOFT,
    )
    style.configure(
        "Primary.TButton",
        **common_button,
        borderwidth=0,
        background=Palette.ACCENT,
        foreground=Palette.ON_ACCENT,
    )
    style.map(
        "Primary.TButton",
        background=[
            ("pressed", Palette.ACCENT_PRESSED),
            ("active", Palette.ACCENT_HOVER),
            ("disabled", Palette.ACCENT_DISABLED),
        ],
        foreground=[("disabled", Palette.ON_ACCENT_MUTED)],
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
        # hover/pressed 一律取调色板键——写死的浅灰在深色主题下会闪白
        background=[("pressed", Palette.SURFACE_PRESSED), ("active", Palette.SURFACE_HOVER), ("disabled", Palette.SURFACE_ALT)],
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
        background=[("pressed", Palette.ACCENT_SOFT_HOVER), ("active", Palette.ACCENT_SOFT_HOVER), ("disabled", Palette.SURFACE_ALT)],
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
        background=[("pressed", Palette.SURFACE_PRESSED), ("active", Palette.SURFACE_HOVER)],
    )
    compact_button = dict(
        font=(FONT_UI, 9),
        padding=(9, 5),
        relief="flat",
        focusthickness=1,
        focuscolor=Palette.ACCENT_SOFT,
    )
    style.configure(
        "Compact.TButton",
        **compact_button,
        borderwidth=0,
        background=Palette.SURFACE,
        foreground=Palette.TEXT_SECONDARY,
    )
    style.map(
        "Compact.TButton",
        background=[
            ("pressed", Palette.SURFACE_PRESSED),
            ("active", Palette.SURFACE_HOVER),
            ("disabled", Palette.SURFACE),
        ],
        foreground=[("disabled", Palette.TEXT_DISABLED)],
    )
    style.configure(
        "CompactSecondary.TButton",
        **compact_button,
        borderwidth=1,
        background=Palette.SURFACE_ALT,
        foreground=Palette.TEXT_SECONDARY,
    )
    style.map(
        "CompactSecondary.TButton",
        background=[
            ("pressed", Palette.SURFACE_PRESSED),
            ("active", Palette.SURFACE_HOVER),
            ("disabled", Palette.SURFACE_ALT),
        ],
        foreground=[("disabled", Palette.TEXT_DISABLED)],
    )
    pager_button = dict(compact_button)
    pager_button["padding"] = (9, 4)
    pager_button["relief"] = "flat"
    style.configure(
        "Pager.TButton",
        **pager_button,
        borderwidth=0,
        background=Palette.SURFACE_ALT,
        foreground=Palette.TEXT_SECONDARY,
    )
    style.map(
        "Pager.TButton",
        background=[
            ("pressed", Palette.SURFACE_PRESSED),
            ("active", Palette.ACCENT_SOFT),
            ("disabled", Palette.SURFACE_ALT),
        ],
        foreground=[
            ("disabled", Palette.TEXT_DISABLED),
            ("active", Palette.TEXT),
        ],
    )
    style.configure(
        "Danger.TButton",
        **common_button,
        background=Palette.DANGER_SOFT,
        foreground=Palette.DANGER,
    )
    style.map("Danger.TButton", background=[("active", Palette.SURFACE_HOVER), ("pressed", Palette.SURFACE_PRESSED)])
    style.configure(
        "SelectionAction.TButton",
        **compact_button,
        borderwidth=0,
        background=Palette.ACCENT_SOFT,
        foreground=Palette.ACCENT_PRESSED,
    )
    style.map(
        "SelectionAction.TButton",
        background=[("active", Palette.ACCENT_SOFT_HOVER), ("pressed", Palette.ACCENT_SOFT_HOVER)],
        foreground=[("disabled", Palette.TEXT_DISABLED)],
    )

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
        rowheight=44,
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
        padding=(10, 10),
    )
    style.map(
        "Modern.Treeview",
        background=[("selected", Palette.SELECTION)],
        foreground=[("selected", Palette.ACCENT_PRESSED)],
    )
    style.map("Modern.Treeview.Heading", background=[("active", Palette.SURFACE_HOVER)])

    style.configure(
        "Modern.Vertical.TScrollbar",
        gripcount=0,
        background=Palette.SCROLLBAR,
        troughcolor=Palette.SCROLLBAR_TROUGH,
        bordercolor=Palette.SCROLLBAR_TROUGH,
        lightcolor=Palette.SCROLLBAR,
        darkcolor=Palette.SCROLLBAR,
        arrowsize=0,
        width=10,
    )
    style.configure(
        "Modern.Horizontal.TScrollbar",
        gripcount=0,
        background=Palette.SCROLLBAR,
        troughcolor=Palette.SCROLLBAR_TROUGH,
        bordercolor=Palette.SCROLLBAR_TROUGH,
        lightcolor=Palette.SCROLLBAR,
        darkcolor=Palette.SCROLLBAR,
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
    style.configure(
        "Compact.TCombobox",
        fieldbackground=Palette.SURFACE_ALT,
        background=Palette.SURFACE_ALT,
        foreground=Palette.TEXT,
        arrowcolor=Palette.TEXT_MUTED,
        bordercolor=Palette.BORDER,
        lightcolor=Palette.BORDER,
        darkcolor=Palette.BORDER,
        selectbackground=Palette.SURFACE_ALT,
        selectforeground=Palette.TEXT,
        padding=(7, 4),
    )
    style.map(
        "Compact.TCombobox",
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
        focusthickness=1,
        focuscolor=Palette.ACCENT_SOFT,
    )
    style.map("Modern.TCheckbutton", background=[("active", Palette.SURFACE)])
    return style


def place_centered(window: tk.Toplevel, parent: tk.Misc, width: int, height: int) -> None:
    parent.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
    window.geometry(f"{width}x{height}+{x}+{y}")

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

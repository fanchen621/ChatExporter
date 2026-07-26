from __future__ import annotations

import os
import ctypes
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

from .adapters.qclaw_compat import QClawAdapter as QClawCompatAdapter
from .adapters.workbuddy_compat import WorkBuddyAdapter as WorkBuddyCompatAdapter
from .exporters import FORMAT_CHOICES, format_label, get_exporter, unique_path
from .gui_cn_v2 import ChatExporterGUI as BaseChineseGUI
from .ui_theme import (
    FONT_LATIN,
    FONT_UI,
    Metrics,
    Palette,
    apply_app_icon,
    apply_theme,
    configure_styles,
    place_centered,
    retheme_widgets,
    theme_color_map,
)


class _NullSettings:
    """偏好存储不可用时的兜底：读到默认值、写入丢弃，绝不让偏好把主程序拖崩。"""

    def get(self, _key, default=None):
        return default

    def set(self, _key, _value, autosave=True):
        return None

    def save(self):
        return None


def _load_settings():
    try:
        from .settings import get_settings
        return get_settings()
    except Exception:
        return _NullSettings()


class ChatExporterGUI(BaseChineseGUI):
    """v1.1.3：面向高 DPI 真机的自适应中文工作台。"""

    SIDEBAR_WIDTH = 304

    DATE_FILTER_ALL = "全部时间"
    DATE_FILTERS = (
        (DATE_FILTER_ALL, None),
        ("今天", 0),
        ("近 7 天", 7),
        ("近 30 天", 30),
        ("近 90 天", 90),
        ("近一年", 365),
    )

    def __init__(self):
        # 主题必须在任何控件被创建之前定下来：界面代码是在构建时直接读 Palette 的。
        self.settings = _load_settings()
        self.theme_name = apply_theme(self.settings.get("theme", "light") or "light")
        super().__init__()

        # 程序检测通过 root.after() 延迟启动，因此在这里替换适配器仍早于首次检测。
        replacements = {
            "workbuddy": WorkBuddyCompatAdapter,
            "qclaw": QClawCompatAdapter,
        }
        self.adapters = [
            replacements[adapter.name]() if adapter.name in replacements else adapter
            for adapter in self.adapters
        ]

        self._apply_screen_geometry()
        self.root.title("ChatExporter · 本地对话归档工作台")
        apply_app_icon(self.root)
        self._restore_saved_geometry()

    def _apply_screen_geometry(self):
        self.root.update_idletasks()
        screen_w = max(1024, self.root.winfo_screenwidth())
        screen_h = max(700, self.root.winfo_screenheight())

        width = min(1760, max(1180, int(screen_w * 0.92)))
        height = min(980, max(680, int(screen_h * 0.86)))
        width = min(width, max(980, screen_w - 40))
        height = min(height, max(620, screen_h - 70))

        min_w = min(1360, max(1020, screen_w - 80))
        min_h = min(760, max(620, screen_h - 110))
        self.root.minsize(min_w, min_h)

        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self._enable_double_buffering()

    def _restore_saved_geometry(self):
        saved = self.settings.get("window_geometry", "")
        if not saved:
            return
        try:
            # 只接受仍落在当前屏幕内的几何值：换过显示器的旧位置直接丢弃。
            size, _, offset = saved.partition("+")
            w, h = (int(v) for v in size.split("x"))
            x, y = (int(v) for v in offset.split("+")) if "+" in offset else (0, 0)
            screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            if 600 <= w <= screen_w and 480 <= h <= screen_h and 0 <= x < screen_w - 200 and 0 <= y < screen_h - 200:
                self.root.geometry(saved)
        except (ValueError, tk.TclError):
            pass

    def _on_close(self):
        try:
            self.settings.set("window_geometry", self.root.geometry(), autosave=False)
            if hasattr(self, "workspace_panes"):
                self.settings.set("sash_position", self.workspace_panes.sashpos(0), autosave=False)
            self.settings.save()
        except Exception:
            pass
        super()._on_close()

    def _select_app(self, adapter):
        # 同一来源重复选中不重新加载：自动回位刚发起的加载还在途时用户又点一下，
        # 会叠出第二次全量读取（TRAE 一次约 4 秒），前一次的结果还会被判废丢弃。
        # 想重读请用"刷新当前来源"。
        if adapter is self.current_adapter:
            return
        super()._select_app(adapter)
        self.settings.set("last_source", getattr(adapter, "name", ""))

    def _on_apps_detected(self, results):
        super()._on_apps_detected(results)
        # 首次检测完成后自动回到上次的来源，省一次每天都要点的鼠标。
        if getattr(self, "_auto_selected_once", False):
            return
        self._auto_selected_once = True
        last = self.settings.get("last_source", "")
        if not last:
            return
        for adapter, _info, available in results:
            if available and adapter.name == last:
                self.root.after_idle(lambda a=adapter: self._select_app(a))
                break

    def _enable_double_buffering(self):
        """启用 Windows WS_EX_COMPOSITED 双缓冲，消除窗口缩放时的闪烁。"""
        if os.name != "nt":
            return
        try:
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_COMPOSITED = 0x02000000
            user32 = ctypes.windll.user32
            ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_COMPOSITED)
        except Exception:
            pass

    # ========== 自适应外壳：禁止固定高度裁切 ==========

    def _build_shell(self):
        self._configure_wide_scrollbar_style()

        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, minsize=self.SIDEBAR_WIDTH, weight=0)
        self.root.grid_columnconfigure(1, weight=1)

        sidebar = ttk.Frame(self.root, style="Sidebar.TFrame", width=self.SIDEBAR_WIDTH)
        sidebar.grid(row=0, column=0, sticky="nsew")
        # 不使用 grid_propagate(False)：列 minsize 已约束宽度，
        # grid_propagate(False) 会导致 grid 管理器在窗口缩放时反复重算。

        workspace = ttk.Frame(self.root, style="App.TFrame")
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.grid_rowconfigure(1, weight=1)
        workspace.grid_columnconfigure(0, weight=1)

        self._build_sidebar(sidebar)
        self._build_header(workspace)
        self._build_workspace(workspace)
        self._build_status_bar(workspace)

    def _configure_wide_scrollbar_style(self):
        self.style.configure(
            "Wide.Vertical.TScrollbar",
            gripcount=0,
            width=20,
            arrowsize=14,
            background=Palette.SCROLLBAR,
            troughcolor=Palette.SCROLLBAR_TROUGH,
            bordercolor=Palette.BORDER,
            lightcolor=Palette.SCROLLBAR,
            darkcolor=Palette.SCROLLBAR,
        )
        self.style.map(
            "Wide.Vertical.TScrollbar",
            background=[("active", Palette.SCROLLBAR_ACTIVE), ("pressed", Palette.SCROLLBAR_ACTIVE)],
        )

    def _build_header(self, parent):
        # 不固定高度，不关闭 geometry propagation，让高 DPI 字体获得真实所需空间。
        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title_row = ttk.Frame(header, style="Surface.TFrame")
        title_row.grid(row=0, column=0, sticky="ew", padx=Metrics.PAD_X, pady=(14, 7))
        title_row.grid_columnconfigure(0, weight=1)

        left = ttk.Frame(title_row, style="Surface.TFrame")
        left.grid(row=0, column=0, sticky="w")
        self.page_title_var = tk.StringVar(value="本地对话库")
        self.page_subtitle_var = tk.StringVar(value="请选择左侧数据来源")
        ttk.Label(left, textvariable=self.page_title_var, style="PageTitle.TLabel").pack(anchor=tk.W)

        subline = ttk.Frame(left, style="Surface.TFrame")
        subline.pack(anchor=tk.W, pady=(4, 0))
        self.source_badge = tk.Label(
            subline,
            text="未选择",
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT_MUTED,
            font=(FONT_UI, 8, "bold"),
            padx=8,
            pady=3,
        )
        self.source_badge.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(subline, textvariable=self.page_subtitle_var, style="PageSub.TLabel").pack(side=tk.LEFT)

        action_row = ttk.Frame(header, style="Surface.TFrame")
        action_row.grid(row=1, column=0, sticky="ew", padx=Metrics.PAD_X, pady=(0, 13))
        action_row.grid_columnconfigure(0, weight=1)

        left_actions = ttk.Frame(action_row, style="Surface.TFrame")
        left_actions.grid(row=0, column=0, sticky="w")
        self.key_button = ttk.Button(
            left_actions,
            text="获取 TRAE 密钥",
            style="AccentSoft.TButton",
            command=self._open_key_assistant,
        )
        self.key_button.pack(side=tk.LEFT, padx=(0, 8))
        self.refresh_button = ttk.Button(
            left_actions,
            text="刷新当前来源",
            style="Secondary.TButton",
            command=self._reload_current_source,
        )
        self.refresh_button.pack(side=tk.LEFT)

        self.theme_button = ttk.Button(
            left_actions,
            text="🌙 深色",
            style="Ghost.TButton",
            command=self._toggle_theme,
        )
        self.theme_button.pack(side=tk.LEFT, padx=(8, 0))

        right_actions = ttk.Frame(action_row, style="Surface.TFrame")
        right_actions.grid(row=0, column=1, sticky="e")

        ttk.Label(right_actions, text="格式", style="Muted.TLabel").pack(side=tk.LEFT, padx=(0, 5))
        saved_format = str(self.settings.get("export_format", "markdown"))
        if saved_format not in {fid for fid, _label in FORMAT_CHOICES}:
            saved_format = "markdown"
        self.export_format_var = tk.StringVar(value=format_label(saved_format))
        format_box = ttk.Combobox(
            right_actions,
            textvariable=self.export_format_var,
            values=[label for _fid, label in FORMAT_CHOICES],
            state="readonly",
            width=9,
            font=(FONT_UI, 9),
        )
        format_box.pack(side=tk.LEFT, padx=(0, 10))
        format_box.bind(
            "<<ComboboxSelected>>",
            lambda _e: self.settings.set("export_format", self._selected_format_id()),
        )

        self.batch_button = ttk.Button(
            right_actions,
            text="批量导出",
            style="Secondary.TButton",
            command=self._export_all,
        )
        self.batch_button.pack(side=tk.LEFT, padx=(0, 8))
        self.export_button = ttk.Button(
            right_actions,
            text="导出当前对话",
            style="Primary.TButton",
            command=self._export_selected,
        )
        self.export_button.pack(side=tk.LEFT)

        tk.Frame(header, bg=Palette.BORDER, height=1).grid(row=2, column=0, sticky="ew")

    def _preferred_library_width(self) -> int:
        screen_w = self.root.winfo_screenwidth()
        if screen_w >= 1900:
            return 700
        if screen_w >= 1600:
            return 640
        if screen_w >= 1400:
            return 580
        return 520

    def _build_workspace(self, parent):
        container = ttk.Frame(parent, style="App.TFrame")
        container.grid(row=1, column=0, sticky="nsew", padx=16, pady=(12, 10))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        # 可拖拽分栏：列表和预览的宽度由用户决定，长标题不再被固定列宽卡死。
        self.workspace_panes = ttk.PanedWindow(container, orient=tk.HORIZONTAL, style="Modern.TPanedwindow")
        self.workspace_panes.grid(row=0, column=0, sticky="nsew")

        library = ttk.Frame(self.workspace_panes, style="Card.TFrame")
        preview = ttk.Frame(self.workspace_panes, style="Card.TFrame")
        self.workspace_panes.add(library, weight=2)
        self.workspace_panes.add(preview, weight=3)

        self._build_library_card(library)
        self._build_preview_card(preview)

        # 首次布局后把分隔条放到偏好宽度；用户拖过之后不再覆盖。
        self.root.after_idle(self._place_initial_sash)

    def _place_initial_sash(self):
        try:
            width = self.workspace_panes.winfo_width()
            if width <= 400:
                return
            saved = self.settings.get("sash_position", 0)
            target = saved if isinstance(saved, int) and 240 <= saved <= width - 360 else self._preferred_library_width()
            self.workspace_panes.sashpos(0, min(target, width - 420))
        except tk.TclError:
            pass

    # ========== 宽对话列表 ==========

    def _build_library_card(self, parent):
        parent.grid_rowconfigure(4, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self._apply_card_border(parent)

        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(14, 8))
        # 标题列固定不压缩，计数列吸收多余空间：窄分栏下不再把"对话列表"截成"对话列"。
        header.grid_columnconfigure(1, weight=1)
        ttk.Label(header, text="对话列表", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.library_count_var = tk.StringVar(value="0 条")
        ttk.Label(header, textvariable=self.library_count_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(10, 10)
        )
        self.sort_var = tk.StringVar(value="最近更新")
        sort_box = ttk.Combobox(
            header,
            textvariable=self.sort_var,
            values=("最近更新", "消息最多", "标题排序"),
            state="readonly",
            width=9,
            font=(FONT_UI, 9),
        )
        sort_box.grid(row=0, column=2, sticky="e")
        sort_box.bind("<<ComboboxSelected>>", lambda _e: self._filter_conversations())

        # 范围和时间两个下拉各占一半宽度：窄分栏下也不会把右边那个挤出可视区。
        mode_row = ttk.Frame(parent, style="Surface.TFrame")
        mode_row.grid(row=1, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(0, 6))
        mode_row.grid_columnconfigure(1, weight=1, uniform="filters")
        mode_row.grid_columnconfigure(3, weight=1, uniform="filters")

        ttk.Label(mode_row, text="范围", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.search_mode_var = tk.StringVar(value=self.SEARCH_MODE_TITLE)
        mode_box = ttk.Combobox(
            mode_row,
            textvariable=self.search_mode_var,
            values=(self.SEARCH_MODE_TITLE, self.SEARCH_MODE_CONTENT),
            state="readonly",
            width=7,
            font=(FONT_UI, 9),
        )
        mode_box.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        mode_box.bind("<<ComboboxSelected>>", self._on_search_mode_changed)

        ttk.Label(mode_row, text="时间", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        self.date_filter_var = tk.StringVar(value=self.DATE_FILTER_ALL)
        date_box = ttk.Combobox(
            mode_row,
            textvariable=self.date_filter_var,
            values=tuple(label for label, _days in self.DATE_FILTERS),
            state="readonly",
            width=7,
            font=(FONT_UI, 9),
        )
        date_box.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        date_box.bind("<<ComboboxSelected>>", lambda _e: self._filter_conversations())

        search_row = tk.Frame(parent, bg=Palette.SURFACE, bd=0)
        search_row.grid(row=2, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(0, 5))
        search_row.grid_columnconfigure(0, weight=1)
        search_wrap = tk.Frame(
            search_row,
            bg=Palette.SURFACE_ALT,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            bd=0,
            padx=10,
            pady=2,
        )
        search_wrap.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.search_var = tk.StringVar(value="搜索标题…")
        self.search_entry = tk.Entry(
            search_wrap,
            textvariable=self.search_var,
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT_DISABLED,
            insertbackground=Palette.TEXT,
            font=(FONT_UI, 10),
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
        )
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=7)
        self.search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self.search_entry.bind("<FocusOut>", self._on_search_focus_out)
        self.search_entry.bind("<Return>", lambda _e: self._filter_conversations())
        self.search_var.trace_add("write", lambda *_: self._schedule_filter())
        ttk.Button(search_row, text="清除", style="Ghost.TButton", command=self._clear_search).grid(
            row=0, column=1, sticky="e"
        )

        self.search_hint_var = tk.StringVar(value="标题搜索即时过滤；切换到“对话内容”可按正文关键词检索。")
        hint_label = ttk.Label(parent, textvariable=self.search_hint_var, style="Muted.TLabel", wraplength=620)
        hint_label.grid(row=3, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(0, 8))
        # 折行宽度跟随卡片实际宽度，否则拖窄分栏时提示语会被截断在半句话上。
        parent.bind(
            "<Configure>",
            lambda e: hint_label.configure(wraplength=max(200, e.width - 2 * Metrics.CARD_PAD - 8)),
            add="+",
        )

        tree_wrap = ttk.Frame(parent, style="Surface.TFrame")
        tree_wrap.grid(row=4, column=0, sticky="nsew", padx=(1, 1))
        tree_wrap.grid_rowconfigure(0, weight=1)
        tree_wrap.grid_columnconfigure(0, weight=1)

        self.conv_tree = ttk.Treeview(
            tree_wrap,
            columns=("title", "date", "messages"),
            show="headings",
            # extended：Ctrl/Shift 多选后"批量导出"只导选中的几条，
            # 不必在"导一条"和"导全部 98 条"之间二选一。
            selectmode="extended",
            style="Modern.Treeview",
        )
        self.conv_tree.heading("title", text="标题")
        self.conv_tree.heading("date", text="更新时间")
        self.conv_tree.heading("messages", text="消息")
        self.conv_tree.column("title", width=410, minwidth=190)
        self.conv_tree.column("date", width=132, minwidth=120, stretch=False, anchor=tk.W)
        self.conv_tree.column("messages", width=66, minwidth=60, stretch=False, anchor=tk.CENTER)
        self.conv_tree.grid(row=0, column=0, sticky="nsew")
        # 写死列宽在某个分栏宽度下必然溢出截字；标题列跟随实际宽度伸缩（实现在 v2）
        self.conv_tree.bind("<Configure>", self._fit_tree_columns, add="+")

        scroll = ttk.Scrollbar(
            tree_wrap,
            orient=tk.VERTICAL,
            command=self.conv_tree.yview,
            style="Wide.Vertical.TScrollbar",
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.conv_tree.configure(yscrollcommand=scroll.set)
        self.conv_tree.bind("<<TreeviewSelect>>", self._on_conv_select, add="+")
        self.conv_tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_action_states(), add="+")
        self.conv_tree.bind("<Double-1>", self._on_tree_double_click)
        self.conv_tree.tag_configure("even", background=Palette.SURFACE)
        self.conv_tree.tag_configure("odd", background=Palette.SURFACE_ALT)
        self.conv_tree.tag_configure("empty", foreground=Palette.TEXT_MUTED)
        self.conv_tree.tag_configure("error", foreground=Palette.DANGER)
        self.conv_tree.tag_configure("loading", foreground=Palette.ACCENT)

        footer = ttk.Frame(parent, style="Surface.TFrame")
        footer.grid(row=5, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(8, 12))
        # 用 grid 而不是 pack：状态文字变长时会挤到右侧提示上，两段文字叠在一起。
        footer.grid_columnconfigure(0, weight=1)
        self.library_footer_var = tk.StringVar(value="请先选择一个数据来源")
        ttk.Label(
            footer, textvariable=self.library_footer_var, style="Muted.TLabel", anchor=tk.W
        ).grid(row=0, column=0, sticky="ew", padx=(0, 12))
        ttk.Label(footer, text="Ctrl+F 搜索", style="Muted.TLabel").grid(row=0, column=1, sticky="e")

    # ========== 厚实滚动条与完整可见区域 ==========

    def _build_preview_card(self, parent):
        super()._build_preview_card(parent)

        # v1.1.2 的预览使用 classic Tk scrollbar；显式设置为不透明、易拖动。
        for widget in self._walk_widgets(parent):
            if isinstance(widget, tk.Scrollbar):
                # 颜色取调色板——写死的浅灰在深色主题下会亮出一条
                widget.configure(
                    width=22,
                    bg=Palette.SCROLLBAR,
                    troughcolor=Palette.SCROLLBAR_TROUGH,
                    activebackground=Palette.SCROLLBAR_ACTIVE,
                    highlightthickness=0,
                    relief=tk.FLAT,
                    bd=0,
                )

        def wheel(event):
            # 按滚轮实际步进量滚动，而不是固定 3 行：触摸板的小幅滚动不再一次跳三行。
            steps = int(event.delta / 120) or (1 if event.delta > 0 else -1)
            self.preview_text.yview_scroll(-steps * 3, "units")
            return "break"

        self.preview_text.bind("<MouseWheel>", wheel)

    # ========== 多格式导出 ==========

    # ========== 时间范围筛选 ==========

    def _date_cutoff(self):
        """选中范围的起始时刻；"全部时间"返回 None。"""
        label = self.date_filter_var.get() if getattr(self, "date_filter_var", None) else self.DATE_FILTER_ALL
        days = next((d for text, d in self.DATE_FILTERS if text == label), None)
        if days is None:
            return None
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return today if days == 0 else today - timedelta(days=days)

    def _passes_date_filter(self, conv) -> bool:
        cutoff = self._date_cutoff()
        if cutoff is None:
            return True
        stamp = conv.updated_at or conv.created_at
        # 没有时间戳的对话不因为筛选而消失：宁可多显示，也不要让人以为它没了。
        return True if stamp is None else stamp >= cutoff

    def _apply_date_filter(self, matches):
        if self._date_cutoff() is None:
            return matches
        return [pair for pair in matches if self._passes_date_filter(pair[1])]

    def _sort_matches(self, matches):
        return super()._sort_matches(self._apply_date_filter(matches))

    def _render_matches(self, matches, query: bool):
        # 时间筛选也算"筛过"：否则计数会显示总数，看着像筛选没生效。
        super()._render_matches(matches, query=query or self._date_cutoff() is not None)

    def _multi_selected_conversations(self):
        """列表里 Ctrl/Shift 选中的对话（少于两条时返回空，走原来的全量批量导出）。"""
        picked = [
            self._tree_conv_map[item]
            for item in self.conv_tree.selection()
            if not item.startswith("__") and item in self._tree_conv_map
        ]
        return picked if len(picked) > 1 else []

    def _export_all(self):
        picked = self._multi_selected_conversations()
        if not picked:
            super()._export_all()
            return

        output_dir = filedialog.askdirectory(
            title=f"导出选中的 {len(picked)} 条对话",
            initialdir=self.settings.get("last_export_dir", "") or None,
        )
        if not output_dir:
            return
        adapter = self.current_adapter
        self._set_status(f"正在导出选中的 {len(picked)} 条…", progress=0, tone="info")
        self.batch_button.configure(state=tk.DISABLED)

        def worker():
            try:
                count, failures = self._batch_export_full_conversations(picked, output_dir, adapter)
                self._post_ui(self._on_batch_export_complete, count, output_dir, failures)
            except Exception as exc:
                self._post_ui(self._on_batch_export_failed, str(exc))

        threading.Thread(target=worker, daemon=True, name="batch-export-selected").start()

    def _sync_action_states(self):
        super()._sync_action_states()
        picked = self._multi_selected_conversations()
        if hasattr(self, "batch_button"):
            self.batch_button.configure(text=f"导出选中 {len(picked)} 条" if picked else "批量导出")

    def _selected_format_id(self) -> str:
        label = self.export_format_var.get() if hasattr(self, "export_format_var") else ""
        for fid, fid_label in FORMAT_CHOICES:
            if fid_label == label:
                return fid
        return "markdown"

    def _export_selected(self):
        conv = self.selected_conv
        if not conv:
            return
        if not conv.messages:
            messagebox.showwarning(
                "无法导出",
                f"“{conv.title or '这条对话'}”没有读到任何消息，请先刷新来源确认预览正常。",
            )
            return
        exporter = get_exporter(self._selected_format_id())
        initial_dir = self.settings.get("last_export_dir", "") or None
        path = filedialog.asksaveasfilename(
            defaultextension=exporter.extension,
            filetypes=[(exporter.label, f"*{exporter.extension}"), ("所有文件", "*.*")],
            initialfile=exporter.suggested_filename(conv),
            initialdir=initial_dir,
            title=f"导出当前对话（{exporter.label}）",
        )
        if not path:
            return
        try:
            exporter.export(conv, path)
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            self._set_status(f"导出失败：{exc}", tone="danger")
            return
        self._set_status(f"已导出：{os.path.basename(path)}", progress=100, tone="success")
        self._after_export(path)

    def _after_export(self, path):
        if not path:
            return
        folder = os.path.dirname(os.path.abspath(path))
        self.settings.set("last_export_dir", folder)
        if self.settings.get("open_after_export", True):
            try:
                os.startfile(folder)
            except OSError:
                pass

    def _batch_export_full_conversations(self, conversations, output_dir, adapter):
        exporter = get_exporter(self._selected_format_id())
        total = len(conversations)
        exported = 0
        failures = []
        for index, conv in enumerate(conversations, start=1):
            full = conv
            if adapter and not conv.messages:
                try:
                    loaded = adapter.get_conversation(conv.id)
                except Exception as exc:
                    failures.append((conv.title or str(conv.id), f"读取失败：{exc}"))
                    continue
                if loaded is None:
                    failures.append((conv.title or str(conv.id), "读取失败：来源返回空"))
                    continue
                full = loaded
            if not full.messages:
                failures.append((full.title or str(full.id), "没有可导出的消息"))
                continue
            path = unique_path(os.path.join(output_dir, exporter.suggested_filename(full)))
            try:
                exporter.export(full, path)
            except Exception as exc:
                failures.append((full.title or str(full.id), f"写入失败：{exc}"))
                continue
            exported += 1
            self._post_ui(
                self._set_status,
                f"正在导出 {index}/{total} · {os.path.basename(path)}",
                int(index / total * 100),
                "info",
            )
        self.settings.set("last_export_dir", output_dir)
        return exported, failures

    # ========== 深浅主题 ==========

    def _toggle_theme(self):
        self._set_theme("dark" if self.theme_name == "light" else "light")

    def _set_theme(self, name: str):
        previous = self.theme_name
        if name == previous:
            return
        self.theme_name = apply_theme(name)
        mapping = theme_color_map(previous, self.theme_name)

        configure_styles(self.root)
        self._configure_wide_scrollbar_style()
        retheme_widgets(self.root, mapping)
        for dialog in (self._key_dialog,):
            if dialog and dialog.winfo_exists():
                retheme_widgets(dialog, mapping)

        # Text/Treeview 的 tag 颜色不是控件选项，换肤后要重新配置。
        self._setup_text_tags()
        self.preview_text.tag_configure("search_hit", background=Palette.WARNING_SOFT)
        self.preview_text.tag_configure("search_current", background=Palette.WARNING, foreground=Palette.TEXT)
        self.conv_tree.tag_configure("even", background=Palette.SURFACE)
        self.conv_tree.tag_configure("odd", background=Palette.SURFACE_ALT)
        self.conv_tree.tag_configure("empty", foreground=Palette.TEXT_MUTED)
        self.conv_tree.tag_configure("error", foreground=Palette.DANGER)
        self.conv_tree.tag_configure("loading", foreground=Palette.ACCENT)

        self.theme_button.configure(text="☀️ 浅色" if self.theme_name == "dark" else "🌙 深色")
        self.settings.set("theme", self.theme_name)
        self._set_status("已切换到深色主题" if self.theme_name == "dark" else "已切换到浅色主题", tone="info")

    def _on_tree_double_click(self, event):
        """双击导出，但只导出双击的那一条。

        预览是异步加载的，selected_conv 要等 worker 回来才更新。旧实现直接
        调 _export_selected，双击一条还在加载的对话会把上一条导出去。
        """
        item = self.conv_tree.identify_row(event.y)
        if not item or item.startswith("__"):
            return
        conv = self._tree_conv_map.get(item)
        if conv is None:
            return
        if self.selected_conv is not conv:
            self._set_status("这条对话还在加载，加载完成后再导出。", tone="info")
            return
        self._export_selected()

    @staticmethod
    def _walk_widgets(widget):
        result = []
        for child in widget.winfo_children():
            result.append(child)
            result.extend(ChatExporterGUI._walk_widgets(child))
        return result

    # ========== 自然高度状态栏，避免覆盖内容 ==========

    def _build_status_bar(self, parent):
        bar = ttk.Frame(parent, style="Surface.TFrame")
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)

        tk.Frame(bar, bg=Palette.BORDER, height=1).grid(
            row=0, column=0, columnspan=4, sticky="ew"
        )
        self.status_dot = tk.Frame(bar, width=8, height=8, bg=Palette.INFO)
        self.status_dot.grid(row=1, column=0, padx=(Metrics.PAD_X, 9), pady=(11, 10), sticky="w")
        self.status_dot.grid_propagate(False)
        self.status_var = tk.StringVar(value="正在初始化本地工作区…")
        ttk.Label(bar, textvariable=self.status_var, style="StatusBar.TLabel").grid(
            row=1, column=1, sticky="w", pady=(8, 8)
        )
        self.progress = ttk.Progressbar(
            bar,
            mode="determinate",
            length=180,
            style="Brand.Horizontal.TProgressbar",
        )
        self.progress.grid(row=1, column=2, padx=(12, 10), pady=(10, 8), sticky="e")
        ttk.Label(bar, text="本地 · 私密", style="StatusBar.TLabel").grid(
            row=1, column=3, padx=(0, Metrics.PAD_X), pady=(8, 8), sticky="e"
        )

    # ========== 密钥助手：可滚动正文 + 固定底部按钮 ==========

    def _open_key_assistant(self):
        adapter = self.current_adapter
        if not adapter or getattr(adapter, "name", "") != "trae":
            messagebox.showwarning("TRAE 密钥助手", "请先在左侧选择 TRAE SOLO。")
            return
        if self._key_dialog and self._key_dialog.winfo_exists():
            self._key_dialog.lift()
            return

        dialog = tk.Toplevel(self.root)
        self._key_dialog = dialog
        dialog.title("TRAE 密钥助手")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        dialog.configure(bg=Palette.WINDOW)
        dialog.protocol("WM_DELETE_WINDOW", self._close_key_dialog)

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        width = min(840, max(680, screen_w - 120))
        height = min(700, max(560, screen_h - 140))
        dialog.minsize(min(680, width), min(540, height))
        place_centered(dialog, self.root, width, height)

        dialog.grid_rowconfigure(1, weight=1)
        dialog.grid_columnconfigure(0, weight=1)

        hero = tk.Frame(dialog, bg=Palette.SIDEBAR, padx=26, pady=20)
        hero.grid(row=0, column=0, sticky="ew")
        tk.Label(
            hero,
            text="TRAE 密钥助手",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 9, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            hero,
            text="解锁完整的本地 TRAE 对话库",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK,
            font=(FONT_UI, 17, "bold"),
        ).pack(anchor=tk.W, pady=(5, 3))
        tk.Label(
            hero,
            text="仅在你点击开始后读取本机 TRAE 进程内存；数据不会离开当前电脑。",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 9),
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        scroll_area = tk.Frame(dialog, bg=Palette.WINDOW)
        scroll_area.grid(row=1, column=0, sticky="nsew")
        scroll_area.grid_rowconfigure(0, weight=1)
        scroll_area.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(scroll_area, bg=Palette.WINDOW, highlightthickness=0, bd=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        body_scroll = tk.Scrollbar(
            scroll_area,
            orient=tk.VERTICAL,
            command=canvas.yview,
            width=18,
            bg=Palette.SCROLLBAR,
            troughcolor=Palette.SCROLLBAR_TROUGH,
            activebackground=Palette.SCROLLBAR_ACTIVE,
            relief=tk.FLAT,
            bd=0,
        )
        body_scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=body_scroll.set)

        body = tk.Frame(canvas, bg=Palette.WINDOW, padx=24, pady=18)
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")

        def sync_scroll(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(body_window, width=canvas.winfo_width())

        body.bind("<Configure>", sync_scroll)
        canvas.bind("<Configure>", sync_scroll)

        checklist = tk.Frame(
            body,
            bg=Palette.SURFACE,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            padx=16,
            pady=13,
        )
        checklist.pack(fill=tk.X)
        tk.Label(
            checklist,
            text="开始前请确认",
            bg=Palette.SURFACE,
            fg=Palette.TEXT_MUTED,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor=tk.W)
        for index, text in enumerate(
            (
                "TRAE SOLO CN 已经启动",
                "至少打开过一个对话窗口",
                "扫描限制为 TRAE 私有内存，最多 8 秒 / 300MB",
            ),
            start=1,
        ):
            line = tk.Frame(checklist, bg=Palette.SURFACE)
            line.pack(fill=tk.X, pady=(8 if index == 1 else 5, 0))
            tk.Label(
                line,
                text=str(index),
                width=2,
                bg=Palette.ACCENT_SOFT,
                fg=Palette.ACCENT_PRESSED,
                font=(FONT_LATIN, 8, "bold"),
                padx=3,
                pady=2,
            ).pack(side=tk.LEFT)
            tk.Label(
                line,
                text=text,
                bg=Palette.SURFACE,
                fg=Palette.TEXT_SECONDARY,
                font=(FONT_UI, 9),
            ).pack(side=tk.LEFT, padx=(9, 0))

        progress_card = tk.Frame(
            body,
            bg=Palette.SURFACE,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            padx=16,
            pady=13,
        )
        progress_card.pack(fill=tk.X, pady=(13, 0))
        self._key_dialog_widgets["stage"] = tk.Label(
            progress_card,
            text="就绪",
            bg=Palette.SURFACE,
            fg=Palette.ACCENT,
            font=(FONT_UI, 8, "bold"),
        )
        self._key_dialog_widgets["stage"].pack(anchor=tk.W)
        self._key_dialog_widgets["status"] = tk.Label(
            progress_card,
            text="TRAE 准备好后即可开始。",
            bg=Palette.SURFACE,
            fg=Palette.TEXT,
            font=(FONT_UI, 11, "bold"),
            wraplength=680,
            justify=tk.LEFT,
        )
        self._key_dialog_widgets["status"].pack(anchor=tk.W, pady=(5, 3))
        self._key_dialog_widgets["detail"] = tk.Label(
            progress_card,
            text="助手会先检查环境变量和安全缓存，仅在需要时扫描进程内存。",
            bg=Palette.SURFACE,
            fg=Palette.TEXT_MUTED,
            font=(FONT_UI, 9),
            wraplength=680,
            justify=tk.LEFT,
        )
        self._key_dialog_widgets["detail"].pack(anchor=tk.W)
        key_progress = ttk.Progressbar(
            progress_card,
            mode="determinate",
            style="Brand.Horizontal.TProgressbar",
        )
        key_progress.pack(fill=tk.X, pady=(12, 0))
        self._key_dialog_widgets["progress"] = key_progress

        key_frame = tk.Frame(body, bg=Palette.WINDOW)
        self._key_dialog_widgets["key_frame"] = key_frame
        self._key_dialog_widgets["key_var"] = tk.StringVar(value="")

        buttons = tk.Frame(dialog, bg=Palette.SURFACE, padx=24, pady=14)
        buttons.grid(row=2, column=0, sticky="ew")
        self._key_dialog_widgets["start"] = ttk.Button(
            buttons,
            text="开始安全扫描",
            style="Primary.TButton",
            command=self._start_key_scan,
        )
        self._key_dialog_widgets["start"].pack(side=tk.LEFT)
        self._key_dialog_widgets["cancel"] = ttk.Button(
            buttons,
            text="关闭",
            style="Secondary.TButton",
            command=self._close_key_dialog,
        )
        self._key_dialog_widgets["cancel"].pack(side=tk.RIGHT)

        dialog.after_idle(sync_scroll)


def run():
    app = ChatExporterGUI()
    app.run()

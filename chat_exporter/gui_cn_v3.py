from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .adapters.qclaw_compat import QClawAdapter as QClawCompatAdapter
from .adapters.workbuddy_compat import WorkBuddyAdapter as WorkBuddyCompatAdapter
from .gui_cn_v2 import ChatExporterGUI as BaseChineseGUI
from .ui_theme import FONT_LATIN, FONT_UI, Metrics, Palette, place_centered


class ChatExporterGUI(BaseChineseGUI):
    """v1.1.3：面向高 DPI 真机的自适应中文工作台。"""

    SIDEBAR_WIDTH = 304

    def __init__(self):
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

    # ========== 自适应外壳：禁止固定高度裁切 ==========

    def _build_shell(self):
        self._configure_wide_scrollbar_style()

        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, minsize=self.SIDEBAR_WIDTH, weight=0)
        self.root.grid_columnconfigure(1, weight=1)

        sidebar = ttk.Frame(self.root, style="Sidebar.TFrame", width=self.SIDEBAR_WIDTH)
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)

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
            background="#98A2B3",
            troughcolor="#E4E7EC",
            bordercolor="#D0D5DD",
            lightcolor="#98A2B3",
            darkcolor="#98A2B3",
        )
        self.style.map(
            "Wide.Vertical.TScrollbar",
            background=[("active", "#667085"), ("pressed", "#475467")],
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

        right_actions = ttk.Frame(action_row, style="Surface.TFrame")
        right_actions.grid(row=0, column=1, sticky="e")
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

        preferred = self._preferred_library_width()
        container.grid_columnconfigure(0, minsize=preferred, weight=2)
        container.grid_columnconfigure(1, minsize=14, weight=0)
        container.grid_columnconfigure(2, minsize=520, weight=3)

        library = ttk.Frame(container, style="Card.TFrame")
        library.grid(row=0, column=0, sticky="nsew")
        preview = ttk.Frame(container, style="Card.TFrame")
        preview.grid(row=0, column=2, sticky="nsew")

        self._build_library_card(library)
        self._build_preview_card(preview)

    # ========== 宽对话列表 ==========

    def _build_library_card(self, parent):
        parent.grid_rowconfigure(4, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self._apply_card_border(parent)

        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(14, 8))
        header.grid_columnconfigure(0, weight=1)
        ttk.Label(header, text="对话列表", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.library_count_var = tk.StringVar(value="0 条")
        ttk.Label(header, textvariable=self.library_count_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e", padx=(8, 10)
        )
        self.sort_var = tk.StringVar(value="最近更新")
        sort_box = ttk.Combobox(
            header,
            textvariable=self.sort_var,
            values=("最近更新", "消息最多", "标题排序"),
            state="readonly",
            width=10,
            font=(FONT_UI, 9),
        )
        sort_box.grid(row=0, column=2, sticky="e")
        sort_box.bind("<<ComboboxSelected>>", lambda _e: self._filter_conversations())

        mode_row = ttk.Frame(parent, style="Surface.TFrame")
        mode_row.grid(row=1, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(0, 6))
        ttk.Label(mode_row, text="检索范围", style="Muted.TLabel").pack(side=tk.LEFT)
        self.search_mode_var = tk.StringVar(value=self.SEARCH_MODE_TITLE)
        mode_box = ttk.Combobox(
            mode_row,
            textvariable=self.search_mode_var,
            values=(self.SEARCH_MODE_TITLE, self.SEARCH_MODE_CONTENT),
            state="readonly",
            width=11,
            font=(FONT_UI, 9),
        )
        mode_box.pack(side=tk.LEFT, padx=(8, 0))
        mode_box.bind("<<ComboboxSelected>>", self._on_search_mode_changed)

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
        tk.Label(
            search_wrap,
            text="搜索",
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT_DISABLED,
            font=(FONT_UI, 8, "bold"),
        ).pack(side=tk.LEFT, padx=(1, 9))
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
        ttk.Label(parent, textvariable=self.search_hint_var, style="Muted.TLabel", wraplength=620).grid(
            row=3, column=0, sticky="w", padx=Metrics.CARD_PAD, pady=(0, 8)
        )

        tree_wrap = ttk.Frame(parent, style="Surface.TFrame")
        tree_wrap.grid(row=4, column=0, sticky="nsew", padx=(1, 1))
        tree_wrap.grid_rowconfigure(0, weight=1)
        tree_wrap.grid_columnconfigure(0, weight=1)

        self.conv_tree = ttk.Treeview(
            tree_wrap,
            columns=("title", "date", "messages"),
            show="headings",
            selectmode="browse",
            style="Modern.Treeview",
        )
        self.conv_tree.heading("title", text="标题")
        self.conv_tree.heading("date", text="更新时间")
        self.conv_tree.heading("messages", text="消息")
        self.conv_tree.column("title", width=410, minwidth=280)
        self.conv_tree.column("date", width=170, minwidth=145, stretch=False, anchor=tk.W)
        self.conv_tree.column("messages", width=70, minwidth=64, stretch=False, anchor=tk.CENTER)
        self.conv_tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(
            tree_wrap,
            orient=tk.VERTICAL,
            command=self.conv_tree.yview,
            style="Wide.Vertical.TScrollbar",
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.conv_tree.configure(yscrollcommand=scroll.set)
        self.conv_tree.bind("<<TreeviewSelect>>", self._on_conv_select)
        self.conv_tree.bind("<Double-1>", lambda _e: self._export_selected())
        self.conv_tree.tag_configure("even", background=Palette.SURFACE)
        self.conv_tree.tag_configure("odd", background=Palette.SURFACE_ALT)
        self.conv_tree.tag_configure("empty", foreground=Palette.TEXT_MUTED)
        self.conv_tree.tag_configure("error", foreground=Palette.DANGER)
        self.conv_tree.tag_configure("loading", foreground=Palette.ACCENT)

        footer = ttk.Frame(parent, style="Surface.TFrame")
        footer.grid(row=5, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(8, 12))
        self.library_footer_var = tk.StringVar(value="请先选择一个数据来源")
        ttk.Label(footer, textvariable=self.library_footer_var, style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Label(footer, text="Ctrl+F 搜索", style="Muted.TLabel").pack(side=tk.RIGHT)

    # ========== 厚实滚动条与完整可见区域 ==========

    def _build_preview_card(self, parent):
        super()._build_preview_card(parent)
        self.preview_text.configure(yscrollincrement=28)

        # v1.1.2 的预览使用 classic Tk scrollbar；显式设置为不透明、易拖动。
        for widget in self._walk_widgets(parent):
            if isinstance(widget, tk.Scrollbar):
                widget.configure(
                    width=22,
                    bg="#98A2B3",
                    troughcolor="#E4E7EC",
                    activebackground="#667085",
                    highlightthickness=0,
                    relief=tk.FLAT,
                    bd=0,
                )

        def wheel(event):
            delta = -1 if event.delta > 0 else 1
            self.preview_text.yview_scroll(delta * 3, "units")
            return "break"

        self.preview_text.bind("<MouseWheel>", wheel)

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
            bg="#98A2B3",
            troughcolor="#E4E7EC",
            activebackground="#667085",
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

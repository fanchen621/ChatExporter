"""Customer-facing ChatExporter application shell.

This class deliberately keeps adapters and export formats compatible while
replacing the fragile UI orchestration with bounded preview pages, a shared LRU
repository, and named cancellable tasks.
"""
from __future__ import annotations

import os
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .conversation_repository import ConversationLoadError, ConversationRepository
from .exporters import FORMAT_CHOICES, batch_export, format_label, get_exporter
from .gui_cn_v3 import ChatExporterGUI as LegacyGUI
from .preview_runtime import PreviewPayload
from .preview_utils import PREVIEW_CLEAN, PREVIEW_FULL, conversation_search_text
from .search_index import conversation_stamp
from .task_runtime import TaskContext, UiTaskRunner
from .ui_theme import FONT_LATIN, FONT_UI, Metrics, Palette


class ChatExporterGUI(LegacyGUI):
    """Product shell: quiet visual hierarchy and bounded background work."""

    SIDEBAR_WIDTH = 232
    PREVIEW_PAGE_SIZE = 180
    UI_QUEUE_POLL_MS = 75

    def __init__(self):
        # Parsed conversations can be tens of megabytes; keep only the two most
        # recent full loads. Preview pages use adapter windows and do not enter
        # this cache.
        self._repository = ConversationRepository(max_items=2)
        self._tasks = None
        self._preview_payload: PreviewPayload | None = None
        self._preview_page_busy = False
        self._selected_stub = None
        self._message_count_generation = 0
        super().__init__()
        self._tasks = UiTaskRunner(self._post_ui, max_workers=3)
        self.root.title("ChatExporter")

    # ------------------------------------------------------------------ shell

    def _build_shell(self):
        self._configure_wide_scrollbar_style()
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, minsize=self.SIDEBAR_WIDTH, weight=0)
        self.root.grid_columnconfigure(1, weight=1)

        sidebar = ttk.Frame(self.root, style="Sidebar.TFrame", width=self.SIDEBAR_WIDTH)
        sidebar.grid(row=0, column=0, sticky="nsew")
        workspace = ttk.Frame(self.root, style="App.TFrame")
        workspace.grid(row=0, column=1, sticky="nsew")
        workspace.grid_rowconfigure(1, weight=1)
        workspace.grid_columnconfigure(0, weight=1)

        self._build_sidebar(sidebar)
        self._build_header(workspace)
        self._build_workspace(workspace)
        self._build_status_bar(workspace)

    def _enable_double_buffering(self):
        """Do not enable WS_EX_COMPOSITED on the customer shell.

        That flag repaints every descendant bottom-to-top.  On this widget-rich
        window it kept one CPU core busy even while idle; normal Tk painting is
        both smoother and dramatically cheaper here.
        """
        return None

    # ---------------------------------------------------------- message counts

    def _on_conversations_loaded(self, conversations, generation: int):
        """Show the library immediately, then hydrate exact counts in-place."""
        super()._on_conversations_loaded(conversations, generation)
        if generation != self._load_generation:
            return
        self._start_message_count_hydration(conversations, generation)

    def _start_message_count_hydration(self, conversations, load_generation: int):
        adapter = self.current_adapter
        counter = getattr(adapter, "get_message_count", None)
        if not callable(counter) or not self._tasks:
            return

        pending = [
            conv
            for conv in conversations
            if not bool((conv.metadata or {}).get("msg_count_known", True))
        ]
        if not pending:
            return

        self._message_count_generation += 1
        count_generation = self._message_count_generation
        self.library_footer_var.set(f"已加载列表 · 正在补齐 {len(pending)} 条消息数")

        def work(context: TaskContext):
            total = len(pending)
            for index, conv in enumerate(pending, start=1):
                context.check_cancelled()
                count = counter(conv, context=context)
                context.check_cancelled()
                self._post_ui(
                    self._apply_message_count,
                    adapter,
                    load_generation,
                    count_generation,
                    str(conv.id),
                    count,
                    index,
                    total,
                )
            flush = getattr(adapter, "flush_message_count_cache", None)
            if callable(flush):
                flush()
            return total

        self._tasks.submit(
            "message-counts",
            work,
            lambda total: self._finish_message_count_hydration(
                adapter,
                load_generation,
                count_generation,
                total,
            ),
            lambda exc: self._fail_message_count_hydration(
                adapter,
                load_generation,
                count_generation,
                exc,
            ),
        )

    def _apply_message_count(
        self,
        adapter,
        load_generation: int,
        count_generation: int,
        conversation_id: str,
        count: int,
        index: int,
        total: int,
    ):
        if (
            adapter is not self.current_adapter
            or load_generation != self._load_generation
            or count_generation != self._message_count_generation
        ):
            return

        for conv in self.current_conversations:
            if str(conv.id) == conversation_id:
                metadata = conv.metadata or {}
                metadata["msg_count"] = int(count)
                metadata["msg_count_known"] = True
                conv.metadata = metadata
                break

        for item_id, conv in list(self._tree_conv_map.items()):
            if str(conv.id) != conversation_id or not self.conv_tree.exists(item_id):
                continue
            values = list(self.conv_tree.item(item_id, "values"))
            if len(values) >= 3:
                values[2] = str(int(count))
                self.conv_tree.item(item_id, values=values)

        if index == total or index == 1 or index % 3 == 0:
            self.library_footer_var.set(f"正在统计消息数 {index}/{total}")

    def _finish_message_count_hydration(
        self,
        adapter,
        load_generation: int,
        count_generation: int,
        total: int,
    ):
        if (
            adapter is not self.current_adapter
            or load_generation != self._load_generation
            or count_generation != self._message_count_generation
        ):
            return
        self.library_footer_var.set(f"已从本机加载 · {total} 条消息数已更新")
        if self.sort_var and self.sort_var.get() == "消息最多":
            self._filter_conversations()

    def _fail_message_count_hydration(
        self,
        adapter,
        load_generation: int,
        count_generation: int,
        _error: BaseException,
    ):
        if (
            adapter is self.current_adapter
            and load_generation == self._load_generation
            and count_generation == self._message_count_generation
        ):
            self.library_footer_var.set("对话已加载 · 部分消息数暂未统计")

    def _build_sidebar(self, parent):
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        brand = tk.Frame(parent, bg=Palette.SIDEBAR, padx=18, pady=18)
        brand.grid(row=0, column=0, sticky="ew")
        logo = tk.Label(
            brand,
            text="CE",
            bg=Palette.ACCENT,
            fg=Palette.ON_ACCENT,
            font=(FONT_LATIN, 10, "bold"),
            padx=8,
            pady=7,
        )
        logo.pack(side=tk.LEFT)
        text = tk.Frame(brand, bg=Palette.SIDEBAR)
        text.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(
            text,
            text="ChatExporter",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK,
            font=(FONT_UI, 11, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            text,
            text="本地对话归档",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 8),
        ).pack(anchor=tk.W, pady=(2, 0))

        section = tk.Frame(parent, bg=Palette.SIDEBAR, padx=14, pady=0)
        section.grid(row=1, column=0, sticky="ew")
        tk.Label(
            section,
            text="数据来源",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor=tk.W, padx=6, pady=(5, 7))

        self.app_list_frame = ttk.Frame(parent, style="Sidebar.TFrame")
        self.app_list_frame.grid(row=2, column=0, sticky="nsew", padx=8)

        footer = tk.Frame(parent, bg=Palette.SIDEBAR, padx=14, pady=14)
        footer.grid(row=3, column=0, sticky="ew")
        self.sidebar_key_button = ttk.Button(
            footer,
            text="TRAE 密钥助手",
            style="AccentSoft.TButton",
            command=self._open_key_assistant,
        )
        self.sidebar_key_button.pack(fill=tk.X)
        self.sidebar_key_hint = tk.Label(
            footer,
            text="选择 TRAE 后可用",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 7),
        )
        self.sidebar_key_hint.pack(anchor=tk.W, pady=(5, 12))
        self.detect_button = tk.Button(
            footer,
            text="重新检测来源",
            command=self._detect_apps,
            bg=Palette.SIDEBAR_RAISED,
            fg=Palette.TEXT_ON_DARK_MUTED,
            activebackground=Palette.SIDEBAR_HOVER,
            activeforeground=Palette.TEXT_ON_DARK,
            relief=tk.FLAT,
            bd=0,
            font=(FONT_UI, 8),
            padx=10,
            pady=8,
            cursor="hand2",
        )
        self.detect_button.pack(fill=tk.X)
        tk.Label(
            footer,
            text="数据只在本机处理",
            bg=Palette.SIDEBAR,
            fg=Palette.SIDEBAR_TEXT_OFF,
            font=(FONT_UI, 7),
        ).pack(anchor=tk.CENTER, pady=(10, 0))

    def _build_header(self, parent):
        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        left = ttk.Frame(header, style="Surface.TFrame")
        left.grid(row=0, column=0, sticky="w", padx=22, pady=13)
        self.page_title_var = tk.StringVar(value="对话")
        self.page_subtitle_var = tk.StringVar(value="选择左侧来源开始")
        ttk.Label(left, textvariable=self.page_title_var, style="PageTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(left, textvariable=self.page_subtitle_var, style="PageSub.TLabel").pack(anchor=tk.W, pady=(2, 0))
        self.source_badge = tk.Label(left, text="", bg=Palette.SURFACE, fg=Palette.TEXT_MUTED)

        actions = ttk.Frame(header, style="Surface.TFrame")
        actions.grid(row=0, column=1, sticky="e", padx=22, pady=13)
        self.refresh_button = ttk.Button(
            actions,
            text="刷新",
            style="Ghost.TButton",
            command=self._reload_current_source,
        )
        self.refresh_button.pack(side=tk.LEFT, padx=(0, 6))
        self.theme_button = ttk.Button(
            actions,
            text="深色",
            style="Ghost.TButton",
            command=self._toggle_theme,
        )
        self.theme_button.pack(side=tk.LEFT, padx=(0, 12))
        self.key_button = ttk.Button(
            actions,
            text="TRAE 密钥",
            style="Ghost.TButton",
            command=self._open_key_assistant,
        )
        self.key_button.pack(side=tk.LEFT, padx=(0, 12))

        saved_format = str(self.settings.get("export_format", "html"))
        if saved_format not in {fid for fid, _label in FORMAT_CHOICES}:
            saved_format = "html"
        self.export_format_var = tk.StringVar(value=format_label(saved_format))
        format_box = ttk.Combobox(
            actions,
            textvariable=self.export_format_var,
            values=[label for _fid, label in FORMAT_CHOICES],
            state="readonly",
            width=16,
            font=(FONT_UI, 9),
        )
        format_box.pack(side=tk.LEFT, padx=(0, 8))
        format_box.bind("<<ComboboxSelected>>", self._on_export_format_changed)
        self.batch_button = ttk.Button(
            actions,
            text="批量导出",
            style="Secondary.TButton",
            command=self._export_all,
        )
        self.batch_button.pack(side=tk.LEFT, padx=(0, 7))
        self.export_button = ttk.Button(
            actions,
            text="导出 HTML",
            style="Primary.TButton",
            command=self._export_selected,
        )
        self.export_button.pack(side=tk.LEFT)
        ttk.Separator(header, orient=tk.HORIZONTAL).grid(row=1, column=0, columnspan=2, sticky="ew")

    def _build_workspace(self, parent):
        container = ttk.Frame(parent, style="App.TFrame")
        container.grid(row=1, column=0, sticky="nsew", padx=14, pady=14)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        self.workspace_panes = ttk.PanedWindow(container, orient=tk.HORIZONTAL, style="Modern.TPanedwindow")
        self.workspace_panes.grid(row=0, column=0, sticky="nsew")
        library = ttk.Frame(self.workspace_panes, style="Card.TFrame")
        preview = ttk.Frame(self.workspace_panes, style="Card.TFrame")
        self.workspace_panes.add(library, weight=2)
        self.workspace_panes.add(preview, weight=5)
        self._build_library_card(library)
        self._build_preview_card(preview)
        self.root.after_idle(self._place_initial_sash)

    def _build_library_card(self, parent):
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self._apply_card_border(parent)

        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        header.grid_columnconfigure(1, weight=1)
        ttk.Label(header, text="对话", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.library_count_var = tk.StringVar(value="0")
        ttk.Label(header, textvariable=self.library_count_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(8, 8)
        )
        self.sort_var = tk.StringVar(value="最近更新")
        sort_box = ttk.Combobox(
            header,
            textvariable=self.sort_var,
            values=("最近更新", "消息最多", "标题排序"),
            state="readonly",
            width=8,
            font=(FONT_UI, 8),
        )
        sort_box.grid(row=0, column=2, sticky="e")
        sort_box.bind("<<ComboboxSelected>>", lambda _e: self._filter_conversations())

        controls = tk.Frame(parent, bg=Palette.SURFACE, padx=14)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        controls.grid_columnconfigure(0, weight=1)
        search = tk.Frame(
            controls,
            bg=Palette.SURFACE_ALT,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            padx=9,
            pady=1,
        )
        search.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 7))
        self.search_var = tk.StringVar(value="搜索对话…")
        self.search_entry = tk.Entry(
            search,
            textvariable=self.search_var,
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT_DISABLED,
            insertbackground=Palette.TEXT,
            relief=tk.FLAT,
            bd=0,
            font=(FONT_UI, 9),
        )
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.search_entry.bind("<FocusIn>", self._on_search_focus_in)
        self.search_entry.bind("<FocusOut>", self._on_search_focus_out)
        self.search_entry.bind("<Return>", lambda _e: self._filter_conversations())
        self.search_var.trace_add("write", lambda *_: self._schedule_filter())

        self.search_mode_var = tk.StringVar(value=self.SEARCH_MODE_TITLE)
        mode = ttk.Combobox(
            controls,
            textvariable=self.search_mode_var,
            values=(self.SEARCH_MODE_TITLE, self.SEARCH_MODE_CONTENT),
            state="readonly",
            width=8,
            font=(FONT_UI, 8),
        )
        mode.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        mode.bind("<<ComboboxSelected>>", self._on_search_mode_changed)
        self.date_filter_var = tk.StringVar(value=self.DATE_FILTER_ALL)
        date_box = ttk.Combobox(
            controls,
            textvariable=self.date_filter_var,
            values=tuple(label for label, _days in self.DATE_FILTERS),
            state="readonly",
            width=8,
            font=(FONT_UI, 8),
        )
        date_box.grid(row=1, column=1, sticky="ew", padx=(0, 6))
        date_box.bind("<<ComboboxSelected>>", lambda _e: self._filter_conversations())
        ttk.Button(controls, text="清除", style="Ghost.TButton", command=self._clear_search).grid(
            row=1, column=2, sticky="e"
        )
        self.search_hint_var = tk.StringVar(value="")

        tree_wrap = ttk.Frame(parent, style="Surface.TFrame")
        tree_wrap.grid(row=2, column=0, sticky="nsew", padx=1)
        tree_wrap.grid_rowconfigure(0, weight=1)
        tree_wrap.grid_columnconfigure(0, weight=1)
        self.conv_tree = ttk.Treeview(
            tree_wrap,
            columns=("title", "date", "messages"),
            show="headings",
            selectmode="extended",
            style="Modern.Treeview",
        )
        self.conv_tree.heading("title", text="标题")
        self.conv_tree.heading("date", text="更新")
        self.conv_tree.heading("messages", text="消息")
        self.conv_tree.column("title", width=270, minwidth=160)
        self.conv_tree.column("date", width=112, minwidth=96, stretch=False)
        self.conv_tree.column("messages", width=58, minwidth=52, stretch=False, anchor=tk.CENTER)
        self.conv_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.conv_tree.yview, style="Wide.Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        self.conv_tree.configure(yscrollcommand=scroll.set)
        self.conv_tree.bind("<Configure>", self._fit_tree_columns, add="+")
        self.conv_tree.bind("<<TreeviewSelect>>", self._on_conv_select, add="+")
        self.conv_tree.bind("<<TreeviewSelect>>", lambda _e: self._sync_action_states(), add="+")
        self.conv_tree.bind("<Double-1>", self._on_tree_double_click)
        for tag, color in (
            ("even", Palette.SURFACE),
            ("odd", Palette.SURFACE_ALT),
        ):
            self.conv_tree.tag_configure(tag, background=color)
        self.conv_tree.tag_configure("empty", foreground=Palette.TEXT_MUTED)
        self.conv_tree.tag_configure("error", foreground=Palette.DANGER)
        self.conv_tree.tag_configure("loading", foreground=Palette.ACCENT)

        footer = ttk.Frame(parent, style="Surface.TFrame")
        footer.grid(row=3, column=0, sticky="ew", padx=14, pady=(8, 10))
        footer.grid_columnconfigure(0, weight=1)
        self.library_footer_var = tk.StringVar(value="请选择数据来源")
        ttk.Label(footer, textvariable=self.library_footer_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(footer, text="Ctrl+F", style="Muted.TLabel").grid(row=0, column=1, sticky="e")

    def _build_preview_card(self, parent):
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self._apply_card_border(parent)

        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(13, 9))
        header.grid_columnconfigure(0, weight=1)
        title_wrap = ttk.Frame(header, style="Surface.TFrame")
        title_wrap.grid(row=0, column=0, sticky="w")
        self.preview_title_var = tk.StringVar(value="选择一条对话")
        self.preview_meta_var = tk.StringVar(value="超长对话按页加载；导出始终完整")
        ttk.Label(title_wrap, textvariable=self.preview_title_var, style="CardTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(title_wrap, textvariable=self.preview_meta_var, style="Muted.TLabel").pack(anchor=tk.W, pady=(3, 0))
        self.preview_source_badge = tk.Label(
            header,
            text="本地",
            bg=Palette.BADGE_BG,
            fg=Palette.BADGE_FG,
            font=(FONT_UI, 8, "bold"),
            padx=8,
            pady=3,
        )
        self.preview_source_badge.grid(row=0, column=1, sticky="e")

        toolbar = tk.Frame(parent, bg=Palette.SURFACE, padx=18)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 9))
        toolbar.grid_columnconfigure(0, weight=1)
        find_wrap = tk.Frame(
            toolbar,
            bg=Palette.SURFACE_ALT,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            padx=9,
            pady=1,
        )
        find_wrap.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.preview_find_var = tk.StringVar(value="")
        self.preview_find_entry = tk.Entry(
            find_wrap,
            textvariable=self.preview_find_var,
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT,
            insertbackground=Palette.TEXT,
            relief=tk.FLAT,
            bd=0,
            font=(FONT_UI, 9),
        )
        self.preview_find_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.preview_find_entry.bind("<Return>", lambda _e: self._goto_preview_hit(1))
        self.preview_find_var.trace_add("write", lambda *_: self._schedule_preview_find())
        self.preview_find_count_var = tk.StringVar(value="")
        tk.Label(
            find_wrap,
            textvariable=self.preview_find_count_var,
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT_MUTED,
            font=(FONT_UI, 8),
        ).pack(side=tk.RIGHT, padx=(8, 0))

        settings = getattr(self, "settings", None)
        self.clean_preview_var = tk.BooleanVar(
            value=bool(settings.get("clean_preview", False)) if settings is not None else False
        )
        tk.Checkbutton(
            toolbar,
            text="只看对话",
            variable=self.clean_preview_var,
            command=self._on_clean_preview_toggled,
            bg=Palette.SURFACE,
            fg=Palette.TEXT_SECONDARY,
            activebackground=Palette.SURFACE,
            activeforeground=Palette.TEXT,
            selectcolor=Palette.SURFACE,
            font=(FONT_UI, 8),
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Button(toolbar, text="复制本页", style="Ghost.TButton", command=self._copy_preview_text).grid(
            row=0, column=2, padx=(0, 12)
        )

        pager = ttk.Frame(toolbar, style="Surface.TFrame")
        pager.grid(row=0, column=3, sticky="e")
        self.preview_first_button = ttk.Button(pager, text="最早", width=4, style="Ghost.TButton", command=lambda: self._request_preview_page("earliest"))
        self.preview_older_button = ttk.Button(pager, text="‹", width=3, style="Ghost.TButton", command=lambda: self._request_preview_page("older"))
        self.preview_page_var = tk.StringVar(value="—")
        self.preview_newer_button = ttk.Button(pager, text="›", width=3, style="Ghost.TButton", command=lambda: self._request_preview_page("newer"))
        self.preview_latest_button = ttk.Button(pager, text="最新", width=4, style="Ghost.TButton", command=lambda: self._request_preview_page("latest"))
        self.preview_first_button.pack(side=tk.LEFT)
        self.preview_older_button.pack(side=tk.LEFT, padx=(2, 4))
        ttk.Label(pager, textvariable=self.preview_page_var, style="Muted.TLabel").pack(side=tk.LEFT, padx=4)
        self.preview_newer_button.pack(side=tk.LEFT, padx=(4, 2))
        self.preview_latest_button.pack(side=tk.LEFT)

        text_wrap = tk.Frame(parent, bg=Palette.SURFACE, bd=0)
        text_wrap.grid(row=2, column=0, sticky="nsew", padx=1, pady=(0, 1))
        text_wrap.grid_rowconfigure(0, weight=1)
        text_wrap.grid_columnconfigure(0, weight=1)
        self.preview_text = tk.Text(
            text_wrap,
            wrap=tk.WORD,
            font=(FONT_UI, 10),
            bg=Palette.SURFACE,
            fg=Palette.TEXT_SECONDARY,
            insertbackground=Palette.TEXT,
            selectbackground=Palette.ACCENT_SOFT,
            selectforeground=Palette.TEXT,
            padx=30,
            pady=24,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
            spacing1=2,
            spacing3=3,
        )
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL, command=self.preview_text.yview, style="Wide.Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        self.preview_text.configure(yscrollcommand=scroll.set)
        self._setup_text_tags()
        self.preview_text.tag_configure("search_hit", background=Palette.WARNING_SOFT)
        self.preview_text.tag_configure("search_current", background=Palette.WARNING, foreground=Palette.TEXT)
        self._show_preview_placeholder()
        self._sync_preview_pager()

    def _build_status_bar(self, parent):
        bar = ttk.Frame(parent, style="Surface.TFrame")
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_columnconfigure(1, weight=1)
        ttk.Separator(bar, orient=tk.HORIZONTAL).grid(row=0, column=0, columnspan=4, sticky="ew")
        self.status_dot = tk.Frame(bar, width=7, height=7, bg=Palette.INFO)
        self.status_dot.grid(row=1, column=0, padx=(18, 8), pady=8)
        self.status_dot.grid_propagate(False)
        self.status_var = tk.StringVar(value="正在初始化…")
        ttk.Label(bar, textvariable=self.status_var, style="StatusBar.TLabel").grid(row=1, column=1, sticky="w", pady=6)
        self.progress = ttk.Progressbar(bar, mode="determinate", length=130, style="Brand.Horizontal.TProgressbar")
        self.progress.grid(row=1, column=2, padx=(8, 10), pady=7)
        ttk.Label(bar, text="本地处理", style="StatusBar.TLabel").grid(row=1, column=3, padx=(0, 18), pady=6)

    # ------------------------------------------------------------ preview flow

    def _on_conv_select(self, _event):
        selection = self.conv_tree.selection()
        if not selection:
            return
        item_id = selection[0]
        if item_id.startswith("__"):
            return
        stub = self._tree_conv_map.get(item_id)
        if stub is None:
            return
        self._preview_generation += 1
        self.preview_title_var.set(stub.title or "无标题对话")
        self.preview_meta_var.set("正在读取本机记录…")
        self._preview_payload = None
        self._selected_stub = stub
        self.selected_conv = stub
        self._set_status("正在加载对话…", tone="info")
        self._set_preview_busy(True)
        adapter = self.current_adapter
        mode = PREVIEW_CLEAN if self._clean_preview_enabled() else PREVIEW_FULL

        def work(context: TaskContext):
            return self._repository.preview_payload(
                adapter,
                stub,
                mode=mode,
                page_size=self.PREVIEW_PAGE_SIZE,
                anchor="latest",
                context=context,
            )

        self._tasks.submit("preview", work, self._apply_preview_payload, self._on_preview_failed)

    def _request_preview_page(self, anchor: str):
        if self._preview_page_busy or self._selected_stub is None:
            return
        page = self._preview_payload.page if self._preview_payload else None
        cursor = None
        if page and anchor == "older":
            cursor = page.source_start
        elif page and anchor == "newer":
            cursor = page.source_end
        mode = PREVIEW_CLEAN if self._clean_preview_enabled() else PREVIEW_FULL
        self._set_preview_busy(True)
        self._set_status("正在切换预览页…", tone="info")
        stub = self._selected_stub
        adapter = self.current_adapter

        def work(context: TaskContext):
            return self._repository.preview_payload(
                adapter,
                stub,
                mode=mode,
                page_size=self.PREVIEW_PAGE_SIZE,
                anchor=anchor,
                cursor=cursor,
                context=context,
            )

        self._tasks.submit("preview", work, self._apply_preview_payload, self._on_preview_failed)

    def _apply_preview_payload(self, payload: PreviewPayload):
        self._set_preview_busy(False)
        self._preview_payload = payload
        self.selected_conv = self._selected_stub or payload.conversation
        page = payload.page
        self._preview_visible_count = page.visible_count
        self._preview_plain_text = payload.plain_text
        self.preview_title_var.set(payload.conversation.title or "无标题对话")
        self.preview_source_badge.configure(text=payload.conversation.source_app or "本地")
        updated = payload.conversation.updated_at.strftime("%Y-%m-%d %H:%M") if payload.conversation.updated_at else "未知时间"
        if page.entries:
            total = (
                f" · 原始记录 {page.total_source_messages:,} 条"
                if page.total_source_messages
                else " · 大型对话按需加载"
            )
            self.preview_meta_var.set(f"本页 {page.visible_count} 条{total} · 更新于 {updated}")
            segments = payload.segments
        else:
            self.preview_meta_var.set(f"原始记录 {page.total_source_messages:,} 条 · 当前页无可见正文")
            segments = (("\n\n当前页没有可显示的用户或 AI 正文。\n", "empty_body"),)
        self._preview_generation += 1
        self._start_preview_render(segments, payload.plain_text, self._preview_generation)
        self._sync_preview_pager()
        self._sync_action_states()

    def _on_preview_failed(self, exc: BaseException):
        self._set_preview_busy(False)
        message = str(exc)
        if isinstance(exc, ConversationLoadError):
            self.preview_meta_var.set(message)
        else:
            self.preview_meta_var.set("预览暂时不可用")
        self._show_preview_error(message)
        self._set_status(f"预览失败：{message}", tone="danger")

    def _set_preview_busy(self, busy: bool):
        self._preview_page_busy = busy
        if busy:
            self._set_busy_progress(True)
        else:
            self._set_busy_progress(False)
        self._sync_preview_pager()

    def _sync_preview_pager(self):
        widgets = (
            getattr(self, "preview_first_button", None),
            getattr(self, "preview_older_button", None),
            getattr(self, "preview_newer_button", None),
            getattr(self, "preview_latest_button", None),
        )
        if not all(widgets):
            return
        page = self._preview_payload.page if self._preview_payload else None
        disabled = self._preview_page_busy or page is None
        self.preview_first_button.configure(state=tk.DISABLED if disabled or not page.has_older else tk.NORMAL)
        self.preview_older_button.configure(state=tk.DISABLED if disabled or not page.has_older else tk.NORMAL)
        self.preview_newer_button.configure(state=tk.DISABLED if disabled or not page.has_newer else tk.NORMAL)
        self.preview_latest_button.configure(state=tk.DISABLED if disabled or not page.has_newer else tk.NORMAL)
        if not page or not page.entries:
            self.preview_page_var.set("—")
        elif page.label:
            self.preview_page_var.set(page.label)
        else:
            self.preview_page_var.set(f"{page.source_start + 1:,}–{page.source_end:,}")

    def _on_clean_preview_toggled(self):
        self.settings.set("clean_preview", self._clean_preview_enabled())
        if self.selected_conv is not None:
            self._request_preview_page("latest")

    def _copy_preview_text(self):
        if not self._preview_plain_text:
            self._set_status("当前页没有可复制的正文", tone="warning")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._preview_plain_text)
        self.root.update_idletasks()
        self._set_status("已复制当前预览页", tone="success")

    # --------------------------------------------------------------- search flow

    def _start_content_search(self, query: str):
        self._content_search_generation += 1
        generation = self._content_search_generation
        adapter = self.current_adapter
        conversations = list(self.current_conversations)
        if adapter is None:
            return
        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)
        self.conv_tree.insert("", tk.END, iid="__loading__", values=("正在搜索正文…", "", ""), tags=("loading",))
        self.library_footer_var.set("首次全文检索会建立本机索引")
        self._set_status("正在搜索对话正文…", progress=0, tone="info")
        persistent = self._persistent_search_index()

        def work(context: TaskContext):
            matches = []
            total = max(1, len(conversations))
            for index, conv in enumerate(conversations, start=1):
                context.check_cancelled()
                cache_key = (adapter.name, str(conv.id))
                stamp = conversation_stamp(conv)
                cached = self._content_index.get(cache_key)
                searchable = cached[1] if cached and cached[0] == stamp else None
                if searchable is None and persistent:
                    searchable = persistent.get(adapter.name, str(conv.id), stamp)
                    if searchable is not None:
                        self._content_index[cache_key] = (stamp, searchable)
                if searchable is None:
                    try:
                        full = self._repository.get(adapter, conv, context=context)
                    except ConversationLoadError:
                        full = conv
                    searchable = conversation_search_text(full)
                    self._content_index[cache_key] = (stamp, searchable)
                    if persistent:
                        persistent.put(adapter.name, str(conv.id), stamp, searchable)
                if query in searchable:
                    matches.append((index - 1, conv))
                if index == total or index % 3 == 0:
                    progress = int(index / total * 100)
                    self._post_ui(self._on_content_search_progress, generation, index, total, progress)
            return generation, query, matches

        self._tasks.submit(
            "search",
            work,
            lambda result: self._on_content_search_done(*result),
            self._on_content_search_failed,
        )

    def _on_content_search_failed(self, exc: BaseException):
        self.library_footer_var.set("全文检索未完成")
        self._set_status(f"全文检索失败：{exc}", tone="danger")

    def _invalidate_search_work(self):
        if self._tasks:
            self._tasks.cancel("search")
        self._content_search_generation += 1

    # --------------------------------------------------------------- task state

    def _sync_action_states(self):
        super()._sync_action_states()
        busy = self._export_running or self._preview_page_busy
        if hasattr(self, "export_button"):
            label = self._selected_format_short_label()
            can_export = self.selected_conv is not None and not self._export_running
            self.export_button.configure(
                text=f"正在生成 {label}…" if self._export_running else f"导出 {label}",
                state=tk.NORMAL if can_export else tk.DISABLED,
            )
        if hasattr(self, "batch_button"):
            picked = self._multi_selected_conversations()
            has_items = bool(self.current_conversations)
            self.batch_button.configure(
                text="处理中…" if self._export_running else (f"导出选中 {len(picked)} 条" if picked else "批量导出"),
                state=tk.NORMAL if has_items and not self._export_running else tk.DISABLED,
            )
        if hasattr(self, "refresh_button"):
            self.refresh_button.configure(
                state=tk.NORMAL if self.current_adapter is not None and not busy else tk.DISABLED
            )

    def _select_app(self, adapter):
        if adapter is self.current_adapter:
            return
        if self._tasks:
            self._tasks.cancel("preview")
            self._tasks.cancel("search")
            self._tasks.cancel("message-counts")
        self._message_count_generation += 1
        self._preview_payload = None
        self._selected_stub = None
        super()._select_app(adapter)

    def _reload_current_source(self):
        if self.current_adapter:
            self._repository.invalidate(getattr(self.current_adapter, "name", None))
        if self._tasks:
            self._tasks.cancel("preview")
            self._tasks.cancel("search")
            self._tasks.cancel("message-counts")
        self._message_count_generation += 1
        self._preview_payload = None
        self._selected_stub = None
        super()._reload_current_source()

    # -------------------------------------------------------------- export flow

    def _export_selected(self):
        if self._export_running or not self._selected_stub:
            return
        conv = self._selected_stub
        adapter = self.current_adapter
        exporter = get_exporter(self._selected_format_id())
        path = filedialog.asksaveasfilename(
            defaultextension=exporter.extension,
            filetypes=[(exporter.label, f"*{exporter.extension}"), ("所有文件", "*.*")],
            initialfile=exporter.suggested_filename(conv),
            initialdir=self.settings.get("last_export_dir", "") or None,
            title=f"导出 {self._selected_format_short_label()}",
        )
        if not path:
            return
        self._begin_export_activity(f"正在读取完整对话并生成 {self._selected_format_short_label()}…")

        def work(context: TaskContext):
            started = time.perf_counter()
            context.check_cancelled()
            full = self._repository.get(adapter, conv, context=context)
            exporter.export(full, path)
            context.check_cancelled()
            return path, os.path.getsize(path), time.perf_counter() - started

        self._tasks.submit(
            "export",
            work,
            lambda result: self._on_single_export_complete(*result),
            lambda exc: self._on_single_export_failed(
                conv.title or "这条对话",
                exporter.label,
                path,
                f"{type(exc).__name__}: {exc}",
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            ),
        )

    def _export_all(self):
        if self._export_running:
            return
        picked = self._multi_selected_conversations()
        conversations = picked or list(self.current_conversations)
        if not conversations:
            return
        output_dir = filedialog.askdirectory(
            title=(f"导出选中的 {len(picked)} 条对话" if picked else "选择批量导出目录"),
            initialdir=self.settings.get("last_export_dir", "") or None,
        )
        if not output_dir:
            return
        if not picked and not messagebox.askyesno(
            "批量导出",
            f"将 {len(conversations)} 条对话导出到：\n{output_dir}\n\n是否继续？",
        ):
            return
        adapter = self.current_adapter
        format_id = self._selected_format_id()
        self._begin_export_activity(
            f"正在导出 {'选中' if picked else '全部'} {len(conversations)} 条对话…"
        )

        def work(context: TaskContext):
            def loader(conv):
                return self._repository.get(adapter, conv, context=context)

            def progress(index: int, total: int, path: str | None):
                context.check_cancelled()
                name = os.path.basename(path) if path else "已跳过失败项"
                self._post_ui(
                    self._set_status,
                    f"正在导出 {index}/{total} · {name}",
                    int(index / max(1, total) * 100),
                    "info",
                )

            return batch_export(
                conversations,
                output_dir,
                format_id,
                progress_callback=progress,
                loader=loader,
            )

        self._tasks.submit(
            "export",
            work,
            lambda result: self._on_batch_export_complete(result[0], output_dir, result[1]),
            lambda exc: self._on_batch_export_failed(
                str(exc),
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                output_dir,
            ),
        )

    def _on_single_export_complete(self, path: str, size: int, elapsed: float):
        super()._on_single_export_complete(path, size, elapsed)

    def _on_close(self):
        if self._tasks:
            self._tasks.shutdown(wait=False)
        self._repository.invalidate()
        super()._on_close()


def run():
    app = ChatExporterGUI()
    app.run()

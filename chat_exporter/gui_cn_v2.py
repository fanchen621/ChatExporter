from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk
from typing import Dict, List, Optional, Tuple

from .gui_cn import ChatExporterGUI as BaseChineseGUI
from .models import AppInfo, Conversation, Role
from .preview_utils import conversation_search_text, message_preview_text, plain_preview_text, visible_messages
from .ui_theme import FONT_LATIN, FONT_UI, Metrics, Palette


class ChatExporterGUI(BaseChineseGUI):
    """中文优先的第二版工作台。

    重点解决高 DPI 下侧栏拥挤、TRAE 密钥入口不明显、预览信息过载，
    并加入按对话正文全文检索、当前对话内查找和一键复制。
    """

    SIDEBAR_WIDTH = 312
    LIBRARY_WIDTH = 540
    SEARCH_MODE_TITLE = "标题"
    SEARCH_MODE_CONTENT = "对话内容"

    def __init__(self):
        self.sidebar_key_button = None
        self.sidebar_key_hint = None
        self.search_mode_var = None
        self.sort_var = None
        self.search_hint_var = None
        self.preview_find_var = None
        self.preview_find_count_var = None
        self._content_index: Dict[Tuple[str, str], str] = {}
        self._content_search_generation = 0
        self._content_search_cancel: Optional[threading.Event] = None
        self._preview_plain_text = ""
        self._preview_find_hits: List[Tuple[str, str]] = []
        self._preview_find_cursor = -1
        self._preview_find_after_id = None
        super().__init__()
        self.root.title("ChatExporter · 本地对话归档工作台")
        self.root.geometry("1560x920")
        self.root.minsize(1280, 760)

    # ========== 稳定、宽松的整体布局 ==========

    def _build_shell(self):
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

    def _build_sidebar(self, parent):
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        brand = ttk.Frame(parent, style="Sidebar.TFrame")
        brand.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 16))

        logo = tk.Label(
            brand,
            text="CE",
            width=3,
            bg=Palette.ACCENT,
            fg="#FFFFFF",
            font=(FONT_LATIN, 12, "bold"),
            relief=tk.FLAT,
            bd=0,
            padx=7,
            pady=7,
        )
        logo.pack(side=tk.LEFT)

        brand_text = ttk.Frame(brand, style="Sidebar.TFrame")
        brand_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))
        ttk.Label(brand_text, text="ChatExporter", style="Brand.TLabel").pack(anchor=tk.W)
        ttk.Label(brand_text, text="本地对话归档", style="BrandSub.TLabel").pack(anchor=tk.W, pady=(2, 0))

        section = ttk.Frame(parent, style="Sidebar.TFrame")
        section.grid(row=1, column=0, sticky="ew", padx=16)
        ttk.Label(section, text="数据来源", style="SidebarSection.TLabel").pack(anchor=tk.W, padx=5, pady=(0, 8))

        self.app_list_frame = ttk.Frame(parent, style="Sidebar.TFrame")
        self.app_list_frame.grid(row=2, column=0, sticky="nsew", padx=12)

        quick = tk.Frame(parent, bg=Palette.SIDEBAR_RAISED, bd=0, padx=14, pady=13)
        quick.grid(row=3, column=0, sticky="ew", padx=16, pady=(10, 10))
        tk.Label(
            quick,
            text="TRAE 完整对话库",
            bg=Palette.SIDEBAR_RAISED,
            fg=Palette.TEXT_ON_DARK,
            font=(FONT_UI, 9, "bold"),
        ).pack(anchor=tk.W)
        self.sidebar_key_hint = tk.Label(
            quick,
            text="选择 TRAE 后可提取本机密钥",
            bg=Palette.SIDEBAR_RAISED,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 8),
        )
        self.sidebar_key_hint.pack(anchor=tk.W, pady=(3, 9))
        self.sidebar_key_button = tk.Button(
            quick,
            text="获取 TRAE 密钥",
            command=self._open_key_assistant,
            state=tk.DISABLED,
            bg=Palette.ACCENT,
            fg="#FFFFFF",
            disabledforeground="#A7A5D8",
            activebackground=Palette.ACCENT_HOVER,
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            bd=0,
            font=(FONT_UI, 9, "bold"),
            padx=12,
            pady=8,
            cursor="hand2",
        )
        self.sidebar_key_button.pack(fill=tk.X)

        footer = ttk.Frame(parent, style="Sidebar.TFrame")
        footer.grid(row=4, column=0, sticky="ew", padx=16, pady=(0, 16))
        tk.Label(
            footer,
            text="● 仅本地处理 · 不上传对话与密钥",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 8),
            anchor=tk.W,
        ).pack(fill=tk.X, pady=(0, 8))
        self.detect_button = tk.Button(
            footer,
            text="重新检测数据来源",
            command=self._detect_apps,
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            activebackground=Palette.SIDEBAR_HOVER,
            activeforeground=Palette.TEXT_ON_DARK,
            relief=tk.FLAT,
            bd=0,
            font=(FONT_UI, 9),
            padx=10,
            pady=8,
            cursor="hand2",
        )
        self.detect_button.pack(fill=tk.X)

    def _build_header(self, parent):
        header = ttk.Frame(parent, style="Surface.TFrame", height=126)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(0, weight=1)

        title_row = ttk.Frame(header, style="Surface.TFrame")
        title_row.grid(row=0, column=0, sticky="ew", padx=Metrics.PAD_X, pady=(14, 6))
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
        action_row.grid(row=1, column=0, sticky="ew", padx=Metrics.PAD_X, pady=(0, 12))
        action_row.grid_columnconfigure(0, weight=1)
        ttk.Label(
            action_row,
            text="预览只展示用户与 AI 正文；完整导出仍保留思考过程、工具调用与结果。",
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")

        action_group = ttk.Frame(action_row, style="Surface.TFrame")
        action_group.grid(row=0, column=1, sticky="e")
        self.key_button = ttk.Button(
            action_group,
            text="获取 TRAE 密钥",
            style="AccentSoft.TButton",
            command=self._open_key_assistant,
        )
        self.key_button.pack(side=tk.LEFT, padx=(0, 8))
        self.refresh_button = ttk.Button(
            action_group,
            text="刷新当前来源",
            style="Secondary.TButton",
            command=self._reload_current_source,
        )
        self.refresh_button.pack(side=tk.LEFT, padx=(0, 8))
        self.batch_button = ttk.Button(
            action_group,
            text="批量导出",
            style="Secondary.TButton",
            command=self._export_all,
        )
        self.batch_button.pack(side=tk.LEFT, padx=(0, 8))
        self.export_button = ttk.Button(
            action_group,
            text="导出当前对话",
            style="Primary.TButton",
            command=self._export_selected,
        )
        self.export_button.pack(side=tk.LEFT)

        separator = tk.Frame(parent, bg=Palette.BORDER, height=1)
        separator.grid(row=0, column=0, sticky="sew")

    def _build_workspace(self, parent):
        container = ttk.Frame(parent, style="App.TFrame")
        container.grid(row=1, column=0, sticky="nsew", padx=18, pady=(16, 12))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, minsize=self.LIBRARY_WIDTH, weight=0)
        container.grid_columnconfigure(1, minsize=18, weight=0)
        container.grid_columnconfigure(2, minsize=500, weight=1)

        library = ttk.Frame(container, style="Card.TFrame", width=self.LIBRARY_WIDTH)
        library.grid(row=0, column=0, sticky="nsew")
        library.grid_propagate(False)

        preview = ttk.Frame(container, style="Card.TFrame")
        preview.grid(row=0, column=2, sticky="nsew")

        self._build_library_card(library)
        self._build_preview_card(preview)

    # ========== 更宽的列表与两种搜索模式 ==========

    def _build_library_card(self, parent):
        parent.grid_rowconfigure(3, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self._apply_card_border(parent)

        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(16, 10))
        header.grid_columnconfigure(0, weight=1)
        ttk.Label(header, text="对话列表", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.library_count_var = tk.StringVar(value="0 条")
        ttk.Label(header, textvariable=self.library_count_var, style="Muted.TLabel").grid(row=0, column=1, sticky="e", padx=(8, 10))
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

        search_row = tk.Frame(parent, bg=Palette.SURFACE, bd=0)
        search_row.grid(row=1, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(0, 5))
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

        self.search_mode_var = tk.StringVar(value=self.SEARCH_MODE_TITLE)
        mode_box = ttk.Combobox(
            search_row,
            textvariable=self.search_mode_var,
            values=(self.SEARCH_MODE_TITLE, self.SEARCH_MODE_CONTENT),
            state="readonly",
            width=10,
            font=(FONT_UI, 9),
        )
        mode_box.grid(row=0, column=1, sticky="e", padx=(0, 8))
        mode_box.bind("<<ComboboxSelected>>", self._on_search_mode_changed)
        ttk.Button(search_row, text="清除", style="Ghost.TButton", command=self._clear_search).grid(row=0, column=2, sticky="e")

        self.search_hint_var = tk.StringVar(value="标题搜索会即时过滤；切换到“对话内容”可按正文关键词检索。")
        ttk.Label(parent, textvariable=self.search_hint_var, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", padx=Metrics.CARD_PAD, pady=(0, 10)
        )

        tree_wrap = ttk.Frame(parent, style="Surface.TFrame")
        tree_wrap.grid(row=3, column=0, sticky="nsew", padx=(1, 1))
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
        self.conv_tree.column("title", width=305, minwidth=230)
        self.conv_tree.column("date", width=145, minwidth=130, stretch=False, anchor=tk.W)
        self.conv_tree.column("messages", width=64, minwidth=58, stretch=False, anchor=tk.CENTER)
        self.conv_tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(
            tree_wrap,
            orient=tk.VERTICAL,
            command=self.conv_tree.yview,
            style="Modern.Vertical.TScrollbar",
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
        footer.grid(row=4, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(10, 14))
        self.library_footer_var = tk.StringVar(value="请先选择一个数据来源")
        ttk.Label(footer, textvariable=self.library_footer_var, style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Label(footer, text="Ctrl+F 搜索", style="Muted.TLabel").pack(side=tk.RIGHT)

    def _on_search_mode_changed(self, _event=None):
        mode = self.search_mode_var.get() if self.search_mode_var else self.SEARCH_MODE_TITLE
        self._search_placeholder_active = True
        if mode == self.SEARCH_MODE_CONTENT:
            self.search_var.set("搜索用户与 AI 对话内容…")
            self.search_hint_var.set("全文检索会按需读取对话正文并缓存在本机；工具调用与思考过程不会参与检索。")
        else:
            self.search_var.set("搜索标题…")
            self.search_hint_var.set("标题搜索会即时过滤；切换到“对话内容”可按正文关键词检索。")
        self.search_entry.configure(fg=Palette.TEXT_DISABLED)
        self._filter_conversations()

    def _on_search_focus_out(self, _event):
        if not self.search_var.get().strip():
            self._search_placeholder_active = True
            placeholder = "搜索用户与 AI 对话内容…" if self.search_mode_var.get() == self.SEARCH_MODE_CONTENT else "搜索标题…"
            self.search_var.set(placeholder)
            self.search_entry.configure(fg=Palette.TEXT_DISABLED)

    def _clear_search(self):
        self._search_placeholder_active = False
        self.search_var.set("")
        self.search_entry.focus_set()
        self._filter_conversations()

    def _schedule_filter(self):
        if self._filter_after_id:
            try:
                self.root.after_cancel(self._filter_after_id)
            except tk.TclError:
                pass
        delay = 480 if self.search_mode_var and self.search_mode_var.get() == self.SEARCH_MODE_CONTENT else self.SEARCH_DEBOUNCE_MS
        self._filter_after_id = self.root.after(delay, self._filter_conversations)

    def _search_query(self) -> str:
        return "" if self._search_placeholder_active else self.search_var.get().strip().casefold()

    def _sort_matches(self, matches):
        mode = self.sort_var.get() if self.sort_var else "最近更新"
        if mode == "消息最多":
            return sorted(
                matches,
                key=lambda pair: (pair[1].metadata.get("msg_count", len(pair[1].messages)) if pair[1].metadata else len(pair[1].messages)),
                reverse=True,
            )
        if mode == "标题排序":
            return sorted(matches, key=lambda pair: (pair[1].title or "").casefold())
        return sorted(matches, key=lambda pair: pair[1].updated_at or datetime.min, reverse=True)

    def _filter_conversations(self):
        self._filter_after_id = None
        query = self._search_query()
        mode = self.search_mode_var.get() if self.search_mode_var else self.SEARCH_MODE_TITLE

        if mode == self.SEARCH_MODE_CONTENT and query:
            if len(query) < 2:
                self.search_hint_var.set("全文检索至少输入 2 个字符，避免误触发大量本地读取。")
                self._render_matches(self._sort_matches(list(enumerate(self.current_conversations))), query=False)
                return
            self._start_content_search(query)
            return

        matches = []
        for index, conv in enumerate(self.current_conversations):
            if query and query not in (conv.title or "").casefold():
                continue
            matches.append((index, conv))
        self._render_matches(self._sort_matches(matches), query=bool(query))

    def _render_matches(self, matches, query: bool):
        self._tree_render_generation += 1
        generation = self._tree_render_generation
        self._tree_conv_map.clear()
        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)
        self.library_count_var.set(
            f"{len(matches)} / {len(self.current_conversations)}" if query else f"{len(self.current_conversations)} 条"
        )
        if not matches:
            text = "没有匹配的对话" if query else "当前来源没有可显示的对话"
            self.conv_tree.insert("", tk.END, iid="__empty__", values=(text, "", ""), tags=("empty",))
            return
        self._insert_tree_batch(matches, 0, generation)

    def _start_content_search(self, query: str):
        if self._content_search_cancel:
            self._content_search_cancel.set()
        cancel = threading.Event()
        self._content_search_cancel = cancel
        self._content_search_generation += 1
        generation = self._content_search_generation
        adapter = self.current_adapter
        conversations = list(self.current_conversations)
        if not adapter:
            return

        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)
        self.conv_tree.insert("", tk.END, iid="__loading__", values=("正在全文检索…", "", ""), tags=("loading",))
        self.library_footer_var.set("正在读取本机对话正文并建立临时索引…")
        self._set_status("正在按对话内容检索…", progress=0, tone="info")

        def worker():
            matches = []
            total = max(1, len(conversations))
            for index, conv in enumerate(conversations, start=1):
                if cancel.is_set():
                    return
                cache_key = (adapter.name, str(conv.id))
                searchable = self._content_index.get(cache_key)
                if searchable is None:
                    full = conv
                    if not conv.messages:
                        try:
                            loaded = adapter.get_conversation(conv.id)
                        except Exception:
                            loaded = None
                        if loaded:
                            full = loaded
                            conv.messages = loaded.messages
                            conv.metadata.update(loaded.metadata or {})
                    searchable = conversation_search_text(full)
                    self._content_index[cache_key] = searchable
                if query in searchable:
                    matches.append((index - 1, conv))
                if index == total or index % 4 == 0:
                    progress = int(index / total * 100)
                    self._post_ui(self._on_content_search_progress, generation, index, total, progress)
            self._post_ui(self._on_content_search_done, generation, query, matches)

        threading.Thread(target=worker, daemon=True, name="conversation-fulltext-search").start()

    def _on_content_search_progress(self, generation: int, current: int, total: int, progress: int):
        if generation != self._content_search_generation:
            return
        self.library_footer_var.set(f"全文检索中：{current}/{total}")
        self._set_status(f"正在全文检索 {current}/{total}…", progress=progress, tone="info")

    def _on_content_search_done(self, generation: int, query: str, matches):
        if generation != self._content_search_generation:
            return
        matches = self._sort_matches(matches)
        self._render_matches(matches, query=True)
        self.library_footer_var.set(f"正文关键词“{query}”命中 {len(matches)} 条对话")
        self.search_hint_var.set("全文检索只匹配用户与 AI 正文；思考过程和工具明细仅保留在完整导出中。")
        self._set_status(f"全文检索完成：命中 {len(matches)} 条对话", progress=0, tone="success")

    # ========== 清晰预览：只显示用户与 AI 正文 ==========

    def _build_preview_card(self, parent):
        parent.grid_rowconfigure(3, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self._apply_card_border(parent)

        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(16, 10))
        header.grid_columnconfigure(0, weight=1)
        title_wrap = ttk.Frame(header, style="Surface.TFrame")
        title_wrap.grid(row=0, column=0, sticky="w")
        self.preview_title_var = tk.StringVar(value="对话预览")
        self.preview_meta_var = tk.StringVar(value="选择一条对话后查看用户与 AI 正文")
        ttk.Label(title_wrap, textvariable=self.preview_title_var, style="CardTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(title_wrap, textvariable=self.preview_meta_var, style="Muted.TLabel").pack(anchor=tk.W, pady=(4, 0))
        self.preview_source_badge = tk.Label(
            header,
            text="本地",
            bg=Palette.SUCCESS_SOFT,
            fg=Palette.SUCCESS,
            font=(FONT_UI, 8, "bold"),
            padx=9,
            pady=4,
        )
        self.preview_source_badge.grid(row=0, column=1, sticky="e")

        note = tk.Frame(parent, bg=Palette.INFO_SOFT, bd=0, padx=14, pady=9)
        note.grid(row=1, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(0, 8))
        tk.Label(
            note,
            text="预览已精简：只展示用户和 AI 的完整可见正文。思考、工具调用和工具结果仍会保留在 Markdown 导出中。",
            bg=Palette.INFO_SOFT,
            fg=Palette.TEXT_SECONDARY,
            font=(FONT_UI, 9),
            anchor=tk.W,
            justify=tk.LEFT,
            wraplength=760,
        ).pack(fill=tk.X)

        toolbar = tk.Frame(parent, bg=Palette.SURFACE, bd=0)
        toolbar.grid(row=2, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(0, 8))
        toolbar.grid_columnconfigure(0, weight=1)
        find_wrap = tk.Frame(
            toolbar,
            bg=Palette.SURFACE_ALT,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            padx=9,
            pady=1,
        )
        find_wrap.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        tk.Label(find_wrap, text="对话内查找", bg=Palette.SURFACE_ALT, fg=Palette.TEXT_MUTED, font=(FONT_UI, 8, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        self.preview_find_var = tk.StringVar(value="")
        preview_find_entry = tk.Entry(
            find_wrap,
            textvariable=self.preview_find_var,
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT,
            insertbackground=Palette.TEXT,
            relief=tk.FLAT,
            bd=0,
            font=(FONT_UI, 9),
        )
        preview_find_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        preview_find_entry.bind("<Return>", lambda _e: self._goto_preview_hit(1))
        self.preview_find_var.trace_add("write", lambda *_: self._schedule_preview_find())
        self.preview_find_entry = preview_find_entry
        self.preview_find_count_var = tk.StringVar(value="")
        tk.Label(find_wrap, textvariable=self.preview_find_count_var, bg=Palette.SURFACE_ALT, fg=Palette.TEXT_MUTED, font=(FONT_UI, 8)).pack(side=tk.RIGHT, padx=(8, 0))

        ttk.Button(toolbar, text="上一处", style="Ghost.TButton", command=lambda: self._goto_preview_hit(-1)).grid(row=0, column=1, padx=(0, 4))
        ttk.Button(toolbar, text="下一处", style="Ghost.TButton", command=lambda: self._goto_preview_hit(1)).grid(row=0, column=2, padx=(0, 8))
        ttk.Button(toolbar, text="复制正文", style="Secondary.TButton", command=self._copy_preview_text).grid(row=0, column=3, padx=(0, 4))
        ttk.Button(toolbar, text="顶部", style="Ghost.TButton", command=lambda: self.preview_text.see("1.0")).grid(row=0, column=4, padx=(0, 4))
        ttk.Button(toolbar, text="底部", style="Ghost.TButton", command=lambda: self.preview_text.see(tk.END)).grid(row=0, column=5)

        text_wrap = tk.Frame(parent, bg=Palette.SURFACE, bd=0)
        text_wrap.grid(row=3, column=0, sticky="nsew", padx=(1, 1), pady=(0, 1))
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
            pady=22,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
            spacing1=2,
            spacing3=2,
        )
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        v_scroll = tk.Scrollbar(
            text_wrap,
            orient=tk.VERTICAL,
            command=self.preview_text.yview,
            width=15,
            bg=Palette.BORDER_STRONG,
            troughcolor=Palette.SURFACE_ALT,
            activebackground=Palette.TEXT_DISABLED,
            relief=tk.FLAT,
            bd=0,
        )
        v_scroll.grid(row=0, column=1, sticky="ns")
        self.preview_text.configure(yscrollcommand=v_scroll.set)
        self._setup_text_tags()
        self.preview_text.tag_configure("search_hit", background="#FEF0C7")
        self.preview_text.tag_configure("search_current", background="#FEC84B", foreground=Palette.TEXT)
        self._show_preview_placeholder()

    def _setup_text_tags(self):
        super()._setup_text_tags()
        t = self.preview_text
        t.tag_configure("user_header", font=(FONT_UI, 10, "bold"), foreground=Palette.ACCENT_PRESSED, spacing1=12, spacing3=5)
        t.tag_configure("user_body", foreground=Palette.TEXT, lmargin1=22, lmargin2=22, rmargin=22, spacing3=10)
        t.tag_configure("assistant_header", font=(FONT_UI, 10, "bold"), foreground=Palette.SUCCESS, spacing1=12, spacing3=5)
        t.tag_configure("assistant_body", foreground=Palette.TEXT, lmargin1=22, lmargin2=22, rmargin=22, spacing3=10)

    def _show_preview_placeholder(self):
        self._preview_plain_text = ""
        self._preview_find_hits = []
        self._preview_find_cursor = -1
        if self.preview_find_count_var:
            self.preview_find_count_var.set("")
        self.preview_text.configure(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert(tk.END, "\n\n\n")
        self.preview_text.insert(tk.END, "选择一条对话开始阅读\n", "empty_title")
        self.preview_text.insert(tk.END, "\n这里将只显示用户和 AI 的可见正文，阅读更干净。\n", "empty_body")
        self.preview_text.configure(state=tk.DISABLED)
        self.selected_conv = None
        self._sync_action_states()

    def _on_conv_select(self, _event):
        selection = self.conv_tree.selection()
        if not selection:
            return
        item_id = selection[0]
        if item_id.startswith("__"):
            return
        conv = self._tree_conv_map.get(item_id)
        if not conv:
            return

        self._preview_generation += 1
        generation = self._preview_generation
        self.preview_title_var.set(conv.title or "无标题对话")
        self.preview_meta_var.set("正在从本机存储读取用户与 AI 正文…")
        self._set_status("正在加载对话预览…", tone="info")
        adapter = self.current_adapter

        def worker():
            try:
                full = conv
                if adapter and not conv.messages:
                    loaded = adapter.get_conversation(conv.id)
                    if loaded:
                        full = loaded
                        conv.messages = loaded.messages
                        conv.metadata.update(loaded.metadata or {})
                self._post_ui(self._show_preview, "", full, generation)
            except Exception as exc:
                self._post_ui(self._set_status, f"预览失败：{exc}", -1, "danger")
                self._post_ui(self._show_preview_error, str(exc))

        threading.Thread(target=worker, daemon=True, name="preview-load").start()

    def _show_preview(self, _markdown: str, conv: Conversation, generation: int):
        if generation != self._preview_generation:
            return
        self.selected_conv = conv
        visible = visible_messages(conv)
        self._preview_visible_count = len(visible)
        self._preview_plain_text = plain_preview_text(conv)
        self.preview_title_var.set(conv.title or "无标题对话")
        updated = conv.updated_at.strftime("%Y-%m-%d %H:%M") if conv.updated_at else "未知时间"
        self.preview_meta_var.set(
            f"{conv.source_app} · 用户/AI 正文 {len(visible)} 条 · 更新于 {updated}"
        )

        # 全量渲染 + 后台分批插入：无论会话多大，尾部都不会被截断，界面也不会卡死。
        segments = []
        if not visible:
            segments.append((
                "\n\n这条记录没有可显示的用户/AI 正文\n\n"
                "工具记录、系统消息和思考过程仍会保留在完整导出中。\n",
                "empty_body",
            ))
        else:
            for message, eff_role, text in visible:
                role_name = "用户" if eff_role == Role.USER else "AI 助手"
                header_tag = "user_header" if eff_role == Role.USER else "assistant_header"
                body_tag = "user_body" if eff_role == Role.USER else "assistant_body"
                timestamp = message.timestamp.strftime("%Y-%m-%d %H:%M:%S") if message.timestamp else ""
                header = role_name
                if timestamp:
                    header += f"  ·  {timestamp}"
                if message.model and eff_role == Role.ASSISTANT:
                    header += f"  ·  {message.model}"
                segments.append((header + "\n", header_tag))
                # 用户/AI 正文：kind="text" 永远不截断，确保会话尾部完整可见。
                segments.append((self._preview_part(text or "", "text") + "\n\n", body_tag))
                segments.append(("────────────────────────────────────────\n\n", "separator"))

        self._start_preview_render(segments, self._preview_plain_text, generation)
        # 不在此处设置"完成"状态：_start_preview_render 是异步的（root.after 调度），
        # 实际渲染在 _insert_preview_batch 中分批进行，完成后由 _finish_preview_render
        # 设置最终状态和同步按钮。这里提前设置会导致状态栏闪"完成"再跳回"渲染中"。

    def _copy_preview_text(self):
        if not self._preview_plain_text:
            messagebox.showinfo("复制正文", "当前没有可复制的用户/AI 正文。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._preview_plain_text)
        self.root.update_idletasks()
        self._set_status("已复制当前对话的用户/AI 正文", tone="success")

    def _schedule_preview_find(self):
        if self._preview_find_after_id:
            try:
                self.root.after_cancel(self._preview_find_after_id)
            except tk.TclError:
                pass
        self._preview_find_after_id = self.root.after(180, self._refresh_preview_find)

    def _refresh_preview_find(self):
        self._preview_find_after_id = None
        if not hasattr(self, "preview_text"):
            return
        query = self.preview_find_var.get().strip() if self.preview_find_var else ""
        self.preview_text.tag_remove("search_hit", "1.0", tk.END)
        self.preview_text.tag_remove("search_current", "1.0", tk.END)
        self._preview_find_hits = []
        self._preview_find_cursor = -1
        if not query:
            if self.preview_find_count_var:
                self.preview_find_count_var.set("")
            return

        start = "1.0"
        while True:
            index = self.preview_text.search(query, start, stopindex=tk.END, nocase=True)
            if not index:
                break
            end = f"{index}+{len(query)}c"
            self._preview_find_hits.append((index, end))
            self.preview_text.tag_add("search_hit", index, end)
            start = end
        if self.preview_find_count_var:
            self.preview_find_count_var.set(f"{len(self._preview_find_hits)} 处")
        if self._preview_find_hits:
            self._preview_find_cursor = 0
            self._apply_current_preview_hit()

    def _goto_preview_hit(self, delta: int):
        if not self._preview_find_hits:
            self._refresh_preview_find()
        if not self._preview_find_hits:
            return
        self._preview_find_cursor = (self._preview_find_cursor + delta) % len(self._preview_find_hits)
        self._apply_current_preview_hit()

    def _apply_current_preview_hit(self):
        self.preview_text.tag_remove("search_current", "1.0", tk.END)
        if not self._preview_find_hits or self._preview_find_cursor < 0:
            return
        start, end = self._preview_find_hits[self._preview_find_cursor]
        self.preview_text.tag_add("search_current", start, end)
        self.preview_text.see(start)
        if self.preview_find_count_var:
            self.preview_find_count_var.set(f"{self._preview_find_cursor + 1}/{len(self._preview_find_hits)}")

    # ========== 状态联动 ==========

    def _sync_action_states(self):
        super()._sync_action_states()
        is_trae = bool(self.current_adapter and getattr(self.current_adapter, "name", "") == "trae")
        state = tk.NORMAL if is_trae and not self._key_extract_running else tk.DISABLED
        if self.sidebar_key_button:
            self.sidebar_key_button.configure(state=state)
        if self.sidebar_key_hint:
            self.sidebar_key_hint.configure(
                text="点击后从本机 TRAE 进程安全提取" if is_trae else "选择 TRAE 后可提取本机密钥"
            )

    def _add_nav_row(self, adapter, info: AppInfo, available: bool):
        name = adapter.name
        accent = self.APP_ACCENTS.get(name, Palette.ACCENT)
        row = tk.Frame(self.app_list_frame, bg=Palette.SIDEBAR, bd=0, padx=0, pady=2)
        row.pack(fill=tk.X, pady=3)

        bar = tk.Frame(row, bg=Palette.SIDEBAR, width=4)
        bar.pack(side=tk.LEFT, fill=tk.Y)
        bar.pack_propagate(False)
        body = tk.Frame(row, bg=Palette.SIDEBAR, cursor="hand2" if available else "arrow", padx=12, pady=12)
        body.pack(side=tk.LEFT, fill=tk.X, expand=True)

        avatar = tk.Label(
            body,
            text=self.APP_INITIALS.get(name, name[:2].upper()),
            width=3,
            bg=Palette.SIDEBAR_RAISED if available else Palette.SIDEBAR,
            fg=accent if available else "#64748B",
            font=(FONT_LATIN, 9, "bold"),
            padx=4,
            pady=6,
        )
        avatar.pack(side=tk.LEFT)
        title = tk.Label(
            body,
            text=self.SOURCE_NAMES.get(name, info.display_name),
            anchor=tk.W,
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK if available else "#64748B",
            font=(FONT_UI, 10, "bold"),
        )
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 8))
        meta = tk.Label(
            body,
            text="可用" if available else "未检测",
            bg=Palette.SIDEBAR,
            fg=Palette.SUCCESS if available else "#64748B",
            font=(FONT_UI, 8),
        )
        meta.pack(side=tk.RIGHT)
        status = tk.Frame(body, width=7, height=7, bg=Palette.SUCCESS if available else "#475569")
        status.pack(side=tk.RIGHT, padx=(0, 7))
        status.pack_propagate(False)

        widgets = (row, body, avatar, title, meta, status)
        if available:
            for widget in widgets:
                widget.bind("<Button-1>", lambda _e, a=adapter: self._select_app(a))
                widget.bind("<Enter>", lambda _e, n=name: self._set_nav_hover(n, True))
                widget.bind("<Leave>", lambda _e, n=name: self._set_nav_hover(n, False))

        self._nav_rows[name] = {
            "row": row,
            "bar": bar,
            "body": body,
            "avatar": avatar,
            "labels": body,
            "title": title,
            "meta": meta,
            "status": status,
            "accent": accent,
            "available": available,
        }

    def _select_app(self, adapter):
        if self._content_search_cancel:
            self._content_search_cancel.set()
        super()._select_app(adapter)
        if self.preview_find_var:
            self.preview_find_var.set("")
        self._sync_action_states()

    def _reload_current_source(self):
        if self.current_adapter:
            prefix = self.current_adapter.name
            self._content_index = {key: value for key, value in self._content_index.items() if key[0] != prefix}
        super()._reload_current_source()

    def _bind_shortcuts(self):
        super()._bind_shortcuts()
        self.root.bind_all("<Control-Shift-f>", lambda _e: self.preview_find_entry.focus_set())


def run():
    app = ChatExporterGUI()
    app.run()

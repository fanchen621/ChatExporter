from __future__ import annotations

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import List

from .gui_modern import ChatExporterGUI as ModernGUI
from .markdown_exporter import MarkdownExporter
from .models import AppInfo, Conversation, Role
from .ui_theme import FONT_LATIN, FONT_MONO, FONT_UI, Metrics, Palette, place_centered


class ChatExporterGUI(ModernGUI):
    """中文优先、固定布局的现代化本地对话工作台。"""

    SOURCE_NAMES = {
        "trae": "TRAE SOLO",
        "qoderwork": "QoderWork",
        "workbuddy": "WorkBuddy",
        "qclaw": "QClaw",
        "marvis": "腾讯 Marvis",
    }

    def __init__(self):
        super().__init__()
        self.root.title("ChatExporter · 本地对话归档工作台")

    # ========== 中文固定布局 ==========

    def _build_shell(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, minsize=264, weight=0)
        self.root.grid_columnconfigure(1, weight=1)

        sidebar = ttk.Frame(self.root, style="Sidebar.TFrame", width=264)
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
        brand.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 18))

        logo = tk.Label(
            brand,
            text="CE",
            width=3,
            bg=Palette.ACCENT,
            fg="#FFFFFF",
            font=(FONT_LATIN, 13, "bold"),
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=8,
        )
        logo.pack(side=tk.LEFT)

        brand_text = ttk.Frame(brand, style="Sidebar.TFrame")
        brand_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(11, 0))
        ttk.Label(brand_text, text="对话归档", style="Brand.TLabel").pack(anchor=tk.W)
        ttk.Label(brand_text, text="本地对话工作台", style="BrandSub.TLabel").pack(anchor=tk.W, pady=(2, 0))

        section = ttk.Frame(parent, style="Sidebar.TFrame")
        section.grid(row=1, column=0, sticky="ew", padx=14)
        ttk.Label(section, text="数据来源", style="SidebarSection.TLabel").pack(anchor=tk.W, padx=6, pady=(0, 8))

        self.app_list_frame = ttk.Frame(parent, style="Sidebar.TFrame")
        self.app_list_frame.grid(row=2, column=0, sticky="nsew", padx=10)

        footer = ttk.Frame(parent, style="Sidebar.TFrame")
        footer.grid(row=3, column=0, sticky="ew", padx=14, pady=(12, 16))

        privacy = tk.Frame(footer, bg=Palette.SIDEBAR_RAISED, bd=0, padx=12, pady=11)
        privacy.pack(fill=tk.X, pady=(0, 10))
        top = tk.Frame(privacy, bg=Palette.SIDEBAR_RAISED)
        top.pack(fill=tk.X)
        dot = tk.Frame(top, width=8, height=8, bg=Palette.SUCCESS)
        dot.pack(side=tk.LEFT, padx=(0, 8))
        dot.pack_propagate(False)
        tk.Label(
            top,
            text="仅本地处理",
            bg=Palette.SIDEBAR_RAISED,
            fg=Palette.TEXT_ON_DARK,
            font=(FONT_UI, 9, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            privacy,
            text="对话与密钥均留在本机\n不会上传到云端",
            justify=tk.LEFT,
            bg=Palette.SIDEBAR_RAISED,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 8),
        ).pack(anchor=tk.W, pady=(5, 0))

        self.detect_button = tk.Button(
            footer,
            text="重新检测来源",
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
        header = ttk.Frame(parent, style="Surface.TFrame", height=112)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
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

        actions = ttk.Frame(header, style="Surface.TFrame")
        actions.grid(row=1, column=0, sticky="ew", padx=Metrics.PAD_X, pady=(0, 12))
        actions.grid_columnconfigure(0, weight=1)

        action_group = ttk.Frame(actions, style="Surface.TFrame")
        action_group.grid(row=0, column=1, sticky="e")

        self.refresh_button = ttk.Button(
            action_group, text="刷新当前来源", style="Secondary.TButton", command=self._reload_current_source
        )
        self.refresh_button.pack(side=tk.LEFT, padx=(0, 8))
        self.key_button = ttk.Button(
            action_group, text="TRAE 密钥助手", style="AccentSoft.TButton", command=self._open_key_assistant
        )
        self.key_button.pack(side=tk.LEFT, padx=(0, 8))
        self.batch_button = ttk.Button(
            action_group, text="批量导出", style="Secondary.TButton", command=self._export_all
        )
        self.batch_button.pack(side=tk.LEFT, padx=(0, 8))
        self.export_button = ttk.Button(
            action_group, text="导出选中", style="Primary.TButton", command=self._export_selected
        )
        self.export_button.pack(side=tk.LEFT)

        separator = tk.Frame(parent, bg=Palette.BORDER, height=1)
        separator.grid(row=0, column=0, sticky="sew")

    def _build_workspace(self, parent):
        container = ttk.Frame(parent, style="App.TFrame")
        container.grid(row=1, column=0, sticky="nsew", padx=18, pady=(16, 12))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, minsize=440, weight=0)
        container.grid_columnconfigure(1, minsize=16, weight=0)
        container.grid_columnconfigure(2, weight=1)

        library = ttk.Frame(container, style="Card.TFrame", width=440)
        library.grid(row=0, column=0, sticky="nsew")
        library.grid_propagate(False)

        preview = ttk.Frame(container, style="Card.TFrame")
        preview.grid(row=0, column=2, sticky="nsew")

        self._build_library_card(library)
        self._build_preview_card(preview)

    def _build_library_card(self, parent):
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self._apply_card_border(parent)

        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(16, 10))
        header.grid_columnconfigure(0, weight=1)
        ttk.Label(header, text="对话列表", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.library_count_var = tk.StringVar(value="0 条")
        ttk.Label(header, textvariable=self.library_count_var, style="Muted.TLabel").grid(row=0, column=1, sticky="e")

        search_wrap = tk.Frame(
            parent,
            bg=Palette.SURFACE_ALT,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            bd=0,
            padx=10,
            pady=2,
        )
        search_wrap.grid(row=1, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(0, 12))
        tk.Label(
            search_wrap,
            text="搜索",
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT_DISABLED,
            font=(FONT_UI, 8, "bold"),
        ).pack(side=tk.LEFT, padx=(1, 9))

        self.search_var = tk.StringVar(value="按标题搜索对话…")
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
        self.search_var.trace_add("write", lambda *_: self._schedule_filter())

        tree_wrap = ttk.Frame(parent, style="Surface.TFrame")
        tree_wrap.grid(row=2, column=0, sticky="nsew", padx=(1, 1))
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
        self.conv_tree.column("title", width=245, minwidth=170)
        self.conv_tree.column("date", width=130, minwidth=115, stretch=False, anchor=tk.W)
        self.conv_tree.column("messages", width=56, minwidth=50, stretch=False, anchor=tk.CENTER)
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
        footer.grid(row=3, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(10, 14))
        self.library_footer_var = tk.StringVar(value="请先选择一个数据来源")
        ttk.Label(footer, textvariable=self.library_footer_var, style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Label(footer, text="Ctrl+F 搜索", style="Muted.TLabel").pack(side=tk.RIGHT)

    def _build_preview_card(self, parent):
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self._apply_card_border(parent)

        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(16, 12))
        header.grid_columnconfigure(0, weight=1)

        title_wrap = ttk.Frame(header, style="Surface.TFrame")
        title_wrap.grid(row=0, column=0, sticky="w")
        self.preview_title_var = tk.StringVar(value="对话预览")
        self.preview_meta_var = tk.StringVar(value="选择左侧对话后查看完整内容")
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

        text_wrap = tk.Frame(parent, bg=Palette.SURFACE, bd=0)
        text_wrap.grid(row=1, column=0, sticky="nsew", padx=(1, 1), pady=(0, 1))
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
            padx=28,
            pady=22,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
            spacing1=2,
            spacing3=2,
        )
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        v_scroll = ttk.Scrollbar(
            text_wrap,
            orient=tk.VERTICAL,
            command=self.preview_text.yview,
            style="Modern.Vertical.TScrollbar",
        )
        v_scroll.grid(row=0, column=1, sticky="ns")
        self.preview_text.configure(yscrollcommand=v_scroll.set)
        self._setup_text_tags()
        self._show_preview_placeholder()

    def _build_status_bar(self, parent):
        bar = ttk.Frame(parent, style="Surface.TFrame", height=Metrics.STATUS_HEIGHT)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)

        separator = tk.Frame(bar, bg=Palette.BORDER, height=1)
        separator.grid(row=0, column=0, columnspan=4, sticky="new")
        self.status_dot = tk.Frame(bar, width=8, height=8, bg=Palette.INFO)
        self.status_dot.grid(row=1, column=0, padx=(Metrics.PAD_X, 9), pady=(13, 0), sticky="w")
        self.status_dot.grid_propagate(False)
        self.status_var = tk.StringVar(value="正在初始化本地工作区…")
        ttk.Label(bar, textvariable=self.status_var, style="StatusBar.TLabel").grid(row=1, column=1, sticky="w", pady=(9, 0))
        self.progress = ttk.Progressbar(bar, mode="determinate", length=170, style="Brand.Horizontal.TProgressbar")
        self.progress.grid(row=1, column=2, padx=(12, 10), pady=(12, 0), sticky="e")
        ttk.Label(bar, text="本地 · 私密", style="StatusBar.TLabel").grid(
            row=1, column=3, padx=(0, Metrics.PAD_X), pady=(9, 0), sticky="e"
        )

    # ========== 中文运行状态 ==========

    def _detect_apps(self):
        self.detect_button.configure(state=tk.DISABLED, text="正在检测…")
        self._set_status("正在扫描本机应用数据目录…", tone="info")
        self._render_nav_loading()

        def worker():
            results = []
            for adapter in self.adapters:
                try:
                    available = adapter.detect()
                    info = adapter.get_app_info() if available else AppInfo(
                        name=adapter.name,
                        display_name=adapter.display_name,
                        is_available=False,
                        data_path=None,
                        conversation_count=0,
                    )
                except Exception:
                    available = False
                    info = AppInfo(
                        name=adapter.name,
                        display_name=adapter.display_name,
                        is_available=False,
                        data_path=None,
                        conversation_count=0,
                    )
                results.append((adapter, info, available))
            self._post_ui(self._on_apps_detected, results)

        threading.Thread(target=worker, daemon=True, name="app-detect").start()

    def _render_nav_loading(self):
        for widget in self.app_list_frame.winfo_children():
            widget.destroy()
        self._nav_rows.clear()
        tk.Label(
            self.app_list_frame,
            text="正在检测本机数据来源…",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 9),
            padx=12,
            pady=18,
        ).pack(anchor=tk.W)

    def _on_apps_detected(self, results):
        for widget in self.app_list_frame.winfo_children():
            widget.destroy()
        self._nav_rows.clear()
        self._app_infos.clear()
        for adapter, info, available in results:
            self._app_infos[adapter.name] = info
            self._add_nav_row(adapter, info, available)
        available_count = sum(1 for _, _, available in results if available)
        self.detect_button.configure(state=tk.NORMAL, text="重新检测来源")
        self._set_status(
            f"已发现 {available_count} 个可用数据来源",
            tone="success" if available_count else "warning",
        )
        self._sync_action_states()

    def _add_nav_row(self, adapter, info: AppInfo, available: bool):
        name = adapter.name
        accent = self.APP_ACCENTS.get(name, Palette.ACCENT)
        row = tk.Frame(self.app_list_frame, bg=Palette.SIDEBAR, bd=0, padx=0, pady=2)
        row.pack(fill=tk.X, pady=2)

        bar = tk.Frame(row, bg=Palette.SIDEBAR, width=3)
        bar.pack(side=tk.LEFT, fill=tk.Y)
        bar.pack_propagate(False)
        body = tk.Frame(row, bg=Palette.SIDEBAR, cursor="hand2" if available else "arrow", padx=8, pady=9)
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

        labels = tk.Frame(body, bg=Palette.SIDEBAR)
        labels.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        display_name = self.SOURCE_NAMES.get(name, info.display_name)
        title = tk.Label(
            labels,
            text=display_name,
            anchor=tk.W,
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK if available else "#64748B",
            font=(FONT_UI, 9, "bold"),
        )
        title.pack(fill=tk.X)
        meta = tk.Label(
            labels,
            text="可用" if available else "未检测到",
            anchor=tk.W,
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED if available else "#475569",
            font=(FONT_UI, 8),
        )
        meta.pack(fill=tk.X, pady=(2, 0))
        status = tk.Frame(body, width=7, height=7, bg=Palette.SUCCESS if available else "#475569")
        status.pack(side=tk.RIGHT, padx=(6, 1))
        status.pack_propagate(False)

        widgets = (row, body, avatar, labels, title, meta, status)
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
            "labels": labels,
            "title": title,
            "meta": meta,
            "status": status,
            "accent": accent,
            "available": available,
        }

    def _select_app(self, adapter):
        super()._select_app(adapter)
        self.preview_title_var.set("对话预览")
        self.preview_meta_var.set("选择左侧对话后查看完整内容")
        self.page_subtitle_var.set("正在读取本机对话记录")
        self.source_badge.configure(text=self.SOURCE_NAMES.get(adapter.name, adapter.display_name))
        self.preview_source_badge.configure(text=self.SOURCE_NAMES.get(adapter.name, adapter.display_name))
        self._show_center_loading(f"正在加载 {adapter.display_name}…")

    def _reload_current_source(self):
        if not self.current_adapter:
            return
        if hasattr(self.current_adapter, "reset_runtime_cache"):
            self.current_adapter.reset_runtime_cache()
        elif hasattr(self.current_adapter, "_cached_conversations"):
            self.current_adapter._cached_conversations = None
        self._show_center_loading(f"正在刷新 {self.current_adapter.display_name}…")
        self._load_generation += 1
        self._load_conversations(self._load_generation)

    def _show_center_loading(self, text: str):
        self._tree_conv_map.clear()
        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)
        self.conv_tree.insert("", tk.END, iid="__loading__", values=(text, "", ""), tags=("loading",))
        self.library_count_var.set("加载中")
        self.library_footer_var.set(text)
        self._set_status(text, tone="info")

    def _on_conversations_failed(self, error: str, generation: int):
        if generation != self._load_generation:
            return
        self.current_conversations = []
        self._tree_conv_map.clear()
        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)
        self.conv_tree.insert("", tk.END, iid="__error__", values=("加载失败", error[:80], ""), tags=("error",))
        self.library_count_var.set("0 条")
        self.library_footer_var.set("数据来源返回错误")
        self._set_status(f"加载失败：{error}", tone="danger")
        self._sync_action_states()

    def _on_conversations_loaded(self, conversations, generation: int):
        if generation != self._load_generation:
            return
        self.current_conversations = conversations
        self.library_count_var.set(f"{len(conversations)} 条")
        self.library_footer_var.set("已从本机存储加载")
        self.page_subtitle_var.set(f"本机可用对话 {len(conversations)} 条")
        self._filter_conversations()
        self._set_status(
            f"已加载 {len(conversations)} 条对话",
            tone="success" if conversations else "warning",
        )
        self._sync_action_states()

    def _on_search_focus_out(self, _event):
        if not self.search_var.get().strip():
            self._search_placeholder_active = True
            self.search_var.set("按标题搜索对话…")
            self.search_entry.configure(fg=Palette.TEXT_DISABLED)

    def _filter_conversations(self):
        self._filter_after_id = None
        self._tree_render_generation += 1
        generation = self._tree_render_generation
        self._tree_conv_map.clear()

        search = "" if self._search_placeholder_active else self.search_var.get().lower().strip()
        matches = []
        for index, conv in enumerate(self.current_conversations):
            title = conv.title or ""
            if search and search not in title.lower():
                continue
            matches.append((index, conv))

        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)
        self.library_count_var.set(
            f"{len(matches)} / {len(self.current_conversations)}" if search else f"{len(self.current_conversations)} 条"
        )

        if not matches:
            text = "没有匹配的对话" if search else "当前来源没有可显示的对话"
            self.conv_tree.insert("", tk.END, iid="__empty__", values=(text, "", ""), tags=("empty",))
            return
        self._insert_tree_batch(matches, 0, generation)

    def _insert_tree_batch(self, matches, start: int, generation: int):
        if generation != self._tree_render_generation:
            return
        end = min(start + self.TREE_INSERT_BATCH_SIZE, len(matches))
        for display_index, (source_index, conv) in enumerate(matches[start:end], start=start):
            updated = conv.updated_at.strftime("%Y-%m-%d %H:%M") if conv.updated_at else "—"
            count = conv.metadata.get("msg_count") if conv.metadata else None
            if count is None:
                count = len(conv.messages)
            title = conv.title or "（无标题对话）"
            if len(title) > 48:
                title = title[:45] + "…"
            item_id = f"conv_{source_index}"
            self._tree_conv_map[item_id] = conv
            tag = "even" if display_index % 2 == 0 else "odd"
            self.conv_tree.insert("", tk.END, iid=item_id, values=(title, updated, count), tags=(tag,))
        if end < len(matches):
            self.root.after(1, lambda: self._insert_tree_batch(matches, end, generation))

    # ========== 中文预览 ==========

    def _show_preview_placeholder(self):
        self.preview_text.configure(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert(tk.END, "\n\n\n\n")
        self.preview_text.insert(tk.END, "在这里查看完整对话\n", "empty_title")
        self.preview_text.insert(tk.END, "\n请先选择数据来源，再从左侧列表选择一条对话。\n", "empty_body")
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
        self.preview_meta_var.set("正在从本机存储读取完整对话…")
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
                markdown = self._render_markdown(full)
                self._post_ui(self._show_preview, markdown, full, generation)
            except Exception as exc:
                self._post_ui(self._set_status, f"预览失败：{exc}", -1, "danger")
                self._post_ui(self._show_preview_error, str(exc))

        threading.Thread(target=worker, daemon=True, name="preview-load").start()

    def _show_preview_error(self, error: str):
        self.preview_text.configure(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert(tk.END, "\n\n")
        self.preview_text.insert(tk.END, "无法读取这条对话\n", "empty_title")
        self.preview_text.insert(tk.END, f"\n{error}\n", "empty_body")
        self.preview_text.configure(state=tk.DISABLED)
        self.preview_meta_var.set("读取失败，请刷新来源后重试")
    # _show_preview / _render_colored_preview / _role_to_tags 已移除：
    # 活动路径 gui_cn_v2._show_preview 使用 visible_messages + _start_preview_render
    # （后台分批渲染），不再需要这些旧方法。_preview_insert / _preview_part
    # 继承自 gui_modern（已取消累计上限，正文永不截断）。

    # ========== 中文密钥助手 ==========

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
        dialog.resizable(False, False)
        dialog.configure(bg=Palette.WINDOW)
        place_centered(dialog, self.root, 720, 520)
        dialog.protocol("WM_DELETE_WINDOW", self._close_key_dialog)

        hero = tk.Frame(dialog, bg=Palette.SIDEBAR, padx=26, pady=22)
        hero.pack(fill=tk.X)
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
            font=(FONT_UI, 18, "bold"),
        ).pack(anchor=tk.W, pady=(6, 4))
        tk.Label(
            hero,
            text="仅在你点击开始后读取本机 TRAE 进程内存；所有数据都留在当前电脑。",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 9),
            wraplength=650,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        body = tk.Frame(dialog, bg=Palette.WINDOW, padx=24, pady=20)
        body.pack(fill=tk.BOTH, expand=True)

        checklist = tk.Frame(
            body,
            bg=Palette.SURFACE,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            padx=16,
            pady=14,
        )
        checklist.pack(fill=tk.X)
        tk.Label(
            checklist,
            text="开始前请确认",
            bg=Palette.SURFACE,
            fg=Palette.TEXT_MUTED,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor=tk.W)

        for index, text in enumerate((
            "TRAE SOLO CN 已经启动",
            "至少打开过一个对话窗口",
            "扫描范围限制为 TRAE 私有内存，最多 8 秒 / 300MB",
        ), start=1):
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
            pady=14,
        )
        progress_card.pack(fill=tk.X, pady=(14, 0))

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
            wraplength=640,
            justify=tk.LEFT,
        )
        self._key_dialog_widgets["status"].pack(anchor=tk.W, pady=(5, 3))
        self._key_dialog_widgets["detail"] = tk.Label(
            progress_card,
            text="助手会先检查环境变量和本地缓存，仅在需要时扫描进程内存。",
            bg=Palette.SURFACE,
            fg=Palette.TEXT_MUTED,
            font=(FONT_UI, 9),
            wraplength=640,
            justify=tk.LEFT,
        )
        self._key_dialog_widgets["detail"].pack(anchor=tk.W)

        key_progress = ttk.Progressbar(progress_card, mode="determinate", style="Brand.Horizontal.TProgressbar")
        key_progress.pack(fill=tk.X, pady=(12, 0))
        self._key_dialog_widgets["progress"] = key_progress

        key_frame = tk.Frame(body, bg=Palette.WINDOW)
        self._key_dialog_widgets["key_frame"] = key_frame
        self._key_dialog_widgets["key_var"] = tk.StringVar(value="")

        buttons = tk.Frame(body, bg=Palette.WINDOW)
        buttons.pack(fill=tk.X, side=tk.BOTTOM, pady=(18, 0))
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

    def _start_key_scan(self):
        if self._key_extract_running or not self.current_adapter:
            return
        self._key_extract_running = True
        self._key_cancel_event = threading.Event()
        self._sync_action_states()
        self._key_dialog_widgets["start"].configure(state=tk.DISABLED)
        self._key_dialog_widgets["cancel"].configure(text="取消扫描", command=self._cancel_key_scan)
        self._key_dialog_widgets["stage"].configure(text="扫描中", fg=Palette.INFO)
        self._key_dialog_widgets["status"].configure(text="正在准备安全的本机扫描…")
        self._key_dialog_widgets["detail"].configure(text="请保持 TRAE 打开；你可以随时取消。")
        progress = self._key_dialog_widgets["progress"]
        progress.configure(mode="indeterminate")
        progress.start(12)
        self._set_busy_progress(True)
        self._set_status("TRAE 密钥助手正在扫描本机进程内存…", tone="info")
        adapter = self.current_adapter

        def progress_callback(event: dict):
            self._post_ui(self._update_key_scan_progress, event)

        def worker():
            try:
                result = adapter.extract_key_for_user(
                    progress_callback=progress_callback,
                    cancel_event=self._key_cancel_event,
                )
            except Exception as exc:
                result = {"ok": False, "reason": str(exc), "hint": "扫描过程中发生异常。"}
            self._post_ui(self._finish_key_scan, result)

        threading.Thread(target=worker, daemon=True, name="trae-key-scan").start()

    def _update_key_scan_progress(self, event: dict):
        if not self._key_dialog or not self._key_dialog.winfo_exists():
            return
        message = event.get("message", "正在扫描…")
        stage_map = {
            "prepare": "准备中",
            "processes": "已发现进程",
            "scan_pid": "扫描进程",
            "progress": "扫描中",
            "found": "已找到",
            "not_found": "未找到",
            "no_process": "未发现进程",
            "cancelled": "已取消",
        }
        stage = stage_map.get(event.get("stage", "scan"), "扫描中")
        self._key_dialog_widgets["stage"].configure(text=stage)
        self._key_dialog_widgets["status"].configure(text=message)
        if "scanned_mb" in event:
            self._key_dialog_widgets["detail"].configure(
                text=f"已扫描约 {event['scanned_mb']}MB 的 TRAE 私有内存。"
            )
        self._set_status(message, tone="info")

    def _cancel_key_scan(self):
        if self._key_cancel_event:
            self._key_cancel_event.set()
        self._key_dialog_widgets["cancel"].configure(state=tk.DISABLED, text="正在取消…")
        self._key_dialog_widgets["status"].configure(text="正在安全停止扫描…")

    def _finish_key_scan(self, result: dict):
        self._key_extract_running = False
        self._set_busy_progress(False)
        self._sync_action_states()
        progress = self._key_dialog_widgets.get("progress")
        if progress:
            progress.stop()
            progress.configure(mode="determinate")
            progress["value"] = 100 if result.get("ok") else 0
        if not self._key_dialog or not self._key_dialog.winfo_exists():
            return

        if not result.get("ok"):
            cancelled = result.get("cancelled")
            self._key_dialog_widgets["stage"].configure(
                text="已取消" if cancelled else "未找到",
                fg=Palette.WARNING,
            )
            self._key_dialog_widgets["status"].configure(text=result.get("reason", "未找到有效密钥"))
            self._key_dialog_widgets["detail"].configure(
                text=result.get("hint", "请打开 TRAE 对话后重试。")
            )
            self._key_dialog_widgets["start"].configure(state=tk.NORMAL, text="重新尝试")
            self._key_dialog_widgets["cancel"].configure(
                state=tk.NORMAL,
                text="关闭",
                command=self._close_key_dialog,
            )
            self._set_status("TRAE 密钥扫描已取消" if cancelled else "未找到 TRAE 密钥", tone="warning")
            return

        key_hex = result.get("key_hex", "")
        source = result.get("source", "本机")
        elapsed = result.get("elapsed", 0)
        self._key_dialog_widgets["stage"].configure(text="已完成", fg=Palette.SUCCESS)
        self._key_dialog_widgets["status"].configure(text="TRAE 密钥已验证并安全保存在本机")
        self._key_dialog_widgets["detail"].configure(
            text=f"来源：{source} · 耗时：{elapsed}s。正在重新加载完整数据库。"
        )
        self._key_dialog_widgets["start"].pack_forget()
        self._key_dialog_widgets["cancel"].configure(
            state=tk.NORMAL,
            text="完成",
            command=self._close_key_dialog,
        )
        self._render_key_result(key_hex)

        adapter = self.current_adapter
        if adapter and getattr(adapter, "name", "") == "trae":
            adapter.reset_runtime_cache()
            self._load_generation += 1
            self._show_center_loading("密钥已验证，正在加载完整 TRAE 数据库…")
            self._load_conversations(self._load_generation)
        self._set_status("TRAE 密钥验证成功，正在加载完整对话库", tone="success")

    def _render_key_result(self, key_hex: str):
        frame = self._key_dialog_widgets["key_frame"]
        for child in frame.winfo_children():
            child.destroy()
        frame.pack(fill=tk.X, pady=(14, 0))
        card = tk.Frame(
            frame,
            bg=Palette.SUCCESS_SOFT,
            highlightbackground="#ABEFC6",
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        card.pack(fill=tk.X)
        tk.Label(
            card,
            text="已验证密钥",
            bg=Palette.SUCCESS_SOFT,
            fg=Palette.SUCCESS,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor=tk.W)
        key_var = self._key_dialog_widgets["key_var"]
        key_var.set(key_hex)
        entry = tk.Entry(
            card,
            textvariable=key_var,
            show="*",
            state="readonly",
            readonlybackground=Palette.SUCCESS_SOFT,
            fg=Palette.TEXT,
            font=(FONT_MONO, 9),
            relief=tk.FLAT,
            bd=0,
        )
        entry.pack(fill=tk.X, pady=(7, 8))
        row = tk.Frame(card, bg=Palette.SUCCESS_SOFT)
        row.pack(fill=tk.X)
        show_var = tk.BooleanVar(value=False)

        def toggle():
            entry.configure(show="" if show_var.get() else "*")

        ttk.Checkbutton(
            row,
            text="显示密钥",
            variable=show_var,
            command=toggle,
            style="Modern.TCheckbutton",
        ).pack(side=tk.LEFT)
        ttk.Button(
            row,
            text="复制",
            style="Secondary.TButton",
            command=lambda: self._copy_key(key_hex),
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(
            row,
            text="保存到 Windows",
            style="AccentSoft.TButton",
            command=lambda: self._persist_key(key_hex),
        ).pack(side=tk.RIGHT)

    def _copy_key(self, key_hex: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(key_hex)
        self.root.update_idletasks()
        self._key_dialog_widgets["detail"].configure(text="密钥已复制到剪贴板，请妥善保管。")

    # ========== 中文导出 ==========

    def _export_selected(self):
        if not self.selected_conv:
            return
        conv = self.selected_conv
        default_name = MarkdownExporter.sanitize_filename(conv.title)
        if conv.updated_at:
            default_name += f"_{conv.updated_at.strftime('%Y%m%d_%H%M%S')}"
        default_name += ".md"
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown 文件", "*.md"), ("所有文件", "*.*")],
            initialfile=default_name,
            title="导出当前对话",
        )
        if not path:
            return
        try:
            self.exporter.include_thinking = True
            self.exporter.export(conv, path)
            self._set_status(f"已导出：{os.path.basename(path)}", progress=100, tone="success")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            self._set_status(f"导出失败：{exc}", tone="danger")

    def _export_all(self):
        if not self.current_conversations:
            return
        output_dir = filedialog.askdirectory(title="选择批量导出目录")
        if not output_dir:
            return
        if not messagebox.askyesno(
            "批量导出",
            f"将 {len(self.current_conversations)} 条对话导出到：\n{output_dir}\n\n是否继续？",
        ):
            return
        adapter = self.current_adapter
        conversations = list(self.current_conversations)
        self._set_status("正在导出完整对话…", progress=0, tone="info")
        self.batch_button.configure(state=tk.DISABLED)

        def worker():
            try:
                count = self._batch_export_full_conversations(conversations, output_dir, adapter)
                self._post_ui(self._on_batch_export_complete, count, output_dir)
            except Exception as exc:
                self._post_ui(self._on_batch_export_failed, str(exc))

        threading.Thread(target=worker, daemon=True, name="batch-export").start()

    def _batch_export_full_conversations(self, conversations: List[Conversation], output_dir: str, adapter) -> int:
        os.makedirs(output_dir, exist_ok=True)
        exporter = MarkdownExporter(include_metadata=True, include_timestamp=True, include_thinking=True)
        total = len(conversations)
        exported = 0
        for index, conv in enumerate(conversations, start=1):
            full = conv
            if adapter and not conv.messages:
                loaded = adapter.get_conversation(conv.id)
                if loaded:
                    full = loaded
            safe_title = MarkdownExporter.sanitize_filename(full.title)
            timestamp = full.updated_at.strftime("%Y%m%d_%H%M%S") if full.updated_at else ""
            filename = f"{safe_title}_{timestamp}.md" if timestamp else f"{safe_title}.md"
            path = os.path.join(output_dir, filename)
            base, ext = os.path.splitext(path)
            counter = 1
            while os.path.exists(path):
                path = f"{base}_{counter}{ext}"
                counter += 1
            exporter.export(full, path)
            exported += 1
            progress = int(index / total * 100)
            self._post_ui(
                self._set_status,
                f"正在导出 {index}/{total} · {os.path.basename(path)}",
                progress,
                "info",
            )
        return exported

    def _on_batch_export_complete(self, count: int, output_dir: str):
        self._set_status(f"已导出 {count} 条对话", progress=100, tone="success")
        self._sync_action_states()
        messagebox.showinfo("导出完成", f"已将 {count} 个 Markdown 文件导出到：\n{output_dir}")

    def _on_batch_export_failed(self, error: str):
        self._set_status(f"批量导出失败：{error}", tone="danger")
        self._sync_action_states()
        messagebox.showerror("导出失败", error)


def run():
    app = ChatExporterGUI()
    app.run()

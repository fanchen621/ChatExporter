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
from .exporters import batch_export, get_exporter
from .gui_cn_v3 import ChatExporterGUI as LegacyGUI
from .models import AppInfo
from .preview_runtime import PreviewPayload
from .preview_utils import PREVIEW_CLEAN, PREVIEW_FULL, conversation_search_text
from .search_index import conversation_stamp
from .task_runtime import TaskContext, UiTaskRunner
from .ui_theme import FONT_LATIN, FONT_UI, Palette, resource_path


class ChatExporterGUI(LegacyGUI):
    """Product shell: quiet visual hierarchy and bounded background work."""

    SIDEBAR_WIDTH = 220
    MIN_LIBRARY_WIDTH = 400
    MIN_PREVIEW_WIDTH = 520
    PREVIEW_PAGE_SIZE = 180
    UI_QUEUE_POLL_MS = 75
    FORMAT_UI_CHOICES = (
        ("markdown", "Markdown"),
        ("html", "HTML"),
        ("json", "JSON"),
        ("txt", "纯文本"),
    )

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
        self._pane_resize_after_id = None
        self._progress_busy = False
        self._progress_hide_after_id = None
        self._preview_find_placeholder_active = True
        self._brand_image = None
        self._tree_context_menu = None
        self._toast_after_id = None
        self._toast_frame = None
        self._preview_state_action = None
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

    def _apply_screen_geometry(self):
        """Fit ordinary laptop screens instead of forcing a near-fullscreen minimum."""
        self.root.update_idletasks()
        screen_w = max(960, self.root.winfo_screenwidth())
        screen_h = max(640, self.root.winfo_screenheight())

        min_w = min(1080, max(940, screen_w - 80))
        min_h = min(680, max(580, screen_h - 100))
        width = min(1680, max(min_w, int(screen_w * 0.88)))
        height = min(960, max(min_h, int(screen_h * 0.84)))
        width = min(width, max(min_w, screen_w - 24))
        height = min(height, max(min_h, screen_h - 64))

        self.root.minsize(min_w, min_h)
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self._enable_double_buffering()

    # ---------------------------------------------------------- message counts

    def _on_conversations_loaded(self, conversations, generation: int):
        """Show the library immediately, then hydrate exact counts in-place."""
        super()._on_conversations_loaded(conversations, generation)
        if generation != self._load_generation:
            return
        self._update_selection_summary()
        if hasattr(self, "command_context_var"):
            self.command_context_var.set(
                f"{len(conversations)} 条本机对话可用 · 选择后可预览和完整导出"
                if conversations
                else "这个来源暂时没有可预览的对话"
            )
        if not conversations:
            self.preview_title_var.set("这个来源还没有对话")
            self.preview_meta_var.set("没有发现可预览或导出的本机记录")
            self._show_preview_state(
                "暂无可用对话",
                "应用产生对话记录后按 F5 刷新；现有本机数据不会被修改。",
                "刷新来源",
                self._reload_current_source,
            )
        self._start_message_count_hydration(conversations, generation)

    def _on_conversations_failed(self, error: str, generation: int):
        super()._on_conversations_failed(error, generation)
        if generation != self._load_generation:
            return
        self.preview_title_var.set("这个来源暂时打不开")
        self.preview_meta_var.set("可以按 F5 重试，或重新扫描本机来源")
        self._show_preview_state(
            "读取来源失败",
            f"没有修改任何本机数据。\n\n{str(error)[:180]}",
            "重新加载",
            self._reload_current_source,
        )
        self._update_selection_summary()
        if hasattr(self, "command_context_var"):
            self.command_context_var.set("来源读取失败 · 可以刷新或重新扫描")

    def _on_apps_detected(self, results):
        super()._on_apps_detected(results)
        available_count = sum(1 for _adapter, _info, available in results if available)
        if self.current_adapter is not None:
            return
        if available_count:
            self.page_title_var.set("本机对话")
            self.page_subtitle_var.set(f"已发现 {available_count} 个数据来源 · 从左侧选择")
            self.preview_title_var.set("选择一个数据来源")
            self.preview_meta_var.set("所有读取和导出都只在本机完成")
            self._show_preview_state(
                "先选择数据来源",
                "从左侧选择一个已连接的应用，然后再选择要阅读的对话。",
            )
            if hasattr(self, "command_context_var"):
                self.command_context_var.set(f"已发现 {available_count} 个本机来源")
        else:
            self.page_title_var.set("未发现本机对话")
            self.page_subtitle_var.set("请确认支持的应用已运行或已有本机记录")
            self.preview_title_var.set("没有可用数据来源")
            self.preview_meta_var.set("可重新扫描；不会上传或改写任何数据")
            self._show_preview_state(
                "没有检测到本机对话",
                "启动支持的应用并产生对话后，点击左下角“重新扫描来源”。",
                "重新扫描来源",
                self._detect_apps,
            )
            if hasattr(self, "command_context_var"):
                self.command_context_var.set("没有检测到可读取的本机来源")

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

        brand = tk.Frame(parent, bg=Palette.SIDEBAR, padx=16, pady=16)
        brand.grid(row=0, column=0, sticky="ew")
        try:
            image = tk.PhotoImage(file=resource_path("assets", "window.png"))
            factor = max(1, image.width() // 30)
            self._brand_image = image.subsample(factor, factor)
        except tk.TclError:
            self._brand_image = None
        logo = tk.Label(
            brand,
            image=self._brand_image,
            text="C" if self._brand_image is None else "",
            compound=tk.CENTER,
            bg=Palette.SIDEBAR,
            fg=Palette.ON_ACCENT,
            font=(FONT_LATIN, 11, "bold"),
            padx=2,
            pady=2,
        )
        logo.pack(side=tk.LEFT)
        text = tk.Frame(brand, bg=Palette.SIDEBAR)
        text.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(
            text,
            text="ChatExporter",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK,
            font=(FONT_UI, 12, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            text,
            text="本地、私密、可导出",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 8),
        ).pack(anchor=tk.W, pady=(2, 0))

        section = tk.Frame(parent, bg=Palette.SIDEBAR, padx=12, pady=0)
        section.grid(row=1, column=0, sticky="ew")
        tk.Label(
            section,
            text="数据来源",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 8, "bold"),
        ).pack(anchor=tk.W, padx=6, pady=(5, 7))

        self.app_list_frame = ttk.Frame(parent, style="Sidebar.TFrame")
        self.app_list_frame.grid(row=2, column=0, sticky="nsew", padx=7)

        footer = tk.Frame(parent, bg=Palette.SIDEBAR, padx=12, pady=12)
        footer.grid(row=3, column=0, sticky="ew")
        self.sidebar_key_button = ttk.Button(
            footer,
            text="TRAE 密钥助手",
            style="AccentSoft.TButton",
            command=self._open_key_assistant,
        )
        self.sidebar_key_button.pack(fill=tk.X)
        self.key_button = self.sidebar_key_button
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
            text="重新扫描来源",
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

    def _add_nav_row(self, adapter, info: AppInfo, available: bool):
        """Compact source rows with one clear selected state."""
        name = adapter.name
        accent = self.APP_ACCENTS.get(name, Palette.ACCENT)
        row = tk.Frame(self.app_list_frame, bg=Palette.SIDEBAR, bd=0)
        row.pack(fill=tk.X, pady=2)

        bar = tk.Frame(row, bg=Palette.SIDEBAR, width=3)
        bar.pack(side=tk.LEFT, fill=tk.Y)
        bar.pack_propagate(False)
        body = tk.Frame(
            row,
            bg=Palette.SIDEBAR,
            cursor="hand2" if available else "arrow",
            padx=9,
            pady=8,
        )
        body.pack(side=tk.LEFT, fill=tk.X, expand=True)

        avatar = tk.Label(
            body,
            text=self.APP_INITIALS.get(name, name[:2].upper()),
            width=2,
            bg=Palette.SIDEBAR_RAISED if available else Palette.SIDEBAR,
            fg=accent if available else Palette.SIDEBAR_TEXT_OFF,
            font=(FONT_LATIN, 8, "bold"),
            padx=5,
            pady=5,
        )
        avatar.pack(side=tk.LEFT)
        labels = tk.Frame(body, bg=Palette.SIDEBAR)
        labels.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(9, 6))
        title = tk.Label(
            labels,
            text=self.SOURCE_NAMES.get(name, info.display_name),
            anchor=tk.W,
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK if available else Palette.SIDEBAR_TEXT_OFF,
            font=(FONT_UI, 9, "bold"),
        )
        title.pack(fill=tk.X)
        meta = tk.Label(
            labels,
            text="已连接" if available else "未检测到本地数据",
            anchor=tk.W,
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED if available else Palette.SIDEBAR_TEXT_OFF,
            font=(FONT_UI, 7),
        )
        meta.pack(fill=tk.X, pady=(1, 0))
        status = tk.Frame(
            body,
            width=7,
            height=7,
            bg=Palette.SUCCESS if available else Palette.SIDEBAR_DOT_OFF,
        )
        status.pack(side=tk.RIGHT, padx=(4, 1))
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

    def _build_header(self, parent):
        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        title_row = ttk.Frame(header, style="Surface.TFrame")
        title_row.grid(row=0, column=0, sticky="ew", padx=20, pady=(11, 9))
        title_row.grid_columnconfigure(0, weight=1)
        left = ttk.Frame(title_row, style="Surface.TFrame")
        left.grid(row=0, column=0, sticky="w")
        self.page_title_var = tk.StringVar(value="对话")
        self.page_subtitle_var = tk.StringVar(value="选择左侧来源开始")
        ttk.Label(left, textvariable=self.page_title_var, style="PageTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(left, textvariable=self.page_subtitle_var, style="PageSub.TLabel").pack(anchor=tk.W, pady=(2, 0))

        utilities = ttk.Frame(title_row, style="Surface.TFrame")
        utilities.grid(row=0, column=1, sticky="e")
        self.refresh_button = ttk.Button(
            utilities,
            text="刷新",
            width=6,
            style="Compact.TButton",
            command=self._reload_current_source,
        )
        self.refresh_button.pack(side=tk.LEFT, padx=(0, 4))
        self.theme_button = ttk.Button(
            utilities,
            text="深色",
            width=6,
            style="Compact.TButton",
            command=self._toggle_theme,
        )
        self.theme_button.pack(side=tk.LEFT)

        command = ttk.Frame(header, style="CommandBar.TFrame")
        command.grid(row=1, column=0, sticky="ew")
        command.grid_columnconfigure(0, weight=1)
        context = ttk.Frame(command, style="CommandBar.TFrame")
        context.grid(row=0, column=0, sticky="w", padx=(20, 10), pady=8)
        self.source_badge = tk.Label(
            context,
            text="未选择来源",
            bg=Palette.ACCENT_SOFT,
            fg=Palette.ACCENT_PRESSED,
            font=(FONT_UI, 8, "bold"),
            padx=8,
            pady=3,
        )
        self.source_badge.pack(side=tk.LEFT)
        self.command_context_var = tk.StringVar(value="选择对话后可预览并导出完整记录")
        ttk.Label(
            context,
            textvariable=self.command_context_var,
            style="CommandHint.TLabel",
        ).pack(side=tk.LEFT, padx=(9, 0))

        actions = ttk.Frame(command, style="CommandBar.TFrame")
        actions.grid(row=0, column=1, sticky="e", padx=(10, 20), pady=7)

        saved_format = str(self.settings.get("export_format", "html"))
        if saved_format not in {fid for fid, _label in self.FORMAT_UI_CHOICES}:
            saved_format = "html"
        display_by_id = dict(self.FORMAT_UI_CHOICES)
        self.export_format_var = tk.StringVar(value=display_by_id[saved_format])
        ttk.Label(actions, text="导出为", style="CommandHint.TLabel").pack(side=tk.LEFT, padx=(0, 5))
        format_box = ttk.Combobox(
            actions,
            textvariable=self.export_format_var,
            values=[label for _fid, label in self.FORMAT_UI_CHOICES],
            state="readonly",
            width=7,
            font=(FONT_UI, 9),
            style="Compact.TCombobox",
        )
        format_box.pack(side=tk.LEFT, padx=(0, 7))
        format_box.bind("<<ComboboxSelected>>", self._on_export_format_changed)
        self.batch_button = ttk.Button(
            actions,
            text="批量导出",
            width=12,
            style="CompactSecondary.TButton",
            command=self._export_all,
        )
        self.batch_button.pack(side=tk.LEFT, padx=(0, 6))
        self.export_button = ttk.Button(
            actions,
            text="导出 HTML",
            width=12,
            style="Primary.TButton",
            command=self._export_selected,
        )
        self.export_button.pack(side=tk.LEFT)
        ttk.Separator(header, orient=tk.HORIZONTAL).grid(row=2, column=0, sticky="ew")

    def _selected_format_id(self) -> str:
        label = self.export_format_var.get() if hasattr(self, "export_format_var") else ""
        for format_id, display in self.FORMAT_UI_CHOICES:
            if display == label:
                return format_id
        return "html"

    def _build_workspace(self, parent):
        container = ttk.Frame(parent, style="App.TFrame")
        container.grid(row=1, column=0, sticky="nsew", padx=12, pady=12)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        self.workspace_panes = ttk.PanedWindow(container, orient=tk.HORIZONTAL, style="Modern.TPanedwindow")
        self.workspace_panes.grid(row=0, column=0, sticky="nsew")
        self.library_pane = ttk.Frame(self.workspace_panes, style="Card.TFrame")
        self.preview_pane = ttk.Frame(self.workspace_panes, style="Card.TFrame")
        self.workspace_panes.add(self.library_pane, weight=2)
        self.workspace_panes.add(self.preview_pane, weight=5)
        self._build_library_card(self.library_pane)
        self._build_preview_card(self.preview_pane)
        self.workspace_panes.bind("<Configure>", self._schedule_pane_constraint, add="+")
        self.workspace_panes.bind("<ButtonRelease-1>", self._schedule_pane_constraint, add="+")
        self.root.after_idle(self._place_initial_sash)

    def _place_initial_sash(self):
        try:
            width = self.workspace_panes.winfo_width()
            if width <= self.MIN_LIBRARY_WIDTH + self.MIN_PREVIEW_WIDTH:
                return
            saved = self.settings.get("sash_position", 0)
            maximum = width - self.MIN_PREVIEW_WIDTH
            if isinstance(saved, int) and self.MIN_LIBRARY_WIDTH <= saved <= maximum:
                target = saved
            else:
                target = min(max(int(width * 0.36), self.MIN_LIBRARY_WIDTH), maximum)
            self.workspace_panes.sashpos(0, target)
            self._fit_tree_columns()
        except tk.TclError:
            pass

    def _schedule_pane_constraint(self, _event=None):
        if self._pane_resize_after_id:
            try:
                self.root.after_cancel(self._pane_resize_after_id)
            except tk.TclError:
                pass
        self._pane_resize_after_id = self.root.after(40, self._enforce_pane_limits)

    def _enforce_pane_limits(self):
        self._pane_resize_after_id = None
        try:
            width = self.workspace_panes.winfo_width()
            if width <= 1:
                return
            minimum = min(self.MIN_LIBRARY_WIDTH, max(240, width - self.MIN_PREVIEW_WIDTH))
            maximum = max(minimum, width - self.MIN_PREVIEW_WIDTH)
            position = self.workspace_panes.sashpos(0)
            clamped = min(max(position, minimum), maximum)
            if clamped != position:
                self.workspace_panes.sashpos(0, clamped)
            self._fit_tree_columns()
        except tk.TclError:
            pass

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
            style="Compact.TCombobox",
        )
        sort_box.grid(row=0, column=2, sticky="e")
        sort_box.bind("<<ComboboxSelected>>", lambda _e: self._filter_conversations())

        controls = tk.Frame(parent, bg=Palette.SURFACE, padx=14)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        controls.grid_columnconfigure(0, weight=1)
        controls.grid_columnconfigure(1, weight=1)
        search = tk.Frame(
            controls,
            bg=Palette.SURFACE_ALT,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            padx=9,
            pady=1,
        )
        self.search_field_frame = search
        search.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 7))
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
            width=1,
        )
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.search_clear_button = tk.Button(
            search,
            text="×",
            command=self._clear_search,
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT_MUTED,
            activebackground=Palette.SURFACE_HOVER,
            activeforeground=Palette.TEXT,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            font=(FONT_LATIN, 11),
            padx=5,
            cursor="hand2",
        )
        self.search_clear_button.pack(side=tk.RIGHT)
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
            style="Compact.TCombobox",
        )
        mode.grid(row=1, column=0, sticky="ew", padx=(0, 3))
        mode.bind("<<ComboboxSelected>>", self._on_search_mode_changed)
        self.date_filter_var = tk.StringVar(value=self.DATE_FILTER_ALL)
        date_box = ttk.Combobox(
            controls,
            textvariable=self.date_filter_var,
            values=tuple(label for label, _days in self.DATE_FILTERS),
            state="readonly",
            width=8,
            font=(FONT_UI, 8),
            style="Compact.TCombobox",
        )
        date_box.grid(row=1, column=1, sticky="ew", padx=(3, 0))
        date_box.bind("<<ComboboxSelected>>", lambda _e: self._filter_conversations())
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
        self.conv_tree.heading("title", text="标题", anchor=tk.W)
        self.conv_tree.heading("date", text="更新", anchor=tk.CENTER)
        self.conv_tree.heading("messages", text="消息", anchor=tk.E)
        self.conv_tree.column("title", width=180, minwidth=96, anchor=tk.W, stretch=True)
        self.conv_tree.column("date", width=78, minwidth=72, stretch=False, anchor=tk.CENTER)
        self.conv_tree.column("messages", width=64, minwidth=54, stretch=False, anchor=tk.E)
        self.conv_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.conv_tree.yview, style="Wide.Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        self.conv_tree.configure(yscrollcommand=scroll.set)
        self.conv_tree.bind("<Configure>", self._fit_tree_columns, add="+")
        self.root.after_idle(self._fit_tree_columns)
        self.conv_tree.bind("<<TreeviewSelect>>", self._on_tree_selection_changed, add="+")
        self.conv_tree.bind("<Double-1>", self._on_tree_double_click)
        self.conv_tree.bind("<Button-3>", self._show_tree_context_menu)
        self.conv_tree.bind("<Return>", self._focus_selected_preview)
        self.conv_tree.bind("<Control-a>", self._select_all_visible)
        for tag, color in (
            ("even", Palette.SURFACE),
            ("odd", Palette.SURFACE_ALT),
        ):
            self.conv_tree.tag_configure(tag, background=color)
        self.conv_tree.tag_configure("empty", foreground=Palette.TEXT_MUTED)
        self.conv_tree.tag_configure("error", foreground=Palette.DANGER)
        self.conv_tree.tag_configure("loading", foreground=Palette.ACCENT)

        self.selection_bar = ttk.Frame(parent, style="SelectionBar.TFrame")
        self.selection_bar.grid(row=3, column=0, sticky="ew", padx=10, pady=(7, 0))
        self.selection_bar.grid_columnconfigure(0, weight=1)
        self.selection_count_var = tk.StringVar(value="")
        ttk.Label(
            self.selection_bar,
            textvariable=self.selection_count_var,
            style="SelectionBar.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 1))
        ttk.Label(
            self.selection_bar,
            text="同一格式批量导出",
            style="SelectionBar.TLabel",
        ).grid(row=1, column=0, sticky="w", padx=10, pady=(1, 6))
        ttk.Button(
            self.selection_bar,
            text="取消",
            width=5,
            style="SelectionAction.TButton",
            command=self._clear_tree_selection,
        ).grid(row=1, column=1, padx=(4, 2), pady=(1, 4))
        ttk.Button(
            self.selection_bar,
            text="导出",
            width=5,
            style="SelectionAction.TButton",
            command=self._export_all,
        ).grid(row=1, column=2, padx=(2, 5), pady=(1, 4))
        self.selection_bar.grid_remove()

        footer = ttk.Frame(parent, style="Surface.TFrame")
        footer.grid(row=4, column=0, sticky="ew", padx=14, pady=(8, 10))
        footer.grid_columnconfigure(0, weight=1)
        self.library_footer_var = tk.StringVar(value="请选择数据来源")
        ttk.Label(footer, textvariable=self.library_footer_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.selection_summary_var = tk.StringVar(value="Ctrl+F 搜索")
        ttk.Label(footer, textvariable=self.selection_summary_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e", padx=(8, 0)
        )

        self._tree_context_menu = tk.Menu(
            self.root,
            tearoff=False,
            font=(FONT_UI, 9),
            borderwidth=1,
            relief=tk.SOLID,
        )
        self._tree_context_menu.add_command(label="预览此对话", command=self._focus_selected_preview)
        self._tree_context_menu.add_command(label="导出此对话…", command=self._export_selected)
        self._tree_context_menu.add_separator()
        self._tree_context_menu.add_command(label="导出全部…", command=self._export_all)
        self._tree_context_menu.add_separator()
        self._tree_context_menu.add_command(label="复制标题", command=self._copy_selected_title)

    def _real_tree_selection(self):
        return [
            item
            for item in self.conv_tree.selection()
            if not item.startswith("__") and item in self._tree_conv_map
        ]

    def _active_tree_item(self):
        selection = self._real_tree_selection()
        if not selection:
            return ""
        focused = self.conv_tree.focus()
        return focused if focused in selection else selection[-1]

    def _update_selection_summary(self):
        var = getattr(self, "selection_summary_var", None)
        if var is None:
            return
        count = len(self._real_tree_selection())
        if count > 1:
            var.set(f"已选 {count} 条 · Esc 取消")
            self.selection_count_var.set(f"已选 {count} 条")
            self.selection_bar.grid()
            if hasattr(self, "command_context_var"):
                self.command_context_var.set(f"已选择 {count} 条对话 · 将按同一格式批量导出")
        elif count == 1:
            var.set("双击阅读 · Ctrl+E 导出")
            self.selection_bar.grid_remove()
            if hasattr(self, "command_context_var"):
                self.command_context_var.set("已选择 1 条对话 · 预览按页加载，导出保持完整")
        else:
            var.set("Ctrl+F 搜索")
            self.selection_bar.grid_remove()
            if hasattr(self, "command_context_var") and self.current_adapter is not None:
                self.command_context_var.set(f"{len(self.current_conversations)} 条本机对话可用")

    def _clear_tree_selection(self):
        self.conv_tree.selection_remove(self.conv_tree.selection())
        self.conv_tree.focus("")
        self._show_preview_placeholder()
        self._update_selection_summary()

    def _on_tree_selection_changed(self, _event=None):
        self._update_selection_summary()
        self._sync_action_states()
        item_id = self._active_tree_item()
        if not item_id:
            return
        stub = self._tree_conv_map.get(item_id)
        if stub is None:
            return
        if stub is self._selected_stub and (self._preview_payload is not None or self._preview_page_busy):
            return
        self._load_preview_stub(stub)

    def _focus_selected_preview(self, _event=None):
        item_id = self._active_tree_item()
        if not item_id:
            return "break"
        self._on_tree_selection_changed()
        self.preview_text.focus_set()
        self.preview_text.see("1.0")
        self._set_status("预览已获得焦点 · Ctrl+Shift+F 可查找当前页", tone="info")
        return "break"

    def _on_tree_double_click(self, event):
        """Double-click means read, never an implicit export side effect."""
        item_id = self.conv_tree.identify_row(event.y)
        if not item_id or item_id.startswith("__") or item_id not in self._tree_conv_map:
            return "break"
        self.conv_tree.selection_set(item_id)
        self.conv_tree.focus(item_id)
        self._on_tree_selection_changed()
        return self._focus_selected_preview()

    def _select_all_visible(self, _event=None):
        items = [item for item in self.conv_tree.get_children() if not item.startswith("__")]
        if not items:
            return "break"
        focused = self.conv_tree.focus()
        self.conv_tree.selection_set(*items)
        self.conv_tree.focus(focused if focused in items else items[0])
        self._update_selection_summary()
        self._sync_action_states()
        return "break"

    def _copy_selected_title(self):
        item_id = self._active_tree_item()
        conv = self._tree_conv_map.get(item_id)
        if conv is None:
            return
        title = conv.title or "无标题对话"
        self.root.clipboard_clear()
        self.root.clipboard_append(title)
        self.root.update_idletasks()
        self._set_status("已复制对话标题", tone="success")
        self._show_toast("标题已复制", title[:72], tone="success")

    def _show_tree_context_menu(self, event):
        item_id = self.conv_tree.identify_row(event.y)
        if not item_id or item_id.startswith("__") or item_id not in self._tree_conv_map:
            return "break"
        if item_id not in self._real_tree_selection():
            self.conv_tree.selection_set(item_id)
        self.conv_tree.focus(item_id)
        self._on_tree_selection_changed()

        menu = self._tree_context_menu
        if menu is None:
            return "break"
        picked = self._multi_selected_conversations()
        total = len(self.current_conversations)
        batch_label = f"导出选中 {len(picked)} 条…" if picked else f"导出全部 {total} 条…"
        menu.entryconfigure(1, state=tk.DISABLED if self._export_running else tk.NORMAL)
        menu.entryconfigure(
            3,
            label=batch_label,
            state=tk.NORMAL if total and not self._export_running else tk.DISABLED,
        )
        menu.configure(
            background=Palette.SURFACE,
            foreground=Palette.TEXT,
            activebackground=Palette.ACCENT_SOFT,
            activeforeground=Palette.ACCENT_PRESSED,
            selectcolor=Palette.ACCENT,
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    @staticmethod
    def _compact_updated_at(conversation) -> str:
        updated = conversation.updated_at
        if not updated:
            return "—"
        current_year = time.localtime().tm_year
        if updated.year == current_year:
            return updated.strftime("%m-%d")
        return updated.strftime("%y-%m-%d")

    def _insert_tree_batch(self, matches, start: int, generation: int):
        if generation != self._tree_render_generation:
            return
        end = min(start + self.TREE_INSERT_BATCH_SIZE, len(matches))
        for display_index, (source_index, conv) in enumerate(matches[start:end], start=start):
            count = conv.metadata.get("msg_count") if conv.metadata else None
            count_known = bool(conv.metadata.get("msg_count_known", True)) if conv.metadata else True
            if not count_known:
                count = "…"
            elif count is None:
                count = len(conv.messages)
            title = conv.title or "无标题对话"
            if len(title) > 72:
                title = title[:69] + "…"
            item_id = f"conv_{source_index}"
            self._tree_conv_map[item_id] = conv
            tag = "even" if display_index % 2 == 0 else "odd"
            self.conv_tree.insert(
                "",
                tk.END,
                iid=item_id,
                values=(title, self._compact_updated_at(conv), count),
                tags=(tag,),
            )
        if end < len(matches):
            self.root.after(1, lambda: self._insert_tree_batch(matches, end, generation))
        else:
            self._update_selection_summary()

    def _render_matches(self, matches, query: bool):
        super()._render_matches(matches, query)
        self.root.after_idle(self._update_selection_summary)

    def _fit_tree_columns(self, _event=None):
        """Keep 更新/消息 fully readable under DPI scale; title absorbs squeeze.

        Fixed pixel guesses regress at 125%/150% (dates become ``07-1…``). Measure
        the compact stamps we actually render instead.
        """
        try:
            total = self.conv_tree.winfo_width()
        except tk.TclError:
            return
        if total <= 80:
            return
        if not hasattr(self, "_tree_cell_font"):
            import tkinter.font as tkfont

            self._tree_cell_font = tkfont.Font(font=(FONT_UI, 10))
            self._tree_head_font = tkfont.Font(font=(FONT_UI, 9, "bold"))
        date_w = max(
            self._tree_cell_font.measure("00-00-00"),
            self._tree_head_font.measure("更新"),
        ) + 28
        msg_w = max(
            self._tree_cell_font.measure("88888"),
            self._tree_head_font.measure("消息"),
        ) + 24
        gutter = 10
        reserved = date_w + msg_w + gutter
        title_min = 96
        if total < reserved + title_min:
            # Extreme narrow panes: protect the date stamp first, then messages.
            overflow = reserved + title_min - total
            shrink_msg = min(max(0, msg_w - 48), overflow)
            msg_w -= shrink_msg
            overflow -= shrink_msg
            if overflow > 0:
                date_w = max(
                    self._tree_cell_font.measure("00-00") + 20,
                    date_w - overflow,
                )
            reserved = date_w + msg_w + gutter
        title_w = max(title_min, total - reserved)
        # Never let column mins overflow the widget; Treeview would clip dates again.
        overflow = title_w + date_w + msg_w - total
        if overflow > 0:
            shrink_title = min(max(0, title_w - 72), overflow)
            title_w -= shrink_title
            overflow -= shrink_title
            if overflow > 0:
                shrink_msg = min(max(0, msg_w - 40), overflow)
                msg_w -= shrink_msg
                overflow -= shrink_msg
            if overflow > 0:
                date_w = max(self._tree_cell_font.measure("00-00") + 16, date_w - overflow)
        self.conv_tree.column("title", width=title_w, minwidth=min(title_min, title_w), stretch=True, anchor=tk.W)
        self.conv_tree.column("date", width=date_w, minwidth=min(date_w, date_w), stretch=False, anchor=tk.CENTER)
        self.conv_tree.column("messages", width=msg_w, minwidth=min(40, msg_w), stretch=False, anchor=tk.E)

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
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        toolbar.grid_columnconfigure(0, weight=1)
        find_wrap = tk.Frame(
            toolbar,
            bg=Palette.SURFACE_ALT,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            padx=9,
            pady=1,
        )
        self.preview_find_field_frame = find_wrap
        find_wrap.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.preview_find_var = tk.StringVar(value="页内查找…")
        self.preview_find_entry = tk.Entry(
            find_wrap,
            textvariable=self.preview_find_var,
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT_DISABLED,
            insertbackground=Palette.TEXT,
            relief=tk.FLAT,
            bd=0,
            font=(FONT_UI, 9),
            width=1,
        )
        self.preview_find_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6)
        self.preview_find_entry.bind("<FocusIn>", self._on_preview_find_focus_in)
        self.preview_find_entry.bind("<FocusOut>", self._on_preview_find_focus_out)
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
        self.preview_find_clear_button = tk.Button(
            find_wrap,
            text="×",
            command=self._clear_preview_find,
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT_MUTED,
            activebackground=Palette.SURFACE_HOVER,
            activeforeground=Palette.TEXT,
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            font=(FONT_LATIN, 11),
            padx=5,
            cursor="hand2",
        )
        self.preview_find_clear_button.pack(side=tk.RIGHT)

        settings = getattr(self, "settings", None)
        self.clean_preview_var = tk.BooleanVar(
            value=bool(settings.get("clean_preview", False)) if settings is not None else False
        )
        control_bar = ttk.Frame(toolbar, style="Surface.TFrame")
        control_bar.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        control_bar.grid_columnconfigure(1, weight=1)

        left_tools = ttk.Frame(control_bar, style="Surface.TFrame")
        left_tools.grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            left_tools,
            text="只看对话",
            variable=self.clean_preview_var,
            command=self._on_clean_preview_toggled,
            style="Modern.TCheckbutton",
        ).pack(side=tk.LEFT)
        ttk.Button(
            left_tools,
            text="复制本页",
            style="Compact.TButton",
            command=self._copy_preview_text,
        ).pack(side=tk.LEFT, padx=(12, 0))

        self.preview_page_var = tk.StringVar(value="选择左侧对话后开始预览")
        ttk.Label(
            control_bar,
            textvariable=self.preview_page_var,
            style="Muted.TLabel",
        ).grid(row=0, column=1, padx=(16, 12))

        pager_shell = tk.Frame(
            control_bar,
            bg=Palette.SURFACE_ALT,
            highlightbackground=Palette.BORDER,
            highlightthickness=1,
            padx=3,
            pady=2,
        )
        pager_shell.grid(row=0, column=2, sticky="e")
        pager = tk.Frame(pager_shell, bg=Palette.SURFACE_ALT)
        pager.pack(side=tk.LEFT)
        self.preview_first_button = ttk.Button(
            pager,
            text="最早",
            width=4,
            style="Pager.TButton",
            command=lambda: self._request_preview_page("earliest"),
        )
        self.preview_older_button = ttk.Button(
            pager,
            text="上页",
            width=4,
            style="Pager.TButton",
            command=lambda: self._request_preview_page("older"),
        )
        self.preview_newer_button = ttk.Button(
            pager,
            text="下页",
            width=4,
            style="Pager.TButton",
            command=lambda: self._request_preview_page("newer"),
        )
        self.preview_latest_button = ttk.Button(
            pager,
            text="最新",
            width=4,
            style="Pager.TButton",
            command=lambda: self._request_preview_page("latest"),
        )
        self.preview_first_button.pack(side=tk.LEFT)
        self.preview_older_button.pack(side=tk.LEFT, padx=(2, 0))
        self.preview_newer_button.pack(side=tk.LEFT, padx=(2, 0))
        self.preview_latest_button.pack(side=tk.LEFT, padx=(2, 0))

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
            width=1,
            height=1,
        )
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL, command=self.preview_text.yview, style="Wide.Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        self.preview_text.configure(yscrollcommand=scroll.set)
        self._setup_text_tags()
        self.preview_text.tag_configure("search_hit", background=Palette.WARNING_SOFT)
        self.preview_text.tag_configure("search_current", background=Palette.WARNING, foreground=Palette.TEXT)

        self.preview_state_layer = tk.Frame(text_wrap, bg=Palette.SURFACE, bd=0)
        self.preview_state_layer.place(x=0, y=0, relwidth=1, relheight=1)
        self.preview_state_card = ttk.Frame(self.preview_state_layer, style="Overlay.TFrame", padding=(30, 24))
        self.preview_state_card.place(relx=0.5, rely=0.44, anchor=tk.CENTER)
        self.preview_state_title_var = tk.StringVar(value="")
        self.preview_state_detail_var = tk.StringVar(value="")
        ttk.Label(
            self.preview_state_card,
            textvariable=self.preview_state_title_var,
            style="OverlayTitle.TLabel",
            anchor=tk.CENTER,
        ).pack()
        ttk.Label(
            self.preview_state_card,
            textvariable=self.preview_state_detail_var,
            style="OverlayBody.TLabel",
            anchor=tk.CENTER,
            justify=tk.CENTER,
            wraplength=460,
        ).pack(pady=(8, 0))
        self.preview_state_button = ttk.Button(
            self.preview_state_card,
            text="",
            style="AccentSoft.TButton",
        )
        self._show_preview_placeholder()
        self._sync_preview_pager()

    def _build_status_bar(self, parent):
        bar = ttk.Frame(parent, style="Surface.TFrame")
        self.status_bar = bar
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
        self.progress.grid_remove()
        ttk.Label(bar, text="仅在本机处理", style="StatusBar.TLabel").grid(row=1, column=3, padx=(0, 18), pady=6)

    def _show_progress(self):
        if self._progress_hide_after_id:
            try:
                self.root.after_cancel(self._progress_hide_after_id)
            except tk.TclError:
                pass
            self._progress_hide_after_id = None
        if not self.progress.winfo_ismapped():
            self.progress.grid()

    def _hide_progress(self):
        self._progress_hide_after_id = None
        if self._progress_busy:
            return
        try:
            self.progress.grid_remove()
        except tk.TclError:
            pass

    def _set_status(self, text: str, progress: int = -1, tone: str = "info"):
        super()._set_status(text, progress=progress, tone=tone)
        if progress >= 0:
            self._show_progress()
            if progress >= 100 and not self._progress_busy:
                self._progress_hide_after_id = self.root.after(1200, self._hide_progress)
        elif not self._progress_busy:
            self._hide_progress()

    def _hide_toast(self):
        self._toast_after_id = None
        if self._toast_frame is not None:
            try:
                self._toast_frame.destroy()
            except tk.TclError:
                pass
            self._toast_frame = None

    def _show_toast(self, title: str, detail: str = "", tone: str = "info", duration: int = 2600):
        """Show non-blocking feedback without stealing keyboard focus."""
        if self._toast_after_id:
            try:
                self.root.after_cancel(self._toast_after_id)
            except tk.TclError:
                pass
        self._hide_toast()
        tone_color = {
            "success": Palette.SUCCESS,
            "warning": Palette.WARNING,
            "danger": Palette.DANGER,
            "info": Palette.INFO,
        }.get(tone, Palette.INFO)
        frame = tk.Frame(
            self.root,
            bg=Palette.SURFACE,
            highlightbackground=Palette.BORDER_STRONG,
            highlightthickness=1,
            bd=0,
            padx=15,
            pady=12,
        )
        self._toast_frame = frame
        tk.Frame(frame, bg=tone_color, width=4).pack(side=tk.LEFT, fill=tk.Y, padx=(0, 11))
        body = tk.Frame(frame, bg=Palette.SURFACE)
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(
            body,
            text=title,
            bg=Palette.SURFACE,
            fg=Palette.TEXT,
            font=(FONT_UI, 9, "bold"),
            anchor=tk.W,
        ).pack(anchor=tk.W)
        if detail:
            tk.Label(
                body,
                text=detail,
                bg=Palette.SURFACE,
                fg=Palette.TEXT_MUTED,
                font=(FONT_UI, 8),
                justify=tk.LEFT,
                anchor=tk.W,
                wraplength=320,
            ).pack(anchor=tk.W, pady=(3, 0))
        frame.place(relx=1.0, x=-18, y=18, anchor=tk.NE)
        frame.lift()
        self._toast_after_id = self.root.after(max(1200, duration), self._hide_toast)

    def _set_busy_progress(self, busy: bool):
        self._progress_busy = bool(busy)
        try:
            if busy:
                self._show_progress()
                self.progress.configure(mode="indeterminate")
                self.progress.start(14)
            else:
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self.progress["value"] = 0
                self._hide_progress()
        except tk.TclError:
            pass

    def _reset_preview_find_marks(self):
        self._preview_find_hits = []
        self._preview_find_cursor = -1
        if getattr(self, "preview_find_count_var", None):
            self.preview_find_count_var.set("")
        if hasattr(self, "preview_text"):
            self.preview_text.tag_remove("search_hit", "1.0", tk.END)
            self.preview_text.tag_remove("search_current", "1.0", tk.END)

    def _on_search_focus_in(self, event=None):
        super()._on_search_focus_in(event)
        frame = getattr(self, "search_field_frame", None)
        if frame is not None:
            frame.configure(highlightbackground=Palette.ACCENT)

    def _on_search_focus_out(self, event=None):
        super()._on_search_focus_out(event)
        frame = getattr(self, "search_field_frame", None)
        if frame is not None:
            frame.configure(highlightbackground=Palette.BORDER)

    def _on_preview_find_focus_in(self, _event=None):
        frame = getattr(self, "preview_find_field_frame", None)
        if frame is not None:
            frame.configure(highlightbackground=Palette.ACCENT)
        if self._preview_find_placeholder_active:
            self._preview_find_placeholder_active = False
            self.preview_find_var.set("")
            self.preview_find_entry.configure(fg=Palette.TEXT)

    def _on_preview_find_focus_out(self, _event=None):
        frame = getattr(self, "preview_find_field_frame", None)
        if frame is not None:
            frame.configure(highlightbackground=Palette.BORDER)
        if not self.preview_find_var.get().strip():
            self._preview_find_placeholder_active = True
            self.preview_find_var.set("页内查找…")
            self.preview_find_entry.configure(fg=Palette.TEXT_DISABLED)
            self._reset_preview_find_marks()

    def _clear_preview_find(self):
        self._preview_find_placeholder_active = False
        self.preview_find_var.set("")
        self.preview_find_entry.configure(fg=Palette.TEXT)
        self._reset_preview_find_marks()
        self.preview_find_entry.focus_set()

    def _restore_preview_find_placeholder(self):
        self._preview_find_placeholder_active = True
        self.preview_find_var.set("页内查找…")
        self.preview_find_entry.configure(fg=Palette.TEXT_DISABLED)
        self._reset_preview_find_marks()

    def _schedule_preview_find(self):
        if self._preview_find_placeholder_active:
            self._reset_preview_find_marks()
            return
        super()._schedule_preview_find()

    # ------------------------------------------------------------ preview flow

    def _display_preview_state(
        self,
        title: str,
        detail: str,
        action_text: str = "",
        action=None,
    ):
        self.preview_text.configure(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.configure(state=tk.DISABLED)
        self.preview_state_title_var.set(title)
        self.preview_state_detail_var.set(detail)
        self._preview_state_action = action
        if action_text and callable(action):
            self.preview_state_button.configure(text=action_text, command=action)
            self.preview_state_button.pack(pady=(16, 0))
        else:
            self.preview_state_button.pack_forget()
        self.preview_state_layer.place(x=0, y=0, relwidth=1, relheight=1)
        self.preview_state_layer.lift()

    def _hide_preview_state(self):
        try:
            self.preview_state_layer.place_forget()
        except tk.TclError:
            pass

    def _show_preview_state(self, title: str, detail: str, action_text: str = "", action=None):
        self._preview_payload = None
        self._selected_stub = None
        self._preview_plain_text = ""
        self._reset_preview_find_marks()
        self._display_preview_state(title, detail, action_text, action)
        self.selected_conv = None
        self._sync_preview_pager()
        self._sync_action_states()

    def _show_preview_loading(self, title: str):
        """Replace stale content immediately when the selection changes."""
        self._preview_plain_text = ""
        self._reset_preview_find_marks()
        self._display_preview_state("正在打开对话", f"{title}\n\n内容正在从本机按需读取。")

    def _load_preview_stub(self, stub):
        self._preview_generation += 1
        self.preview_title_var.set(stub.title or "无标题对话")
        self.preview_meta_var.set("正在读取本机记录…")
        self._preview_payload = None
        self._selected_stub = stub
        self.selected_conv = stub
        self._show_preview_loading(stub.title or "无标题对话")
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

    def _on_conv_select(self, _event=None):
        """Compatibility entry point for older callers and tests."""
        self._on_tree_selection_changed(_event)

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
        self._hide_preview_state()
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
        self._preview_payload = None
        self._preview_plain_text = ""
        message = str(exc)
        if isinstance(exc, ConversationLoadError):
            self.preview_meta_var.set(message)
        else:
            self.preview_meta_var.set("预览暂时不可用")
        self._display_preview_state(
            "无法读取这条对话",
            f"{message}\n\n原始数据未被修改。",
            "重新尝试",
            self._reload_selected_preview,
        )
        self._sync_preview_pager()
        self._sync_action_states()
        self._set_status(f"预览失败：{message}", tone="danger")

    def _reload_selected_preview(self):
        if self._selected_stub is not None and not self._preview_page_busy:
            self._load_preview_stub(self._selected_stub)

    def _set_preview_busy(self, busy: bool):
        self._preview_page_busy = busy
        self._set_busy_progress(self._preview_page_busy or self._export_running)
        self._sync_preview_pager()

    def _show_preview_placeholder(self):
        self._preview_payload = None
        self._selected_stub = None
        self._preview_plain_text = ""
        self._reset_preview_find_marks()
        self.preview_title_var.set("选择一条对话")
        self.preview_meta_var.set("从左侧点选后可预览；导出始终完整")
        self._display_preview_state(
            "还没有选中对话",
            "在左侧列表单击一条即可预览。\n长对话按页加载，导出始终保留完整内容。",
        )
        self.selected_conv = None
        self._sync_preview_pager()
        self._sync_action_states()

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
        if page is None:
            for button in widgets:
                button.configure(state=tk.DISABLED)
            self.preview_page_var.set("选择左侧对话后开始预览")
            return
        disabled = self._preview_page_busy
        self.preview_first_button.configure(state=tk.DISABLED if disabled or not page.has_older else tk.NORMAL)
        self.preview_older_button.configure(state=tk.DISABLED if disabled or not page.has_older else tk.NORMAL)
        self.preview_newer_button.configure(state=tk.DISABLED if disabled or not page.has_newer else tk.NORMAL)
        self.preview_latest_button.configure(state=tk.DISABLED if disabled or not page.has_newer else tk.NORMAL)
        if not page.entries:
            self.preview_page_var.set("本页无可见正文")
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
            self._show_toast("没有可复制的正文", "请先选择包含正文的预览页。", tone="warning")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._preview_plain_text)
        self.root.update_idletasks()
        self._set_status("已复制当前预览页", tone="success")
        self._show_toast("当前页已复制", "完整导出仍会保留全部对话内容。", tone="success")

    # -------------------------------------------------------------- shortcuts

    def _bind_shortcuts(self):
        # Bind to the main toplevel instead of ``bind_all`` so shortcuts do not
        # steal focus from the key assistant and error dialogs.
        self.root.bind("<Control-f>", lambda _event: self.search_entry.focus_set())
        self.root.bind("<Control-k>", lambda _event: self.search_entry.focus_set())
        self.root.bind("<Control-e>", lambda _event: self._export_selected())
        self.root.bind("<Control-Shift-E>", lambda _event: self._export_all())
        self.root.bind("<Control-Shift-f>", self._focus_preview_find)
        self.root.bind("<F5>", lambda _event: self._reload_current_source())
        self.root.bind("<Escape>", self._handle_escape)
        self.root.bind("<Alt-Left>", lambda _event: self._page_shortcut("older"))
        self.root.bind("<Alt-Right>", lambda _event: self._page_shortcut("newer"))

    def _focus_preview_find(self, _event=None):
        if self._preview_find_placeholder_active:
            self._on_preview_find_focus_in()
        self.preview_find_entry.focus_set()
        self.preview_find_entry.selection_range(0, tk.END)
        return "break"

    def _page_shortcut(self, anchor: str):
        self._request_preview_page(anchor)
        return "break"

    def _handle_escape(self, _event=None):
        if not self._preview_find_placeholder_active:
            self._restore_preview_find_placeholder()
            self.preview_text.focus_set()
            return "break"
        if self._search_query():
            self._clear_search()
            return "break"
        selection = self._real_tree_selection()
        if selection:
            if len(selection) > 1:
                active = self._active_tree_item() or selection[0]
                self.conv_tree.selection_set(active)
                self.conv_tree.focus(active)
                self._on_tree_selection_changed()
            else:
                self.conv_tree.selection_remove(*selection)
                self._show_preview_placeholder()
                self._update_selection_summary()
                self._set_status("已取消选择", tone="info")
            return "break"
        return None

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
            default_label = f"导出全部 {len(self.current_conversations)} 条" if has_items else "导出全部"
            self.batch_button.configure(
                text=(
                    "处理中…"
                    if self._export_running
                    else (f"导出选中 {len(picked)} 条" if picked else default_label)
                ),
                state=tk.NORMAL if has_items and not self._export_running else tk.DISABLED,
            )
        if hasattr(self, "refresh_button"):
            self.refresh_button.configure(
                state=tk.NORMAL if self.current_adapter is not None and not busy else tk.DISABLED
            )
        self._update_selection_summary()

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
        self._restore_preview_find_placeholder()

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
        self._restore_preview_find_placeholder()

    # -------------------------------------------------------------- export flow

    def _begin_export_activity(self, status: str):
        self._export_running = True
        self._set_status(status, tone="info")
        self._set_busy_progress(True)
        self._sync_action_states()

    def _finish_export_activity(self):
        self._export_running = False
        self._set_busy_progress(self._preview_page_busy)
        self._sync_action_states()

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
        self._show_toast(
            "导出完成",
            f"{os.path.basename(path)} · {self._human_file_size(size)} · {elapsed:.1f} 秒",
            tone="success",
            duration=3600,
        )

    def _on_batch_export_complete(self, count, output_dir: str, failures=None):
        super()._on_batch_export_complete(count, output_dir, failures)
        failed = len(failures or [])
        detail = f"成功 {count} 条"
        if failed:
            detail += f" · 跳过 {failed} 条"
        self._show_toast("批量导出完成", detail, tone="warning" if failed else "success", duration=4200)

    def _on_single_export_failed(self, title, exporter_label, path, error, details):
        super()._on_single_export_failed(title, exporter_label, path, error, details)
        self._show_toast("导出没有完成", "原始对话与已有文件未被修改。", tone="danger", duration=4200)

    def _on_batch_export_failed(self, error: str, details: str = "", output_dir: str = ""):
        super()._on_batch_export_failed(error, details, output_dir)
        self._show_toast("批量导出没有完成", "已成功写入的文件仍会保留。", tone="danger", duration=4200)

    def _on_close(self):
        if self._toast_after_id:
            try:
                self.root.after_cancel(self._toast_after_id)
            except tk.TclError:
                pass
        self._hide_toast()
        if self._tasks:
            self._tasks.shutdown(wait=False)
        self._repository.invalidate()
        super()._on_close()


def run():
    app = ChatExporterGUI()
    app.run()

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, List, Optional, Tuple

from .adapters.marvis import MarvisAdapter
from .adapters.qclaw import QClawAdapter
from .adapters.qoderwork import QoderWorkAdapter
from .adapters.trae_optimized import TraeAdapter
from .adapters.workbuddy import WorkBuddyAdapter
from .markdown_exporter import MarkdownExporter
from .models import AppInfo, Conversation, Role
from .ui_theme import FONT_LATIN, FONT_MONO, FONT_UI, Metrics, Palette, configure_styles, enable_windows_dpi_awareness, place_centered


class ChatExporterGUI:
    """Modern local-first conversation archive UI."""

    PREVIEW_MAX_CHARS = 350_000
    PREVIEW_PART_MAX_CHARS = 80_000
    TREE_INSERT_BATCH_SIZE = 220
    SEARCH_DEBOUNCE_MS = 180
    UI_QUEUE_POLL_MS = 25

    APP_INITIALS = {
        "trae": "TR",
        "qoderwork": "QW",
        "workbuddy": "WB",
        "qclaw": "QC",
        "marvis": "MV",
    }
    APP_ACCENTS = {
        "trae": "#635BFF",
        "qoderwork": "#2E90FA",
        "workbuddy": "#12B76A",
        "qclaw": "#F79009",
        "marvis": "#E04F9B",
    }

    def __init__(self):
        enable_windows_dpi_awareness()
        self.root = tk.Tk()
        self.root.title("ChatExporter · Local Conversation Studio")
        self.root.geometry("1440x900")
        self.root.minsize(1120, 700)
        self.root.configure(bg=Palette.WINDOW)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.style = configure_styles(self.root)

        self.adapters = [
            TraeAdapter(),
            QoderWorkAdapter(),
            WorkBuddyAdapter(),
            QClawAdapter(),
            MarvisAdapter(),
        ]
        self.current_adapter = None
        self.current_conversations: List[Conversation] = []
        self.selected_conv: Optional[Conversation] = None
        self.exporter = MarkdownExporter(include_metadata=True, include_timestamp=True, include_thinking=True)

        self._load_generation = 0
        self._preview_generation = 0
        self._tree_render_generation = 0
        self._filter_after_id = None
        self._tree_conv_map: Dict[str, Conversation] = {}
        self._app_infos: Dict[str, AppInfo] = {}
        self._nav_rows: Dict[str, Dict[str, tk.Widget]] = {}
        self._search_placeholder_active = True
        self._key_extract_running = False
        self._key_cancel_event: Optional[threading.Event] = None
        self._key_dialog = None
        self._key_dialog_widgets: Dict[str, tk.Widget] = {}
        self._closed = False
        self._ui_queue: "queue.Queue[Tuple[Callable, tuple, dict]]" = queue.Queue()

        self._build_shell()
        self._bind_shortcuts()
        self.root.after(self.UI_QUEUE_POLL_MS, self._drain_ui_queue)
        self.root.after(80, self._detect_apps)

    # ========== Thread-safe UI dispatch ==========

    def _post_ui(self, callback: Callable, *args, **kwargs):
        if not self._closed:
            self._ui_queue.put((callback, args, kwargs))

    def _drain_ui_queue(self):
        if self._closed:
            return
        try:
            while True:
                callback, args, kwargs = self._ui_queue.get_nowait()
                try:
                    callback(*args, **kwargs)
                except tk.TclError:
                    pass
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.root.after(self.UI_QUEUE_POLL_MS, self._drain_ui_queue)

    # ========== Shell ==========

    def _build_shell(self):
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, minsize=Metrics.SIDEBAR_WIDTH, weight=0)
        self.root.grid_columnconfigure(1, weight=1)

        sidebar = ttk.Frame(self.root, style="Sidebar.TFrame", width=Metrics.SIDEBAR_WIDTH)
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
            brand, text="CE", width=3, height=1,
            bg=Palette.ACCENT, fg="#FFFFFF",
            font=(FONT_LATIN, 13, "bold"),
            relief="flat", bd=0, padx=8, pady=8,
        )
        logo.pack(side=tk.LEFT)
        brand_text = ttk.Frame(brand, style="Sidebar.TFrame")
        brand_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(11, 0))
        ttk.Label(brand_text, text="CHAT EXPORTER", style="Brand.TLabel").pack(anchor=tk.W)
        ttk.Label(brand_text, text="Local conversation studio", style="BrandSub.TLabel").pack(anchor=tk.W, pady=(2, 0))

        section = ttk.Frame(parent, style="Sidebar.TFrame")
        section.grid(row=1, column=0, sticky="ew", padx=14)
        ttk.Label(section, text="SOURCES", style="SidebarSection.TLabel").pack(anchor=tk.W, padx=6, pady=(0, 8))

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
        tk.Label(top, text="LOCAL ONLY", bg=Palette.SIDEBAR_RAISED, fg=Palette.TEXT_ON_DARK,
                 font=(FONT_LATIN, 9, "bold")).pack(side=tk.LEFT)
        tk.Label(
            privacy,
            text="Files stay on this device.\nNo cloud upload.",
            justify=tk.LEFT,
            bg=Palette.SIDEBAR_RAISED,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 8),
        ).pack(anchor=tk.W, pady=(5, 0))

        self.detect_button = tk.Button(
            footer,
            text="Refresh sources",
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
        header = ttk.Frame(parent, style="Surface.TFrame", height=Metrics.HEADER_HEIGHT)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(0, weight=1)

        left = ttk.Frame(header, style="Surface.TFrame")
        left.grid(row=0, column=0, sticky="w", padx=(Metrics.PAD_X, 12), pady=15)
        self.page_title_var = tk.StringVar(value="Conversation Library")
        self.page_subtitle_var = tk.StringVar(value="Choose a local source to begin")
        ttk.Label(left, textvariable=self.page_title_var, style="PageTitle.TLabel").pack(anchor=tk.W)
        subline = ttk.Frame(left, style="Surface.TFrame")
        subline.pack(anchor=tk.W, pady=(4, 0))
        self.source_badge = tk.Label(
            subline,
            text="NO SOURCE",
            bg=Palette.SURFACE_ALT,
            fg=Palette.TEXT_MUTED,
            font=(FONT_LATIN, 8, "bold"),
            padx=8,
            pady=3,
        )
        self.source_badge.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(subline, textvariable=self.page_subtitle_var, style="PageSub.TLabel").pack(side=tk.LEFT)

        actions = ttk.Frame(header, style="Surface.TFrame")
        actions.grid(row=0, column=1, sticky="e", padx=(12, Metrics.PAD_X), pady=16)
        self.refresh_button = ttk.Button(actions, text="Refresh", style="Secondary.TButton", command=self._reload_current_source)
        self.refresh_button.pack(side=tk.LEFT, padx=(0, 8))
        self.key_button = ttk.Button(actions, text="TRAE Key Assistant", style="AccentSoft.TButton", command=self._open_key_assistant)
        self.key_button.pack(side=tk.LEFT, padx=(0, 8))
        self.batch_button = ttk.Button(actions, text="Export all", style="Secondary.TButton", command=self._export_all)
        self.batch_button.pack(side=tk.LEFT, padx=(0, 8))
        self.export_button = ttk.Button(actions, text="Export selected", style="Primary.TButton", command=self._export_selected)
        self.export_button.pack(side=tk.LEFT)

        separator = tk.Frame(parent, bg=Palette.BORDER, height=1)
        separator.grid(row=0, column=0, sticky="sew")

    def _build_workspace(self, parent):
        container = ttk.Frame(parent, style="App.TFrame")
        container.grid(row=1, column=0, sticky="nsew", padx=Metrics.PAD_X, pady=(Metrics.PAD_Y, 12))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        paned = ttk.PanedWindow(container, orient=tk.HORIZONTAL, style="Modern.TPanedwindow")
        paned.grid(row=0, column=0, sticky="nsew")

        library = ttk.Frame(paned, style="Card.TFrame", width=430)
        preview = ttk.Frame(paned, style="Card.TFrame")
        paned.add(library, weight=1)
        paned.add(preview, weight=2)

        self._build_library_card(library)
        self._build_preview_card(preview)

    def _build_library_card(self, parent):
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self._apply_card_border(parent)

        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(16, 10))
        header.grid_columnconfigure(0, weight=1)
        ttk.Label(header, text="Conversations", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        self.library_count_var = tk.StringVar(value="0 items")
        ttk.Label(header, textvariable=self.library_count_var, style="Muted.TLabel").grid(row=0, column=1, sticky="e")

        search_wrap = tk.Frame(parent, bg=Palette.SURFACE_ALT, highlightbackground=Palette.BORDER,
                               highlightthickness=1, bd=0, padx=10, pady=2)
        search_wrap.grid(row=1, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(0, 12))
        tk.Label(search_wrap, text="FIND", bg=Palette.SURFACE_ALT, fg=Palette.TEXT_DISABLED,
                 font=(FONT_LATIN, 8, "bold")).pack(side=tk.LEFT, padx=(1, 9))
        self.search_var = tk.StringVar(value="Search titles...")
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
        self.conv_tree.heading("title", text="TITLE")
        self.conv_tree.heading("date", text="UPDATED")
        self.conv_tree.heading("messages", text="MSGS")
        self.conv_tree.column("title", width=230, minwidth=150)
        self.conv_tree.column("date", width=122, minwidth=104, stretch=False, anchor=tk.W)
        self.conv_tree.column("messages", width=58, minwidth=50, stretch=False, anchor=tk.CENTER)
        self.conv_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(tree_wrap, orient=tk.VERTICAL, command=self.conv_tree.yview, style="Modern.Vertical.TScrollbar")
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
        self.library_footer_var = tk.StringVar(value="Select a source from the sidebar")
        ttk.Label(footer, textvariable=self.library_footer_var, style="Muted.TLabel").pack(side=tk.LEFT)
        ttk.Label(footer, text="Ctrl+F to search", style="Muted.TLabel").pack(side=tk.RIGHT)

    def _build_preview_card(self, parent):
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)
        self._apply_card_border(parent)

        header = ttk.Frame(parent, style="Surface.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=Metrics.CARD_PAD, pady=(16, 12))
        header.grid_columnconfigure(0, weight=1)

        title_wrap = ttk.Frame(header, style="Surface.TFrame")
        title_wrap.grid(row=0, column=0, sticky="w")
        self.preview_title_var = tk.StringVar(value="Preview")
        self.preview_meta_var = tk.StringVar(value="Select a conversation to inspect its full local record")
        ttk.Label(title_wrap, textvariable=self.preview_title_var, style="CardTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(title_wrap, textvariable=self.preview_meta_var, style="Muted.TLabel").pack(anchor=tk.W, pady=(4, 0))

        self.preview_source_badge = tk.Label(
            header,
            text="LOCAL",
            bg=Palette.SUCCESS_SOFT,
            fg=Palette.SUCCESS,
            font=(FONT_LATIN, 8, "bold"),
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
            padx=24,
            pady=20,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0,
            spacing1=2,
            spacing3=2,
        )
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        v_scroll = ttk.Scrollbar(text_wrap, orient=tk.VERTICAL, command=self.preview_text.yview, style="Modern.Vertical.TScrollbar")
        h_scroll = ttk.Scrollbar(text_wrap, orient=tk.HORIZONTAL, command=self.preview_text.xview, style="Modern.Horizontal.TScrollbar")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        self.preview_text.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        self._setup_text_tags()
        self._show_preview_placeholder()

    @staticmethod
    def _apply_card_border(frame):
        try:
            frame.configure(relief="solid", borderwidth=1)
        except tk.TclError:
            pass

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
        self.status_var = tk.StringVar(value="Initializing local workspace...")
        ttk.Label(bar, textvariable=self.status_var, style="StatusBar.TLabel").grid(row=1, column=1, sticky="w", pady=(9, 0))
        self.progress = ttk.Progressbar(bar, mode="determinate", length=170, style="Brand.Horizontal.TProgressbar")
        self.progress.grid(row=1, column=2, padx=(12, 10), pady=(12, 0), sticky="e")
        ttk.Label(bar, text="LOCAL · PRIVATE", style="StatusBar.TLabel").grid(
            row=1, column=3, padx=(0, Metrics.PAD_X), pady=(9, 0), sticky="e"
        )

    # ========== Visual helpers ==========

    def _set_status(self, text: str, progress: int = -1, tone: str = "info"):
        self.status_var.set(text)
        colors = {
            "info": Palette.INFO,
            "success": Palette.SUCCESS,
            "warning": Palette.WARNING,
            "danger": Palette.DANGER,
        }
        self.status_dot.configure(bg=colors.get(tone, Palette.INFO))
        if progress >= 0:
            self.progress["value"] = progress

    def _set_busy_progress(self, busy: bool):
        try:
            if busy:
                self.progress.configure(mode="indeterminate")
                self.progress.start(12)
            else:
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self.progress["value"] = 0
        except tk.TclError:
            pass

    def _sync_action_states(self):
        has_source = self.current_adapter is not None
        has_conversations = bool(self.current_conversations)
        has_selection = self.selected_conv is not None
        is_trae = bool(has_source and getattr(self.current_adapter, "name", "") == "trae")
        self.refresh_button.configure(state=tk.NORMAL if has_source else tk.DISABLED)
        self.key_button.configure(state=tk.NORMAL if is_trae and not self._key_extract_running else tk.DISABLED)
        self.batch_button.configure(state=tk.NORMAL if has_conversations else tk.DISABLED)
        self.export_button.configure(state=tk.NORMAL if has_selection else tk.DISABLED)

    # ========== App detection / navigation ==========

    def _detect_apps(self):
        self.detect_button.configure(state=tk.DISABLED, text="Refreshing...")
        self._set_status("Scanning local application data paths...", tone="info")
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
            text="Scanning local sources...",
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
        self.detect_button.configure(state=tk.NORMAL, text="Refresh sources")
        self._set_status(f"{available_count} local sources ready", tone="success" if available_count else "warning")
        self._sync_action_states()

    def _add_nav_row(self, adapter, info: AppInfo, available: bool):
        name = adapter.name
        accent = self.APP_ACCENTS.get(name, Palette.ACCENT)
        row = tk.Frame(self.app_list_frame, bg=Palette.SIDEBAR, bd=0, padx=0, pady=2)
        row.pack(fill=tk.X, pady=2)

        bar = tk.Frame(row, bg=Palette.SIDEBAR, width=3)
        bar.pack(side=tk.LEFT, fill=tk.Y)
        bar.pack_propagate(False)
        body = tk.Frame(row, bg=Palette.SIDEBAR, cursor="hand2" if available else "arrow", padx=8, pady=8)
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
        title = tk.Label(labels, text=info.display_name, anchor=tk.W, bg=Palette.SIDEBAR,
                         fg=Palette.TEXT_ON_DARK if available else "#64748B", font=(FONT_UI, 9, "bold"))
        title.pack(fill=tk.X)
        meta = tk.Label(labels, text="Ready" if available else "Not detected", anchor=tk.W,
                        bg=Palette.SIDEBAR, fg=Palette.TEXT_ON_DARK_MUTED if available else "#475569",
                        font=(FONT_UI, 8))
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
            "row": row, "bar": bar, "body": body, "avatar": avatar,
            "labels": labels, "title": title, "meta": meta, "status": status,
            "accent": accent, "available": available,
        }

    def _set_nav_hover(self, name: str, hovered: bool):
        row = self._nav_rows.get(name)
        if not row or not row["available"]:
            return
        selected = bool(self.current_adapter and self.current_adapter.name == name)
        bg = Palette.SIDEBAR_RAISED if selected else (Palette.SIDEBAR_HOVER if hovered else Palette.SIDEBAR)
        for key in ("row", "body", "labels", "title", "meta"):
            row[key].configure(bg=bg)
        row["avatar"].configure(bg=Palette.SIDEBAR_HOVER if selected else Palette.SIDEBAR_RAISED)

    def _select_app(self, adapter):
        self.current_adapter = adapter
        for name, row in self._nav_rows.items():
            selected = name == adapter.name
            bg = Palette.SIDEBAR_RAISED if selected else Palette.SIDEBAR
            row["bar"].configure(bg=row["accent"] if selected else Palette.SIDEBAR)
            for key in ("row", "body", "labels", "title", "meta"):
                row[key].configure(bg=bg)
            row["avatar"].configure(bg=Palette.SIDEBAR_HOVER if selected else Palette.SIDEBAR_RAISED)
            row["title"].configure(fg=Palette.TEXT_ON_DARK if row["available"] else "#64748B")

        self.selected_conv = None
        self.current_conversations = []
        self.preview_title_var.set("Preview")
        self.preview_meta_var.set("Select a conversation to inspect its full local record")
        self._show_preview_placeholder()
        self.page_title_var.set(adapter.display_name)
        self.page_subtitle_var.set("Reading local conversation history")
        self.source_badge.configure(
            text=adapter.name.upper(),
            bg=Palette.ACCENT_SOFT,
            fg=Palette.ACCENT_PRESSED,
        )
        self.preview_source_badge.configure(text=adapter.name.upper())
        self._show_center_loading(f"Loading {adapter.display_name}...")
        self._sync_action_states()
        self._load_generation += 1
        self._load_conversations(self._load_generation)

    def _reload_current_source(self):
        if not self.current_adapter:
            return
        if hasattr(self.current_adapter, "reset_runtime_cache"):
            self.current_adapter.reset_runtime_cache()
        elif hasattr(self.current_adapter, "_cached_conversations"):
            self.current_adapter._cached_conversations = None
        self._show_center_loading(f"Refreshing {self.current_adapter.display_name}...")
        self._load_generation += 1
        self._load_conversations(self._load_generation)

    # ========== Loading / filtering ==========

    def _show_center_loading(self, text: str):
        self._tree_conv_map.clear()
        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)
        self.conv_tree.insert("", tk.END, iid="__loading__", values=(text, "", ""), tags=("loading",))
        self.library_count_var.set("Loading")
        self.library_footer_var.set(text)
        self._set_status(text, tone="info")

    def _load_conversations(self, generation: int):
        adapter = self.current_adapter
        if not adapter:
            return

        def worker():
            try:
                conversations = adapter.list_conversations()
                self._post_ui(self._on_conversations_loaded, conversations, generation)
            except Exception as exc:
                self._post_ui(self._on_conversations_failed, str(exc), generation)

        threading.Thread(target=worker, daemon=True, name=f"load-{adapter.name}").start()

    def _on_conversations_failed(self, error: str, generation: int):
        if generation != self._load_generation:
            return
        self.current_conversations = []
        self._tree_conv_map.clear()
        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)
        self.conv_tree.insert("", tk.END, iid="__error__", values=("Unable to load source", error[:80], ""), tags=("error",))
        self.library_count_var.set("0 items")
        self.library_footer_var.set("Source returned an error")
        self._set_status(f"Load failed: {error}", tone="danger")
        self._sync_action_states()

    def _on_conversations_loaded(self, conversations, generation: int):
        if generation != self._load_generation:
            return
        self.current_conversations = conversations
        self.library_count_var.set(f"{len(conversations)} items")
        self.library_footer_var.set("Loaded from local storage")
        self.page_subtitle_var.set(f"{len(conversations)} conversations available locally")
        self._filter_conversations()
        tone = "success" if conversations else "warning"
        self._set_status(f"Loaded {len(conversations)} conversations", tone=tone)
        self._sync_action_states()

    def _on_search_focus_in(self, _event):
        if self._search_placeholder_active:
            self._search_placeholder_active = False
            self.search_var.set("")
            self.search_entry.configure(fg=Palette.TEXT)

    def _on_search_focus_out(self, _event):
        if not self.search_var.get().strip():
            self._search_placeholder_active = True
            self.search_var.set("Search titles...")
            self.search_entry.configure(fg=Palette.TEXT_DISABLED)

    def _schedule_filter(self):
        if self._filter_after_id:
            try:
                self.root.after_cancel(self._filter_after_id)
            except tk.TclError:
                pass
        self._filter_after_id = self.root.after(self.SEARCH_DEBOUNCE_MS, self._filter_conversations)

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
            f"{len(matches)} / {len(self.current_conversations)}" if search else f"{len(self.current_conversations)} items"
        )

        if not matches:
            text = "No matching conversations" if search else "No conversations found"
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
            title = conv.title or "(Untitled conversation)"
            if len(title) > 62:
                title = title[:59] + "..."
            item_id = f"conv_{source_index}"
            self._tree_conv_map[item_id] = conv
            tag = "even" if display_index % 2 == 0 else "odd"
            self.conv_tree.insert("", tk.END, iid=item_id, values=(title, updated, count), tags=(tag,))
        if end < len(matches):
            self.root.after(1, lambda: self._insert_tree_batch(matches, end, generation))

    # ========== Preview ==========

    def _setup_text_tags(self):
        t = self.preview_text
        t.tag_configure("heading1", font=(FONT_UI, 18, "bold"), foreground=Palette.TEXT, spacing3=12)
        t.tag_configure("meta", foreground=Palette.TEXT_MUTED, font=(FONT_UI, 9), spacing3=4)
        t.tag_configure("user_header", font=(FONT_UI, 10, "bold"), foreground=Palette.ACCENT_PRESSED, spacing1=8, spacing3=5)
        t.tag_configure("user_body", foreground=Palette.TEXT_SECONDARY, lmargin1=18, lmargin2=18, spacing3=6)
        t.tag_configure("assistant_header", font=(FONT_UI, 10, "bold"), foreground=Palette.SUCCESS, spacing1=8, spacing3=5)
        t.tag_configure("assistant_body", foreground=Palette.TEXT_SECONDARY, lmargin1=18, lmargin2=18, spacing3=6)
        t.tag_configure("tool_header", font=(FONT_UI, 10, "bold"), foreground=Palette.WARNING, spacing1=8, spacing3=5)
        t.tag_configure("tool_body", foreground=Palette.TEXT_SECONDARY, lmargin1=18, lmargin2=18, spacing3=6)
        t.tag_configure("system_header", font=(FONT_UI, 10, "bold"), foreground=Palette.INFO, spacing1=8, spacing3=5)
        t.tag_configure("system_body", foreground=Palette.TEXT_MUTED, lmargin1=18, lmargin2=18, spacing3=6)
        t.tag_configure("code", font=(FONT_MONO, 9), background=Palette.CODE_BG, foreground=Palette.TEXT, lmargin1=18, lmargin2=18, spacing1=6, spacing3=6)
        t.tag_configure("separator", foreground=Palette.BORDER, spacing1=6, spacing3=6)
        t.tag_configure("empty_title", font=(FONT_UI, 16, "bold"), foreground=Palette.TEXT, justify=tk.CENTER)
        t.tag_configure("empty_body", font=(FONT_UI, 10), foreground=Palette.TEXT_MUTED, justify=tk.CENTER)

    def _show_preview_placeholder(self):
        self.preview_text.configure(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert(tk.END, "\n\n\n\n")
        self.preview_text.insert(tk.END, "A clean view of your local conversations\n", "empty_title")
        self.preview_text.insert(tk.END, "\nChoose a source, then select a conversation from the library.\n", "empty_body")
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
        self.preview_title_var.set(conv.title or "Untitled conversation")
        self.preview_meta_var.set("Loading full conversation from local storage...")
        self._set_status("Loading conversation preview...", tone="info")
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
                self._post_ui(self._set_status, f"Preview failed: {exc}", -1, "danger")

        threading.Thread(target=worker, daemon=True, name="preview-load").start()

    def _show_preview(self, markdown: str, conv: Conversation, generation: int):
        if generation != self._preview_generation:
            return
        self.selected_conv = conv
        self.preview_title_var.set(conv.title or "Untitled conversation")
        updated = conv.updated_at.strftime("%Y-%m-%d %H:%M") if conv.updated_at else "Unknown time"
        self.preview_meta_var.set(f"{conv.source_app} · {len(conv.messages)} messages · Updated {updated}")
        self.preview_text.configure(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        self._preview_chars = 0
        self._preview_truncated = False
        if conv.messages:
            self._render_colored_preview(conv)
        else:
            self._preview_insert(markdown, None)
        self.preview_text.configure(state=tk.DISABLED)
        suffix = " · preview truncated; export remains complete" if self._preview_truncated else ""
        self._set_status(f"Preview ready: {len(conv.messages)} messages{suffix}", tone="success")
        self._sync_action_states()

    def _preview_insert(self, text: str, tag=None) -> bool:
        if not text:
            return True
        remaining = self.PREVIEW_MAX_CHARS - self._preview_chars
        if remaining <= 0:
            if not self._preview_truncated:
                self.preview_text.insert(tk.END, "\n\n[Preview truncated. Export still includes the full record.]\n", "system_body")
                self._preview_truncated = True
            return False
        if len(text) > remaining:
            self.preview_text.insert(tk.END, text[:remaining], tag)
            self.preview_text.insert(tk.END, "\n\n[Preview truncated. Export still includes the full record.]\n", "system_body")
            self._preview_chars = self.PREVIEW_MAX_CHARS
            self._preview_truncated = True
            return False
        self.preview_text.insert(tk.END, text, tag)
        self._preview_chars += len(text)
        return True

    def _preview_part(self, text: str) -> str:
        if not text:
            return ""
        if len(text) > self.PREVIEW_PART_MAX_CHARS:
            self._preview_truncated = True
            return text[:self.PREVIEW_PART_MAX_CHARS] + "\n\n[This section was truncated in preview.]"
        return text

    def _render_colored_preview(self, conv: Conversation):
        metadata = [
            f"# {conv.title}", "",
            f"Source: {conv.source_app}",
            f"Created: {conv.created_at.strftime('%Y-%m-%d %H:%M:%S') if conv.created_at else 'N/A'}",
            f"Updated: {conv.updated_at.strftime('%Y-%m-%d %H:%M:%S') if conv.updated_at else 'N/A'}",
            f"Messages: {len(conv.messages)}", "", "────────────────────────────────────────", "",
        ]
        if not self._preview_insert("\n".join(metadata), "meta"):
            return

        for msg in conv.messages:
            role_name, header_tag, body_tag = self._role_to_tags(msg.role)
            timestamp = msg.timestamp.strftime("%Y-%m-%d %H:%M:%S") if msg.timestamp else ""
            header = role_name
            if timestamp:
                header += f"  ·  {timestamp}"
            if msg.model:
                header += f"  ·  {msg.model}"
            if not self._preview_insert(header + "\n", header_tag):
                return
            content = self._preview_part(msg.content or "")
            if content and not self._preview_insert(content + "\n\n", body_tag):
                return

            for part in msg.parts:
                part_type = part.type.value if hasattr(part.type, "value") else str(part.type)
                if part_type == "tool_call":
                    if not self._preview_insert(f"Tool call · {part.tool_name or 'Unknown'}\n", "tool_header"):
                        return
                    if not self._preview_insert(self._preview_part(part.tool_input or "") + "\n\n", "tool_body"):
                        return
                elif part_type == "tool_result":
                    if not self._preview_insert(f"Tool result · {part.tool_name or 'Unknown'}\n", "tool_header"):
                        return
                    if not self._preview_insert(self._preview_part(part.tool_output or part.content or "") + "\n\n", "tool_body"):
                        return
                elif part_type == "thinking":
                    if not self._preview_insert("Thinking\n", "system_header"):
                        return
                    if not self._preview_insert(self._preview_part(part.content or "") + "\n\n", "system_body"):
                        return
                elif part_type == "code":
                    language = part.language or ""
                    if not self._preview_insert(f"{language}\n{self._preview_part(part.content or '')}\n\n", "code"):
                        return
                elif part_type in ("file", "image"):
                    name = part.file_name or part.content or "Attachment"
                    if not self._preview_insert(f"Attachment · {name}\n\n", "system_body"):
                        return
            if not self._preview_insert("────────────────────────────────────────\n\n", "separator"):
                return

    @staticmethod
    def _role_to_tags(role):
        return {
            Role.USER: ("USER", "user_header", "user_body"),
            Role.ASSISTANT: ("ASSISTANT", "assistant_header", "assistant_body"),
            Role.TOOL: ("TOOL", "tool_header", "tool_body"),
            Role.SYSTEM: ("SYSTEM", "system_header", "system_body"),
        }.get(role, ("UNKNOWN", "system_header", "system_body"))

    def _render_markdown(self, conv: Conversation) -> str:
        self.exporter.include_thinking = True
        return self.exporter.export(conv)

    # ========== Key assistant ==========

    def _open_key_assistant(self):
        adapter = self.current_adapter
        if not adapter or getattr(adapter, "name", "") != "trae":
            messagebox.showwarning("TRAE Key Assistant", "Select TRAE SOLO CN from the sidebar first.")
            return
        if self._key_dialog and self._key_dialog.winfo_exists():
            self._key_dialog.lift()
            return

        dialog = tk.Toplevel(self.root)
        self._key_dialog = dialog
        dialog.title("TRAE Key Assistant")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=Palette.WINDOW)
        place_centered(dialog, self.root, 700, 500)
        dialog.protocol("WM_DELETE_WINDOW", self._close_key_dialog)

        hero = tk.Frame(dialog, bg=Palette.SIDEBAR, padx=24, pady=20)
        hero.pack(fill=tk.X)
        tk.Label(hero, text="TRAE KEY ASSISTANT", bg=Palette.SIDEBAR, fg=Palette.TEXT_ON_DARK_MUTED,
                 font=(FONT_LATIN, 8, "bold")).pack(anchor=tk.W)
        tk.Label(hero, text="Unlock the complete local archive", bg=Palette.SIDEBAR, fg=Palette.TEXT_ON_DARK,
                 font=(FONT_UI, 17, "bold")).pack(anchor=tk.W, pady=(5, 3))
        tk.Label(
            hero,
            text="A bounded, user-authorized scan of the running TRAE process. Nothing leaves this computer.",
            bg=Palette.SIDEBAR,
            fg=Palette.TEXT_ON_DARK_MUTED,
            font=(FONT_UI, 9),
            wraplength=630,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        body = tk.Frame(dialog, bg=Palette.WINDOW, padx=24, pady=20)
        body.pack(fill=tk.BOTH, expand=True)

        checklist = tk.Frame(body, bg=Palette.SURFACE, highlightbackground=Palette.BORDER, highlightthickness=1,
                             padx=16, pady=14)
        checklist.pack(fill=tk.X)
        tk.Label(checklist, text="BEFORE YOU START", bg=Palette.SURFACE, fg=Palette.TEXT_MUTED,
                 font=(FONT_LATIN, 8, "bold")).pack(anchor=tk.W)
        for index, text in enumerate((
            "TRAE SOLO CN is running",
            "At least one conversation is open",
            "The scan is limited to TRAE private memory (8 seconds / 300MB)",
        ), start=1):
            line = tk.Frame(checklist, bg=Palette.SURFACE)
            line.pack(fill=tk.X, pady=(8 if index == 1 else 5, 0))
            tk.Label(line, text=str(index), width=2, bg=Palette.ACCENT_SOFT, fg=Palette.ACCENT_PRESSED,
                     font=(FONT_LATIN, 8, "bold"), padx=3, pady=2).pack(side=tk.LEFT)
            tk.Label(line, text=text, bg=Palette.SURFACE, fg=Palette.TEXT_SECONDARY,
                     font=(FONT_UI, 9)).pack(side=tk.LEFT, padx=(9, 0))

        progress_card = tk.Frame(body, bg=Palette.SURFACE, highlightbackground=Palette.BORDER,
                                 highlightthickness=1, padx=16, pady=14)
        progress_card.pack(fill=tk.X, pady=(14, 0))
        self._key_dialog_widgets["stage"] = tk.Label(
            progress_card, text="READY", bg=Palette.SURFACE, fg=Palette.ACCENT,
            font=(FONT_LATIN, 8, "bold")
        )
        self._key_dialog_widgets["stage"].pack(anchor=tk.W)
        self._key_dialog_widgets["status"] = tk.Label(
            progress_card, text="Start when TRAE is ready.", bg=Palette.SURFACE, fg=Palette.TEXT,
            font=(FONT_UI, 11, "bold"), wraplength=620, justify=tk.LEFT
        )
        self._key_dialog_widgets["status"].pack(anchor=tk.W, pady=(5, 3))
        self._key_dialog_widgets["detail"] = tk.Label(
            progress_card, text="The assistant first checks environment/cache, then scans only when needed.",
            bg=Palette.SURFACE, fg=Palette.TEXT_MUTED, font=(FONT_UI, 9), wraplength=620, justify=tk.LEFT
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
        self._key_dialog_widgets["start"] = ttk.Button(buttons, text="Start secure scan", style="Primary.TButton", command=self._start_key_scan)
        self._key_dialog_widgets["start"].pack(side=tk.LEFT)
        self._key_dialog_widgets["cancel"] = ttk.Button(buttons, text="Close", style="Secondary.TButton", command=self._close_key_dialog)
        self._key_dialog_widgets["cancel"].pack(side=tk.RIGHT)

    def _start_key_scan(self):
        if self._key_extract_running or not self.current_adapter:
            return
        self._key_extract_running = True
        self._key_cancel_event = threading.Event()
        self._sync_action_states()
        self._key_dialog_widgets["start"].configure(state=tk.DISABLED)
        self._key_dialog_widgets["cancel"].configure(text="Cancel scan", command=self._cancel_key_scan)
        self._key_dialog_widgets["stage"].configure(text="SCANNING", fg=Palette.INFO)
        self._key_dialog_widgets["status"].configure(text="Preparing secure local scan...")
        self._key_dialog_widgets["detail"].configure(text="Keep TRAE open. You can cancel at any time.")
        progress = self._key_dialog_widgets["progress"]
        progress.configure(mode="indeterminate")
        progress.start(12)
        self._set_busy_progress(True)
        self._set_status("TRAE Key Assistant is scanning local process memory...", tone="info")
        adapter = self.current_adapter

        def progress_callback(event: dict):
            self._post_ui(self._update_key_scan_progress, event)

        def worker():
            try:
                result = adapter.extract_key_for_user(progress_callback=progress_callback, cancel_event=self._key_cancel_event)
            except Exception as exc:
                result = {"ok": False, "reason": str(exc), "hint": "Unexpected scan error."}
            self._post_ui(self._finish_key_scan, result)

        threading.Thread(target=worker, daemon=True, name="trae-key-scan").start()

    def _update_key_scan_progress(self, event: dict):
        if not self._key_dialog or not self._key_dialog.winfo_exists():
            return
        message = event.get("message", "Scanning...")
        stage = event.get("stage", "scan").replace("_", " ").upper()
        self._key_dialog_widgets["stage"].configure(text=stage)
        self._key_dialog_widgets["status"].configure(text=message)
        if "scanned_mb" in event:
            self._key_dialog_widgets["detail"].configure(text=f"Scanned approximately {event['scanned_mb']}MB of bounded TRAE private memory.")
        self._set_status(message, tone="info")

    def _cancel_key_scan(self):
        if self._key_cancel_event:
            self._key_cancel_event.set()
        self._key_dialog_widgets["cancel"].configure(state=tk.DISABLED, text="Cancelling...")
        self._key_dialog_widgets["status"].configure(text="Stopping scan safely...")

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
            self._key_dialog_widgets["stage"].configure(text="CANCELLED" if cancelled else "NOT FOUND", fg=Palette.WARNING)
            self._key_dialog_widgets["status"].configure(text=result.get("reason", "No valid key found"))
            self._key_dialog_widgets["detail"].configure(text=result.get("hint", "Open a TRAE conversation and retry."))
            self._key_dialog_widgets["start"].configure(state=tk.NORMAL, text="Try again")
            self._key_dialog_widgets["cancel"].configure(state=tk.NORMAL, text="Close", command=self._close_key_dialog)
            self._set_status("TRAE key scan cancelled" if cancelled else "TRAE key not found", tone="warning")
            return

        key_hex = result.get("key_hex", "")
        source = result.get("source", "Local")
        elapsed = result.get("elapsed", 0)
        self._key_dialog_widgets["stage"].configure(text="COMPLETE", fg=Palette.SUCCESS)
        self._key_dialog_widgets["status"].configure(text="TRAE key verified and secured locally")
        self._key_dialog_widgets["detail"].configure(text=f"Source: {source} · {elapsed}s. The complete database is reloading now.")
        self._key_dialog_widgets["start"].pack_forget()
        self._key_dialog_widgets["cancel"].configure(state=tk.NORMAL, text="Done", command=self._close_key_dialog)
        self._render_key_result(key_hex)

        adapter = self.current_adapter
        if adapter and getattr(adapter, "name", "") == "trae":
            adapter.reset_runtime_cache()
            self._load_generation += 1
            self._show_center_loading("Key verified. Loading the complete TRAE database...")
            self._load_conversations(self._load_generation)
        self._set_status("TRAE key verified; loading complete archive", tone="success")

    def _render_key_result(self, key_hex: str):
        frame = self._key_dialog_widgets["key_frame"]
        for child in frame.winfo_children():
            child.destroy()
        frame.pack(fill=tk.X, pady=(14, 0))
        card = tk.Frame(frame, bg=Palette.SUCCESS_SOFT, highlightbackground="#ABEFC6", highlightthickness=1,
                        padx=14, pady=12)
        card.pack(fill=tk.X)
        tk.Label(card, text="VERIFIED KEY", bg=Palette.SUCCESS_SOFT, fg=Palette.SUCCESS,
                 font=(FONT_LATIN, 8, "bold")).pack(anchor=tk.W)
        key_var = self._key_dialog_widgets["key_var"]
        key_var.set(key_hex)
        entry = tk.Entry(card, textvariable=key_var, show="*", state="readonly",
                         readonlybackground=Palette.SUCCESS_SOFT, fg=Palette.TEXT,
                         font=(FONT_MONO, 9), relief=tk.FLAT, bd=0)
        entry.pack(fill=tk.X, pady=(7, 8))
        row = tk.Frame(card, bg=Palette.SUCCESS_SOFT)
        row.pack(fill=tk.X)
        show_var = tk.BooleanVar(value=False)

        def toggle():
            entry.configure(show="" if show_var.get() else "*")

        ttk.Checkbutton(row, text="Show key", variable=show_var, command=toggle, style="Modern.TCheckbutton").pack(side=tk.LEFT)
        ttk.Button(row, text="Copy", style="Secondary.TButton", command=lambda: self._copy_key(key_hex)).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(row, text="Save to Windows", style="AccentSoft.TButton", command=lambda: self._persist_key(key_hex)).pack(side=tk.RIGHT)

    def _copy_key(self, key_hex: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(key_hex)
        self.root.update_idletasks()
        self._key_dialog_widgets["detail"].configure(text="Key copied to clipboard. Treat it as sensitive data.")

    def _persist_key(self, key_hex: str):
        adapter = self.current_adapter
        if not adapter or not hasattr(adapter, "persist_key_to_user_environment"):
            return
        ok, detail = adapter.persist_key_to_user_environment(key_hex)
        self._key_dialog_widgets["detail"].configure(text=detail)
        self._set_status(detail, tone="success" if ok else "danger")

    def _close_key_dialog(self):
        if self._key_extract_running:
            self._cancel_key_scan()
            return
        if self._key_dialog and self._key_dialog.winfo_exists():
            self._key_dialog.grab_release()
            self._key_dialog.destroy()
        self._key_dialog = None
        self._key_dialog_widgets.clear()

    # ========== Export ==========

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
            filetypes=[("Markdown file", "*.md"), ("All files", "*.*")],
            initialfile=default_name,
            title="Export conversation",
        )
        if not path:
            return
        try:
            self.exporter.include_thinking = True
            self.exporter.export(conv, path)
            self._set_status(f"Exported {os.path.basename(path)}", progress=100, tone="success")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            self._set_status(f"Export failed: {exc}", tone="danger")

    def _export_all(self):
        if not self.current_conversations:
            return
        output_dir = filedialog.askdirectory(title="Choose export folder")
        if not output_dir:
            return
        if not messagebox.askyesno("Export all", f"Export {len(self.current_conversations)} conversations to:\n{output_dir}?"):
            return
        adapter = self.current_adapter
        conversations = list(self.current_conversations)
        self._set_status("Exporting complete conversations...", progress=0, tone="info")
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
            self._post_ui(self._set_status, f"Exporting {index}/{total} · {os.path.basename(path)}", progress, "info")
        return exported

    def _on_batch_export_complete(self, count: int, output_dir: str):
        self._set_status(f"Exported {count} conversations", progress=100, tone="success")
        self._sync_action_states()
        messagebox.showinfo("Export complete", f"Exported {count} Markdown files to:\n{output_dir}")

    def _on_batch_export_failed(self, error: str):
        self._set_status(f"Batch export failed: {error}", tone="danger")
        self._sync_action_states()
        messagebox.showerror("Export failed", error)

    # ========== Shortcuts / lifecycle ==========

    def _bind_shortcuts(self):
        self.root.bind_all("<Control-f>", lambda _e: self.search_entry.focus_set())
        self.root.bind_all("<Control-k>", lambda _e: self.search_entry.focus_set())
        self.root.bind_all("<Control-e>", lambda _e: self._export_selected())
        self.root.bind_all("<Control-Shift-E>", lambda _e: self._export_all())
        self.root.bind_all("<F5>", lambda _e: self._reload_current_source())

    def _on_close(self):
        self._closed = True
        if self._key_cancel_event:
            self._key_cancel_event.set()
        self.root.destroy()

    def run(self):
        self._sync_action_states()
        self.root.mainloop()


def run():
    app = ChatExporterGUI()
    app.run()

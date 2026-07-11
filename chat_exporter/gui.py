import os
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Dict, List, Optional

from .adapters.trae import TraeAdapter, SQLCIPHER_KEY_ENV
from .adapters.qoderwork import QoderWorkAdapter
from .adapters.workbuddy import WorkBuddyAdapter
from .adapters.qclaw import QClawAdapter
from .adapters.marvis import MarvisAdapter
from .models import Conversation, AppInfo, Role
from .markdown_exporter import MarkdownExporter


class ChatExporterGUI:
    """多程序对话导出工具 - GUI 主类"""

    COLOR_BG = "#f8fafc"
    COLOR_PANEL = "#ffffff"
    COLOR_BORDER = "#e2e8f0"
    COLOR_ACCENT = "#2563eb"
    COLOR_ACCENT_LIGHT = "#dbeafe"
    COLOR_TEXT = "#1e293b"
    COLOR_TEXT_MUTED = "#64748b"
    COLOR_TEXT_DISABLED = "#94a3b8"
    COLOR_USER = "#1d4ed8"
    COLOR_ASSISTANT = "#059669"
    COLOR_TOOL = "#d97706"
    COLOR_SYSTEM = "#64748b"
    COLOR_SEARCH_BG = "#f1f5f9"
    COLOR_HOVER = "#f8fafc"
    COLOR_SELECTED = "#dbeafe"

    PREVIEW_MAX_CHARS = 2_000_000
    PREVIEW_PART_MAX_CHARS = 200_000
    TREE_INSERT_BATCH_SIZE = 250
    SEARCH_DEBOUNCE_MS = 180

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("ChatExporter - 多程序对话导出工具")
        self.root.geometry("1280x820")
        self.root.minsize(960, 640)
        self.root.configure(bg=self.COLOR_BG)

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
        self._app_infos = {}
        self._key_extract_running = False
        self.trae_key_button = None

        self._setup_style()
        self._build_ui()
        self.root.after(50, self._detect_apps)

    # ========== 样式 ==========

    def _setup_style(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(".", background=self.COLOR_BG, foreground=self.COLOR_TEXT,
                        font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 13, "bold"),
                        foreground="#0f172a", background=self.COLOR_BG)
        style.configure("Subtitle.TLabel", font=("Microsoft YaHei UI", 9),
                        foreground=self.COLOR_TEXT_MUTED, background=self.COLOR_BG)
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 9),
                        foreground=self.COLOR_TEXT_MUTED, background=self.COLOR_BG)
        style.configure("Panel.TFrame", background=self.COLOR_PANEL)
        style.configure("Bg.TFrame", background=self.COLOR_BG)

        style.configure("TButton", font=("Microsoft YaHei UI", 10),
                        padding=(10, 6), relief="flat", borderwidth=0,
                        background="#e2e8f0", foreground=self.COLOR_TEXT)
        style.map("TButton",
                  background=[("active", "#cbd5e1"), ("pressed", "#94a3b8")])

        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10),
                        padding=(10, 6), relief="flat", borderwidth=0,
                        background=self.COLOR_ACCENT, foreground="#ffffff")
        style.map("Primary.TButton",
                  background=[("active", "#1d4ed8"), ("pressed", "#1e40af")])

        style.configure("Treeview",
                        font=("Microsoft YaHei UI", 10),
                        rowheight=32,
                        background=self.COLOR_PANEL,
                        foreground=self.COLOR_TEXT,
                        fieldbackground=self.COLOR_PANEL,
                        borderwidth=0, relief="flat")
        style.configure("Treeview.Heading",
                        font=("Microsoft YaHei UI", 9, "bold"),
                        background=self.COLOR_SEARCH_BG,
                        foreground="#334155",
                        relief="flat", padding=(8, 6))
        style.map("Treeview",
                  background=[("selected", self.COLOR_SELECTED)],
                  foreground=[("selected", self.COLOR_USER)])

        style.configure("TPanedWindow", background=self.COLOR_BORDER)

    # ========== UI 构建 ==========

    def _build_ui(self):
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        left_frame = ttk.Frame(main_paned, width=250, style="Panel.TFrame")
        center_frame = ttk.Frame(main_paned, width=420)
        right_frame = ttk.Frame(main_paned)

        main_paned.add(left_frame, weight=0)
        main_paned.add(center_frame, weight=1)
        main_paned.add(right_frame, weight=2)

        self._build_left_panel(left_frame)
        self._build_center_panel(center_frame)
        self._build_right_panel(right_frame)
        self._build_status_bar()

    def _build_left_panel(self, parent):
        parent.configure(style="Panel.TFrame")

        header = ttk.Frame(parent, style="Panel.TFrame")
        header.pack(fill=tk.X, padx=14, pady=(14, 10))
        ttk.Label(header, text="ChatExporter", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(header, text="多程序对话导出工具", style="Subtitle.TLabel").pack(anchor=tk.W, pady=(2, 0))

        sep = tk.Frame(parent, height=1, bg=self.COLOR_BORDER)
        sep.pack(fill=tk.X, padx=14, pady=(0, 8))

        self.app_list_frame = ttk.Frame(parent, style="Panel.TFrame")
        self.app_list_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        self.app_buttons = {}
        self.app_status_labels = {}

        sep2 = tk.Frame(parent, height=1, bg=self.COLOR_BORDER)
        sep2.pack(fill=tk.X, padx=14, pady=(8, 8))

        ttk.Button(parent, text="刷新检测", command=self._detect_apps).pack(fill=tk.X, padx=14, pady=(0, 14))

    def _build_center_panel(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(top, text="对话列表", style="Title.TLabel").pack(side=tk.LEFT)
        self.conv_count_label = ttk.Label(top, text="", style="Status.TLabel")
        self.conv_count_label.pack(side=tk.RIGHT)

        search_frame = tk.Frame(parent, bg=self.COLOR_SEARCH_BG, padx=10, pady=8)
        search_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(search_frame, text="搜索", background=self.COLOR_SEARCH_BG).pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._schedule_filter())
        ttk.Entry(search_frame, textvariable=self.search_var,
                  font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.conv_tree = ttk.Treeview(list_frame, columns=("title", "date", "messages"),
                                      show="headings", selectmode="browse")
        self.conv_tree.heading("title", text="标题")
        self.conv_tree.heading("date", text="更新时间")
        self.conv_tree.heading("messages", text="消息数")
        self.conv_tree.column("title", width=200, minwidth=120)
        self.conv_tree.column("date", width=130, minwidth=100, stretch=False)
        self.conv_tree.column("messages", width=60, minwidth=50, stretch=False)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.conv_tree.yview)
        self.conv_tree.configure(yscrollcommand=scrollbar.set)
        self.conv_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.conv_tree.bind("<<TreeviewSelect>>", self._on_conv_select)

        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(btn_frame, text="导出选中", command=self._export_selected,
                   style="Primary.TButton").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(btn_frame, text="批量导出", command=self._export_all).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
        self.trae_key_button = ttk.Button(btn_frame, text="提取 TRAE 密钥", command=self._extract_trae_key)
        self.trae_key_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        self.trae_key_button.configure(state=tk.DISABLED)

    def _build_right_panel(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(top, text="预览", style="Title.TLabel").pack(side=tk.LEFT)
        self.preview_title_label = ttk.Label(top, text="", style="Status.TLabel")
        self.preview_title_label.pack(side=tk.RIGHT)

        preview_frame = tk.Frame(parent, bg=self.COLOR_BORDER, highlightbackground=self.COLOR_BORDER,
                                 highlightthickness=1)
        preview_frame.pack(fill=tk.BOTH, expand=True)

        self.preview_text = tk.Text(
            preview_frame, wrap=tk.WORD, font=("Consolas", 11),
            bg=self.COLOR_PANEL, fg=self.COLOR_TEXT, insertbackground=self.COLOR_TEXT,
            padx=18, pady=18, state=tk.DISABLED, borderwidth=0, highlightthickness=0
        )
        v_scroll = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=self.preview_text.yview)
        h_scroll = ttk.Scrollbar(preview_frame, orient=tk.HORIZONTAL, command=self.preview_text.xview)
        self.preview_text.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        preview_frame.grid_rowconfigure(0, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)

        self._setup_text_tags()
        self._show_preview_placeholder()

    def _setup_text_tags(self):
        t = self.preview_text
        t.tag_configure("heading1", font=("Microsoft YaHei UI", 18, "bold"), foreground="#0f172a", spacing3=10)
        t.tag_configure("meta", foreground=self.COLOR_TEXT_MUTED, font=("Microsoft YaHei UI", 9))
        t.tag_configure("code", font=("Consolas", 10), background=self.COLOR_SEARCH_BG, foreground=self.COLOR_ASSISTANT)
        t.tag_configure("user_header", font=("Microsoft YaHei UI", 11, "bold"), foreground=self.COLOR_USER)
        t.tag_configure("user_body", foreground=self.COLOR_TEXT, lmargin1=16, lmargin2=16)
        t.tag_configure("assistant_header", font=("Microsoft YaHei UI", 11, "bold"), foreground=self.COLOR_ASSISTANT)
        t.tag_configure("assistant_body", foreground=self.COLOR_TEXT, lmargin1=16, lmargin2=16)
        t.tag_configure("tool_header", font=("Microsoft YaHei UI", 11, "bold"), foreground=self.COLOR_TOOL)
        t.tag_configure("tool_body", foreground=self.COLOR_TEXT, lmargin1=16, lmargin2=16)
        t.tag_configure("system_header", font=("Microsoft YaHei UI", 11, "bold"), foreground=self.COLOR_SYSTEM)
        t.tag_configure("system_body", foreground="#475569", lmargin1=16, lmargin2=16)
        t.tag_configure("separator", foreground=self.COLOR_BORDER)

    def _show_preview_placeholder(self):
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert(tk.END, "\n\n\n")
        self.preview_text.insert(tk.END, "请在左侧选择一个程序\n", "heading1")
        self.preview_text.insert(tk.END, "\n选择对话后将在此处预览内容\n", "meta")
        self.preview_text.config(state=tk.DISABLED)

    def _build_status_bar(self):
        sep = tk.Frame(self.root, height=1, bg=self.COLOR_BORDER)
        sep.pack(fill=tk.X, side=tk.BOTTOM)

        bar = tk.Frame(self.root, bg=self.COLOR_BG, padx=14, pady=6)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self.status_var = tk.StringVar(value="正在检测已安装的程序...")
        self.progress = ttk.Progressbar(bar, mode="determinate", length=180)
        self.progress.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel").pack(side=tk.LEFT)

    # ========== 状态管理 ==========

    def _set_status(self, text: str, progress: int = -1):
        self.status_var.set(text)
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
        except Exception:
            pass

    # ========== 程序检测 ==========

    def _detect_apps(self):
        self._set_status("正在检测已安装的程序...")

        for widget in self.app_list_frame.winfo_children():
            widget.destroy()
        self.app_buttons.clear()
        self.app_status_labels.clear()

        loading_label = tk.Label(self.app_list_frame, text="正在检测...",
                                 font=("Microsoft YaHei UI", 10), bg=self.COLOR_PANEL,
                                 fg=self.COLOR_TEXT_MUTED)
        loading_label.pack(pady=20)

        def detect_thread():
            results = []
            for adapter in self.adapters:
                try:
                    is_available = adapter.detect()
                    if is_available:
                        info = adapter.get_app_info()
                    else:
                        info = AppInfo(
                            name=adapter.name,
                            display_name=adapter.display_name,
                            is_available=False, data_path=None, conversation_count=0
                        )
                    self._app_infos[adapter.name] = info
                    results.append((adapter, info, is_available))
                except Exception:
                    info = AppInfo(
                        name=adapter.name, display_name=adapter.display_name,
                        is_available=False, data_path=None, conversation_count=0
                    )
                    self._app_infos[adapter.name] = info
                    results.append((adapter, info, False))

            self.root.after(0, lambda: self._on_apps_detected(results))

        threading.Thread(target=detect_thread, daemon=True).start()

    def _on_apps_detected(self, results):
        for widget in self.app_list_frame.winfo_children():
            widget.destroy()

        for adapter, info, is_available in results:
            self._add_app_row(adapter, info, is_available)

        available_count = sum(1 for _, _, is_available in results if is_available)
        self._set_status(f"检测完成，发现 {available_count} 个可用程序。请点击左侧选择。")
        self._update_app_actions()

    def _add_app_row(self, adapter, info: AppInfo, is_available: bool):
        status_text = "[可用]" if is_available else "[未安装]"

        row = tk.Frame(self.app_list_frame, bg=self.COLOR_PANEL)
        row.pack(fill=tk.X, pady=2)

        border = tk.Frame(row, width=3, height=40, bg=self.COLOR_BORDER)
        border.pack(side=tk.LEFT, fill=tk.Y)
        border.pack_propagate(False)

        btn = tk.Button(
            row,
            text=f"  {status_text}  {info.display_name}",
            anchor=tk.W,
            font=("Microsoft YaHei UI", 10),
            relief=tk.FLAT,
            bg=self.COLOR_PANEL if is_available else "#f9fafb",
            fg=self.COLOR_TEXT if is_available else self.COLOR_TEXT_DISABLED,
            cursor="hand2" if is_available else "arrow",
            padx=8, pady=9,
            activebackground=self.COLOR_HOVER,
            activeforeground="#0f172a",
            command=lambda a=adapter, ok=is_available: self._select_app(a) if ok else None
        )
        btn.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        if is_available:
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg=self.COLOR_HOVER)
                     if b.cget("bg") != self.COLOR_SELECTED else None)
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg=self.COLOR_PANEL)
                     if b.cget("bg") != self.COLOR_SELECTED else None)

        self.app_buttons[adapter.name] = btn
        self.app_status_labels[adapter.name] = border

    # ========== 程序切换 ==========

    def _select_app(self, adapter):
        for name, btn in self.app_buttons.items():
            border = self.app_status_labels.get(name)
            if name == adapter.name:
                btn.configure(bg=self.COLOR_SELECTED, fg=self.COLOR_USER,
                              font=("Microsoft YaHei UI", 10, "bold"))
                if border:
                    border.configure(bg=self.COLOR_ACCENT)
            else:
                is_available = btn.cget("cursor") == "hand2"
                btn.configure(bg=self.COLOR_PANEL if is_available else "#f9fafb",
                              fg=self.COLOR_TEXT if is_available else self.COLOR_TEXT_DISABLED,
                              font=("Microsoft YaHei UI", 10))
                if border:
                    border.configure(bg=self.COLOR_BORDER)

        self.selected_conv = None
        self.preview_title_label.config(text="")
        self._show_preview_placeholder()
        self._show_center_loading(f"正在加载 {adapter.display_name} 的对话列表...")

        self.current_adapter = adapter
        self._update_app_actions()
        self._load_generation += 1
        self._load_conversations(self._load_generation)

    def _update_app_actions(self):
        if not self.trae_key_button:
            return
        is_trae = bool(self.current_adapter and getattr(self.current_adapter, "name", "") == "trae")
        self.trae_key_button.configure(state=tk.NORMAL if is_trae and not self._key_extract_running else tk.DISABLED)

    def _show_center_loading(self, text: str):
        self._tree_conv_map.clear()
        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)
        self.conv_tree.insert("", tk.END, iid="__loading__", values=(text, "", ""))
        self._set_status(text)

    # ========== TRAE 密钥助手 ==========

    def _extract_trae_key(self):
        adapter = self.current_adapter
        if not adapter or getattr(adapter, "name", "") != "trae":
            messagebox.showwarning("提示", "请先在左侧选择 TRAE SOLO CN")
            return
        if self._key_extract_running:
            return

        confirm = messagebox.askyesno(
            "提取 TRAE 密钥",
            "将只在本机读取正在运行的 TRAE 进程内存，用于提取你自己的本地 SQLCipher 密钥。\n\n"
            "请先打开 TRAE SOLO CN 并进入任意对话窗口。扫描有 8 秒上限，不会上传数据。\n\n"
            "是否开始？"
        )
        if not confirm:
            return

        self._key_extract_running = True
        self._update_app_actions()
        self._set_busy_progress(True)
        self._set_status("正在准备提取 TRAE 密钥...")

        def progress_cb(event: dict):
            msg = event.get("message", "正在提取 TRAE 密钥...")
            self.root.after(0, lambda m=msg: self._set_status(m))

        def worker():
            try:
                result = adapter.extract_key_for_user(progress_callback=progress_cb)
            except Exception as e:
                result = {"ok": False, "reason": str(e)}
            self.root.after(0, lambda r=result: self._on_trae_key_extract_done(r))

        threading.Thread(target=worker, daemon=True).start()

    def _on_trae_key_extract_done(self, result: dict):
        self._key_extract_running = False
        self._update_app_actions()
        self._set_busy_progress(False)

        if not result.get("ok"):
            reason = result.get("reason", "未找到可用密钥")
            hint = result.get("hint", "请确认 TRAE 已运行，再重新点击“提取 TRAE 密钥”。")
            elapsed = result.get("elapsed")
            suffix = f"\n\n耗时：{elapsed}s" if elapsed is not None else ""
            messagebox.showwarning("提取失败", f"{reason}\n\n{hint}{suffix}")
            self._set_status(f"TRAE 密钥提取失败: {reason}")
            return

        key_hex = result.get("key_hex", "")
        source = result.get("source", "未知")
        elapsed = result.get("elapsed", "")
        self._set_status(f"TRAE 密钥已获取（来源：{source}，耗时 {elapsed}s），正在重新加载数据库...")

        adapter = self.current_adapter
        if adapter and getattr(adapter, "name", "") == "trae" and hasattr(adapter, "reset_runtime_cache"):
            adapter.reset_runtime_cache()
            self._load_generation += 1
            self._show_center_loading("密钥已获取，正在重新加载 TRAE 数据库...")
            self._load_conversations(self._load_generation)

        self._show_trae_key_dialog(key_hex, source, elapsed)

    def _show_trae_key_dialog(self, key_hex: str, source: str, elapsed):
        win = tk.Toplevel(self.root)
        win.title("TRAE 密钥已获取")
        win.geometry("720x260")
        win.transient(self.root)
        win.grab_set()
        win.configure(bg=self.COLOR_BG)

        frame = ttk.Frame(win, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="TRAE SQLCipher 密钥已获取", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(frame, text=f"来源：{source} · 耗时：{elapsed}s · 已写入本地缓存，后续可直接解密。",
                  style="Status.TLabel").pack(anchor=tk.W, pady=(4, 12))
        ttk.Label(frame, text="密钥：", style="Status.TLabel").pack(anchor=tk.W)

        key_var = tk.StringVar(value=key_hex)
        entry = ttk.Entry(frame, textvariable=key_var, width=92, show="*")
        entry.pack(fill=tk.X, pady=(4, 8))
        entry.configure(state="readonly")

        show_var = tk.BooleanVar(value=False)

        def toggle_show():
            entry.configure(show="" if show_var.get() else "*")

        ttk.Checkbutton(frame, text="显示密钥", variable=show_var, command=toggle_show).pack(anchor=tk.W)

        note = (
            "建议：普通用户不需要手动保存；程序已本地缓存。若希望以后命令行也直接解密，可写入用户环境变量。"
        )
        ttk.Label(frame, text=note, style="Status.TLabel", wraplength=660).pack(anchor=tk.W, pady=(8, 12))

        btns = ttk.Frame(frame)
        btns.pack(fill=tk.X)

        def copy_key():
            self.root.clipboard_clear()
            self.root.clipboard_append(key_hex)
            self.root.update_idletasks()
            messagebox.showinfo("已复制", "密钥已复制到剪贴板。")

        def set_env():
            os.environ[SQLCIPHER_KEY_ENV] = key_hex
            if os.name != "nt":
                messagebox.showinfo("已设置当前进程", "已写入当前进程环境变量。非 Windows 系统请手动写入 shell profile。")
                return
            try:
                subprocess.run(["setx", SQLCIPHER_KEY_ENV, key_hex], check=True,
                               capture_output=True, text=True, timeout=10)
                messagebox.showinfo(
                    "已写入环境变量",
                    "已写入用户环境变量。新打开的终端/程序会自动读取；当前程序也已立即生效。"
                )
            except Exception as e:
                messagebox.showerror("写入失败", f"setx 写入失败：{e}\n\n你仍可以点击“复制密钥”后手动设置。")

        ttk.Button(btns, text="复制密钥", command=copy_key).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="写入环境变量", command=set_env).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btns, text="关闭", command=win.destroy).pack(side=tk.RIGHT)

    # ========== 对话列表加载 ==========

    def _load_conversations(self, generation: int):
        if not self.current_adapter:
            return

        adapter = self.current_adapter

        def load_thread():
            try:
                convs = adapter.list_conversations()
                self.root.after(0, lambda: self._on_conversations_loaded(convs, generation))
            except Exception as e:
                self.root.after(0, lambda: self._on_conversations_failed(str(e), generation))

        threading.Thread(target=load_thread, daemon=True).start()

    def _on_conversations_failed(self, error: str, generation: int):
        if generation != self._load_generation:
            return
        self.current_conversations = []
        self.conv_count_label.config(text="共 0 个对话")
        self._tree_conv_map.clear()
        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)
        self.conv_tree.insert("", tk.END, iid="__error__", values=(f"加载失败: {error}", "", ""))
        self._set_status(f"加载失败: {error}")

    def _on_conversations_loaded(self, convs, generation: int):
        if generation != self._load_generation:
            return

        self.current_conversations = convs
        self.conv_count_label.config(text=f"共 {len(convs)} 个对话")
        self._filter_conversations()
        self._set_status(f"已加载 {len(convs)} 个对话")

    def _schedule_filter(self):
        if self._filter_after_id:
            try:
                self.root.after_cancel(self._filter_after_id)
            except Exception:
                pass
        self._filter_after_id = self.root.after(self.SEARCH_DEBOUNCE_MS, self._filter_conversations)

    def _filter_conversations(self):
        self._filter_after_id = None
        self._tree_render_generation += 1
        generation = self._tree_render_generation
        self._tree_conv_map.clear()

        search = self.search_var.get().lower().strip()
        matches = []
        for idx, conv in enumerate(self.current_conversations):
            title = conv.title or ""
            if search and search not in title.lower():
                continue
            matches.append((idx, conv))

        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)

        if search:
            self.conv_count_label.config(text=f"匹配 {len(matches)} / {len(self.current_conversations)}")
        else:
            self.conv_count_label.config(text=f"共 {len(self.current_conversations)} 个对话")

        if not matches:
            self.conv_tree.insert("", tk.END, iid="__empty__", values=("没有匹配的对话", "", ""))
            return

        self._insert_tree_batch(matches, 0, generation)

    def _insert_tree_batch(self, matches, start: int, generation: int):
        if generation != self._tree_render_generation:
            return

        end = min(start + self.TREE_INSERT_BATCH_SIZE, len(matches))
        for idx, conv in matches[start:end]:
            date_str = conv.updated_at.strftime("%Y-%m-%d %H:%M") if conv.updated_at else ""
            msg_count = conv.metadata.get("msg_count") if conv.metadata else None
            if msg_count is None:
                msg_count = len(conv.messages)
            display_title = conv.title[:60] + "..." if len(conv.title) > 60 else conv.title
            item_id = f"conv_{idx}"
            self._tree_conv_map[item_id] = conv
            self.conv_tree.insert("", tk.END, iid=item_id, values=(display_title, date_str, msg_count))

        if end < len(matches):
            self.root.after(1, lambda: self._insert_tree_batch(matches, end, generation))

    # ========== 对话预览 ==========

    def _on_conv_select(self, event):
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
        preview_generation = self._preview_generation
        self._set_status("正在加载对话内容...")
        self.preview_title_label.config(text=(conv.title or "")[:50])

        adapter = self.current_adapter

        def load_thread():
            try:
                full_conv = conv
                if adapter and not conv.messages:
                    loaded = adapter.get_conversation(conv.id)
                    if loaded:
                        full_conv = loaded
                        conv.messages = loaded.messages
                        conv.metadata.update(loaded.metadata or {})
                md = self._render_markdown(full_conv)
                self.root.after(0, lambda: self._show_preview(md, full_conv, preview_generation))
            except Exception as e:
                self.root.after(0, lambda: self._set_status(f"加载对话失败: {e}"))

        threading.Thread(target=load_thread, daemon=True).start()

    def _show_preview(self, md_text: str, conv: Conversation, generation: int):
        if generation != self._preview_generation:
            return
        self.selected_conv = conv
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)

        self._preview_chars = 0
        self._preview_truncated = False
        if conv.messages:
            self._render_colored_preview(conv)
        else:
            self._preview_insert(md_text, None)

        self.preview_text.config(state=tk.DISABLED)
        suffix = "，预览已截断，导出仍保留完整内容" if self._preview_truncated else ""
        self._set_status(f"已加载: {conv.title} ({len(conv.messages)}条消息{suffix})")

    def _preview_insert(self, text: str, tag=None) -> bool:
        if not text:
            return True
        remaining = self.PREVIEW_MAX_CHARS - self._preview_chars
        if remaining <= 0:
            if not self._preview_truncated:
                self.preview_text.insert(tk.END, "\n\n[预览已截断；导出文件仍会保留完整内容。]\n", "system_body")
                self._preview_truncated = True
            return False

        if len(text) > remaining:
            text = text[:remaining]
            self.preview_text.insert(tk.END, text, tag)
            self.preview_text.insert(tk.END, "\n\n[预览已截断；导出文件仍会保留完整内容。]\n", "system_body")
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
            return text[:self.PREVIEW_PART_MAX_CHARS] + "\n\n[该段预览已截断；导出文件仍保留完整内容。]"
        return text

    def _render_colored_preview(self, conv: Conversation):
        meta_lines = [
            f"# {conv.title}",
            "",
            f"- **来源程序**: {conv.source_app}",
            f"- **创建时间**: {conv.created_at.strftime('%Y-%m-%d %H:%M:%S') if conv.created_at else 'N/A'}",
            f"- **更新时间**: {conv.updated_at.strftime('%Y-%m-%d %H:%M:%S') if conv.updated_at else 'N/A'}",
            f"- **消息数量**: {len(conv.messages)}",
            "",
            "---",
            "",
        ]
        if not self._preview_insert("\n".join(meta_lines), "meta"):
            return

        for msg in conv.messages:
            role_name, header_tag, body_tag = self._role_to_tags(msg.role)
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M:%S") if msg.timestamp else ""
            header = f"## {role_name}"
            if ts:
                header += f"  ·  {ts}"
            if msg.model:
                header += f"  ·  {msg.model}"

            if not self._preview_insert(header + "\n", header_tag):
                return

            content = self._preview_part(msg.content or "")
            if content and not self._preview_insert(content + "\n\n", body_tag):
                return

            for part in msg.parts:
                ptype = part.type.value if hasattr(part.type, 'value') else str(part.type)
                if ptype == "tool_call":
                    if not self._preview_insert(f"**工具调用**: {part.tool_name}\n", "tool_header"):
                        return
                    if not self._preview_insert(self._preview_part(part.tool_input or "") + "\n\n", "tool_body"):
                        return
                elif ptype == "tool_result":
                    if not self._preview_insert(f"**工具结果**: {part.tool_name or ''}\n", "tool_header"):
                        return
                    if not self._preview_insert(self._preview_part(part.tool_output or part.content or "") + "\n\n", "tool_body"):
                        return
                elif ptype == "thinking":
                    if not self._preview_insert("**思考过程**\n", "system_header"):
                        return
                    if not self._preview_insert(self._preview_part(part.content or "") + "\n\n", "system_body"):
                        return
                elif ptype == "code":
                    lang = part.language or ""
                    code = f"```{lang}\n{part.content or ''}\n```"
                    if not self._preview_insert(self._preview_part(code) + "\n\n", "code"):
                        return
                elif ptype in ("file", "image"):
                    name = part.file_name or part.content or "附件"
                    if not self._preview_insert(f"**附件**: {name}\n\n", "system_body"):
                        return

            if not self._preview_insert("---\n\n", "separator"):
                return

    @staticmethod
    def _role_to_tags(role):
        mapping = {
            Role.USER: ("用户", "user_header", "user_body"),
            Role.ASSISTANT: ("AI 助手", "assistant_header", "assistant_body"),
            Role.TOOL: ("工具", "tool_header", "tool_body"),
            Role.SYSTEM: ("系统", "system_header", "system_body"),
        }
        return mapping.get(role, ("未知", "system_header", "system_body"))

    def _render_markdown(self, conv: Conversation) -> str:
        self.exporter.include_thinking = True
        return self.exporter.export(conv)

    # ========== 导出 ==========

    def _export_selected(self):
        if not self.selected_conv:
            messagebox.showwarning("提示", "请先选择一个对话")
            return

        conv = self.selected_conv
        default_name = MarkdownExporter.sanitize_filename(conv.title)
        if conv.updated_at:
            default_name += f"_{conv.updated_at.strftime('%Y%m%d_%H%M%S')}"
        default_name += ".md"

        filepath = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown 文件", "*.md"), ("所有文件", "*.*")],
            initialfile=default_name, title="导出对话为 Markdown"
        )
        if not filepath:
            return

        try:
            self.exporter.include_thinking = True
            self.exporter.export(conv, filepath)
            messagebox.showinfo("导出成功", f"已导出到:\n{filepath}")
            self._set_status(f"导出成功: {os.path.basename(filepath)}")
        except Exception as e:
            messagebox.showerror("导出失败", str(e))

    def _export_all(self):
        if not self.current_conversations:
            messagebox.showwarning("提示", "当前没有可导出的对话")
            return

        output_dir = filedialog.askdirectory(title="选择批量导出的目录")
        if not output_dir:
            return

        confirm = messagebox.askyesno(
            "确认批量导出",
            f"将导出 {len(self.current_conversations)} 个对话到:\n{output_dir}\n\n是否继续?"
        )
        if not confirm:
            return

        self._set_status("正在批量导出...", 0)
        adapter = self.current_adapter
        convs = list(self.current_conversations)

        def export_thread():
            try:
                exported = self._batch_export_full_conversations(convs, output_dir, adapter)
                self.root.after(0, lambda: self._on_batch_export_complete(exported, output_dir))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("导出失败", str(e)))

        threading.Thread(target=export_thread, daemon=True).start()

    def _batch_export_full_conversations(self, convs: List[Conversation], output_dir: str, adapter) -> int:
        os.makedirs(output_dir, exist_ok=True)
        exported = 0
        total = len(convs)
        exporter = MarkdownExporter(include_metadata=True, include_timestamp=True, include_thinking=True)

        for i, conv in enumerate(convs):
            full_conv = conv
            if adapter and not conv.messages:
                loaded = adapter.get_conversation(conv.id)
                if loaded:
                    full_conv = loaded

            safe_title = MarkdownExporter.sanitize_filename(full_conv.title)
            ts = full_conv.updated_at.strftime("%Y%m%d_%H%M%S") if full_conv.updated_at else ""
            filename = f"{safe_title}_{ts}.md" if ts else f"{safe_title}.md"
            filepath = os.path.join(output_dir, filename)

            counter = 1
            base, ext = os.path.splitext(filepath)
            while os.path.exists(filepath):
                filepath = f"{base}_{counter}{ext}"
                counter += 1

            exporter.export(full_conv, filepath)
            exported += 1

            self.root.after(
                0,
                lambda cur=i + 1, total=total, fp=filepath: self._set_status(
                    f"导出中 {cur}/{total}: {os.path.basename(fp)}",
                    int(cur / total * 100)
                )
            )

        return exported

    def _on_batch_export_complete(self, count: int, output_dir: str):
        self._set_status(f"批量导出完成，共 {count} 个文件", 100)
        messagebox.showinfo("批量导出完成", f"成功导出 {count} 个对话到:\n{output_dir}")

    # ========== 运行 ==========

    def run(self):
        self.root.mainloop()


def run():
    app = ChatExporterGUI()
    app.run()

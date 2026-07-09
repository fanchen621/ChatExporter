import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
from typing import List, Optional

from .adapters.trae import TraeAdapter
from .adapters.qoderwork import QoderWorkAdapter
from .adapters.workbuddy import WorkBuddyAdapter
from .adapters.qclaw import QClawAdapter
from .adapters.marvis import MarvisAdapter
from .models import Conversation, AppInfo
from .markdown_exporter import MarkdownExporter


class ChatExporterGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("多程序对话导出工具")
        self.root.geometry("1200x800")
        self.root.minsize(900, 600)

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
        self.exporter = MarkdownExporter(include_metadata=True, include_timestamp=True, include_thinking=False)
        self._load_generation = 0  # 防止异步加载竞态

        self._setup_style()
        self._build_ui()
        self._detect_apps()

    def _setup_style(self):
        style = ttk.Style()
        available_themes = style.theme_names()
        if "clam" in available_themes:
            style.theme_use("clam")

        # 全局配色
        self.root.configure(bg="#f8fafc")
        style.configure(".", background="#f8fafc", foreground="#1e293b",
                         font=("Microsoft YaHei UI", 10))

        # 标题样式
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 12, "bold"),
                         foreground="#0f172a", background="#f8fafc")

        # 状态栏样式
        style.configure("Status.TLabel", font=("Microsoft YaHei UI", 9),
                         foreground="#64748b", background="#f8fafc")

        # 按钮样式
        style.configure("TButton", font=("Microsoft YaHei UI", 10),
                         padding=(8, 5), relief="flat", borderwidth=0,
                         background="#e2e8f0", foreground="#1e293b")
        style.map("TButton",
                   background=[("active", "#cbd5e1"), ("pressed", "#94a3b8")])

        # 应用按钮样式
        style.configure("App.TButton", font=("Microsoft YaHei UI", 10),
                         padding=(10, 8))

        # 复选框样式
        style.configure("TCheckbutton", background="#f8fafc", foreground="#1e293b",
                         font=("Microsoft YaHei UI", 10))
        style.configure("Options.TCheckbutton", background="#f8fafc", foreground="#1e293b",
                         font=("Microsoft YaHei UI", 10))

        # Treeview 样式
        style.configure("Treeview",
                         font=("Microsoft YaHei UI", 10),
                         rowheight=32,
                         background="#ffffff",
                         foreground="#1e293b",
                         fieldbackground="#ffffff",
                         borderwidth=0,
                         relief="flat")
        style.configure("Treeview.Heading",
                         font=("Microsoft YaHei UI", 9, "bold"),
                         background="#f1f5f9",
                         foreground="#334155",
                         relief="flat",
                         padding=(8, 6))
        style.map("Treeview",
                   background=[("selected", "#dbeafe")],
                   foreground=[("selected", "#1d4ed8")])

        # PanedWindow 样式
        style.configure("TPanedWindow", background="#e2e8f0")

        # Frame 样式
        style.configure("TFrame", background="#f8fafc")
        style.configure("LeftPanel.TFrame", background="#ffffff")
        style.configure("PreviewPanel.TFrame", background="#ffffff")

    def _build_ui(self):
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        left_frame = ttk.Frame(main_paned, width=240)
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
        parent.configure(style="LeftPanel.TFrame")
        
        # 标题区域
        title_frame = ttk.Frame(parent, style="LeftPanel.TFrame")
        title_frame.pack(fill=tk.X, padx=12, pady=(12, 16))
        ttk.Label(title_frame, text="支持的程序", style="Title.TLabel").pack(anchor=tk.W)

        # 应用列表区域
        self.app_list_frame = ttk.Frame(parent, style="LeftPanel.TFrame")
        self.app_list_frame.pack(fill=tk.BOTH, expand=True, padx=12)

        self.app_buttons = {}
        self.app_status_labels = {}

        # 分隔线
        separator = tk.Frame(parent, height=1, bg="#e2e8f0")
        separator.pack(fill=tk.X, pady=(12, 8))

        # 刷新按钮
        refresh_btn = ttk.Button(parent, text="🔄 刷新检测", command=self._detect_apps, style="App.TButton")
        refresh_btn.pack(fill=tk.X, padx=12, pady=(0, 12))

    def _build_center_panel(self, parent):
        # 顶部标题区
        top_frame = ttk.Frame(parent)
        top_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(top_frame, text="对话列表", style="Title.TLabel").pack(side=tk.LEFT)
        self.conv_count_label = ttk.Label(top_frame, text="", style="Status.TLabel")
        self.conv_count_label.pack(side=tk.RIGHT)

        # 搜索栏 - 更突出
        search_frame = tk.Frame(parent, bg="#f1f5f9", padx=8, pady=6)
        search_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(search_frame, text="🔍").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self._filter_conversations())
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, font=("Microsoft YaHei UI", 10))
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        # 对话列表
        list_frame = ttk.Frame(parent)
        list_frame.pack(fill=tk.BOTH, expand=True)

        self.conv_tree = ttk.Treeview(list_frame, columns=("title", "date", "messages"), show="headings", selectmode="browse")
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

        # 按钮区域 - 更好的间距
        btn_frame = ttk.Frame(parent)
        btn_frame.pack(fill=tk.X, pady=(12, 0))

        ttk.Button(btn_frame, text="📥 导出选中", command=self._export_selected).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(btn_frame, text="📦 批量导出全部", command=self._export_all).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        # 选项区域
        options_frame = ttk.Frame(parent)
        options_frame.pack(fill=tk.X, pady=(10, 0))
        self.opt_thinking = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="包含思考过程", variable=self.opt_thinking,
                        command=self._on_options_change, style="Options.TCheckbutton").pack(side=tk.LEFT)

    def _build_right_panel(self, parent):
        # 顶部标题区
        top_frame = ttk.Frame(parent)
        top_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(top_frame, text="Markdown 预览", style="Title.TLabel").pack(side=tk.LEFT)
        self.preview_title_label = ttk.Label(top_frame, text="", style="Status.TLabel")
        self.preview_title_label.pack(side=tk.RIGHT)

        # 预览区域 - 添加边框
        preview_frame = tk.Frame(parent, bg="#e2e8f0", bd=0, highlightbackground="#e2e8f0", highlightthickness=1)
        preview_frame.pack(fill=tk.BOTH, expand=True)

        self.preview_text = tk.Text(
            preview_frame,
            wrap=tk.WORD,
            font=("Consolas", 11),
            bg="#ffffff",
            fg="#1e293b",
            insertbackground="#1e293b",
            padx=16,
            pady=16,
            state=tk.DISABLED,
            borderwidth=0,
            highlightthickness=0
        )

        v_scroll = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=self.preview_text.yview)
        h_scroll = ttk.Scrollbar(preview_frame, orient=tk.HORIZONTAL, command=self.preview_text.xview)
        self.preview_text.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        self.preview_text.tag_configure("heading1", font=("Microsoft YaHei UI", 18, "bold"), foreground="#0f172a", spacing3=10)
        self.preview_text.tag_configure("heading2", font=("Microsoft YaHei UI", 14, "bold"), foreground="#2563eb", spacing3=8)
        self.preview_text.tag_configure("meta", foreground="#64748b", font=("Microsoft YaHei UI", 9))
        self.preview_text.tag_configure("code", font=("Consolas", 10), background="#f1f5f9", foreground="#059669")

        # 角色着色标签：用户蓝色、AI助手绿色、工具橙色、系统灰色
        self.preview_text.tag_configure("user_header", font=("Microsoft YaHei UI", 11, "bold"), foreground="#1d4ed8")
        self.preview_text.tag_configure("user_body", foreground="#1e293b", lmargin1=16, lmargin2=16)
        self.preview_text.tag_configure("assistant_header", font=("Microsoft YaHei UI", 11, "bold"), foreground="#059669")
        self.preview_text.tag_configure("assistant_body", foreground="#1e293b", lmargin1=16, lmargin2=16)
        self.preview_text.tag_configure("tool_header", font=("Microsoft YaHei UI", 11, "bold"), foreground="#d97706")
        self.preview_text.tag_configure("tool_body", foreground="#1e293b", lmargin1=16, lmargin2=16)
        self.preview_text.tag_configure("system_header", font=("Microsoft YaHei UI", 11, "bold"), foreground="#64748b")
        self.preview_text.tag_configure("system_body", foreground="#475569", lmargin1=16, lmargin2=16)
        self.preview_text.tag_configure("separator", foreground="#cbd5e1")

        self.preview_text.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        preview_frame.grid_rowconfigure(0, weight=1)
        preview_frame.grid_columnconfigure(0, weight=1)

    def _build_status_bar(self):
        # 分隔线
        separator = tk.Frame(self.root, height=1, bg="#e2e8f0")
        separator.pack(fill=tk.X, side=tk.BOTTOM, padx=0, pady=0)

        status_frame = tk.Frame(self.root, bg="#f8fafc", padx=12, pady=6)
        status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_var = tk.StringVar(value="就绪")
        self.progress = ttk.Progressbar(status_frame, mode="determinate", length=180)
        self.progress.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Label(status_frame, textvariable=self.status_var, style="Status.TLabel").pack(side=tk.LEFT)

    def _set_status(self, text: str, progress: int = -1):
        self.status_var.set(text)
        if progress >= 0:
            self.progress["value"] = progress
        self.root.update_idletasks()

    def _detect_apps(self):
        self._set_status("正在检测已安装的程序...")
        for widget in self.app_list_frame.winfo_children():
            widget.destroy()
        self.app_buttons.clear()
        self.app_status_labels.clear()

        first_available = None
        for adapter in self.adapters:
            self._add_app_row(adapter)
            if adapter.detect() and first_available is None:
                first_available = adapter

        if first_available:
            self._select_app(first_available)

        self._set_status("检测完成")

    def _add_app_row(self, adapter):
        info = adapter.get_app_info()
        status_icon = "✅" if info.is_available else "❌"

        # 使用 Canvas 画左侧 3px 强调条，不占用按钮内部空间
        row = tk.Frame(self.app_list_frame, bg="#ffffff")
        row.pack(fill=tk.X, pady=3)

        border_canvas = tk.Canvas(row, width=3, height=38, bg="#e2e8f0",
                                  highlightthickness=0, bd=0)
        border_canvas.pack(side=tk.LEFT, fill=tk.Y)

        btn = tk.Button(
            row,
            text=f"{status_icon}  {info.display_name}",
            anchor=tk.W,
            font=("Microsoft YaHei UI", 10),
            relief=tk.FLAT,
            bg="#ffffff" if info.is_available else "#f9fafb",
            fg="#1e293b" if info.is_available else "#94a3b8",
            cursor="hand2" if info.is_available else "arrow",
            padx=10,
            pady=8,
            activebackground="#f1f5f9",
            activeforeground="#0f172a",
            command=lambda a=adapter: self._select_app(a) if a.detect() else None
        )
        btn.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 悬停效果
        if info.is_available:
            btn.bind("<Enter>", lambda e, b=btn: b.configure(bg="#f8fafc"))
            btn.bind("<Leave>", lambda e, b=btn: b.configure(bg="#ffffff") if b.cget("bg") != "#dbeafe" else None)

        self.app_buttons[adapter.name] = btn
        self.app_status_labels[adapter.name] = border_canvas

    def _select_app(self, adapter):
        for name, btn in self.app_buttons.items():
            border = self.app_status_labels.get(name)
            if name == adapter.name:
                btn.configure(bg="#dbeafe", fg="#1d4ed8", font=("Microsoft YaHei UI", 10, "bold"))
                if border:
                    border.configure(bg="#2563eb")
            else:
                info = next((a.get_app_info() for a in self.adapters if a.name == name), None)
                if info and info.is_available:
                    btn.configure(bg="#ffffff", fg="#1e293b", font=("Microsoft YaHei UI", 10))
                else:
                    btn.configure(bg="#f9fafb", fg="#94a3b8", font=("Microsoft YaHei UI", 10))
                if border:
                    border.configure(bg="#e2e8f0")

        # 切换程序时清空预览和选中状态，避免显示旧程序内容
        self.selected_conv = None
        self.preview_title_label.config(text="")
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.config(state=tk.DISABLED)

        self.current_adapter = adapter
        self._load_generation += 1
        self._load_conversations(self._load_generation)

    def _load_conversations(self, generation: int):
        if not self.current_adapter:
            return

        adapter_name = self.current_adapter.display_name
        self._set_status(f"正在加载 {adapter_name} 的对话列表...")

        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)

        def load_thread():
            try:
                convs = self.current_adapter.list_conversations()
                self.root.after(0, lambda: self._on_conversations_loaded(convs, generation))
            except Exception as e:
                self.root.after(0, lambda: self._set_status(f"加载失败: {e}"))

        threading.Thread(target=load_thread, daemon=True).start()

    def _on_conversations_loaded(self, convs, generation: int):
        # 如果 generation 已过期，说明用户已经切换了程序，丢弃旧结果
        if generation != self._load_generation:
            return
        self.current_conversations = convs
        self.conv_count_label.config(text=f"共 {len(convs)} 个对话")
        self._filter_conversations()
        self._set_status(f"已加载 {len(convs)} 个对话")

    def _filter_conversations(self):
        search = self.search_var.get().lower().strip()
        for item in self.conv_tree.get_children():
            self.conv_tree.delete(item)

        for conv in self.current_conversations:
            if search and search not in conv.title.lower():
                continue
            date_str = conv.updated_at.strftime("%Y-%m-%d %H:%M") if conv.updated_at else ""
            # 优先使用适配器预计算的消息数，避免懒加载时显示 0
            msg_count = conv.metadata.get("msg_count") if conv.metadata else None
            if msg_count is None:
                msg_count = len(conv.messages)
            display_title = conv.title[:60] + "..." if len(conv.title) > 60 else conv.title
            self.conv_tree.insert("", tk.END, iid=conv.id, values=(display_title, date_str, msg_count))

    def _on_conv_select(self, event):
        selection = self.conv_tree.selection()
        if not selection:
            return

        conv_id = selection[0]
        conv = next((c for c in self.current_conversations if c.id == conv_id), None)
        if not conv:
            return

        self._set_status("正在加载对话内容...")
        self.preview_title_label.config(text=conv.title[:50])

        def load_thread():
            try:
                if not conv.messages:
                    full_conv = self.current_adapter.get_conversation(conv.id)
                    if full_conv:
                        conv.messages = full_conv.messages
                md = self._render_markdown(conv)
                self.root.after(0, lambda: self._show_preview(md, conv))
            except Exception as e:
                self.root.after(0, lambda: self._set_status(f"加载对话失败: {e}"))

        threading.Thread(target=load_thread, daemon=True).start()

    def _on_options_change(self):
        self.exporter.include_thinking = self.opt_thinking.get()
        if self.selected_conv:
            self._on_conv_select(None)

    def _show_preview(self, md_text: str, conv: Conversation):
        self.selected_conv = conv
        self.preview_text.config(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)

        # 优先按消息角色逐段渲染；否则回退到纯文本
        if conv.messages:
            self._render_colored_preview(conv)
        else:
            self.preview_text.insert("1.0", md_text)

        self.preview_text.config(state=tk.DISABLED)
        self._set_status(f"已加载: {conv.title} ({len(conv.messages)}条消息)")

    def _render_colored_preview(self, conv: Conversation):
        """按消息角色为 Markdown 预览着色"""
        from .models import Role

        # 渲染元数据头部
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
        self.preview_text.insert(tk.END, "\n".join(meta_lines), "meta")

        for msg in conv.messages:
            role = msg.role
            role_name, header_tag, body_tag = self._role_to_tags(role)
            ts = msg.timestamp.strftime("%Y-%m-%d %H:%M:%S") if msg.timestamp else ""
            header = f"## {role_name}"
            if ts:
                header += f" · {ts}"
            if msg.model:
                header += f" · {msg.model}"

            self.preview_text.insert(tk.END, header + "\n", header_tag)

            content = msg.content or ""
            if content:
                self.preview_text.insert(tk.END, content + "\n\n", body_tag)

            # 工具调用 / 结果单独展示
            for part in msg.parts:
                if part.type.value == "tool_call":
                    self.preview_text.insert(tk.END, f"**工具调用**: {part.tool_name}\n", "tool_header")
                    self.preview_text.insert(tk.END, (part.tool_input or "") + "\n\n", "tool_body")
                elif part.type.value == "tool_result":
                    self.preview_text.insert(tk.END, f"**工具结果**: {part.tool_name or ''}\n", "tool_header")
                    self.preview_text.insert(tk.END, (part.tool_output or "") + "\n\n", "tool_body")
                elif part.type.value == "thinking":
                    self.preview_text.insert(tk.END, "**思考过程**\n", "system_header")
                    self.preview_text.insert(tk.END, (part.content or "") + "\n\n", "system_body")

            self.preview_text.insert(tk.END, "---\n\n", "separator")

    @staticmethod
    def _role_to_tags(role):
        from .models import Role
        mapping = {
            Role.USER: ("👤 用户", "user_header", "user_body"),
            Role.ASSISTANT: ("🤖 AI 助手", "assistant_header", "assistant_body"),
            Role.TOOL: ("🔧 工具", "tool_header", "tool_body"),
            Role.SYSTEM: ("⚙️ 系统", "system_header", "system_body"),
        }
        return mapping.get(role, ("❓ 未知", "system_header", "system_body"))

    def _render_markdown(self, conv: Conversation) -> str:
        self.exporter.include_thinking = self.opt_thinking.get()
        return self.exporter.export(conv)

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
            initialfile=default_name,
            title="导出对话为 Markdown"
        )
        if not filepath:
            return

        try:
            self.exporter.include_thinking = self.opt_thinking.get()
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

        def export_thread():
            try:
                self.exporter.include_thinking = self.opt_thinking.get()
                exported = MarkdownExporter.batch_export(
                    self.current_conversations,
                    output_dir,
                    progress_callback=lambda cur, total, fp: self.root.after(
                        0, lambda: self._set_status(
                            f"导出中 {cur}/{total}: {os.path.basename(fp)}",
                            int(cur / total * 100)
                        )
                    )
                )
                self.root.after(0, lambda: self._on_batch_export_complete(exported, output_dir))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("导出失败", str(e)))

        threading.Thread(target=export_thread, daemon=True).start()

    def _on_batch_export_complete(self, count: int, output_dir: str):
        self._set_status(f"批量导出完成，共 {count} 个文件", 100)
        messagebox.showinfo("批量导出完成", f"成功导出 {count} 个对话到:\n{output_dir}")

    def run(self):
        self.root.mainloop()


def run():
    app = ChatExporterGUI()
    app.run()

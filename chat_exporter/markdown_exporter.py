import os
import re
from datetime import datetime
from typing import Optional

from .models import Conversation, Message, MessagePartType, Role
from .preview_utils import effective_role, strip_internal_context


class MarkdownExporter:
    def __init__(
        self,
        include_metadata: bool = True,
        include_timestamp: bool = True,
        include_thinking: bool = True,
        include_tool_messages: bool = True,
    ):
        self.include_metadata = include_metadata
        self.include_timestamp = include_timestamp
        # 用户反馈：默认保留思考过程，GUI 不再提供关闭开关。
        self.include_thinking = include_thinking
        # 有些客户端（QClaw）把工具输出存成独立的 role=tool 消息而不是 assistant 的
        # tool_result part。预览按设计过滤掉它们，但"完整导出"必须保留：实测一条
        # QClaw 对话 97% 的字节都在这类消息里。
        self.include_tool_messages = include_tool_messages

    @staticmethod
    def _fence(content: str, char: str = "`", minimum: int = 3) -> str:
        """选一条比内容里最长同字符栅栏更长的围栏，避免正文提前闭合。"""
        longest = 0
        for run in re.findall(rf"{re.escape(char)}{{{minimum},}}", str(content or "")):
            longest = max(longest, len(run))
        return char * max(minimum, longest + 1)

    def export(self, conv: Conversation, output_path: Optional[str] = None) -> str:
        md_content = self._build_markdown(conv)

        if output_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(md_content)

        return md_content

    def _build_markdown(self, conv: Conversation) -> str:
        lines = []

        # Build the final chapter list first. Metadata must describe what was
        # actually exported, not every low-level database record.
        visible = []
        tool_sections = 0
        for msg in conv.messages:
            eff_role = effective_role(msg)
            if eff_role is None:
                # effective_role 是"预览可见性"规则，不该决定完整导出的内容。
                # QClaw 把工具输出存成独立的 role=tool 消息（TEXT part），此前整批被丢弃。
                if not (self.include_tool_messages and msg.role == Role.TOOL):
                    continue
                content = self._format_message(msg, source_app=conv.source_app)
                if not content.strip():
                    continue
                tool_sections += 1
                visible.append((msg, Role.TOOL, content))
                continue
            content = self._format_message(msg, source_app=conv.source_app)
            if not content.strip():
                continue
            visible.append((msg, eff_role, content))

        lines.append(f"# {conv.title or '(无标题对话)'}")
        lines.append("")

        meta_lines = []
        meta_lines.append(f"- **来源程序**: {conv.source_app}")
        if conv.created_at:
            meta_lines.append(f"- **创建时间**: {self._fmt_dt(conv.created_at)}")
        if conv.updated_at:
            meta_lines.append(f"- **更新时间**: {self._fmt_dt(conv.updated_at)}")
        if conv.model:
            meta_lines.append(f"- **使用模型**: {conv.model}")
        if conv.messages:
            user_count = sum(1 for _msg, role, _content in visible if role == Role.USER)
            asst_count = sum(1 for _msg, role, _content in visible if role == Role.ASSISTANT)
            meta_lines.append(f"- **消息数量**: {len(visible)}")
            meta_lines.append(f"- **对话轮次**: {user_count} 问 / {asst_count} 答")
            if tool_sections:
                meta_lines.append(f"- **工具记录**: {tool_sections} 条（默认折叠）")

        total_tokens = 0
        for m in conv.messages:
            if m.token_usage:
                total_tokens += m.token_usage.get("total_tokens", m.token_usage.get("total", 0))
        if total_tokens > 0:
            meta_lines.append(f"- **总Token用量**: ~{total_tokens:,}")

        lines.append("\n".join(meta_lines))
        lines.append("")
        lines.append("---")
        lines.append("")

        for i, (msg, eff_role, content) in enumerate(visible):
            role_label = self._get_role_label(eff_role)
            header = f"## {role_label}"
            if self.include_timestamp and msg.timestamp:
                header += f" · {self._fmt_dt(msg.timestamp)}"
            if msg.model:
                header += f" · {msg.model}"

            lines.append(header)
            lines.append("")
            if eff_role == Role.TOOL:
                # 工具记录默认折叠，完整保留但不打断阅读节奏。
                fence = self._fence(content, "~")
                lines.append(
                    f"<details>\n<summary>🔧 工具记录</summary>\n\n{fence}\n{content}\n{fence}\n\n</details>"
                )
            else:
                lines.append(content)
            lines.append("")

            if i < len(visible) - 1:
                lines.append("---")
                lines.append("")

        lines.append("")
        lines.append("---")
        lines.append(f"*导出时间: {self._fmt_dt(datetime.now())} · 多程序对话导出工具*")
        lines.append("")

        return "\n".join(lines)

    def _get_role_label(self, role: Role) -> str:
        return {
            Role.USER: "👤 用户",
            Role.ASSISTANT: "🤖 AI助手",
            Role.SYSTEM: "⚙️ 系统",
            Role.TOOL: "🔧 工具",
        }.get(role, str(role))

    @staticmethod
    def _fallback_body(thinking_parts, tool_results, source_app: str) -> str:
        """Return the readable body used when a message has no TEXT parts.

        Some TRAE task rows store the final delivery in reasoning parts, while
        some clients store it only in a tool result. Previously those records
        existed in the detailed blocks but had no visible body, which looked
        like an incomplete export.
        """
        cleaned_thinking = [str(item).strip() for item in thinking_parts if item and str(item).strip()]
        if cleaned_thinking:
            if (source_app or "").casefold().startswith("trae"):
                return "\n\n---\n\n".join(cleaned_thinking)

            lines = [line for line in cleaned_thinking[0].splitlines() if line.strip()]
            if lines:
                summary = "\n".join(lines[:8])
                if len(summary) > 600:
                    summary = summary[:600] + "…"
                return f"[AI 思考摘要]\n{summary}"

        for result in reversed(tool_results):
            output = result.tool_output or result.content or ""
            output = str(output).strip()
            if output:
                return f"[工具结果]\n{output}"
        return ""

    def _format_message(self, msg: Message, source_app: str = "") -> str:
        parts_text = []
        main_text = msg.content

        if not msg.parts:
            cleaned = strip_internal_context(main_text or "", source_app=source_app)
            return cleaned if cleaned else self._clean_content(main_text)

        thinking_parts = [p.content for p in msg.parts if p.type == MessagePartType.THINKING and p.content]
        tool_results = [p for p in msg.parts if p.type == MessagePartType.TOOL_RESULT]
        has_body = False

        # 按 parts 原始顺序渲染：讲解和它对应的代码块必须保持相邻。
        # 旧实现先输出全部 TEXT 再输出全部 CODE，读者会把代码认到错误的步骤上。
        index = 0
        while index < len(msg.parts):
            part = msg.parts[index]

            if part.type == MessagePartType.TEXT and part.content:
                cleaned = strip_internal_context(part.content, source_app=source_app)
                body = cleaned if cleaned else self._clean_content(part.content)
                if body:
                    parts_text.append(body)
                    has_body = True

            elif part.type == MessagePartType.CODE:
                lang = part.language or ""
                fence = self._fence(part.content, "`")
                parts_text.append(f"\n{fence}{lang}\n{part.content}\n{fence}\n")
                has_body = True

            elif part.type == MessagePartType.THINKING:
                # 合并相邻思考块为一个 <details>，避免几十个折叠块堆叠。
                run = []
                while index < len(msg.parts) and msg.parts[index].type == MessagePartType.THINKING:
                    if msg.parts[index].content and msg.parts[index].content.strip():
                        run.append(msg.parts[index].content.strip())
                    index += 1
                index -= 1
                if self.include_thinking and run:
                    combined = "\n\n---\n\n".join(run)
                    fence = self._fence(combined, "~")
                    parts_text.append(
                        f"\n<details>\n<summary>💭 思考过程</summary>\n\n{fence}\n{combined}\n{fence}\n\n</details>\n"
                    )

            elif part.type == MessagePartType.TOOL_CALL:
                name = part.tool_name or "unknown tool"
                inp = part.tool_input or part.content or ""
                fence = self._fence(inp, "`")
                parts_text.append(
                    f"\n> 🔧 **调用工具**: `{name}`\n>\n> {fence}json\n> {self._indent(inp, '> ')}\n> {fence}\n"
                )

            elif part.type == MessagePartType.TOOL_RESULT:
                output = str(part.tool_output or part.content or "")
                fence = self._fence(output, "~")
                parts_text.append(
                    f"\n<details>\n<summary>📎 工具返回结果</summary>\n\n{fence}\n{output}\n{fence}\n\n</details>\n"
                )

            elif part.type == MessagePartType.FILE:
                parts_text.append(f"\n📄 **附件**: `{part.file_name or 'file'}`\n")

            elif part.type == MessagePartType.IMAGE:
                parts_text.append(f"\n🖼️ **图片**: `{part.file_name or 'image.png'}`\n")

            index += 1

        # 最终交付只存在于 thinking/tool_result 时，仍需补一段可见正文。
        if not has_body:
            fallback = self._fallback_body(thinking_parts, tool_results, source_app)
            if fallback:
                parts_text.insert(0, fallback)

        result = "\n".join(parts_text).strip()
        if not result:
            cleaned = strip_internal_context(main_text or "", source_app=source_app)
            result = cleaned if cleaned else self._clean_content(main_text)
        return result

    @staticmethod
    def _clean_content(text: str) -> str:
        if not text:
            return ""
        text = text.replace("\r\n", "\n")
        return text.strip()

    @staticmethod
    def _indent(text: str, prefix: str) -> str:
        lines = str(text).split("\n")
        return ("\n" + prefix).join(lines)

    @staticmethod
    def _fmt_dt(dt: Optional[datetime]) -> str:
        if not dt:
            return ""
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def sanitize_filename(name: str) -> str:
        name = str(name or "conversation")
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        name = re.sub(r'_+', '_', name)
        name = name.strip('_ .')
        if len(name) > 100:
            name = name[:100]
        return name or "conversation"

    @staticmethod
    def batch_export(conv_list, output_dir: str, progress_callback=None) -> int:
        os.makedirs(output_dir, exist_ok=True)
        exported = 0
        total = len(conv_list)

        for i, conv in enumerate(conv_list):
            safe_title = MarkdownExporter.sanitize_filename(conv.title)
            ts = conv.updated_at.strftime("%Y%m%d_%H%M%S") if conv.updated_at else ""
            filename = f"{safe_title}_{ts}.md" if ts else f"{safe_title}.md"
            filepath = os.path.join(output_dir, filename)

            counter = 1
            base, ext = os.path.splitext(filepath)
            while os.path.exists(filepath):
                filepath = f"{base}_{counter}{ext}"
                counter += 1

            exporter = MarkdownExporter(include_thinking=True)
            exporter.export(conv, filepath)
            exported += 1

            if progress_callback:
                progress_callback(i + 1, total, filepath)

        return exported

"""多格式导出：Markdown / HTML / JSON / 纯文本。

Markdown 分支直接复用 MarkdownExporter，这里一行渲染逻辑都不重写：
那个类里积累了大量真实客户端的兼容补丁（围栏碰撞、parts 顺序、
fallback 正文、role=tool 消息），复制一份必然随版本漂移。

四种格式的定位互不重叠，别把它们当成同一份内容的皮肤：
- markdown：完整存档，含思考与工具明细，给人读也给别的工具吃。
- html：单文件自包含，双击即看，正文与折叠块分层。
- json：无损结构化转储，唯一能完整还原 Conversation 的格式。
- txt：只有用户/AI 正文的干净阅读版，给粘贴到别处用。
"""

from __future__ import annotations

import html
import json
import os
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .markdown_exporter import MarkdownExporter
from .models import Conversation, Message, MessagePart, MessagePartType, Role
from .preview_utils import effective_role, plain_preview_text, strip_internal_context

JSON_SCHEMA_ID = "chat-exporter/conversation@1"


# ============================================================================
# 基类
# ============================================================================


class BaseExporter:
    """所有格式的统一接口：label / extension / export(conv, path=None)。"""

    format_id: str = ""
    label: str = ""
    extension: str = ""

    def __init__(
        self,
        include_metadata: bool = True,
        include_timestamp: bool = True,
        include_thinking: bool = True,
        include_tool_messages: bool = True,
    ):
        self.include_metadata = include_metadata
        self.include_timestamp = include_timestamp
        self.include_thinking = include_thinking
        self.include_tool_messages = include_tool_messages

    def render(self, conv: Conversation) -> str:
        raise NotImplementedError

    def export(self, conv: Conversation, path: Optional[str] = None) -> str:
        content = self.render(conv)
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
        return content

    # 供 GUI 生成默认文件名用，四种格式共用一套规则。
    def suggested_filename(self, conv: Conversation) -> str:
        safe_title = MarkdownExporter.sanitize_filename(conv.title)
        stamp = conv.updated_at.strftime("%Y%m%d_%H%M%S") if conv.updated_at else ""
        return f"{safe_title}_{stamp}{self.extension}" if stamp else f"{safe_title}{self.extension}"


# ============================================================================
# Markdown：完全委托给既有实现
# ============================================================================


class MarkdownFormatExporter(BaseExporter):
    format_id = "markdown"
    label = "Markdown（完整存档）"
    extension = ".md"

    def _impl(self) -> MarkdownExporter:
        return MarkdownExporter(
            include_metadata=self.include_metadata,
            include_timestamp=self.include_timestamp,
            include_thinking=self.include_thinking,
            include_tool_messages=self.include_tool_messages,
        )

    def render(self, conv: Conversation) -> str:
        # 不传 path：写文件统一走 BaseExporter.export，行为对四种格式一致。
        return self._impl().export(conv)


# ============================================================================
# HTML：单文件、内联样式、零外部请求
# ============================================================================

_HTML_STYLE = """
:root {
  color-scheme: light dark;
  --bg: #f0eee6;
  --card: #ffffff;
  --fg: #191919;
  --muted: #6e6b62;
  --line: #e5e2d9;
  --user-bg: #faf9f5;
  --user-accent: #78736a;
  --ai-bg: #ffffff;
  --ai-accent: #c96442;
  --tool-bg: #faf9f5;
  --code-bg: #f5f3ed;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1b1a18;
    --card: #262624;
    --fg: #f5f4ef;
    --muted: #a19d93;
    --line: #3a3936;
    --user-bg: #30302e;
    --user-accent: #a8a296;
    --ai-bg: #262624;
    --ai-accent: #e08a6b;
    --tool-bg: #30302e;
    --code-bg: #30302e;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0;
  padding: 32px 16px 64px;
  background: var(--bg);
  color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei",
               "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", sans-serif;
  /* 中文正文行距放大到 1.8，标点不挤在一起 */
  line-height: 1.8;
  font-size: 16px;
}
.wrap { max-width: 900px; margin: 0 auto; }
.conv-head {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 20px 24px;
  margin-bottom: 24px;
}
.conv-head h1 { margin: 0 0 12px; font-size: 24px; line-height: 1.5; }
.meta { margin: 0; color: var(--muted); font-size: 14px; }
.meta span { margin-right: 16px; white-space: nowrap; }
.msg {
  background: var(--card);
  border: 1px solid var(--line);
  border-left: 4px solid var(--line);
  border-radius: 10px;
  padding: 16px 20px;
  margin: 0 0 18px;
}
.msg-user { background: var(--user-bg); border-left-color: var(--user-accent); }
.msg-assistant { background: var(--ai-bg); border-left-color: var(--ai-accent); }
.msg-tool { background: var(--tool-bg); border-left-color: var(--muted); }
.msg-head {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: baseline;
  margin-bottom: 10px;
  font-size: 14px;
  color: var(--muted);
}
.who { font-weight: 600; color: var(--fg); font-size: 15px; }
.para { white-space: pre-wrap; word-wrap: break-word; overflow-wrap: anywhere; margin: 0 0 12px; }
.para:last-child { margin-bottom: 0; }
pre {
  background: var(--code-bg);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
  overflow-x: auto;
  margin: 0 0 12px;
  line-height: 1.6;
}
pre, code {
  font-family: "Cascadia Mono", Consolas, "SF Mono", Menlo, "Courier New", monospace;
  font-size: 13.5px;
}
.lang { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
details {
  background: var(--tool-bg);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 12px;
  margin: 0 0 12px;
}
details > summary { cursor: pointer; color: var(--muted); font-size: 14px; user-select: none; }
details[open] > summary { margin-bottom: 8px; }
details pre { margin-bottom: 0; background: transparent; border: none; padding: 0; }
.attachment { color: var(--muted); font-size: 14px; margin: 0 0 12px; }
.foot { color: var(--muted); font-size: 13px; text-align: center; margin-top: 28px; }
@media (max-width: 640px) {
  body { padding: 16px 8px 40px; font-size: 15px; }
  .msg, .conv-head { padding: 14px 14px; }
}
""".strip()


def _esc(value: Any) -> str:
    """所有进入 HTML 的对话内容都必须过这里。

    导出的是别人机器上的聊天记录，正文里出现 <script> 和 & 是常态，
    漏一处转义就是一个可以双击触发的存储型 XSS。
    """
    return html.escape("" if value is None else str(value), quote=True)


class HtmlExporter(BaseExporter):
    format_id = "html"
    label = "HTML 网页（单文件）"
    extension = ".html"

    def render(self, conv: Conversation) -> str:
        visible = self._collect(conv)
        title = conv.title or "(无标题对话)"

        chunks: List[str] = []
        chunks.append("<!DOCTYPE html>")
        chunks.append('<html lang="zh-CN">')
        chunks.append("<head>")
        chunks.append('<meta charset="utf-8">')
        chunks.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
        chunks.append(f"<title>{_esc(title)}</title>")
        chunks.append(f"<style>\n{_HTML_STYLE}\n</style>")
        chunks.append("</head>")
        chunks.append("<body>")
        chunks.append('<div class="wrap">')
        chunks.append(self._header(conv, visible, title))

        for msg, role, body in visible:
            chunks.append(self._article(msg, role, body))

        chunks.append(
            f'<div class="foot">导出时间：{_esc(_fmt_dt(datetime.now()))} · 多程序对话导出工具</div>'
        )
        chunks.append("</div>")
        chunks.append("</body>")
        chunks.append("</html>")
        return "\n".join(chunks) + "\n"

    # ---- 结构 ----

    def _collect(self, conv: Conversation) -> List[Tuple[Message, Role, str]]:
        """与 MarkdownExporter._build_markdown 同一套可见性规则。"""
        visible: List[Tuple[Message, Role, str]] = []
        for msg in conv.messages:
            eff_role = effective_role(msg)
            if eff_role is None:
                if not (self.include_tool_messages and msg.role == Role.TOOL):
                    continue
                body = self._body(msg, conv.source_app)
                if not body.strip():
                    continue
                visible.append((msg, Role.TOOL, body))
                continue
            body = self._body(msg, conv.source_app)
            if not body.strip():
                continue
            visible.append((msg, eff_role, body))
        return visible

    def _header(self, conv: Conversation, visible, title: str) -> str:
        items: List[str] = []
        if conv.source_app:
            items.append(f"来源程序：{conv.source_app}")
        if conv.created_at:
            items.append(f"创建：{_fmt_dt(conv.created_at)}")
        if conv.updated_at:
            items.append(f"更新：{_fmt_dt(conv.updated_at)}")
        if conv.model:
            items.append(f"模型：{conv.model}")
        if visible:
            users = sum(1 for _m, role, _b in visible if role == Role.USER)
            asst = sum(1 for _m, role, _b in visible if role == Role.ASSISTANT)
            tools = sum(1 for _m, role, _b in visible if role == Role.TOOL)
            items.append(f"消息：{len(visible)} 条（{users} 问 / {asst} 答）")
            if tools:
                items.append(f"工具记录：{tools} 条")
        total_tokens = _total_tokens(conv)
        if total_tokens:
            items.append(f"Token：约 {total_tokens:,}")

        meta = "".join(f"<span>{_esc(item)}</span>" for item in items)
        return (
            '<header class="conv-head">'
            f"<h1>{_esc(title)}</h1>"
            f'<div class="meta">{meta}</div>'
            "</header>"
        )

    def _article(self, msg: Message, role: Role, body: str) -> str:
        css_role = {
            Role.USER: "msg-user",
            Role.ASSISTANT: "msg-assistant",
            Role.TOOL: "msg-tool",
            Role.SYSTEM: "msg-system",
        }.get(role, "msg-system")
        label = {
            Role.USER: "👤 用户",
            Role.ASSISTANT: "🤖 AI 助手",
            Role.SYSTEM: "⚙️ 系统",
            Role.TOOL: "🔧 工具",
        }.get(role, str(role))

        head = [f'<span class="who">{_esc(label)}</span>']
        if self.include_timestamp and msg.timestamp:
            head.append(f"<span>{_esc(_fmt_dt(msg.timestamp))}</span>")
        if msg.model:
            head.append(f"<span>{_esc(msg.model)}</span>")

        if role == Role.TOOL:
            # 独立的 role=tool 消息整块折叠：完整保留，但不打断阅读节奏。
            body = f"<details><summary>🔧 工具记录</summary>{body}</details>"

        return (
            f'<article class="msg {css_role}">'
            f'<div class="msg-head">{"".join(head)}</div>'
            f'<div class="msg-body">{body}</div>'
            "</article>"
        )

    # ---- 片段 ----

    @staticmethod
    def _para(text: str) -> str:
        return f'<div class="para">{_esc(text)}</div>'

    @staticmethod
    def _code(content: str, language: Optional[str]) -> str:
        lang = f'<span class="lang">{_esc(language)}</span>' if language else ""
        return f"<pre>{lang}<code>{_esc(content)}</code></pre>"

    @staticmethod
    def _details(summary: str, content: str, css_class: str) -> str:
        return (
            f'<details class="{css_class}"><summary>{_esc(summary)}</summary>'
            f"<pre><code>{_esc(content)}</code></pre></details>"
        )

    @staticmethod
    def _attachment(kind: str, name: str) -> str:
        return f'<div class="attachment">{_esc(kind)}：<code>{_esc(name)}</code></div>'

    def _body(self, msg: Message, source_app: str) -> str:
        if not msg.parts:
            text = strip_internal_context(msg.content or "", source_app=source_app) or (msg.content or "").strip()
            return self._para(text) if text else ""

        fragments: List[str] = []
        thinking_parts = [p.content for p in msg.parts if p.type == MessagePartType.THINKING and p.content]
        tool_results = [p for p in msg.parts if p.type == MessagePartType.TOOL_RESULT]
        has_body = False

        index = 0
        while index < len(msg.parts):
            part = msg.parts[index]

            if part.type == MessagePartType.TEXT and part.content:
                text = strip_internal_context(part.content, source_app=source_app) or part.content.strip()
                if text:
                    fragments.append(self._para(text))
                    has_body = True

            elif part.type == MessagePartType.CODE:
                fragments.append(self._code(part.content, part.language))
                has_body = True

            elif part.type == MessagePartType.THINKING:
                # 合并相邻思考块，否则一条消息会堆出几十个折叠框。
                run: List[str] = []
                while index < len(msg.parts) and msg.parts[index].type == MessagePartType.THINKING:
                    chunk = msg.parts[index].content
                    if chunk and chunk.strip():
                        run.append(chunk.strip())
                    index += 1
                index -= 1
                if self.include_thinking and run:
                    fragments.append(self._details("💭 思考过程", "\n\n---\n\n".join(run), "thinking"))

            elif part.type == MessagePartType.TOOL_CALL:
                name = part.tool_name or "unknown tool"
                fragments.append(
                    self._details(f"🔧 调用工具：{name}", part.tool_input or part.content or "", "tool")
                )

            elif part.type == MessagePartType.TOOL_RESULT:
                fragments.append(
                    self._details("📎 工具返回结果", str(part.tool_output or part.content or ""), "tool")
                )

            elif part.type == MessagePartType.FILE:
                fragments.append(self._attachment("📄 附件", part.file_name or "file"))

            elif part.type == MessagePartType.IMAGE:
                fragments.append(self._attachment("🖼️ 图片", part.file_name or "image.png"))

            index += 1

        if not has_body:
            fallback = MarkdownExporter._fallback_body(thinking_parts, tool_results, source_app)
            if fallback:
                fragments.insert(0, self._para(fallback))

        if not fragments:
            text = strip_internal_context(msg.content or "", source_app=source_app) or (msg.content or "").strip()
            if text:
                fragments.append(self._para(text))
        return "".join(fragments)


# ============================================================================
# JSON：无损结构化转储
# ============================================================================


def _json_default(value: Any) -> Any:
    """metadata 里可能混进适配器塞的任何东西，绝不能因为一个值让导出崩掉。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", errors="replace")
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    return str(value)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if isinstance(value, (datetime, date)) else None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def part_to_dict(part: MessagePart) -> Dict[str, Any]:
    return {
        "type": _enum_value(part.type),
        "content": part.content,
        "tool_name": part.tool_name,
        "tool_input": part.tool_input,
        "tool_output": part.tool_output,
        "file_name": part.file_name,
        "language": part.language,
        "metadata": part.metadata or {},
    }


def message_to_dict(msg: Message) -> Dict[str, Any]:
    return {
        "role": _enum_value(msg.role),
        "content": msg.content,
        "timestamp": _iso(msg.timestamp),
        "message_id": msg.message_id,
        "parent_id": msg.parent_id,
        "model": msg.model,
        "token_usage": msg.token_usage,
        "metadata": msg.metadata or {},
        "parts": [part_to_dict(part) for part in msg.parts],
    }


def conversation_to_dict(conv: Conversation) -> Dict[str, Any]:
    return {
        "id": conv.id,
        "title": conv.title,
        "source_app": conv.source_app,
        "model": conv.model,
        "created_at": _iso(conv.created_at),
        "updated_at": _iso(conv.updated_at),
        "metadata": conv.metadata or {},
        "messages": [message_to_dict(msg) for msg in conv.messages],
    }


def conversation_from_dict(data: Dict[str, Any]) -> Conversation:
    """JSON 反序列化。信封和裸对话体都接受，方便把导出文件重新读回来。"""
    payload = data.get("conversation") if isinstance(data, dict) and "conversation" in data else data
    payload = payload or {}

    messages: List[Message] = []
    for raw in payload.get("messages") or []:
        parts = [
            MessagePart(
                type=_to_part_type(item.get("type")),
                content=item.get("content") or "",
                tool_name=item.get("tool_name"),
                tool_input=item.get("tool_input"),
                tool_output=item.get("tool_output"),
                file_name=item.get("file_name"),
                language=item.get("language"),
                metadata=item.get("metadata") or {},
            )
            for item in raw.get("parts") or []
        ]
        messages.append(
            Message(
                role=_to_role(raw.get("role")),
                content=raw.get("content") or "",
                timestamp=_parse_iso(raw.get("timestamp")),
                message_id=raw.get("message_id"),
                parent_id=raw.get("parent_id"),
                parts=parts,
                model=raw.get("model"),
                token_usage=raw.get("token_usage"),
                metadata=raw.get("metadata") or {},
            )
        )

    return Conversation(
        id=payload.get("id") or "",
        title=payload.get("title") or "",
        created_at=_parse_iso(payload.get("created_at")),
        updated_at=_parse_iso(payload.get("updated_at")),
        messages=messages,
        model=payload.get("model"),
        metadata=payload.get("metadata") or {},
        source_app=payload.get("source_app") or "",
    )


def _to_role(value: Any) -> Role:
    try:
        return Role(value)
    except (ValueError, TypeError):
        return Role.SYSTEM


def _to_part_type(value: Any) -> MessagePartType:
    try:
        return MessagePartType(value)
    except (ValueError, TypeError):
        return MessagePartType.TEXT


class JsonExporter(BaseExporter):
    format_id = "json"
    label = "JSON 结构化数据"
    extension = ".json"

    def render(self, conv: Conversation) -> str:
        # 有意忽略 include_thinking / include_tool_messages：
        # JSON 的契约是无损转储，过滤过的 JSON 是最糟糕的一种数据——
        # 看着完整，其实缺了正文，且没有任何痕迹说明缺了什么。
        payload = {
            "schema": JSON_SCHEMA_ID,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "conversation": conversation_to_dict(conv),
        }
        return json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)


# ============================================================================
# 纯文本：只有用户/AI 正文
# ============================================================================


class TextExporter(BaseExporter):
    format_id = "txt"
    label = "纯文本（仅正文）"
    extension = ".txt"

    def render(self, conv: Conversation) -> str:
        lines: List[str] = [conv.title or "(无标题对话)"]
        meta: List[str] = []
        if conv.source_app:
            meta.append(f"来源程序：{conv.source_app}")
        if conv.updated_at:
            meta.append(f"更新时间：{_fmt_dt(conv.updated_at)}")
        if conv.model:
            meta.append(f"模型：{conv.model}")
        if meta:
            lines.append(" · ".join(meta))
        lines.append("=" * 48)
        lines.append("")
        lines.append(plain_preview_text(conv))
        lines.append("")
        return "\n".join(lines)


# ============================================================================
# 注册表与批量导出
# ============================================================================

_EXPORTER_CLASSES: Tuple[type, ...] = (
    MarkdownFormatExporter,
    HtmlExporter,
    JsonExporter,
    TextExporter,
)

#: GUI 下拉框直接遍历这个字典；key 是稳定 id，写进设置文件里的就是它。
EXPORTERS: Dict[str, BaseExporter] = {cls.format_id: cls() for cls in _EXPORTER_CLASSES}

#: 保持顺序的 (id, 中文标签)，给 GUI 下拉框用。
FORMAT_CHOICES: List[Tuple[str, str]] = [(cls.format_id, cls.label) for cls in _EXPORTER_CLASSES]

DEFAULT_FORMAT = MarkdownFormatExporter.format_id


def get_exporter(format_id: str, **options: Any) -> BaseExporter:
    """按 id 取一个新的导出器实例（带选项时必须新建，EXPORTERS 里的是共享单例）。"""
    for cls in _EXPORTER_CLASSES:
        if cls.format_id == format_id:
            return cls(**options)
    raise ValueError(f"未知的导出格式：{format_id}")


def format_label(format_id: str) -> str:
    exporter = EXPORTERS.get(format_id)
    return exporter.label if exporter else format_id


def suggested_filename(conv: Conversation, format_id: str = DEFAULT_FORMAT) -> str:
    return get_exporter(format_id).suggested_filename(conv)


def export_conversation(
    conv: Conversation,
    path: Optional[str] = None,
    format_id: str = DEFAULT_FORMAT,
    **options: Any,
) -> str:
    return get_exporter(format_id, **options).export(conv, path)


def unique_path(path: str) -> str:
    """同名文件永不覆盖：批量导出里两条同标题对话是常态。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    counter = 1
    candidate = f"{base}_{counter}{ext}"
    while os.path.exists(candidate):
        counter += 1
        candidate = f"{base}_{counter}{ext}"
    return candidate


def batch_export(
    conversations: Sequence[Conversation],
    output_dir: str,
    format_id: str = DEFAULT_FORMAT,
    progress_callback: Optional[Callable[[int, int, Optional[str]], None]] = None,
    loader: Optional[Callable[[Conversation], Optional[Conversation]]] = None,
    **options: Any,
) -> Tuple[int, List[Tuple[str, str]]]:
    """批量导出，返回 (成功条数, [(标题, 失败原因), ...])。

    progress_callback(index, total, path)：每条对话都会回调一次，
    失败时 path 为 None，否则进度条会卡在失败的那条上不动。
    loader：列表里的对话通常没有 messages，需要时由调用方按 id 现读；
    读不出来就记失败，绝不写一个只有元数据的空壳文件冒充成功。
    """
    exporter = get_exporter(format_id, **options)
    os.makedirs(output_dir, exist_ok=True)

    total = len(conversations)
    exported = 0
    failures: List[Tuple[str, str]] = []

    def report(index: int, path: Optional[str]) -> None:
        if not progress_callback:
            return
        try:
            progress_callback(index, total, path)
        except Exception:
            # 进度显示出错不该带走整批导出。
            pass

    for index, conv in enumerate(conversations, start=1):
        title = conv.title or str(conv.id)
        full = conv

        if loader is not None and not conv.messages:
            try:
                loaded = loader(conv)
            except Exception as exc:
                failures.append((title, f"读取失败：{exc}"))
                report(index, None)
                continue
            if loaded is None:
                failures.append((title, "读取失败：来源返回空"))
                report(index, None)
                continue
            full = loaded

        title = full.title or str(full.id)
        if not full.messages:
            failures.append((title, "没有可导出的消息"))
            report(index, None)
            continue

        path = unique_path(os.path.join(output_dir, exporter.suggested_filename(full)))
        try:
            exporter.export(full, path)
        except Exception as exc:
            failures.append((title, f"写入失败：{exc}"))
            report(index, None)
            continue

        exported += 1
        report(index, path)

    return exported, failures


# ============================================================================
# 小工具
# ============================================================================


def _fmt_dt(value: Optional[datetime]) -> str:
    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _total_tokens(conv: Conversation) -> int:
    total = 0
    for msg in conv.messages:
        if msg.token_usage:
            total += msg.token_usage.get("total_tokens", msg.token_usage.get("total", 0)) or 0
    return total

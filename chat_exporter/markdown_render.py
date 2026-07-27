"""Markdown 解析与渲染：一份解析器，两个后端。

之前这套解析器长在 GUI 类里，只服务预览面板；HtmlExporter 那边是
`<div class="para">{转义后的原文}</div>`，靠 CSS 的 pre-wrap 保留换行。
结果是导出的 HTML 里 `**加粗**`、`# 标题`、表格竖线全是字面量——
**导出文件的渲染质量反而不如程序内的预览**。这个模块把解析抽出来两边共用。

解析产物是**逐行**的（Line），不是段落级的：Tk 后端本来就是逐行 insert，
保持逐行才能让抽取前后的输出逐字节一致（tests/md_golden.json 钉死了这一点）。
HTML 后端再把连续的同类行合并成 <ul>/<ol>/<table>/<blockquote>。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape
from typing import List, Sequence, Tuple

# ---------------------------------------------------------------- 行内标记

INLINE_MD = re.compile(
    r"`([^`\n]+)`"                        # 1 行内代码
    r"|\*\*([^*\n]+?)\*\*"                # 2 加粗
    r"|\[([^\]\n]+)\]\(([^)\s]+)\)"       # 3 链接文字 4 地址
)

# 标题专用：只剥符号、不分段。它与 INLINE_MD 有两处**刻意保留**的差异，
# 属于既有行为，动它会改变预览里已经渲染出来的结果：
#   1. 交替顺序不同（这里加粗在前、代码在后），`` `**a**` `` 这类重叠输入归属不同；
#   2. 链接地址允许为空——`[文字]()` 这里会被剥掉，INLINE_MD 则要求地址非空。
STRIP_INLINE = re.compile(r"\*\*([^*\n]+?)\*\*|`([^`\n]+)`|\[([^\]\n]+)\]\([^)\s]*\)")

# 不要写成 (.+?)\s*#*\s*$ ——惰性组配尾部 #* 是 O(n²) 回溯，
# 一行几千个 '#' 就能把 Tk 主线程冻住几十秒。贪婪匹配后用 rstrip 剥尾。
MD_HEADER = re.compile(r"^(#{1,4})\s+(.+)$")
MD_BULLET = re.compile(r"^(\s*)[-*+•]\s+(.+)$")
MD_NUMBERED = re.compile(r"^(\s*)(\d{1,3})[.、)]\s+(.+)$")
MD_HR = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
MD_QUOTE = re.compile(r"^\s*>\s?(.*)$")
MD_TABLE = re.compile(r"^\s*\|.*\|\s*$")
FENCE_CLOSE = re.compile(r"`{3,}\s*$")

# 表格分隔行的单元格：--- / :--- / ---: / :---:
_TABLE_SEP_CELL = re.compile(r"^:?-+:?$")

# 允许出现在 href 里的协议。导出的 HTML 会被浏览器打开，而正文来自
# 第三方客户端的对话记录——javascript: / data: 这类必须挡掉。
_SAFE_SCHEMES = ("http://", "https://", "mailto:", "ftp://", "#", "/", "./", "../")


# ---------------------------------------------------------------- 中间表示

@dataclass(slots=True)
class Span:
    """行内片段。kind ∈ {text, code, bold, link}。"""

    kind: str
    text: str
    href: str = ""


@dataclass(slots=True)
class Line:
    """一行块级结构。kind ∈ {blank, heading, hr, quote, table, bullet, numbered, para}。"""

    kind: str
    spans: Tuple[Span, ...] = ()
    level: int = 0          # heading 层级 1-4
    indent: bool = False    # 列表项是否缩进了一层
    number: str = ""        # 有序列表的序号原文
    plain: str = ""         # heading 用：按既有规则剥完标记的纯文本
    raw: str = ""           # table 用：整行原文


@dataclass(slots=True)
class Block:
    """顶层块。kind ∈ {prose, code}。"""

    kind: str
    lines: Tuple[Line, ...] = ()
    lang: str = ""
    text: str = ""


# ---------------------------------------------------------------- 解析

def parse_inline(text: str) -> Tuple[Span, ...]:
    """把一行拆成行内片段；没有任何标记时返回单个 text 片段。"""
    spans: List[Span] = []
    cursor = 0
    for match in INLINE_MD.finditer(text):
        if match.start() > cursor:
            spans.append(Span("text", text[cursor:match.start()]))
        if match.group(1) is not None:
            spans.append(Span("code", match.group(1)))
        elif match.group(2) is not None:
            spans.append(Span("bold", match.group(2)))
        else:
            spans.append(Span("link", match.group(3), match.group(4)))
        cursor = match.end()
    if cursor < len(text):
        spans.append(Span("text", text[cursor:]))
    return tuple(spans) if spans else (Span("text", text),)


def _strip_inline(text: str) -> str:
    """标题用：只留文字。见 STRIP_INLINE 上方关于两处差异的说明。"""
    return STRIP_INLINE.sub(lambda g: g.group(1) or g.group(2) or g.group(3), text)


def parse_lines(chunk: str) -> Tuple[Line, ...]:
    """逐行识别块级结构。"""
    lines: List[Line] = []
    for line in chunk.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append(Line("blank"))
            continue

        match = MD_HEADER.match(stripped)
        if match:
            level = min(len(match.group(1)), 4)
            body = re.sub(r"\s+#+\s*$", "", match.group(2))
            plain = _strip_inline(body)
            # 剥完只剩空白的（例如 "## **"）不算标题，往下按正文走
            if plain.strip():
                lines.append(Line("heading", parse_inline(body), level=level, plain=plain))
                continue

        if MD_HR.match(stripped):
            lines.append(Line("hr"))
            continue

        match = MD_QUOTE.match(line)
        if match:
            lines.append(Line("quote", parse_inline(match.group(1))))
            continue

        if MD_TABLE.match(line):
            lines.append(Line("table", raw=line.rstrip()))
            continue

        match = MD_BULLET.match(line)
        if match:
            lines.append(Line("bullet", parse_inline(match.group(2)), indent=bool(match.group(1))))
            continue

        match = MD_NUMBERED.match(line)
        if match:
            lines.append(
                Line("numbered", parse_inline(match.group(3)),
                     indent=bool(match.group(1)), number=match.group(2))
            )
            continue

        lines.append(Line("para", parse_inline(line)))
    return tuple(lines)


def parse(text: str) -> Tuple[Block, ...]:
    """把正文拆成排版块与 ``` 代码块。

    按 CommonMark 语义配对围栏：开栏行记住语言；栏内只有『纯 ``` 行』才闭合
    ——闭合围栏不允许带 info string，带语言的行属于内容。无状态的 re.split
    会把代码块里的 ```python 行（比如整段被引用的 markdown 教程）当成闭合，
    之后正文和代码全部互换。未闭合的块照收，绝不丢字。
    """
    blocks: List[Block] = []
    prose: List[str] = []
    code: List[str] = []
    lang = ""
    in_code = False

    for line in text.split("\n"):
        stripped = line.strip()
        if not in_code and stripped.startswith("```"):
            if prose:
                blocks.append(Block("prose", parse_lines("\n".join(prose))))
                prose = []
            lang = stripped.lstrip("`").strip()
            in_code = True
            continue
        if in_code and FENCE_CLOSE.fullmatch(stripped):
            blocks.append(Block("code", lang=lang, text="\n".join(code)))
            code = []
            in_code = False
            continue
        (code if in_code else prose).append(line)

    if prose:
        blocks.append(Block("prose", parse_lines("\n".join(prose))))
    if code or in_code:
        blocks.append(Block("code", lang=lang, text="\n".join(code)))
    return tuple(blocks)


# ---------------------------------------------------------------- Tk 后端

def _merge(base, extra):
    """组合 Tk 文本标签；Text.insert 的 tag 参数接受元组。"""
    if isinstance(base, tuple):
        return base + (extra,)
    return (base, extra)


def _tk_inline(spans: Sequence[Span], body_tag):
    out = []
    for span in spans:
        if span.kind == "code":
            out.append((span.text, "inline_code"))
        elif span.kind == "bold":
            out.append((span.text, _merge(body_tag, "md_bold")))
        elif span.kind == "link":
            out.append((span.text, _merge(body_tag, "md_link")))
        else:
            out.append((span.text, body_tag))
    return out


def to_tk_segments(blocks: Sequence[Block], body_tag) -> List[tuple]:
    """渲染成 (文本, 标签) 序列，直接喂给 tk.Text.insert。"""
    segments: List[tuple] = []
    for block in blocks:
        if block.kind == "code":
            if not block.text.strip():
                continue
            if block.lang:
                segments.append((f"{block.lang}\n", "code_lang"))
            segments.append((block.text.rstrip("\n") + "\n", "code_block"))
            continue

        if not any(line.kind != "blank" for line in block.lines):
            continue

        for line in _trim_blank_edges(block.lines):
            if line.kind == "blank":
                segments.append(("\n", body_tag))
            elif line.kind == "heading":
                segments.append((line.plain + "\n", f"md_h{line.level}"))
            elif line.kind == "hr":
                segments.append(("─" * 42 + "\n", "md_hr"))
            elif line.kind == "quote":
                segments.append(("▍ ", "md_quote_bar"))
                segments.extend(_tk_inline(line.spans, "md_quote"))
                segments.append(("\n", "md_quote"))
            elif line.kind == "table":
                segments.append((line.raw + "\n", "md_table"))
            elif line.kind in ("bullet", "numbered"):
                indent = "      " if line.indent else ""
                marker = "•  " if line.kind == "bullet" else f"{line.number}.  "
                segments.append((indent + marker, _merge(body_tag, "md_marker")))
                segments.extend(_tk_inline(line.spans, _merge(body_tag, "md_list")))
                segments.append(("\n", body_tag))
            else:
                segments.extend(_tk_inline(line.spans, body_tag))
                segments.append(("\n", body_tag))
    return segments


def _trim_blank_edges(lines: Sequence[Line]) -> Sequence[Line]:
    """去掉块首尾的空行——原实现是对 chunk 做 strip("\\n") 后再逐行解析。"""
    start, end = 0, len(lines)
    while start < end and lines[start].kind == "blank":
        start += 1
    while end > start and lines[end - 1].kind == "blank":
        end -= 1
    return lines[start:end]


def render_tk(text: str, body_tag) -> List[tuple]:
    """解析 + Tk 渲染。空结果但原文非空时整段回退，绝不丢字。"""
    segments = to_tk_segments(parse(text), body_tag)
    if not segments and text.strip():
        segments.append((text.strip("\n") + "\n", body_tag))
    return segments


# ---------------------------------------------------------------- HTML 后端

def _safe_href(href: str) -> str:
    """只放行已知协议，其余当普通文字处理。"""
    candidate = href.strip()
    # 无协议 authority 会继承导出文件的 file: 协议。浏览器按 WHATWG 把
    # 反斜杠当斜杠，//host 与 /\host 在 Windows 上会落到 UNC/SMB。
    if candidate[:2].replace("\\", "/") == "//":
        return ""
    lowered = candidate.lower()
    if lowered.startswith(_SAFE_SCHEMES):
        return candidate
    return ""


def _html_inline(spans: Sequence[Span]) -> str:
    out = []
    for span in spans:
        if span.kind == "code":
            out.append(f"<code>{escape(span.text)}</code>")
        elif span.kind == "bold":
            out.append(f"<strong>{escape(span.text)}</strong>")
        elif span.kind == "link":
            href = _safe_href(span.href)
            if href:
                out.append(
                    f'<a href="{escape(href, quote=True)}" '
                    f'rel="noopener noreferrer">{escape(span.text)}</a>'
                )
            else:
                # 协议不在白名单里：原样显示成 markdown 源码，别悄悄吞掉
                out.append(escape(f"[{span.text}]({span.href})"))
        else:
            out.append(escape(span.text))
    return "".join(out)


def _is_table_separator(raw: str) -> bool:
    cells = [c.strip() for c in raw.strip().strip("|").split("|")]
    nonempty = [cell for cell in cells if cell]
    return bool(nonempty) and all(_TABLE_SEP_CELL.fullmatch(cell) for cell in nonempty)


def _table_cells(raw: str) -> List[str]:
    return [c.strip() for c in raw.strip().strip("|").split("|")]


def _render_table(rows: Sequence[Line]) -> str:
    raws = [r.raw for r in rows]
    header: List[str] = []
    body_start = 0
    if len(raws) >= 2 and _is_table_separator(raws[1]):
        header = _table_cells(raws[0])
        body_start = 2
    out = ["<table>"]
    if header:
        cells = "".join(f"<th>{_html_inline(parse_inline(c))}</th>" for c in header)
        out.append(f"<thead><tr>{cells}</tr></thead>")
    body_rows = raws[body_start:]
    if body_rows:
        out.append("<tbody>")
        for raw in body_rows:
            cells = "".join(f"<td>{_html_inline(parse_inline(c))}</td>" for c in _table_cells(raw))
            out.append(f"<tr>{cells}</tr>")
        out.append("</tbody>")
    out.append("</table>")
    return "".join(out)


def _render_list(items: Sequence[Line], ordered: bool) -> str:
    """一级嵌套的列表，并保留有序列表原始编号。"""
    if not items:
        return ""
    tag = "ol" if ordered else "ul"

    def number(item: Line | None) -> int | None:
        # 真实对话里会出现只有缩进项、没有父项的残缺 Markdown。
        # first_top 此时是 None，导出必须修复结构而不是让整条对话失败。
        if not ordered or item is None or not item.number:
            return None
        return int(item.number)

    def open_tag(item: Line | None) -> str:
        value = number(item)
        if not ordered or value in (None, 1):
            return f"<{tag}>"
        return f'<ol start="{value}">'

    first_top = next((item for item in items if not item.indent), None)
    out = [open_tag(first_top)]
    top_expected = number(first_top) or 1
    nested_expected = 1
    nested_open = False
    item_open = False

    for item in items:
        content = _html_inline(item.spans)
        value = number(item)
        if item.indent:
            if not item_open:                 # 没有父项，先补一个空的
                out.append("<li>")
                item_open = True
            if not nested_open:
                out.append(open_tag(item))
                nested_expected = value or 1
                nested_open = True
            value_attr = ""
            if ordered and value is not None and value != nested_expected:
                value_attr = f' value="{value}"'
                nested_expected = value
            out.append(f"<li{value_attr}>{content}</li>")
            if ordered:
                nested_expected += 1
        else:
            if nested_open:
                out.append(f"</{tag}>")
                nested_open = False
            if item_open:
                out.append("</li>")
            value_attr = ""
            if ordered and value is not None and value != top_expected:
                value_attr = f' value="{value}"'
                top_expected = value
            out.append(f"<li{value_attr}>{content}")
            if ordered:
                top_expected += 1
            item_open = True
    if nested_open:
        out.append(f"</{tag}>")
    if item_open:
        out.append("</li>")
    out.append(f"</{tag}>")
    return "".join(out)


def _render_prose(lines: Sequence[Line]) -> List[str]:
    """把逐行结构合并成 HTML 块级元素。"""
    out: List[str] = []
    buffer: List[Line] = []
    buffer_kind = ""

    def flush():
        nonlocal buffer, buffer_kind
        if not buffer:
            return
        if buffer_kind == "para":
            body = "\n".join(_html_inline(line.spans) for line in buffer)
            out.append(f'<div class="para">{body}</div>')
        elif buffer_kind == "quote":
            body = "\n".join(_html_inline(line.spans) for line in buffer)
            out.append(f'<blockquote class="para">{body}</blockquote>')
        elif buffer_kind == "table":
            out.append(_render_table(buffer))
        elif buffer_kind in ("bullet", "numbered"):
            out.append(_render_list(buffer, ordered=buffer_kind == "numbered"))
        buffer = []
        buffer_kind = ""

    for line in _trim_blank_edges(lines):
        if line.kind == "blank":
            flush()
            continue
        if line.kind == "heading":
            flush()
            out.append(f"<h{line.level}>{_html_inline(line.spans)}</h{line.level}>")
            continue
        if line.kind == "hr":
            flush()
            out.append("<hr>")
            continue
        if line.kind != buffer_kind:
            flush()
            buffer_kind = line.kind
        buffer.append(line)
    flush()
    return out


def render_html(text: str) -> str:
    """解析 + HTML 渲染。返回若干块级元素拼成的片段（不含外层容器）。"""
    out: List[str] = []
    for block in parse(text):
        if block.kind == "code":
            if not block.text.strip() and not block.lang:
                continue
            lang = f'<span class="lang">{escape(block.lang)}</span>' if block.lang else ""
            out.append(f"<pre>{lang}<code>{escape(block.text.rstrip(chr(10)))}</code></pre>")
        else:
            out.extend(_render_prose(block.lines))
    if not out and text.strip():
        out.append(f'<div class="para">{escape(text.strip(chr(10)))}</div>')
    return "".join(out)

"""Markdown 渲染的共用语料。

抽取解析器时用它锁住 Tk 侧的输出（见 tests/md_golden.json），
同时给 HTML 后端当输入。往里加用例是安全的——加完重新生成金样即可；
改动**已有**条目则必须先确认那是有意的行为变更。
"""
from __future__ import annotations

CORPUS: dict[str, str] = {
    "empty": "",
    "blank_only": "\n\n   \n",
    "plain": "就是一段普通的话。",
    "multi_para": "第一段。\n\n第二段。\n\n第三段。",
    "headers": "# 一级\n## 二级\n### 三级\n#### 四级\n##### 五级不算标题",
    "header_trailing_hashes": "## 标题 ##",
    "header_with_inline": "## 参考 **重点** 与 `code` 与 [文档](https://example.com/a?b=1)",
    "header_only_markup": "## **",
    "bold": "这是 **加粗** 文字。",
    "bold_multiple": "**一** 普通 **二** 普通 **三**",
    "inline_code": "运行 `pip install x` 就好。",
    "link": "见 [文档](https://example.com/path) 一节。",
    "link_and_code": "把 `--flag` 写进 [配置](https://e.com) 里。",
    "bullets": "- 第一项\n- 第二项\n- 第三项",
    "bullets_nested": "- 顶层\n  - 嵌套一\n  - 嵌套二\n- 又一个顶层",
    "bullets_star": "* 星号项\n+ 加号项\n• 圆点项",
    "bullets_with_inline": "- 装 `foo` 包\n- 看 **重点**\n- 读 [文档](https://e.com)",
    "numbered": "1. 第一步\n2. 第二步\n3. 第三步",
    "numbered_cn": "1、中文顿号\n2)  右括号",
    "numbered_nested": "1. 顶层\n   1. 嵌套",
    "quote": "> 这是引用。\n> 第二行。",
    "quote_with_inline": "> 引用里有 **加粗** 和 `代码`",
    "quote_empty": ">",
    "hr_dash": "上面\n\n---\n\n下面",
    "hr_star": "***",
    "hr_underscore": "___",
    "table": "| 列A | 列B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |",
    "table_aligned": "| 左 | 中 | 右 |\n|:---|:---:|---:|\n| a | b | c |",
    "table_no_sep": "| 只有 | 一行 |",
    "code_plain": "```\nprint(1)\n```",
    "code_lang": "```python\nprint(1)\nprint(2)\n```",
    "code_unclosed": "```python\nprint(1)\n没有闭合",
    "code_nested_fence": (
        "教程如下：\n"
        "```markdown\n"
        "先写围栏：\n"
        "```python\n"
        "print(1)\n"
        "```\n"
        "真正文结束"
    ),
    "code_then_prose": "```py\nx=1\n```\n后面还有正文 **加粗**。",
    "code_empty": "```python\n```",
    "mixed_doc": (
        "# 根因\n\n"
        "问题出在缓存键上：它只用了 `user_id`，没有把 `locale` 算进去。\n\n"
        "## 修复\n\n"
        "1. 把 `locale` 加进键\n"
        "2. 清一次旧缓存\n\n"
        "```python\ndef key(uid, locale):\n    return f\"{uid}:{locale}\"\n```\n\n"
        "> 注意：上线前先灰度。\n\n"
        "| 环境 | 状态 |\n|---|---|\n| dev | OK |\n\n"
        "---\n\n"
        "参考 [文档](https://example.com)。"
    ),
    "html_injection": "<script>alert(1)</script> & \"引号\" 与 <b>标签</b>",
    "html_in_code": "```\n<script>alert(1)</script>\n```",
    "html_in_bold": "**<b>粗</b>**",
    "html_in_link": "[<img>](https://e.com/<script>)",
    "unicode": "emoji 😀 与 中文 と 日本語 и русский",
    "long_line": "很长的一行。" * 200,
    "hash_flood": "# abc" + "#" * 500 + "x",
    "backtick_unbalanced": "这里有 ` 一个反引号",
    "asterisk_unbalanced": "这里有 ** 两个星号",
    "windows_newlines": "第一行\r\n第二行\r\n",
    "tabs": "\t缩进的行\n\t\t更深",
    "only_whitespace_lines": "文字\n   \n文字",
}

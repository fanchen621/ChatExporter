# ChatExporter

AI 客户端把对话锁在各自的私有存储里——加密的 SQLite、散在项目目录下的 JSONL、按账号分库的目录树。
ChatExporter 把它们读出来，归一成同一套消息模型，导出成文件。

[![CI](https://github.com/fanchen621/ChatExporter/actions/workflows/ci.yml/badge.svg)](https://github.com/fanchen621/ChatExporter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](#安装)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](#从源码运行)

纯本地。没有网络请求代码，没有遥测，没有账号体系。

![主界面](docs/images/screenshot-light.png)

---

## 来源

| 客户端 | 存储形态 | 位置 |
|---|---|---|
| TRAE SOLO CN | SQLCipher 加密 SQLite | 密钥来自环境变量，或运行时从进程内存提取 |
| QoderWork CN | SQLite | `%APPDATA%\QoderWork CN\data\agents.db` |
| WorkBuddy | SQLite + 每会话 JSONL | `~/WorkBuddy`、`~/.workbuddy`，多代目录布局并存 |
| QClaw | SQLite，工具输出独立成消息 | `~/.qclaw/memory/lossless/lcm.db` |
| 腾讯 Marvis | 按账号分库的 SQLite | `%APPDATA%\Tencent\Marvis\User\<uid>\database\data.db` |

读取一律走只读连接并对源库制作快照，不写回。

接一个新客户端 = 实现 `chat_exporter/adapters/base.py` 里的 `BaseAdapter`：`detect()`、`list_conversations()`、`get_conversation()`。

## 数据模型

归一化后的结构（`chat_exporter/models.py`）：

```
Conversation
├── id · title · created_at · updated_at · source_app · model
└── messages: List[Message]
    ├── role: USER | ASSISTANT | SYSTEM | TOOL
    ├── timestamp · message_id · parent_id · model · token_usage
    └── parts: List[MessagePart]
        └── type: TEXT | THINKING | TOOL_CALL | TOOL_RESULT | CODE | FILE | IMAGE
```

`parts` 保持源顺序。思考、工具调用、工具结果都是一等公民——它们参与导出，只在阅读视图里被折叠。

## 导出

| 格式 | 说明 |
|---|---|
| **Markdown** | 全量存档。围栏长度按内容动态选取，思考与工具记录折叠在 `<details>` 里 |
| **HTML** | 自包含单文件，全字段转义，`prefers-color-scheme` 自适应 |
| **JSON** | 与内存模型一一对应，可无损回读 |
| **纯文本** | 只留问答正文 |

单条、多选、整源批量。失败项逐条列出原因，不写空壳文件冒充成功。

## 阅读视图

预览默认只渲染用户与 AI 的正文。勾选**只看对话**进一步隐藏思考与工具记录。

这个开关不能简单地"过滤掉 thinking"——不同客户端存"回答"的方式不一样。有的客户端从不写入独立的回答字段，AI 的输出整个落在推理里；对它们做朴素过滤，AI 会在阅读视图里彻底失声。所以按整段对话的实际形态自适应：

- AI 在任意一轮给过真正的回答正文 → 纯机器轮次整条隐藏
- AI 全程没有回答正文 → 保留每轮最后一块推理作为结论

实测一条 TRAE 对话：阅读视图 242 万字符 → 5.7 万，AI 的话一句没少。

**导出不受预览影响，始终全量。**

![只看对话](docs/images/screenshot-clean.png)

## 检索

- **标题**：即时过滤
- **全文**：按用户/AI 正文搜索。结果以 `(来源, 对话 id, 更新戳)` 为键落盘缓存，跨会话复用，源数据变化自动失效，总量上限 200 MB
- **时间**：今天 / 7 天 / 30 天 / 90 天 / 一年，可与关键词叠加

## 界面

深浅双主题即时切换，可拖拽分栏，代码块与行内代码等宽渲染，高 DPI 中文排版。
窗口尺寸、分栏位置、上次来源、导出格式持久化到 `%LOCALAPPDATA%\ChatExporter\settings.json`。

| 快捷键 | 功能 |
|---|---|
| `Ctrl+F` / `Ctrl+K` | 聚焦搜索 |
| `Ctrl+Shift+F` | 对话内查找 |
| `Ctrl+E` | 导出当前 |
| `Ctrl+Shift+E` | 批量导出（多选时只导选中） |
| `F5` | 刷新当前来源 |

![深色主题](docs/images/screenshot-dark.png)

---

## 实现说明

几个不那么显然的地方，记下来供接手的人参考。

**围栏碰撞。** 对话正文本身经常包含 ` ``` `。固定三反引号会被内容提前闭合，后面的结构全部错位。围栏长度按内容里最长的同字符游程动态选取。

**剥离注入上下文的边界。** 客户端会往用户消息里塞环境信息。早期实现用前缀匹配找标签、找不到闭合就一路删到消息末尾——一个未闭合的标签能吃掉整条提问。现在要求整词边界匹配，且必须找到配对的闭合标签才跳过；代码围栏内的内容先遮蔽再处理，用户在代码块里讨论这些标签不会被误伤。

**工具消息不是噪声。** QClaw 把工具输出存成独立的 `role=tool` 消息。按预览的可见性规则去筛导出，实测一条对话 97% 的字节不会进文件。预览语义和导出语义必须分开——这是本项目唯一一条不可让步的约束。

**适配器不是线程安全的。** 它们共享快照临时目录和内部缓存。GUI 侧对 `list_conversations()` 全局串行化，并在拿到锁后重新校验代次，丢弃已经过期的排队请求。

**多账号分库会重号。** Marvis 每个账号一个库，`conversation_id` 跨库重复。ID 加来源库命名空间，否则 A 账号的对话会被 B 账号的同号对话顶掉。

## 约束

- **仅 Windows。** 路径推断与 TRAE 的密钥提取都依赖 Win32。
- **只读。** 不写回源库，不做同步，不支持导入。
- **TRAE 需要密钥。** 客户端运行时可自动提取（有界 8 秒，仅扫描 TRAE 进程）；也可通过 `TRAE_SQLCIPHER_KEY` 环境变量提供，或用 `TRAE_ENABLE_MEMORY_SCAN=0` 完全关闭扫描。
- **不跨来源去重。** 同一段对话在两个客户端里各存一份，就导出两份。

## 安装

从 [Releases](https://github.com/fanchen621/ChatExporter/releases) 取 `ChatExporter.exe`，直接运行。无需安装、无需 Python。

### 从源码运行

```bash
git clone https://github.com/fanchen621/ChatExporter.git
cd ChatExporter
pip install -r requirements.txt
python main.py
```

Python 3.10+。界面用 tkinter（标准库），运行期唯一第三方依赖是 `cryptography`，只用于解密 TRAE 的 SQLCipher 库。

### 打包

```bash
pip install -r requirements-dev.txt
python build_exe.py
```

产物在 `dist/ChatExporter.exe`。

## 隐私

- 无网络代码。无遥测，无崩溃上报，无自动更新。
- 源数据只读连接 + 快照，绝不写回。
- 不落盘任何凭据。TRAE 密钥仅在内存中用于解密本地库。
- 设置与检索缓存位于 `%LOCALAPPDATA%\ChatExporter\`，不含凭据。

## 开发

```bash
python -m pytest tests/ -v
```

117 个测试。数据完整性相关的缺陷一律要求附带能复现它的回归测试——"导出必须全量"是这个项目的核心约束，约束需要测试来守。

欢迎 PR，尤其是新客户端的适配器。

## 许可

[MIT](LICENSE)

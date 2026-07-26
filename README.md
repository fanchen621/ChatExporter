# ChatExporter · 本地对话归档工作台

ChatExporter 是一款本地优先的多程序对话导出工具，可读取多个 AI 助手客户端的本地对话，进行标题/正文检索、清晰预览，并完整导出为 Markdown。对话与密钥均在用户自己的电脑上处理，不上传云端。

## 核心能力

- **多程序支持**：TRAE SOLO CN、QoderWork CN、WorkBuddy、QClaw、腾讯 Marvis
- **多格式导出**：Markdown（完整存档）、HTML（自包含单文件）、JSON（无损结构化）、纯文本（只留正文）
- **高 DPI 自适应中文界面**：深浅双主题、可拖拽分栏，布局按实际宽度自适应
- **标题与正文检索**：既可按标题即时筛选，也可按用户/AI 正文关键词全文检索（结果落盘缓存，二次检索无需重读）
- **干净预览**：只展示真实用户消息与 AI 最终可见正文，代码块等宽高亮
- **完整导出**：保留思考过程、工具调用、工具结果、代码块和附件
- **TRAE 密钥助手**：用户显式授权后，在本机有界扫描 TRAE 私有内存
- **本地隐私**：不上传对话、不上传 key、不依赖云端服务

## v2.0 深度优化

本轮以「导出必须完整」为唯一标尺做了一次全面审计，所有修复都在本机真实数据上验证过。

**修复的数据丢失**

- 未闭合或同名前缀的内部标签（如 `<current_timezone>` 撞上 `current_time`）会吞掉整条消息的剩余正文，现已改为整词匹配 + 闭合配对才跳过
- 用户自己敲的 `Shell:`、`OS Version:`、`Path:` 开头的行曾被当成环境噪声整行删除，现在只在消息确实带注入上下文时才清理
- 围栏代码块内的内容不再被当作注入上下文清洗；WorkBuddy 回答结尾的闭合围栏也不会再被尾部清理吃掉
- QClaw 把工具输出存成独立 `role=tool` 消息，此前整批不进导出（实测一条真实对话 97% 的字节缺失）；现在完整保留并默认折叠
- QoderWork 的 `tool-invocation` 形状与工具输出字段此前被整块丢弃
- 腾讯 Marvis 只导出「对话数最多」的那一个账号库，其余账号既看不见也导不出；现已覆盖全部账号库
- 导出内容里自带 ``` 或 `~~~` 会提前闭合围栏，现按内容动态选择围栏长度
- 消息片段按原始顺序渲染，讲解与其对应代码块不再错位

**修复的静默失败**

- 批量导出遇到读取失败会写出只有元数据的空壳文件并计为成功，现在逐条记录失败原因并在完成时列出
- 预览读取失败曾显示成「这条记录没有可显示的正文」，与真正的空对话混为一谈
- 适配器的 `except Exception: return None` 把 schema 变动、数据库被锁压成「找不到对话」，现在真实故障会带原因抛出
- 非 dict 的 `usage` 字段会让整批导出崩溃

**界面与性能**

- 深浅双主题，可即时切换；分栏位置、窗口几何、上次来源与导出格式均会记住
- 全文检索结果按 (来源, 对话, 更新戳) 落盘缓存，跨会话复用，陈旧条目自动失效
- 切换来源、刷新、清空搜索会作废在途的后台结果，旧来源的预览不会再变成当前选中项
- 双击导出只导双击的那一条；Ctrl/Shift 多选后可只导出选中的几条
- 同一来源重复选中不再触发重复的全量读取

## 安装

### 下载预编译版本

从 Releases 页面下载最新的 `ChatExporter.exe`，双击运行。

### 从源码运行

```bash
git clone https://github.com/fanchen621/ChatExporter.git
cd ChatExporter
pip install -r requirements.txt
python main.py
```

### 自行打包

```bash
pip install pyinstaller
python build_exe.py
```

打包结果：

```text
dist/ChatExporter.exe
```

## 使用方法

1. 启动 ChatExporter
2. 从左侧选择一个已检测到的数据来源
3. 选择“标题”或“对话内容”检索
4. 选择一条对话，在右侧阅读用户与 AI 正文
5. 点击“导出当前对话”或“批量导出”生成完整 Markdown

### 快捷键

| 快捷键 | 功能 |
|---|---|
| `Ctrl + F` / `Ctrl + K` | 聚焦对话列表搜索框 |
| `Ctrl + Shift + F` | 聚焦当前对话内查找 |
| `Ctrl + E` | 导出当前对话 |
| `Ctrl + Shift + E` | 批量导出（列表里多选时只导选中的几条） |
| `F5` | 刷新当前来源 |

导出格式在顶部「格式」下拉框里选，对单条导出和批量导出同时生效。

## 搜索说明

### 标题搜索

输入后即时过滤，不读取完整对话，速度最快。

### 对话内容全文检索

- 至少输入 2 个字符
- 只匹配用户和 AI 的可见正文
- 不匹配思考过程、工具调用、工具结果与系统注入上下文
- 首次检索会按需读取正文，后续复用本机内存缓存
- 所有检索均在本机完成

## 预览与完整导出的区别

### 界面预览

显示：

- 用户消息
- AI 助手最终可见正文
- 正文中的代码块
- 附件名称

隐藏：

- Thinking / Reasoning
- Tool Calls
- Tool Results
- System Messages
- WorkBuddy 注入的设备、环境、身份和工作区上下文

### Markdown 完整导出

仍会保留适配器能够读取的思考过程、工具调用、工具结果等详细记录。WorkBuddy 的运行时注入样板不会作为真实用户正文导出。

## TRAE 密钥助手

TRAE SOLO CN 的完整对话数据库使用 SQLCipher 加密。普通用户不知道 key 时：

1. 启动 TRAE SOLO CN
2. 打开任意一个对话窗口
3. 在 ChatExporter 左侧选择 TRAE
4. 点击顶部或左侧的“获取 TRAE 密钥”
5. 点击“开始安全扫描”
6. 成功后程序会安全缓存 key，并重新加载完整数据库

安全策略：

- 默认不会无提示读取进程内存
- 只有用户显式点击后才扫描
- 扫描仅限 TRAE 相关进程的可读私有内存
- 有 8 秒和 300MB 上限，并支持取消
- 候选 key 必须通过数据库第一页校验
- Windows 缓存使用 DPAPI，与当前 Windows 用户绑定

手动设置：

```powershell
$env:TRAE_SQLCIPHER_KEY="<你的SQLCipher密钥>"
python main.py
```

> SQLCipher key 属于敏感信息，请勿上传到公开仓库、Issue、截图或聊天记录。

## 项目结构

```text
ChatExporter/
├── main.py
├── build_exe.py
├── requirements.txt
├── tests/
│   ├── test_trae_optimized.py
│   ├── test_marvis_compat.py
│   ├── test_preview_utils.py
│   └── test_preview_adapters_v3.py
└── chat_exporter/
    ├── gui_cn_v3.py             # 当前默认高 DPI 中文工作台
    ├── gui_cn_v2.py             # 上一版中文工作台
    ├── gui_cn.py
    ├── gui_modern.py
    ├── preview_utils.py
    ├── ui_theme.py
    ├── models.py
    ├── markdown_exporter.py
    └── adapters/
        ├── trae_optimized.py
        ├── workbuddy_compat.py
        ├── qclaw_compat.py
        ├── workbuddy.py
        ├── qclaw.py
        ├── qoderwork.py
        └── marvis.py
```

## 开发与验证

```bash
python -m compileall -q main.py build_exe.py chat_exporter
python -m unittest discover -s tests -v
```

Windows CI 会在 Python 3.10 / 3.12 上执行编译与单元测试。

## 更新日志

### v1.1.3

- 修复高 DPI 下顶部裁切和底部状态栏遮挡
- 对话列表自适应加宽，滚动条改为宽、实色样式
- TRAE 密钥助手改为可滚动、固定操作栏
- 清理 WorkBuddy 运行时注入正文
- 增强 QClaw 用户/AI 角色恢复

### v1.1.2

- 新增正文全文检索、干净预览、对话内查找与复制正文

### v1.1.1

- 中文界面重做，修复腾讯 Marvis schema 兼容问题

## 免责声明

本工具仅用于读取和导出用户自己设备上的本地数据。使用时请遵守相关软件的使用条款和当地法律。作者不对因错误使用造成的损失承担责任。

## License

MIT

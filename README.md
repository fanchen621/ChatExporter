# ChatExporter · 本地对话归档工作台

ChatExporter 是一款本地优先的多程序对话导出工具，可从多个 AI 助手客户端读取对话、快速检索、清晰预览，并完整导出为 Markdown。所有数据都在用户自己的电脑上处理，不上传到云端。

## 核心能力

- **多程序支持**：TRAE SOLO CN、QoderWork CN、WorkBuddy、QClaw、腾讯 Marvis
- **中文桌面工作台**：宽侧栏、稳定双栏布局、高 DPI 适配、清晰状态反馈
- **标题与正文检索**：既可按标题即时筛选，也可按用户/AI 对话正文关键词全文检索
- **干净预览**：预览只展示用户与 AI 的可见正文，不展示思考过程和工具调用明细
- **完整导出**：Markdown 导出仍保留正文、思考过程、工具调用、工具结果、代码块和附件信息
- **TRAE 密钥助手**：用户显式授权后，尝试从本机 TRAE 进程中提取 SQLCipher key
- **本地隐私**：不上传对话、不上传 key、不依赖云端服务

## v1.1.2 界面改进

- 左侧来源栏扩大到 312px，减少高 DPI 下的拥挤和截断
- TRAE 密钥助手在顶部和左侧均有明确入口
- 对话列表扩大到 540px，标题、更新时间和消息数不再互相挤压
- 新增排序：最近更新、消息最多、标题排序
- 新增“对话内容”全文检索，首次按需读取正文并缓存在本机内存
- 右侧预览只显示用户/AI正文，思考与工具细节仅保留在完整导出
- 右侧新增明显的纵向滚动条、对话内查找、上一处/下一处、顶部/底部、复制正文

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
3. 在对话列表上方选择“标题”或“对话内容”检索
4. 选择一条对话，在右侧阅读用户与 AI 的正文
5. 点击“导出当前对话”或“批量导出”生成完整 Markdown

### 快捷键

| 快捷键 | 功能 |
|---|---|
| `Ctrl + F` / `Ctrl + K` | 聚焦对话列表搜索框 |
| `Ctrl + Shift + F` | 聚焦当前对话内查找 |
| `Ctrl + E` | 导出当前对话 |
| `Ctrl + Shift + E` | 批量导出 |
| `F5` | 刷新当前来源 |

> 超大对话会在界面预览中做长度保护，但导出的 Markdown 文件仍保留完整内容。

## 搜索说明

### 标题搜索

输入后即时过滤，不读取完整对话，速度最快。

### 对话内容全文检索

- 至少输入 2 个字符
- 仅匹配用户与 AI 的可见正文
- 不匹配思考过程、工具调用和工具结果
- 首次检索会按需读取对话正文，后续复用本机内存缓存
- 所有检索都在本机完成

## 预览与完整导出的区别

### 界面预览

只显示：

- 用户消息
- AI 助手最终可见正文
- 代码块
- 附件名称

默认隐藏：

- Thinking / Reasoning
- Tool Calls
- Tool Results
- System Messages

### Markdown 完整导出

仍会保留所有可读取内容，包括思考过程和工具明细。

## TRAE 密钥助手

TRAE SOLO CN 的完整对话数据库使用 SQLCipher 加密。普通用户不知道 key 时，可以使用内置助手：

1. 启动 TRAE SOLO CN
2. 打开任意一个对话窗口
3. 在 ChatExporter 左侧选择 TRAE
4. 点击顶部或左侧的“获取 TRAE 密钥”
5. 阅读说明后点击“开始安全扫描”
6. 成功后程序会自动缓存 key，并重新加载完整 TRAE 数据库

### 扫描与安全策略

- 默认不会自动读取进程内存
- 只有用户显式点击按钮后才执行扫描
- 扫描范围仅限 TRAE 相关进程的可写私有内存
- 扫描有 8 秒和 300MB 上限，并支持取消
- 先扫描 ASCII / UTF-16 hex 字符串，再做有限原始缓冲区扫描
- key 使用数据库第一页快速校验，不会缓存未验证候选
- Windows 下本地缓存使用 DPAPI 加密，与当前 Windows 用户绑定
- 写入环境变量使用当前用户注册表

### 手动设置 key

```powershell
$env:TRAE_SQLCIPHER_KEY="<你的SQLCipher密钥>"
python main.py
```

如果没有 key，程序会快速回退到日志解析模式，避免卡死；日志模式内容可能不如数据库模式完整。

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
│   └── test_preview_utils.py
└── chat_exporter/
    ├── gui_cn_v2.py           # 当前默认中文工作台
    ├── gui_cn.py              # 上一版中文界面
    ├── gui_modern.py          # 英文现代界面，保留作回滚
    ├── gui.py                 # 旧版界面
    ├── preview_utils.py       # 正文预览与全文检索文本提取
    ├── ui_theme.py
    ├── models.py
    ├── markdown_exporter.py
    └── adapters/
        ├── trae_optimized.py
        ├── trae.py
        ├── qoderwork.py
        ├── workbuddy.py
        ├── qclaw.py
        └── marvis.py
```

## 开发与验证

```bash
python -m compileall -q main.py build_exe.py chat_exporter
python -m unittest discover -s tests -v
```

Windows CI 会在 Python 3.10 / 3.12 上执行编译与单元测试。

## 更新日志

### v1.1.2

- 左侧导航加宽并简化来源行
- TRAE 密钥入口固定显示在侧栏与顶部
- 对话列表加宽并新增排序
- 新增按用户/AI正文关键词全文检索
- 预览改为只展示用户和 AI 正文
- 新增对话内查找、复制正文、顶部/底部跳转和明显滚动条

### v1.1.1

- 中文界面重做
- 修复腾讯 Marvis 缺少 `model_id` 等字段时的预览兼容

### v1.1.0

- 新增现代化 UI、线程安全队列、TRAE DPAPI 缓存与 Windows CI

## 免责声明

本工具仅用于读取和导出用户自己设备上的本地数据。使用时请遵守相关软件的使用条款和当地法律。作者不对因错误使用造成的损失承担责任。

## License

MIT

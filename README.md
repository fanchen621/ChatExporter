# ChatExporter · 本地对话归档工作台

ChatExporter 是一款本地优先的多程序对话导出工具，可从多个 AI 助手客户端读取对话、预览内容，并完整导出为 Markdown。所有数据都在用户自己的电脑上处理，不上传到云端。

## 核心能力

- **多程序支持**：TRAE SOLO CN、QoderWork CN、WorkBuddy、QClaw、腾讯 Marvis
- **现代化桌面界面**：深色来源导航、卡片式对话库、沉浸式预览、清晰状态反馈
- **完整导出**：默认包含正文、思考过程、工具调用、工具结果、代码块和附件信息
- **高性能交互**：列表分批渲染、搜索防抖、后台线程读取、超大对话预览保护
- **TRAE Key Assistant**：用户显式授权后，快速尝试从本机 TRAE 进程中提取 SQLCipher key
- **本地隐私**：不上传对话、不上传 key、不依赖云端服务

## 界面设计

v1.1.0 采用新的 **Local Conversation Studio** 设计语言：

- 左侧固定来源导航，使用应用缩写、状态点和来源色区分不同客户端
- 顶部统一命令栏，集中放置刷新、TRAE Key Assistant、批量导出和单条导出
- 中间对话库采用更高密度但更清晰的表格与搜索体验
- 右侧预览区重新设计角色层级、代码样式、工具调用和空状态
- 底部状态栏统一显示任务状态、进度和 Local / Private 标识
- 使用纯 Tk/Ttk 实现，不增加额外 GUI 运行时依赖，仍可打包为单文件 EXE

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

打包结果位于：

```text
dist/ChatExporter.exe
```

## 使用方法

1. 启动 ChatExporter
2. 从左侧选择一个已检测到的来源
3. 在对话库中搜索或选择对话
4. 在右侧预览完整内容
5. 点击 `Export selected` 导出当前对话，或点击 `Export all` 批量导出

快捷键：

| 快捷键 | 功能 |
|---|---|
| `Ctrl + F` / `Ctrl + K` | 聚焦搜索框 |
| `Ctrl + E` | 导出当前对话 |
| `Ctrl + Shift + E` | 批量导出 |
| `F5` | 刷新当前来源 |

> 超大对话会在界面预览中做长度保护，但导出的 Markdown 文件仍保留完整内容。

## TRAE Key Assistant

TRAE SOLO CN 的完整对话数据库使用 SQLCipher 加密。普通用户不知道 key 时，可以使用内置助手：

1. 启动 TRAE SOLO CN
2. 打开任意一个对话窗口
3. 在 ChatExporter 左侧选择 TRAE
4. 点击顶部 `TRAE Key Assistant`
5. 阅读说明后点击 `Start secure scan`
6. 成功后程序会自动缓存 key，并重新加载完整 TRAE 数据库

### 扫描与安全策略

- 默认不会自动读取进程内存
- 只有用户显式点击按钮后才执行扫描
- 扫描范围仅限 TRAE 相关进程的可写私有内存
- 扫描有 8 秒和 300MB 上限，并支持取消
- 先扫描 ASCII / UTF-16 hex 字符串，再做有限原始缓冲区扫描
- key 使用数据库第一页快速校验，不会把未验证候选写入缓存
- Windows 下本地缓存使用 DPAPI 加密，与当前 Windows 用户绑定
- 写入环境变量使用用户注册表，不再通过带明文参数的 `setx` 子进程

### 手动设置 key

```powershell
# 当前 PowerShell 窗口
$env:TRAE_SQLCIPHER_KEY="<你的SQLCipher密钥>"
python main.py
```

```bat
:: 当前 CMD 窗口
set TRAE_SQLCIPHER_KEY=<你的SQLCipher密钥>
python main.py
```

也可以在 Key Assistant 成功页面点击 `Save to Windows`，安全写入当前用户环境变量。

如果没有 key，程序会快速回退到日志解析模式，避免卡死；日志模式的内容可能不如数据库模式完整。

> SQLCipher key 属于敏感信息，请勿上传到公开仓库、Issue、截图或聊天记录。

## 支持的内容类型

- 文本消息
- Thinking / Reasoning
- Tool Calls
- Tool Results
- 代码块
- 文件附件
- 图片引用
- 时间戳与模型信息

## 项目结构

```text
ChatExporter/
├── main.py
├── build_exe.py
├── requirements.txt
├── tests/
│   └── test_trae_optimized.py
└── chat_exporter/
    ├── gui_modern.py          # 当前现代化 UI
    ├── gui.py                 # 旧版 UI，保留作为回滚路径
    ├── ui_theme.py            # 设计令牌与 Ttk 样式系统
    ├── models.py
    ├── markdown_exporter.py
    └── adapters/
        ├── base.py
        ├── trae.py            # 已验证的 TRAE 数据解析逻辑
        ├── trae_optimized.py  # 显式扫描、DPAPI 缓存和快速 key 验证
        ├── qoderwork.py
        ├── workbuddy.py
        ├── qclaw.py
        └── marvis.py
```

`main.py` 默认启动 `gui_modern.py`。旧版 `gui.py` 暂时保留，便于快速回滚和差异核验。

## 性能与可靠性

- GUI 工作线程通过线程安全队列回到 Tk 主线程，不再从后台线程直接操作 Tk
- 对话列表按批次写入 Treeview，减少大量记录下的假死
- 搜索输入使用防抖
- 预览有总长度和单段长度保护
- TRAE key 扫描使用两阶段策略，并优先扫描主进程
- key 候选仅解密第一页首块完成快速验证
- QClaw message parts 使用批量读取，减少 N+1 查询
- Windows GitHub Actions 对 Python 3.10 / 3.12 执行编译和单元测试

## 开发与验证

```bash
python -m compileall -q main.py build_exe.py chat_exporter
python -m unittest discover -s tests -v
```

本项目的 CI 在 Windows 环境运行，以覆盖 Tkinter、Windows 路径和 TRAE 相关平台代码的基础回归。

## 更新日志

### v1.1.0

- 全面重做前端 UI，升级为 Local Conversation Studio
- 新增统一设计系统、现代导航、命令栏、卡片式列表与预览
- 改为线程安全 UI 队列，减少 Tk 跨线程不稳定问题
- TRAE 默认改为显式扫描，不再无提示自动读取进程内存
- TRAE key 扫描升级为 ASCII / UTF-16 / raw 两阶段策略
- key 缓存升级为 v2；Windows 使用 DPAPI 加密
- key 缓存不再绑定数据库 mtime，数据库更新后仍可复用并重新校验
- 新增扫描取消、主进程优先级和安全环境变量写入
- 新增 Windows CI 和 key/cache 回归测试

### v1.0.2

- 新增显式 TRAE 密钥助手
- 成功提取后自动本地缓存并重新加载数据库
- 增加扫描状态、时间和内存上限

### v1.0.1

- 修复 GUI 卡顿和 Windows/Tk 黑块显示
- 取消“包含思考过程”开关，默认完整导出
- 优化 QClaw 列表和 message parts 读取

### v1.0.0

- 初始版本

## 免责声明

本工具仅用于读取和导出用户自己设备上的本地数据。使用时请遵守相关软件的使用条款和当地法律。作者不对因错误使用造成的损失承担责任。

## License

MIT

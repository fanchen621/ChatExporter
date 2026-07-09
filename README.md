# ChatExporter - 多程序对话导出工具

一个跨平台对话导出工具，支持从多个 AI 助手程序中提取和导出对话记录为 Markdown 格式。

## ✨ 特性

- **多程序支持**: TRAE SOLO CN、QoderWork CN、WorkBuddy、QClaw、腾讯 Marvis
- **TRAE 密钥助手**: 提供显式按钮，在用户确认后从本机 TRAE 进程中尝试提取 SQLCipher 密钥
- **智能标题**: 自动清洗无效标题，过滤部分 QClaw 内部 memory/dream diary 噪声
- **完整导出**: 默认包含文本、思考过程、工具调用、代码块等内容类型
- **轻量界面**: 对话列表分批渲染；预览大对话时自动截断预览但不影响真实导出
- **隐私保护**: 所有数据本地处理，不上传任何信息

## 安装

### 方法一：下载预编译版本（推荐）

从 [Releases](https://github.com/fanchen621/ChatExporter/releases) 页面下载最新的 `ChatExporter.exe`，双击即可运行。

### 方法二：从源码运行

```bash
# 克隆仓库
git clone https://github.com/fanchen621/ChatExporter.git
cd ChatExporter

# 安装依赖
pip install -r requirements.txt

# 运行程序
python main.py
```

### 方法三：自行打包

```bash
# 安装 PyInstaller
pip install pyinstaller

# 打包为单个 EXE
python build_exe.py
```

打包完成后，EXE 文件位于 `dist/ChatExporter.exe`

## 🚀 使用方法

### 基本使用

1. **启动程序**: 双击 `ChatExporter.exe` 或运行 `python main.py`
2. **自动检测**: 程序会自动检测已安装的 AI 助手程序
3. **选择程序**: 点击左侧面板中的程序名称
4. **浏览对话**: 在中间面板查看对话列表，支持搜索过滤
5. **预览内容**: 点击对话后在右侧查看预览内容
6. **导出对话**:
   - 单个导出：选中对话后点击「导出选中」
   - 批量导出：点击「批量导出」

> 说明：为了避免超大对话卡住界面，右侧预览会做长度保护；导出的 Markdown 文件仍会保留完整内容。

### TRAE SOLO CN 特殊配置

TRAE SOLO CN 使用 SQLCipher 加密数据库。完整导出需要 SQLCipher 密钥。本工具支持三种方式：

#### 方式一：点击「提取 TRAE 密钥」（推荐给普通用户）

1. 启动 TRAE SOLO CN，并打开任意一个对话窗口
2. 启动 ChatExporter，左侧选择「TRAE SOLO CN」
3. 点击中间底部的「提取 TRAE 密钥」
4. 确认授权后，程序会在本机有界扫描 TRAE 进程内存
5. 成功后会自动写入本地缓存，并重新加载 TRAE 数据库
6. 弹窗中可以选择「复制密钥」或「写入环境变量」

这个流程是显式触发的：程序不会在你没有点击按钮时自动读取进程内存。

#### 方式二：手动配置环境变量

如果你知道密钥，可以设置为环境变量：

```bash
# Windows PowerShell，仅当前窗口有效
$env:TRAE_SQLCIPHER_KEY="<你的SQLCipher密钥>"
python main.py

# Windows CMD，仅当前窗口有效
set TRAE_SQLCIPHER_KEY=<你的SQLCipher密钥>
python main.py

# Windows 持久写入用户环境变量
setx TRAE_SQLCIPHER_KEY <你的SQLCipher密钥>
```

#### 方式三：日志回退模式

如果没有密钥，程序会快速回退到日志解析模式。日志模式不会卡顿，但内容可能不如数据库模式完整。

> ⚠️ **安全提示**: 密钥属于敏感信息。请不要上传到公开仓库、Issue、截图或聊天记录中。

### 导出选项

- **思考过程**: 默认包含，不再提供关闭开关
- **时间戳**: 默认包含每条消息的时间戳
- **元数据**: 包含对话的基本信息（来源程序、创建时间、消息数量等）

## 📁 支持的数据格式

### 消息类型

- ✅ 文本消息
- ✅ 思考过程（Thinking/Reasoning）
- ✅ 工具调用（Tool Calls）
- ✅ 工具返回结果
- ✅ 代码块（带语法高亮）
- ✅ 文件附件
- ✅ 图片引用

### 导出格式

- Markdown (.md) - 支持所有主流 Markdown 阅读器
- 文件名格式：`{对话标题}_{时间戳}.md`

## 技术细节

### 架构设计

```text
ChatExporter/
├── main.py                 # 程序入口
├── build_exe.py            # PyInstaller 打包脚本
├── requirements.txt        # Python 依赖
└── chat_exporter/
    ├── gui.py              # Tkinter GUI 界面
    ├── models.py           # 数据模型定义
    ├── markdown_exporter.py# Markdown 导出器
    └── adapters/           # 各程序适配器
        ├── base.py         # 基础适配器类
        ├── trae.py         # TRAE SOLO CN
        ├── qoderwork.py    # QoderWork CN
        ├── workbuddy.py    # WorkBuddy
        ├── qclaw.py        # QClaw
        └── marvis.py       # 腾讯 Marvis
```

### TRAE 数据库解密原理

TRAE SOLO CN 使用 SQLCipher 4 加密：

- 加密算法：AES-256-CBC
- KDF 迭代：256,000 次
- 验证方式：HMAC-SHA512

本工具通过以下方式获取解密密钥：

1. **环境变量**: 从 `TRAE_SQLCIPHER_KEY` 读取
2. **本地缓存**: 成功提取后保存到用户本地缓存，并用数据库指纹校验
3. **显式内存提取**: 仅当用户点击「提取 TRAE 密钥」并确认后，扫描 TRAE 进程内存
4. **日志回退**: 无密钥时快速读取最近日志尾部，避免卡顿

解密后的数据库会保存到临时文件，程序退出后自动清理。

## 开发指南

### 添加新程序支持

1. 在 `chat_exporter/adapters/` 创建新的适配器文件
2. 继承 `BaseAdapter` 类
3. 实现以下方法：
   - `detect()`: 检测程序是否安装
   - `get_app_info()`: 获取程序信息
   - `list_conversations()`: 列出所有对话
   - `get_conversation()`: 获取单个对话详情
4. 在 `gui.py` 中注册新适配器

### 代码规范

- 使用 Python 3.8+
- 遵循 PEP 8 编码规范
- 类型注解：使用 `typing` 模块
- 文档字符串：所有公共方法都需要 docstring

## 贡献

欢迎提交 Issue 和 Pull Request！

### 贡献流程

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支
5. 开启 Pull Request

## 📝 更新日志

### v1.0.2 (2026-07-09)
- 新增 TRAE 密钥助手按钮：用户显式确认后扫描本机 TRAE 进程内存
- 成功提取后自动本地缓存，并可复制密钥或写入用户环境变量
- 无 key 时默认不扫内存，继续保持快速日志回退
- 密钥获取前只做第一页快速校验，不为“获取 key”提前全库解密
- 扫描过程增加状态提示、8 秒上限、300MB 上限和结果弹窗

### v1.0.1 (2026-07-09)
- 修复 GUI 卡顿：对话列表分批渲染、搜索防抖、超大预览截断
- 修复 Windows/Tk 黑块显示：移除界面 emoji，Canvas 指示条改为 Frame
- 取消“包含思考过程”开关，导出默认包含思考过程
- 修复批量导出可能只导出列表元数据的问题：导出前按需加载完整对话
- 优化 QClaw：列表阶段不读取大文本、批量读取 message_parts、清理内部 memory 噪声标题

### v1.0.0 (2026-07-09)
- 初始版本发布
- ✅ 支持 5 个 AI 助手程序
- ✅ TRAE SOLO CN 数据库解密
- ✅ 智能标题清洗
- ✅ 现代 GUI 界面
- ✅ 批量导出功能

## ⚠️ 免责声明

本工具仅供学习和研究使用。使用本工具导出对话记录时，请遵守相关程序的使用条款和隐私政策。作者不对因使用本工具造成的任何损失承担责任。

## 📄 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件

## 🙏 致谢

- [PyInstaller](https://www.pyinstaller.org/) - Python 打包工具
- [cryptography](https://cryptography.io/) - 加密库
- [Tkinter](https://docs.python.org/3/library/tkinter.html) - GUI 框架

---

**Made with ❤️ by fanchen621**

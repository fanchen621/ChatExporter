# ChatExporter · 本地对话归档工作台

ChatExporter 是一款本地优先的多程序对话导出工具，可读取多个 AI 助手客户端的本地对话，进行标题/正文检索、清晰预览，并完整导出为 Markdown。对话与密钥均在用户自己的电脑上处理，不上传云端。

## 核心能力

- **多程序支持**：TRAE SOLO CN、QoderWork CN、WorkBuddy、QClaw、腾讯 Marvis
- **高 DPI 自适应中文界面**：顶部、工作区和状态栏使用自然高度，避免缩放后裁切或遮挡
- **标题与正文检索**：既可按标题即时筛选，也可按用户/AI 正文关键词全文检索
- **干净预览**：只展示真实用户消息与 AI 最终可见正文
- **完整导出**：Markdown 仍保留可读取的思考过程、工具调用、工具结果、代码块和附件
- **TRAE 密钥助手**：用户显式授权后，在本机有界扫描 TRAE 私有内存
- **本地隐私**：不上传对话、不上传 key、不依赖云端服务

## v1.1.3 真机优化

- 顶部标题和操作栏不再使用固定高度，高 DPI 下不会显示不全
- 底部状态栏改为自然高度，不再覆盖列表或预览内容
- 对话列表按屏幕宽度自适应，宽屏下显著增加标题列空间
- 列表和预览滚动条加宽、使用不透明实色，便于拖动
- TRAE 密钥助手改为可调整大小的滚动正文区，开始/关闭按钮固定在底部
- WorkBuddy 自动移除 `system-reminder`、环境信息、身份文件等运行时注入内容
- QClaw 兼容 `user_message`、`human-input`、`assistant_message`、`agent-output` 等角色名
- 预览与全文检索只使用用户/AI正文；系统消息、工具明细与思考过程不进入阅读视图

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
| `Ctrl + Shift + E` | 批量导出 |
| `F5` | 刷新当前来源 |

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

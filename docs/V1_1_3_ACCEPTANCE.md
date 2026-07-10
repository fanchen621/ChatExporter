# ChatExporter v1.1.3 Windows 真机验收

## 1. 高 DPI 与窗口布局

分别在 100%、125%、150% 缩放下验证：

- 顶部标题、副标题和四个操作按钮全部可见
- 对话列表和预览区没有被顶部裁切
- 底部状态栏位于工作区下方，不覆盖列表页脚或预览正文
- 窗口不会超出 Windows 任务栏可用区域
- 1366×768、1920×1080、2560×1440 下均可正常使用

## 2. 对话列表

- 宽屏下标题列明显宽于 v1.1.2
- 更新时间和消息数字段完整显示
- 列表纵向滚动条宽、实色、可直接拖动
- 标题搜索、正文检索、排序和清除搜索均可用

## 3. 预览

- 预览只显示用户和 AI 的可见正文
- 不显示系统消息、Thinking、Tool Calls、Tool Results
- 预览纵向滚动条宽、实色、可拖动
- 鼠标滚轮、顶部/底部、上一处/下一处、复制正文可用
- 完整 Markdown 导出仍包含思考过程和工具明细

## 4. WorkBuddy

选择包含运行时上下文的会话，确认预览和正文检索不出现：

- `<system-reminder>`
- `<user_references>`
- `<user_info>`
- `<identity_context>`
- OS Version / Shell / IDE Theme
- SOUL.md / IDENTITY.md / BOOTSTRAP.md 注入样板

真实用户问题和 AI 回答必须保留。

## 5. QClaw

至少验证包含以下角色变体的记录：

- user / human / user_message / human-input
- assistant / agent / assistant_message / agent-output

用户问题和 AI 回答都应出现在预览中，不能只剩 AI 回复。

## 6. TRAE 密钥助手

- 未选择 TRAE 时按钮禁用
- 选择 TRAE 后顶部与左侧入口同时启用
- 对话框底部“开始安全扫描”和“关闭”始终可见
- 对话框缩小时，中间内容可以滚动
- 扫描成功、失败、取消三条路径均不会卡死 UI
- 成功后可重新加载完整 TRAE 数据库

## 7. 自动验证

```bash
python -m compileall -q main.py build_exe.py chat_exporter
python -m unittest discover -s tests -v
python build_exe.py
```

发布前重新生成 `dist/ChatExporter.exe`，旧 EXE 不包含 v1.1.3 修复。

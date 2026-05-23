# 闭环测试方案

## 目标

用同一套测试流程覆盖两份真实资料：

- `测试资料/Attention is all you need.pdf`：小文件，配套 LaTeX 源码用于公式、段落和图像对照。
- `测试资料/Napkin.pdf`：大文件，配套 LaTeX 源码和图片资源用于长文档性能、滚动、缩放、知识库构建和数学内容识别压力测试。

测试必须同时检查功能正确性、交互闭环、渲染稳定性、性能和日志。

## 工具选择

- `pywinauto`：启动进程、等待 Windows 窗口、设置焦点、退出应用。
- `pyautogui`：真实鼠标双击、滚轮滚动、快捷键缩放、截图。
- `pytest-qt`：后续补充 Qt 组件级交互测试。

调研依据：`pywinauto` 适合 Windows UI Automation；`PyAutoGUI` 提供跨平台鼠标、键盘、截图能力；`pytest-qt` 提供 Qt/PySide 测试夹具。

## 流程

每个 PDF 都执行：

1. 清理 `logs/app.log`。
2. 使用 `python src/main.py --test-mode --open <pdf>` 启动应用。
3. 等待主窗口出现，等待日志出现 `文档加载完成`。
4. 截图首屏。
5. 连续滚动，截图。
6. 执行多次跳转尝试，截图。
7. 执行 `Ctrl+=` 放大、`Ctrl+-` 缩小，截图并检查日志中的缩放记录。
8. 强制重建当前文档知识库，并等待日志或测试事件确认完成。
9. 回到顶部附近，打开翻译裂缝并覆盖折叠、再次展开。
10. 发起一次裂缝内问答，检查全文检索和回答完成日志。
11. 发起一次右侧全文问答，检查证据列表、回答区和引用状态。
12. 再滚动一次，截图。
13. 收集日志尾部、错误/警告数量、关键行为计数和截图路径，写入 `test_artifacts/e2e/report.json`。

## 当前自动化命令

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/e2e_pdf_workflow.py --case attention
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/e2e_pdf_workflow.py --case napkin
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/e2e_pdf_workflow.py --case all
```

## 判定标准

首版门槛：

- 主窗口成功启动并保持响应。
- PDF 成功打开并记录 `文档加载完成`。
- 滚动、页码跳转、缩放、翻译折叠、知识库重建、裂缝问答、右侧全文问答动作完成并生成截图。
- 右侧全文问答必须产生至少 1 条检索依据和非空回答。
- 日志中无 `ERROR`、`WARNING`、`CRITICAL`。
- 输出 `test_artifacts/e2e/report.json`。

后续加强门槛：

- Attention：抽样公式与 LaTeX 源中的 `\frac`、`\sqrt`、`\mathrm`、`\operatorname` 等结构对齐。
- 公式审计报告需记录 `recovered_common_source_commands` 和 `common_source_command_recall`，用于量化源码常见命令在 PDF 抽取/MFR 后的恢复情况。
- Napkin：大文档打开首屏、滚动、缩放、知识库状态达到明确时间预算。
- 缩放后不能长期停留在模糊缩放图，应在异步精确渲染完成后替换。
- 跳转应使用真实页码/目录 UI，而不是快捷键尝试。
- 问答应展示基于全文检索的引用片段和不可回答边界。

## 已知缺口

- 测试模式下的 QA/翻译使用 Mock 生成模型，能验证全文检索和 UI 闭环，但不能代表真实模型质量。
- Napkin 全文知识库仍使用哈希嵌入兜底，不是真语义 embedding。
- 右侧全文问答已有证据面板、检索状态和引用页跳转；仍需补建议问题、真实模型质量评估和更细粒度的引用高亮。
- 公式审计已经能统计 LaTeX 源和 PDF 抽取差距；扫描/图片公式已接入 Pix2Text MFR OCR，但整体 LaTeX 保真仍明显不足，需要继续做源码对齐和文本公式恢复。

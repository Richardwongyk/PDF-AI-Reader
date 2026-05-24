# 闭环测试方案

## 目标

用同一套测试流程覆盖两份真实资料：

- `测试资料/Attention is all you need.pdf`：小文件，配套 LaTeX 源码用于公式、段落和图像对照。
- `测试资料/Napkin.pdf`：大文件，配套 LaTeX 源码和图片资源用于长文档性能、滚动、缩放、知识库构建和数学内容识别压力测试。

测试必须同时检查功能正确性、交互闭环、渲染稳定性、性能和日志。

新会话接手时必须先读 `AGENTS.md` 和 `docs/next_session_handoff.md`。当前闭环测试不是
单纯跑 UI，而是要验证多轮公式解析、全文 RAG/GraphRAG、日志和性能是否偏离设计边界。

## 工具选择

- `pywinauto`：启动进程、等待 Windows 窗口、设置焦点、退出应用。
- `pyautogui`：真实鼠标双击、滚轮滚动、快捷键缩放、截图。
- `pytest-qt`：后续补充 Qt 组件级交互测试。

调研依据：`pywinauto` 官方文档支持 Windows UI Automation 后端；`PyAutoGUI` 官方文档提供鼠标、键盘和截图 API；`pytest-qt` 官方文档提供 Qt/PySide 的 `qtbot` 测试夹具。桌面闭环优先使用真实鼠标/键盘动作，文件命令桥只用于稳定跳页、重建知识库、读取 UI 状态和在真实鼠标定位失败时兜底。

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
9. 回到顶部附近，优先通过真实鼠标双击打开翻译裂缝，再双击折叠、再次双击展开；若 UIA 找不到段落热区，才使用测试命令桥兜底并在报告中标记。
10. 发起一次裂缝内问答，检查全文检索和回答完成日志。
11. 发起一次右侧全文问答，检查证据列表、回答区、引用状态和追问建议。
12. 再滚动一次，截图。
13. 对同一 PDF 执行 born-digital 结构审计和 LaTeX 源码对照审计，把公式质量门禁纳入报告。
14. 收集日志尾部、错误/警告数量、关键行为计数、截图路径、性能样本和公式审计路径，写入 `test_artifacts/e2e/report.json`。

## 当前自动化命令

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/e2e_pdf_workflow.py --case attention
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/e2e_pdf_workflow.py --case napkin
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/e2e_pdf_workflow.py --case all
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/test_log_audit.py --clear
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/test_log_audit.py --output test_artifacts/log_audit.json
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/formula_latex_audit.py --case attention --output test_artifacts/formula_audit_attention.json
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/formula_latex_audit.py --case napkin --max-pages 120 --output test_artifacts/formula_audit_napkin_120.json
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/formula_latex_audit.py --case attention --quality-gate --output test_artifacts/formula_audit_gate.json
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools/formula_tool_comparison.py --case attention --max-pages 6 --sample-limit 2 --output test_artifacts/formula_tool_comparison/attention_report.json --db test_artifacts/formula_tool_comparison/attention_jobs.db
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools/formula_index_performance.py --case attention --max-pages 8 --output test_artifacts/formula_index_performance/attention_report.json --db test_artifacts/formula_index_performance/attention_jobs.db
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools/formula_index_performance.py --case napkin --max-pages 12 --output test_artifacts/formula_index_performance/napkin_report.json --db test_artifacts/formula_index_performance/napkin_jobs.db
```

## 判定标准

首版门槛：

- 主窗口成功启动并保持响应。
- PDF 成功打开并记录 `文档加载完成`。
- 滚动、页码跳转、缩放、真实鼠标双击翻译折叠/展开、知识库重建、裂缝问答、右侧全文问答动作完成并生成截图。
- 右侧全文问答必须产生至少 1 条检索依据、非空回答和追问建议。
- 日志中无 `ERROR`、`WARNING`、`CRITICAL`。
- 输出 `test_artifacts/e2e/report.json`。
- 公式审计输出 LaTeX 对照报告；使用 `--quality-gate` 时，若公式质量低于阈值必须返回非零退出码。
- 当前公式质量未过门禁时，E2E 总体应失败并写明 `expected_quality_gate_failure`，不能把低质量公式识别当作通过。
- 多轮公式索引性能报告必须证明导入热路径不加载 OCR/MFR 模型，并输出 `r0_pdf_structure`、`r1_cached_recognition`、`r3_cloud_semantic_review` 等轮次任务统计。
- 多轮公式解析必须覆盖 r0/r1/r2/r3/r4/r5 的设计边界：每轮落库、输入 hash 跳过、低置信只写候选、r3 不覆盖正文、GraphRAG 不阻塞阅读。
- r3 语义复核测试必须证明云端修正只写候选 JSON，不覆盖原始公式块；后台 QThread smoke 必须通过；真实 DeepSeek 调用作为可选 smoke test 单独运行，不能进入默认导入热路径。

公式审计门槛：

- 公式审计报告需记录 `recovered_common_source_commands`、`common_source_command_recall`、`source_near_match_rate`、`source_weak_match_rate`、`average_best_similarity`、`low_similarity_pdf_formula_count`，用于量化源码常见命令和源码公式在 PDF 抽取/MFR 后的恢复情况。
- 审计必须同时输出低相似 PDF 公式样本和未匹配源码公式样本，后续用它们对比 Pix2Text、Paddle PP-FormulaNet_plus、UniMERNet 等后端的真实提升。
- 审计默认使用 token 倒排索引和每公式 60 个候选做快速对比；需要更慢的严格对比时提高 `--max-match-candidates`。
- 默认 `--quality-gate` 阈值：`common_source_command_recall >= 0.35`、`source_weak_match_rate >= 0.35`、`low_similarity_pdf_rate <= 0.60`。
- 当前 no-MFD baseline 不满足公式质量门槛，门禁用于防止把低质量公式识别误判为已完成。

后续加强门槛：

- Napkin：大文档打开首屏、滚动、缩放、知识库状态达到明确时间预算。
- 缩放后不能长期停留在模糊缩放图，应在异步精确渲染完成后替换。
- 跳转应使用真实页码/目录 UI，而不是快捷键尝试。
- 问答应展示基于全文检索的引用片段和不可回答边界。

## 已知缺口

- 测试模式下的 QA/翻译使用 Mock 生成模型，能验证全文检索和 UI 闭环，但不能代表真实模型质量。
- Napkin 全文知识库仍使用哈希嵌入兜底，不是真语义 embedding。
- 右侧全文问答已有证据面板、检索状态、引用页跳转和追问建议；仍需补真实模型质量评估和更细粒度的引用高亮。
- 公式审计已经能统计 LaTeX 源和 PDF 抽取差距；扫描/图片公式已接入 Pix2Text MFR OCR，但整体 LaTeX 保真仍明显不足，需要继续做源码对齐和文本公式恢复。
- 外部工具环境已按独立 worker 思路存在：MinerU、Paddle Formula、Pix2Text 已有不同程度 smoke；PDF-Extract-Kit/UniMERNet 尚未跑通，旧 magic-pdf 缺权重。E2E 仍必须把这些工具输出当候选审计，不能把单图 smoke 当质量通过。

## 本轮闭环结果

2026-05-24 本轮已跑 Attention 和 Napkin 桌面闭环。结论如下：

- Attention：真实鼠标双击打开/折叠/展开翻译裂缝已通过；滚动、跳页、缩放、裂缝问答、右侧全文问答、截图和日志检查均完成；日志无 `ERROR/WARNING/CRITICAL`。公式 LaTeX 对照门禁失败，`common_source_command_recall=0.000`、`source_weak_match_rate=0.049`、`low_similarity_pdf_rate=0.611`。
- Napkin：1050 页长文档滚动、跳页、缩放、真实鼠标双击翻译循环、裂缝问答、右侧全文问答均完成；日志无 `ERROR/WARNING/CRITICAL`。缩放 max 约 232.7ms，渲染 max 约 43.4ms，visible update max 约 248.6ms。知识库强制重建耗时约 108.5s，必须优化；公式 LaTeX 对照门禁失败，`common_source_command_recall=0.000`、`source_weak_match_rate=0.039`、`low_similarity_pdf_rate=0.665`。
- 真实鼠标坐标在 Windows 高 DPI 下必须从 Qt 逻辑坐标换算到物理截图坐标；E2E 已自动换算，并把 `screen.device_pixel_ratio` 和点击点写入报告。
- 当前闭环测试总结果按预期失败，因为公式质量门禁没有达标。这个失败是后续优化入口，不能降级为通过。

下一步性能改进优先级：

- 手动“重建知识库”不应无条件删除并重写未变化的 Napkin 索引；应先用指纹快跳过，真正清空重建另设高级入口。
- 在没有真实 embedding 时，优先评估 SQLite FTS5 / Qdrant hybrid 等更合适的全文检索后端，避免 Chroma + 哈希向量成为长文档默认瓶颈。
- 公式质量继续走 born-digital PDF 结构解析和源码对照，不用 OCR 处理可解析文本层公式。

2026-05-24 性能修复验证：

- 手动“构建/重建知识库”已改为先检查现有索引，不再默认强制删除重建。
- Napkin 二次闭环中知识库检查耗时从约 108.5s 降到约 0.273s，等待来源为测试事件 `kb_rebuilt`。
- 本轮 Napkin 日志仍无 `ERROR/WARNING/CRITICAL`；公式质量门禁继续失败，属于预期未完成项。

2026-05-24 多轮公式索引性能基准：

- 新增 `tools/formula_index_performance.py`，只测导入阶段结构解析和多轮任务持久化，不加载 OCR/MFR 模型。
- Attention 前 8 页：结构解析 1.3314s，持久化 0.0036s，轮次任务 `r0_pdf_structure:queued=8`、`r3_cloud_semantic_review:queued=10`。
- Napkin 前 12 页：结构解析 0.9658s，持久化 0.0037s，轮次任务 `r0_pdf_structure:queued=12`、`r3_cloud_semantic_review:queued=2`。
- 最新 `--case all` 检测：Attention 15 页总 2.1970s、持久化 0.0046s；Napkin 前 16 页总 1.2997s、持久化 0.0306s。
- 当前报告证明任务入库开销极小；还不能证明全量 Napkin 1050 页、真实云端修正和高精度模型 worker 的性能，需要继续扩展闭环。
- r3 语义复核已有单元测试覆盖候选写回、坏 JSON 失败记录、缺失块跳过、批量限制和真实 QThread 后台 smoke；E2E 仍需补充真实 DeepSeek smoke 与 accepted 门禁验证。

2026-05-24 最新多工具候选状态：

- r0 born-digital 页面扫描已经只写 PDF 结构候选，不加载 OCR/MFR。
- `formula_recognition_results` 已记录结构候选、本地工具候选和 accepted 状态；同一候选 accepted 唯一性已有测试。
- r2 已通过外部 JSON worker 接 Paddle Formula 与 Pix2Text，输出默认未接受；后续 E2E/审计必须验证这些候选不会覆盖正文。
- `tools/formula_tool_comparison.py` 已能对同一批公式图运行隔离工具、计算源码相似度、记录耗时/warnings，并把 r2 候选落库。
- 最新相关单元测试组合为 66 passed；文档变更后如未改代码可不重复全量跑，但正式交付前必须重跑 Attention/Napkin 闭环。

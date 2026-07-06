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
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/full_software_validation.py --profile quick --case all --max-pages 2 --output-dir test_artifacts/full_software_validation_quick_live
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File tools/run_full_software_validation.ps1 -Profile standard -Case all
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/full_software_validation.py --profile full --case all --include-desktop-e2e --strict-logs --output-dir test_artifacts/full_software_validation_full
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/e2e_pdf_workflow.py --case attention
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/e2e_pdf_workflow.py --case napkin
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/e2e_pdf_workflow.py --case all
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/test_log_audit.py --clear
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/test_log_audit.py --output test_artifacts/log_audit.json
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/formula_latex_audit.py --case attention --output test_artifacts/formula_audit_attention.json
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/formula_latex_audit.py --case napkin --max-pages 120 --output test_artifacts/formula_audit_napkin_120.json
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools/formula_latex_audit.py --case attention --quality-gate --output test_artifacts/formula_audit_gate.json
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools/formula_multiround_pipeline.py --case attention --max-pages 6 --r1-limit 4 --r2-limit 1 --r3-limit 2 --r4-limit 12 --output test_artifacts/formula_multiround/attention_default.json --formula-db test_artifacts/formula_multiround/attention_default_formula.db --graph-db test_artifacts/formula_multiround/attention_default_graph.db
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools/formula_multiround_pipeline.py --case attention --max-pages 6 --r2-sample-formulas 1 --r2-limit 1 --r3-limit 1 --r4-limit 4 --output test_artifacts/formula_multiround/attention_forced_r2_local.json --formula-db test_artifacts/formula_multiround/attention_forced_r2_formula.db --graph-db test_artifacts/formula_multiround/attention_forced_r2_graph.db
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools/formula_multiround_pipeline.py --case attention --max-pages 6 --r3-limit 1 --r4-limit 1 --run-cloud-review --output test_artifacts/formula_multiround/attention_cloud_r3.json --formula-db test_artifacts/formula_multiround/attention_cloud_r3_formula.db --graph-db test_artifacts/formula_multiround/attention_cloud_r3_graph.db
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools/formula_index_performance.py --case attention --max-pages 8 --output test_artifacts/formula_index_performance/attention_report.json --db test_artifacts/formula_index_performance/attention_jobs.db
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools/formula_index_performance.py --case napkin --max-pages 12 --output test_artifacts/formula_index_performance/napkin_report.json --db test_artifacts/formula_index_performance/napkin_jobs.db
```

`tools/full_software_validation.py` 是当前全软件总验收入口，分四档：

- `quick`：短时间确认核心 pytest、公式索引、LaTeX 源码审计、多轮公式流水线、日志审计都能启动并产出报告。
- `standard`：覆盖 core/RAG/GraphRAG、公式、多轮、TinyBDMath、Attention、Napkin 前段和 Napkin 公式密集页段，并验证同一 DB 二次打开跳过。
- `full`：跑全仓 pytest 和更完整 Attention/Napkin 审计；桌面 E2E 仍需显式 `--include-desktop-e2e`，避免在无 GUI/无人值守时误开窗口。
- `nightly`：面向长时间无人值守，默认包括桌面 E2E，适合作为夜间全量验收。

默认总验收仍遵守 born-digital 非 OCR 主线：不启用云端、不会自动跑第三方公式工具。只有显式传入 `--include-cloud` 时，才会调用 DeepSeek；第三方公式工具 worker 已移除。

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
- `tools/formula_multiround_pipeline.py` 默认路径必须证明 born-digital PDF 不进入 OCR/MFR；显式 r2 样本路径必须证明本机候选只写 `local_precise` 未接受结果；`--reuse-db` 必须证明同一输入跳过。
- 多轮公式流水线必须输出 `formula_accuracy.stage_metrics`，按源 LaTeX 对照每个 stage/model 的 exact/near/weak match rate、average best similarity 和低相似候选；r0/r1/r2/r3 的准确率应递增，否则本轮只能算候选探索，不能 accepted。
- 源 LaTeX 只用于测试、审计和验收；真实用户运行路径不得依赖源码。源码中 `$...$`、`\(...\)`、`\[...\]`、`$$...$$` 包裹内容都算公式，其中行内公式必须单独统计。
- 每次公式验收必须同时检查是否违反“禁止造轮子/禁止硬编码”：生产公式路径不能出现样本特化词表、论文样本正则、一次性手写修复链，默认 r0 不能调用自写 LaTeX 重建器冒充高精度解析。
- r3 语义复核测试必须证明云端修正只写候选 JSON，不覆盖原始公式块；后台 QThread smoke 必须通过；真实 DeepSeek 调用作为可选 smoke test 单独运行，不能进入默认导入热路径。
- 全软件验证必须输出 `full_software_validation_report.json`，其中包含每一步命令、耗时、退出码、stdout/stderr 日志路径和关键产物路径；失败时不得只看终端最后一行，必须查对应 step 日志。
- 总验收不能只相信退出码：`formula_index`、`formula_latex_audit`、`formula_multiround` 和 `log_audit` 产物必须再做 JSON 语义检查。多轮报告必须能看到 r0、必要时 r0.5、TinyBDMath 候选、默认非 OCR/非云端边界、二次打开 skip/cache 证据。
- `standard/full/nightly` 必须覆盖 Attention 和 Napkin；Napkin 至少覆盖前段页和公式密集页段，不能只用 Attention 小文件证明整套软件跑通。
- `standard/full/nightly` 必须包含二次打开/复用 DB 跳过验证，证明 r0/r2a/fusion/r3/r4/r5 不是每次重扫。

2026-05-28 新增交互门槛：

- Napkin 前台 400x 或更高强度测试不能只统计动作次数。滚动强度必须模拟人类连续大滚轮：
  单次大幅滚动、连续 20 次以上无延迟滚动、反向滚动、极大缩放下滚动和翻译框打开时滚动。
- 极大缩放/快速滚动/跳页时，中间页必须始终可见。允许短暂显示模糊旧图、低清整页 fallback
  或最近 snapshot，不允许黑底、空白页或只有页码占位。
- tile 渲染首次 miss 时必须有视觉 fallback；日志中如果出现 `_paint_tiles` 首帧 `缓存:0 裁剪:0`，
  对应截图不能是纯黑/空白，否则测试失败。
- 400x 压测脚本必须覆盖多页和极端页，不允许在两三页之间重复跳几百次来凑次数。当前脚本已改成
  覆盖型抽样，但还需要在渲染 fallback 修好后重跑前台视觉验证。
- 翻译专项 E2E 需新增并发公式保护测试：同时请求两个含不同公式的段落，最终渲染的公式不得互相串线。

公式审计门槛：

- 公式审计报告需记录 `recovered_common_source_commands`、`common_source_command_recall`、`source_near_match_rate`、`source_weak_match_rate`、`average_best_similarity`、`low_similarity_pdf_formula_count`，用于量化源码常见命令和源码公式在 PDF 抽取/MFR 后的恢复情况。
- 多轮公式报告还必须记录 `inline_near_match_rate`、`inline_weak_match_rate`、`inline_unmatched_count`。小的行内公式、变量、上下标和数学字体如果丢失，应视为公式质量失败。
- 审计必须同时输出低相似 PDF 公式样本和未匹配源码公式样本，后续用它们对比 r0 facts、r2 本机候选和 TinyBDMath r2a 的真实提升。
- 审计默认使用 token 倒排索引和每公式 60 个候选做快速对比；需要更慢的严格对比时提高 `--max-match-candidates`。
- 默认 `--quality-gate` 阈值：`common_source_command_recall >= 0.35`、`source_weak_match_rate >= 0.35`、`low_similarity_pdf_rate <= 0.60`。
- 最终公式准确率目标为 `>= 99.9%`。当前 no-MFD / facts-only baseline 不满足公式质量门槛，门禁用于防止把低质量公式识别误判为已完成。

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
- 第三方公式工具 worker 已移除；E2E 不再验证 Paddle/MinerU/PEK 等隔离环境输出。公式质量仍必须通过 r0/r2/r2a/r3 候选审计和 accepted gate。
- 公式门禁仍保留 manual revision、recognition/fusion evidence 预览和 accepted 写回底层能力；主窗口人工审核入口已移除，E2E 重点转为二次打开 accepted 状态复用和 GraphRAG 路径证据检查。

## 本轮闭环结果

2026-07-05 文档/单元验证补充：

- 本轮没有重跑 Attention/Napkin 桌面 E2E，也没有关闭 Napkin 400x 极端滚动/缩放视觉门禁。
- 当前工作树保留用户确认的 TinyBDMath Graph Parser M5 改动：whole-formula graph
  context、结构化关系筛选、默认批量 torch eval 和 fast decode 可跳过 layout verifier。
  E2E 或全软件验证中如果要报告公式质量，必须区分 fast decode 与 `--full-verifier`
  结果；未跑 verifier 的 `layout_status=not_run` 不能作为 accepted gate 依据。
- 新增批注 / TOC / 运行时检查：
  `C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_annotation_store.py tests/test_navigator_toc.py tests/test_smoke.py -q`
  结果为 27 passed。
- 合并回归 + M5 定向组合：
  `C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_pdf_viewer_navigation.py tests/test_formula_index_flow.py tests/test_tinybdmath_graph_parser.py tests/test_tinybdmath_eval_decoded_latex.py -q`
  结果为 94 passed。
- 轻量接手测试：
  `C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_formula_index_flow.py tests/test_born_digital_math.py tests/test_formula_semantic_review.py tests/test_smoke.py -q`
  结果为 95 passed。
- M5 定向测试：
  `C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_tinybdmath_graph_parser.py tests/test_tinybdmath_eval_decoded_latex.py -q`
  结果为 32 passed。
- TinyBDMath 主线测试组合结果为 159 passed。

2026-06-05 UI 小步回归补充：

- 最近一次只补跑了 UI/导航单元级回归，不是桌面 E2E：
  `C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_smoke.py tests/test_pdf_viewer_navigation.py -q`
  结果为 30 passed。
- 覆盖范围包括菜单栏同层左右侧栏按钮、左右 dock 不可关闭和弹出/归位、工具栏空白区折叠/恢复、
  PDF 按需横向滚动、侧栏折叠后水平居中、bbox 横向定位、split page 缩放重建和离屏 split 状态保持。
- 未重跑 Attention/Napkin 桌面闭环；Napkin 400x 极端滚动/缩放视觉门禁仍需后续专门验证。

2026-05-27 全软件与桌面闭环复测：

- `tools/full_software_validation.py --profile quick --case all --max-pages 2 --output-dir test_artifacts/full_software_validation_quick_semantic_v2 --fail-fast` 通过：7 步约 53.721s，required failures=0。
- `tools/full_software_validation.py --profile standard --case all --output-dir test_artifacts/full_software_validation_standard_semantic` 通过：15 步约 119.678s，required failures=0。该版本已经做 JSON 产物语义检查，不只是看退出码。
- Attention 桌面 E2E 通过：启动约 7.061s，打开约 0.058s；滚动、跳页、缩放、知识库检查、真实鼠标双击翻译打开/折叠/再打开、裂缝问答、右侧全文问答全部完成；日志 `ERROR/WARNING/CRITICAL=0`；右侧问答 evidence=8、answer chars=173、followups=3；公式审计 quality gate 通过。
- Napkin 桌面 E2E 的 UI/性能/RAG 链路跑完：启动约 50.855s，打开约 0.035s，1050 页长文档跳转到 1/11/51/121/251 页、缩放、双击翻译、裂缝问答、右侧问答均完成；日志 `ERROR/WARNING/CRITICAL=0`；知识库检查约 2.710s；缩放 max 225.5ms、render max 40.3ms、visible update max 395.9ms。
- Napkin E2E 总退出码仍为失败，原因是公式质量门禁未过：`common_source_command_recall 0.128 < 0.350`。这不是 UI 崩溃，而是正确暴露公式识别质量未达标；不能降级为通过。

2026-05-28 公式门禁定位历史记录：

- 旧人工复核窗口曾展示 recognition result / fusion record 的已落库 evidence JSON，并把 `page_num + bbox` 传给 `PdfViewer.scroll_to_bbox()`；当前主窗口不再暴露该入口。
- 已补回归测试覆盖 fresh/offscreen layout 下 bbox 定位时滚动范围同步；此前可能出现高亮对象已创建但滚动条 maximum 为 0、页面没有移动的 bug。
- 后续桌面 E2E 不再走人工复核窗口；应验证后台候选、accepted/revision 数据复用、r5 写回和 GraphRAG artifact 不污染默认阅读路径。

2026-05-28 Napkin 400x 前台验证失败记录：

- 本轮验证暴露大页面 tile-only 渲染退化：极大缩放快速滚动/跳页时，中间页可能显示黑底/空白。
- 日志证据包括 `大页面 ... 仅瓦片渲染`、`_paint_tiles: ... 缓存:0 裁剪:0`、`TileCache: EVICT`，
  以及 `_update_visible_pages` 峰值约 241.9ms。
- 该失败不是“压力测试太强”，而是阅读器必须修复的 P0 体验问题。后续 E2E 只有在页面内容始终可见时才可通过。

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

2026-05-24 最新候选状态：

- r0 born-digital 页面扫描已经只写 PDF 结构候选，不加载 OCR/MFR。
- r0 facts-only 页面扫描不默认调用自写 LaTeX 重建器；它只保存 PDF 文本层、glyph/font/bbox/vector、diagnostics、input hash 和未接受候选。
- `formula_recognition_results` 已记录结构候选、本地工具候选和 accepted 状态；同一候选 accepted 唯一性已有测试。
- 2026-07-05 已拆掉第三方公式工具 worker 和对比脚本；r2 不再发现或调用 Paddle/Pix2Text/MinerU/PEK 隔离环境。
- `tools/formula_multiround_pipeline.py` 仍可在 Attention 前 6 页跑通 r0/r1/r2/r3/r4；显式 r2 单样本现在只走本机/缓存候选路径，真实 DeepSeek r3 单条 smoke 仍作为可选项。
- 当前源 LaTeX 准确率复核显示：Attention 前 6 页 facts-only r0/parsed blocks 平均 best similarity 约 0.668、near match rate 0.429；inline 公式 `inline_weak_match_rate=0.026`、`inline_unmatched_count=75`，远未达标；显式 r2 单样本最佳平均 similarity 约 0.854，有提升但远未达到极高准确率或 exact-match 目标。
- `formula_fusion` 当前能按 bbox/candidate_id 合并 parsed/r0/r2/r3 候选，输出 per-candidate 排名和定向 r2 队列；Attention 前 6 页 facts-only smoke 为 7 个区域、0 个 ready、7 个 `needs_more_evidence`。
- 显式 r2 单样本首轮冷启动约 245s，复用 DB 后约 1.2s 跳过；后续必须优化批处理、常驻 worker、模型缓存和超时。
- 旧的相关单元测试组合曾为 142 passed；2026-06-05 UI/导航小步回归为 30 passed；
  2026-07-05 TinyBDMath 主线测试组合为 159 passed。
  正式交付前必须重跑 Attention/Napkin 闭环。

# 新终端交接文档（2026-05-24）

本文件给新终端/新 AI 助手接手用。先读根目录 `AGENTS.md`，再读本文件，然后再看
`TODO.md`、`docs/current_goal_and_next_steps.md`、`docs/async_formula_indexing_design.md`、
`docs/formula_extraction_research.md`、`docs/e2e_test_plan.md`。

## 先读结论

当前主线目标没有完成，不能宣称项目已达标。已经完成的是：闭环测试方案、部分 E2E/日志/公式审计工具、RAG/GraphRAG 设计、公式多轮任务表与 r3 语义复核候选写回的第一版。没有完成的是：真实外部公式工具稳定部署、born-digital 公式高精度 LaTeX 还原、图片/扫描公式高精度 OCR/MFR、RAG/GraphRAG 的最终产品级体验、缩放/翻译/滚动渲染问题的完整闭环验收。

新会话不要先安装工具。先确认当前工作树、环境、防休眠和测试基线，再按本文的顺序继续。

## 当前真实状态

- 工作目录：`D:\程设大作业`。
- 主程序环境：`C:\Users\WYK\.conda\envs\pdf_ai_reader_314`。
- 现有用户环境不要动：`base`、`cs231n`、`drawing`、`science`、`pdf_ai_reader`、`pdf_ai_reader_314`、`lottery_python`、`pku_elective` 等。
- 2026-05-24 外部 PDF/公式工具环境安装尝试失败后，临时环境已删除；最后一次 `conda env list` 确认没有 `pdf_tool_*` 或 `pdf_formula_*` 环境。
- 防休眠脚本仍应检查，不要假设一定有效。脚本在 `tools/keep_awake.ps1` 和 `tools/keep_awake_watchdog.ps1`。
- 当前工作树有较多未提交改动和未跟踪文件。不要随手回退，也不要把 `测试资料/`、日志、缓存、临时 benchmark 输出提交。

## 必须遵守的设计哲学

1. **事实优先**：PDF 里真实存在的文本层、glyph、font、bbox、vector、ActualText、图片和源码对照才是证据。
2. **工具优先**：优先查官方文档、成熟工具、开源源码和论文工程实践。自写代码只做编排、适配、缓存、审计和必要 glue。
3. **证据优先**：公式、RAG、GraphRAG、问答都必须能追溯到页码、bbox、源码、检索片段、模型响应或任务日志。
4. **性能优先**：打开、滚动、缩放、翻译、基础问答不能等待 OCR/MFR/MinerU/GraphRAG/云端修正。
5. **异步持久化优先**：导入后可以尽早全篇入队，但每轮结果必须落库，二次打开必须复用，不能反复重扫。
6. **可替换优先**：MinerU、Pix2Text、UniMERNet、PDF-Extract-Kit、PaddleOCR、DeepSeek 等必须通过统一接口或独立 worker 接入。
7. **审计优先**：Attention 与 Napkin PDF + LaTeX 源码是公式验收基准；闭环 UI 操作、日志和性能报告是交互验收基准。

明确禁止：

- 不要用样本特化正则、固定词表、一次性启发式函数伪装公式识别。
- born-digital PDF 的公式默认不走 OCR；只有图片、扫描、无文本层、乱码、缺失映射、低置信或用户显式精扫才进入 OCR/MFR。
- 不要把重工具混进主程序环境或 UI 热路径。
- 不要提交额外署名、来源标记、生成工具署名、日志、缓存、测试资料、临时产物。

## 多轮公式解析要求（新会话必须先看）

多轮公式解析不是“一次扫完”，而是导入后快速可用、后台异步分批增强、每轮结果落库、可暂停恢复、可跳过已完成任务。

| 轮次 | 名称 | 目标 | 默认触发 | 写入位置 | 规则 |
| --- | --- | --- | --- | --- | --- |
| r0 | `r0_pdf_structure` | born-digital 快扫，抽取文本层、glyph、font、bbox、vector、图片和页级候选 | PDF 导入后全篇页面入队 | page scan jobs、基础 `DocumentBlock`、候选 metadata | 必须快；不 OCR；不阻塞首屏 |
| r1 | `r1_cached_recognition` | 对已有公式块或 `needs_ocr=True` 的图片/扫描候选做缓存优先识别 | 导入后小批量后台调度 | formula jobs、OCR/MFR cache、增量块 | 先查缓存；未命中才推理；不能抢 UI |
| r2 | `r2_local_high_precision` | 本地高精度/多工具复核，处理低置信、复杂矩阵、对齐环境、用户显式精扫 | 用户触发或后台空闲 | round jobs、recognition results | 独立 worker；可暂停；结果先做候选 |
| r3 | `r3_cloud_semantic_review` | DeepSeek 等分析模型基于上下文和候选公式做语义校对建议 | 所有已解析公式块可入队，按批消费 | `formula_round_jobs.result_json` | 不直接覆盖正文；必须保留 `suggested_latex/confidence/reason/risks/raw_response` |
| r4 | `r4_knowledge_graph` | 将公式、章节、定理、概念、引用关系写入 GraphRAG artifact | 基础索引就绪后异步 | graph artifact、关系边、证据节点 | GraphRAG 不阻塞基础问答和阅读 |
| r5 | 知识库增量增强（设计轮次，代码未落地） | 把高置信修正增量写回全文 RAG/FTS/向量库 | accepted 结果变化时 | knowledge index、block revision | 必须按 block/content hash 跳过未变内容 |

硬要求：

- 每轮任务必须有 `queued/running/done/failed/skipped` 状态和输入 hash。
- 每轮输出都要落库，不能只保存在内存。
- 同一页、同一 bbox、同一图像 hash、同一模型版本、同一预处理版本命中时必须跳过。
- 低置信结果只能写候选和 warnings，不能覆盖 `DocumentBlock.content`。
- r3 云端语义修正只能给建议，自动接受必须另有门禁：语法、证据一致性、上下文一致性、置信度和回归测试。
- UI 热路径只读缓存，不做模型冷启动。

对应设计文件：`docs/async_formula_indexing_design.md`。对应当前代码主要在：

- `src/app/formula_index_store.py`
- `src/app/formula_index_scheduler.py`
- `src/app/formula_index_flow.py`
- `src/app/formula_semantic_review.py`
- `src/ui/main_window.py`
- `src/main.py`

## 本轮已完成事项

文档与计划：

- 写入根目录 `AGENTS.md`，作为新会话第一入口。
- `TODO.md` 顶部新增 2026-05-24 新终端交接入口。
- 已有文档包含当前目标、设计哲学、RAG/GraphRAG 迁移、公式抽取调研、OCR 性能设计、异步多轮索引设计、E2E 测试方案。

公式与索引代码进度：

- 已有多轮公式任务枚举与存储：r0/r1/r2/r3/r4。
- 导入后会把页级结构扫描、需要 OCR 的公式块、已解析公式块的 r3 复核任务写入队列。
- `FormulaSemanticReviewService` 和 `FormulaSemanticReviewFlow` 已有第一版：批量调用分析模型，写回 JSON 候选，不覆盖正文。
- UI 空闲时可小批量调度公式索引/语义复核，避免导入热路径同步等待。
- 日志改为轮转并增加清理工具，避免日志无限膨胀。

测试和工具进度：

- 已有 `tools/e2e_pdf_workflow.py`，用于桌面闭环测试滚动、跳转、缩放、翻译、问答、截图和日志。
- 已有 `tools/formula_latex_audit.py`，用于 Attention/Napkin 与 LaTeX 源码对照审计。
- 已有 `tools/formula_ocr_benchmark.py`，用于 OCR/MFR 后端抽样性能测试。
- 已有 `tools/formula_index_performance.py`，用于多轮公式索引任务入库性能检测。
- 已有 `tools/test_log_audit.py`，用于清理和审计日志。
- 曾经的测试基线包括：全量约 `153 passed, 3 skipped`；目标测试约 `32 passed`；Attention/Napkin 多轮任务入库性能约秒级。由于后续环境清理和文档修改后未重新跑全量测试，新会话必须重新验证。

防休眠：

- 已有 `tools/keep_awake.ps1`：调用 Windows `SetThreadExecutionState`，可选发送 F15。
- 已有 `tools/keep_awake_watchdog.ps1`：周期性重写电源策略、重启 worker、写日志。
- 最后一次进程检查看到多个 `keep_awake` 相关 PowerShell 进程，但新会话仍必须重新确认。

## 已遇到并解决的问题

1. **不能把外部公式工具混进主环境**
   - 结论：主程序环境只保留项目运行依赖；MinerU/PaddleOCR/PDF-Extract-Kit/UniMERNet 等必须独立 worker 环境。

2. **临时环境污染风险已清理**
   - 曾创建/尝试过 `pdf_formula_tools_310`、`pdf_tool_mineru310`、`pdf_tool_magic310`、`pdf_tool_paddle310`、`pdf_tool_pek310`。
   - 已删除这些临时环境；最后一次 `conda env list` 不再显示它们。

3. **旧用户环境不可触碰**
   - 曾误入已有环境的风险被识别。之后明确：不要动 `cs231n`、`base`、`drawing`、`science` 等用户环境。

4. **多轮任务必须落库**
   - 已从“临时扫描”改为 `FormulaIndexStore` 持久化多轮任务和 r3 候选。

5. **云端模型不应长期 mock**
   - 已在设计中写明：DeepSeek 分析/回答链路要有可选真实 smoke test，并使用现有 `config.yaml` API 配置。

6. **日志不能无限增长**
   - 已加入日志轮转和日志清理/审计工具；每次 E2E 后应检查日志。

## 未解决问题

1. **born-digital 公式还原精度不达目标**
   - 当前结构事实层和审计工具可用，但 LaTeX 还原仍不足。
   - 关键难点：PDF 通常保存排版后的 glyph/bbox，不保存原 TeX AST。复杂二维结构、源码宏、字体编码、表格/列表误吸、页级源码对齐都未完全解决。
   - 方向：优先复用成熟 PDF/公式结构工具或源码；自写只做中间表示、审计、调度、缓存。

2. **图片/扫描公式 OCR/MFR 未稳定跑通**
   - Pix2Text 已有但本地效果差，原因待查：模型版本、预处理、DPI、裁剪、CPU 性能、缓存、输入区域质量都有可能。
   - PaddleOCR Formula、UniMERNet、PDF-Extract-Kit、MinerU 都需要独立环境真实烟测。

3. **外部工具安装尝试失败**
   - 失败不是单个包的问题，而是多个大工具依赖栈互相冲突、PyPI 元数据不完整、CLI 运行时依赖漏声明、Windows 环境和 conda/pip 混用复杂。
   - 下一次必须先查官方文档和版本矩阵，再逐个独立环境验证，不要一次性混装。

4. **RAG/GraphRAG 仍未达到产品级**
   - FTS/RAG 方向已有基础，但全文理解、证据链、公式/定理/引用图谱、问答 UI 还要继续打磨。
   - GraphRAG 必须异步，不得阻塞基础阅读。

5. **闭环 UI 验收未最终完成**
   - 滚动、跳转、双击翻译/隐藏/再次打开、缩放清晰度、长文档问答性能都需要真实 E2E 反复跑。

6. **缩放渲染问题仍需重点复查**
   - 目标是缩放后不模糊、不错位、不丢翻译层、不破坏滚动定位。

## 外部工具调研与环境教训

调研对象：

- MinerU / `magic-pdf`
- Pix2Text
- UniMERNet
- PDF-Extract-Kit
- PaddleOCR Formula

已经得到的教训：

- `pip check` 通过不等于工具可用；必须跑 import、CLI、真实 PDF 小页烟测。
- MinerU、magic-pdf、PaddleOCR、PDF-Extract-Kit、UniMERNet 不要混装。
- Windows 上不要并行跑多个 `conda create`，容易触发缓存/锁冲突。
- 能用 conda/mamba 装的重型底座优先考虑 conda/mamba；PyPI-only 工具再用 pip。
- 使用国内镜像可以提高速度，但版本兼容仍必须按官方文档确认。
- 如果工具会下载模型，必须记录模型路径、版本、大小、冷启动时间、推理时间和缓存命中时间。

建议下一次环境矩阵：

| 环境 | Python | 目标 | 验证 |
| --- | --- | --- | --- |
| `pdf_tool_paddle310` | 3.10 | `paddlepaddle` + `paddleocr` FormulaRecognition | import、单张公式图、真实 PDF 裁剪图 |
| `pdf_tool_mineru311` | 3.11 或官方推荐 | MinerU 最新版本 | CLI help、最小 PDF 转换、Attention 前几页 |
| `pdf_tool_magic310` | 3.10 | 旧 `magic-pdf` 对照 | CLI help、真实 PDF 小页 |
| `pdf_tool_pek310` | 3.10 | PDF-Extract-Kit + UniMERNet | Python API、示例命令、真实 PDF 小页 |
| `pdf_tool_pix2text` | 3.10/3.11 | Pix2Text 现有链路复测 | 预处理/DPI/裁剪 ablation |

每个环境建完必须记录：

- `python --version`
- `pip freeze`
- `pip check`
- 关键包版本
- import 结果
- CLI help 结果
- 真实 PDF 小页 smoke 结果
- 首次冷启动时间、稳定推理时间、峰值内存

## 文件地图

- `AGENTS.md`：新会话第一入口，写明设计哲学、环境状态、防休眠、版本控制、文件地图。
- `TODO.md`：项目长期演化史与最新交接入口。
- `需求文档 (PRD).md`：产品目标和未完成需求基线。
- `技术设计文档 (TDD).md`：原技术设计基线。
- `docs/current_goal_and_next_steps.md`：当前总目标、约束、已完成检查点、今晚/下一阶段任务。
- `docs/e2e_test_plan.md`：Attention/Napkin 闭环测试方案。
- `docs/formula_extraction_research.md`：born-digital 公式解析与成熟工具边界。
- `docs/formula_ocr_performance_design.md`：图片/扫描公式 OCR/MFR 性能方案。
- `docs/async_formula_indexing_design.md`：异步多轮公式和全文索引设计。
- `docs/rag_graphrag_migration_plan.md`：RAG/GraphRAG 迁移方案。
- `src/main.py`：应用入口、日志轮转、服务注册、模型/知识库服务创建。
- `src/ui/main_window.py`：主窗口、PDF 交互、后台公式和 r3 语义复核调度。
- `src/app/document_flow.py`：文档解析完成后的知识库和图谱调度入口。
- `src/app/formula_index_store.py`：公式多轮任务/结果持久化。
- `src/app/formula_index_scheduler.py`：公式任务优先级和轮次规划。
- `src/app/formula_index_flow.py`：公式索引后台 QThread 流程。
- `src/app/formula_semantic_review.py`：r3 云端/分析模型语义复核候选写回。
- `src/core/math_ocr.py`：OCR/MFR 调用边界与缓存。
- `src/core/formula_recognizers.py`：公式识别后端适配。
- `tools/e2e_pdf_workflow.py`：桌面闭环测试。
- `tools/formula_latex_audit.py`：公式与 LaTeX 源码对照审计。
- `tools/formula_ocr_benchmark.py`：OCR/MFR 抽样性能测试。
- `tools/formula_index_performance.py`：多轮公式索引任务入库性能测试。
- `tools/external_formula_tools_smoke.py`：外部公式工具烟测入口。
- `tools/test_log_audit.py`：日志清理和审计。
- `tools/keep_awake.ps1` / `tools/keep_awake_watchdog.ps1`：防休眠。
- `测试资料/`：Attention、Napkin PDF、LaTeX 源码和图片资源，仅测试使用，不要提交。
- `开源借鉴/`：优秀开源项目参考，调研时可读，不要盲目复制。

## 新会话第一小时建议

1. 查看工作树：

```powershell
git status --short
```

2. 确认环境和不要动的用户环境：

```powershell
conda env list
```

3. 检查防休眠：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*keep_awake*' } |
  Select-Object ProcessId,Name,CommandLine
Get-Content logs\keep_awake_watchdog.log -Tail 20
```

4. 清理/审计日志：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\test_log_audit.py --clear
```

5. 跑轻量测试：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_smoke.py -q
```

6. 跑公式多轮入库性能：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_index_performance.py --case all
```

7. 跑 Attention/Napkin 公式质量审计：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_latex_audit.py --case attention --quality-gate
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_latex_audit.py --case napkin --max-pages 120 --quality-gate
```

8. 跑 E2E 闭环测试：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\e2e_pdf_workflow.py --case all
```

9. 如果要重新装外部公式工具，先写版本矩阵，再逐个独立环境安装，且每个环境只装一个工具栈。

## 下一步任务清单

P0：

1. 重新验证当前代码基线，确认文档修改前后的测试状态。
2. 补齐 r0/r1/r2/r3/r4/r5 的端到端测试：入队、跳过、失败重试、结果落库、二次打开复用。
3. 用 Attention/Napkin 做真实公式质量门禁，记录失败样例和性能。
4. 修复或隔离任何正则/硬编码公式识别实验，不能进入默认路径。
5. 重新调研并独立安装外部工具，优先跑通 Pix2Text 复测和 PaddleOCR Formula，然后再 MinerU/PDF-Extract-Kit/UniMERNet。

P1：

1. 将外部工具封装成 worker/backend 接口，主环境只调统一协议。
2. 建立公式候选 accepted/rejected/revision 门禁。
3. 改进问答 UI：证据可读性、全文知识库状态、追问、失败原因、性能反馈。
4. 完成 DeepSeek 分析回答真实 smoke test，并确保错误时有清晰降级。
5. 继续推进 GraphRAG artifact：章节、定理、公式、引用、概念关系。

P2：

1. Profile 证明热点后再考虑 C++17/pybind11：bbox overlap、二维布局、源码对齐、图像裁剪预处理。
2. 将长文档性能基准纳入固定门禁。
3. 建立自动日志摘要和失败样例归档。

## 交接提示词

把下面这段给下一个新会话：

```text
你接手的是 D:\程设大作业 的 PDF AI Reader 项目。先读根目录 AGENTS.md，再读 docs/next_session_handoff.md、TODO.md、docs/current_goal_and_next_steps.md、docs/async_formula_indexing_design.md、docs/formula_extraction_research.md、docs/e2e_test_plan.md。

当前主环境是 C:\Users\WYK\.conda\envs\pdf_ai_reader_314。不要动用户已有环境 base/cs231n/drawing/science/pdf_ai_reader/pdf_ai_reader_314 等。之前临时 PDF 工具环境安装失败后已删除，conda env list 最后确认没有 pdf_tool_* 或 pdf_formula_*。不要先装包，先确认 git status、conda env list、防休眠进程和测试基线。

设计红线：born-digital PDF 公式默认不走 OCR；图片/扫描/无文本层/乱码/低置信才走 OCR/MFR。禁止样本特化正则、固定词表、一次性启发式函数伪装公式识别。优先成熟工具和官方文档，自写代码只做编排、适配、缓存、审计和必要 glue。所有重任务异步分批，结果必须落库，二次打开跳过已完成任务。不要把 MinerU/Pix2Text/UniMERNet/PDF-Extract-Kit/PaddleOCR 混装到主环境。

多轮公式解析必须按 r0/r1/r2/r3/r4/r5 推进：r0 PDF 结构快扫，不 OCR；r1 缓存优先 OCR/MFR 补救；r2 本地高精度多工具复核；r3 DeepSeek 语义校对只写候选；r4 GraphRAG 结构增强；r5 高置信结果增量写回知识库。每轮必须有任务状态、输入 hash、模型版本、结果 JSON 和跳过机制，低置信不能覆盖正文。

测试资料在 测试资料/：Attention 是小文件，Napkin 是大文件，两者 PDF、LaTeX 源码和图片资源都要用于公式质量和性能验收。闭环测试必须模拟滚动翻页、跳转、双击翻译/隐藏/再次双击打开、缩放、问答、日志审计。长文档性能、缩放清晰度、翻译和问答延迟必须作为门槛。

下一步先跑轻量测试：C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_smoke.py -q。然后跑 tools/formula_index_performance.py、tools/formula_latex_audit.py 的 Attention/Napkin 门禁，再跑 tools/e2e_pdf_workflow.py。外部工具要先写版本矩阵，再逐个独立环境安装和真实 PDF 小页烟测。所有提交不得带额外署名、来源标记或生成工具署名，不提交测试资料、日志、缓存、临时产物。
```

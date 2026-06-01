# 新会话必读

本文件是新终端/新 AI 助手接手本项目时的第一入口。先读这里，再读
`docs/next_session_handoff.md`、`docs/current_goal_and_next_steps.md`、`TODO.md`、
`需求文档 (PRD).md`、`技术设计文档 (TDD).md`。

特别注意：多轮公式解析要求必须先看 `docs/next_session_handoff.md` 的
“多轮公式解析要求”章节和 `docs/async_formula_indexing_design.md`。新会话不得把公式
解析理解成一次性同步扫描；r0/r1/r2/r3/r4/r5 每轮都必须异步分批、结果落库、
二次打开跳过已完成任务。

## 当前真实状态

- 工作目录：`D:\程设大作业`。
- 主程序环境仍是 `C:\Users\WYK\.conda\envs\pdf_ai_reader_314`。
- 用户已有环境不要动：`base`、`cs231n`、`pdf_ai_reader`、`pdf_ai_reader_314`、`drawing`、`science`、`lottery_python`、`pku_elective` 等。
- 2026-05-24 已重新按独立 worker 思路建立外部工具环境，当前 `conda env list`
  显示：`pdf_tool_paddle310`、`pdf_tool_mineru310`、`pdf_tool_pix2text310`、
  `pdf_tool_magic310`、`pdf_tool_pek310`。这些是隔离工具环境，不是主程序环境。
- 当前工具验证状态：MinerU 3.1.15 已用本地新模型跑通 Attention 单页 smoke；
  PaddleOCR Formula 与 Pix2Text 已通过单张公式图 worker smoke，但结果只能作为候选；
  `magic-pdf` 旧路线已装但缺旧权重；PDF-Extract-Kit 环境存在但源码拉取失败，尚未跑通。
- 2026-05-28 已补齐公式审核基础闭环：accepted/rejected audit、命令行审核、基础 UI、人工
  revision、evidence JSON 预览、PDF page/bbox 定位、r5 知识库增量 upsert 和 accepted
  公式 GraphRAG artifact 同步均已接线。仍未完成的是批量审核体验、r4/r5 高质量语义路径证据、
  Napkin 大样本公式质量门禁和最终 99.9%+ 公式还原质量。
- 2026-05-28 性能修复尝试暴露 P0 退化：Napkin 前台 400x 测试在极大缩放/快速滚动/跳页时
  出现大页面 tile-only 首帧空瓦片，用户看到黑底/空白页。`TODO.md` 顶部已记录今日全部
  改动复盘、优化收益、破坏点和修复顺序。后续必须先修“滚动时中间页始终可见”，不能继续
  以牺牲阅读体验换性能指标。
- 同一复盘还记录翻译链路风险：`TranslationService` 共享 `TextPreprocessor`，并发流式翻译
  可能互相覆盖公式占位映射；当前 diff 还会把 `$...$` 改写为 `\(...\)` 并 strip 内容。后续
  主攻翻译时要先做独立 protection session 和并发复现测试，不得靠硬编码规则补。
- 2026-05-28 最新停工红线：用户已明确要求“不要再工作了，除非你非常确定能改掉一个 bug”。
  新会话不得继续大规模重构、压测、算法探索、工具安装或性能实验；只有在读完交接文档、定位到
  单一具体 bug、修复路径非常确定且可用小范围测试验证时，才允许动代码。否则只允许汇报状态或等待
  用户明确新指令。
- 2026-06-01 已提交 TinyBDMath relation scoring 性能优化 `5b5b38e`：PyTorch batch/vectorized
  打分、compact score、direct structural decode、no-score-jsonl 和 streaming eval 已接入。全量 direct
  eval 约 192.59s，structural F1=0.315585，decoded exact_match_rate=0.523242、near_match_rate=0.659550；
  性能路径可用，但公式质量远未达标。后续只能改监督、模型、decoder/verifier 和门禁，不能写样本硬编码规则。
- 2026-06-01 已清理旧运行产物和废弃 TinyBDMath 入口。目录保留/可删边界见
  `docs/workspace_inventory.md`。已删除 `tools/tinybdmath_realdata_pipeline.py`、
  `tools/tinybdmath_score_candidates.py`、`tools/run_tinybdmath_realdata.ps1` 和
  `tools/run_tinybdmath_torch_science.ps1`；当前 TinyBDMath 入口以插桩数据、graph rows 和
  `tools/run_tinybdmath_relation_pipeline.ps1` 为准。
- 2026-06-01 已重写 TinyBDMath 下一步方案：旧 edge scorer/弱 relation hint/旧 decoder 补丁路线
  只作为 baseline，不再作为主线。下一步先做 CSLT 目标树、PDF graph 到 CSLT 的 alignment、
  Graph Parser、constrained decode 和 layout verifier；细节见
  `docs/tiny_born_digital_math_model_engineering.md` 和 `docs/current_goal_and_next_steps.md`。
  若工作树中仍有全局 parent forest 试验代码，只能当 baseline/ablation，不能当作最终 bug fix。
- 外部工具模型缓存已迁到仓库本地 `.tool_models/`，并由 `PDF_AI_READER_TOOL_MODELS_DIR`
  作为统一覆盖入口；不要再把新模型缓存放到 `C:\pdf_ai_reader_tool_models` 或用户默认
  `.cache\modelscope`。
- 如果新会话要修改、重装或删除外部工具，必须先 `conda env list` 和检查残留进程，
  不能假设旧环境不存在或一定可用。
- 不要把测试资料、日志、缓存、临时 benchmark 输出提交进版本库。

## 不可违反的设计哲学

- **事实优先**：PDF 里真实存在的文本层、glyph、font、bbox、vector、ActualText、图片和源码对照才是证据。
- **工具优先**：优先调研成熟工具、官方文档、开源源码和论文工程实践；自写代码只做编排、适配、缓存、审计和必要 glue。
- **证据优先**：公式、RAG、GraphRAG、问答必须能追溯到页码、bbox、源码、检索片段或模型响应日志。
- **性能优先**：打开、滚动、缩放、翻译、基础问答不能等待 OCR/MFR/MinerU/GraphRAG/云端修正。
- **异步持久化优先**：导入后可以尽早全篇入队，但每轮结果必须落库，不能每次重新扫。
- **长任务并行推进优先**：预计超过 1 分钟的 LaTeX 编译、插桩训练集、Napkin 全量、OCR/MFR、外部工具 benchmark 必须后台运行并写日志；前台继续写代码、设计、文档或审计，不能同步干等脚本结束。
- **可替换优先**：外部工具必须是独立 worker 或统一接口后的可选后端，不能写死进 UI 或热路径。
- **审计优先**：Attention 和 Napkin PDF + LaTeX 源码是公式验收基准；日志和性能报告是交互验收基准。

## 公式识别边界

- 当前主攻 born-digital PDF，非扫描版公式不要用 OCR。
- born-digital 路线应优先用 MuPDF/Poppler/pdftext/pdfminer 等结构事实，恢复 glyph、bbox、font、vector 和二维布局证据。
- OCR/MFR 只用于图片公式、扫描页、无文本层、乱码/缺失映射、用户显式高精度精扫、低置信 evidence。
- 不允许用样本特化正则、固定词表、一次性启发式函数伪装公式识别。
- 数学内容输出必须有定界符：行内 `\(...\)`，行间 `$$...$$`。
- 低置信公式只能写候选和 warnings，不能直接覆盖正文或知识库的高置信内容。

多轮公式解析最低要求：

- `r0_pdf_structure`：PDF 结构快扫，导入后全篇入队，不 OCR，不阻塞首屏。
- `r1_cached_recognition`：缓存优先处理图片/扫描/needs_ocr 候选，未命中才推理。
- `r2_local_high_precision`：本地多工具高精度复核，只写候选，适合低置信和用户精扫。
- `r3_cloud_semantic_review`：DeepSeek 等分析模型做语义校对建议，写入 result JSON，不覆盖正文。
- `r4_knowledge_graph`：异步写入公式/章节/定理/引用/概念图谱证据。
- `r5_knowledge_incremental_update`：accepted 高置信结果变化后增量 upsert 全文知识库，并同步
  accepted 公式 GraphRAG artifact；低置信候选、rejected result 和未审核 fusion 不能污染正文、
  FTS、向量库或 GraphRAG。

## 外部工具环境原则

2026-05-24 的失败教训：

- 不要把 MinerU、magic-pdf、PaddleOCR、PDF-Extract-Kit、UniMERNet 混装到一个环境。
- 不要并行执行多个 `conda create`，Windows 上会触发 conda repodata/cache 锁冲突。
- `pip check` 通过不代表 CLI 可跑；这些工具可能漏声明运行时依赖。
- 如果必须重装，先设计版本矩阵，再按顺序建环境，再安装，再做 import/CLI/真实 PDF 小页烟测。

当前矩阵只作为起点，必须重新查官方文档确认后再升级或重装：

- `pdf_tool_paddle310`：Python 3.10，目标 `paddlepaddle` + `paddleocr`，验证 `FormulaRecognition`。
- `pdf_tool_mineru311` 或 `pdf_tool_mineru310`：独立 MinerU，不与 PDF-Extract-Kit 混装。
- `pdf_tool_magic310`：旧 `magic-pdf` 独立环境，只作为历史 MinerU 路线对照。
- `pdf_tool_pek310`：PDF-Extract-Kit + UniMERNet 独立环境，注意 `unimernet.exe` 入口可能损坏，要从 Python API/源码入口验证。
- `pdf_tool_pix2text310`：Pix2Text 独立验证环境，用于和主环境已有 Pix2Text 能力对照。

重装前必须执行：

```powershell
conda env list
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'pdf_tool_|pdf_formula_|mineru|magic-pdf|paddleocr|unimernet|conda.*create' } |
  Select-Object ProcessId,Name,CommandLine
```

## 防休眠要求

用户要求长任务期间不能锁屏休眠。仓库已有脚本：

- `tools/keep_awake.ps1`：调用 `SetThreadExecutionState`，可选发送 F15。
- `tools/keep_awake_watchdog.ps1`：周期性设置电源超时、重启 worker、写 `logs/keep_awake_watchdog.log`。

建议新会话先检查：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*keep_awake*' } |
  Select-Object ProcessId,Name,CommandLine
Get-Content logs\keep_awake_watchdog.log -Tail 20
```

如未运行，再启动：

```powershell
Start-Process -FilePath powershell.exe `
  -ArgumentList '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "D:\程设大作业\tools\keep_awake_watchdog.ps1" -IntervalSeconds 60 -WorkerIntervalSeconds 20' `
  -WindowStyle Hidden
```

这只能防普通空闲睡眠，不能防断电、电池耗尽、系统更新、用户手动关机或系统策略强制锁定。

## 版本控制要求

- 提交信息不得添加任何额外署名、来源标记或生成工具署名。
- 提交前检查：

```powershell
git status --short
rg -n "额外署名|来源标记|生成工具署名|自动署名" . -S -g '!测试资料/**' -g '!test_artifacts/**' -g '!logs/**'
```

- 不提交 `TODO.md` 以外的用户临时资料，除非用户明确要求；历史要求中曾强调不要提交 `测试资料/`、缓存、日志、临时产物。

## 文件与组件地图

- `需求文档 (PRD).md`：产品目标和用户需求基线。
- `技术设计文档 (TDD).md`：原技术架构和设计说明。
- `TODO.md`：长期演化史、当前状态、下一步任务。顶部为最新交接入口。
- `docs/next_session_handoff.md`：本轮详细交接、问题清单、下一位 AI 助手提示词。
- `docs/current_goal_and_next_steps.md`：当前总目标、设计边界、已完成检查点。
- `docs/tiny_born_digital_math_model_engineering.md`：TinyBDMath 神经符号公式恢复计划，当前下一步主线入口。
- `docs/e2e_test_plan.md`：Attention/Napkin 闭环测试方案和验收门槛。
- `TODO.md` 顶部 `2026-05-28 今日性能修复复盘`：今日相对最初基线的全部改动、明确退化、
  翻译风险和下一步 P0 修复顺序。
- `docs/formula_extraction_research.md`：born-digital 公式解析路线与工具边界。
- `docs/formula_multitool_fusion_design.md`：多工具公式候选融合、源码准确率复核、accepted 门禁和禁止硬编码解析规则的细设计。
- `docs/formula_ocr_performance_design.md`：图片/扫描公式 OCR/MFR 性能路线。
- `docs/async_formula_indexing_design.md`：异步多轮公式索引与持久化设计。
- `docs/rag_graphrag_migration_plan.md`：RAG/GraphRAG 迁移方案。
- `src/main.py`：应用入口、服务注册、日志轮转、模型/知识库服务创建。
- `src/ui/main_window.py`：主窗口、PDF 交互、后台公式/语义复核调度。
- `src/app/document_flow.py`：文档解析完成、知识库构建和图谱调度入口。
- `src/app/formula_index_store.py`：公式多轮任务/结果持久化。
- `src/app/formula_index_scheduler.py`：公式任务优先级和轮次规划。
- `src/app/formula_index_flow.py`：公式索引后台 QThread 流程。
- `src/app/formula_semantic_review.py`：r3 云端/分析模型语义复核候选写回。
- `src/core/born_digital_formula_extractor.py`：r0 born-digital PDF 结构候选抽取适配。
- `src/core/external_formula_tools.py`：隔离外部公式工具 worker 统一调用接口。
- `src/core/math_ocr.py`、`src/core/formula_recognizers.py`：OCR/MFR 后端适配与缓存边界。
- `tools/formula_tool_worker.py`：外部工具 JSON worker，当前支持 Paddle Formula 和 Pix2Text 公式图 smoke。
- `tools/e2e_pdf_workflow.py`：桌面闭环测试，覆盖滚动、跳转、缩放、双击翻译、问答、截图、日志。
- `tools/formula_latex_audit.py`：公式与 LaTeX 源码对照审计。
- `tools/formula_ocr_benchmark.py`：OCR/MFR 抽样性能和后端对比。
- `tools/formula_tool_comparison.py`：同一批公式图的外部工具候选对比，并把 r2 候选落库；`--auto-local-tools` 只在显式传入时发现隔离工具环境。
- `tools/formula_multiround_pipeline.py`：r0-r5 端到端多轮公式流水线 smoke/benchmark；默认 born-digital 不 OCR，显式 `--r2-sample-formulas` 才跑 r2 多工具，`--reuse-db` 验证跳过。
- `tools/formula_index_performance.py`：导入阶段多轮公式索引任务入库性能。
- `tools/test_log_audit.py`：清理/审计日志。

## 新会话建议第一步

1. 不要先装工具。先读 `docs/next_session_handoff.md`。
2. 执行 `git status --short`，确认未提交改动。
3. 执行 `conda env list`，确认主环境和隔离工具环境状态，不要误删或混装。
4. 检查 keep-awake 是否运行。
5. 用主环境跑轻量测试，确认代码基线：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_external_formula_tools.py tests/test_formula_index_flow.py tests/test_born_digital_math.py tests/test_formula_semantic_review.py tests/test_smoke.py -q
```

6. 如果要继续外部工具调研，先写环境矩阵和验收命令，再安装。
7. 如果要继续代码主线，优先推进：多轮公式解析真实 worker 接口、Attention/Napkin 公式源码对照、RAG/GraphRAG 证据链、闭环性能测试。

# 当前目标与执行方案

## 设计哲学

这个项目后续按“事实优先、工具优先、证据优先、性能优先”的方式推进：

- **事实优先**：PDF 里实际存在什么结构，就解析什么结构；无法从文本层、字体、glyph、bbox、矢量线、标签或 OCR 证据证明的内容，不能靠猜测补全。
- **工具优先**：先找成熟库、成熟源码、官方文档和论文/工程实践，再决定是否自己写适配层；自写代码必须围绕已有工具补足工程粘合和质量审计。
- **证据优先**：公式识别、RAG、GraphRAG、问答回答都必须能追溯到 PDF 页面、源码对照、检索证据或模型响应日志。
- **性能优先**：任何“更准”的方案，如果让打开、滚动、缩放、翻译或基础问答明显变慢，都不能进默认路径；重任务只能后台化、缓存化、可取消。
- **可替换优先**：后端可以大胆升级，但必须抽象稳定、配置隔离、缓存版本隔离、测试可证明、失败可降级。
- **审计优先**：不能用主观感觉验收。Attention/Napkin 源 LaTeX 对照、长文档性能、真实云模型 smoke test 和日志检查是升级门槛。

## 设计边界与验收方式

设计边界：

- born-digital PDF 公式识别只走 PDF 结构解析，不走 OCR。
- 图片/扫描公式才走 OCR/MFR，并且默认后台限流。
- 禁止把样本特化正则、固定词表、一次性启发式函数作为核心算法。
- 禁止在 UI、知识库热路径或问答流程里直接写死具体模型/工具。
- 禁止为了接入重工具污染主环境；重依赖必须是可选 worker 或独立后端。
- 禁止把低置信结果当作已识别公式写入正文或知识库。
- 禁止提交未测试、不可回滚、含缓存/日志/测试资料/额外署名的改动。

验收方式：

- **正确性**：公式结果必须对齐 Attention/Napkin PDF 与 LaTeX 源码；RAG/GraphRAG 必须展示全文证据；云端模型链路必须有真实 API smoke test。
- **性能**：默认打开、滚动、缩放、翻译不得加载 OCR/MFR/离线重建/GraphRAG 重任务；长文档 Napkin 是必测门槛。
- **可维护性**：新增后端必须有统一接口、配置开关、缓存命名空间、失败降级和单元测试。
- **可审计性**：每轮工具或算法迁移必须记录 recall/precision、near-match/weak-match、unknown glyph、冷启动、P95、cache hit、日志错误。
- **版本控制**：每个阶段拆小提交；提交前后检查不包含额外署名、测试资料、缓存或临时产物。

## 必须遵守的任务边界

这些约束优先级高于单个实现点，后续继续工作时先对照本节：

- **不造轮子**：公式、RAG、GraphRAG、PDF 解析优先复用成熟工具/源码/引擎；自写代码只做编排、适配、缓存、审计和必要的结构 glue。
- **不硬编码**：不能靠样本特化正则、固定论文词表、临时启发式函数伪装公式识别。策略必须来自 PDF 结构事实、成熟库能力、配置和可审计数据。
- **不搞丑陋设计**：新能力必须可插拔、可测试、可回滚；不能把 Paddle/UniMERNet/Marker/Docling/MinerU/DeepSeek 等直接写死进 UI 或知识库热路径。
- **经常联网搜索**：遇到工具选择、模型名、API、性能边界、公式识别方案时必须查官方文档或权威源码，不凭记忆拍脑袋。
- **加强测试**：不能只跑单元测试；公式必须用 Attention/Napkin PDF 与 LaTeX 源码对照，性能必须覆盖长文档打开、滚动、缩放、问答和后台任务。
- **性能优先**：默认阅读路径不能加载重模型，不能等待全量 OCR、GraphRAG 或离线全文重建。后台任务必须限流、可暂停、可恢复、缓存优先。
- **born-digital PDF 不走 OCR**：有文本层、glyph、bbox、字体、矢量结构的非扫描 PDF，优先走 MuPDF/Poppler 等 PDF 结构解析。
- **OCR 只做补救层**：图片公式、扫描页、乱码/缺失文本层、用户显式高精度精扫、问答证据低置信时才进入 OCR/MFR。
- **公式输出必须有定界符**：行内数学用 `\(...\)`，行间公式用 `$$...$$`；数学字体符号和行内变量不能裸露进入翻译/RAG。
- **真实云端测试**：DeepSeek V4 Pro 作为分析/回答模型时，要使用现有 `config.yaml` API 配置做可选真实 smoke test，不能一直用 mock。
- **版本控制干净**：不要提交 `TODO.md`、`测试资料/`、缓存、日志、临时产物；每个阶段拆小提交。
- **提交无额外署名**：所有 git 提交信息和提交日志只保留项目作者信息，不添加自动署名。

## 当前总目标

把 PDF AI Reader 从原型升级成高性能、可验证、可长期维护的论文阅读和全文理解工具：

1. **born-digital 公式极高精度/极高速度**：先把非扫描 PDF 的公式做对，不用 OCR；用 PDF 文本层、字体、glyph bbox、矢量线、可选标签结构恢复公式。
2. **图片/扫描公式后台高精度识别**：OCR/MFR 作为后台补救层，缓存优先、预算限流、可修正、可恢复。
3. **全文 RAG / GraphRAG 真正可用**：知识库和问答必须基于全文证据、章节/定理/公式/引用关系，而不是形式上的问答框。
4. **交互性能不回退**：打开、滚动、缩放、翻译、问答入口不能被后台索引或公式识别拖垮。
5. **工具路线专业化**：可大胆迁移到更成熟工具，但必须调研、测试、版本隔离和可回滚。

## 当前任务的通俗描述

现在做的不是“把 PDF 全部 OCR 一遍”，也不是写一个靠正则猜公式的小脚本，而是把 PDF 阅读器改成“先快后准”的论文理解系统：

1. 打开 PDF 时，先用 PDF 自带的文本层、字体、字形坐标、矢量线和图片信息快速建立可读内容，让滚动、缩放、翻译、基础问答先可用。
2. 后台把每一页、每个公式候选、每个工具结果都排成任务，分 r0/r1/r2/r3/r4/r5 多轮慢慢增强；每轮结果都落库，下次打开同一输入直接跳过。
3. born-digital PDF 的公式优先靠 PDF 结构事实恢复；只有图片、扫描、乱码、缺文本层、低置信或用户显式精扫时，才调用 OCR/MFR。
4. Paddle、Pix2Text、MinerU、UniMERNet/PDF-Extract-Kit 等外部工具只作为隔离 worker 提供候选，不允许直接覆盖正文。
5. 高置信公式和章节/定理/引用关系最终要增量进入全文知识库和 GraphRAG，让问答能引用真实证据，而不是普通聊天。

今晚目标是持续运行到基础目标真正达成：公式扫描最终准确度高于 99.9%，RAG/知识系统完整可用，并继续全线优化。当前还没有完成这个目标，不能标记 goal 完成。LaTeX 源码只用于测试、审计和验收；真实用户运行路径不能假设有源码。

当前阶段的核心任务是：把 r0 非 OCR 结构快扫、r1 缓存补救、r2 多工具候选、r3 语义校对、r4 图谱、r5 知识库增量更新跑成一条可验证的流水线，并用 Attention/Napkin 证明准确率和性能。2026-05-25 已能在 `tools/formula_multiround_pipeline.py` 中验证 r0-r5 的落库、跳过、fusion 持久化、定向 r2、候选证据增强 r3、结构图谱 r4 和 accepted r5 增量写回 service；仍未完成 Napkin 大样本质量门禁、产品级 accepted/rejected/revision 门禁、GraphRAG 高质量语义抽取和最终 99.9% 公式准确率。

2026-05-25 追加实跑结论：

- 第一阶段 PDF 解析器研发已落地 r0/r0.5 最小闭环：`src/core/pdf_glyph_graph.py` 将现有 MuPDF rawdict facts 转成 `RawGlyphGraph`，包含 glyph/vector/image/font/line/span/reading edges/PDF health/input hash；`src/core/symbol_identity_repair.py` 将 raw graph enrich 成 `EnrichedGlyphGraph`，目前支持已知 PDF Unicode 保留、glyph name 静态映射、同 normalized_font+cid 锚点传播、冲突保守不修复和独立 hash。`BornDigitalFormulaStructureExtractor` 的 r0 evidence 已同时带局部 raw graph 与 enriched graph，后续 TinyBDMath 不再需要重新定义输入格式。验证：`tests/test_formula_multiround_pipeline.py tests/test_formula_knowledge_graph.py tests/test_formula_knowledge_update.py tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_graph_index_flow.py tests/test_graph_index_store.py tests/test_formula_tool_comparison.py tests/test_external_formula_tools.py tests/test_formula_detector.py tests/test_born_digital_math.py tests/test_pdf_glyph_graph.py tests/test_symbol_identity_repair.py tests/test_smoke.py -q` 为 `188 passed`。
- r1 在哪里：`FormulaIndexStore` 的 `r1_cached_recognition` queue 和 `FormulaIndexFlow` cache-first OCR/MFR worker；默认 born-digital Attention/Napkin 不触发 r1 未命中推理，只有 `needs_ocr=True` 图片/扫描/乱码/缺文本层候选才进入。
- r3 在哪里：`src/app/formula_semantic_review.py` 和 UI idle 调度；候选/fusion 证据进入 prompt，DeepSeek 或 mock 返回的 `suggested_latex/confidence/reason/risks/raw_response` 写入 `formula_round_jobs.result_json`，不覆盖正文。
- r3 最新边界：prompt 使用压缩证据包，inline 候选带 `source_context` 和 `inline_pdf_evidence`，输出自动补数学定界符但仍只作为候选；DeepSeek 非 JSON 响应会 failed 落库并保存 raw response 摘要。fusion 派生的 r3 任务已按 `semantic_review_priority` 排序：结构/本地工具证据、低相似、候选冲突、复杂 LaTeX 和风险项先审；单字符 inline 延后但不丢弃。r3 完成后保留原始 `review_candidate`、queued/review hash 和优先级原因，便于审计。
- r4 在哪里：`src/app/formula_knowledge_graph.py` 和 `src/app/graph_index_flow.py`；普通公式写 `formula` 节点，未过门禁的 fusion 候选写 `formula_candidate` 节点，只做图谱候选证据。
- 最新测试集合为 `188 passed`。Attention 前 6 页默认非 OCR pipeline：r3/r4 各处理 122 个候选；targeted r2 后发现 Pix2Text-MFR 对 born-digital 7 个样本平均 similarity 约 0.578，低于 r0 约 0.668，因此 fusion 记录 `local_precise_degraded=5` 且 `ready_for_manual_accept=0`。Attention 前 2 页 `--r3-limit 1` 小批 smoke 已证明 r3 先处理 `ht−1` 这类高价值候选，低价值单字符 `t` 保留 queued；inline evidence smoke 证明 `ht−1` 带 CMMI/CMSY/CMR 字体、字号和脚本证据进入 fusion/r3。
- 结论：多轮高性能解析框架已经跑通，质量门禁也能阻止降质候选污染正文；但“公式准确率 >99.9%”没有达成，当前任务不能完成。下一步必须提升 born-digital LaTeX 还原本身，而不是继续堆 OCR。

多工具配合的下一步必须按 `docs/formula_multitool_fusion_design.md` 推进：不手写硬编码公式解析规则，不靠样本正则修公式；自写部分只做 evidence/candidate/fusion schema、工具编排、源码准确率复核、候选排序、accepted 门禁和增量写回。

## 到大作业完成的路线

1. **公式质量闭环**
   - Attention 和 Napkin 的 PDF、LaTeX 源码、图片资源都要纳入审计。
   - LaTeX 源码中 `$...$`、`\(...\)`、`\[...\]`、`$$...$$` 包裹内容都算公式；行内公式、变量、上下标和数学字体必须单独统计和验收。
  - r0/r1/r2/r3 每轮必须能证明：入队、输入 hash、模型版本、结果 JSON、跳过机制、失败记录、低置信不覆盖正文。
   - 行内公式验收必须检查 `inline_pdf_evidence`：字体、字号、bbox、脚本字号和数学字体证据是否进入 fusion/r3。源码仍只用于验收，不能进入真实用户运行路径。
   - 外部工具必须做同样样本对比，输出准确率、弱匹配率、P95 耗时、冷启动、缓存命中和失败样例。
   - 每次验收必须检查是否造轮子/硬编码：生产公式路径不能出现样本特化词表、论文样本正则、一次性手写修复链，默认 r0 不能调用自写 LaTeX 重建器冒充高精度解析。

2. **知识库与 GraphRAG 闭环**
   - 基础 FTS/RAG 保持秒级可用。
   - accepted 高置信公式变化后，r5 要增量更新 FTS/向量索引和 GraphRAG artifact。
   - r4 要把章节、公式、定理、概念、引用关系写成可追溯图谱证据。

3. **真实交互闭环**
   - E2E 必须模拟滚动翻页、页码跳转、缩放、双击翻译、隐藏/再打开、裂缝问答、右侧全文问答、日志审计。
   - Napkin 作为长文档性能门槛，不能只用 Attention 小文件证明可用。
   - 缩放后要清晰、翻译层不丢、滚动定位不乱，后台任务不能抢 UI。

4. **产品完成与交付**
   - 清理环境和缓存路径，写清主环境与各工具环境的安装/验证方法。
   - 固定最终测试命令、演示脚本、失败边界和性能报告。
   - 提交前确认无测试资料、日志、缓存、临时 benchmark 输出和额外署名。

## 2026-05-24 新会话交接状态

新终端/新会话接手时必须先读：

1. 根目录 `AGENTS.md`
2. `docs/next_session_handoff.md`
3. `docs/async_formula_indexing_design.md`
4. 本文件
5. `TODO.md`

当前真实状态：

- 主程序环境仍是 `C:\Users\WYK\.conda\envs\pdf_ai_reader_314`。
- 当前隔离工具环境存在：`pdf_tool_paddle310`、`pdf_tool_mineru310`、`pdf_tool_pix2text310`、`pdf_tool_magic310`、`pdf_tool_pek310`。
- 不要动用户已有环境，不要在主环境混装 MinerU/PaddleOCR/PDF-Extract-Kit/UniMERNet；工具环境只作为独立 worker 使用。
- 防休眠脚本存在，但新会话必须重新确认进程和日志。
- 公式多轮解析要求见 `docs/next_session_handoff.md` 的“多轮公式解析要求”章节，不能遗漏 r0/r1/r2/r3/r4/r5。

多轮公式解析的核心边界：

- r0：PDF 结构快扫，导入后全篇页面入队，不 OCR，不阻塞首屏。
- r1：缓存优先识别，只处理图片/扫描/needs_ocr/已有公式块，命中缓存直接跳过。
- r2：本地高精度多工具复核，独立 worker，只写候选。
- r3：DeepSeek 等分析模型语义复核，写候选 JSON，不覆盖正文。
- r4：公式/章节/定理/引用/概念关系异步写 GraphRAG。
- r5：`FormulaKnowledgeUpdateService` 已能消费 `r5_knowledge_incremental_update` 任务；只有 accepted 结果变化才按 input hash 把 `accepted_latex` 增量 upsert 到 `KnowledgeEngine`，知识库未就绪时保持 queued。仍缺产品级 accepted/rejected/revision UI 和 GraphRAG 同步更新。

最新实现状态：

- 已提交代码到 `6cb0860 Add formula multiround pipeline runner`，后续继续补齐了候选融合、facts-only r0、行内公式指标、r4 公式图谱和反硬编码测试。
- r0 页面扫描已经改为 born-digital PDF 结构快扫：只用 MuPDF/现有审计事实写候选，不初始化 OCR/MFR，不默认调用自写 LaTeX 重建器。
- `formula_recognition_results` 已保存每个模型/轮次/输入 hash 的公式候选，accepted 唯一性已测试。
- r2 已有外部多工具候选 worker，当前 Paddle Formula 和 Pix2Text 单图 smoke 能返回候选；所有 r2 结果默认不覆盖正文。
- `tools/formula_multiround_pipeline.py` 已把 r0/r1/r2/r3/r4 串成可审计命令行流水线；默认 born-digital 路径不 OCR，显式 `--r2-sample-formulas` 才抽样送 r2 多工具精扫，`--reuse-db` 可证明已完成输入跳过。
- Attention 前 6 页验证：facts-only 默认 r0 写 7 个结构公式候选，r1/r2 正确跳过；复用 DB 后 r0 `processed_pages=0`、`skipped_completed_pages=6`；显式 r2 单样本调用 `pix2text-mfr`、Paddle Formula、Pix2Text，首轮约 245s、复用约 1.2s；真实 DeepSeek r3 单条约 60s，只写候选 JSON。
- 源 LaTeX 准确率复核已接入多轮报告：facts-only r0/parsed blocks 平均 best similarity 约 0.668、near match rate 0.429；行内候选接入后 Attention 前 6 页 `pdf_inline_formula_snippets=115`，`inline_source_weak_match_rate=0.299`，`inline_source_unmatched_count=54`，明显好于此前 0.026/75，但仍远未达标；显式 r2 单样本最佳平均 similarity 约 0.854，证明有提升但还远未达到极高准确率或 exact-match accepted 门槛。
- `formula_fusion` 已按 bbox/candidate_id 合并 parsed/r0/r2/r3/inline 候选，输出 per-candidate 排名、accepted gate、持久化 input hash 和定向 r2/r3/r5 派生统计；当前 Attention 前 6 页 34 个候选区域 0 个 ready，全部 `needs_more_evidence`，其中结构/display 低证据 r2 队列 6 个，纯 inline 只进入候选审计/r3 复核，不默认 OCR。
- MinerU 3.1.15 本地新模型已跑通 Attention 单页离线解析；PEK/UniMERNet 未跑通，旧 magic-pdf 缺权重。
- 此前相关测试：`tests/test_formula_multiround_pipeline.py tests/test_formula_knowledge_update.py tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_graph_index_flow.py tests/test_graph_index_store.py tests/test_formula_tool_comparison.py tests/test_external_formula_tools.py tests/test_formula_detector.py tests/test_born_digital_math.py tests/test_smoke.py` 为 157 passed；2026-05-25 本轮扩展后对应集合为 174 passed。

## 今晚执行边界

优先顺序：

1. 保持机器不休眠，长任务不中断。
2. 固化目标、约束、工具路线和质量门禁文档。
3. 撤回正则/硬编码公式重构实验，不让它进入主链路。
4. 继续调研并实现 MuPDF-backed born-digital 结构抽取审计，而不是直接写“猜 LaTeX”的解析器。
5. 加强 Attention/Napkin 源 LaTeX 对照测试和性能测试。
6. 继续推进 RAG/GraphRAG：全文证据、结构化章节/定理/公式关系、DeepSeek V4 Pro 真实问答链路不能被公式任务挤掉。
7. 在 born-digital 公式路线稳定后，再继续推进后台 OCR 修正轮、RAG/GraphRAG worker。

明确不做：

- 不用 OCR 处理可解析文本层公式。
- 不为了短期输出堆正则。
- 不安装庞大依赖作为默认运行路径。
- 不在 UI 线程或默认打开流程加载 OCR/MFR/GraphRAG 重任务。
- 不提交未经测试、无法回滚、污染版本库的改动。

## 今晚任务队列

1. **防休眠与长任务保障**
   - 维持电源策略、keep-awake worker、watchdog 和登录自启动。
   - 定期检查后台进程仍在运行。

2. **born-digital 公式结构解析**
   - 先做 MuPDF 事实层：glyph、font、bbox、span、line、image、vector、unknown glyph。
   - 加 Poppler 对照审计入口。
   - 不写正则公式重构，不把低置信结果并入主链路。

3. **公式质量与性能测试**
   - Attention/Napkin 与 LaTeX 源码对齐审计。
   - 记录 unknown glyph、字体映射失败、二维结构缺口、耗时。
   - 验证默认阅读路径不加载 OCR。

4. **RAG / GraphRAG 升级**
   - 保留基础全文 RAG 先可用的原则。
   - 推进章节、概念、定理、公式、引用关系的异步图谱 worker 设计。
   - DeepSeek V4 Pro 作为分析/回答模型，使用现有 `config.yaml` 做可选真实云端 smoke test。
   - 不把 GraphRAG 放进打开/滚动/缩放热路径。

5. **后台 OCR 修正轮**
   - 只处理图片/扫描/乱码/低置信区域。
   - 继续保持缓存优先、预算限流、可暂停、可恢复。

6. **版本控制**
   - 文档和每个可验证代码阶段分开提交。
   - 提交前后检查无额外署名、无测试资料、无缓存、无临时产物。

## 防休眠状态

当前采取三层防护：

- 电源策略：当前电源计划已把睡眠、休眠、关闭显示、关闭硬盘的 AC/DC 超时设为 0。
- 保活 worker：`tools/keep_awake.ps1` 后台运行，周期性调用 `SetThreadExecutionState`，并发送 F15。
- watchdog：`tools/keep_awake_watchdog.ps1` 后台运行，周期性重写电源超时、声明系统/显示活跃，并在 worker 退出时重启 worker。
- 登录自启动：当前用户 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` 已写入 keep-awake 和 watchdog 启动项。

边界：

- 这能防普通空闲睡眠、显示关闭、硬盘空闲关闭，以及保活 worker 意外退出。
- 不能防断电、电池耗尽、系统更新重启、用户手动睡眠/关机、公司/系统策略强制锁定。
- Windows 计划任务注册因当前权限不足失败，已改用当前用户 Run 注册表项。

## 我们现在在做什么

当前项目不是单纯补一个功能，而是在把 PDF AI Reader 从原型推进到可验证的论文阅读工具。

核心目标有四个：

1. **公式识别更准**
   - PDF 中图片格式/扫描版公式要尽可能识别为 LaTeX。
   - 已有文本公式但不是 LaTeX 的块，要能重识别和纠错。

2. **性能不能牺牲**
   - 打开 PDF、滚动、缩放、翻译不能被 OCR 或索引任务拖慢。
   - 长文档如 Napkin 必须作为性能门槛。

3. **知识库/问答要真正基于全文**
   - 不是 UI 上有个问答框，而是检索全文证据、展示依据、按证据回答、支持追问。
   - 后续要支持更强 RAG / GraphRAG，而不是长期手写简单向量检索。

4. **版本控制和可回滚**
   - 每一阶段单独提交，能独立测试、独立回滚。
   - 所有提交信息不得出现额外自动署名。

## 已完成的检查点

### 设计边界与保活

已完成：

- 明确写入设计哲学：事实优先、工具优先、证据优先、性能优先、可替换优先、审计优先。
- 明确写入设计边界：不造轮子、不硬编码、不用 OCR 处理 born-digital 公式、不把重任务放热路径。
- 明确写入今晚任务队列：公式结构解析、RAG/GraphRAG、真实云端 smoke test、后台 OCR 修正轮、版本控制。
- 启动 keep-awake worker 与 watchdog，并把睡眠、休眠、显示关闭、硬盘关闭超时设为 0。

### Born-Digital 公式事实层

已完成：

- 新增 `MuPDFBornDigitalExtractor`，只抽取 PDF 结构事实：glyph、font、bbox、line、span、image、vector、unknown glyph。
- 新增 `tools/born_digital_math_audit.py`，支持 MuPDF rawdict 审计和 Poppler `pdftotext -bbox-layout` 对照。
- 新增 `DisplayFormulaRegion` / `DisplayFormulaSegmenter`，在不 OCR、不猜 LaTeX 的前提下，把显示公式的多行/多块 PDF 结构合成候选区域。
- 审计工具新增 display region 和 display-only LaTeX 源码对齐指标。
- 不生成 LaTeX、不猜公式、不接入主解析热路径，避免硬编码实验污染生产链路。

当前验证：

- `tests/test_born_digital_math.py` 13 项通过，覆盖 glyph/vector 抽取、数学证据、显示公式分割、脚注误报、正文宽度内联数学误报。
- 全量 `pytest -q`：105 passed，2 skipped。
- Attention 第 3-4 页 MuPDF rawdict 审计：约 0.206s，5602 glyph，9 vector，2 image，unknown glyph 为 0。
- Attention 第 3 页 Poppler 对照：MuPDF 约 0.161s，Poppler `-bbox-layout` 约 0.282s。
- 后续新增结构证据审计：evidence region、cluster、context cluster，并接入 LaTeX 源码匹配指标。
- Attention 第 3-4 页 cluster 能出现少量源码弱匹配；context cluster 能匹配部分 FFN/MultiHead 片段，但会误吸正文，因此只保留为审计上限，不进入主链路。
- Attention 前 6 页 display region 审计：约 0.593s，17816 glyph，display region 7 个，unknown glyph 为 0；`Attention`、`FFN`、`MultiHead`、`PE` 样例可整体对齐源码。
- Napkin 第 60-79 页 display region 审计：约 0.874s，30974 glyph，display region 31 个，unknown glyph 为 0。
- `DocumentChunker(enable_born_digital_math=True)` 已可选追加 display formula 块，并把重叠原段落标记为 `shadowed_by=born_digital_display_formula`；知识库索引跳过 shadowed 段落，避免重复证据。
- Attention 全量纯 born-digital display 审计：约 2.189s，公式块 11 个，`source_weak_match_rate=0.069`，`low_similarity_pdf_rate=0.455`。
- Napkin 前 120 页纯 born-digital display 审计：约 17.823s，公式块 116 个，`source_weak_match_rate=0.037`，`low_similarity_pdf_rate=0.250`。
- `PdfFormulaSemanticReconstructor` v1 已可选恢复上下标、局部分式线、根号和 Unicode 数学符号；Attention 公式 1 可恢复 `\frac{Q K^{T}}{\sqrt{d_{k}}}` 结构。
- Attention 全量 semantic v1：约 2.276s，`common_source_command_recall=0.353`，`source_weak_match_rate=0.069`，`low_similarity_pdf_rate=0.455`。
- Napkin 前 120 页 semantic v1：约 21.017s，`common_source_command_recall=0.019`，`source_weak_match_rate=0.035`，`low_similarity_pdf_rate=0.319`。
- 新增 born-digital display region 诊断层：记录 `formula_candidate/review` 分类、`prose_like_region`、`tabular_alignment`、`table_or_text_like_region` 等风险，不提升置信、不生成 LaTeX，只为默认策略和审计提供可量化过滤依据。
- 最新诊断审计：Attention 全量约 2.254s，11 个候选中 10 个 `formula_candidate`、1 个 `review`；Napkin 前 120 页约 21.002s，116 个候选中 101 个 `formula_candidate`、15 个 `review`。
- 公式 LaTeX 审计新增 display / inline / all 匹配范围。E2E 的 born-digital display 门禁现在只对齐源码 display 公式，行内公式后续单独验收；Attention display-scope 最新结果约 2.204s，`source_weak_match_rate=0.625`、`low_similarity_pdf_rate=0.545` 已过对应门槛，但 `common_source_command_recall=0.333` 仍未过 0.35，因此公式质量仍按失败处理。
- 撤回未提交的轻量 LaTeX 宏解析和额外 glyph 规则实验：这类代码只能作为审计原型，不进入主线。后续若要处理源码宏、公式 AST 或二维结构恢复，必须优先复用成熟解析库/源码，并先用 Attention/Napkin 源码对照和性能门槛证明收益。
- 下一步必须做 region 级类型判别、表格/列表过滤、矩阵/对齐环境建模和 LaTeX 源码页级对齐；当前可选入口不能默认进入阅读热路径。

### 全文问答与证据

已完成：

- 右侧全文问答入口。
- 证据树展示全文检索依据。
- 问答完成后生成追问建议。
- evidence 中包含 `retrieval_score / lexical_score / vector_score`。
- UI 展示证据编号、页码、块类型、相关度、词面/向量分数和片段，不暴露原始向量 distance。
- `QAService` prompt 已把检索片段组织成 `[S1]`、`[S2]` 证据，并要求回答中引用证据编号，不能引用未提供来源。
- QA 线程流式回答失败时会自动用同一 QAService 做一次非流式重试，避免 UI 只显示底层 streaming 异常。
- 已用当前 `config.yaml` 的 DeepSeek provider key 做真实云端 smoke：DeepSeek V4 Pro 同步生成、QAService 回答和流式生成均通过。

### 检索质量

已完成：

- 知识库检索不再只取 `top_k` 向量结果。
- 先扩大候选池，再按向量距离、关键词覆盖、章节/摘要/关键词元数据混合重排。
- 对 HashingEmbedding 兜底场景更稳定。

### 知识库构建性能

已完成：

- Chroma collection metadata 中写入基础块索引指纹、基础块数和 schema 版本。
- 手动“重建知识库”会先比较当前 `DocumentBlock` 指纹。
- 指纹一致时跳过删除 collection、重新 embedding 和批量 upsert。
- 允许后台公式 OCR 增量块存在于 collection 中，不因 collection 总块数大于基础块数而误判失配。
- 新增 `sqlite_fts` 轻量全文索引后端，不引入新环境或新模型，作为长文档快速召回基线。
- 运行时如果实际 embedding 只是 `HashingEmbeddingClient` 兜底，会自动把知识库后端从 `legacy_chroma` 切到 `sqlite_fts`；真实 embedding 可用时仍保留配置后端。
- `sqlite_fts` fallback 不再初始化 `ChromaRepo`，减少没有真实 embedding 时的知识库启动开销。
- Ollama 不可达时先做 120ms TCP 快速探测并短期缓存；FTS fallback 路径也不再 import Chroma。当前实测：AI 引擎创建约 0.15s，FTS fallback 知识库引擎约 0.012s。

### GraphRAG 管线

已完成：

- 新增 `GraphIndexFlow`，在 `rag.enable_graph_index=true` 时由 `DocumentFlow` 异步调度；默认关闭，不影响导入、滚动、缩放、翻译和基础问答热路径。
- 默认 `structural_v1` extractor 只抽取已有 PDF/DocumentBlock 事实：document、page、block、section、formula、theorem 节点及 contains / in_section / expresses_formula 等证据边。
- 图谱 artifact 继续写入 `GraphIndexStore`，支持内容 hash、跳过未变化块、失败记录和后续恢复。
- 当前版本不使用临时概念词表或样本特化正则，不把结构抽取伪装成最终 GraphRAG；下一步接 DeepSeek V4 Pro 或 LlamaIndex PropertyGraph 作为可选高阶抽取后端。

关键性能结论：

- Attention 第二次重建：日志构建耗时 0.0s，最新 E2E 等待约 0.29s。
- Napkin 第二次重建：日志构建耗时约 0.2s，最新 E2E 等待约 0.26s。
- 旧向量后端首次写入长文档仍然慢；最新 Napkin E2E 中，非跳过的 Chroma 首次构建约 108.5s。
- SQLite FTS5 基准：Attention 全量 488 blocks 构建 0.030s、检索 0.0346s；Napkin 120 页 1831 blocks 构建 0.080s、检索 0.0422s；Napkin 全量 21687 blocks 构建 0.835s、检索 0.1028s。
- 结论：默认阅读路径应优先拥有一个秒级全文索引；高质量语义检索、rerank 和 GraphRAG 在此基础上异步叠加。
- 在没有真实语义 embedding 时，FTS5 已成为默认实际后端，避免旧 Chroma + 哈希向量拖慢长文档首次索引。

### RAG / GraphRAG 迁移

已完成：

- 新增 `rag` 配置段。
- 拆分 `cloud_translation` 和 `cloud_reasoning`。
- 修复 DeepSeek reasoning 模型名：配置层使用 LiteLLM 兼容的 `deepseek/deepseek-v4-pro`，
  旧的 `deepseek-v4-pro` 会在启动时自动规范化。
- 云端 key 查找支持同一 DeepSeek provider family 复用，`config.yaml` 里已有的
  `deepseek/deepseek-v4-flash` key 可用于 reasoning 模型。
- 新增可选真实云端 smoke test，设置 `PDF_AI_READER_RUN_CLOUD_TESTS=1` 时会使用当前
  `config.yaml` 调用 DeepSeek reasoning 模型验证问答链路。
- 本轮已真实运行云端 smoke test：`tests/test_cloud_models.py` 在 `PDF_AI_READER_RUN_CLOUD_TESTS=1`
  下 2 项通过，确认当前配置可调用 DeepSeek reasoning 模型。
- 写入迁移方案文档：[rag_graphrag_migration_plan.md](rag_graphrag_migration_plan.md)。
- 抽出 `KnowledgeIndexBackend`。
- 保留 `legacy_chroma` 默认后端。
- 新增版本隔离的 `llamaindex_chroma` 后端。
- 新增版本隔离的 `sqlite_fts` 后端，数据写入 `data/knowledge_bases_fts`。
- 安装并登记 LlamaIndex 依赖。
- 新增 `GraphIndexStore`，用 SQLite 持久化 GraphRAG block-level 抽取任务和 artifact。
- GraphRAG 任务层仅记录任务状态与抽取结果，不调用模型、不接 UI 热路径；后续 worker 可接
  LlamaIndex PropertyGraph / Neo4j / DeepSeek V4 Pro 等成熟后端。
- 图谱任务数据库写入 `data/graph_index_jobs.db`，已加入 `.gitignore`。

关键性能结论：

- LlamaIndex 默认 `ChromaVectorStore.add(TextNode)` 写入路径很慢，不能放入热路径。
- 优化后，写入/检索热路径改回 Chroma 原生批量 upsert/query，LlamaIndex 保留为 schema/GraphRAG 编排层。
- 5000 块合成基准：Chroma 写入 batch 从 50 提到 512 后，约 16.2s 降到 9.9s。
- Napkin E2E：无变化手动重建已降到约 0.26s，日志 ERROR/WARNING/CRITICAL 为 0。
- SQLite FTS5 不是最终语义理解层，但它证明全文索引本身不应成为长文档交互瓶颈。

### 公式 OCR / MFD 性能

已完成：

- `MathOCR` 新增公式图片 hash 缓存。
- 缓存命中时不检查 Pix2Text 可用性、不加载模型、不推理。
- 单元测试验证缓存命中不会触发模型加载。
- `MathOCR.recognize_batch(..., max_uncached=N)` 支持缓存优先和未命中推理预算。
- 默认交互式 PDF 解析把 MFD 页扫描预算设为 `max_mfd_pages=0`，打开文档、滚动、缩放、翻译不再等待重型页面公式检测。
- 显式精扫场景可以传入 `max_mfd_pages>0`，候选页已按图片、已有公式块、LaTeX/数学符号密度排序。
- 扫描版/图片公式的 MFR 已按页、置信度、面积做优先级排序；默认只走缓存回填，不对未命中图片即时推理。
- 未进入预算或识别失败的公式仍写入 `DocumentBlock`，标记 `needs_ocr=True`，为后台公式索引继续补扫保留稳定位置。
- Pix2Text 当前是正式默认的图片/扫描公式 MFD/MFR 后端。它不应被误解为不能用；限制只在于不能把它同步放进打开、滚动、缩放、翻译热路径，也不能用 OCR 替代 born-digital PDF 的结构解析。
- 对 born-digital PDF：优先 MuPDF/Poppler glyph、font、bbox、vector 事实层；Pix2Text 只在文本层缺失、图片公式、扫描页、用户主动精扫或后台补救时运行。
- `MathOCR.recognize_batch()` 对同批未命中图片按 image hash 去重，MFR 预算按唯一图片计，重复公式图复用同一次识别结果。
- 新增 `FormulaIndexFlow`，对 `needs_ocr=True` 的公式块做后台预算式 OCR。
- 新增 `FormulaIndexStore`，使用 SQLite 持久化 `doc_hash/block_id/page/bbox/priority/status/latex/image_hash/model/error/attempts`，支持 queued / running / done / failed / skipped 状态。
- `FormulaIndexStore` 同时持久化页面级 MFD 任务，导入 PDF 时把全文页码入队，用于发现图片/扫描版公式。
- 新增 `FormulaIndexScheduler` / `FormulaScanPolicy`，把视口页、全文问答 evidence 页、用户触发页转换为统一扫描计划。
- 默认阅读路径使用 cache-only 小批量扫描；只有显式高精度计划才允许 MFR 加载模型推理。
- 工具菜单和工具栏新增“公式精扫”入口，用户主动触发时只高精度扫描当前视口附近公式，避免误触发长文档全篇推理。
- 打开文档后立即持久化全文公式任务：已有公式块进入 OCR 队列，所有页进入页面级 MFD 队列；真正识别在后台空闲小批量执行。
- 页面级 MFD 后台批次会把新发现的图片/扫描公式写成 `DocumentBlock`，标记 `needs_ocr=True`，再进入公式 OCR 队列。
- 后台 background 扫描不连续 drain，只跑一小批并等待下一轮空闲定时器；显式当前视口高精度扫描才允许连续处理当前范围。
- 公式索引任务数据库写入 `data/formula_index_jobs.db`，已加入 `.gitignore`，不会进入版本库。
- 公式后台识别成功后会刷新页面 block，并通过 `KnowledgeEngine.upsert_blocks()` 增量写回知识库。
- 如果公式识别早于基础知识库构建完成，主窗口会暂存增量块，等 `build_finished` 后统一写入，避免竞态导致全文问答漏掉公式。
- `FormulaIndexStore` 已扩展为多轮公式任务存储：`scan_round` 区分 `r0_pdf_structure`、`r1_cached_recognition`、`r2_local_high_precision`、`r3_cloud_semantic_review`、`r4_knowledge_graph`、`r5_knowledge_incremental_update`。
- 新增 `formula_round_jobs` 统一记录非 OCR 轮次和跨轮状态，主键为 `doc_hash + scan_round + target_type + target_id`。
- 新增 `formula_recognition_results` 统一保存结构解析、本地工具和云端语义修正候选；同一候选的 accepted 结果已保证唯一。
- 导入时所有页进入 `r0_pdf_structure`，`needs_ocr=True` 公式进入 `r1_cached_recognition`，所有已解析公式进入 `r3_cloud_semantic_review` 复核记录。
- r0 页面 worker 当前只执行 born-digital 结构快扫并写 `pdf_structure` 候选，不初始化 MFD/OCR。
- r0 低置信结构候选会排入 `r2_local_high_precision` 待复核 round，但不会默认启动重模型，也不会覆盖正文。
- r2 本地高精度轮当前通过外部 JSON worker 接入 Paddle Formula 和 Pix2Text 候选，后续扩展 MinerU/PEK/UniMERNet。
- 新增 `FormulaSemanticReviewService`，用于消费 r3 队列并把云端语义修正写成候选 JSON；它不会覆盖原始公式块，也不会自动 accepted。
- 新增 `FormulaSemanticReviewFlow`，用于在 UI 空闲时通过后台 QThread 小批量消费 r3 队列，避免导入、滚动、缩放等待云端复核。
- 显式高精度扫描使用 `r2_local_high_precision`，不会因为 `r1_cached_recognition` 已完成而被跳过。
- 新增 `tools/formula_index_performance.py`，用于 Attention/Napkin 导入热路径性能检测，不加载 OCR/MFR 模型。

## 异步公式索引与知识图谱规划

详尽的多轮异步扫描、持久化表、缓存 key、修正轮、DeepSeek 语义校对和
C++17 加速边界见：[async_formula_indexing_design.md](async_formula_indexing_design.md)。

这个过程分成三条可独立回滚的后台流水线，不能混成一个同步任务。

当前已经落地的是第一条和第二条的基础闭环：阅读路径保持轻量，全文 RAG 基础索引可快速可用，公式 OCR 与页面 MFD 任务已经在导入时持久化，视口/evidence/用户触发页已有统一调度策略，识别结果能增量 upsert 回知识库。还没有完成的是全篇高精度确认流、量化公式精度审计和 GraphRAG 图谱 worker。

```text
PDF 打开/滚动/缩放
  -> 快速块解析
  -> 渲染缓存/页面块缓存

全文 RAG 索引
  -> DocumentBlock 基础文本和已知公式
  -> 先让全文问答可用

公式索引 + GraphRAG
  -> 导入时全文页码进入 MFD 队列
  -> 导入时已有公式候选进入 MFR 队列
  -> 后台小批量 MFD 发现图片/扫描公式
  -> OCR 缓存命中立即回填
  -> 未命中按优先级批量 MFR/高精度后端
  -> 公式结果增量 upsert 到知识库
  -> 抽取章节/概念/定理/公式/引用关系
  -> 形成可选知识图谱索引
```

优先级顺序：

1. 当前视口页和用户双击解释的公式。
2. 全文问答 evidence 涉及的页面。
3. 目录、标题、定理、证明附近的公式。
4. 后台空闲时按页扫描剩余候选。

关键约束：

- 首屏阅读、滚动、缩放永远不等待 MFR 或 GraphRAG。
- 基础知识库构建不等待全量公式 OCR。
- 后台公式识别必须全部写缓存，二次打开不重复推理。
- 每一轮修正必须写入持久存储：任务状态、输入 hash、模型版本、输出、置信度、耗时、错误和 accepted 状态都不能只存在内存里。
- 本地模型、整页解析和云端修正必须分轮写回；DeepSeek 语义修正只能作为候选或通过门禁后的 accepted 修订，不能覆盖原始证据。
- GraphRAG 只作为增强索引，失败不能影响基础全文问答。

## 2026-05-24 多轮公式入队与性能检测

新增代码与测试：

- `src/app/formula_index_store.py`：多轮任务字段、`formula_round_jobs`、轮次统计和查询。
- `src/app/formula_index_flow.py`：导入/后台/高精度任务带轮次，新增语义复核轮入队。
- `src/app/formula_index_scheduler.py`：普通后台/视口/evidence 为 `r1_cached_recognition`，显式高精度为 `r2_local_high_precision`。
- `src/ui/main_window.py`：导入后额外把所有公式块排入 `r3_cloud_semantic_review`。
- `src/app/formula_semantic_review.py`：r3 语义复核服务和后台 flow，按批读取已持久化任务，调用分析模型后只写候选结果。
- `src/ui/main_window.py`：空闲调度已纳入 r3 pending 计数；后台顺序为 OCR/缓存任务优先、r3 小批量语义复核、页面级检测兜底。
- `tools/formula_index_performance.py`：真实 Attention/Napkin 资料的轻量性能基准。
- `tools/formula_tool_comparison.py`：同一批公式图的外部工具候选对比，计算源码相似度并把 r2 候选写入 `formula_recognition_results`；`--auto-local-tools` 可显式发现 Paddle/Pix2Text 隔离环境。
- `tests/test_formula_index_flow.py`、`tests/test_formula_index_scheduler.py`、`tests/test_formula_index_performance.py`：覆盖多轮不互相覆盖、导入轮次统计、性能报告字段。
- `tests/test_external_formula_tools.py`、`tests/test_formula_tool_comparison.py`：覆盖外部公式工具 JSON runner、失败候选、环境变量配置、多工具候选落库和无工具跳过路径。

验证：

- `pytest tests/test_formula_index_flow.py tests/test_formula_index_scheduler.py tests/test_formula_index_performance.py -q`：27 passed。
- Attention 前 8 页：136 blocks，10 formula blocks，结构解析 1.3314s，持久化 0.0036s；`r0_pdf_structure:queued=8`、`r3_cloud_semantic_review:queued=10`。
- Napkin 前 12 页：126 blocks，2 formula blocks，结构解析 0.9658s，持久化 0.0037s；`r0_pdf_structure:queued=12`、`r3_cloud_semantic_review:queued=2`。
- 最新 `tools/formula_index_performance.py --case all`：Attention 15 页总 2.1970s、持久化 0.0046s；Napkin 前 16 页总 1.2997s、持久化 0.0306s。
- r3 复核测试：`pytest tests/test_formula_semantic_review.py tests/test_formula_index_flow.py -q` 为 28 passed，包含真实 QThread smoke。
- 旧组合测试记录：`pytest tests/test_external_formula_tools.py tests/test_formula_index_flow.py tests/test_born_digital_math.py tests/test_formula_semantic_review.py tests/test_smoke.py -q` 曾为 66 passed；当前应优先使用上方包含多轮流水线和 GraphRAG 的 65 passed 基线。

仍未完成：

- `r3_cloud_semantic_review` 已有独立服务消费、后台 flow、UI 空闲调度和候选写回，但还没有真实 DeepSeek smoke test、accepted 修订门禁和人工/自动验收策略。
- `r2_local_high_precision` 已有轮次隔离和 Paddle/Pix2Text 候选 worker，但还没有接 UniMERNet/PDF-Extract-Kit，对 MinerU 也还没有接入统一候选协议。
- 性能基准是限页热路径，不等同于完整 Napkin 1050 页闭环。
- 公式准确率仍未达到目标，需要继续做 born-digital 源码对齐、高精度候选生成和多模型对照。

## 为什么不能直接“知识库构建时全量扫描公式”

可以在知识库构建期间启动公式扫描。现在采用的是导入即排队：基础知识库先构建，全文页面 MFD 和公式 OCR 任务同时进入持久队列；但高精度 OCR 不同步塞进知识库构建主流程。

原因：

- MFD 负责找公式 bbox，较快。
- MFR 负责把图片公式转 LaTeX，最慢。
- Napkin 这种 1000 页文档如果全量同步 MFR，会显著拖慢首屏、知识库构建和问答可用时间。

正确做法是拆成两条索引：

```text
文本/结构知识库
  -> 快速完成
  -> 让全文问答先可用

公式索引
  -> 导入时全文页码/公式候选入队
  -> 后台异步小批次执行
  -> 命中缓存立即返回
  -> 未命中按优先级批量 OCR
  -> 增量更新知识库和 UI

知识图谱索引
  -> 读取章节/概念/定理/公式/引用
  -> 异步抽取关系
  -> 增量服务 GraphRAG
```

也就是说，“构建知识库时完成全部扫描”现在被拆成“导入时完成全文扫描任务排队 + 后台分批执行 + 错误修正轮”。基础全文索引先完成，公式扫描任务按优先级运行；每识别一批公式，就增量写回知识库和未来图谱索引。这样既能越扫越准，又不会让用户为了打开文档等完整 OCR。

## 公式识别目标方案

### 第一层：快速路径

打开 PDF 时只做：

- PyMuPDF 文本解析。
- 轻量公式启发式识别。
- 已有缓存结果回填。

目标：

- 首屏、滚动、缩放不变慢。
- 不因 OCR 模型加载阻塞 UI。

### 第二层：按需精扫

优先扫描：

- 当前视口页。
- 用户点击解释的公式块。
- 问答证据中引用的页面。
- 标题、定理、证明附近的公式。

目标：

- 用户正在看的内容优先精准。
- 不先扫用户可能永远看不到的页面。

### 第三层：后台批处理

后台处理：

- 空闲时继续扫描剩余页面。
- 长文档按页优先级队列运行。
- 结果写入公式 OCR 缓存。
- 已识别公式增量更新 `DocumentBlock` 和知识库索引。

目标：

- 文档越用越完整。
- 二次打开速度显著变快。

### 第四层：高精度增强引擎

候选方案：

- Pix2Text：继续作为本地默认 MFD/MFR。
- PaddleOCR PP-FormulaNet / PP-FormulaNet_plus：评估为本地高性能公式识别后端，优先测试 plus-S 的速度/精度平衡，再测试 plus-M/L 的高精度档。
- UniMERNet：作为复杂真实公式的高精度备选，适合低置信度修正轮。
- MinerU / Marker / Nougat：评估科学 PDF 到结构化 Markdown/LaTeX 的整体提取能力，不直接放入交互式逐公式热路径。
- Mathpix：作为可选云端高精度公式 OCR，不默认启用，适合用户明确选择“高精度模式”。

原则：

- 本地模型优先保证隐私和成本。
- 云端高精度只作为可配置增强。
- 所有结果必须缓存。

## 公式 OCR 性能与工具迁移策略

在不损失精度的前提下，大幅提升公式 OCR 性能主要靠管线优化，而不是简单降低模型大小。

详细设计见：[formula_ocr_performance_design.md](formula_ocr_performance_design.md)。

已落地的无损优化：

- MFD 与 MFR 解耦：页面级 MFD 先找 bbox，MFR 只处理需要 LaTeX 的裁剪图。
- 导入时全篇排队，后台按页小批量执行，避免打开 PDF 时同步加载重模型。
- MFR 图片 hash 缓存：同一公式二次打开不再推理。
- MFR `max_uncached` 预算：默认交互路径只回填缓存，不让未命中图片抢 CPU。
- 后台 background 不连续 drain，防止长文档持续占满 CPU。
- Pix2Text MFD 检测器进程内复用，避免每个后台小批次重复初始化检测模型。
- MFD 结果做同页重叠框去重，新增图片/扫描公式保留各自 bbox，避免重复占位块污染 OCR 队列。

当前公式审计基线：

- Attention no-MFD：源码公式 180 个，PDF 公式块 23 个，源码常见命令恢复率 0，`source_near_match_rate=0.020`，`source_weak_match_rate=0.049`，平均相似度约 0.226。
- Attention no-MFD 当前会触发公式质量门禁失败，原因是 LaTeX 命令恢复率和弱匹配率都远低于最低阈值。
- Attention MFD 第 3-5 页：新增 2 个图片/扫描公式候选，均标记 `needs_ocr=True`，当前默认缓存优先路径没有得到 LaTeX；重复 bbox 写入问题已修复。
- Napkin 前 120 页 no-MFD：源码公式约 30929 个，PDF 公式块 726 个，源码常见命令恢复率 0，`source_near_match_rate=0.012`，`source_weak_match_rate=0.033`，说明现有抽取把大量数学文本保留为普通排版文本而不是 LaTeX。
- Napkin 审计瓶颈在源码公式与 PDF 公式相似度匹配，不在 PDF 解析；已用 token 倒排索引和候选数限制把前 80 页匹配从约 73s 降到约 29s。后续如果要全篇反复对比模型，可把这一段下沉到 C++17/pybind11。
- `tools/formula_latex_audit.py --quality-gate` 已加入硬门禁，默认要求 `common_source_command_recall >= 0.35`、`source_weak_match_rate >= 0.35`、`low_similarity_pdf_rate <= 0.60`。当前 baseline 应失败，后续接入 Paddle/UniMERNet/更好文本公式恢复后必须用该门禁证明提升。

可用 C++17 / Python C API 加速的边界：

- 适合下沉：页面 bbox overlap 匹配、公式裁剪前的图像预处理、LaTeX 源码与 PDF 抽取公式的大规模相似度审计、批量 hash/归一化这类纯计算热点。
- 不适合下沉：Qt UI 调度、SQLite 任务状态机、RAG 后端选择、模型加载和失败降级。这些仍保留在 Python，避免版本冲突和跨线程复杂度扩散。
- 接入方式优先 `pybind11` 或稳定 C ABI 小模块；每个 native 模块必须有 Python fallback、独立基准和 Attention/Napkin 回归测试。
- 只有当 Python 侧算法和数据结构稳定后再写 C++，否则会把错误架构固化成更难维护的二进制扩展。

可插拔后端状态：

- `pix2text`: 继续作为当前默认后端，兼容已有缓存和代码路径。
- `paddle_formula`: 已接入 PaddleOCR 3.x `FormulaRecognition` 适配层。官方模块支持 `PP-FormulaNet_plus-S/M/L`、`UniMERNet` 和 `batch_size` 配置；当前配置项 `model.formula_ocr_model` 默认 `PP-FormulaNet_plus-S`，用于优先验证速度/精度平衡。
- `unimernet`: 作为复杂真实公式的高精度备选，适合放在低置信度修正轮。
- `nougat`: 适合整页科学 PDF 转 Markdown 的离线增强，不适合作为交互式逐公式 OCR 主链路。

迁移原则：

- 新后端必须实现同一个 `FormulaRecognizer` 接口，不能把 Paddle/UniMERNet 直接写死在 UI 或知识库流程里。
- `MathOCR` 已改为通过 `FormulaRecognizerRegistry` 创建后端，默认 `formula_ocr_backend=pix2text-mfr`；Paddle 后端已复用现有缓存、限流和任务队列。
- 每个后端独立缓存 key 必须包含 `image_hash/model/model_version/preprocess_version`，避免新旧模型结果冲突；`paddle_formula` 当前缓存命名空间为 `paddle_formula:{formula_ocr_model}:png-v1`。
- 默认模式必须保证 Attention/Napkin 打开、滚动、缩放性能不回退。
- 高精度模式允许更慢，但必须可暂停、可恢复、有进度、有低置信度修正队列。
- 引入新工具前必须跑 Attention/Napkin PDF 与 LaTeX 源公式的 recall/precision 审计，不以主观观感替代验收。

当前性能判断：

- 这次接入不会改变默认阅读路径；没有安装 PaddleOCR 时不会在启动时导入或加载 Paddle。
- `paddle_formula` 只在 `model.formula_ocr_backend=paddle_formula` 且显式/后台公式扫描真正触发 OCR 时加载模型。
- 不能直接宣称 Paddle 后端已经“够好”。必须先安装 PaddleOCR 后对 Attention/Napkin 跑真实 MFR、再用 `tools/formula_latex_audit.py --quality-gate` 与源 LaTeX 对齐审计。
- 若 `PP-FormulaNet_plus-S` 速度够快但复杂公式准确率不够，下一轮才比较 `PP-FormulaNet_plus-M/L` 或 UniMERNet，并把慢模型限制在低置信度修正轮。

## 下一步执行顺序

1. **提交当前持久公式任务队列改动**
   - 已通过 `pytest -q`。
   - 需要通过 Attention / Napkin E2E。
   - 提交前后继续检查无额外自动署名。

2. **完善公式扫描调度器**
   - 持久任务表已完成。
   - 导入时全文页面级 MFD 任务和已有公式 OCR 任务已经入队。
   - 视口、问答 evidence、用户触发页已经统一生成扫描计划。
   - 后台空闲小批次扫描和当前视口高精度入口已完成。
   - 下一步补全篇高精度确认流和扫描进度/暂停 UI。
   - 每批继续动态限制 MFD/MFR 数量，避免 CPU 抢占阅读和渲染。
   - 二次打开时从任务表恢复 queued / failed 任务，done 任务直接依赖 OCR cache 和知识库 metadata。

3. **完善公式结果增量写回知识库**
   - 已有 `DocumentBlock` 更新。
   - 已新增 `KnowledgeEngine.upsert_blocks()` 和后端 `upsert_blocks()`。
   - 后台公式识别结果会增量 upsert 到当前知识库后端。
   - 全文问答可引用识别后的公式。
   - 下一步要把任务表 done 状态、缓存 hash 和知识库 metadata 串起来，避免重复扫描。

4. **评估 Qdrant hybrid**
   - 目标不是“换库”，而是更快更强的 dense+sparse 检索。
   - 必须用 Attention 和 Napkin E2E 证明性能不劣化。

5. **评估高精度公式引擎**
   - 对 Attention 和 Napkin 源 LaTeX 做公式 recall/precision 审计。
   - 只引入真正提升精度且可缓存、可限流的方案。

## 验收标准

- `pytest -q` 通过。
- Attention E2E 通过，日志无 ERROR/WARNING/CRITICAL。
- Napkin E2E 通过，长文档滚动、缩放、问答正常。
- 知识库构建时间不回退。
- 默认交互式解析不加载 MFD 页检测模型。
- 公式 OCR 缓存命中时不加载模型。
- 提交信息和提交日志不出现额外自动署名。

## 2026-05-24 历史公式审计记录

### 当前边界

本节保留早些时候的公式审计判断，主要用于理解 Napkin/Attention 源码对照问题。
它不再代表当前工作树状态，也不再表示“暂停、不提交”。当前状态以上方“2026-05-24
新会话交接状态”和“最新实现状态”为准。

当前工作树状态：

- 以执行时的 `git status --short` 为准。
- `测试资料/`、日志、缓存、临时 benchmark 输出仍不应提交。
- 防休眠状态必须每次接手时重新检查。

最近已提交成果：

- `918d0e9 Canonicalize formula audit text commands`
  - 公式审计把 `\text`、`\operatorname`、`\mathrm` 归为同一类直立文本/算子命令。
  - 这是审计归一化，不是生产公式识别器，也不是样例硬凑。

当时未提交但已验证的历史成果：

- `tools/formula_latex_audit.py`
  - 新增按主 TeX 的 `\input` / `\include` 顺序展开源码。
  - 页数受限时使用 PDF outline/TOC 选择相关源码文件，避免用 Napkin 前 120 页 PDF 去对比整本书全部 LaTeX。
  - 报告新增 `source_total_tex_files`、`source_selected_tex_files`、`source_coverage`，明确记录覆盖方法、选中文件、排除文件和 TOC 映射样本。
  - 修复源码公式提取误判：排除 `\\[1ex]`、`\\[0.4cm]` 这类 LaTeX 换行间距，不再当作 `\[...\]` 行间公式。
- `tests/test_formula_detector.py`
  - 新增 `test_formula_audit_ignores_latex_line_break_spacing`。

已跑过的目标验证：

- `pytest` 目标用例：5 passed。
- `py_compile tools/formula_latex_audit.py` 通过。
- 禁用字样扫描当前改动文件无命中。
- Attention display-scope 公式审计通过：
  - `source_formula_snippets=5`
  - `pdf_formula_blocks=11`
  - `common_source_command_recall=0.667`
  - `source_weak_match_rate=1.000`
  - `average_best_similarity=0.815`
  - `low_similarity_pdf_rate=0.545`
- Napkin 前 120 页 display-scope 公式审计仍未通过，但口径已更可信：
  - 源码覆盖：`source_selected_tex_files=13 / source_total_tex_files=112`
  - `source_formula_snippets=122`
  - `pdf_formula_blocks=116`
  - `common_source_command_recall=0.128`
  - `source_weak_match_rate=0.438`
  - `average_best_similarity=0.544`
  - `low_similarity_pdf_rate=0.336`
  - 当前失败项只剩 `common_source_command_recall 0.128 < 0.350`

### 当前问题判断

1. Napkin 的旧失败指标被“审计口径不公平”严重放大过。
   之前把前 120 页 PDF 与整本 LaTeX 源码比较，导致弱匹配率极低。现在改成 PDF outline 约束源码范围后，弱匹配率从约 `0.034` 提到 `0.438`，说明必须先保证审计基线正确，再谈识别质量。

2. Napkin 当前主要瓶颈不是 OCR，也不是交互渲染，而是 born-digital PDF glyph 到语义 LaTeX 命令的恢复。
   区域数量已经接近：前 120 页源码 display 公式 122 个，PDF 结构公式块 116 个。真正弱项是命令召回只有 `0.128`。

3. 不能用 OCR 处理 born-digital PDF 公式。
   对 Napkin 这种有文本层、字体、glyph bbox、PDF outline 的文档，继续走结构解析路线。Pix2Text/Paddle/UniMERNet 只作为图片/扫描公式或低置信补救层。

4. 当前缺失命令主要来自语义归一化和二维结构恢复。
   高频缺失包括 `\ZZ`、`\QQ`、`\RR`、`\phi`、`\to`、`\mapsto`、`\longmapsto`、`\star`、`\cong`、`\le`、`\mid`、`\lvert`、`\rvert`、`\defeq`、`\dots`、`\left`、`\right`、`\pmod`、`\inv` 等。PDF 侧已经能看到对应视觉符号或字体线索，但还没有稳定恢复为源码命令。

5. 当前 PDF 公式重建仍有明显结构问题。
   典型问题包括表格/矩阵/多行对齐误合并、正文数学句子误入 display 公式、罗马单词被过度包装为 `\mathrm{...}`、黑板粗体字体如 `MSBM10` 未映射到 `\mathbb{}`、希腊字母和关系符号映射不足。

6. RAG/QA 仍未达到目标。
   E2E 中问答链路可以走通，但仍存在“云端模型不可用时降级”的情况；用户要求后续使用配置里的真实 API、用 DeepSeek V4 Pro 作为分析回答模型，并做真实云端 smoke test。当前不能把 QA/RAG 视为完成。

7. 性能方向不能回退。
   Napkin E2E 交互链路能完成，日志无 ERROR/WARNING/CRITICAL，缩放/渲染在当前预算内。后续所有公式、RAG、知识图谱增强都必须保持默认打开、滚动、缩放、翻译不等待重任务。

### 下一步建议顺序

1. 历史审计基线已经不是当前唯一待提交内容。恢复工作时先看上方最新状态和 `git status --short`。

2. 若提交，提交前必须再跑：
   - `python -X utf8 -m pytest tests\test_formula_detector.py::test_formula_audit_ignores_latex_line_break_spacing tests\test_formula_detector.py::test_formula_latex_audit_can_match_display_scope tests\test_formula_detector.py::test_formula_audit_quality_gate_flags_low_recall -q`
   - `python -X utf8 tools\formula_latex_audit.py --case attention --born-digital-math --born-digital-semantics --no-legacy-formula-heuristic --match-scope display --quality-gate --output test_artifacts\formula_audit_attention_display_scope.json`
   - `python -X utf8 tools\formula_latex_audit.py --case napkin --max-pages 120 --born-digital-math --born-digital-semantics --no-legacy-formula-heuristic --match-scope display --quality-gate --output test_artifacts\formula_audit_napkin_120_toc_scope.json`
   Napkin 当前预期仍会因命令召回失败返回非零；这是正确的问题暴露，不应当强行调阈值掩盖。

3. 下一轮公式重点不应继续写零散正则。
   应做一个可解释、可扩展的 glyph/font 到 LaTeX 语义映射层：
   - 基于 Unicode math ranges 和 PDF font family，如 `MSBM10`、`LMMathSymbols`、`LMMathItalic`。
   - 统一同义命令审计，如 `≤` 对 `\le` / `\leq`，`→` 对 `\to`，`∼` 对 `\sim`。
   - 把符号映射表和审计等价类做成数据驱动，不嵌在业务流程里。
   - 先用于审计和离线重建，验证 Attention/Napkin 指标提升后再考虑进入主路径。

4. 继续用 Napkin 前 120 页作为公式结构压力测试。
   目标先把 `common_source_command_recall` 从 `0.128` 提升到门槛以上，同时保证 `source_weak_match_rate` 不下降、耗时不明显增加。

5. 再推进 RAG/知识图谱。
   方向仍是导入即建基础索引，后台异步补全公式、图谱和高精度理解；首屏与滚动不等待 GraphRAG。需要补真实云端模型测试，确认配置 API、模型名和日志错误。

6. 长文档 E2E 仍是最终验收。
   Attention 必须保持通过；Napkin 只有在交互性能、日志、QA、公式审计同时满足后，才算接近完成。

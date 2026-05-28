# 异步多轮公式与全文索引设计

## 目标

本设计面向 born-digital PDF，默认硬件按 16G RAM / 4 核 CPU 估算。目标不是一次
打开 PDF 就把所有公式、知识库和知识图谱做完，而是让系统先快速可用，再通过多轮
异步扫描逐步变准，并且每一轮结果都持久化，避免重复计算。

核心原则：

- 第一轮必须快：阅读、滚动、缩放、翻译、基础全文问答不能等待重模型。
- 后续每一轮必须可恢复：任务状态、模型输出、置信度、耗时和错误都要写入存储。
- 每个结果必须可追溯：公式结果要能回到 PDF 页、bbox、文本层、模型名和修正来源。
- 每个重任务必须可取消、可暂停、可限流：不能长期占满 4 核 CPU。
- 每个模型后端都必须可替换：MinerU、Pix2Text、UniMERNet、PDF-Extract-Kit、DeepSeek
  只通过统一接口接入。

## 总体流水线

```text
PDF 导入
  -> Round 0: 原生结构快扫
       MuPDF / Poppler 读取文本层、glyph、font、bbox、vector、ToUnicode、ActualText
       写入 document_blocks、基础全文索引、formula_region_candidates
       UI 和基础问答立即可用

  -> Round 1: 缓存优先 OCR/MFR 补救
       只处理图片公式、扫描公式、低置信纯公式裁剪或整页对照任务
       先查 image_hash / input_hash / model_version 缓存，未命中才推理
       写回 formula_recognition_results 和缓存

  -> Round 2: 本地高精度多工具复核
       对低置信、问答 evidence、用户标注错误、复杂矩阵/对齐环境做二次识别
       Pix2Text / Paddle Formula / MinerU / UniMERNet / PDF-Extract-Kit 通过独立 worker 运行
       写回未接受候选，不直接覆盖高置信原始证据

  -> Round 3: 云端语义修正
       DeepSeek 基于上下文、候选公式、PDF 证据和模型置信度做校对建议
       只写候选 JSON；通过语法、证据一致性、上下文一致性门禁后才允许 accepted
       低置信结果进入人工/后续修正队列

  -> Round 4: GraphRAG 结构增强
       公式、章节、定理、概念、引用关系增量写入图谱 artifact
       基础 RAG 永远不等待 GraphRAG

  -> Round 5: 知识库增量更新
       accepted 高置信公式结果变化后增量写回 FTS/RAG/向量索引
       按 block/content/input hash 跳过未变化内容
```

## 存储模型

所有轮次都必须持久化。不能只把结果留在内存，也不能每次打开文档重新扫描。

### 当前落地状态（2026-05-24）

已在现有 `FormulaIndexStore` 上完成第一版多轮任务持久化，而不是另起一个同步扫描器：

- `formula_index_jobs` 增加 `scan_round`，默认 `r1_cached_recognition`，用于公式块 OCR/cache 回填任务。
- `formula_page_scan_jobs` 增加 `scan_round`，默认 `r0_pdf_structure`，用于导入后页级结构/MFD 发现任务。
- 新增 `formula_round_jobs`，统一记录非 OCR 轮次和跨轮状态，主键为
  `doc_hash + scan_round + target_type + target_id`。
- 当前轮次枚举：
  - `r0_pdf_structure`
  - `r1_cached_recognition`
  - `r2_local_high_precision`
  - `r3_cloud_semantic_review`
  - `r4_knowledge_graph`
  - `r5_knowledge_incremental_update`
- `formula_recognition_results` 已落地，用于保存不同轮次、模型、输入 hash 和预处理版本的
  公式候选结果；`accepted=true` 已有唯一性约束逻辑。
- `formula_acceptance_decisions` 已落地，用于记录 accept/reject 审核事件、上一 accepted
  result、触发来源、原因、接受后的 LaTeX 和 r5 input hash。切换 accepted 结果必须经由
  `FormulaIndexStore.accept_recognition_result()` / `reject_recognition_result()` /
  `accept_fusion_record()`，不能直接改表。
- 导入 PDF 后会立即持久化：
  - 全文页码进入 `r0_pdf_structure` 页面队列。
  - `needs_ocr=True` 的公式块进入 `r1_cached_recognition`。
  - 所有已解析公式块进入 `r3_cloud_semantic_review` 的非 OCR 复核记录。
- 显式高精度扫描使用 `r2_local_high_precision`，不会被 `r1_cached_recognition` 的 done 状态吞掉。
- r0 页面 worker 当前调用 `BornDigitalFormulaExtractor`，只消费 MuPDF born-digital 结构证据，
  写入 `stage=pdf_structure` 的未接受候选；r0 不初始化 MFD/OCR。
- r0 低置信、空 LaTeX 或需要复核的结构候选会额外排入 `r2_local_high_precision`
  任务，作为后续显式精扫/多工具对比的待处理目标；不会自动覆盖正文，也不会在默认阅读路径启动重模型。
- r2 当前通过 `ExternalFormulaToolRunner` 和 `tools/formula_tool_worker.py` 调独立工具环境，
  已支持 Paddle Formula 与 Pix2Text 公式图候选；所有 r2 结果默认 `accepted=false`。
- `formula_round_jobs.result_json` 和 `formula_recognition_results` 已记录输入 hash、模型和预处理版本；同一轮次同一输入完成后，二次打开或 `--reuse-db` 会跳过已完成 r0/r2 重任务。
- 新增 `tools/formula_multiround_pipeline.py`，用于 r0-r5 端到端 smoke/benchmark：默认 born-digital 路线不 OCR，显式 `--r2-sample-formulas` 才把现有公式块送入 r2 多工具候选复核，`--run-cloud-review` 可跑真实 DeepSeek r3，r4/r5 可用小批量 drain 验证图谱和知识库增量更新。
- 多轮报告已接入源 LaTeX 对照准确率复核：每个 stage/model 都输出 exact/near/weak match rate、average best similarity 和低相似候选；r0/r1/r2/r3 必须证明准确率逐轮递增，未达门槛的结果不能 accepted。
- 多工具协同细设计见 `docs/formula_multitool_fusion_design.md`：下一步实现候选级 fusion table、coverage-comparable 检查、accepted 门禁和 r5 增量写回；自写代码只做编排、审计、候选排序和门禁，不写硬编码公式解析规则。

这一步已完成多轮调度、存储闭环和 r3 候选写回的第一版：

- `FormulaSemanticReviewService` 消费 `r3_cloud_semantic_review` 队列，按批调用分析模型，对单个公式块生成 JSON 复核候选。
- `FormulaSemanticReviewFlow` 已把 r3 消费接入后台 QThread；MainWindow 空闲调度会小批量启动语义复核，导入热路径只负责入队。
- r3 结果写入 `formula_round_jobs.result_json`，保留 `suggested_latex`、`confidence`、`reason`、`risks` 和原始响应。
- r3 不覆盖 `DocumentBlock.content`，也不改 accepted 公式；低置信或未通过门禁的结果只能作为候选证据保存。
- fusion 派生的 r3 任务带 `semantic_review_priority`、`review_priority` 和 `review_priority_reason`：结构证据、本地工具候选、低相似、候选冲突、风险项和复杂 LaTeX 优先；低价值单字符 inline 只降优先级，仍然入库等待审计。
- r3 done 结果会合并原始排队 payload，保留 `review_candidate`、`queued_input_hash`、`review_input_hash`、优先级和优先级原因，保证审计时能复原云端审了哪个候选、基于哪个 fusion input。
- 行内公式候选额外携带 `inline_pdf_evidence`：原 PDF math-font span 的 text/font/size/bbox、候选 bbox、字体列表、字号范围和脚本字号证据。它用于 r3/工具复核和审计，不是默认 LaTeX 重建器，也不能绕过 accepted 门禁。
- 真实 DeepSeek r3 单条 smoke 已跑通。2026-05-28 已补上 accepted/rejected audit 表、
  命令行审核入口和 accepted 变化触发 r5 知识库 upsert 的闭环；r5 已同步 accepted
  公式 GraphRAG artifact 并记录 graph sync 状态。后续仍需产品级 UI 和更高质量的
  r4 语义图谱。

### 文档块

`document_blocks` 是 UI 和知识库的稳定边界：

- `doc_hash`
- `block_id`
- `page_num`
- `block_type`
- `content`
- `bbox`
- `metadata_json`
- `block_version`
- `source_stage`
- `updated_at`

规则：

- Round 0 写基础块。
- 后续轮次只做增量 upsert，不重建整篇文档。
- 低置信公式不能覆盖原始文本块，只能写候选或 shadow metadata。
- 高置信修正写回时必须保留 `previous_block_id` / `previous_content_hash`。

### 公式候选

`formula_candidates` 保存 PDF 结构事实和候选区域：

- `candidate_id`
- `doc_hash`
- `page_num`
- `bbox`
- `kind`: `inline` / `display` / `image` / `scan` / `unknown`
- `source`: `mupdf_raw` / `poppler_audit` / `tagged_pdf` / `model_detector`
- `confidence`
- `risk_flags_json`
- `glyph_facts_hash`
- `vector_facts_hash`
- `needs_model`
- `status`
- `updated_at`

规则：

- born-digital 候选默认 `needs_model=false`，除非乱码、缺字、低置信或用户显式精扫。
- 图片/扫描候选默认 `needs_model=true`。
- 风险标记必须可见：`prose_like_region`、`table_or_text_like_region`、
  `tabular_alignment`、`unknown_glyph`、`missing_tounicode` 等。

### 公式识别结果

`formula_recognition_results` 保存每个模型或修正轮的结果：

- `result_id`
- `candidate_id`
- `doc_hash`
- `stage`: `pdf_structure` / `local_fast` / `local_precise` / `cloud_semantic`
- `model`
- `model_version`
- `preprocess_version`
- `input_hash`
- `latex`
- `normalized_latex`
- `score`
- `duration_ms`
- `peak_memory_mb`
- `warnings_json`
- `evidence_json`
- `accepted`
- `created_at`

规则：

- 不同模型、不同预处理版本、不同裁剪输入必须各自保留结果。
- `accepted=true` 只能有一条当前有效结果；切换接受结果要写 revision。
- 本地模型输出和云端修正输出必须分开存，不能互相覆盖。
- 缓存 key 至少包含 `doc_hash + candidate_id + input_hash + model + model_version + preprocess_version`。

### 公式接受决策

`formula_acceptance_decisions` 保存每次审核事件：

- `decision_id`
- `doc_hash`
- `candidate_id`
- `result_id`
- `action`: `accept` / `reject`
- `decision_source`
- `decider`
- `reason`
- `accepted_latex`
- `previous_result_id`
- `input_hash`
- `payload_json`
- `created_at`

规则：

- 接受某个 result 时，必须清除同一候选的其他 accepted 标志，并写入一条 audit event。
- 拒绝某个 result 时，只清除该 result 的 accepted 标志，不触发 r5 知识库覆盖。
- 接受 fusion record 时，若 best result 已在 `formula_recognition_results` 中存在，直接接受该
  result；否则从 fusion payload 生成 `manual_fusion_acceptance` synthetic result，再进入同一
  acceptance 流程。
- r5 payload 必须带 `acceptance_decision_id`、`acceptance_source`、`best_result_id`、
  `accepted_latex`、page/bbox 和稳定 input hash。当前 blocks 列表里没有对应候选块时，
  r5 service 可从 payload 恢复一个公式块并增量 upsert。
- 命令行入口为 `tools/formula_acceptance_review.py`：
  - `list` 列出 recognition results。
  - `ready` 列出 `ready_for_manual_accept` fusion records。
  - `accept` 接受单个 recognition result。
  - `reject` 拒绝单个 recognition result。
  - `accept-fusion` 接受单个 fusion record，默认只允许 `ready_for_manual_accept` /
    `auto_accept_allowed`，强制覆盖必须显式 `--allow-not-ready` 并留下 reason。
  - `decisions` 列出 audit events。

### 任务队列

`async_jobs` 保存所有后台任务：

- `job_id`
- `doc_hash`
- `job_type`: `structure_scan` / `mfd` / `mfr` / `mineru_page` / `semantic_fix` / `graph_extract`
- `target_id`
- `priority`
- `status`: `queued` / `running` / `done` / `failed` / `paused` / `skipped`
- `attempts`
- `max_attempts`
- `lease_owner`
- `lease_until`
- `input_hash`
- `output_ref`
- `error`
- `created_at`
- `updated_at`

规则：

- 每个任务启动前先查输入 hash 和已有结果，命中则 `skipped` 或直接 `done`。
- `running` 任务必须有 lease，程序异常退出后可恢复为 `queued`。
- 失败任务按错误类型退避重试；模型缺失、显存/内存不足、API 限流不无限重试。
- 所有任务都必须可暂停和恢复。

## 多轮调度策略

优先级按用户价值和风险排序：

1. 当前视口、用户双击解释、正在翻译的区域。
2. 全文问答 evidence 命中的页和块。
3. 标题、摘要、定理、证明、图表说明附近的公式。
4. 高置信 display 公式，适合快速写入 RAG。
5. 低置信、疑似表格/正文混排公式，进入慢速复核。
6. 后台空闲时扫描剩余页。

默认预算：

- UI 热路径：只允许读取缓存，不允许模型未命中推理。
- 后台轻扫：每批少量页面或少量公式，任务间 sleep，避免持续占满 CPU。
- 显式精扫：用户主动触发时可提高预算，但仍只扫描当前视口附近或选定范围。
- 全篇精扫：必须有进度、暂停、恢复和预计耗时，不默认开启。

## 为什么必须异步分批

16G RAM / 4 核 CPU 的瓶颈不是 PDF 文本层解析，而是模型冷启动、图像裁剪、公式识别、
整页解析和云端请求。同步全量扫描会导致：

- 打开长文档时首屏长期不可用。
- 滚动、缩放、翻译与后台模型抢 CPU。
- 公式模型冷启动被误计入用户操作延迟。
- 任务失败后无法恢复，只能重头再扫。
- 同一公式或同一页面反复推理，浪费时间和 API 成本。

异步分批的目标不是降低质量，而是保证质量提升可持续：

- 首轮先提供可读、可检索、可问答的基础内容。
- 后续轮次不断提升公式和图谱质量。
- 每轮结果落库，二次打开直接复用。
- 失败只影响单个任务，不拖垮整篇文档。

## 性能优化路线

### Python 层先优化

先稳定数据结构和算法，再下沉 C++。优先事项：

- 页面级结果缓存：`doc_hash + page_num + parser_version`。
- 公式区域去重：同页 bbox overlap、文本 hash、图片 hash。
- 批量任务合并：同模型、同预处理、同 DPI 的公式裁剪一起推理。
- 常驻 worker：模型只在后台 worker 懒加载，不在 UI 进程冷启动。
- cache-only 快路径：视口和问答默认只读取已存在结果。
- SQLite 批量事务：任务状态、公式结果、知识库增量写入必须批量提交。
- 背压控制：CPU、内存、队列长度、API 限流超过阈值时暂停低优先级任务。

### C++17 / Python C API 加速边界

适合下沉：

- 大规模 bbox overlap、interval tree、空间索引。
- glyph line grouping、baseline 聚类、二维布局关系计算。
- LaTeX 源码与 PDF 抽取结果的批量相似度审计。
- 图片 hash、裁剪归一化、简单二值化、边缘收紧。
- 大批量 token normalization 和 command recall 统计。

不适合下沉：

- Qt UI 调度。
- SQLite 任务状态机。
- 模型加载、模型选择、失败降级。
- DeepSeek API 调用。
- RAG 后端选择和证据展示。

接入原则：

- 原型先用 Python 写清楚数据结构和测试。
- 只有 profile 证明是热点，才写 C++17。
- C++ 模块必须释放 GIL，避免阻塞 Python UI 和任务调度线程。
- 每个 native 模块必须有 Python fallback。
- native ABI 必须小而稳定，优先处理数组/结构化数据，不把业务对象传进去。

推荐接口形态：

```python
from pdf_ai_native import bbox_index, latex_similarity

matches = bbox_index.match_overlaps(
    block_bboxes_np,
    formula_bboxes_np,
    min_overlap_ratio=0.3,
)

scores = latex_similarity.batch_score(
    pdf_formula_tokens,
    source_formula_tokens,
    candidate_limit=128,
)
```

C++ 实现内部使用 `pybind11::gil_scoped_release` 或等价机制释放 GIL；Python 只负责
调度、存储和错误处理。

## DeepSeek 语义修正边界

DeepSeek 的角色是校对和解释，不是无证据生成公式。

输入必须包含：

- 原公式候选。
- PDF 原生文本或 glyph 证据。
- 前后文。
- 本地模型结果和置信度。
- 可疑点，如未知 glyph、括号不平衡、上下标异常、表格混排风险。

输出必须包含：

- 修正后的 LaTeX。
- 是否建议替换。
- 替换理由。
- 保留原公式的理由。
- 需要人工复核的风险。

自动写回条件：

- LaTeX 语法基本有效。
- 定界符正确。
- 与 PDF 可见符号不冲突。
- 与上下文一致。
- 本地结构结果和云端修正差异可解释。
- 置信度超过配置阈值。

低于阈值时只保存为候选，不覆盖当前 accepted 结果。

## 与 RAG / GraphRAG 的关系

基础 RAG 不等待公式全量完成：

- Round 0 基础块立即写入全文索引。
- 高置信公式结果增量 upsert。
- 低置信公式保留 evidence，不伪装成正确 LaTeX。
- 问答命中低置信公式时，提高该公式和所在页的修正优先级。

GraphRAG 只消费已持久化事实：

- document / page / section / block / formula / theorem / citation 节点。
- contains / in_section / references / defines / uses_formula 等边。
- DeepSeek 或其他抽取器只能补充候选关系，必须写入 artifact 和证据。
- 图谱失败不影响基础问答。

## 验收指标

每轮必须输出结构化报告：

- 新增/更新候选数。
- accepted 公式数。
- low_confidence 公式数。
- 跳过任务数和跳过原因。
- 缓存命中率。
- 冷启动耗时。
- P50 / P95 单公式耗时。
- 峰值内存。
- API 请求数、失败数、限流数。
- Attention/Napkin 源 LaTeX 对齐指标。

门槛：

- 默认打开、滚动、缩放不加载 MFR/MinerU/PDF-Extract-Kit。
- cache-only 路径不触发模型 import。
- 同一输入 hash 二次运行必须跳过或缓存命中。
- 长文档后台任务可暂停、恢复、失败重试。
- 公式结果必须带定界符和置信度。
- 自动修正必须保留原始结果和修正来源。

## 当前性能基准（2026-05-24）

新增 `tools/formula_index_performance.py`，只测导入热路径的结构解析和多轮任务持久化，不加载 OCR/MFR 模型。

命令：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools\formula_index_performance.py --case attention --max-pages 8 --output test_artifacts/formula_index_performance/attention_report.json --db test_artifacts/formula_index_performance/attention_jobs.db
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools\formula_index_performance.py --case napkin --max-pages 12 --output test_artifacts/formula_index_performance/napkin_report.json --db test_artifacts/formula_index_performance/napkin_jobs.db
```

结果：

- Attention 前 8 页：136 blocks，10 formula blocks，结构解析 1.3314s，持久化 0.0036s；轮次任务为 `r0_pdf_structure:queued=8`、`r3_cloud_semantic_review:queued=10`。
- Napkin 前 12 页：126 blocks，2 formula blocks，结构解析 0.9658s，持久化 0.0037s；轮次任务为 `r0_pdf_structure:queued=12`、`r3_cloud_semantic_review:queued=2`。
- 最新 `--case all` 检测：Attention 15 页总 2.1970s，结构解析 2.1650s，持久化 0.0046s；Napkin 前 16 页总 1.2997s，结构解析 1.2449s，持久化 0.0306s。

结论：

- 多轮任务入库不是性能瓶颈，当前约 0.3-0.45ms/page。
- 结构解析目前是导入阶段主要成本，仍可接受，但后续大文档应继续做页缓存和可见页优先。
- born-digital 公式不进入 OCR 队列，但已进入语义复核轮；云端修正通过后台小批量消费 `r3_cloud_semantic_review` 并写候选结果，不能重新全篇扫描。

## 下一步落地

1. 定义统一 `FormulaCandidateStore` / `FormulaResultStore` / `AsyncJobStore`。
2. `FormulaIndexStore` 多轮任务表、`formula_recognition_results` 和 accepted 唯一性已完成第一版；继续补 accepted revision/audit 表。
3. Pix2Text/Paddle 外部 worker 已有最小候选接入；下一步用同一批 Attention/Napkin 裁剪样本扩展到 MinerU、UniMERNet/PDF-Extract-Kit，并做源码对齐。
4. 增加更完整 worker 抽象：常驻 worker、批量协议、模型版本探测、模型缓存路径、失败降级和超时回收。
5. 用 Attention/Napkin 建立同一批候选样本，比较 Pix2Text、Paddle Formula、UniMERNet、PDF-Extract-Kit、MinerU 的准确率与耗时。
6. 对 bbox overlap 和 LaTeX 相似度审计做 profile，确认是否值得 C++17 下沉。
7. 把 accepted 高置信结果增量写回知识库和 GraphRAG artifact，低置信进入修正队列。

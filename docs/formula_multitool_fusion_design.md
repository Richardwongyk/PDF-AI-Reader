# 多工具公式候选融合与高精度门禁设计

## 目标

本设计解决一个核心问题：多轮公式解析不能只是把 Pix2Text、Paddle、MinerU、UniMERNet
等工具“各跑一遍”，而必须让它们在同一套证据、候选、源码对照和门禁体系下协作，
逐轮提高 LaTeX 还原准确率。

目标不是手写一个 LaTeX 解析器，也不是靠样本正则修补 Attention/Napkin，而是：

1. r0 快速、非 OCR 地提取 PDF 结构事实和 born-digital 候选。
2. r1 只对图片、扫描、乱码、缺文本层、缓存命中区域做低成本补救。
3. r2 调用多套本地工具生成候选，并对同一公式区域做候选对齐、比较和复核。
4. r3 用 DeepSeek 等分析模型做语义校对建议，但只写候选，不直接覆盖。
5. r4/r5 只接收 accepted 高置信结果，增量写入 GraphRAG 和全文知识库。

最终验收目标是公式扫描准确度高于 99.9%、阅读路径极高效、RAG/知识系统完整可用。
未达到 Attention/Napkin 大样本测试和真实交互闭环前，不得把目标标记为完成。
LaTeX 源码只用于测试、审计和验收；真实用户运行时不能假设存在源码。

## 不做什么

明确禁止：

- 不写按论文样本特化的正则、词表、字符串替换链来“修公式”。
- 不把自研 LaTeX AST/布局解析器作为默认主路去猜 PDF 中没有证据的结构。
- 不让 LLM 在缺少 PDF、工具或源码证据时编造公式。
- 不把 OCR/MFR 作为 born-digital PDF 的默认路径。
- 不把低置信候选写入正文、RAG、GraphRAG 或 accepted 结果。
- 不在 UI 热路径加载 MinerU、Pix2Text、Paddle、UniMERNet 等重模型。

允许自写的只有工程层：

- 统一候选协议、任务调度、缓存 key、结果落库。
- PDF 事实抽取和跨工具 evidence 编排。
- 候选相似度、源码对齐、质量门禁、审计报告。
- accepted/rejected/revision 状态机和增量写回。

验收时必须同时检查是否违反这些边界：生产公式路径不能出现样本特化词表、
样本论文正则、一次性手写修复链，默认 r0 不能调用自写 LaTeX 重建器冒充高精度解析。

## 核心数据模型

### FormulaEvidence

FormulaEvidence 描述“证据”，不是最终公式。

字段：

- `doc_hash`
- `candidate_id`
- `page_num`
- `bbox`
- `source`: `mupdf_raw` / `poppler_bbox` / `pdfminer_chars` / `actual_text` / `latex_source` / `tool_image` / `cloud_review`
- `input_hash`
- `preprocess_version`
- `payload_json`
- `warnings`

规则：

- r0 必须优先保存 glyph、font、bbox、span、line、vector、ActualText、image 引用等事实。
- r2/r3 必须引用 r0 的 bbox/input hash，不能创建不可追溯候选。
- 源 LaTeX 对齐只作为测试/开发/验收证据，产品运行时不能假设用户有源码。

### FormulaCandidate

FormulaCandidate 描述“一个工具或一轮生成的 LaTeX 候选”。

字段：

- `candidate_id`
- `stage`: `pdf_structure` / `cached_recognition` / `local_precise` / `cloud_semantic`
- `model`
- `model_version`
- `preprocess_version`
- `input_hash`
- `latex`
- `normalized_latex`
- `score`
- `duration_ms`
- `warnings`
- `evidence_refs`
- `accepted=false`

规则：

- 同一公式区域可以有多个候选，不能互相覆盖。
- r2/r3 候选默认 `accepted=false`。
- 缓存 key 必须包含 `input_hash + model + model_version + preprocess_version`。

### FormulaFusionRecord

FormulaFusionRecord 描述“候选融合结果”，也仍然不是正文。

字段：

- `candidate_id`
- `doc_hash`
- `fusion_version`
- `best_result_id`
- `ranked_result_ids`
- `coverage`
- `agreement_score`
- `source_similarity`
- `syntax_valid`
- `risk_flags`
- `accepted_gate`
- `decision`: `candidate_only` / `needs_more_evidence` / `ready_for_manual_accept` / `auto_accept_allowed`
- `result_json`

规则：

- Fusion 只排序和门禁候选，不发明新 LaTeX。
- 如果多个工具输出不一致，必须保留冲突和各自证据。
- `auto_accept_allowed` 只表示满足自动接受前置条件，真正写回仍需 accepted/revision 流程。

## 多工具协同策略

### r0: born-digital 结构事实层

职责：

- 用 MuPDF 读取文本层、glyph、font、bbox、span、line、image、vector。
- 可选 Poppler/pdfminer 做审计对照。
- 输出 PDF 结构事实候选和风险标记。r0 默认只保存 PDF facts 文本、bbox、font、
  vector、diagnostics 和 input hash；高精度 LaTeX 由后续工具/模型候选轮产生。

不做：

- 不 OCR。
- 不使用样本正则修复公式。
- 不默认调用项目自写布局 LaTeX 重建器生成 accepted 候选。
- 不把低相似度结果 accepted。

当前问题：

- Attention 前 6 页 facts-only r0 平均 best similarity 约 0.668，display 候选仍有表格/正文误吸和二维结构缺失。
- 同一报告显示 inline 公式缺口更严重：`inline_weak_match_rate=0.026`，
  `inline_unmatched_count=75`。行内公式和数学字体必须作为单独门槛。
- 因此 r0 只能作为事实层和候选层，不能直接作为最终知识库公式。

### r1: 缓存优先补救层

职责：

- 只处理 `needs_ocr=True` 的图片/扫描/无文本层/乱码区域。
- 先查 `formula_recognition_results` 和图像 hash 缓存。
- 未命中时才进入轻量 OCR/MFR。

不做：

- 不主动处理正常 born-digital 公式。
- 不在缓存未命中时阻塞打开、滚动、缩放。

### r2: 本地高精度多工具复核层

触发条件：

- r0 低置信或 `formula_accuracy` 低相似。
- bbox 含复杂二维结构、表格/矩阵/多行对齐风险。
- 问答 evidence 命中但公式候选低置信。
- 用户显式精扫当前页/当前选区/全文。

候选工具：

- Pix2Text：公式图 MFR 候选。
- Paddle Formula：FormulaRecognition 候选。
- MinerU：整页结构化 Markdown/布局候选，适合离线页级对照。
- UniMERNet/PDF-Extract-Kit：高精度公式图候选，待跑通。
- Poppler/pdfminer：born-digital 字符/bbox 交叉审计，不作为 MFR。

融合方式：

1. 对同一 r0 candidate 的 bbox 生成统一裁剪输入。
2. 每个工具写 `FormulaCandidate`，不覆盖。
3. 对每个候选计算：
   - 与 r0 文本证据的一致性。
   - 与其他工具候选的一致性。
   - 与源 LaTeX 的 best similarity（测试资料有源码时）。
   - 语法/定界符/空结果/明显失败 warnings。
   - 耗时、模型版本、预处理版本。
4. 生成 `FormulaFusionRecord`，选择最佳候选或标记 `needs_more_evidence`。

关键边界：

- Fusion 不做字符串改写修复，只做候选排序和门禁。
- 如果一个工具比 r0 分数高但覆盖样本少，报告必须写明 `coverage_comparable=false`。
- r2 首轮冷启动慢，必须后续优化常驻 worker、批处理和缓存，不得放入默认热路径。

### r3: 云端语义校对层

输入：

- r0 PDF 结构事实。
- r2 多工具候选和 fusion 排名。
- 周围段落、章节标题、公式编号、引用关系。
- 源 LaTeX 对齐摘要仅用于测试/验收，不作为普通用户运行假设。

输出：

- `suggested_latex`
- `should_replace`
- `confidence`
- `reason`
- `risks`
- `raw_response`

规则：

- 只写候选 JSON。
- 不直接 accepted，不覆盖正文。
- 当 r2 多工具候选冲突时，r3 必须解释选择依据或标记证据不足。

### r4/r5: 图谱与知识库写回

r4：

- 读取 accepted 或高置信候选，写公式/章节/定理/引用/概念结构图。
- 当前已完成结构图谱第一版，但语义级图谱仍需增强。

r5：

- 只有 accepted 结果变化后，才按 block/content/input hash 增量 upsert FTS/RAG/向量库。
- 未变内容必须跳过。
- 低置信候选不能进入知识库正文。

## accepted 门禁

一个候选要进入 accepted，至少满足：

1. `syntax_valid=true`，且有正确数学定界符。
2. `input_hash`、`model_version`、`preprocess_version` 可追溯。
3. `formula_accuracy` 在测试资料上达到高门槛：
   - 最终目标：Attention/Napkin 大样本公式准确率 `>= 0.999`
   - 阶段门槛：`near_match_rate >= 0.95`
   - 阶段门槛：`inline_near_match_rate` / `inline_weak_match_rate` 必须单独报告并持续提升
   - 阶段门槛：`average_best_similarity >= 0.90`
   - `low_similarity_candidate_count == 0`
4. 对 born-digital 公式，候选不得明显背离 r0 glyph/bbox 证据。
5. 对 r2/r3，若覆盖样本少，不能声称整轮质量提升，只能声称样本提升。
6. 低置信、工具冲突、空输出、表格误吸、正文误吸必须 `candidate_only`。

## 定向复核队列

下一步实现不应继续随机抽样，而应从审计报告定向选样：

1. 默认 r0 报告生成低相似候选列表。
2. 将低相似、低置信、复杂二维结构、工具冲突候选排入 r2。
3. r2 对这些候选批量跑多工具。
4. fusion 报告比较 r0 vs r2 最佳候选：
   - 准确率是否提升。
   - 覆盖是否可比。
   - 哪些公式仍需要 MinerU/UniMERNet/人工复核。
5. 达不到 accepted 门槛时，不写回正文。

## 性能预算

默认打开：

- 只运行 r0 入队和小批量结构扫描。
- 不启动重模型。

后台：

- r2 按低相似/高风险优先。
- 每批限制候选数、页数、超时时间。
- 支持 `--reuse-db` 和 input hash 跳过。

显式精扫：

- 用户触发时可以提高预算。
- 必须显示进度、可暂停、可恢复。

长文档：

- Napkin 是性能门槛。
- r2/r3/r4/r5 必须分批，不得全量同步。

## 下一步实现顺序

1. 已扩展 `tools/formula_multiround_pipeline.py`：
   - 输出 per-candidate fusion table、accepted gate、targeted r2 队列、inline candidate-only review 统计。
   - `--reuse-db` 可证明同一 fusion input hash 不重复派生 r2/r3/r5。
   - `--run-targeted-r2-after-fusion` 可在同次运行中立即消费一批 fusion 派生 r2。
2. 已新增 `formula_fusion_records` 持久化：
   - 记录 `fusion_version`、`input_hash`、best/ranked result ids、coverage、agreement、source similarity、risk flags、accepted gate、decision 和完整 result JSON。
3. 已接入 MinerU 页级候选、PEK/UniMERNet 后端 spec：
   - Paddle/Pix2Text/Pix2Text-MFR 可返回候选。
   - PEK 当前环境缺 `unimernet`，以 `pek_unimernet` warning 候选落库。
   - MinerU 3.1.15 走 `mineru_pdf_page` 后端，适合离线页级结构对照，当前耗时高。
4. 已新增 r5 accepted 增量写回 service：
   - 只消费 accepted 结果变化。
   - 知识库未就绪时保持 queued。
   - 当前仍缺 accepted/rejected/revision 的产品级审核 UI 和 GraphRAG 同步更新。
5. 行内公式已纳入候选与质量门禁：
   - `inline_spans:document_chunker` 参与 accuracy/fusion。
   - 纯脚注/装饰符号不再包成公式。
   - 纯 inline 候选默认不进入 OCR/MFR，只进入审计和 r3 复核。
6. 下一步仍必须完成：
   - 建立 accepted/rejected/revision 表与 UI。
   - r5 accepted 变化后同步 GraphRAG artifact。
   - 用 Attention/Napkin 大样本跑质量门禁和性能门禁。
   - 优化 r2 常驻 worker/批处理，降低多工具冷启动。

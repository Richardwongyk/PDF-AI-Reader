# 新终端交接文档（2026-05-25）

本文件给新终端/新 AI 助手接手用。先读根目录 `AGENTS.md`，再读本文件，然后再看
`TODO.md`、`docs/current_goal_and_next_steps.md`、`docs/async_formula_indexing_design.md`、
`docs/formula_extraction_research.md`、`docs/formula_multitool_fusion_design.md`、`docs/e2e_test_plan.md`。

## 先读结论

当前主线目标没有完成，不能宣称项目已达标。今晚目标被明确提高为：不间断运行，公式扫描准确度最终高于 99.9%，建立完整 RAG/知识系统，并在此基础上继续全线优化；未用 Attention/Napkin 大样本、行内公式、数学字体、真实性能和交互闭环证明前，不得标记完成。已经完成的是：闭环测试方案、部分 E2E/日志/公式审计工具、RAG/GraphRAG 设计、公式多轮任务表、`formula_recognition_results` 候选表、`formula_fusion_records` 融合记录表、r0 born-digital facts-only 结构候选落库、r1 缓存优先队列、r2 本地多工具候选 worker、r3 带候选/fusion 证据的语义复核候选写回、r4 结构图谱批处理第一版、r5 accepted 公式增量知识库 upsert service，以及 `tools/formula_multiround_pipeline.py` 对 r0-r5 的可审计流水线 smoke/benchmark。没有完成的是：born-digital 公式高精度 LaTeX 还原、行内公式高覆盖、外部工具大样本准确率/性能对比、PEK/UniMERNet 跑通、r4 语义级图谱质量、RAG/GraphRAG 的最终产品级体验、缩放/翻译/滚动渲染问题的最终闭环验收。

2026-05-25 最新补充：r3/r4 已不只是设计文档。`FormulaSemanticReviewService` 可以从 fusion payload 合成 inline/formula 候选块，真实 DeepSeek smoke 已跑通小批量候选，只写 JSON；r3 prompt 现在给云端的是压缩证据包而不是整段数据库 JSON，并保留 inline 来源段落 `source_context`，要求只输出 JSON 对象。若云端返回非 JSON，失败会落库并记录 raw response 摘要，便于审计和重试。r3 入队现在带 `semantic_review_priority` 和可读的 `review_priority_reason`，按证据价值、冲突/风险、相似度缺口和 LaTeX 复杂度优先消费；低价值单字符 inline 会延后但不会丢弃。r3 完成后仍保留 `review_candidate`、`queued_input_hash`、`review_input_hash` 和优先级原因，方便审计“云端到底审了哪个候选”。行内公式候选现在会把 PDF 字体、字号、bbox、math font span 和脚本字号证据写入 `inline_pdf_evidence`，传到 fusion/r3；例如 Attention 前 2 页 `ht−1` 已带 CMMI10/CMMI7/CMSY7/CMR7、6.974/9.963pt 和 `has_script_size=true`。这只是证据通道，不是手写 LaTeX 解析器，也不自动接受。`FormulaKnowledgeGraphService` 已消费 `r4_knowledge_graph` 队列，把普通公式和 fusion candidate-only 公式写入 `GraphIndexStore` artifact。`tools/formula_multiround_pipeline.py` 现在支持 `--drain-r2/--drain-r3/--drain-r4/--drain-r5`，报告 `formula_fusion_snapshots` 展示每次 fusion 的写入、缓存命中和派生 r2/r3/r5 入队数量。新增质量门禁：如果 r2 本地 MFR 候选比 born-digital 结构候选更差，会记录 `local_precise_degraded_against_born_digital`，不能进入 `ready_for_manual_accept`，不能写正文/RAG/GraphRAG accepted。

2026-05-25 TinyBDMath 数据集补充：新增 `src/core/latex_math_source_parser.py` 作为训练/审计专用 LaTeX math span 扫描器，保留源码 offset、delimiter、env、上下文；新增 `src/core/latex_macro_expander.py`，训练目标改为标准 LaTeX canonical target，项目私有宏如 Napkin `\pre` 会展开为 `^{\text{pre}}`，原始源码保留为 raw evidence。`tools/born_digital_formula_dataset.py` 和 `tools/formula_latex_audit.py` 已统一走该扫描器，不再用多套正则提取 source gold。新增 `tools/tinybdmath_sharded_dataset.py`、`tools/tinybdmath_shard_consolidate.py`、`tools/tinybdmath_gold_audit.py` 和 `tools/run_tinybdmath_full_data_pipeline.ps1`，用于 Attention/Napkin 页级分片、断点续跑、原子写入、合并、gold/label tier 审计、baseline/PyTorch 训练。PDF 候选标签引入 TOC 页锚点窗口，优先同页/近页源码匹配，短公式也先受页窗口约束，避免全书范围误配。当前分片版本为 `tinybdmath_pdf_source_page_anchor_macro_v3`；旧版本分片不会被合并器当成新数据。

2026-05-26 标准/计划补充：重要公开标准和参考资料已下载到本地 `.local_references/standards/`，包括 PDF 1.7、PDF Association ISO 32000-2 访问页、W3C MathML Core/3/4、XML entities、Unicode UTR #25/UAX #44/UCD/MathClass、Adobe AGL/AGLFN/zapfdingbats、texglyphlist、OpenType MATH/cmap/font file、LaTeX2e/amsmath/unicode-math。该目录已写入 `.git/info/exclude`，不能提交外部标准全文；索引见 `docs/local_standards_cache_index.md`。下一轮实现必须先强化 r0.5 补丁层，不能让 TinyBDMath 或 OCR 替代符号身份修复；覆盖所有公式规则的正确方式是 MathML/SLT 结构目标、coverage audit、模型置信、verifier 和 candidate-only 兜底，而不是手写固定规则表。

2026-05-26 TinyBDMath graph/relation 补充：插桩 exact rows 已资产化为 graph rows（Attention 138 + Napkin 29743，rows=29881，blockers={}，dataset hash `f49359d58f2b34b006028cfd106d6678e7999f116934fbbaebbf6b250c886ba0`）。新增 graph baseline、weak relation labels、edge baseline、relation scorer、structural candidate、SLT skeleton/verifier、structural eval 和 KaTeX MathML audit extractor；relation labels 现在带 `mathml_relation_hints`。`TinyBDMathCandidateService` 支持 `edge_model_path`，`tools/formula_multiround_pipeline.py --tinybdmath-edge-model` 可把 relation scores 和 structural candidate 写入 r2a evidence。全链条 smoke 已跑通并做了性能修正：MathML extraction 改为分块批量调用，relation label 构建可复用预计算 MathML rows；2000 行训练/审计链完成 MathML -> labels -> train -> score -> structural -> eval，总耗时约 48.758s，micro precision=0.978121、recall=0.124314、F1=0.220591。生产 r2a 限流错误已修正：`r2_limit=0` 只跳过视觉/本地高精度 r2，不再截断非视觉 TinyBDMath r2a；Attention 前 6 页 born-digital 非 OCR 链路 r2a processed=115、Napkin 8-16 页 r2a processed=78，并已验证二次打开 r0/r2a 均按 input hash 跳过。当前仍复用原候选 LaTeX，质量门禁仍失败，不能宣称公式质量已提升。下一步必须做 SLT/MathML hard label 对齐和 decoder/verifier。

2026-05-26 最新后台任务：已启动 `tools/run_tinybdmath_relation_pipeline.ps1 -OutputDir test_artifacts\tinybdmath_relation_pipeline_full_all_fast -Limit 0` 全量 29881 行 relation pipeline。当前日志显示已完成全量 MathML + relation labels 阶段，coverage 包括 inline=27725、display=2156、math_alphabet=5772、subscript=7188、superscript=4331、fraction=765、radical=564、script_size_pdf_evidence=11247，blockers={}；仍在训练/后续阶段时不要重复启动同一全量任务，先检查进程和日志。

2026-05-26 PyTorch 训练补充：新增 `tools/tinybdmath_train_edge_torch.py`，用隔离 `science` 环境训练 edge relation model，并导出主程序可读的 `tinybdmath_edge_baseline_model.json`；`tools/run_tinybdmath_relation_pipeline.ps1 -UseTorchEdge` 可启用。PyTorch 2.5.1 在 `science` 中可用（CPU）。2000 行/121174 条边 smoke 验证 accuracy=0.999424；500 行端到端 v2 structural eval precision=0.910751、recall=0.022944、F1=0.044761，低于保守 baseline，不能默认替换。后续要做 class-weighted loss、两层 MLP/GNN、阈值校准和 decoder 优化。

2026-05-26 最新 TinyBDMath 全线跑通补充：

- 准确训练集没有丢。当前可靠入口仍是 `test_artifacts/instrumented_attention_full/instrumented_training_rows.jsonl` 和 `test_artifacts/instrumented_napkin_fast_delivery_v3/instrumented_training_rows.jsonl`。真实 verified exact 行数是 Attention 135、Napkin 29743，合计 29878；Attention 另有 3 个 marker 未在编译 PDF 中找到，不能作为 verified 训练行。每行保留 raw source 和 macro-expanded canonical LaTeX，源码只用于训练/验收。
- “KaTeX warning 很多”不是插桩训练集失败，而是下一层“把 canonical LaTeX 解析成 MathML/关系监督”时，KaTeX 对 Napkin 中的 alignment `&`、xy-pic/量子电路、部分宏展开形态覆盖不足。后续应补 LaTeXML/更强 TeX AST/MathML parser，而不是质疑已验证的 PDF 彩色框训练行。
- 已删除 decoder 替模型判断的硬编码：`tinybdmath_latex_decoder` 不再在根号缺 `RADICAL_BODY` 时拿右邻 `HORIZONTAL` 关系补主体；decoder 只消费模型/结构候选已经选中的关系并渲染 candidate-only LaTeX。训练标签层也从直接检查 `\sqrt/\frac` 字符串，改为使用 KaTeX/MathML `relation_hints` 加 PDF 几何证据监督关系模型。
- 全量 relation pipeline 已完成：graph rows 29878，relation labels 2037744，PyTorch edge model 有效样本 1965743，导出 `tinybdmath_edge_softmax_v2_geometry_vector_rule_radical` JSON 模型；relation scores 29878 行/1570380 条；structural candidates 29878 行/219617 条 selected relations；structural eval micro precision=0.963245、recall=0.189623、F1=0.316868。该指标是弱监督关系层，不是最终公式准确率。
- 产物位置：`test_artifacts/tinybdmath_relation_pipeline_v4_full_train_all/edge_model/tinybdmath_edge_baseline_model.json`、`relation_scores/`、`structural_candidates/`、`structural_eval_report.json`。这些都是测试/训练产物，不提交。
- 主线 r2a 已接上全量模型：`test_artifacts/formula_multiround_attention4_tinybdmath_v4_full_model` 中 Attention 前 4 页 `r2a_tinybdmath_structural:done=46`，`formula_recognition_results` 写入 46 条 `tinybdmath_structural:tinybdmath`，accepted 全部为 0。复用 DB 后 fusion 50 条均 `already_done_same_input`，r2/r3/r5 不重复入队，证明跳过机制生效。
- `tools/tinybdmath_score_relations.py` 已新增 `--stream`，`tools/run_tinybdmath_relation_pipeline.ps1` 默认流式打分，并正确读取 PyTorch report。后续更大训练集不要再用整批读写打分。
- 当前质量边界必须说清：链路已经全线跑通，但公式还原质量远未达标。Attention 前 4 页 TinyBDMath group near_match_rate 约 0.25、average_best_similarity 约 0.648，且 `h_{t-1}` 等样例仍可能结构错误。下一步要补强 relation hard labels/SLT 对齐、LaTeXML parser、GNN/MLP 结构模型和 verifier/decoder，不允许在 decoder 里继续手写几何补丁。

2026-05-27 全软件验证补充：

- 新增 `tools/full_software_validation.py`：全软件总验收入口，统一编排 pytest、公式索引性能、LaTeX 源码审计、多轮公式流水线、二次打开跳过、日志审计；可显式开启桌面 E2E、云端 r3 和本地 OCR/MFR 工具。
- 新增 `tools/run_full_software_validation.ps1`：长时间 standard/full/nightly 验证后台运行入口，输出 pid、stdout、stderr、report 路径，避免前台干等。
- 新增 `tests/test_full_software_validation.py`，验证总计划确实覆盖 core/RAG/GraphRAG、公式、多轮、TinyBDMath、Attention/Napkin 和可选 E2E/cloud/local tools。
- quick 实跑已通过：`--profile quick --case all --max-pages 2` 7 步骤约 65.663s，required failures=0，报告在 `test_artifacts/full_software_validation_quick_live/full_software_validation_report.json`。
- standard 后台首跑除 `pytest_formula_pipeline` 中一个旧顺序断言外，其余 14 步通过；断言已修正为源码顺序，`pytest_formula_pipeline` 复跑 191 passed。
- 总验收已强化为“退出码 + JSON 产物语义检查”：公式索引、源码审计、多轮流水线和日志审计都要检查关键字段。默认总验收不能意外启用 OCR/MFD、本地工具或云端；多轮报告必须看到 r0/r0.5/TinyBDMath 候选和 reopen skip/cache 证据。
- 强化语义门禁后 quick 通过：`test_artifacts/full_software_validation_quick_semantic_v2`，7 步约 53.721s，required failures=0。
- 强化语义门禁后 standard 通过：`tools/full_software_validation.py --profile standard --case all --output-dir test_artifacts/full_software_validation_standard_semantic`，15 步约 119.678s，required failures=0。该报告和所有 `test_artifacts/` 产物不提交。
- 桌面 E2E 复测：Attention 通过；Napkin UI/RAG/日志链路完成但总结果因公式 quality gate 失败而返回 1，具体失败为 `common_source_command_recall 0.128 < 0.350`。不要把这个失败改成通过；它是当前公式质量未达标的验收证据。

新增资料入口：`tools/tinybdmath_sharded_dataset.py --case <name> --pdf <pdf> --latex-root <latex_root> --output-dir <dir>`。这让 Attention、Napkin 和后续任意 PDF+LaTeX source root 走同一条源码扫描、导言区宏展开、页锚点、PDF 分片、严格 gold、复核队列流程。

2026-05-25 训练集 100% 准确性补充：不要把 source/PDF 相似度高的候选直接当 gold。新增 `tools/tinybdmath_gold_policy.py` 统一 verified gold 闸门，要求同页窗口、源码页窗唯一、PDF 结构证据完整、无 unknown/warnings、严格 token 签名一致，且过短/单字符公式必须复核。`tools/tinybdmath_review_queue.py` 可把未自动通过的样本导出为 JSONL、PDF crop 图、源码上下文、PDF evidence 和视觉大模型审核 prompt；`tools/tinybdmath_apply_review.py` 只把自动 verified 行和高置信 `accept/revise` 复核结果合成独立 verified gold JSONL。阶段合并结果：Attention 15 页 + Napkin 1050 页分片、30790 条 source formulas、2493 条 PDF candidates；极严自动 gold 仅 12 条，其余进入复核，不得宣称两份 PDF 已全部一一对应完成。

2026-05-25 插桩训练集补充：当前更可靠的训练集路线是源码插桩/重编译，而不是拿用户给的已编译 PDF 做坐标基准。新增 `tools/tinybdmath_instrumented_latex_dataset.py`，对临时 LaTeX 副本中的每个公式染唯一颜色，重编译后直接从 born-digital PDF 结构层读取彩色 glyph/vector bbox，输出可复用 JSONL 训练集。新增 `tools/run_instrumented_dataset_background.ps1`，所有 Napkin 全量、LaTeX 编译、训练集长任务必须后台运行并从启动即写日志。Attention 全量回归为 138/138 精确框、`pdflatex-once` 约 6.423s；Napkin v3 已在 `test_artifacts/instrumented_napkin_fast_delivery_v3` 全量跑通，`pdflatex-once + fast-no-asy` 约 192.92s，源码公式 29743 条，`boxes_found=29743`、`verified_exact_boxes=29743`、`training_rows=29743`、`blockers={}`。这份插桩数据集是当前最可靠的 TinyBDMath 训练/评测起点，但仍只用于训练和验收，不进入真实用户生产路径。

2026-05-25 插桩训练集修复原因：上一版 Napkin 833 个缺失 marker 不是公式识别失败，而是训练集制备工具把不可渲染或被 TeX 忽略的源码也算进了分母，并且 alignment 环境染色不够细。已通用修复三类问题：跳过 `asy/asydef` 外部绘图语言块；遇到未注释 `\endinput` 后停止扫描；对 `align*` 等带 `&` 的 display 环境逐对齐单元染色。不要回退到“按样本删 833 行”或“硬编码 Napkin 文件名”的方案。

重要纠正：不要把项目自定义宏渲染后的视觉相似说成“就是同一个 LaTeX”。Napkin `tex/macros.tex` 中 `\pre` 定义为 `^{\text{pre}}`，所以 `f\pre(T)` 的 PDF 视觉上会像 `f^{pre}(T)`；这类样本仍然不能自动进 gold，必须通过宏定义审计或复核改写成 canonical LaTeX。

新会话不要先安装工具。先确认当前工作树、环境、防休眠和测试基线，再按本文的顺序继续。

## 当前真实状态

- 工作目录：`D:\程设大作业`。
- 主程序环境：`C:\Users\WYK\.conda\envs\pdf_ai_reader_314`。
- 现有用户环境不要动：`base`、`cs231n`、`drawing`、`science`、`pdf_ai_reader`、`pdf_ai_reader_314`、`lottery_python`、`pku_elective` 等。
- 2026-05-24 已重新按独立 worker 思路建立外部工具环境，当前 `conda env list` 显示：
  `pdf_tool_paddle310`、`pdf_tool_mineru310`、`pdf_tool_pix2text310`、`pdf_tool_magic310`、`pdf_tool_pek310`。
  这些都是隔离工具环境，不是主程序环境。
- 当前代码在 `6cb0860 Add formula multiround pipeline runner`、`a83986d Add formula fusion quality gates` 之后继续推进了 fusion 持久化、r4 公式图谱、r5 增量知识库接线、多工具 worker 扩展、行内公式候选审计和反硬编码 guard：r0 不走 OCR，不默认调用自写 LaTeX 重建器，只写 born-digital PDF facts 候选；r2 通过低置信结构候选或显式精扫调用 Paddle/Pix2Text/MinerU/PEK 等隔离 worker 写未接受候选；r3 可用 mock 或真实 DeepSeek，并读取候选/fusion 证据；r4 写结构图谱任务和 artifact；r5 只消费 accepted 变化，按 input hash 增量 upsert。
- 防休眠脚本仍应检查，不要假设一定有效。脚本在 `tools/keep_awake.ps1` 和 `tools/keep_awake_watchdog.ps1`。
- 当前工作树必须以 `git status --short` 为准。不要随手回退，也不要把 `测试资料/`、日志、缓存、临时 benchmark 输出提交。
- 若看到 `tools/tinybdmath_sharded_dataset.py --case all --workers 3 --output-dir test_artifacts\tinybdmath_sharded_full_page_anchor_v2` 进程，说明 v2 全量真实数据集仍在跑；可以用 `tools/tinybdmath_shard_consolidate.py --output-dir test_artifacts\tinybdmath_sharded_full_page_anchor_v2 --min-age-sec 10` 合并已完成分片做阶段审计，但不要提交 `test_artifacts/`。

## 必须遵守的设计哲学

1. **事实优先**：PDF 里真实存在的文本层、glyph、font、bbox、vector、ActualText、图片和源码对照才是证据。
2. **工具优先**：优先查官方文档、成熟工具、开源源码和论文工程实践。自写代码只做编排、适配、缓存、审计和必要 glue。
3. **证据优先**：公式、RAG、GraphRAG、问答都必须能追溯到页码、bbox、源码、检索片段、模型响应或任务日志。
4. **性能优先**：打开、滚动、缩放、翻译、基础问答不能等待 OCR/MFR/MinerU/GraphRAG/云端修正。
5. **异步持久化优先**：导入后可以尽早全篇入队，但每轮结果必须落库，二次打开必须复用，不能反复重扫。
6. **可替换优先**：MinerU、Pix2Text、UniMERNet、PDF-Extract-Kit、PaddleOCR、DeepSeek 等必须通过统一接口或独立 worker 接入。
7. **审计优先**：Attention 与 Napkin PDF + LaTeX 源码是公式验收基准；闭环 UI 操作、日志和性能报告是交互验收基准。
8. **长任务并行推进**：预计超过 1 分钟的 LaTeX 编译、插桩训练集、Napkin 全量、OCR/MFR、外部工具 benchmark 必须后台运行并写日志；前台继续写代码、设计、文档或审计，不允许同步干等。启动后台任务时记录 pid、输出目录、日志路径，之后只做短轮询和故障处理。

明确禁止：

- 不要用样本特化正则、固定词表、一次性启发式函数伪装公式识别。
- 验收时必须检查生产公式路径是否出现样本特化词表、论文样本正则、手写修复链或默认自写 LaTeX 重建器。
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
| r5 | `r5_knowledge_incremental_update` | 把高置信修正增量写回全文 RAG/FTS/向量库，并同步 accepted 公式 GraphRAG artifact | accepted 结果变化时 | knowledge index、block revision、graph artifact | 只消费 accepted；低置信候选不能污染正文/RAG/GraphRAG |

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

2026-05-25 本轮新增：

- 新增 `src/app/formula_knowledge_graph.py`：r4 公式图谱服务读取 `r4_knowledge_graph` round jobs，按 input/content hash 跳过已完成任务，写入 `GraphIndexStore` artifact，结果 JSON 包含 stage、input hash、model/model_version、node/edge 数、candidate-only 状态和 fusion 决策。
- `src/app/graph_index_flow.py` 支持 candidate-only 公式节点：未过门禁的 fusion 候选写成 `formula_candidate` 节点和 `suggests_formula_candidate` 边，不伪装成 accepted 公式。
- `src/ui/main_window.py` 已接入 r4/r5 空闲调度：导入时排 r4 公式图谱任务，r3 后可继续小批量 r4，r5 只消费 accepted 变化。
- `src/app/formula_semantic_review.py` 已记录 r3 input hash、model/model_version，并规范化云端 `risks` 字段；即使 DeepSeek 返回字符串 risks，也保存为单个风险项，不再拆成逐字符日志。
- `src/app/formula_semantic_review.py` 的 r3 prompt 已改成压缩证据包：只保留 PDF diagnostics、候选模型/version/input hash、fusion gate、ranked candidates 和 inline 段落上下文；候选输出会自动补数学定界符，但仍只作为候选写库。
- r3 云端队列已加入通用优先级：高价值、低相似、候选冲突、结构/本地工具证据和复杂 LaTeX 先审；单字符/低价值 inline 只降优先级，不删除。优先级分数和原因写入 `formula_round_jobs.result_json`。
- r3 完成结果会合并原始排队 payload，保留 `review_candidate`、`queued_input_hash`、`review_input_hash`、`review_priority` 和 `review_priority_reason`，避免 done 后丢失“审的是哪个候选”的证据。
- `DocumentChunker` 对 math-font 行内公式新增结构化 `inline_math_candidates` metadata：每个候选保存原 PDF span 的 text/font/size/bbox、候选 bbox、字体列表、字号范围和 `has_script_size`。`tools/formula_multiround_pipeline.py` 会把它转成 fusion evidence 和 r3 `review_candidate.inline_pdf_evidence`。
- `tools/formula_multiround_pipeline.py` 已能 drain r2/r3/r4/r5；r4 使用真实 `FormulaKnowledgeGraphService`；fusion 将 inline 候选按 candidate id 分开，不再把同一段落多个行内公式合并；报告新增 `fusion_best:*` 准确率组和 `formula_fusion_snapshots`。
- fusion 门禁已加严：存在 r2 但 r2 质量低于 r0/parsed/inline born-digital 证据时，记录 `local_precise_degraded_against_born_digital`，最终仍为 `needs_more_evidence`，不进入 accepted/r5。
- 新增/更新测试覆盖：r4 公式图谱、candidate-only graph 节点、r3 payload inline 候选、r3 风险字段规范化、r1/r2 结果 JSON 的 input hash/model version、r2 降质不接受、fusion_best 准确率组、drain r2/r3/r4。

2026-05-25 验证结果：

- 相关测试：`tests/test_formula_multiround_pipeline.py tests/test_formula_knowledge_graph.py tests/test_formula_knowledge_update.py tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_graph_index_flow.py tests/test_graph_index_store.py tests/test_formula_tool_comparison.py tests/test_external_formula_tools.py tests/test_formula_detector.py tests/test_born_digital_math.py tests/test_smoke.py -q` 为 `174 passed`。
- Attention 前 2 页默认非 OCR 多轮：约 0.993s；`r0_pdf_structure:done=2`、`r3_cloud_semantic_review:done=9`、`r4_knowledge_graph:done=9`；首次 fusion 写入 9 条并派生 r3 9 条，r3 后同 input hash 全部缓存命中。
- Attention 前 6 页默认非 OCR 多轮：约 5.755s；`r0_pdf_structure:done=13`、`r3_cloud_semantic_review:done=122`、`r4_knowledge_graph:done=122`；`ready_for_manual_accept=0`、`needs_more_evidence=122`；严格质量门禁失败，r0/fusion/inline 仍远未达 99.9%。
- Attention 前 6 页 targeted r2 + drain：`r2_local_high_precision:done=7`、`r3=122`、`r4=122`；`local_precise:pix2text-mfr` 平均 best similarity 约 0.578，低于 r0 的 0.668；fusion 记录 `local_precise_degraded=5`、`ready_for_manual_accept=0`，证明降质 r2 只保留候选，不覆盖正文。
- Attention 前 2 页真实 DeepSeek r3 smoke：约 35.777s；实际 client 为 `deepseek/deepseek-v4-pro`，处理 2 条、剩余 7 条保持 queued；只写候选 JSON 和 fusion/r4 candidate，不 accepted。
- Attention 前 2 页 r3 证据包 smoke：默认 mock 全 drain 约 0.926s，r3/r4 各 9 条；真实 DeepSeek 限 1 条约 37.304s，模型未按 JSON 返回时该任务标记 failed，error 写入 raw response 摘要，剩余 8 条保持 queued。失败不覆盖正文、不影响 r4 候选图谱。
- Attention 前 2 页 r3 优先队列 smoke：`--r3-limit 1` 不 drain 时，只处理 1 条、剩 8 条 queued；首条从原先容易命中的单字符 `t` 改为更高价值的 `ht−1` 候选。SQLite 审计显示 done 记录保留 `latex='ht−1'`、queued hash、review hash 和 `review_priority_reason`；单字符 `t` 仍在队列中，只是延后。
- Attention 前 2 页 inline PDF evidence smoke：`ht`、`ht−1`、`t` 这些 inline 候选已带实际 PDF bbox、fonts、font size 和 `has_script_size`。`ht−1` 的 evidence 包含 CMMI10/CMMI7/CMSY7/CMR7，字号范围 6.974 到 9.963，bbox 为实际 span union；后续 r3/工具可以据此恢复上下标，但当前仍只作为候选证据。
- Napkin 前 8 页轻量多轮：约 7.873s；r0 处理 8 页，没有公式候选时 r1/r2/r3/r4 正确跳过，证明大 PDF 前言区不会误触发 OCR/MFR。

文档与计划：

- 写入根目录 `AGENTS.md`，作为新会话第一入口。
- `TODO.md` 顶部新增 2026-05-24 新终端交接入口。
- 已有文档包含当前目标、设计哲学、RAG/GraphRAG 迁移、公式抽取调研、OCR 性能设计、异步多轮索引设计、E2E 测试方案。

公式与索引代码进度：

- 已有多轮公式任务枚举与存储：r0/r1/r2/r3/r4/r5。
- 已有 `formula_recognition_results` 候选结果表，记录 stage、model、model_version、preprocess_version、input_hash、latex、score、warnings/evidence 和 accepted。
- 已有 accepted 唯一性：同一候选当前只允许一个 accepted 结果。
- 导入后会把页级结构扫描、需要 OCR 的公式块、已解析公式块的 r3 复核任务写入队列。
- r0 页面扫描当前只走 born-digital PDF 结构事实，使用 `BornDigitalFormulaStructureExtractor` 写 `stage=pdf_structure` 的未接受候选，不初始化 OCR/MFR，不默认调用自写 `PdfFormulaSemanticReconstructor` 生成 LaTeX。
- r0 低置信结构候选会排入 `r2_local_high_precision` 待复核任务；这只是持久化待办，不会在默认阅读路径启动重模型。
- r2 本地高精度轮通过 `ExternalFormulaToolRunner` + `tools/formula_tool_worker.py` 调隔离工具环境；当前已有 Paddle Formula、Pix2Text、MinerU 3.1.15 页级后端、PEK/UniMERNet 后端 spec。工具成功、空输出、不可用都会以各自工具身份写候选/warning，结果默认不 accepted。
- `tools/formula_multiround_pipeline.py` 已把 r0/r1/r2/r3/r4/r5 串到同一条可审计命令行流水线：每轮输出状态、耗时、任务统计、结果表统计；`--reuse-db` 可证明二次打开跳过已完成 r0/r2/fusion 输入；`--run-targeted-r2-after-fusion` 可在 fusion 后立即消费一批定向 r2；`--r2-sample-formulas` 是显式高精度精扫，不是默认 OCR。
- 新增 `docs/formula_multitool_fusion_design.md`：明确下一步不是手写公式解析规则，而是统一 evidence/candidate/fusion schema、源码准确率复核、候选融合、accepted 门禁和 r5 增量写回。
- `FormulaSemanticReviewService` 和 `FormulaSemanticReviewFlow` 已接入候选/fusion 证据：批量调用分析模型，写回 JSON 候选，不覆盖正文。
- `FormulaKnowledgeUpdateService` 已接入 r5：只有 accepted 结果变化才按 input hash 增量 upsert 到 `KnowledgeEngine`，知识库未就绪时保持 queued，不重建全文；同一批 accepted 公式会同步写入 `GraphIndexStore` artifact，并在 r5 result JSON 记录 `graph_synced/graph_failed`。
- `tools/formula_acceptance_review.py` 和基础公式审核对话框已支持 accept/reject、accept-fusion 以及审核者输入的 manual revision；revision 写成 `manual_revision/human_review` 候选后再走同一 audit/r5 流程，不是自动修正规则。
- UI 空闲时可小批量调度公式索引/语义复核，避免导入热路径同步等待。
- 日志改为轮转并增加清理工具，避免日志无限膨胀。

测试和工具进度：

- 已有 `tools/e2e_pdf_workflow.py`，用于桌面闭环测试滚动、跳转、缩放、翻译、问答、截图和日志。
- 已有 `tools/formula_latex_audit.py`，用于 Attention/Napkin 与 LaTeX 源码对照审计。
- 已有 `tools/formula_ocr_benchmark.py`，用于 OCR/MFR 后端抽样性能测试。
- 已有 `tools/formula_tool_comparison.py`，用于同一批公式图的外部工具候选对比，并把 r2 候选写入 `formula_recognition_results`。
- 已有 `tools/formula_index_performance.py`，用于多轮公式索引任务入库性能检测。
- 已有 `tools/test_log_audit.py`，用于清理和审计日志。
- 此前相关测试基线：`tests/test_formula_multiround_pipeline.py tests/test_formula_knowledge_update.py tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_graph_index_flow.py tests/test_graph_index_store.py tests/test_formula_tool_comparison.py tests/test_external_formula_tools.py tests/test_formula_detector.py tests/test_born_digital_math.py tests/test_smoke.py` 为 `157 passed`；2026-05-25 本轮已更新到 `171 passed`，见上方验证结果。
- Attention 前 6 页真实多轮流水线验证：
  - facts-only 默认 born-digital 路线：r0 处理 6 页约 0.95s，写入 7 个 `pdf_structure:pymupdf_born_digital_structure` 结果；不初始化 OCR/MFR，不使用自写 LaTeX 重建器；r1/r2 正确跳过；r3 mock 写候选，r4 写结构图谱，r5 无 accepted 时跳过。
  - `--reuse-db` 二次运行：r0 `processed_pages=0`、`skipped_completed_pages=6`，证明已完成页跳过，整条报告约 1.69s。
  - 显式 `--r2-sample-formulas 1 --auto-local-tools`：r2 对 1 个公式样本调用 `pix2text-mfr`、Paddle Formula、Pix2Text 隔离 worker，写入 3 条 `local_precise` 未接受候选；冷启动约 245s，后续 `--reuse-db` 约 1.2s 跳过同一输入。
  - `--run-cloud-review`：DeepSeek `deepseek/deepseek-v4-pro` r3 单条真实 smoke 通过，约 60s，只写候选 JSON，不覆盖正文。
  - 多轮流水线报告已接入源 LaTeX 准确率复核。源码只用于验收，不进入真实用户运行路径。facts-only r0/parsed blocks 前 6 页 average best similarity 约 0.668，near match rate 0.429；行内候选接入后，Attention 前 6 页 `pdf_inline_formula_snippets=115`，`inline_source_weak_match_rate=0.299`，`inline_source_unmatched_count=54`，明显好于此前 0.026/75，但仍远未达 99.9%。
  - `formula_fusion` 已按 bbox/candidate_id 归并 parsed/r0/r2/r3/inline 候选，输出 per-candidate 排名、accepted gate 和定向 r2/r3/r5 派生统计；当前 Attention 前 6 页 34 个候选区域 0 个 ready，全部 `needs_more_evidence`；其中 `missing_or_insufficient_r2=6`、`inline_candidate_only_needs_review=10`。纯 inline 候选默认不进 OCR/MFR，只进入候选审计和 r3 复核。
- Attention 最新多轮入库性能：15 页总约 2.31s，持久化约 0.006s，入队 `r0_pdf_structure:15`、`r3_cloud_semantic_review:11`。
- Attention born-digital 前 6 页结构审计：约 0.608s，17816 glyph，unknown glyph 为 0，display region 7 个；仍未达到完整 LaTeX 还原目标。

防休眠：

- 已有 `tools/keep_awake.ps1`：调用 Windows `SetThreadExecutionState`，可选发送 F15。
- 已有 `tools/keep_awake_watchdog.ps1`：周期性重写电源策略、重启 worker、写日志。
- 最后一次进程检查看到多个 `keep_awake` 相关 PowerShell 进程，但新会话仍必须重新确认。

## 已遇到并解决的问题

1. **不能把外部公式工具混进主环境**
   - 结论：主程序环境只保留项目运行依赖；MinerU/PaddleOCR/PDF-Extract-Kit/UniMERNet 等必须独立 worker 环境。

2. **外部工具必须隔离**
   - 曾经混装/并行创建环境的风险已明确；当前改为每个大工具一个独立环境。
   - 当前存在 `pdf_tool_paddle310`、`pdf_tool_mineru310`、`pdf_tool_pix2text310`、`pdf_tool_magic310`、`pdf_tool_pek310`，不要混进主环境。

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
   - 行内公式现在单独纳入源码验收：LaTeX 中 `$...$`、`\(...\)`、`\[...\]`、`$$...$$` 都算公式；当前 inline 质量远未达标。
   - 关键难点：PDF 通常保存排版后的 glyph/bbox，不保存原 TeX AST。复杂二维结构、源码宏、字体编码、表格/列表误吸、页级源码对齐都未完全解决。
   - 方向：优先复用成熟 PDF/公式结构工具或源码；自写只做中间表示、审计、调度、缓存。
   - 现在每轮必须看 `formula_accuracy.stage_metrics`，不能只看候选数量。r0/r1/r2/r3 必须证明 exact/near/weak/average similarity 逐步提升；未达到门槛的结果只能保留候选，不能写入 accepted 或知识库。

2. **图片/扫描公式 OCR/MFR 仍只能作为候选层**
   - Pix2Text 和 Paddle Formula 已有独立 worker smoke，但 Attention 单图输出仍有明显归一化/字符问题，不能覆盖正文。
   - MinerU 3.1.15 本地新模型已跑通单页整页解析，但耗时长，当前适合离线候选和结构对照。
   - UniMERNet/PDF-Extract-Kit 尚未跑通；旧 magic-pdf 缺权重，只能作为历史路线对照。

3. **外部工具还缺大样本对比**
   - 当前只证明部分工具“能启动/能返回候选”，还没有证明 Attention/Napkin 大样本准确率、P95 耗时、内存和缓存命中。
   - 下一步必须用同一批裁剪样本比较 Pix2Text、Paddle Formula、MinerU、UniMERNet/PDF-Extract-Kit，不能靠单图观感定方案。当前 r2 单样本多工具冷启动约 245s，必须重点优化 worker 常驻、批处理、模型缓存和超时策略。

4. **RAG/GraphRAG 仍未达到产品级**
   - FTS/RAG 方向已有基础，但全文理解、证据链、公式/定理/引用图谱、问答 UI 还要继续打磨。
   - r4 当前只是结构图谱第一版，证明异步入库和跳过机制；语义级公式/定理/概念/引用关系仍需模型或规则证据增强。GraphRAG 必须异步，不得阻塞基础阅读。

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

当前环境矩阵：

| 环境 | Python | 目标 | 验证 |
| --- | --- | --- | --- |
| `pdf_tool_paddle310` | 3.10 | `paddlepaddle` + `paddleocr` FormulaRecognition | import 与单张公式图 smoke 通过；输出质量待审计 |
| `pdf_tool_mineru310` | 3.10 | MinerU 3.1.15 新模型本地解析 | Attention 单页 smoke 通过；耗时约 172s |
| `pdf_tool_magic310` | 3.10 | 旧 `magic-pdf` 对照 | 基础环境可用；缺旧模型权重，真实小页未通过 |
| `pdf_tool_pek310` | 3.10 | PDF-Extract-Kit + UniMERNet | 环境存在；源码拉取失败，未 smoke |
| `pdf_tool_pix2text310` | 3.10 | Pix2Text 现有链路复测 | 单张公式图 smoke 通过；缓存路径和质量待整理 |

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
- `docs/formula_multitool_fusion_design.md`：多工具公式候选融合和高精度门禁细设计，明确禁止手写硬编码解析规则。
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
- `tools/formula_tool_comparison.py`：外部公式工具候选对比和 r2 落库审计。
- `tools/formula_multiround_pipeline.py`：r0-r5 端到端多轮公式流水线 smoke/benchmark，支持默认 born-digital 路线、显式 r2 多工具精扫、真实/模拟 r3、r4 图谱批处理、r5 增量更新 smoke、drain 批处理和 `--reuse-db` 跳过验证。
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
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_formula_multiround_pipeline.py tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_graph_index_flow.py tests/test_graph_index_store.py tests/test_formula_tool_comparison.py tests/test_external_formula_tools.py tests/test_smoke.py -q
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

8. 跑多轮公式流水线 smoke：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools\formula_multiround_pipeline.py --case attention --max-pages 6 --r1-limit 4 --r2-limit 1 --r3-limit 2 --r4-limit 12
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools\formula_multiround_pipeline.py --case attention --max-pages 6 --r2-sample-formulas 1 --r2-limit 1 --auto-local-tools
```

9. 跑 E2E 闭环测试：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\e2e_pdf_workflow.py --case all
```

9. 如果要重新装外部公式工具，先写版本矩阵，再逐个独立环境安装，且每个环境只装一个工具栈。

## 下一步任务清单

P0：

1. 重新验证当前代码基线，确认文档修改前后的测试状态。
2. 继续补齐 r0/r1/r2/r3/r4/r5 的端到端测试：失败重试、租约恢复、r5 增量写回、真实 UI 触发和二次打开复用。
3. 用 Attention/Napkin 做真实公式质量门禁，记录每轮 exact/near/weak/average similarity、低相似样例和性能；准确率不递增时必须回退或标记失败。
4. 修复或隔离任何正则/硬编码公式识别实验，不能进入默认路径。
5. 继续外部工具大样本对比，优先把 PEK/UniMERNet 跑通或明确淘汰，再比较 Pix2Text/Paddle/MinerU 的 Attention/Napkin 质量和性能。

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
你接手的是 D:\程设大作业 的 PDF AI Reader 项目。先读根目录 AGENTS.md，再读 docs/next_session_handoff.md、TODO.md、docs/current_goal_and_next_steps.md、docs/async_formula_indexing_design.md、docs/formula_extraction_research.md、docs/formula_multitool_fusion_design.md、docs/e2e_test_plan.md。

当前主环境是 C:\Users\WYK\.conda\envs\pdf_ai_reader_314。不要动用户已有环境 base/cs231n/drawing/science/pdf_ai_reader/pdf_ai_reader_314 等。当前隔离工具环境包括 pdf_tool_paddle310、pdf_tool_mineru310、pdf_tool_pix2text310、pdf_tool_magic310、pdf_tool_pek310；不要混装进主环境，也不要未经确认删除。不要先装包，先确认 git status、conda env list、防休眠进程和测试基线。

设计红线：born-digital PDF 公式默认不走 OCR；图片/扫描/无文本层/乱码/低置信才走 OCR/MFR。禁止样本特化正则、固定词表、一次性启发式函数伪装公式识别。优先成熟工具和官方文档，自写代码只做编排、适配、缓存、审计和必要 glue。所有重任务异步分批，结果必须落库，二次打开跳过已完成任务。不要把 MinerU/Pix2Text/UniMERNet/PDF-Extract-Kit/PaddleOCR 混装到主环境。

多轮公式解析必须按 r0/r1/r2/r3/r4/r5 推进：r0 PDF 结构快扫，不 OCR；r1 缓存优先 OCR/MFR 补救；r2 本地高精度多工具复核；r3 DeepSeek 语义校对只写候选；r4 GraphRAG 结构增强；r5 高置信结果增量写回知识库。每轮必须有任务状态、输入 hash、模型版本、结果 JSON 和跳过机制，低置信不能覆盖正文。

测试资料在 测试资料/：Attention 是小文件，Napkin 是大文件，两者 PDF、LaTeX 源码和图片资源都要用于公式质量和性能验收。闭环测试必须模拟滚动翻页、跳转、双击翻译/隐藏/再次双击打开、缩放、问答、日志审计。长文档性能、缩放清晰度、翻译和问答延迟必须作为门槛。

下一步先跑轻量测试：C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_formula_multiround_pipeline.py tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_graph_index_flow.py tests/test_graph_index_store.py tests/test_formula_tool_comparison.py tests/test_external_formula_tools.py tests/test_smoke.py -q。然后跑 tools/formula_multiround_pipeline.py 的默认 born-digital、--reuse-db 跳过、显式 --r2-sample-formulas 多工具、可选 --run-cloud-review smoke，再跑 tools/formula_index_performance.py、tools/formula_latex_audit.py 的 Attention/Napkin 门禁，最后跑 tools/e2e_pdf_workflow.py。外部工具要先确认现有环境和模型缓存，再逐个真实 PDF 小页烟测和大样本对比。所有提交不得带额外署名、来源标记或生成工具署名，不提交测试资料、日志、缓存、临时产物。
```

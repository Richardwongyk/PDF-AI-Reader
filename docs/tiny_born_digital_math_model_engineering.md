# TinyBDMath 小模型研发工程方案

日期：2026-05-25

本文件只讨论小模型研发工程。PDF 底层事实抽取、r0.5 符号身份修复、视觉工具兜底、云端复核和 RAG 写回不在本文展开。

相关标准和本地资料缓存见 `docs/local_standards_cache_index.md`。TinyBDMath 不能替代这些标准和补丁层：它的目标是学习 PDF glyph/vector graph 中的二维结构关系，而不是凭模型记忆覆盖完整 TeX/LaTeX 规则或修复所有 glyph identity。

## 1. 小模型职责

TinyBDMath 的输入是 r0.5 之后的 Enriched Glyph Graph。图中每个节点已经尽力修复了符号身份，并保留身份置信度和候选。小模型的任务是预测这些符号之间的二维数学结构关系。

输入：

- glyph nodes。
- vector/path nodes。
- identity candidates。
- font/bbox/size/baseline。
- line-of-sight edges。
- PDF health 和 repair confidence。

输出：

- relation logits。
- Symbol Layout Tree。
- canonical LaTeX candidate。
- confidence。
- explanation/evidence。

不负责：

- OCR。
- glyph 身份修复。
- 作者原始宏恢复。
- 语义证明。
- 自动 accepted。

特别禁止：

- 把 TinyBDMath 当作“所有公式规则解析器”。
- 用固定 LaTeX 命令表、样本论文词表或手写正则伪装结构识别。
- 在符号身份缺失时跳过 r0.5，让模型硬猜 Unicode/LaTeX。
- 让模型自由生成 LaTeX 后直接进入正文、RAG 或 GraphRAG。

## 2. 为什么适合小模型

born-digital PDF 已经提供了大量信息。模型不需要从像素中学“这个形状是不是 x”，它主要学：

- 谁在谁右边。
- 谁是谁的上标/下标。
- 谁在线上方/下方。
- 哪些符号属于根号内部。
- 哪些符号构成矩阵行列。
- 哪些符号只是编号或标点。

这类任务输入维度小、结构明确、可解释，适合 MLP/GNN/Graph Transformer，而不是大视觉模型。

## 3. 模型路线

### 3.1 v0：MLP edge scorer

第一版先做 pairwise edge classifier：

- 对候选边提取几何/字体/identity 特征。
- 用 MLP/SVM/XGBoost 预测关系类别。
- 用约束解码生成 SLT。

优点：

- 快。
- 容易训练。
- 容易 ONNX/NumPy 部署。
- 适合先打通数据、指标和 verifier。

缺点：

- 全局上下文弱。
- 复杂公式准确率有限。

### 3.2 v1：GNN edge classifier

第二版使用 GNN：

- GraphSAGE/GAT/GIN/TransformerConv 均可评估。
- 2-4 层 message passing。
- 对候选边输出 relation logits。

GNN 是主路线，因为公式天然是 glyph relation graph。

### 3.3 v2：Graph Transformer

复杂结构可以尝试 Graph Transformer 或 Set Transformer：

- 全局上下文更强。
- 对矩阵、多行、复杂嵌套更有帮助。
- 数据需求更高。

不建议直接做自由 LaTeX seq2seq，因为可解释性和可验证性差。

### 3.4 视觉 CNN/ViT 的位置

CNN/ViT/视觉 Transformer 不属于 TinyBDMath 主模型。它们用于 r2b 视觉兜底。

### 3.5 规则覆盖的正确含义

LaTeX 是宏系统，PDF 也通常不保存作者原始宏或公式 AST，因此不能承诺“用一套手写规则覆盖所有公式”。TinyBDMath 的覆盖目标必须拆成：

- relation label 覆盖：horizontal、sup、sub、subsup、fraction、radical、accent、over/under、operator limits、delimiter、matrix、cases、aligned、text run、math alphabet、arrow label 等。
- dataset coverage：每类结构都有训练/验证/测试样本，manifest 中有数量和来源统计。
- decoder coverage：每类关系能转成 SLT/Presentation MathML/canonical LaTeX。
- verifier coverage：每类结构有符号覆盖、几何一致性和合法性检查。
- abstain coverage：未知宏、低置信、unknown glyph、shape-only 关键符号、横线歧义、正文污染风险都必须 candidate-only。

## 4. 特征设计

### 4.1 Node feature

- Unicode/category embedding。
- identity confidence。
- identity source。
- font family embedding。
- font size。
- bbox normalized x0/y0/x1/y1/cx/cy/w/h。
- baseline。
- glyph advance。
- math font flags。
- repair warnings。
- vector/glyph node type。

### 4.2 Edge feature

- dx/dy/distance/angle。
- x overlap/y overlap。
- bbox IoU。
- font size ratio。
- baseline delta。
- same line/span/font。
- line-of-sight direction。
- vector separator evidence。
- nearest neighbor ranks。
- page/region normalized distance。

### 4.3 Relation classes

第一版：

- HORIZONTAL。
- SUP。
- SUB。
- SUBSUP。
- ABOVE。
- BELOW。
- NUMERATOR。
- DENOMINATOR。
- RADICAL_BODY。
- RADICAL_INDEX。
- INSIDE。
- ACCENT。
- OVERLINE。
- UNDERLINE。
- OPERATOR_OVER。
- OPERATOR_UNDER。
- ARROW_LABEL_ABOVE。
- ARROW_LABEL_BELOW。
- FENCE_OPEN。
- FENCE_CLOSE。
- MATRIX_RIGHT。
- MATRIX_DOWN。
- CASES_BRANCH。
- TEXT_RUN。
- MATH_ALPHABET。
- PUNCT。
- NO_EDGE。

第一版模型可以只训练高价值子集，但 manifest 和 coverage report 必须列出未覆盖类别，不能把未覆盖类别伪装成已解决。

## 5. 数据工程

### 5.1 训练数据来源

- synthetic LaTeX formulas。
- arXiv LaTeX source。
- arXMLiv/LaTeXML MathML。
- IM2LATEX 公式重新编译成 PDF。
- Attention/Napkin 仅做回归和验收，不做过拟合训练。

### 5.2 数据生成流程

1. 取 LaTeX 公式。
2. 编译为 PDF。
3. r0 抽取 Raw Glyph Graph。
4. r0.5 修复符号身份。
5. 用 LaTeXML/KaTeX/自定义转换得到 SLT/Presentation MathML。
6. 对齐 glyph node 与 target node。
7. 生成 edge relation labels。

### 5.3 训练集切分

- 按论文切分。
- 按年份切分。
- 按领域切分。
- 按字体/engine 切分。
- 保留 Office/PPT 可复制公式单独域。

不能随机按公式切分后宣称泛化。

## 6. 解码器

模型输出 edge logits 后，解码器负责：

- 选择合法父边。
- 避免环。
- 合成 sub/sup/fraction/root/matrix。
- 分离 equation number。
- 生成 SLT。
- 序列化 canonical LaTeX。

解码器使用通用数学结构约束，不使用样本特化词表。

## 7. Verifier 接口

TinyBDMath 输出不能直接 accepted。必须交给 verifier：

- symbol coverage。
- geometry consistency。
- structure legality。
- render/layout check。
- identity uncertainty check。
- PDF health check。

模型输出里必须保留足够信息给 verifier 判断。

## 8. 部署

### 8.1 训练

训练可使用 PyTorch/PyTorch Geometric/DGL。训练环境可独立，不进主程序环境。

### 8.2 推理

推理优先：

- MLP：ONNX Runtime 或 NumPy。
- GNN：ONNX、TorchScript 或自实现轻量前向。
- 批处理公式 graph。
- 后台 worker，不进 UI 热路径。

### 8.3 版本

每个结果必须记录：

- graph_schema_version。
- model_version。
- decoder_version。
- verifier_version。
- threshold_profile_version。
- input_hash。

## 9. 指标

模型内部：

- relation F1。
- per-relation precision/recall。
- SLT exact。
- tree edit distance。

产品指标：

- candidate recall。
- accepted precision。
- accepted coverage。
- latency。
- cache skip rate。
- downstream RAG contamination rate。

99.999% 只用于 accepted precision 的长期统计目标。

## 10. 研发里程碑

### 2026-05-26 当前实跑状态

当前已经完成“模型链路跑通”，但没有完成“模型效果达标”。

已确认事实：

- 精确训练样本来自 LaTeX 插桩/重编译/PDF 结构层彩色框，不是粗略 PDF-源码匹配。当前 verified exact 行数是 Attention 135、Napkin 29743，合计 29878；Attention 未找到颜色 marker 的 3 条不进入 verified 训练行。
- 每条样本保留 `raw_source_latex` 和 macro-expanded `label_latex`。宏展开是训练目标标准化的一部分，真实生产推理不读源码。
- 训练样本层是准确的；当前 warning 主要来自 KaTeX 将复杂 canonical LaTeX 转为 MathML/parse-tree 时的覆盖限制，特别是 Napkin 的 alignment `&`、xy-pic/电路图和复杂宏形态。下一步应引入 LaTeXML 或更强 TeX AST/MathML 监督。

已跑通链路：

- `instrumented_training_rows.jsonl -> tinybdmath_graph_rows.jsonl`：29878 行。
- `graph rows -> MathML hints -> relation labels`：2037744 条 edge labels。
- `relation labels -> PyTorch edge model`：有效 edge samples 1965743，导出 JSON 模型 `tinybdmath_edge_softmax_v2_geometry_vector_rule_radical`，主程序环境不需要 PyTorch。
- `edge model -> relation scores`：29878 行，1570380 条 relation scores。
- `relation scores -> structural candidates`：29878 行，219617 条 selected relations。
- `structural eval`：micro precision=0.963245、recall=0.189623、F1=0.316868。
- `r2a main pipeline`：Attention 前 4 页用全量模型写入 46 条 `tinybdmath_structural:tinybdmath`；所有结果 candidate-only，accepted=0；复用 DB 时 fusion 50 条全部 `already_done_same_input`。

当前边界：

- 关系层验证 accuracy/precision 高不等于最终 LaTeX 还原准确率。
- 结构候选召回低，SLT/decoder 仍弱，`h_{t-1}`、`\sqrt{d_k}` 这类样例仍可能被错误结构化。
- TinyBDMath 输出不能 accepted，不能写正文/RAG/GraphRAG accepted。
- decoder 只能把模型选中的关系渲染为候选 LaTeX；不能在输出层用几何 fallback 猜根号主体、分数上下、上下标归属。

下一步优先级：

1. 用 LaTeXML/TeX AST 替换或补充 KaTeX 对复杂环境的关系监督，减少 MathML warning 和 weak-label 噪声。
2. 从 weak relation hints 升级到更强的 SLT/MathML 节点对齐标签，尤其是根号主体、分数 numerator/denominator、overline/accent、operator limits、matrix/cases/alignment。
3. 训练 class-weighted MLP/GNN，而不是只用线性 edge softmax；把结构一致性放入模型/候选选择层。
4. decoder 只做图到 LaTeX 的通用序列化和合法性检查，任何“选谁当主体”的判断必须来自模型输出或 verifier，不在 decoder 写补丁。
5. 继续用 Attention/Napkin 全量和后续新增 PDF+LaTeX 资料做真实评测，报告 formula-level exact/near/weak，而不是只报 relation-level 指标。

### M0：数据 schema

- Raw Glyph Graph schema 已由 `src/core/pdf_glyph_graph.py` 落地，r0 evidence 已携带局部 graph hash/health/glyph/vector/image。
- Enriched Glyph Graph schema MVP 已由 `src/core/symbol_identity_repair.py` 落地，当前支持 PDF text/glyph name/same font+CID 三类保守身份证据。
- Enriched Glyph Graph 已作为 `r0_5_symbol_identity_repair` 独立轮次写入 `formula_round_jobs`，按 input hash 跳过。
- `GlyphNameMappingLoader` 已提供 AGL/texglyphlist 风格资源加载入口，并把 mapping source/warnings 写入 r0.5 summary。
- `GlyphNameMappingLoader` 已支持项目资源目录、环境变量和 TeX Live/MiKTeX 常见路径自动发现；发现失败仅记录 warning。
- 本地标准和映射参考已缓存到 `.local_references/standards/`，索引见 `docs/local_standards_cache_index.md`；这些文件不提交，后续产品化资源必须先做许可证与 hash 审计。
- `tools/born_digital_formula_dataset.py` 已开始把 Attention/Napkin 全量 PDF + LaTeX 源码整理为真实训练/验收数据：source formula index、PDF candidate index、TinyBD feature graph JSONL。
- 下一步扩展真实 TeX Live/CTAN 资源打包策略、font cmap 和 outline 候选。
- edge candidate generator。
- 可视化工具。

### M1：MLP baseline

- Attention/Napkin 真实 PDF + LaTeX 源码数据流已开始落地：`tools/born_digital_formula_dataset.py`
  生成 source formula index、PDF candidate index 和 TinyBD feature graph JSONL。
- 源码插桩/重编译训练集已成为更可靠的第一训练资产：Attention 全量 138/138 verified exact rows；Napkin v3 全量 29743/29743 verified exact rows，blockers 为空。该数据集提供真实 PDF glyph/vector bbox 和 canonical LaTeX label，是下一阶段 TinyBDMath 训练/评测的主入口。
- `tools/tinybdmath_training_data.py` 已把真实数据产物转成训练/验收行，包含 quality label、
  unknown glyph rate、edge hint counts、feature density、structural signal count 和源码目标。
- `src/core/tinybdmath_baseline.py` 已提供标准库一隐藏层 MLP，用 PDF graph 特征预测候选质量；
  源 LaTeX 只用于标签和评估，不作为推理特征。
- `src/core/tinybdmath_torch_backend.py` 和 `tools/tinybdmath_train_torch.py` 已提供可选 PyTorch 后端；
  用户本机 `science` conda 环境可用于训练，主程序环境不安装 torch。
- 仍缺真正 relation label/SLT decoder；当前 MLP 是质量门控基线，不负责从 graph 直接生成 LaTeX。

### M2：插桩数据资产化

目标：把插桩训练集转换成结构模型真正能学习的 graph/relation 数据，而不是只把公式 bbox 和 LaTeX label 存起来。

必须产出：

- `instrumented_training_rows.jsonl -> tinybdmath_graph_rows.jsonl` 转换器。
- 每条样本包含 glyph nodes、vector nodes、font/size/bbox、reading/order edges、candidate structural edges、label_latex、raw_source_latex、case/page/source id、compiled PDF hash、schema/model preprocessing version。
- dataset manifest：输入文件 hash、行数、case split、公式类型统计、inline/display 统计、数学字体统计、vector-line 参与统计、失败样本。
- train/validation/test split：按 case、章节/页段和公式类型分层，禁止同页近邻泄漏。

第一版 relation label 可以分两层：

- 弱监督 formula-level：模型先学候选质量和局部结构风险，用于 r2a candidate scoring。
- 结构监督 relation-level：用可审计的 LaTeX/MathML/SLT 转换器生成父子/上下标/分数/根号/alignment 关系，再对齐 glyph graph。该层必须保留对齐置信度，不能把低置信关系当 hard label。

验收：

- Attention 转换行数 138，Napkin 转换行数 29743。
- 0 个缺 label、0 个缺 bbox、0 个 JSON schema error。
- 低置信 relation label 可以存在，但必须标记为 weak/ignored，不参与 hard supervision。

### M3：项目接入

- r2a structural candidate worker 已有第一版：`src/app/tinybdmath_candidate_service.py` 从 r0/r0.5
  persisted evidence 生成 TinyBD feature graph，写入 `formula_recognition_results`
  的 `tinybdmath_structural:tinybdmath` 候选，并写 `r2a_tinybdmath_structural` round record。
- `tools/formula_multiround_pipeline.py --run-tinybdmath` 已接入 r0/r0.5 后、fusion 前的非视觉 r2a；`--tinybdmath-edge-model` 可传入 edge relation model，把 `relation_scoring` 和 `structural_candidate` 一并写入 evidence。
- FormulaIndexStore 落库。
- pipeline 报告 TinyBDMath 候选，并进入 fusion/r3/r4 后续链路；结果固定 candidate-only，不覆盖正文/RAG。

下一步接入要求：

- r2a 输出必须包含 candidate LaTeX、relation evidence、confidence、verifier warnings、model_version、feature_schema_version、input_hash。
- Attention/Napkin 验收报告必须能比较 r0、r2a、r2b 视觉工具、r3 review 和 fusion 最终候选。
- 没有通过 verifier/accepted gate 时，r2a 永远不能覆盖正文和 RAG。

### M4：GNN

- GNN edge classifier。
- 分数/根号/上下标专项评估。
- inline 初步支持。

### M5：复杂结构

- matrix/cases/aligned。
- math alphabet。
- Office/PPT 可复制公式域。

### M6：质量证明

- 大规模独立测试。
- accepted gate 校准。
- 统计置信区间。

## 11. 当前最小可执行任务

1. 已完成 Raw Glyph Graph schema。
2. 已完成 r0.5 静态映射 MVP，生成 Enriched Glyph Graph。
3. 已完成 r0.5 独立落库最小闭环。
4. 已完成 AGL/texglyphlist 风格映射资源 loader。
5. 已完成 glyph map 自动发现入口。
6. 已开始 Attention/Napkin 真实数据集生成器。
7. 已完成真实训练行生成器、标准库 MLP 训练器、候选打分器、数据审计器、realdata pipeline。
8. 已完成可选 PyTorch 训练后端和 `tools/run_tinybdmath_torch_science.ps1`，可调用 `science` 环境训练。
9. 已接入 r2a candidate-only 服务和 `--run-tinybdmath` pipeline 开关。
10. Attention 前 6 页 smoke：`r2a_tinybdmath_structural` 处理 7 条 r0 候选，写入
    `tinybdmath_structural:tinybdmath` 7 条候选和 7 条 round done；fusion 能读取，`ready_for_manual_accept=0`。
11. 已完成插桩训练集第一阶段：Attention 138/138、Napkin v3 29743/29743 verified exact rows。
12. 已完成插桩训练集 graph assetization 第一版：`tools/tinybdmath_instrumented_graph_dataset.py` 将 Attention + Napkin 29881 条 verified exact rows 转成 `tinybdmath_graph_rows.jsonl`、manifest 和 split；实跑 rows=29881、attention=138、napkin=29743、blockers={}，dataset hash `f49359d58f2b34b006028cfd106d6678e7999f116934fbbaebbf6b250c886ba0`。
13. 已完成 dependency-light graph baseline smoke：`src/core/tinybdmath_graph_baseline.py` 和 `tools/tinybdmath_train_graph_baseline.py` 可从 graph rows 训练弱监督结构桶 softmax baseline，输出 model artifact 和 report。2000 行 smoke、3 epochs 验证集 accuracy 约 0.796；该 baseline 只证明数据/模型工件链路，不负责 LaTeX 生成或 accepted。
14. 已完成 relation label 弱监督流水线：`src/core/tinybdmath_relation_labels.py` 和 `tools/tinybdmath_build_relation_labels.py` 从 graph rows 生成 edge label rows。全量实跑 rows=29881、edge_labels=2035016、blockers={}，标签包括 HORIZONTAL/SUP/SUB/ABOVE/BELOW/FRACTION_BAR/RADICAL_BODY/NO_RELATION/IGNORE。弱标签只用于 bootstrapping，不能作为 accepted 质量证明。
15. 已完成 dependency-light edge baseline smoke：`src/core/tinybdmath_edge_baseline.py` 和 `tools/tinybdmath_train_edge_baseline.py` 可从 graph rows + relation labels 训练 edge softmax baseline。2000 行 graph 对应 121174 条 edge samples、3 epochs smoke 验证 accuracy=1.0；该结果主要证明弱标签和 hint 高相关，不能当泛化指标或最终准确率。
16. 已完成 relation scorer inference bridge：`src/core/tinybdmath_relation_scorer.py` 和 `tools/tinybdmath_score_relations.py` 可加载 edge model，对 graph rows 输出 candidate-only relation scores、summary、warnings 和 manifest。2000 行 smoke 输出 relation_scores=99467，warning 仍包含 expected_subscript/superscript/fraction not scored，说明当前模型只能作为候选证据，不能 accepted。
17. 已完成结构候选整理 MVP：`src/core/tinybdmath_structural_candidate.py` 和 `tools/tinybdmath_decode_structural_candidates.py` 将 relation scores 转成 selected_relations、horizontal-rule ambiguity report、verifier warnings 和 abstain 标记；不生成 accepted LaTeX，不把横线硬判为分数线/根号线/overline。
18. 已把 edge model 接入 r2a service 和 multiround pipeline：`TinyBDMathCandidateService(edge_model_path=...)` 会在 `evidence_json` 写入 `relation_scoring` 和 `structural_candidate`。Attention 前 6 页 smoke：r2a records_seen=7、processed=7、`relation_model_version=tinybdmath_edge_softmax_v0`，SQLite 抽查每条候选有 200+ relation scores 和 selected_relations；仍只沿用 r0 LaTeX 候选，未提升最终准确率。
19. 已把 inline math-font 证据接入 r2a：`TinyBDMathCandidateService.process_inline_candidates(...)` 会消费 `inline_pdf_evidence.spans` 中的字体、字号、bbox，并生成 inline feature graph、relation scores 和 structural candidate。Attention 前 2 页 smoke：inline records_seen=9、processed=6、skipped_no_evidence=3；`h_t`、`h_{t-1}` 等带脚本字号证据的行内候选已进入 `tinybdmath_structural:tinybdmath`。这仍是 candidate-only，单字母或无 span 证据会 abstain/跳过。
20. 已加入 SLT skeleton/verifier MVP：`structural_candidate` 现在包含 `slt_skeleton` 和 `verifier_report`，记录节点、roots、孤立节点、覆盖率、多父冲突、横线歧义和 accepted blocker。结构选择加入通用出入度约束，2000 行 smoke 中 selected_relations 从 39573 收敛到 9004，`slt_node_has_multiple_parents` 从 1346 降到 78。它仍不生成 LaTeX，也明确 `passed_for_accepted=false`；下一步 decoder 可以消费这个 skeleton，而不是直接读散乱 edge。
21. 已加入 structural candidate relation 级评测入口：`src/core/tinybdmath_structural_eval.py` 和 `tools/tinybdmath_eval_structural_candidates.py` 可把 selected relations 与 relation labels 对齐，输出 per-relation precision/recall/F1、row report 和 warning counts。2000 行 weak-label smoke：micro precision=0.978121、recall=0.124314、F1=0.220591，说明当前结构候选很保守、不能作为最终识别质量。
22. 已加入 KaTeX MathML/parse-tree audit extractor：`src/core/latex_mathml_extractor.py` 和 `tools/tinybdmath_extract_mathml.py` 复用本地 `src/ui/katex.min.js`，输出 MathML、parse tree node counts 和 relation hints。300 行 graph rows smoke 成功，只有 1 条 alignment `&` parse warning；该工具只用于训练/审计，不进入生产 PDF 推理输入。
23. relation labels 已带 MathML 结构提示：`tinybdmath_relation_labels.py` 为每条 graph row 记录 `mathml_relation_hints/mathml_node_counts/mathml_warnings`。100 行 smoke 中 MathML hints 包含 FRACTION_BAR=3、RADICAL_BODY=5、SUB=70、SUP=22。
24. 已加入一键 relation pipeline smoke：`tools/run_tinybdmath_relation_pipeline.ps1` 串起 MathML extraction、graph rows -> relation labels -> edge baseline training -> relation scoring -> structural candidates -> relation eval，并生成 `relation_pipeline_summary.json`。500 行训练/审计链已跑通主体步骤：MathML rows=500、relation labels rows=500、score rows=500、structural eval micro precision=0.966903、recall=0.125402、F1=0.222011；80 行 summary check 已验证汇总 JSON 正常生成。
25. 已完成 relation pipeline 性能修正：KaTeX MathML extraction 改为分块批量调用，relation label 构建可复用预计算 MathML rows，不再对每条公式重复启动/调用 KaTeX。2000 行完整 relation pipeline（MathML -> labels -> train -> score -> structural -> eval）已跑通，总耗时约 48.758s；relation labels 单步 2000 行降到约 5.791s，edge_labels=122580，structural eval micro precision=0.978121、recall=0.124314、F1=0.220591。
26. 已修正生产 r2a 限流错误：`r2_limit=0` 只表示跳过视觉/本地高精度 r2，不再截断非视觉 TinyBDMath r2a。新增回归测试保证 `r2_limit=0` 时 inline candidates 仍会全量进入 r2a。
27. 已跑通生产候选链 v2：Attention 前 6 页 `formula_multiround_pipeline.py --run-tinybdmath --tinybdmath-edge-model ... --r2-limit 0` 成功完成 r0/r0.5/r2a/r1/r2/r3/r4/r5，r2a records_seen=122、processed=115（7 条 r0 structure + 108 条 inline）、r2a elapsed=1.254s、总 elapsed=12.521s；Napkin 8-16 页 r2a records_seen=81、processed=78（2 条 r0 structure + 76 条 inline）、r2a elapsed=0.586s、总 elapsed=10.432s。两者均为 born-digital 非 OCR 默认路径，所有 TinyBDMath 输出仍是 candidate-only，`ready_for_manual_accept=0`。
28. 已验证二次打开跳过：Attention 前 6 页复用同 DB 时 r0 processed_pages=0、skipped_completed_pages=6，r2a processed=0、skipped_cached=115，总 elapsed=5.629s；Napkin 8-16 页 r0 processed_pages=0、skipped_completed_pages=8，r2a processed=0、skipped_cached=78，总 elapsed=5.703s。
29. 当前质量门禁仍未达标：Attention 前 6 页 TinyBDMath group candidate_count=78、near_match_rate=0.257、average_best_similarity=0.605；Napkin 8-16 页 TinyBDMath group candidate_count=30、near_match_rate=0.0、average_best_similarity=0.116。严格质量门禁均失败，说明链路已跑通但公式 LaTeX 恢复质量远未完成，不能写 accepted，也不能进入正文/RAG/GraphRAG。
30. 全量 29881 行 relation pipeline 已启动后台运行，当前已完成全量 MathML 和 relation labels 阶段，coverage 包括 inline=27725、display=2156、math_alphabet=5772、subscript=7188、superscript=4331、fraction=765、radical=564、script_size_pdf_evidence=11247，blockers={}；后续需等待 train/score/structural/eval 完成并审计报告。
31. 已新增正式 PyTorch edge relation 训练入口：`tools/tinybdmath_train_edge_torch.py` 必须在隔离 `science` 环境运行，训练后导出主程序可读取的 `tinybdmath_edge_baseline_model.json`，主环境不安装 torch；`tools/run_tinybdmath_relation_pipeline.ps1 -UseTorchEdge` 可切换到该路径。
32. PyTorch edge smoke 已跑通但不能直接替代当前候选 baseline：2000 行/121174 条边、CPU 4 epochs 验证 accuracy=0.999424，并能导出兼容模型；500 行端到端 PyTorch relation pipeline v2 structural eval precision=0.910751、recall=0.022944、F1=0.044761，仍低于 hint-prior/保守 baseline 的 500 行 F1=0.222011。原因包括类别极不平衡、置信度校准偏保守、decoder 对 LOW_CONFIDENCE 和横线/上下标选择仍过度 abstain。结论：PyTorch 路径已接通，但后续必须做 class-weighted loss、两层 MLP/GNN、阈值校准和 decoder 改进，不能为了“用 PyTorch”牺牲准确率。
33. 下一步第一优先级：把 `mathml_relation_hints` 升级为可对齐到 glyph graph 的 SLT/MathML hard labels，降低对 candidate hint 的直接依赖，形成真正 relation-level supervision。
34. 下一步第二优先级：用 graph rows 训练更正式的 MLP/PyTorch edge baseline，输出按 inline/display、上下标、分数线、根号、align、数学字体分项的评测报告。
35. 下一步第三优先级：让 r2a 用 structural candidate 进入真正 decoder/verifier，而不是继续复用 r0 latex；证明相对 r0/fusion baseline 有提升后才能提高门禁。
36. 下一步继续接入真实 TeX Live/CTAN 资源目录、font cmap 和 outline/shape identity candidate。
37. 下一步生成 100-1000 条 synthetic 公式 PDF graph，并开始 SLT/MathML decoder/verifier MVP。

## 12. 近期 48 小时执行计划

1. **数据转换器**
   - 输入：`test_artifacts/instrumented_attention_fast_delivery/instrumented_training_rows.jsonl` 和 `test_artifacts/instrumented_napkin_fast_delivery_v3/instrumented_training_rows.jsonl`。
   - 输出：`tinybdmath_graph_rows.jsonl`、`tinybdmath_graph_manifest.json`、`tinybdmath_graph_split.json`。
   - 要求：0 缺失、0 schema error、保留所有 hash/version/source/bbox/glyph/font/vector 证据，并记录 MathML/Unicode/glyph resource version。

2. **评测基线**
   - 先训练质量/结构风险 MLP，不急着宣称完整 LaTeX 生成。
   - 输出：train/val/test 指标、confusion、低置信样例、按公式类型分桶指标、未覆盖结构类别清单。
   - 目标：证明它能识别当前 r0/r2a 哪些候选可靠、哪些需要 r3/r2b/人工复核。

3. **结构解码 MVP**
   - 先覆盖高价值结构：horizontal、sup、sub、subsup、fraction bar、sqrt/radical body、large operator limits、alignment rows、math alphabet。
   - 横线歧义处理原则：横线节点不先验指定为分数线/根号线/overline/limit bar，而是由上下文候选关系和 verifier 判断；低置信时输出多候选，不 accepted。
   - 未覆盖结构必须显式 abstain 或进入 r2b/r3/人工复核，不能用粗糙 LaTeX fallback 冒充正确结果。

4. **r2a 集成**
   - 新模型输出进入 `formula_recognition_results`，stage/model 写清 `tinybdmath_structural` 和模型版本。
   - fusion 使用 r2a 置信度和 verifier warnings 排序。
   - `--reuse-db` 必须跳过同 input hash 的已完成 r2a。

5. **门禁与 E2E**
   - accepted/rejected/revision 表和最小命令行审核先行，UI 可后接。
   - Napkin 长文档默认打开路径不得加载训练/模型冷启动。
   - E2E 再覆盖滚动、跳转、缩放、翻译、问答和日志审计。

# TinyBDMath 神经符号公式恢复计划

最后更新：2026-06-01

本文替换旧的 TinyBDMath 研发流水账。旧文档里关于 v0 边分类器、弱
relation hint、直接 decoder 补丁、一次性性能脚本的内容只保留为历史
baseline，不再作为下一步主路线。历史细节从 git 记录查看。

## 0. 当前结论

TinyBDMath 不能继续被理解成“给每条候选边打分，然后 decoder 用几条规则
拼 LaTeX”。这个方向已经证明只能得到一个速度可用、质量不达标的候选器。

下一步应转为神经符号 inverse typesetting：

1. 先从 PDF glyph/vector/font/bbox 事实中建立符号观测图。
2. 用源码只在训练和审计阶段生成可验证的目标树，不进入真实推理路径。
3. 用可审计的对齐器把 PDF 观测图对齐到目标 Symbol Layout Tree。
4. 训练图到树的结构模型，输出父子关系、结构关系、组边界、符号身份和
   非语义节点 mask。
5. 用语法约束解码和 layout verifier 做 n-best 选择、置信校准和 abstain。
6. 只有通过 accepted gate 的结果才能写正文、FTS、向量库或 GraphRAG。

这不是硬编码规则路线。所有“谁是谁的上下标、根号主体、分数上下、矩阵
单元、文本组、非语义空白/装饰 glyph”的判断，必须来自训练标签、模型
输出、约束解码和 verifier。decoder 只允许执行通用树合法性约束和序列化，
不能写样本特化补丁。

## 1. 已知事实与失败诊断

### 1.1 已完成的有效工程资产

- 插桩训练行：Attention + Napkin 合计约 29881 条 verified rows。
- PDF 图资产：`tinybdmath_graph_rows.jsonl` 保存 glyph/vector/font/bbox 等
  结构事实。
- relation label 资产：由 KaTeX/MathML hint 和 PDF 几何生成的弱监督边标签。
- edge model 资产：已有 PyTorch edge softmax 模型，可导出主程序可读 JSON。
- 性能路径：2026-06-01 已完成 batched/vectorized relation scoring、
  compact score、direct structural decode、streaming eval。
- 主线接入：r2a TinyBDMath 结果固定 candidate-only，能落库、跳过、进入
  fusion/r3/r4/r5 但不会污染 accepted。

这些资产不能丢。它们是下一阶段 alignment、训练、评测和回归的输入。

### 1.2 当前量化基线

2026-06-01 全量 direct eval，未继续训练：

- rows = 29881。
- elapsed 约 192.59s。
- selected relations = 202364。
- structural micro precision = 0.971798。
- structural micro recall = 0.188380。
- structural micro F1 = 0.315585。
- decoded exact_match_rate = 0.523242。
- decoded near_match_rate = 0.659550。
- decoded avg_similarity = 0.839214。

结论：

- 性能链路已能支撑全量评测。
- relation precision 高但 recall 低，说明候选选择过度保守。
- decoded exact 只有约 52%，远不能作为公式恢复结果。
- 质量瓶颈不在 JSONL 读写，不应继续只优化 fast score。

### 1.3 刚验证过但不能作为终局的试验方向

全局 parent forest 约束试验能改进一部分局部错误，例如把某些根号主体重新
挂到 `RADICAL_BODY`，但它本质上仍然使用弱 relation 概率和后处理约束。

在 200 行抽样上，它能把 decoded exact 从约 0.485 提到约 0.57，把 near
从约 0.615 提到约 0.70。但它仍然无法稳定解决：

- `h_{t-1}` 这类多 token 下标。
- `d_{\text{model}}` 这类文本组。
- PDF 图中空白 glyph、装饰 glyph、layout artifact 抢 root/parent。
- 弱标签把很多 `subscript_zone` 直接变成 `SUB` 的训练噪声。
- 概率饱和到 1.0 后，约束层无法知道哪个 1.0 是真结构。

因此它只能作为 baseline 或 ablation，不能提交为“decoder bug 已修复”的最终
路线。

### 1.4 当前根因

当前模型学到的是“局部几何位置很像某种关系”，不是“公式排版树”。核心缺口：

1. 目标层不够强：只有弱边 hint，没有完整 SLT hard/soft label。
2. 对齐层不够强：没有把源码 AST/MathML 节点稳定对齐到 PDF glyph 节点。
3. 符号层不够强：PDF glyph 身份、math alphabet、文本组、非语义 glyph 没有
   统一建模。
4. 模型层不够强：pairwise edge classifier 缺少全局树、组边界和语法信息。
5. 解码层不够强：只在散乱边上做局部选择，不能做 n-best 树搜索与 verifier
   rerank。
6. 评测层不够强：relation F1 不能代表 formula-level exact；必须以 canonical
   tree / LaTeX / layout 三层一起验收。

## 2. 不可违反的边界

### 2.1 生产推理边界

- born-digital PDF 默认不走 OCR/MFR。
- 生产推理不能读取 LaTeX 源码。
- 低置信结果只能 candidate-only。
- TinyBDMath 不直接写 accepted，不直接污染正文、FTS、向量库或 GraphRAG。
- 外部重工具只能走独立 worker 或可选后端，不进入 UI 热路径。

### 2.2 算法边界

禁止：

- 为 Attention/Napkin 写样本特化正则。
- 为 `\sqrt`、`\frac`、`h_t`、`\text{model}` 等单例写 decoder 补丁。
- 用固定论文词表、固定命令表、输出字符串替换伪装识别能力。
- 只调 canonicalizer 让评测变好，却不改善结构树。
- 用 OCR 结果覆盖 born-digital 结构证据。

允许：

- 使用通用数学排版语法约束。
- 使用数据驱动的符号映射资源，例如 Unicode Math、AGL、TeX glyph list、
  OpenType MATH、font cmap。
- 使用源码生成训练/审计标签。
- 使用 verifier 对候选做可解释筛选。
- 使用模型或统计校准决定 accepted/abstain。

## 3. 目标表示

### 3.1 统一目标：Canonical Symbol Layout Tree

训练目标不应是作者源码字符串，而应是 canonical Symbol Layout Tree，简称
CSLT。

CSLT 节点类型：

- `symbol`：数学符号、字母、数字、关系符、运算符。
- `text_run`：`\text{...}`、`\operatorname{...}`、`\mathrm{...}` 中的连续文本。
- `group`：普通数学组，通常对应 `{...}` 或隐式局部结构。
- `script`：上标、下标、上下标组合。
- `fraction`：分子、分母、fraction bar 证据。
- `radical`：根号主体、可选指数、根号线/钩线证据。
- `accent`：hat、bar、tilde、vec 等。
- `under_over`：大型算子上下限、overline/underline、arrow label。
- `fence`：括号/定界符组，包括 `\left...\right` 等价结构。
- `matrix`：矩阵、cases、aligned、array，含 row/column/cell。
- `equation_number`：公式编号，默认与数学主体分离。
- `artifact`：PDF 中存在但不应进入语义 LaTeX 的节点。

CSLT 边类型：

- `next`：同一基线水平顺序。
- `child`：通用组子节点。
- `base`、`sup`、`sub`、`over`、`under`。
- `numerator`、`denominator`、`fraction_bar`。
- `radical_body`、`radical_index`、`radical_rule`。
- `accent_base`、`accent_mark`。
- `fence_open`、`fence_body`、`fence_close`。
- `matrix_row`、`matrix_cell`、`cell_content`。
- `text_char`。
- `artifact_of`。

### 3.2 为什么不用作者源码作为直接目标

作者源码有大量等价写法：

- `h_t` 与 `h_{t}`。
- `\le` 与 `\leq`。
- `\mathbb N` 与 `\mathbb{N}`。
- `\operatorname{Hom}`、`\mathrm{Hom}`、`\text{Hom}` 的局部等价。
- 宏展开前后的命令完全不同。

PDF 中通常不保存作者宏，也不保存完整 AST。直接追源码 exact 会把“源码风格”
误当成“识别质量”。训练和评测应分层：

1. CSLT exact：结构树是否正确。
2. Canonical LaTeX exact：规范化后的 LaTeX 是否等价。
3. Render/layout match：重新渲染后视觉布局是否与 PDF 观测一致。
4. Source-style match：仅用于审计，不作为生产要求。

## 4. 数据与标签路线

### 4.1 数据分层

保留并明确区分四类数据：

1. `instrumented_training_rows`：源码插桩/重编译得到的训练入口。
2. `graph_rows`：PDF glyph/vector/font/bbox 观测图。
3. `target_tree_rows`：由源码经 TeX/MathML/AST 工具生成的 CSLT 目标树。
4. `alignment_rows`：PDF 观测节点到 CSLT 节点的软/硬对齐。

旧 relation labels 只作为第五类辅助数据：

5. `weak_relation_rows`：用于 baseline、预训练或置信补充，不作为最终 hard truth。

### 4.2 目标树生成

目标树生成器必须把源码转换为 CSLT，而不是只抽 MathML hint。

候选实现顺序：

1. KaTeX parse tree：本地已有，速度快，适合作为第一版覆盖常见结构。
2. MathML 输出：用于与 W3C 表示对齐，补 Presentation MathML 结构。
3. LaTeXML 或等价 TeX AST 工具：用于处理 alignment、宏、复杂环境。
4. 自定义 CSLT normalizer：把不同工具输出统一成项目内部 schema。

目标树生成必须记录：

- source formula hash。
- macro-expanded label hash。
- parser backend/version。
- unsupported command/environment。
- canonicalization warnings。
- tree node count、edge count、depth。
- structure coverage buckets。

无法解析的源码不能丢；标记为 `target_parse_failed`，进入审计和后续工具覆盖。

### 4.3 PDF 观测图清洗

当前 graph rows 中可能包含空白、装饰、重复、不可见、颜色 marker、PDF artifact。
下一步必须在模型前新增观测节点分类：

- `semantic_symbol`：应进入公式语义。
- `text_symbol`：属于 `\text{...}` 或 operator name。
- `layout_rule`：fraction bar、radical rule、overline、underline、matrix rule。
- `fence_symbol`：括号/定界符。
- `spacing`：空白或仅排版间距。
- `marker`：训练插桩 marker，不进模型目标。
- `artifact`：PDF 残留、重复绘制、隐藏字符。
- `unknown`：暂不能判断，训练时可 ignore。

清洗不能靠样本文字规则，应使用：

- PDF draw/text facts。
- bbox 面积、可见性、颜色、字体、glyph id。
- ToUnicode/CMap/glyph name。
- OpenType MATH/font cmap。
- 文档内同字体同 CID 传播。
- 目标树对齐反馈。

### 4.4 对齐器

对齐器是下一阶段最重要的工程，不做它就不会有真正的监督。

输入：

- PDF observation graph。
- CSLT target tree。
- source/canonical LaTeX metadata。

输出：

- `pdf_node_id -> target_node_id` soft alignment。
- `target_node_id -> pdf_node_ids` group alignment。
- `alignment_confidence`。
- `ignored_pdf_nodes` 及原因。
- `unmatched_target_nodes` 及原因。
- 由对齐导出的 hard/soft relation labels。

对齐成本函数：

- 字符/Unicode/LaTeX symbol 一致性。
- glyph name、CID、font family、math alphabet 一致性。
- bbox 顺序与 target tree inorder 一致性。
- baseline/size/relative position 与 script/fraction/radical 结构一致性。
- vector line 与 fraction/radical/accent/underline 结构一致性。
- text run 连续性。
- matrix row/column 对齐一致性。
- macro-expanded canonical symbol 等价类。

算法可分阶段：

1. 叶子节点 bipartite matching。
2. 连续文本和连续数学 token 动态规划。
3. group 节点 bottom-up 聚合。
4. vector/rule 节点角色分配。
5. 对低置信冲突保留 n-best alignment。

重要原则：

- 高置信 alignment 进入 hard supervision。
- 中置信 alignment 进入 soft label 或 consistency loss。
- 低置信 alignment 不参与 hard label，只进入审计。
- 对齐失败是数据事实，不能在 decoder 中补掉。

### 4.5 标签审计

训练前必须先让标签可信。每次生成标签都输出 audit report：

- row_count。
- target_parse_success_rate。
- pdf_node_coverage。
- target_leaf_coverage。
- semantic_symbol_precision。
- artifact_rejection_rate。
- hard_relation_count。
- soft_relation_count。
- ignored_relation_count。
- per-structure coverage：sub/sup/fraction/radical/text/matrix/accent/operator/fence。
- alignment_confidence histogram。
- top failure cases。

第一阶段门槛：

- 200 行审计：人工查看 top failures，确认 schema 和报告可信。
- 2000 行审计：hard aligned rows 不低于 70%，关键结构都有样本。
- 全量审计：不要求全成功，但失败必须分桶，不能无解释地吞掉。

## 5. 模型路线

### 5.1 模型输入

每个公式区域构造成 packed graph：

节点特征：

- node type：glyph/vector/image/artifact candidate。
- Unicode/category/canonical symbol candidate。
- glyph id/CID/glyph name。
- font family、font size、font flags、math alphabet candidate。
- bbox normalized x0/y0/x1/y1/cx/cy/w/h。
- baseline、advance、line id、span id。
- identity confidence/source。
- visibility/color/marker flags。
- local reading order index。

边特征：

- dx/dy/distance/angle。
- x/y overlap、IoU、containment。
- baseline delta、font size ratio。
- nearest-neighbor ranks。
- line-of-sight direction。
- same span/line/font/page region。
- vector crossing/adjacency evidence。
- candidate structural prior。

全局特征：

- inline/display。
- formula bbox size/aspect。
- page/font health。
- unknown glyph rate。
- vector density。
- source domain bucket 仅训练审计使用，不进生产推理。

### 5.2 模型输出

不要只输出 pairwise relation。正式模型至少输出：

- node semantic mask。
- canonical symbol distribution。
- parent pointer distribution。
- relation type distribution。
- group boundary distribution。
- vector/rule role distribution。
- matrix row/column/cell distribution。
- formula-level confidence。

### 5.3 模型结构

推荐从轻到重分三档：

#### M1：Biaffine Graph Parser

用途：替换当前 pairwise edge softmax。

结构：

- MLP node encoder。
- relative geometry edge encoder。
- 2-4 层 Graph Transformer 或 attention message passing。
- biaffine parent scorer。
- relation classifier head。
- node mask head。
- symbol head。

优点：

- 能学习“每个节点只能有一个主要父节点”的全局偏好。
- 能输出 parent + relation 联合分数。
- 推理仍可批处理，不需要大型视觉模型。

#### M2：Grammar-Constrained Tree Decoder

用途：从模型分数中生成 n-best CSLT。

能力：

- arborescence/MST 或 ILP 选主树。
- relation grammar 检查。
- group/fence/script/fraction/radical/matrix 结构合法性检查。
- beam search 保留多个候选。
- 对低置信冲突 abstain。

#### M3：Verifier Reranker

用途：把 n-best CSLT 转为 canonical LaTeX/MathML 后重新验证。

Verifier 信号：

- symbol coverage。
- bbox coverage。
- geometry consistency。
- render-layout match。
- grammar legality。
- unsupported command。
- source-free confidence calibration。

### 5.4 损失函数

多任务损失：

- node semantic mask cross entropy。
- symbol identity cross entropy 或 candidate ranking loss。
- parent pointer loss。
- relation type loss。
- group boundary loss。
- vector/rule role loss。
- tree legality auxiliary loss。
- alignment soft-label KL loss。
- formula-level accepted/abstain calibration loss。

类别极不平衡必须处理：

- class-weighted loss。
- focal loss 或 hard negative mining。
- per-formula balanced sampling。
- relation family balanced batches。
- display/inline 分桶采样。

不能再让 `NO_RELATION` 和简单水平关系淹没训练。

### 5.5 高级模型是否值得

“更高级模型”不是直接上自由生成大模型。更合理的高级化顺序：

1. 先做强标签和对齐。没有强标签，模型越大越会学偏。
2. 再做 graph parser。它最匹配 PDF glyph graph 的结构。
3. 再做 constrained decoder 和 verifier。它们决定能不能 accepted。
4. 最后才考虑 LayoutLM/Donut/视觉语言模型作为 r2b/r3 辅助，不放默认路径。

原因：

- born-digital PDF 已经给出矢量和字体事实，视觉大模型会浪费信息。
- 自由 seq2seq LaTeX 难以解释和校准，容易幻觉。
- 用户最需要的是高精度 accepted，不是一个看起来流畅但不可证的字符串。

## 6. 解码与验证

### 6.1 decoder 职责

decoder 只做：

- 读取模型输出。
- 生成合法 CSLT。
- 输出 n-best canonical LaTeX。
- 保留完整 evidence。
- 标记 warnings 和 abstain reasons。

decoder 不做：

- 根据字符串写单例补丁。
- 在模型没选主体时猜根号/分数主体。
- 把低置信候选强行 canonicalize 成正确答案。
- 为提高 eval exact 改写结构事实。

### 6.2 通用语法约束

允许的约束示例：

- 每个 semantic node 最多一个主父节点。
- 树不能成环。
- `fraction` 必须有 numerator 和 denominator。
- `radical` 必须有 body，index 可选。
- `script` 必须有 base，sup/sub 至少一个。
- fence open/close 必须方向兼容。
- matrix cell 必须属于 row，row 必须属于 matrix。
- artifact/spacing 不进入 semantic serialization。

这些是数学排版通用合法性约束，不是样本硬编码。

### 6.3 verifier

Verifier 输出：

- `passed_for_candidate`。
- `passed_for_accepted`。
- `confidence_calibrated`。
- `symbol_coverage`。
- `layout_coverage`。
- `render_similarity`。
- `warnings`。
- `blockers`。

accepted 的最低条件：

- 没有 unsupported critical structure。
- semantic symbol coverage 高。
- layout/render 一致。
- 模型置信和 verifier 置信均超过门槛。
- 当前 PDF health 足够好。
- 不含高风险未解析 artifact。

达不到条件时只能 candidate-only。

## 7. 工程落地计划

### 7.1 新增/重写模块

建议文件：

- `src/core/tinybdmath_cslt_schema.py`：CSLT 节点、边、序列化 schema。
- `src/core/tinybdmath_target_tree.py`：KaTeX/MathML/LaTeXML 输出到 CSLT。
- `src/core/tinybdmath_observation_cleaner.py`：PDF 观测节点分类和清洗。
- `src/core/tinybdmath_alignment.py`：PDF graph 与 CSLT 对齐。
- `src/core/tinybdmath_alignment_audit.py`：标签审计和失败分桶。
- `src/core/tinybdmath_graph_parser.py`：PyTorch graph parser 结构。
- `src/core/tinybdmath_constrained_decode.py`：CSLT 约束解码。
- `src/core/tinybdmath_layout_verifier.py`：候选验证和置信校准。
- `src/core/tinybdmath_candidate_serializer.py`：CSLT/LaTeX/MathML 输出。

建议工具：

- `tools/tinybdmath_build_target_trees.py`。
- `tools/tinybdmath_align_targets.py`。
- `tools/tinybdmath_audit_alignment.py`。
- `tools/tinybdmath_train_graph_parser.py`。
- `tools/tinybdmath_decode_cslt.py`。
- `tools/tinybdmath_eval_formula_recovery.py`。
- `tools/run_tinybdmath_neural_symbolic_pipeline.ps1`。

旧入口保留但降级：

- `tools/run_tinybdmath_relation_pipeline.ps1` 只作为 weak-label baseline。
- `tools/tinybdmath_decode_structural_candidates.py` 只作为历史 candidate baseline。
- `tools/tinybdmath_eval_structural_candidates.py` 只评估 relation 层，不代表公式质量。

### 7.2 第一阶段：目标树和对齐审计

目标：不训练，先证明标签层可信。

任务：

1. 定义 CSLT schema。
2. 从 200 行 graph rows + label LaTeX 生成 target tree。
3. 实现 leaf alignment。
4. 实现 text run、script、fraction、radical 的 group alignment。
5. 输出 alignment report。
6. 人工查看 top 50 failures。

验收：

- target tree JSON schema 稳定。
- 每行保留 source hash、graph hash、parser backend、warnings。
- 200 行报告可解释。
- `h_{t-1}`、`\sqrt{d_k}`、`d_{\text{model}}` 这类样例能在标签层表达正确目标。
- 不要求模型准确率。

### 7.3 第二阶段：2000 行标签规模化

目标：确认标签覆盖不是靠几个样例。

任务：

1. 扩展到 2000 行。
2. 统计 sub/sup/fraction/radical/text/fence/operator/matrix coverage。
3. 按 confidence 分 hard/soft/ignore。
4. 输出可复现 manifest。
5. 对比旧 weak relation labels，列出冲突分桶。

验收：

- hard aligned rows >= 70% 或明确说明失败结构。
- 每类关键结构至少有样本和指标。
- artifact/spacing 节点不再无解释进入 hard supervision。
- 不用 decoder 补丁掩盖标签失败。

### 7.4 第三阶段：Graph Parser 训练

目标：训练 M1，不追求一步到位 accepted。

任务：

1. 用 hard labels 训练 node mask、parent pointer、relation type。
2. 用 soft labels 做辅助 KL。
3. class-balanced sampling。
4. 每 1-2 epoch 输出 formula-level dev decode。
5. 与旧 edge model 做 ablation。

初始验收：

- 2000 行 dev formula exact 明显高于旧 baseline。
- `h_{t-1}`、`\sqrt{d_k}`、`d_{\text{model}}` 不再依赖 decoder 猜。
- relation recall 提升时 precision 不崩。
- 模型可导出独立 artifact，主程序环境不强制装 PyTorch。

### 7.5 第四阶段：约束解码与 verifier

目标：把模型输出变成可校准候选。

任务：

1. 实现 CSLT constrained decoder。
2. 实现 n-best beam。
3. 实现 layout verifier。
4. 输出 candidate/evidence/warnings/blockers。
5. 旧 LaTeX decoder 改为 CSLT serializer，不再直接消费散边。

验收：

- formula-level exact/near 用新 eval 评估。
- verifier 能拒绝明显错误候选。
- accepted gate 默认仍关闭或极保守。
- 所有候选保留 bbox/source-free evidence。

### 7.6 第五阶段：主线 r2a 集成

目标：替换 r2a 内部候选生成，但保持生产安全边界。

任务：

1. `TinyBDMathCandidateService` 支持新 graph parser artifact。
2. 写入 `formula_recognition_results` 的 evidence JSON：
   - graph schema version。
   - target/model/decode/verifier version。
   - selected CSLT。
   - n-best candidates。
   - confidence。
   - verifier report。
   - abstain reasons。
3. fusion 只把它作为候选排序依据。
4. accepted 仍由审核/门禁控制。

验收：

- Attention/Napkin 限页 pipeline 可跑。
- 二次打开同 input hash 跳过。
- 不加载 OCR/MFR。
- 不污染正文、FTS、向量库、GraphRAG。

### 7.7 第六阶段：全量评估与校准

目标：给出可信的质量结论。

评估维度：

- formula canonical exact。
- formula near。
- CSLT tree edit distance。
- render/layout match。
- per-structure exact。
- accepted precision。
- accepted coverage。
- abstain rate。
- latency P50/P95。
- cache skip rate。

门槛：

- 先超过旧 direct eval exact=0.523/near=0.660。
- 再超过 r0/fusion baseline。
- 再开极保守 accepted profile。
- accepted precision 没有足够样本和置信区间前，不宣称 99.9%。

## 8. 具体执行清单

### 8.1 立即做

1. 冻结旧 edge/decoder 试验为 baseline，不继续在旧 decoder 上写补丁。
2. 新建 CSLT schema。
3. 新建 target tree builder。
4. 新建 alignment report，先跑 200 行。
5. 把 200 行失败样例分桶写入报告。

### 8.2 跑通后做

1. 扩展到 2000 行。
2. 实现 Graph Parser M1。
3. 训练 2000 行 smoke。
4. 新建 formula-level eval。
5. 与旧 direct eval 对比。

### 8.3 再之后做

1. 全量训练。
2. verifier/reranker。
3. r2a 接入。
4. Attention/Napkin 限页 E2E。
5. 全量质量报告。
6. 极保守 accepted profile。

## 9. 需要删除或停止依赖的旧思路

以下内容不再作为主路线：

- 单纯 pairwise edge softmax 当最终模型。
- 只看 relation F1 就判断公式识别完成。
- 在 decoder 里补根号主体、分数主体、上下标组。
- 通过 canonicalizer 放宽 exact 来掩盖结构错误。
- 为性能继续堆中间 JSONL 和评分脚本，而不改标签/模型。
- 用 OCR/MFR 解决 born-digital 公式主问题。
- 把 LaTeX 源码风格 exact 当成生产目标。

保留用途：

- 旧 edge model：baseline、pretrain、ablation。
- 旧 relation labels：weak supervision、错误分析。
- 旧 direct eval：性能和质量回归基线。
- 旧 structural candidate：fallback candidate，不 accepted。

## 10. 风险与应对

### 10.1 源码标签泄漏

风险：训练工具无意把源码 token 作为推理特征。

应对：

- 训练 row 明确区分 input graph 和 target。
- 推理 API 只接受 PDF graph。
- 单元测试验证 production path 不读取 source/label 字段。

### 10.2 Parser 覆盖不足

风险：KaTeX/MathML 无法解析复杂 TeX 环境。

应对：

- 解析失败进入分桶。
- 引入 LaTeXML 或其他 AST 后端补覆盖。
- 不把 parser failure 训练成错误 hard label。

### 10.3 PDF artifact 污染

风险：空白 glyph、marker、重复绘制、不可见节点进入语义树。

应对：

- 单独训练 node semantic mask。
- alignment 审计 artifact rejection。
- verifier 检查 semantic coverage 与 artifact blockers。

### 10.4 Text run 和 operator name

风险：`model`、`Hom`、`Spec` 等文本被拆成错误下标或变量序列。

应对：

- CSLT 有 `text_run`。
- alignment 支持连续文本组。
- 模型输出 group boundary。
- eval 单独统计 text run integrity。

### 10.5 Matrix/cases/alignment

风险：复杂二维布局不是简单树。

应对：

- CSLT 显式建 `matrix/row/cell`。
- 第一阶段可先 ignore 低置信 matrix，不硬解。
- 后续引入行列聚类和 cell head。

### 10.6 accepted 误污染

风险：低置信公式进入正文或知识库。

应对：

- r2a 默认 candidate-only。
- accepted gate 单独校准。
- r5 只消费 accepted 或人工 revision。

## 11. 推荐命令草案

以下命令是计划中的目标形态，具体参数以实现为准：

```powershell
# 1. 生成目标树
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe `
  tools\tinybdmath_build_target_trees.py `
  --graph-rows test_artifacts\tinybdmath_graph_unique_color_components_v3_20260601\tinybdmath_graph_rows.jsonl `
  --limit 200 `
  --output-dir test_artifacts\tinybdmath_cslt_target_200

# 2. 对齐并审计
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe `
  tools\tinybdmath_align_targets.py `
  --graph-rows test_artifacts\tinybdmath_graph_unique_color_components_v3_20260601\tinybdmath_graph_rows.jsonl `
  --target-trees test_artifacts\tinybdmath_cslt_target_200\target_trees.jsonl `
  --output-dir test_artifacts\tinybdmath_alignment_200

C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe `
  tools\tinybdmath_audit_alignment.py `
  --alignment-rows test_artifacts\tinybdmath_alignment_200\alignment_rows.jsonl `
  --output test_artifacts\tinybdmath_alignment_200\alignment_audit.json

# 3. 训练 graph parser smoke
C:\Users\WYK\.conda\envs\science\python.exe `
  tools\tinybdmath_train_graph_parser.py `
  --alignment-rows test_artifacts\tinybdmath_alignment_2000\alignment_rows.jsonl `
  --output-dir test_artifacts\tinybdmath_graph_parser_2000

# 4. 公式级评估
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe `
  tools\tinybdmath_eval_formula_recovery.py `
  --graph-rows test_artifacts\tinybdmath_graph_unique_color_components_v3_20260601\tinybdmath_graph_rows.jsonl `
  --model test_artifacts\tinybdmath_graph_parser_2000\model_export.json `
  --output-dir test_artifacts\tinybdmath_formula_eval_2000
```

## 12. 接手时的判断标准

下一位接手者不要先训练，也不要先调 decoder。先问三个问题：

1. CSLT 目标树是否能表达当前失败样例？
2. PDF graph 到 CSLT 的 alignment 是否可信？
3. 标签审计是否证明 hard labels 足够干净？

这三个答案为“是”之前，继续训练更大模型没有意义；继续改 decoder 只会把
错误藏得更深。

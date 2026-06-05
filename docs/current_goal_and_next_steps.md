# 当前目标与下一步

最后更新：2026-06-05

本文是新会话接手时的短入口。历史流水账已从本文删除，避免把过期状态、
旧指标和临时路线误当成下一步计划。TinyBDMath 的白话版执行方案见
`docs/tiny_born_digital_math_model_engineering.md`。AI/Math 第一阶段的
LaTeX 数学排版结构范围见
`docs/tinybdmath_ai_math_latex_structure_scope.md`。

## 1. 当前真实目标

把 PDF AI Reader 做成高性能、可审计、可长期维护的论文阅读和全文理解工具。
当前最重要的未完成部分是 born-digital 公式恢复质量，而不是继续堆 OCR 或
只优化评分脚本。

核心目标：

1. born-digital PDF 默认走 PDF 结构事实：文本层、glyph、font、bbox、vector、
   ActualText、CMap、ToUnicode、OpenType MATH。
2. 图片/扫描公式才走 OCR/MFR，并且必须后台化、缓存化、可暂停、可恢复。
3. 公式结果必须能追溯到页码、bbox、PDF graph、模型版本、候选证据和审核状态。
4. RAG/GraphRAG 只消费 accepted 或人工 revision 的高置信内容。
5. 打开、滚动、缩放、翻译和基础问答不能等待公式重任务。

第一阶段收束为 AI/Math 论文中的数学排版结构恢复。数学公式数量无限，不能
枚举公式类型；要恢复的是 LaTeX/amsmath/mathtools/unicode-math 这类系统
生成的有限二维排版组合器，例如 sequence、script、under/over、fraction、
radical、accent、fence、matrix/aligned、operator、text run、style/mathvariant
和 equation tag。化学结构图、TikZ/tikz-cd、proof tree、算法伪代码和复杂图示
先做 route/unsupported/abstain，不进入第一阶段普通公式 CSLT。

## 2. 当前代码与提交状态

最近已提交的主线成果：

- `5b5b38e Optimize TinyBDMath relation scoring pipeline`
  - PyTorch batch/vectorized relation scoring。
  - compact score。
  - direct structural decode。
  - no-score-jsonl。
  - streaming structural/decoded eval。
  - 全量 direct eval：rows=29881，structural F1=0.315585，
    decoded exact=0.523242，near=0.659550。

- `a258fe2 Clean legacy TinyBDMath tooling and docs`
  - 删除旧 `realdata` 和旧 scoring 入口脚本。
  - 清理旧缓存/日志/临时 benchmark 输出。
  - 新增 `docs/workspace_inventory.md`。
  - 更新 README/AGENTS/TODO/交接文档。

- `c75d919 Add TinyBDMath graph parser pipeline`
  - 新增 CSLT schema、target tree、PDF graph 到 CSLT alignment。
  - 新增 Graph Parser M1 训练和纯 Python 推理 artifact。
  - r2a 主路径切到 Graph Parser artifact；缺模型时 abstain。

- `a77a37b Clean legacy TinyBDMath scope`
  - 提交节点保留/丢弃学习、节点分类头和结构监督收口。
  - 新增 AI/Math LaTeX 结构范围文档。
  - 删除旧 TinyBDMath quality/edge/sharded/gold/review 路线、孤立历史文档和旧脚本入口。

- `a5091f8 Add AI dock panel controls`
  - `src/ui/main_window.py` 右侧 `AI 工具集` dock 去掉关闭按钮，保留移动和浮动能力。
  - 主工具栏新增 `right_panel_toggle_button`，可隐藏/显示右侧 AI 面板，并保存用户展开宽度。
  - 自定义 dock 标题栏新增 `right_dock_float_button`，支持弹出独立窗口和归位右侧栏。
  - `tests/test_smoke.py` 覆盖 `main_toolbar`、toggle 文案、dock 不可关闭和 float/restore。
  - 已补跑 `tests/test_smoke.py -q`：11 passed；完整轻量接手测试尚未重跑完成。

2026-06-02 当前已在 Graph Parser M1 上提交“节点保留/丢弃”学习：

- `src/core/tinybdmath_graph_parser.py`
  - 增加节点分类头：`SYMBOL`、`TEXT`、`OPERATOR`、`SPACING`、`UNKNOWN`、
    `HORIZONTAL_RULE`、`VERTICAL_RULE`。
  - 节点特征加入 Unicode 大类、单字符标记、相对字号等通用证据。
  - 推理输出 `node_predictions` 和模型携带的 `node_filter_threshold`。
- `tools/tinybdmath_train_graph_parser.py`
  - 同时训练关系头和节点头。
  - 按 alignment confidence 做样本加权，避免弱 `UNKNOWN` 标签被当成强答案。
  - 节点头对强标签类使用 class weight，但不放大弱 `UNKNOWN`。
- `src/core/tinybdmath_target_tree.py` / `src/core/tinybdmath_alignment.py`
  - target tree 保存 KaTeX `family` 等结构属性。
  - alignment 将 `target_node_type`、`target_attrs` 写入节点对齐记录。
- `src/core/tinybdmath_latex_decoder.py`
  - 只按 Graph Parser 高置信 `SPACING` 节点过滤 glyph/relation。
  - 不写字符表，不按具体符号补规则。

r2a 只加载 Graph Parser artifact；缺少 Graph Parser artifact 时会写 candidate-only
abstain 和缺模型 warning。

`测试资料/` 是用户资料，不提交、不清理、不移动。

当前主窗口右侧 AI 工具集仍是全文问答/证据/回答/追问入口；2026-06-05 起它可以
通过主工具栏按钮隐藏/显示，也可以从自定义 dock 标题栏弹出为独立窗口或归位到右侧栏。
这只是阅读界面可用性更新，不改变公式多轮索引、RAG/GraphRAG 或 accepted gate 边界。

## 3. 关键结论

### 3.1 性能不是当前主瓶颈

2026-06-01 的 full direct eval 已证明：

- scoring/eval 可以在全量 29881 行上运行。
- JSONL 中间文件和逐边 Python 循环已经不是唯一瓶颈。
- 继续只做 fast score 优化，不会把 decoded exact 从 52% 提到可用水平。

下一步必须改监督、模型、解码和 verifier。

### 3.2 Decoder 不能补模型缺口

在 decoder 里修 `\sqrt{d_k}`、`h_{t-1}`、`d_{\text{model}}` 这类个案，看起来
能提高少数样例，但本质是把模型和标签没学到的结构塞进后处理。

正确方向：

- PDF 里的每个小字、小符号、小横线都要进入模型可学习的证据。
- 模型必须直接判断保留/丢弃、符号身份、父子关系、组边界和结构线条角色。
- decoder 只做通用合法性检查和 LaTeX 序列化。
- 低置信时拒绝或只给候选，不用字符串补丁硬凑。

### 3.3 更高级模型的前提是更高级标签

直接换大模型或自由生成 LaTeX 不是最佳路线。born-digital PDF 已经有结构事实，
应先建立能让模型学结构的训练目标：

- Canonical Symbol Layout Tree。
- PDF graph 到 CSLT 的软/硬对齐。
- node semantic mask。
- graph parser。
- constrained decoder。
- layout verifier。

没有这些，模型变大只会更快学到弱标签噪声。

## 4. 不可违反边界

- 不用 OCR/MFR 解决 born-digital PDF 主问题。
- 不写样本特化正则、固定论文词表、输出字符串替换。
- 不在 TinyBDMath 对齐器、decoder 或后处理里维护本地 TeX/Unicode 符号表。
- 符号等价只能来自 KaTeX/MathML/TeX AST、PDF identity repair、font/cmap、
  AGL/TeX glyph list、OpenType MATH 等统一证据层。
- 不在 decoder 中猜根号主体、分数主体、上下标组。
- 不把低置信 TinyBDMath 输出写 accepted。
- 不把候选直接写正文、FTS、向量库或 GraphRAG。
- 不把外部重工具混进主环境。
- 不提交 `测试资料/`、日志、缓存、临时 benchmark 输出。
- 提交信息不加任何额外署名或生成工具署名。

## 5. 下一步唯一主线

下一步主线是：该模型学
的就放进模型学，后处理只负责整理和拒绝低置信结果。

```text
PDF 小块证据
  -> 训练标签
  -> 模型直接输出公式结构
  -> 通用合法性检查
  -> 低置信拒绝或 candidate-only
  -> 人工/门禁 accepted 后再进知识库
```

详细设计见 `docs/tiny_born_digital_math_model_engineering.md`。

## 6. 近期执行顺序

### Step 1：冻结当前边界

任务：

- r2a 只加载 Graph Parser artifact。
- decoder 只做结构序列化和合法性检查。
- 低置信结果 candidate-only 或 abstain。
- 提交前撤掉不在当前主线上的试验入口。

验收：

- 文档写清旧路线边界。
- git diff 中不混入“看似修好 bug 但实为后处理补丁”的改动。

### Step 2：定义 CSLT schema（M1 已完成）

任务：

- 新增 `src/core/tinybdmath_cslt_schema.py`。
- 支持 symbol、text_run、group、script、fraction、radical、accent、
  under_over、fence、matrix、equation_number、artifact。
- 支持 canonical JSON 序列化和 hash。
- 增加 schema 单元测试。

验收：

- `h_{t-1}`、`\sqrt{d_k}`、`d_{\text{model}}`、简单 fraction、
  简单 matrix 都能被 schema 表达。
- schema 不依赖源码字符串风格。

2026-06-02 结构范围已升级为 AI/Math LaTeX M0。后续不要把“支持哪些公式”
写成清单，必须按排版组合器审计：

- 基础：symbol、text_run、sequence、group、spacing_artifact。
- 附着：script、prescript、under_over、accent_annotation。
- 分隔/包围：fraction、radical、fence、基础 enclosure evidence。
- 网格/多行：matrix_grid、aligned_display、equation_tag。
- 身份/样式：operator、style_variant、mathvariant、font_identity。

下一步在训练前先做 100 个 AI/Math 公式的结构覆盖审计，输出
`ready_for_model`、`needs_identity_repair`、`needs_schema_extension`、
`needs_image_mfr`、`route_unsupported`、`abstain` 分桶。

2026-06-03 已完成第一轮 100 行审计工具和 smoke，并把审计口径收敛为
parser/target/alignment/PDF evidence 驱动，不再按 LaTeX 字符串模式推断
schema 缺口：

- 新增 `tools/tinybdmath_audit_structure_scope.py`。
- target tree 输出新增 parser summary，记录 KaTeX type/family 与 MathML tag
  计数，供审计归因使用，不进入生产推理。
- 输出 `test_artifacts/tinybdmath_structure_scope_audit_100.json`，不提交。
- 结果：`ready_for_model=84`、`needs_identity_repair=15`、
  `needs_schema_extension=1`。
- 已补一个通用 schema 小步：KaTeX parse tree 中的 `cr` 行断点会生成
  `aligned_display`/`matrix_grid` target container。
- 2026-06-03 已补标准 KaTeX AST 结构：large operator/operatorname limits
  生成 `under_over`，`operatorname` 生成 operator `text_run`，overline/underline/
  horizBrace 生成 `accent`，boxed/fbox 生成 enclosure evidence，left/right/middle
  和 sized delimiter 保留 fence/delimiter 证据，equation tag 生成
  `equation_number`，phantom/smash/lap/color 保留 layout/style evidence。
- 2026-06-03 已把标准结构监督继续接入 alignment 和 Graph Parser：relation
  labels 接住 `UNDER`、`OVER`、`ACCENT_BASE`，structure labels 新增
  `TARGET_TEXT_RUN_EVIDENCE`、`TARGET_OPERATOR_TEXT_RUN_EVIDENCE`、
  `TARGET_UNDER_OVER_EVIDENCE`、`TARGET_ACCENT_ANNOTATION_EVIDENCE`、
  `TARGET_FENCE_EVIDENCE`、`TARGET_MATRIX_GRID_EVIDENCE`、
  `TARGET_MATRIX_ROW_EVIDENCE`、`TARGET_MATRIX_CELL_EVIDENCE`、
  `TARGET_ENCLOSURE_EVIDENCE`、`TARGET_EQUATION_TAG_EVIDENCE`。Graph Parser
  训练样本能消费 `UNDER`、`OVER`、`TEXT_RUN_NEXT`、`RADICAL_BODY`、
  `FENCE_OPEN`、`FENCE_CLOSE`、`FENCE_BODY`、`MATRIX_ROW`、`CELL_CONTENT`
  和 over/under line vector evidence；operator text run 进入 `OPERATOR` 节点标签。
  decoder 只序列化已支持的 `UNDER`/`OVER`，其余暂未支持关系会降为 warning 和
  低置信，不用于补洞。
- 复跑 100 行后 `under_over` 已在真实样本中覆盖；fraction/radical/matrix/
  aligned 虽已出现，但本批仍未进入 `ready_for_model` 分桶，后续先修
  PDF-to-target identity/alignment。
- 新增 `tests/test_tinybdmath_no_hardcoded_patterns.py`，用 AST/源码扫描和
  synthetic 反例验证，阻止 TinyBDMath 主线再次引入宏注入、正则解析、
  LaTeX 命令分支或样本术语硬编码。
- 当前 schema blocker 只剩 1 个未包裹 display alignment parse failure；
  其余 blocker 主要是 PDF-to-target identity/alignment，不得在 decoder 中硬补。

### Step 3：生成 target tree

M1 已完成：

- 新增 `src/core/tinybdmath_target_tree.py`。
- 新增 `tools/tinybdmath_build_target_trees.py`。
- 从 graph rows 的 `label_latex/raw_source_latex` 只在训练/审计阶段生成 CSLT。
- 第一版复用 KaTeX parse tree。
- 复杂失败样例记录 parser warning，不丢行。

已跑 smoke：

- 20 行真实 graph rows：success_rows=20，failed_rows=0。
- warning：`target_tree_unsupported_katex_type:font` 4 次，已分桶。

### Step 4：实现 alignment

M1 已完成：

- 新增 `src/core/tinybdmath_alignment.py`。
- 新增 `tools/tinybdmath_align_targets.py`。
- 实现 PDF leaf 到 CSLT leaf 的 matching。
- 实现 script/radical/text/artifact 的初版 hard/soft/ignore 标签。
- 输出 hard/soft/ignore 标签。

已跑 smoke：

- 20 行真实 graph rows：rows_with_hard_labels=13，avg_hard_alignment_rate=0.894048。
- warning：`alignment_low_hard_coverage` 3 次，`alignment_unmatched_target_nodes` 1 次。
- relation_counts：BASE=8、CHILD=43、NEXT=43、SUB=12。

2026-06-02 重新按“无 TinyBDMath 本地符号表”边界跑了 200 行审计：

- 输出目录：`test_artifacts/tinybdmath_graph_parser_audit_200_no_local_symbol_map/`
  仅为临时产物，不提交。
- target tree：rows=200，success_rows=199，failed_rows=1。
- alignment：rows_with_hard_labels=148，avg_hard_alignment_rate=0.940763。
- audit：hard_row_rate=0.915，relation_row_rate=0.74，gate passed。
- 主要失败桶：`\cong`/`\mapsto` 等复合 glyph、`\cdot`/根号 glyph、
  KaTeX alignment 源码解析失败、个别 text/operator 顺序冲突。
- 处理原则：这些失败必须进入 PDF identity repair、font/cmap、MathML/AST
  target alias 或观测图清洗层；禁止回到 alignment/decoder 写局部符号表。

### Step 5：扩大到 2000 行（已完成）

2026-06-02 已完成 2000 行无 TinyBDMath 本地符号表审计：

- 临时目录：`test_artifacts/tinybdmath_graph_parser_audit_2000_no_local_symbol_map/`
  不提交。
- target tree：rows=2000，success_rows=1993，failed_rows=7。
- alignment：rows_with_hard_labels=1269，avg_hard_alignment_rate=0.950611。
- audit：hard_row_rate=0.9395，relation_row_rate=0.6345，gate passed。
- 主要失败桶：KaTeX alignment 环境解析失败、复合 glyph、点乘/箭头/根号等
  identity 证据不足、少量文本块和 operator 顺序冲突。

验收：

- hard aligned rows 已超过 70%。
- 未覆盖结构进入 unmatched/ignore/warning，不作为硬标签。
- relation_row_rate 仍偏低，说明还需要补 group/text/operator/vector role 标签，
  不能直接长训后宣称完成。

### Step 6：训练 Graph Parser M1（节点头已接入，2000 行短训已完成，正式全量未完成）

已完成：

- 新增 `src/core/tinybdmath_graph_parser.py`。
- 新增 `tools/tinybdmath_train_graph_parser.py`。
- 用 PyTorch 训练 relation type / parent edge 初版。
- 新增节点头，训练每个 PDF 小块是语义节点、空白/间距，还是不确定。
- 处理类别不平衡和弱证据：关系头保留 class weight，节点头按 confidence 加权，
  不把弱 `UNKNOWN` 标签放大成默认答案。
- 导出主程序可读 artifact。
- artifact 现在携带 `node_filter_threshold`，主程序推理不依赖 PyTorch。

已跑 smoke：

- `science` 环境 PyTorch 2.5.1 可用。
- 20 行 alignment、2 epochs、CPU 训练成功，导出 `tinybdmath_graph_parser_model.json`。
- smoke validation accuracy 约 0.527、positive_recall 约 0.389；这只证明链路可跑，不代表质量达标。
- 200 行 role-label smoke 已重建 target tree/alignment，临时目录：
  `test_artifacts/tinybdmath_graph_parser_role_labels_smoke/`，不提交。
- role-label smoke 的节点标签分布：OPERATOR=196、SPACING=336、SYMBOL=711、
  TEXT=96、UNKNOWN=252。
- 同一 smoke 的关系头 validation accuracy=0.763872、positive_recall=0.688172。
- 节点头 validation accuracy=0.642857；recall：OPERATOR=0.46875、
  SPACING=1.0、SYMBOL=0.684685、TEXT=1.0、UNKNOWN=0.0；precision：
  OPERATOR=0.681818、SPACING=0.92、SYMBOL=0.697248、TEXT=0.280702。
- 结论：TEXT/OPERATOR 标签和训练链路已接通，但 TEXT precision 偏低、UNKNOWN
  暂不预测；这仍是 smoke，不是正式质量结论。

2026-06-03 已用当前结构标签重跑 2000 行短训 smoke：

- 临时目录：`test_artifacts/tinybdmath_graph_parser_smoke_current_20260603/`，
  不提交。
- target tree：2000 行，1993 成功，7 失败。
- alignment：rows_with_hard_labels=1270，avg_hard_alignment_rate=0.951501，
  rows_with_structure_labels=167。
- structure_counts 已出现 text run、operator text run、fraction/radical、
  accent、fence、matrix、under_over 结构监督。
- 训练样本：relation samples=90979，node samples=14729。
- relation validation：accuracy=0.818262，positive_recall=0.405096。
- node validation：accuracy=0.761431；OPERATOR recall=0.946903、
  precision=0.550600；TEXT recall=0.900000、precision=0.095745。
- 公式级 decoded eval：rows=2000，exact_match_rate=0.569500，
  near_match_rate=0.710500，weak_match_rate=0.924500，
  average_similarity=0.866203。
- 对比 2026-06-01 旧 direct eval（exact≈0.523、near≈0.660），短训已有提升，
  但 decoder warning 仍多，TEXT precision 很低，不能 accepted，只能作为
  candidate-only/verifier 下一步基线。

2026-06-03 已接入 layout verifier M0 并重跑同一 2000 行 decoded eval：

- 整体 decoded 指标不变：exact=0.569500，near=0.710500，
  weak=0.924500，average_similarity=0.866203。
- verifier gate：pass=557，review=935，abstain=508，final_abstain_rate=0.254。
- 未拒绝子集：exact=0.726542，near=0.819035。
- constrained decode 接入后 gate：pass=557，review=859，abstain=584，
  final_abstain_rate=0.292；未拒绝子集 exact=0.748588，near=0.831215。
- 2026-06-03 已把 Graph Parser relation alternatives 透传为 n-best CSLT
  candidate evidence，并在 decoded evidence 中输出 canonical CSLT、
  `n_best_cslt` 和 rank-1 `latex_candidates`；同一 2000 行评估 exact/near
  与 gate 指标保持不变。该路径只扩展候选证据，不新增 LaTeX 模板规则。
- 2026-06-03 接续会话修复了重复 LaTeX candidate 证据合并的确定 bug：
  同样 LaTeX 的无环投影等更干净结构会写入 rank-1 的
  `alternative_structure_evidence`，但 selected graph 不再作为自身替代证据
  重复写入。814 行审计指标保持 rank-1 exact/near=0.515971/0.687961，
  n-best oracle exact/near=0.527027/0.787469。
- 2026-06-03 已继续补通用结构序列化：`FENCE_OPEN`、`FENCE_CLOSE`、
  `FENCE_BODY`、`TEXT_RUN_NEXT`、`MATRIX_ROW`、`MATRIX_CELL`、
  `CELL_CONTENT`、`ACCENT_BASE` 进入 decoder/constrained decode/layout
  verifier 的可序列化路径。TEXT_RUN 只在 node head 对链上节点给出高置信
  `TEXT`/`OPERATOR` 时包 `\text{...}`，否则只作为普通顺序关系。814 行
  gated-text 审计：rank-1 exact/near=0.531941/0.680590，n-best oracle
  exact/near=0.540541/0.766585，pass_or_review exact/near=0.711504/0.821239。
- 2026-06-03 按 M0/M1 结构清单继续补齐支持，而不是按失败样例决定路线：
  `prescript`/左附着通用关系已接入 CSLT、alignment、Graph Parser、
  decoder、constrained decode 和 layout verifier；`radical_index` 已完成
  nth-root index 监督与序列化；operator text run 现在由 node head 高置信
  `OPERATOR` 输出 `\operatorname{...}`，高置信 `TEXT` 仍输出 `\text{...}`；
  `matrix_grid` 训练监督和 decoder 已按 row/cell/content 三层语义修正。
  这些改动只消费模型/target-derived 结构关系，不按公式内容、函数名或样本词表
  分支。focused 结构测试 82 passed，TinyBDMath 主线 129 passed。
- 修复了 decoder 对完整 rule structure 的一个确定 bug：分数/overline 等结构已消费
  全部 glyph 后，不再回退遍历所有 glyph 并误报 `decoder_no_root`/`decoder_cycle`。
- 该 gate 只影响 candidate 可信度和 abstain/review/pass 统计，不写 accepted。

正式验收仍是：

- 2000 行 dev formula exact/near 明显超过旧 direct eval 抽样基线。
- 失败样例不依赖 decoder 猜。
- 关系 recall 提升时 precision 不崩。

### Step 7：CSLT constrained decoder + verifier（layout verifier M0 已接入）

已完成：

- 新增 `src/core/tinybdmath_constrained_decode.py`，只验证模型输出结构图的
  schema、缺失节点、cycle、coverage 和 blockers，不生成 LaTeX 模板。
- 新增 `src/core/tinybdmath_layout_verifier.py`。
- `decoded_latex` evidence 输出 `abstain`、`layout_status`、
  `layout_confidence` 和 `layout_verification`。
- `TinyBDMathCandidateService` 的 r2a recognition score 使用 verifier
  `layout_confidence`，仍然 `accepted=false`。
- Graph Parser prediction evidence 已输出 relation alternatives；constrained
  decode 将其整理成 canonical CSLT 和 n-best CSLT candidate evidence。
- 重复 LaTeX 的替代结构证据会合并进同一 candidate，供人工审核比较结构
  证据；rank-1 仍保持 selected graph，不自动 accepted。
- decoder 已序列化基础 fence、text/operator run、matrix row/cell/content、
  left attachment、radical index、accent base 关系；
  未满足模型证据门控的结构仍保守降级为 candidate-only。
- `tools/tinybdmath_eval_decoded_latex.py` 输出 `layout_gate` 统计。
- `tests/test_tinybdmath_layout_verifier.py` 已覆盖 pass/review/abstain 基础门控。

仍需完成：

- 继续基于 n-best CSLT 做 verifier 排序/abstain 校准，而不是补样本模板。
- 继续校准 TEXT precision、decoder warning、n-best 排序和 verifier 阈值。

验收：

- verifier 能拒绝明显错误候选。
- 低置信输出仍 candidate-only。
- accepted gate 默认关闭或极保守。

### Step 8：r2a 集成与全量评估

M1 已完成：

- `TinyBDMathCandidateService` 支持并要求 Graph Parser artifact 作为 r2a 主路径。
- 缺少 Graph Parser artifact 时写 abstain。
- evidence JSON 写入 graph_parser、relation_scoring、structural_candidate、decoded_latex。
- `tools/formula_multiround_pipeline.py` 新增 `--tinybdmath-graph-parser-model`。
- `tools/full_software_validation.py` 默认寻找 Graph Parser artifact。

仍需完成：

- Attention/Napkin 限页 pipeline 验证二次打开跳过。
- 全量评估 formula-level exact/near/tree edit/render match。

验收：

- 不加载 OCR/MFR。
- 不污染正文/RAG/GraphRAG。
- 质量报告超过旧 direct eval，且分项可解释。

## 7. 当前推荐命令

轻量测试基线：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest `
  tests/test_external_formula_tools.py `
  tests/test_formula_index_flow.py `
  tests/test_born_digital_math.py `
  tests/test_formula_semantic_review.py `
  tests/test_smoke.py -q
```

TinyBDMath Graph Parser M1 测试：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest `
  tests/test_tinybdmath_symbol_equivalence.py `
  tests/test_tinybdmath_cslt_schema.py `
  tests/test_tinybdmath_target_tree.py `
  tests/test_tinybdmath_alignment.py `
  tests/test_tinybdmath_alignment_audit.py `
  tests/test_tinybdmath_graph_parser.py `
  tests/test_tinybdmath_latex_decoder.py `
  tests/test_tinybdmath_layout_verifier.py `
  tests/test_tinybdmath_constrained_decode.py `
  tests/test_tinybdmath_eval_decoded_latex.py `
  tests/test_tinybdmath_candidate_service.py `
  tests/test_tinybdmath_no_hardcoded_patterns.py `
  tests/test_tinybdmath_structure_scope_audit.py `
  tests/test_formula_multiround_pipeline.py `
  tests/test_full_software_validation.py -q
```

20 行 smoke 命令：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\tinybdmath_build_target_trees.py `
  --graph-rows test_artifacts\tinybdmath_graph_unique_color_components_v3_20260601\tinybdmath_graph_rows.jsonl `
  --output-dir test_artifacts\tinybdmath_graph_parser_smoke\target_trees `
  --limit 20

C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\tinybdmath_align_targets.py `
  --graph-rows test_artifacts\tinybdmath_graph_unique_color_components_v3_20260601\tinybdmath_graph_rows.jsonl `
  --target-trees test_artifacts\tinybdmath_graph_parser_smoke\target_trees\tinybdmath_target_trees.jsonl `
  --output-dir test_artifacts\tinybdmath_graph_parser_smoke\alignment `
  --limit 20

C:\Users\WYK\.conda\envs\science\python.exe tools\tinybdmath_train_graph_parser.py `
  --graph-rows test_artifacts\tinybdmath_graph_unique_color_components_v3_20260601\tinybdmath_graph_rows.jsonl `
  --alignment-rows test_artifacts\tinybdmath_graph_parser_smoke\alignment\tinybdmath_alignment_rows.jsonl `
  --output-dir test_artifacts\tinybdmath_graph_parser_smoke\model `
  --limit 20 --epochs 2 --batch-size 128 --hidden-units 32 --hidden-layers 1 --device cpu
```

2000 行无本地符号表审计已跑过。下一步不要直接长训；先按
`docs/tinybdmath_ai_math_latex_structure_scope.md` 做 AI/Math LaTeX 结构覆盖
审计，再决定补哪些 group/text/operator/vector role 标签。临时产物仍只放
`test_artifacts/`，不提交。

## 8. 停止做的事

- 不在 decoder 里补模型没学到的结构。
- 不继续用 canonicalizer 掩盖结构错误。
- 不继续只追 scoring 吞吐。
- 不继续把 relation precision 当公式准确率。
- 不继续用 OCR/MFR 兜 born-digital 主问题。
- 不继续在文档里堆历史执行日志。

## 9. 提交前检查

提交前必须：

```powershell
git status --short
rg -n "额外署名|来源标记|生成工具署名|自动署名" . -S `
  -g '!测试资料/**' -g '!test_artifacts/**' -g '!logs/**'
```

不要提交：

- `测试资料/`
- `test_artifacts/`
- `logs/`
- `.pytest_cache/`
- 模型缓存
- 临时 benchmark 输出

## 10. 判断是否继续训练

除非同时满足以下条件，否则不要继续长时间训练：

1. 目标公式结构树已生成并审计。
2. PDF 小块到目标结构的标签已通过 200/2000 行审计。
3. hard/soft/ignore 标签分层明确。
4. 2026-06-01 direct eval 对照指标已固定。
5. 节点头、关系头、后续组边界/vector role 标签都进入同一个评估报告。
6. 训练目标是 graph parser。

如果这些条件不满足，训练一晚上只会放大错误标签和后处理补丁的问题。

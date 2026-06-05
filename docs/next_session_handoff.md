# 新会话交接文档

最后更新：2026-06-05

本文件是新终端/新 AI 助手接手项目时的交接入口。旧版长篇流水账已删除。
当前真实下一步以本文、`TODO.md` 和
`docs/current_goal_and_next_steps.md` 为准。

## 1. 必读顺序

1. `AGENTS.md`
2. `docs/current_goal_and_next_steps.md`
3. `docs/tiny_born_digital_math_model_engineering.md`
4. `docs/tinybdmath_ai_math_latex_structure_scope.md`
5. `docs/async_formula_indexing_design.md`
6. `docs/workspace_inventory.md`
7. `TODO.md`

不要先安装工具，不要先训练，不要先改 decoder。先确认工作树、环境、后台
进程和当前文档边界。

## 2. 当前真实状态

- 工作目录：`D:\程设大作业`。
- 主程序环境：`C:\Users\WYK\.conda\envs\pdf_ai_reader_314`。
- 训练环境可用：`C:\Users\WYK\.conda\envs\science`，不要把 torch 装进主环境。
- 隔离工具环境已存在：`pdf_tool_paddle310`、`pdf_tool_mineru310`、
  `pdf_tool_pix2text310`、`pdf_tool_magic310`、`pdf_tool_pek310`。
- 外部工具模型缓存统一放在仓库 `.tool_models/`，通过
  `PDF_AI_READER_TOOL_MODELS_DIR` 覆盖。
- `测试资料/` 是用户资料，不提交、不清理、不移动。
- `test_artifacts/`、`logs/`、缓存、临时 benchmark 输出不提交。

最近已提交：

- `5b5b38e Optimize TinyBDMath relation scoring pipeline`
- `a258fe2 Clean legacy TinyBDMath tooling and docs`
- `1cbb9e1 Document neural symbolic TinyBDMath plan`
- `c75d919 Add TinyBDMath graph parser pipeline`
- `a77a37b Clean legacy TinyBDMath scope`
- `84496c4 Add TinyBDMath constrained layout verification`
- `2b964aa Support TinyBDMath enclosure and equation tag relations`
- `c6ae63d Rank TinyBDMath n-best candidates with verifier evidence`
- `4e4a861 Complete TinyBDMath graph parser eval loop`
- `aceba02 Improve TinyBDMath target parsing and eval tooling`
- `a5091f8 Add AI dock panel controls`

此前 TinyBDMath 范围收口已在 Graph Parser M1 基础上提交节点保留/丢弃学习：

- `src/core/tinybdmath_graph_parser.py`
  - artifact 新增节点分类头。
  - 节点标签已升级为 `SYMBOL`、`TEXT`、`OPERATOR`、`SPACING`、`UNKNOWN`、
    `HORIZONTAL_RULE`、`VERTICAL_RULE`。
  - 节点特征加入 Unicode 大类、单字符标记、相对字号等通用证据。
  - 推理输出 `node_predictions` 和 `node_filter_threshold`。
- `tools/tinybdmath_train_graph_parser.py`
  - 同时训练关系头和节点头。
  - 用样本 confidence 加权训练，弱 `UNKNOWN` 不再被当成强标签。
  - 节点头使用 class weight 召回 TEXT/OPERATOR，但不放大弱 `UNKNOWN`。
- `src/core/tinybdmath_target_tree.py`、`src/core/tinybdmath_alignment.py`
  - target tree 保存 KaTeX `family`。
  - alignment 在节点对齐里写入 `target_node_type` 和 `target_attrs`。
- `src/core/tinybdmath_latex_decoder.py`
  - 只按模型高置信 `SPACING` 节点过滤 glyph/relation。
  - 不写字符映射，不猜根号/分数/上下标。
- 对应测试：
  - `tests/test_tinybdmath_graph_parser.py`
  - `tests/test_tinybdmath_latex_decoder.py`
- 旧 TinyBDMath quality/edge/sharded/gold/review 路线和孤立历史文档已清理。

当前 r2a 默认推理路径只保留 Graph Parser artifact。缺模型时写 candidate-only
abstain 和缺模型 warning。

2026-06-05 最新 UI 提交 `a5091f8 Add AI dock panel controls`：

- `src/ui/main_window.py`
  - 主工具栏设置 objectName `main_toolbar`，右侧增加 `right_panel_toggle_button`。
  - 右侧 `AI 工具集` dock 去掉 closable feature，保留 movable/floatable。
  - dock 内容拆成 `_right_panel_body`，工具栏按钮可隐藏/显示右侧 AI 面板，并保存展开宽度。
  - dock 使用自定义标题栏，`right_dock_float_button` 可在弹出独立窗口和归位右侧栏之间切换。
  - 最小宽度当前为 300，默认展开宽度当前为 360。
- `tests/test_smoke.py`
  - `test_main_window_smoke` 覆盖 toolbar/toggle、隐藏/显示文案、dock 不可关闭、
    float/restore 和右侧停靠区域。
- 已补跑：
  - `C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_smoke.py -q`
  - 结果：11 passed，约 39 秒。
- 轻量接手完整测试在用户打断后未完成；需要重新确认基线时按本文末尾命令重跑。

2026-06-02 进一步收束：第一阶段目标不是开放科学记号识别，而是 AI/Math 论文
中的通用数学排版结构恢复。数学公式不能按内容枚举；应按 LaTeX/amsmath/
mathtools/unicode-math 支持的递归二维排版组合器建 CSLT，例如 sequence、
script、under/over、fraction、radical、accent、fence、matrix/aligned、
operator、text run、style/mathvariant 和 equation tag。详见
`docs/tinybdmath_ai_math_latex_structure_scope.md`。

## 3. 当前核心结论

TinyBDMath 性能路径已跑通，但公式质量不达标：

- 全量 direct eval rows=29881。
- 耗时约 192.59s。
- structural F1=0.315585。
- decoded exact_match_rate=0.523242。
- decoded near_match_rate=0.659550。

这说明：

- fast score/streaming eval 已经够用来做全量实验。
- 继续只优化 JSONL、batch、direct decode 不会解决质量。
- 根因是监督、目标树、PDF/目标对齐、模型结构、约束解码和 verifier。

下一步主线已经改为“模型直接学公式结构”：

```text
PDF 小块证据
  -> 训练标签
  -> 模型直接输出公式结构
  -> 通用合法性检查
  -> 低置信拒绝或 candidate-only
  -> 人工/门禁 accepted 后再进知识库
```

详细方案见 `docs/tiny_born_digital_math_model_engineering.md`。

## 4. 禁止事项

禁止：

- 用 OCR/MFR 解决 born-digital PDF 主问题。
- 在 decoder 中为 `\sqrt`、`\frac`、上下标、文本组写样本特化补丁。
- 用固定论文词表、固定命令表、字符串替换或 ad hoc 正则伪装公式识别。
- 在 TinyBDMath 对齐器、decoder 或后处理里维护本地 TeX/Unicode 符号表。
- 把复合符号写成固定 glyph 序列来追样本指标。
- 把 relation precision 当成 formula-level 准确率。
- 把低置信 TinyBDMath 输出写正文、FTS、向量库或 GraphRAG。
- 把 Paddle/MinerU/Pix2Text/UniMERNet 等重工具混进 UI 热路径或主环境。
- 提交 `测试资料/`、`test_artifacts/`、`logs/`、缓存、模型权重、临时产物。
- 提交信息添加额外署名、来源标记或生成工具署名。

允许：

- 用源码在训练/审计阶段生成 target tree。
- 用 Unicode Math、AGL、TeX glyph list、OpenType MATH、font cmap 等数据资源。
- 用 KaTeX/MathML/TeX AST 在 target tree 生成阶段产出可审计的
  `identity_aliases`。
- 用通用数学排版语法约束。
- 用 verifier 做 evidence-based reject/abstain。

## 5. 多轮公式解析要求

多轮公式解析不是“一次性同步扫描”。导入后可以尽早全篇入队，但每轮都必须
异步分批、结果落库、可恢复、二次打开跳过。

| 轮次 | 名称 | 目标 | 默认路径 |
| --- | --- | --- | --- |
| r0 | `r0_pdf_structure` | born-digital 快扫，抽取文本层、glyph、font、bbox、vector、图片和页级候选 | 不 OCR，不阻塞首屏 |
| r1 | `r1_cached_recognition` | 对图片/扫描/needs_ocr 候选先查缓存 | 未命中才推理，不能抢 UI |
| r2 | `r2_local_high_precision` | 本地高精度/多工具候选，处理低置信或用户显式精扫 | 独立 worker，结果 candidate-only |
| r2a | `r2a_tinybdmath_structural` | born-digital 非 OCR 结构候选 | candidate-only，不 accepted |
| r3 | `r3_cloud_semantic_review` | DeepSeek 等模型做语义校对建议 | 写 result JSON，不覆盖正文 |
| r4 | `r4_knowledge_graph` | 写公式/章节/定理/概念/引用图谱证据 | 异步，不阻塞基础问答 |
| r5 | `r5_knowledge_incremental_update` | accepted 变化后增量 upsert 知识库和 GraphRAG artifact | 只消费 accepted |

硬要求：

- 每轮任务必须有 `queued/running/done/failed/skipped`。
- 每轮输入必须有 hash。
- 同一 input hash、模型版本、预处理版本命中时必须跳过。
- 低置信结果只能写候选和 warnings。
- UI 热路径只读缓存，不做模型冷启动。
- r5 不能消费未审核 fusion、rejected 或低置信候选。

对应设计：`docs/async_formula_indexing_design.md`。

## 6. 下一步具体工作

### 6.1 固定 r2a 边界

r2a 只加载 Graph Parser artifact。decoder 只做结构序列化、合法性检查和
candidate-only 输出；结构缺失或低置信时 abstain。

### 6.2 CSLT schema（M1 已完成）

已新增：

- `src/core/tinybdmath_cslt_schema.py`
- `tests/test_tinybdmath_cslt_schema.py`

必须能表达：

- `h_{t-1}`
- `\sqrt{d_k}`
- `d_{\text{model}}`
- 简单分数
- 简单矩阵/cases/aligned 的骨架
- artifact/spacing 节点

2026-06-02 起，schema 验收不再停留在这些示例公式。AI/Math M0 必须按组合器
覆盖审计：

- 基础：symbol、text_run、sequence、group、spacing_artifact。
- 附着：script、prescript、under_over、accent_annotation。
- 分隔/包围：fraction、radical、fence、基础 enclosure evidence。
- 网格/多行：matrix_grid、aligned_display、equation_tag。
- 身份/样式：operator、style_variant、mathvariant、font_identity。

样本清单只用于验收覆盖，不能变成公式类型枚举或 decoder 规则。

### 6.3 target tree builder（M1 已完成）

已新增：

- `src/core/tinybdmath_target_tree.py`
- `tools/tinybdmath_build_target_trees.py`
- `tests/test_tinybdmath_target_tree.py`

要求：

- 源码只用于训练/审计。
- parser backend/version/warnings 必须入 manifest。
- 解析失败保留 failure bucket，不丢样本。
- 输出 CSLT JSONL，不直接追作者源码 exact。

### 6.4 alignment（M1 已完成，2000 行审计已完成）

已新增：

- `src/core/tinybdmath_alignment.py`
- `tools/tinybdmath_align_targets.py`
- `tests/test_tinybdmath_alignment.py`

要求：

- 输出 hard/soft/ignore 标签。
- 对 artifact、spacing、marker、unknown 节点给出原因。
- 已跑 20 行 smoke：rows_with_hard_labels=13，avg_hard_alignment_rate=0.894048。
- 已跑 200 行无本地符号表审计，gate passed。
- 已跑 2000 行无本地符号表审计，gate passed。
- 未通过 alignment audit 前不要训练大模型。

2026-06-02 已按无 TinyBDMath 本地符号表的写法重跑 200 行审计：

- 临时目录：`test_artifacts/tinybdmath_graph_parser_audit_200_no_local_symbol_map/`
  不提交。
- target tree：200 行，199 成功，1 个 KaTeX alignment 源码解析失败。
- alignment：rows_with_hard_labels=148，avg_hard_alignment_rate=0.940763。
- audit：hard_row_rate=0.915，relation_row_rate=0.74，gate passed。
- 失败样例集中在 `\cong`、`\mapsto`、`\cdot`、根号 glyph 等 identity/字体
  证据不足，不得在 alignment/decoder 中补表或拆固定 glyph 序列。

2026-06-02 同一路线已扩大到 2000 行：

- 临时目录：`test_artifacts/tinybdmath_graph_parser_audit_2000_no_local_symbol_map/`
  不提交。
- target tree：rows=2000，success_rows=1993，failed_rows=7。
- alignment：rows_with_hard_labels=1269，avg_hard_alignment_rate=0.950611。
- audit：hard_row_rate=0.9395，relation_row_rate=0.6345，gate passed。
- 结论：标签足以继续小规模模型实验，但 relation_row_rate 仍说明 group/text/operator/
  vector role 监督不足，不能直接长训后宣称质量完成。

### 6.5 Graph Parser（M1 已提交，节点头正在完善）

已新增：

- `src/core/tinybdmath_graph_parser.py`
- `tools/tinybdmath_train_graph_parser.py`
- `tests/test_tinybdmath_graph_parser.py`

模型要学的目标：

- 每个 PDF 小块保留还是丢弃。
- 每个小块的符号身份。
- 谁是谁的父节点。
- 父子关系类型。
- 哪些小块组成同一组。
- 哪些横向/竖向 vector 是低层结构证据。
- 哪些 target tree 结构关系由 edge/group head 学出；decoder 不能从 node label
  直接合成分数、根号或矩阵。

已跑 smoke：

- `science` 环境 PyTorch 2.5.1 可用。
- 20 行 alignment、2 epochs、CPU 训练成功并导出 JSON artifact。
- 该 smoke 只证明链路可跑，质量未达标。
- 200 行、1 epoch 节点头曾全部预测 `UNKNOWN`，已定位为弱标签训练方式不稳。
- 三分类节点头已升级为 role-label 五分类节点头。
- 200 行 role-label smoke 目录：
  `test_artifacts/tinybdmath_graph_parser_role_labels_smoke/`，不提交。
- role-label smoke：
  - relation validation accuracy=0.763872。
  - relation positive_recall=0.688172。
  - node label counts：OPERATOR=196、SPACING=336、SYMBOL=711、TEXT=96、UNKNOWN=252。
  - node_validation accuracy=0.642857。
  - node recall：OPERATOR=0.46875、SPACING=1.0、SYMBOL=0.684685、TEXT=1.0、UNKNOWN=0.0。
  - node precision：OPERATOR=0.681818、SPACING=0.92、SYMBOL=0.697248、TEXT=0.280702。
- 结论：TEXT/OPERATOR 已进入模型学习路径，但 TEXT precision 偏低、UNKNOWN
  暂不预测；下一步要扩大样本、校准阈值，并补 vector role/group boundary。
- 2026-06-03 已用当前结构标签完成 2000 行短训 smoke：
  relation validation accuracy=0.818262，positive_recall=0.405096；
  node validation accuracy=0.761431；OPERATOR recall=0.946903、
  precision=0.550600；TEXT recall=0.900000、precision=0.095745。
- 同一 2000 行 decoded eval：exact=0.569500，near=0.710500，
  weak=0.924500，average_similarity=0.866203。
- 2026-06-03 已接入 layout verifier M0：gate 后 pass=557、review=935、
  abstain=508，final_abstain_rate=0.254000；未拒绝子集 exact=0.726542、
  near=0.819035。该 gate 只影响 candidate-only 的可信度和拒绝状态，不写
  accepted。
- 2026-06-03 已新增 constrained decode M0：只检查模型结构图 schema、缺失节点、
  cycle、coverage 和 blockers，不生成 LaTeX 模板；接入后同一 2000 行 gate 为
  pass=557、review=859、abstain=584，final_abstain_rate=0.292000，
  未拒绝子集 exact=0.748588、near=0.831215。
- 2026-06-03 已接入 n-best CSLT/LaTeX candidate evidence：Graph Parser
  relation alternatives 会透传到 constrained decode；成环结构额外生成按模型
  置信度保留的无环投影候选。rank-1 永远保持原始 selected graph，不自动
  accepted。814 行重生成候选审计：rank-1 exact/near=0.515971/0.687961，
  n-best oracle exact/near=0.527027/0.787469，average_candidate_count=2.125307。
- 2026-06-03 已修复接续会话故障点：重复 LaTeX 的替代结构证据会合并进同一
  candidate 的 `alternative_structure_evidence`，但 `candidate_id=selected`
  不会作为自身替代证据重复写入。814 行审计指标保持不变。
- 2026-06-03 继续补通用结构序列化：decoder/constrained decode/layout verifier
  已把 `FENCE_OPEN`、`FENCE_CLOSE`、`FENCE_BODY`、`TEXT_RUN_NEXT`、
  `MATRIX_ROW`、`MATRIX_CELL`、`CELL_CONTENT`、`ACCENT_BASE` 纳入可序列化关系。
  TEXT_RUN 只有在 node head 对链上节点给出高置信 `TEXT`/`OPERATOR` 时才包
  `\text{...}`，否则只作为普通顺序关系，避免把数学符号误包成文本。814 行
  gated-text 审计：rank-1 exact/near=0.531941/0.680590，n-best oracle
  exact/near=0.540541/0.766585，pass_or_review exact/near=0.711504/0.821239。
- 2026-06-03 后续接续已按 M0/M1 结构清单继续推进，而不是按失败样例决定
  计划：`prescript`/左附着通用关系已完成 CSLT、alignment、Graph Parser、
  decoder、constrained decode、layout verifier 闭环；`radical_index` 已完成
  nth-root index 监督和序列化；operator text run 由 node head 高置信
  `OPERATOR` 输出 `\operatorname{...}`，高置信 `TEXT` 输出 `\text{...}`；
  `matrix_grid` 训练监督和 decoder 已按 row/cell/content 三层语义修正。
  失败分桶只用于验收归因，不作为实现顺序来源。focused 结构测试 82 passed，
  TinyBDMath 主线 129 passed。
- 已修复 decoder 一个具体 bug：分数/overline 等 rule structure 已消费全部
  glyph 后，不再回退遍历所有 glyph 并误报 `decoder_no_root`/`decoder_cycle`。

正式训练前提：

- CSLT target tree 已通过审计。
- alignment rows 已通过 200/2000 行审计。
- hard/soft/ignore 分层明确。
- 2026-06-01 direct eval 数字固定为历史对照。
- 节点头、关系头、group/text/operator/vector role 标签要纳入同一评估报告。
- 已完成 100 个 AI/Math LaTeX 结构覆盖审计，输出 `ready_for_model`、
  `needs_identity_repair`、`needs_schema_extension`、`needs_image_mfr`、
  `route_unsupported`、`abstain` 分桶。

### 6.6 r2a 主路径

- `tools/formula_multiround_pipeline.py` 支持 `--tinybdmath-graph-parser-model`。
- `tools/full_software_validation.py` 默认寻找
  `test_artifacts/tinybdmath_graph_parser_m1/tinybdmath_graph_parser_model.json`。
- `src/core/tinybdmath_constrained_decode.py` 现在输出结构约束 status、blockers、
  coverage、relation schema 统计、canonical CSLT、n-best CSLT。
- `decode_latex_candidate()` 现在输出 `abstain`、`layout_status`、
  `layout_confidence`、`layout_verification`、rank-1/n-best LaTeX candidates；
  `tools/tinybdmath_eval_decoded_latex.py` 输出 `layout_gate` 和 n-best oracle
  审计指标。

## 7. 环境检查命令

接手后先跑：

```powershell
git status --short
conda env list
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'pdf_tool_|pdf_formula_|mineru|magic-pdf|paddleocr|unimernet|conda.*create|tinybdmath|keep_awake' } |
  Select-Object ProcessId,Name,CommandLine
```

防休眠检查：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*keep_awake*' } |
  Select-Object ProcessId,Name,CommandLine
Get-Content logs\keep_awake_watchdog.log -Tail 20
```

如需启动防休眠：

```powershell
Start-Process -FilePath powershell.exe `
  -ArgumentList '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "D:\程设大作业\tools\keep_awake_watchdog.ps1" -IntervalSeconds 60 -WorkerIntervalSeconds 20' `
  -WindowStyle Hidden
```

这只能防普通空闲睡眠，不能防断电、电池耗尽、系统更新、用户手动关机或
系统策略强制锁定。

## 8. 测试基线

轻量接手测试：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest `
  tests/test_external_formula_tools.py `
  tests/test_formula_index_flow.py `
  tests/test_born_digital_math.py `
  tests/test_formula_semantic_review.py `
  tests/test_smoke.py -q
```

Graph Parser M1 测试：

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

本轮已验证：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest `
  tests/test_tinybdmath_symbol_equivalence.py `
  tests/test_tinybdmath_alignment.py `
  tests/test_tinybdmath_alignment_audit.py `
  tests/test_tinybdmath_target_tree.py `
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

结果：116 passed。

接续故障点补充验证：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest `
  tests/test_tinybdmath_latex_decoder.py `
  tests/test_tinybdmath_eval_decoded_latex.py `
  tests/test_tinybdmath_graph_parser.py `
  tests/test_tinybdmath_constrained_decode.py `
  tests/test_tinybdmath_layout_verifier.py `
  tests/test_tinybdmath_candidate_service.py `
  tests/test_tinybdmath_no_hardcoded_patterns.py -q
```

结果：42 passed。814 行 duplicate-evidence 审计输出：
rank-1 exact/near=0.515971/0.687961，n-best oracle
exact/near=0.527027/0.787469，average_candidate_count=2.125307，
manual recommendation exact/near=0.522113/0.686732，
auto_accept_allowed_count=0。

## 9. 提交前检查

```powershell
git status --short
rg -n "额外署名|来源标记|生成工具署名|自动署名" . -S `
  -g '!测试资料/**' -g '!test_artifacts/**' -g '!logs/**'
```

提交只包含本次任务相关文件。不要把试验代码、文档重写、测试产物混在同一个
含义不清的提交里。

## 10. 何时可以说完成

不能因为“训练跑完”或“性能更快”就说完成。

阶段性完成条件：

1. CSLT schema 能表达主要失败结构。
2. target tree builder 有 200 行审计报告。
3. alignment 有 200/2000 行审计报告。
4. graph parser 在 formula-level eval 上超过 2026-06-01 direct eval 对照。
5. constrained decoder/verifier 能拒绝明显错误候选。
6. r2a 仍 candidate-only，accepted gate 独立校准。

最终产品完成还需要 Attention/Napkin 大样本、交互 E2E、日志审计、RAG/GraphRAG
证据链和 accepted precision 统计置信区间，当前尚未达到。

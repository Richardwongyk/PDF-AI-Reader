# Born-digital 公式解析系统文档索引

日期：2026-05-25

本索引用来把 born-digital PDF 公式解析研发拆成单一职责文档。不要再把 PDF 基础、符号修复、小模型训练、质量门禁和产品集成混在一篇巨型报告里。

## 1. 总体目标

目标不是把所有 PDF 公式一律截图 OCR，而是建立一条证据优先、可审计、可分批、可跳过的多轮路线：

1. 先从 PDF 结构层提取事实。
2. 对缺失符号身份做非视觉修复。
3. 用极轻量小模型解析公式二维结构。
4. 用视觉工具和云端模型做候选补救与复核。
5. 只有通过严格门禁的 accepted 结果进入 RAG/GraphRAG。

## 2. 职责分层

| 层级 | 职责 | 默认是否 OCR | 产物 | 详细文档 |
|---|---|---:|---|---|
| r0 | PDF 事实抽取：glyph、font、bbox、vector、PDF health、公式区域 | 否 | Raw Glyph Graph | `docs/born_digital_pdf_foundations.md` |
| r0.5 | 符号身份修复：AGL/texglyphlist/font cmap/TeX encoding/传播/shape candidate | 否 | Enriched Glyph Graph | `docs/born_digital_symbol_identity_repair_report.md` |
| r2a | TinyBDMath 小模型：glyph graph -> relation/SLT/LaTeX candidate | 否 | structural candidate | `docs/tiny_born_digital_math_model_engineering.md` |
| r2b | 视觉/文档工具兜底：Pix2Text/Paddle/UniMERNet/MinerU 等 | 是，仅必要时 | visual/document candidate | `docs/formula_multitool_fusion_design.md` |
| r3 | 云端语义复核 | 否/不识别图像为主 | review candidate JSON | `docs/async_formula_indexing_design.md` |
| r4/r5 | GraphRAG/知识库 accepted 增量更新 | 否 | graph/index artifact | `docs/rag_graphrag_migration_plan.md` |
| QA | 质量门禁、99.999% accepted precision、源码验收 | 不限 | quality report | `docs/formula_quality_acceptance_plan.md` |

## 3. 决策树

### 3.1 PDF/区域分诊

- 可复制、ToUnicode/font/bbox 可靠：走 r0 -> r0.5 -> r2a。
- ToUnicode 差但字体/编码/轮廓仍有证据：先走 r0.5，不能直接 OCR。
- Office/PPT PDF：按区域分流。可复制公式仍走结构层；图片化区域走视觉。
- 扫描/图片公式：走 r1/r2b 视觉路线。
- 混合文档：页级/区域级分流，不整篇一刀切。

### 3.2 accepted 原则

- r0/r0.5/r2a/r2b/r3 默认都只产候选。
- 自动 accepted 必须通过质量门禁。
- 低置信不能覆盖正文，不能污染 RAG/GraphRAG。
- 99.999% 是 accepted precision 的长期证明目标，不是全量自动转换承诺。

## 4. 小模型研发独立边界

TinyBDMath 小模型研发只负责：

- 输入 Enriched Glyph Graph。
- 预测符号之间的结构关系。
- 输出 SLT/Presentation MathML/canonical LaTeX 候选。
- 给出 calibrated confidence 和可解释关系。

它不负责：

- 从图片识别字符。
- 修复 unknown glyph identity。
- 语义理解变量含义。
- 直接 accepted。
- 写入知识库。

因此所有模型训练、数据集、特征、GNN/MLP/Transformer 对比、ONNX 推理和模型评估都放在 `docs/tiny_born_digital_math_model_engineering.md`。

## 5. 近期研发优先级

1. 已完成第一阶段入口：`src/core/pdf_glyph_graph.py` 固化 r0 Raw Glyph Graph schema，r0 formula candidate evidence 已携带局部 graph schema/hash/health/glyph/vector/image。
2. 已完成 r0.5 静态修复 MVP：`src/core/symbol_identity_repair.py` 生成 Enriched Glyph Graph，保留已知 PDF Unicode，支持 glyph name 静态映射和同 normalized_font+cid 锚点传播，冲突时保守保留 unknown。
3. 已完成 r0.5 最小持久化：`FormulaScanRound.SYMBOL_IDENTITY_REPAIR` 写入 `formula_round_jobs`，记录 `input_hash/raw_input_hash/model_version/preprocess_version/summary`，同 input hash 跳过。
4. 已建立本地标准/映射参考缓存：`.local_references/standards/`，索引见 `docs/local_standards_cache_index.md`。该缓存不提交，只用于研发对照；后续把 AGL/texglyphlist/Unicode/MathML/OpenType 资源产品化前必须确认许可证和资源 hash。
5. 已完成源码插桩/重编译训练集第一阶段：Attention 138/138、Napkin v3 29743/29743 verified exact rows，作为 TinyBDMath 训练/评测主资产。
6. 下一步必须先强化 r0.5 补丁层：接入真实 AGL/texglyphlist、fonttools cmap、TeX encoding、OpenType MATH/cmap 证据和 outline/path shape candidate。不能把 TinyBDMath 当成符号身份修复层。
7. 下一步优先把插桩训练集转换成 TinyBDMath graph rows、manifest 和 split，而不是继续做不可靠的 PDF/source 粗匹配。
8. 下一步训练/实现 MLP edge/quality scorer baseline，并建立按 inline/display、上下标、分数线、根号、overline、large operator limits、align、matrix/cases、数学字体分项的评测。
9. 建立 verifier 和 accepted gate。目标是 `accepted precision` 极高，不是把所有候选强行 accepted。
10. 再升级 GNN/Graph Transformer。

## 6. 覆盖策略红线

“涵盖所有公式规则”不能理解成手写一份固定规则表。LaTeX 是宏系统，PDF 也通常不保留原始 LaTeX AST。项目必须按以下方式覆盖：

- 标准目标：以 SLT / Presentation MathML / canonical LaTeX 为输出目标。
- 结构类别：每类结构都要有训练样本、relation label、decoder 支持、verifier 检查和指标分桶。
- 补丁优先：符号身份先走 r0.5 多证据修复，不能让结构模型背锅。
- 不确定就候选：低置信、未知宏、unknown glyph、shape-only 关键符号、横线歧义和正文污染风险都不能自动 accepted。
- 兜底分层：结构层失败后再进入 r2b 视觉工具、r3 云端复核或人工审核。

## 7. 文档维护规则

- 总索引只写导航、边界和决策树。
- PDF/LaTeX/PPT 基础放到 foundations。
- r0.5 修复算法放到 symbol identity repair。
- 小模型研发放到 TinyBDMath engineering。
- 质量、统计证明和验收放到 quality acceptance。
- 本地标准缓存与许可证边界放到 `docs/local_standards_cache_index.md`。
- 已实现状态写 `TODO.md` 和 `docs/current_goal_and_next_steps.md`，不要在研究报告里伪装成已完成。

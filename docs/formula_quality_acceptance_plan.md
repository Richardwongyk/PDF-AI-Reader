# 公式解析质量门禁与验收计划

日期：2026-05-25

本文件只定义质量、验收和 accepted 门禁。模型研发见 `docs/tiny_born_digital_math_model_engineering.md`。

## 1. 核心原则

- 候选可以多，accepted 必须少而准。
- 99.999% 是 accepted precision 的长期目标，不是所有公式自动还原承诺。
- 低置信结果不能覆盖正文，不能进入 RAG/GraphRAG 的确定知识。
- LaTeX 源码只用于测试验收，不用于真实用户路径。

## 2. 结果分层

1. Raw Evidence：PDF glyph/font/bbox/vector。
2. Repaired Evidence：r0.5 修复后的 Enriched Glyph Graph。
3. Candidate：TinyBDMath/视觉/云端候选。
4. Accepted：通过门禁，可进入知识库。

## 3. Accepted 门禁

自动 accepted 至少需要：

- PDF health 合格。
- r0.5 符号身份冲突少。
- TinyBDMath 关键关系置信高。
- SLT 结构合法。
- 符号覆盖无明显漏/增。
- 几何一致性通过。
- 数学字体证据充分。
- 无高风险 warning。
- 可选视觉/云端不强烈反对。
- 当前 model/verifier/threshold 版本有独立评估记录。

## 4. 指标

候选层：

- formula candidate recall。
- inline recall。
- display recall。
- unknown glyph reduction。
- relation F1。
- SLT exact。

accepted 层：

- accepted precision。
- accepted coverage。
- critical error rate。
- math alphabet preservation。
- RAG contamination rate。

性能层：

- first-open latency。
- background throughput。
- second-open skip rate。
- cache hit rate。

## 5. 99.999% 统计含义

如果 accepted 样本中零错误，要用 95% 置信证明错误率 < 1e-5，约需 300,000 个零错误 accepted 样本。短期不能声称已达到，只能报告当前样本和置信区间。

## 6. Attention/Napkin 验收

Attention：

- 快速回归。
- display/inline 基础公式。
- 小公式如 `h_{t-1}`。
- 数学字体。

Napkin：

- 大文档性能。
- 复杂布局。
- 多页跳转和二次打开。
- 大量真实公式统计。

每次验收必须检查：

- 是否误用 OCR 作为 born-digital 默认路线。
- 是否有样本特化正则/固定词表。
- 低置信是否污染正文/RAG。
- 每轮结果是否落库且可跳过。

## 7. 错误分级

- Critical：符号或结构错误，改变数学含义。
- Major：数学字体或上下标等重要语义错误。
- Minor：等价 LaTeX 风格差异。
- Cosmetic：显示风格差异。

accepted precision 目标主要约束 Critical + Major。

## 8. 报告格式

每次 benchmark 输出：

- case。
- PDF pages。
- stage。
- model/version。
- input hash。
- candidate count。
- accepted count。
- exact/near/weak metrics。
- critical errors。
- latency。
- skip/cache stats。
- hardcoding audit result。

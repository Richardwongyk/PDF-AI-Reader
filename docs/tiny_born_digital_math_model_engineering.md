# TinyBDMath 小模型研发工程方案

日期：2026-05-25

本文件只讨论小模型研发工程。PDF 底层事实抽取、r0.5 符号身份修复、视觉工具兜底、云端复核和 RAG 写回不在本文展开。

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
- ABOVE。
- BELOW。
- NUMERATOR。
- DENOMINATOR。
- RADICAL_BODY。
- INSIDE。
- OPERATOR_OVER。
- OPERATOR_UNDER。
- MATRIX_RIGHT。
- MATRIX_DOWN。
- PUNCT。
- NO_EDGE。

后续再加 accent、overline、arrow label、cases、text run。

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

### M0：数据 schema

- Raw Glyph Graph schema 已由 `src/core/pdf_glyph_graph.py` 落地，r0 evidence 已携带局部 graph hash/health/glyph/vector/image。
- 下一步补 Enriched Glyph Graph schema。
- edge candidate generator。
- 可视化工具。

### M1：MLP baseline

- 1k-10k synthetic formula。
- relation F1 baseline。
- decoder MVP。

### M2：项目接入

- r2a structural candidate worker。
- FormulaIndexStore 落库。
- pipeline 报告 TinyBDMath 候选。

### M3：GNN

- GNN edge classifier。
- 分数/根号/上下标专项评估。
- inline 初步支持。

### M4：复杂结构

- matrix/cases/aligned。
- math alphabet。
- Office/PPT 可复制公式域。

### M5：质量证明

- 大规模独立测试。
- accepted gate 校准。
- 统计置信区间。

## 11. 当前最小可执行任务

1. 已完成 Raw Glyph Graph schema。
2. 下一步实现 r0.5 静态映射，生成 Enriched Glyph Graph。
3. 生成 100-1000 条 synthetic 公式 PDF graph。
4. 写 MLP edge scorer baseline。
5. 接入 r2a candidate-only。
6. 用 Attention/Napkin 验证不污染正文和 RAG。

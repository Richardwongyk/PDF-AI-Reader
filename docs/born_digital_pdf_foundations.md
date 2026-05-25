# Born-digital PDF / LaTeX / Office 公式解析基础

日期：2026-05-25

本文件只放底层基础：PDF 能提供什么、LaTeX 编译后丢失什么、Office/PPT PDF 如何分流。小模型研发见 `docs/tiny_born_digital_math_model_engineering.md`，符号身份修复见 `docs/born_digital_symbol_identity_repair_report.md`。

## 1. PDF 不是 LaTeX AST

PDF 页面是绘制指令序列，不是公式语法树。常见内容包括：

- 选择字体。
- 设置文本矩阵。
- 绘制 glyph。
- 绘制线段和路径。
- 放置图片。

因此 PDF 通常不会保存“这是 `\frac{a}{b}`”或“这是上标”。它只保存最终排版结果。

## 2. 可利用的结构事实

可从 PDF 提取：

- glyph Unicode 或 raw code。
- glyph name / CID。
- font name / font subtype。
- bbox / origin / advance。
- font size。
- baseline。
- line/span/block。
- vector path：分数线、根号线、矩阵线。
- image block。
- ToUnicode、font encoding、cmap。
- Tagged PDF / ActualText（若存在）。

这些事实构成 Raw Glyph Graph。

## 3. 常见失败模式

- ToUnicode 缺失。
- glyph code 无法映射 Unicode。
- Type 3/旧 Type 1 字体。
- 字体 subset 和重编码。
- 绘制顺序不等于阅读顺序。
- glyph bbox 不等于真实 ink bbox。
- 公式和正文/表格/图注混杂。
- 图片化公式。
- OCR text layer 假装文本层。

遇到这些问题不能直接 accepted。

## 4. TeX/LaTeX 基础

TeX 数学模式会构建 math list/noad，再转换为盒子和 PDF 绘制指令。过程中会丢失：

- 作者自定义宏。
- 源码空格和换行。
- 部分字体命令原意。
- `\left...\right` 是否显式存在。
- `\frac` / `\dfrac` / `\tfrac` 的源码选择。
- alignment marker。

因此目标是恢复 canonical LaTeX / SLT / Presentation MathML，而不是作者原始源码。

## 5. MathML / SLT / LaTeX 的关系

- Presentation MathML：描述排版结构。
- Content MathML：描述数学语义。
- SLT：描述符号空间关系。
- LaTeX：便于显示和用户编辑。

从 PDF 反推时，SLT/Presentation MathML 最现实；Content MathML 需要上下文和语义推理。

## 6. Office/PPT PDF 分流

PPT/Office PDF 不能简单归为图片类。

区域可能是：

- 可复制公式文本。
- glyph + vector 的公式。
- 图片化公式。
- 混合对象。
- 来自 OMML 的结构被展平。

策略：

- 可复制且 bbox/font 稳定：结构路线。
- glyph 碎片化但仍有证据：结构候选 + 视觉交叉验证。
- 真图片化：视觉路线。
- 若有 `.pptx/.docx`：优先解析 OMML，再与 PDF 对齐。

## 7. 规则边界

可以写：

- PDF 事实抽取规则。
- font/encoding 标准化。
- 通用结构合法性约束。
- verifier。

不能写：

- 样本特化词表。
- 针对 Attention/Napkin 的修复逻辑。
- 看见某个词就转固定 LaTeX。
- 低置信候选直接覆盖正文。

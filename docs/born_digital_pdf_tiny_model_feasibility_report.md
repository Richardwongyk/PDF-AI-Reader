# Born-digital PDF 公式结构小模型可行性调研报告

日期：2026-05-25

状态：综合调研草案。后续执行请优先阅读 `docs/born_digital_formula_system_index.md`。单一职责文档已经拆分为：

- `docs/born_digital_pdf_foundations.md`：PDF/LaTeX/Office 基础。
- `docs/born_digital_symbol_identity_repair_report.md`：r0.5 符号身份修复。
- `docs/tiny_born_digital_math_model_engineering.md`：TinyBDMath 小模型研发工程。
- `docs/formula_quality_acceptance_plan.md`：质量门禁和验收。

本文讨论一个明确目标：不把 born-digital PDF 公式默认转成图片做 OCR，而是直接利用 PDF 结构层中的 glyph、font、bbox、字号、baseline、绘图线段和 ToUnicode 等事实，研发一个极轻量模型，将数学公式区域还原为可审计的 LaTeX / Symbol Layout Tree / MathML 候选。本文中的“模型”不负责从像素识别字符，而主要负责从 PDF 坐标云和图形原语中推断二维数学结构。LaTeX 源码只作为训练、验证和审计数据来源，真实用户 PDF 解析不能依赖源码。

2026-05-28 状态补充：可行性调研已经部分落地为 TinyBDMath graph rows、weak relation labels、
edge scorer、structural candidate、SLT skeleton/verifier 和 r2a candidate-only 服务。当前结论是
“链路可运行但质量未达标”：Attention/Napkin 仍不能通过最终公式门禁，TinyBDMath 输出不能 accepted。
下一步重点是 SLT/MathML hard labels、正式 MLP/GNN、decoder/verifier 和大样本 accepted precision 证明。

同日交互状态补充：TinyBDMath 和公式质量优化不能替代 PDF viewer 体验修复。Napkin 极大缩放快速滚动
黑底/空白页是 P0 渲染 fallback 问题，必须先恢复页面可见性，再继续扩大前台压力测试。

## 1. 结论先行

### 1.1 可行性判断

研发一个处理 born-digital PDF 的极轻量公式结构模型是可行的，并且比继续堆视觉 OCR 更符合 arXiv 科学论文这类 PDF 的本质。

原因很直接：在 born-digital PDF 中，很多公式符号已经不是图片，而是页面绘制指令中的字符和图形对象。PDF 渲染器之所以能把公式画出来，是因为文件里有字体、glyph code、坐标、字号、变换矩阵、线段和页面资源。PyMuPDF 文档也明确说明 `rawdict` / `rawjson` 能给出字符级位置、bbox、span 字体和字号等信息。也就是说，对于现代 LaTeX/PDF 工具链生成的论文，字符识别这一步常常已经由 PDF 完成；剩下最难的是“这些符号之间是什么关系”：谁是上标、谁是下标、谁在分数线上方、谁在根号内部、谁属于矩阵单元、谁是同一主基线上的相邻符号。

这正好适合小模型。小模型不需要像视觉 OCR 模型那样从百万像素里学习字符形状，它可以把每个 glyph 当作带属性的节点，把相邻关系、line-of-sight、baseline 差、字号比例、bbox 重叠、矢量线段等当作边特征，训练一个边关系分类器或轻量 GNN，然后用图解码得到 Symbol Layout Tree，再序列化成 LaTeX。

### 1.2 99.999% 的现实含义

“全量所有公式自动还原准确率 99.999%”在工程上不可直接承诺。原因不是模型一定做不到某些简单公式，而是 PDF 输入、LaTeX 表达方式和语义等价本身存在大量不确定性。一个公式可以有多个正确 LaTeX 写法；同一个视觉结果可能由不同宏、不同字体命令、不同 spacing 命令生成；旧 PDF 可能缺 ToUnicode CMap；有些公式混入正文、编号、图片、表格、脚注或 TikZ。

但“自动 accepted 的结果达到 99.999% precision”可以作为严肃目标。换句话说，系统可以覆盖一部分高置信公式，并对这些自动写入知识库/RAG/GraphRAG 的公式做到极端保守；其余低置信公式进入候选、云端复核或人工确认，不污染正文与知识库。

统计上，99.999% precision 等价于错误率小于 1e-5。若测试集中零错误，要用 95% 置信度证明错误率低于 1e-5，按 rule of three 近似需要约 300,000 个零错误 accepted 样本；若要 99% 置信度，需要约 460,000 个零错误样本。Attention 和 Napkin 两个样本适合做回归和开发验收，但绝对不足以证明 99.999%。因此正确目标应拆为：

- accepted precision：最终严格门禁目标，长期冲 99.999%。
- accepted coverage：先低后高，不能为覆盖率牺牲 precision。
- candidate recall：尽量全量召回，让低置信公式不会丢。
- latency：阅读热路径不阻塞，后台分批提升候选质量。

### 1.3 推荐路线

推荐研发一个 `TinyBDMath` 层，放在当前多轮公式解析中的 r2 结构增强轮，而不是替代 r0。

推荐轮次：

- r0：PDF 事实抽取，不 OCR，不生成高风险 LaTeX，输出 glyph graph、line primitives、字体诊断、公式区域候选。
- r0.5：符号身份修复，利用 ToUnicode、font cmap、glyph name、AGL/texglyphlist、TeX encoding maps、同字体/code 传播、outline/path 形状候选修复未知 glyph，输出 Enriched Glyph Graph。
- r1：缓存优先，只补救图片/扫描/乱码/缺文本层区域。
- r2a：TinyBDMath，非图像、结构层小模型，从 glyph graph 推断 SLT/LaTeX 候选。
- r2b：视觉工具兜底，只处理低置信、图像化区域、扫描、缺 CMap 或 Office/PPT 中结构证据不足的区域。
- r3：云端模型只看压缩证据包，做语义校对候选，不覆盖正文。
- r4/r5：只有 accepted 结果进入 GraphRAG/知识库增量更新。

核心原则是：born-digital PDF 的默认主线必须是结构事实层，小模型做关系解析，视觉模型只是候选补救。

## 2. 研究现状和关键参考

### 2.1 MathSeer / SymbolScraper / QD-GGA / LGAP

MathSeer 的 PDF 公式抽取研究非常接近我们的方向。其 ICDAR 2021 论文页面说明，该流水线利用 born-digital PDF 中的字符信息，包含 PDF 字符抽取、公式检测和图结构公式解析。论文摘要还提到其字符抽取器能识别非拉丁符号的精确 bbox，并使用 QD-GGA 这类图解析器识别公式结构。该工作不是一个开箱即用的成熟产品，但它证明了“PDF 字符信息 + 图解析”是学术上成立的路线。

LGAP 进一步说明，数学公式识别可以被建模为图搜索问题。LGAP 从输入 primitive 构造 line-of-sight 图，并通过最大生成树得到 Symbol Layout Tree。论文指出，序列 encoder-decoder 模型准确率高但可解释性差，而图搜索方法更快、更可解释，只是早期准确率落后。LGAP 的关键启发不是照搬它的视觉 CNN，而是借鉴它的表示：把公式结构识别转为“primitive 节点 + 空间关系边 + MST/结构解码”。

这对 born-digital PDF 更有利，因为我们的 primitive 不是从二值图像连通域来的，而是 PDF 已经给出的 glyph 和绘图原语。相比 LGAP 的图片/手写场景，我们天然绕过了字符分割和字符识别的大量噪声。也就是说，LGAP/QD-GGA 的结构解析思想在 born-digital 上可能更轻、更准。

### 2.2 ChemScraper 的旁证意义

ChemScraper 是分子图解析，不是数学公式解析，但它的工程路线非常有启发。论文和项目 README 都强调，许多 PDF 包含明确的字符、线段、多边形位置和图形命令，因此可以使用 born-digital PDF primitives，而不是先栅格化再 OCR/向量化。ChemScraper 的 born-digital parser 用 PDF primitives 构建图，再转成化学图结构，论文报告其在合成 USPTO benchmark 上达到 98.4% recognition rate。这个数字不是数学公式结果，不能直接迁移，但它证明了一个重要事实：对于 PDF 中的结构化图形，直接利用 PDF primitives 可以比像素路线更快、更可解释，而且不需要 GPU。

数学公式和分子结构的共同点是：二者都是二维符号/线段结构，语义来自空间关系。差异是：化学图有较强的图结构约束，数学公式语法更广，宏和排版变体更多。因此数学任务更难，但 ChemScraper 证明了 PDF primitives 路线的工程价值。

### 2.3 PyMuPDF / Poppler / pdfminer 等事实抽取层

PyMuPDF 官方文档说明 `dict` 输出保留 blocks、lines、spans、font、size、bbox，`rawdict` 进一步提供每个字符的 `origin`、`bbox` 和字符内容。其 FAQ 也指出，如果 PDF 的编码坏了或缺少 ToUnicode，抽取结果可能是 `U+FFFD` 或乱码，这不是 PyMuPDF 自身能完全修复的问题。

这说明 PDF 结构路线必须先做分诊，并且在放弃结构路线前先尝试 r0.5 符号身份修复：

- 对现代 LaTeX/XeLaTeX/LuaLaTeX 或带良好 ToUnicode 的 PDF，结构层很有价值。
- 对缺 CMap、乱码、Type 3/旧 Type 1 字体的 PDF，不能直接丢给视觉路线；应先用 AGL/texglyphlist、fonttools、TeX encoding maps、SymbolScraper/PDFBox 对照、同字体/code 传播和 outline/path 候选尝试修复。仍无法修复或冲突严重时，才降低置信或转入视觉兜底。
- 对 Office/PPT 导出 PDF，不能按整篇判断；可复制且 bbox/font 可靠的公式仍可走结构层，图片化或结构证据不足的区域才走视觉路线。

因此，TinyBDMath 不是“万能 PDF 公式识别器”，而是“对结构层可读、并经过 r0.5 尽力修复符号身份的 born-digital 数学区域进行超高效关系解析”的模型。

### 2.4 LaTeXML / ar5iv / arXMLiv 的训练价值

LaTeXML、ar5iv 和 arXMLiv 不是 PDF 反向解析工具，而是从 LaTeX 源码生成 XML/HTML/MathML 的工具链和大规模语料。它们对本项目最有价值的地方在训练和评估：

- arXiv 源码可以作为真实论文公式分布。
- LaTeXML 可把 LaTeX 数学转成 Presentation MathML / XML，提供比原始 LaTeX 更结构化的监督。
- 编译后的 PDF 可通过 PyMuPDF/Poppler 抽取 glyph graph。
- 源端 MathML / LaTeX 与 PDF glyph graph 对齐后，可以生成大规模“PDF 结构事实 -> SLT/LaTeX”的训练样本。

这条数据路线比手工标注大量公式更现实。真实用户运行时没有源码，但训练时完全可以利用源码。

### 2.5 2026 PDF 公式抽取 benchmark 的提醒

2026 年 PDF 公式抽取 benchmark 论文和 GitHub 项目显示，当代通用文档解析器在公式抽取上差距很大。其 README 中列出多个视觉/API 工具在公式上的 LLM-as-a-judge 分数，Qwen3-VL、Gemini、Mathpix、PP-StructureV3、LightOnOCR 等视觉/API 方案分数较高，而 PyMuPDF4LLM、GROBID 这类传统文本抽取工具在公式方面明显落后。

这个 benchmark 的启示不是“结构路线没戏”，而是“只做通用文本抽取远远不够”。PyMuPDF4LLM 这类工具通常没有专门做数学 glyph graph 到结构树的建模，所以公式表现弱。TinyBDMath 的意义正是在 PyMuPDF/Poppler 的事实层之上补上数学结构解析模型，而不是让通用 PDF 文本抽取直接承担公式还原。

## 3. Born-digital PDF 结构层到底能给什么

### 3.1 PDF 不是 LaTeX AST

PDF 不是源文档格式。即使它由 LaTeX 编译而来，PDF 中通常也没有“这是 `\frac{a}{b}`”这种逻辑结构。PDF 主要是页面绘图指令：选择字体、设置文本矩阵、显示 glyph、画线、画路径、放置图片。公式语义在编译过程中被降级成视觉排版。

例如 `x^2` 在 PDF 中可能只是：

- 在主基线上画 `x`，字号 10，bbox A；
- 在更高位置画 `2`，字号 7，bbox B。

PDF 不会显式告诉我们 `2` 是 `x` 的上标。需要根据 bbox、baseline、字号比例和空间关系推断。

再如 `\frac{a}{b}` 可能是：

- 画 `a` 在上方；
- 画水平线；
- 画 `b` 在下方。

PDF 不会显式告诉我们这是分数。需要识别水平线和上下区域关系。

这说明“结构层解析公式”不是简单读取 PDF 文本，而是从排版事实重建数学结构。

### 3.2 结构层可用信号

对现代科学 PDF，以下信号非常有价值：

- Unicode 字符：通过 ToUnicode CMap 或字体映射得到。
- glyph bbox：每个字符的矩形范围。
- glyph origin：字符基准点。
- font name：CMMI、CMSY、CMR、LM、STIX、Times Math 等可以暗示数学字体类别。
- font size：上下标、脚注、矩阵单元常有字号变化。
- span flags：斜体、粗体、serif 等可作为弱特征。
- line/baseline：同一 baseline 的字符构成主写线。
- vector paths：分数线、根号横线、括号扩展部件、矩阵线等。
- image blocks：识别结构层不可用或需要视觉兜底。
- ActualText/Tagged PDF：少数文档可能有更好的语义标注。
- text extraction order：不能完全信任，但可以作为候选。

这些信号足够构建一个图：

- 节点：glyph、line segment、delimiter component、image placeholder。
- 边：相邻、遮挡、上下、包含、同 baseline、line-of-sight、delimiter pairing。
- 标签：right、sup、sub、above、below、inside、fraction numerator、fraction denominator、matrix row/col 等。

### 3.3 结构层失败模式

结构层不是总能用。主要失败模式包括：

- ToUnicode 缺失或错误，字符抽取成乱码。
- PDF 使用自定义编码，glyph code 不能可靠回到 Unicode。
- Type 3 字体或旧 Type 1 字体缺少可用映射。
- 公式符号被拆成多个 glyph 或路径，例如大括号、根号、积分号扩展件。
- `\mathbb`、`\mathcal`、`\mathbf`、`\boldsymbol` 等字体语义在 PDF 中只剩字体形状，不一定能还原成原始宏。
- ligature 或字体替换导致源字符和 PDF 字符不一一对应。
- 公式中包含 `\text{...}`，普通文本和数学结构混排。
- 多行公式、aligned、cases、matrix、array、equation number 和标点关系复杂。
- PDF 中绘制顺序不等于阅读顺序。
- 页面中公式区域检测错误，把正文、表格、图注混进公式。
- PPT/Word 导出可能让部分公式图片化，也可能保留可复制 glyph；必须区域级分流。

因此系统必须有 confidence、risk flags 和 fallback，而不能把小模型输出直接当真。

## 3A. PDF 底层规范与公式解析相关细节

本章补充 PDF 结构层的技术细节。研发 TinyBDMath 前，必须明确 PDF 能提供哪些事实，哪些信息已经丢失，哪些信息看似存在但不能信任。

### 3A.1 PDF content stream 与文本绘制指令

PDF 页面内容通常是 content stream。它不是 DOM，也不是文章结构树，而是一串图形状态和绘制操作。与文字相关的常见操作包括：

- `BT` / `ET`：开始/结束文本对象。
- `Tf`：选择字体和字号。
- `Tm`：设置文本矩阵。
- `Td` / `TD` / `T*`：移动文本位置。
- `Tj`：显示一个字符串。
- `TJ`：显示一个字符串数组，数组中可带字距调整。
- `Tr`：设置文本渲染模式。
- `Tc` / `Tw` / `Tz` / `TL`：字符间距、词间距、水平缩放、行距。

LaTeX 公式编译后，最终也落到这些操作上。PDF 不会说“这是上标”，只会说“在某个坐标用某个字体画某个 glyph”。因此，TinyBDMath 的输入本质上是渲染后事实，不是语法树。

这带来一个重要工程判断：不要试图从 PDF 恢复作者原始 LaTeX 宏。PDF 中常常没有这些信息。合理目标是恢复语义等价或视觉等价的 canonical LaTeX。

### 3A.2 字体、编码、glyph code 与 Unicode

PDF 文本抽取最核心的问题是：content stream 中的字符串不是天然 Unicode 字符串。它可能是某种 font encoding 下的 glyph code。要从 glyph code 得到 Unicode，需要字体 encoding、CID 映射和 ToUnicode CMap 等信息。

常见字体相关对象：

- Type 1：传统 PostScript 字体，旧 LaTeX PDF 常见。
- TrueType：现代字体，字符映射通常更清楚。
- Type 0 / CIDFont：复合字体，常用于 Unicode/CJK/现代 PDF。
- Type 3：用 PDF 图形操作定义 glyph，抽取风险较高。
- Embedded subset font：字体名常带随机前缀，如 `ABCDEE+CMR10`。

ToUnicode CMap 是文本抽取的关键。如果存在并正确，PDF 工具能把 glyph code 映射到 Unicode；如果缺失或错误，抽取得到的字符可能乱码、`cid:xxx`、私用区字符或 replacement character。

对数学公式，这尤其重要。LaTeX 的 Computer Modern 数学字体历史上不总是给出理想 Unicode 映射。现代 TeX engine 和宏包通常改善很多，但 arXiv 历史 PDF 中仍会遇到缺映射问题。

因此 r0 必须输出 PDF health：

- `unicode_coverage`：可映射字符比例。
- `unknown_glyph_rate`：未知 glyph 比例。
- `replacement_char_count`。
- `font_type_distribution`。
- `type3_font_present`。
- `to_unicode_present`。
- `font_subset_count`。

如果这些指标差，小模型不应 accepted。

### 3A.3 text extraction order 不等于阅读顺序

PDF 绘制顺序可以与阅读顺序不同。公式中尤其如此：编译器可能先画分数线，再画分子分母；也可能按内部盒子顺序绘制；多栏论文、页眉页脚、浮动图表会进一步打乱顺序。

因此 text extraction order 只能作为弱特征。TinyBDMath 不应依赖“抽取出的字符串就是公式顺序”。更可靠的方式是：

- 使用 bbox 和 baseline 重建主阅读线。
- 使用 line-of-sight 构造候选边。
- 使用字号/位置判别上下标。
- 使用矢量线段判断分式和根号。

### 3A.4 glyph bbox 与真实 ink bbox

PDF 工具给出的 bbox 可能是字体 metrics bbox，不一定完全等于实际墨迹范围。对大符号、组合符、Type 3 字体、旋转文本尤其明显。PyMuPDF 提供 character bbox，但 bbox 准确性仍受字体和提取方式影响。

工程上应同时保留：

- glyph bbox。
- origin。
- font size。
- span bbox。
- line bbox。
- render clip bbox 或可选像素验证。

在 verifier 中，不应要求 bbox 完全精确；应使用容差和归一化比例。

### 3A.5 vector paths：分数线、根号线与扩展符号

数学公式中的分数线、根号横线、矩阵线、括号扩展件可能不是文本，而是 vector path。PDF 抽取文字时会丢掉这些线，但 PyMuPDF 等工具可读取 drawing/path 信息。

这对非图像路线非常关键：

- 有水平线且上下有数学 glyph，是分数强证据。
- 根号符号可能一部分是 glyph，一部分是横线 path。
- 可伸缩括号可能由多个 glyph 或 path 组成。
- cases 左大括号可能是 CMEX glyph 或 path。

r0 glyph graph 不能只包含文字，还应包含 vector primitives。TinyBDMath 的 graph node 应同时支持 glyph nodes 和 vector nodes。

### 3A.6 Tagged PDF、ActualText 与 accessibility math

部分 PDF 可能包含 Tagged PDF 结构、Marked Content、ActualText 或 Alt text。理论上，这些结构可以提供更接近语义的文本，甚至可能包含 LaTeX 或 MathML。但现实科学 PDF 中覆盖不稳定，不能默认依赖。

策略：

- r0 检测 ActualText/Alt/Tagged structure。
- 若存在数学相关 ActualText，把它作为高价值 evidence。
- 但仍要通过符号覆盖和几何验证。
- 不存在时走 glyph graph。

### 3A.7 多引擎抽取的重要性

单一 PDF 库的输出不应被视为真理。PyMuPDF、Poppler、pdfminer 对字体、bbox、排序和 CMap 的处理不同。对高置信 accepted，建议引入 cross-engine evidence：

- PyMuPDF rawdict：主事实层。
- Poppler `pdftotext -bbox-layout`：文本/bbox 对照。
- pdfminer.six：字体/文本抽取对照。
- 可选 qpdf/mutool：底层对象审计。

若不同引擎对 glyph 序列和 bbox 基本一致，提升置信；若冲突，降低置信或只保留候选。

## 3B. PDF 类型分诊：不是所有“非扫描 PDF”都一样

born-digital 只是一个大类。实际应细分。

### 3B.1 A 类：现代 LaTeX 科学论文

特征：

- 文本层可复制。
- 数学符号可复制或大部分可映射。
- 字体嵌入清楚。
- glyph bbox 稳定。
- 公式大多为文本/glyph + vector。

策略：

- TinyBDMath 主线。
- 视觉工具只做低置信补救。

### 3B.2 B 类：旧 LaTeX / CMap 差的论文

特征：

- 正文可读，但公式符号乱码。
- Type 1/Type 3 字体风险高。
- 数学字体映射不完整。

策略：

- r0 提取 bbox/font/geometry。
- TinyBDMath 可推关系，但符号 identity 不可靠。
- 不自动 accepted。
- 视觉 MFR 或云端作为候选。

### 3B.3 C 类：Office/PPT/Word 导出 PDF

特征：

- 公式可能图片化，但不能默认等同于图片 PDF。
- 很多 PPT/Office PDF 中的公式仍有可复制文本、字体、glyph bbox 或矢量结构。
- 同一页上可能同时存在可结构解析公式、图片化公式和普通文本框。
- 文本碎片化、绘制顺序混乱、旋转/缩放/动画残留、对象分组会更常见。
- 布局和特效复杂。
- 公式文本可能来自 Office Math / OMML 转换结果，也可能被展平为 glyph，也可能栅格化为图片。

策略：

- 必须区域级分诊，不能把 PPT PDF 整篇归为图片路线。
- 对可复制、Unicode 覆盖好、bbox/font 稳定的公式区域，仍可走结构层/TinyBDMath，但 PDF health 风险阈值要更保守。
- 对碎片化严重但仍有 glyph 的公式区域，保留结构候选，同时用视觉候选交叉验证。
- 对真正图片化、无文本层或 text layer 明显 OCR/乱码的区域，才走视觉路线。
- 若能获取源 PPTX/DOCX，应优先从源 XML/Office Math(OMML) 提取公式结构，再与 PDF 位置对齐；这比仅从 PDF 反推更可靠。

### 3B.4 D 类：扫描 PDF / OCR text layer

特征：

- 页面主要是图片。
- text layer 可能由 OCR 产生，坐标粗糙。
- 数学符号错误率高。

策略：

- 不用 TinyBDMath accepted。
- 走视觉 OCR/MFR。
- 结构层只做辅助定位。

### 3B.5 E 类：混合 PDF

同一文档内既有 born-digital 正文，又有图片公式、扫描页、PPT 图。必须页级/区域级分流，不允许整篇一刀切。

## 3C. PDF 解析规则：哪些可以写，哪些不能写

用户明确禁止“样本特化正则、固定词表、一次性启发式函数伪装公式识别”。这不等于不能写任何规则。工程上需要区分三类规则。

### 3C.1 可以写的规则：格式事实规则

这些规则来自 PDF 标准或渲染事实：

- 解析 bbox、font、size。
- 标准化字体 subset 前缀。
- 识别 content stream 中的 drawing line。
- 计算 baseline、overlap、line-of-sight。
- 判定 ToUnicode 是否缺失。
- page/region hash。

这些不是公式解析规则，而是事实抽取。

### 3C.2 可以写的规则：通用结构合法性约束

这些规则来自数学排版一般规律，用于 verifier 或 decoder：

- tree 不能有环。
- 一个节点不能同时有两个互斥父关系。
- 分数需要 numerator/denominator 和分隔证据。
- 根号 body 应在 radical 覆盖范围内。
- 矩阵单元应形成行列结构。
- 输出符号不能无证据新增。

这些可写，但必须用于约束和验证，不应针对某篇论文或固定词表。

### 3C.3 不应写的规则：样本语义修复

以下都不应进入生产解析：

- 看到 `Attention` 就改成 `\mathrm{Attention}`。
- 看到 `softmax` 就改成 `\operatorname{softmax}`。
- 看到 `ht−1` 就直接改成 `h_{t-1}`。
- 固定列出论文中的变量名。
- 针对 Attention/Napkin 特定字体或位置写特殊分支。

这些可以作为测试中 expected case，但不能作为生产逻辑。

## 4. 为什么不应该从“手写解析器”继续扩张

项目之前已经遇到一个核心边界：不能用样本特化正则、固定词表、一次性启发式函数伪装公式识别。这个边界是正确的。

手写解析器可以做事实抽取、候选区域聚类和审计诊断，但不适合无限扩张成完整 LaTeX 还原器。原因有三点：

第一，数学排版空间太大。上下标、上下限、分式、根号、矩阵、cases、aligned、长箭头、可伸缩括号、文本片段、编号、标点、字体命令组合起来，规则数量会快速爆炸。

第二，手写规则容易对 Attention/Napkin 这类样本过拟合。比如看到 `CMMI10 + CMMI7` 就直接生成下标，短期看有效，换一本论文可能误判。正确做法是把这些作为特征，让模型和验证器在大规模数据上学习泛化规律。

第三，手写规则很难给出校准后的置信度。99.999% accepted precision 需要知道什么时候 abstain。模型输出的概率也不天然可靠，但可以通过校准、验证器和多证据一致性建立门禁。规则系统通常更难做这种统计校准。

因此，建议将自写代码限制在：

- PDF primitives 抽取和标准化。
- 公式候选区域检测。
- 图构建。
- 模型调用。
- 解码约束。
- 结果验证。
- 缓存、落库、审计和 UI。

核心“二维关系判别”交给训练出来的小模型，而不是继续写字符级 LaTeX 还原规则。

## 5. 模型任务定义

### 5.1 输入

输入不是图片，而是某个公式候选区域内的结构化对象集合。

每个 glyph node 包含：

- Unicode 字符或 unknown 标记。
- glyph id / CID / raw code。
- font name。
- font size。
- bbox：x0, y0, x1, y1。
- origin / baseline y。
- span flags。
- color。
- text extraction order。
- page-level normalized coordinates。
- 是否来自 math font。
- 是否来自 CMMI/CMSY/CMEX/CMR/LM/STIX 等类别。

每个 vector node 包含：

- path 类型：line、rect、curve、unknown。
- bbox。
- stroke width。
- length、angle。
- 是否像分数线、根号横线、矩阵线。

每个候选区域包含：

- page size。
- block bbox。
- surrounding text context。
- display/inline 初步分类。
- PDF health diagnostics：ToUnicode rate、unknown glyph rate、font type risk、image overlap。

### 5.2 输出

输出可以分三层：

1. 关系图：
   - right / horizontal
   - superscript
   - subscript
   - above
   - below
   - inside
   - numerator
   - denominator
   - over/under operator
   - radical body
   - matrix next cell / next row
   - punctuation
   - no-edge

2. Symbol Layout Tree：
   - 与 CROHME / LgEval 系统相近，可用于结构评估。

3. LaTeX 候选：
   - `\( ... \)` 或 `$$ ... $$` 定界。
   - 只使用规范化通用 LaTeX。
   - 字体宏尽量语义化，但缺证据时降置信。

### 5.3 不做什么

TinyBDMath 不应该做：

- 不从整页像素识别字符。
- 不替代 OCR/MFR 处理扫描页。
- 不猜测 PDF 中不存在的符号。
- 不为了提高表面匹配率写样本词表。
- 不直接写入正文或知识库。
- 不把云端模型当作 ground truth。

它只负责把 PDF 结构事实转成候选数学结构。

## 5A. TeX、LaTeX、MathML、SLT 与“正确公式”的定义

公式解析不能只谈模型，还必须谈目标表示。LaTeX、TeX、MathML、Symbol Layout Tree 和视觉布局之间不是一一对应关系。

### 5A.1 TeX 不是普通上下文无关语言

TeX 是宏展开语言。LaTeX 数学表达式看似有固定语法，实际包含：

- 宏定义：`\newcommand`、`\def`、`\DeclareMathOperator`。
- 参数宏：`\foo{x}{y}`。
- 环境：`equation`、`align`、`array`、`matrix`、`cases`。
- catcode 改变。
- package 扩展：amsmath、mathtools、bm、bbm、physics 等。
- style 变化：`\displaystyle`、`\textstyle`、`\scriptstyle`。
- spacing 命令：`\,`、`\!`、`\quad`。
- font 命令：`\mathbf`、`\mathcal`、`\mathbb`、`\mathrm`。

因此不能把 LaTeX 简单当成 JSON 或普通表达式语法解析。训练时若要从源码得到结构标签，最好使用成熟工具：

- LaTeXML：宏展开能力强，可转 XML/MathML。
- KaTeX/MathJax parser：适合 supported subset 和 web 渲染场景。
- pylatexenc / TexSoup：适合轻量解析，但对复杂宏展开有限。

### 5A.2 原始 LaTeX 不是唯一答案

同一个视觉公式有很多 LaTeX 写法：

- `x^2` 与 `x^{2}`。
- `\frac{a}{b}` 与 `{a \over b}`。
- `\left( x \right)` 与 `(x)`。
- `\mathrm{softmax}` 与 `\operatorname{softmax}`。
- `\boldsymbol{x}` 与 `\mathbf{x}` 在某些字体下视觉接近。
- 用户自定义宏 `\dmodel` 与展开后的 `d_{\mathrm{model}}`。

PDF 里通常已经没有“作者原始宏”。因此评估时不能要求字面完全一致。更合理的是多层目标：

- presentation structure：视觉排版结构正确。
- semantic structure：数学含义基本正确。
- canonical LaTeX：输出规范化 LaTeX，便于显示和 RAG。
- source macro recovery：仅作为额外审计，不作为必需目标。

### 5A.3 Presentation MathML 与 Content MathML

MathML 分为 Presentation MathML 和 Content MathML。Presentation MathML 描述公式排版结构，例如 `<msup>`、`<msub>`、`<mfrac>`、`<msqrt>`；Content MathML 描述数学语义，例如函数、运算和变量关系。

从 PDF 反推时，Presentation MathML / SLT 更现实，因为 PDF 保存的是排版事实。Content MathML 往往需要语义理解，单靠 PDF 坐标不够。例如 `f(x)` 是函数调用还是乘法？`|x|` 是绝对值还是竖线分隔？这需要上下文和领域知识。

因此 TinyBDMath 的目标应是 Presentation-level structure，再由 r3/GraphRAG 做语义增强。

### 5A.4 Symbol Layout Tree

Symbol Layout Tree 是数学表达式识别中的常见中间表示。它描述符号之间的空间关系，而不是直接输出 LaTeX 字符串。关系包括：

- right。
- above。
- below。
- superscript。
- subscript。
- inside。
- over/under。

SLT 对 TinyBDMath 很合适，因为：

- 它与 PDF glyph graph 直接对应。
- 可做 relation-level 评估。
- 可解释、可视化。
- 可序列化为 LaTeX 或 MathML。
- verifier 可检查每条关系的几何证据。

### 5A.5 OpenMath 与语义层

OpenMath/Content MathML 更接近数学语义，但不适合作为第一阶段目标。原因：

- PDF 缺少足够语义。
- LaTeX 本身很多时候也只是 presentation。
- 科学论文上下文中符号含义依赖定义、段落和领域。

GraphRAG 可以在 r4/r5 中逐步建立“变量/概念/公式/定理”的语义关系，但那是在公式 presentation 可靠之后的第二层任务。

### 5A.6 公式等价评估

公式评估至少需要四种层次：

1. 字符串层：
   - normalized LaTeX exact。
   - token edit distance。
   - command recall。

2. 结构层：
   - SLT exact。
   - tree edit distance。
   - relation F1。

3. 渲染层：
   - 输出 LaTeX 渲染后和 PDF 区域比较。
   - bbox/layout similarity。

4. 语义层：
   - CAS 简化比较。
   - LLM/专家判断。
   - 仅适合部分公式。

对 99.999% accepted，不能只靠字符串相似度。必须结合结构和证据验证。

## 5C. TeX 数学排版内部机制：为什么反推很难

### 5C.1 math atom / noad

TeX 在数学模式中不是直接把字符排成字符串，而是构建 math list。math list 中有 noad/atom，常见类别包括：

- Ord：普通符号，如变量和数字。
- Op：大运算符或函数操作符，如 `\sum`、`\lim`。
- Bin：二元运算符，如 `+`、`\times`。
- Rel：关系符号，如 `=`、`<`。
- Open：开括号。
- Close：闭括号。
- Punct：标点。
- Inner：内部子公式，如 `\left...\right` 或分式类结构。
- Acc/Rad/Over/Under/Vcent 等特殊结构。

很多 TeX 资料都强调，TeX 会根据 atom class 决定数学间距。比如 `+` 在某些上下文中是二元运算符，在公式开头或关系符号后可能按 ordinary 处理。这个机制说明，源 LaTeX 中的字符不仅有 glyph，还携带 math class；PDF 输出后，math class 大多只留下间距结果。

### 5C.2 nucleus、subscript、superscript

普通 math atom 通常可理解为：

- nucleus：主体。
- subscript：下标。
- superscript：上标。

`x_i^2` 在 TeX 内部是以 `x` 为 nucleus，附带 subscript `i` 和 superscript `2` 的结构。PDF 中却只剩三个 glyph 及其坐标和字号。TinyBDMath 的任务就是从坐标和字体特征反推这种父子关系。

### 5C.3 mlist_to_hlist：语义树到排版盒子的不可逆转换

TeX 会把 math list 转成 horizontal list / vertical list / boxes / glue / kern，然后输出到 DVI/PDF。转换过程中会发生：

- 宏展开。
- math class spacing。
- style 选择：display/text/script/scriptscript。
- delimiter sizing。
- fraction rule positioning。
- operator limits positioning。
- font selection。
- kerning 和 italic correction。

这一过程不是可逆的。PDF 里通常没有“这是 Bin atom”或“这是 `\limits`”。只有最终位置和形状。因此，TinyBDMath 的目标是恢复一个合理的结构树，而不是精确逆转 TeX 引擎内部状态。

### 5C.4 TeX 排版知识如何用于模型

TeX 知识不应变成样本特化规则，但可以变成通用 inductive bias：

- atom classes 可作为输出标签或 verifier 辅助。
- script style 的字号比例可作为特征。
- operator limits 的上下结构可作为关系类别。
- delimiter sizing 可作为分组证据。
- math spacing 可作为弱特征，但不能强依赖。

换句话说，TeX 机制应指导 graph schema、label taxonomy 和 verifier，而不是写死某篇 PDF 的解析规则。

### 5C.5 LaTeX 宏包和数学字体复杂性

LaTeX 宏包会改变符号和字体：

- `amsmath` 提供 align、cases、split、gather 等环境。
- `mathtools` 扩展数学结构。
- `bm` / `boldsymbol` 改变粗体数学符号。
- `amssymb` / `mathrsfs` / `bbm` / `dsfont` 引入字体语义。
- `unicode-math` 在 LuaLaTeX/XeLaTeX 中使用 OpenType math fonts。

这些在 PDF 中体现为不同字体、glyph、路径和位置。模型必须见过足够多字体/宏包组合，不能只在 Computer Modern 上训练。

## 5D. 从 PDF 到 LaTeX 的不可逆信息清单

以下信息通常已经丢失：

- 作者自定义宏名。
- 源码中的空格和换行。
- `\left...\right` 是否显式使用。
- `\dfrac`、`\frac`、`\tfrac` 的源选择。
- `\operatorname` 与 `\mathrm` 的作者意图。
- `\limits` / `\nolimits` 的显式命令，若排版结果相同。
- 某些 font command 的原始宏。
- equation 环境名称。
- alignment marker `&` 的源码位置。

以下信息可能部分保留：

- 可见符号。
- 字体形状/族。
- 上下标、分式、根号等视觉结构。
- 行列对齐。
- 编号位置。
- 分数线/根号线。

因此，项目目标必须是“可用且可审计的 canonical LaTeX”，不是“源码级还原”。

## 5E. 公式语义歧义示例

同一排版可能有多种含义：

- `f(x)`：函数调用还是乘法？
- `|x|`：绝对值、范数、条件分隔？
- `dx`：变量乘积还是微分？
- `E`：普通变量、期望算子、集合？
- `R`：变量还是实数集，取决于字体。
- `sin x`：函数名应 upright，但 PDF 可能只显示普通字母。

这些不能仅靠 PDF glyph graph 解决。TinyBDMath 应先恢复 presentation structure；语义层交给上下文/RAG/GraphRAG，并保留不确定性。

## 5B. 公式类型 taxonomy

为了研发和验收，必须把公式类型分清楚。

### 5B.1 按位置

- display formula：独占行或多行，通常区域清晰。
- inline formula：嵌在正文内，边界难。
- equation number：编号不是公式主体。
- table math：表格单元中的公式。
- figure label math：图中标签，可能图片化。

### 5B.2 按结构

- linear expression。
- subscript/superscript。
- fraction。
- radical。
- big operator with limits。
- accent / hat / bar / tilde。
- delimiter group。
- matrix / array / cases。
- aligned equations。
- piecewise。
- text-in-math。
- multi-line derivation。

### 5B.3 按字体语义

- ordinary italic variables。
- upright operators。
- bold vectors。
- blackboard bold sets。
- calligraphic symbols。
- fraktur symbols。
- Greek letters。
- mathematical alphanumeric Unicode。

### 5B.4 按证据质量

- clean Unicode glyph。
- font-inferred glyph。
- unknown glyph but stable geometry。
- vector/path component。
- image-only component。
- OCR-derived text layer。

每类都应有单独指标。否则一个平均准确率会掩盖真正失败点。

## 6. 模型架构候选

本章补充比较 GNN、CNN、Transformer、MLP、RNN 等模型族。要先强调一个前提：不同模型族擅长处理的输入形态不同。图像 OCR 场景中，CNN/ViT/Transformer 很自然，因为输入是像素网格；born-digital 结构层场景中，输入更像一个带属性的稀疏图或对象集合，因此图模型、边分类器和带集合/图归纳偏置的 Transformer 更自然。把 PDF glyph graph 强行 rasterize 成图片再用 CNN，会丢掉本来已经有的字符和坐标事实。

### 6.1 纯几何规则 baseline

第一版应先实现一个非学习 baseline，但它只能作为数据标注和错误分析工具，不能作为最终“高准确解析器”。

baseline 可以做：

- 基于字号和 baseline 的上下标初判。
- 基于水平线和上下 bbox 的分数初判。
- 基于括号高度和邻近关系的 delimiter 初判。
- 基于行距和列对齐的 matrix/aligned 初判。

这可以快速建立训练数据的 sanity check，也能帮助定位数据问题。但它必须被定位为 baseline 和 verifier 的一部分，而不是继续扩张成生产解析器。

规则 baseline 的成熟度很高，因为几何约束本身确定；但它的上限很低。它适合做三件事：

- 生成训练数据中的候选边。
- 给 verifier 提供通用合法性约束。
- 做模型输出的解释和 debug。

它不适合做自动 accepted 的唯一依据。原因是规则很容易在复杂布局中产生看似合理但语义错误的输出，并且规则系统往往没有校准过的置信度。

### 6.2 MLP：最轻量、最容易落地，但上下文弱

最轻量方案是 pairwise edge classifier。

输入每对节点的特征：

- 相对位置：dx、dy、中心距、角度。
- bbox overlap：x/y overlap ratio、IoU。
- 尺寸比：height ratio、font size ratio。
- baseline 差。
- 字符类别：变量、数字、运算符、括号、大运算符、标点。
- 字体类别差异。
- 是否有矢量线阻隔。
- line-of-sight 是否成立。

模型可以是几层 MLP，参数量非常小，推理速度极快。缺点是缺少全局上下文，容易在复杂公式中犯局部错误。

MLP 的成熟度最高，工程风险最低。scikit-learn、PyTorch、ONNX Runtime 都能轻松部署 MLP。对本项目而言，MLP 很适合做第一版 edge scorer：

- 输入两两 glyph 的几何和字体特征。
- 输出关系类别概率。
- 推理几乎可以忽略不计。
- 易于 ONNX/INT8 量化。
- 易于解释和校准。

但 MLP 有两个根本短板。

第一，它通常只看一条边，不能自然理解“全局结构”。例如 `x_i^2` 中，`i` 和 `2` 同时依附于 `x`，需要理解 sub/sup 组合；分式中，分子分母与分数线的关系需要全局区域；矩阵中，单元格关系来自行列对齐，而不是单个字符对。

第二，MLP 容易在局部相似关系中混淆。例如普通右邻和上标右邻只差几个几何特征，在不同字体和字号下阈值变化大。如果没有 message passing 或全局解码，它会把错误边打高分。

因此 MLP 推荐作为 v0 baseline 和 verifier 辅助，而不是最终主模型。

### 6.3 RNN/LSTM：历史上成熟，但不适合作为主路线

RNN/LSTM 曾经是数学公式识别和 image-to-LaTeX 的主力，尤其在 encoder-decoder + attention 流水线中常见。它的输入通常是 CNN 提取的图像特征序列，输出 LaTeX token 序列。IM2LATEX / image-to-markup 这类工作就是这个范式的重要代表。

RNN 的优点：

- 对序列输出自然。
- 工程实现成熟。
- 小模型可以很轻。
- 对简单线性公式表现不错。

但对 born-digital PDF 结构层，RNN 有明显不适配：

- PDF glyph graph 不是天然一维序列。
- 空间关系如分数、根号、矩阵、上下限本质是二维结构。
- 输入排序一旦错，RNN 输出会级联错误。
- RNN 直接输出 LaTeX 容易生成 PDF 中不存在的符号。
- 可解释性弱，不利于 99.999% accepted precision。

因此 RNN 可以作为研究对照，不推荐作为核心模型。若使用，也应只做候选生成，并受到符号覆盖和几何 verifier 强约束。

### 6.4 CNN：视觉路线成熟，但不是 born-digital 结构小模型首选

CNN 在公式图像识别中非常成熟。许多 OCR/MFR 系统都用 CNN 或 CNN+RNN/Transformer 作为视觉 encoder。CNN 擅长从像素中学习局部形状，适合扫描、手写、PPT 图片公式、缺文本层 PDF。

优点：

- 对图像输入成熟可靠。
- 公开模型和数据较多。
- 对缺 CMap、图片公式、扫描页有价值。
- CPU 上小 CNN 也可以较快。

缺点：

- 需要 rasterize PDF，丢失结构事实。
- 对小行内公式、字体语义、细小上下标可能不稳定。
- 很难证明输出符号完全来自 PDF 原始 glyph。
- 对 born-digital PDF 来说，它重复做了 PDF 已经完成的字符识别。
- 若整页跑，成本明显高于结构模型。

因此 CNN/视觉模型应该作为 `r2_visual_high_precision` 或 r1/r2 fallback，而不是 TinyBDMath 主体。它在项目中的定位是：处理 C 类文档和 B 类低置信区域，而不是替代 A 类结构层良好的 arXiv PDF。

### 6.5 Transformer：表达能力强，但要防止幻觉和过重

Transformer 在图像公式识别、文档理解和序列建模中已经非常成熟。ViT、Swin、Donut、Nougat、Pix2Text、PaddleOCR-VL 等都体现了视觉 Transformer 或多模态 Transformer 的能力。Transformer 的优势是全局注意力和强大的上下文建模。

对本项目有三种 Transformer 用法：

1. 视觉 Transformer：
   - 输入页面/公式图像。
   - 适合 OCR/MFR fallback。
   - 不符合非图像结构主线。

2. Glyph set Transformer：
   - 输入 glyph nodes 的集合，加上几何位置 embedding。
   - 输出关系或 LaTeX token。
   - 可以处理全局上下文，但需要防止无证据生成。

3. Graph Transformer：
   - 在稀疏边上做 attention。
   - 兼具图结构和全局信息。
   - 比普通 GNN 更强，但也更复杂。

Transformer 的问题是：

- 数据需求更大。
- 小模型也容易过拟合或生成 hallucinated LaTeX。
- 直接 seq2seq 输出的可解释性弱。
- ONNX/CPU 速度虽可优化，但比 MLP/GNN 重。

推荐策略：第一代不直接做 seq2seq Transformer。可以在第二代尝试 Graph Transformer 或 Set Transformer，输出边关系而不是直接输出 LaTeX。也就是说，把 Transformer 用作结构关系判别器，而不是自由生成器。

### 6.6 GNN：最匹配 PDF glyph graph 的主模型候选

更合理的第一代 TinyBDMath 是 GNN edge classifier。

步骤：

1. 构建候选边：
   - kNN。
   - line-of-sight。
   - 同 baseline 近邻。
   - 与 fraction/radical vector 相关的上下边。

2. 节点编码：
   - 字符 embedding。
   - 字体类别 embedding。
   - 几何连续特征。
   - 字号和归一化坐标。

3. 边编码：
   - pair geometry。
   - line-of-sight 类型。
   - vector separator 特征。

4. GNN message passing：
   - 2 到 4 层足够。
   - GraphSAGE/GAT/TransformerConv 都可，但应优先简单实现。

5. 边分类：
   - 对每条候选边预测关系类型。

6. 结构解码：
   - 用最大生成树、最大生成 arborescence 或约束优化选择最终关系。
   - 保证每个符号最多一个父节点。
   - 保证分数、根号、上下标等结构合法。

这种模型可以控制在 1M 到 5M 参数以内，ONNX INT8 后非常轻。CPU 上对单个公式几十到几百个节点的图推理通常可做到毫秒级到几十毫秒级。真实瓶颈更可能是 PDF 抽取和候选区域检测，而不是模型。

GNN 的成熟度如何？结论是：研究上成熟，工程库成熟，但在本项目这种“PDF 公式结构解析”垂直任务上没有现成产品。

成熟的部分：

- PyTorch Geometric、DGL 等图学习库成熟。
- GCN、GraphSAGE、GAT、GIN、TransformerConv 等模型成熟。
- 边分类、节点分类、图分类都是标准任务。
- 可以导出 ONNX 或重写轻量推理。
- 图结构天然适合表达 glyph 和空间关系。

不成熟的部分：

- PDF 公式 glyph graph 的公开大规模标注数据很少。
- 公式结构解码需要任务定制。
- 关系标签体系和 SLT 序列化需要自己设计。
- GNN 输出概率仍需 verifier 和 calibration。

所以 GNN 是“技术成熟但任务工程需要自研”。这比从零研究神经网络要轻得多，但比调用现成 OCR 模型复杂。

### 6.7 Glyph-sequence Transformer

另一种方案是把 glyph 按空间顺序排序，输入一个小 Transformer，直接输出 LaTeX token 序列。优点是实现简单，能利用上下文；缺点是可解释性差，容易 hallucinate，且很难满足 99.999% accepted precision。

这个方向可以作为 r3 前的候选生成器，但不应作为高置信 accepted 主模型。若使用，也必须受到符号覆盖验证和布局一致性验证约束。

### 6.8 传统机器学习：CRF、SVM、随机森林仍有价值

在小数据阶段，传统模型值得保留：

- SVM / RandomForest / XGBoost 可做 pairwise relation classifier。
- CRF 可建模序列或局部结构依赖。
- MST parser 可做结构解码。
- Logistic regression 可做 calibrated abstention baseline。

传统模型的优点是数据需求小、可解释、训练快、CPU 轻。缺点是表达能力有限。对项目而言，它们很适合做 v0 baseline 和 verifier 辅助，也适合做统计校准对照。

### 6.9 推荐模型路线

综合成熟度、性能、可解释性和 99.999% accepted precision 目标，推荐路线如下：

第一阶段：

- MLP / XGBoost edge classifier。
- line-of-sight candidate graph。
- 约束解码。
- 几何 verifier。

第二阶段：

- 小型 GNN edge classifier。
- 引入局部 message passing。
- per-relation calibration。
- ONNX/INT8 推理。

第三阶段：

- Graph Transformer 或 Set Transformer 增强复杂结构。
- 只输出关系/SLT，不自由生成。

视觉 CNN/ViT/Transformer 保留为 fallback，不进入 born-digital 默认主线。

### 6.10 图模型 + 约束解码是推荐方案

综合考虑，本项目应优先走：

PDF glyph graph -> GNN/edge classifier -> constrained SLT decoder -> LaTeX serializer -> verifier -> accepted/candidate gate。

原因：

- 可解释：每个 LaTeX 结构能追溯到 glyph 和 bbox。
- 轻量：不需要 CNN/VLM。
- 可校验：输出结构可与原 PDF 几何回比。
- 可增量：只需处理公式区域，不影响阅读热路径。
- 可 abstain：低置信边、结构冲突、验证失败都能候选化。

## 6A. 模型族对比表

| 模型族 | 成熟度 | 对 born-digital 结构层适配 | 速度 | 可解释性 | 主要风险 | 推荐定位 |
|---|---|---:|---:|---:|---|---|
| 几何规则 | 工程成熟 | 中 | 极快 | 高 | 上限低、易过拟合规则 | baseline/verifier |
| MLP | 很成熟 | 中 | 极快 | 中 | 缺全局上下文 | v0 edge scorer |
| SVM/XGBoost | 很成熟 | 中 | 快 | 中高 | 表达能力有限 | v0 对照/校准 |
| RNN/LSTM | 历史成熟 | 低 | 快 | 低 | 序列化损失二维结构 | 研究对照 |
| CNN | 视觉成熟 | 低 | 中 | 低 | 需要图片化、丢结构 | 视觉 fallback |
| ViT/视觉 Transformer | 视觉成熟 | 低 | 中低 | 低 | 重、幻觉、不可追溯 | 高精度视觉候选 |
| Seq2seq Transformer | 很成熟 | 中低 | 中 | 低 | 自由生成、幻觉 | candidate-only |
| GNN | 研究/库成熟 | 高 | 快 | 高 | 数据和解码需自研 | 主模型 |
| Graph Transformer | 成熟度中高 | 高 | 中 | 中高 | 较重、数据需求大 | 第二代增强 |

## 7. 数据工程方案

### 7.1 数据来源

训练数据应来自多源：

1. arXiv LaTeX 源码：
   - 覆盖真实科学论文公式分布。
   - 可编译得到 PDF。
   - 可用源公式作为监督。

2. LaTeXML / arXMLiv / ar5iv：
   - 提供 XML/MathML 结构。
   - 可作为 LaTeX 到 SLT/MathML 的中间监督。

3. 合成公式：
   - 自动生成简单到复杂的公式模板。
   - 控制字体、engine、包、布局、噪声。
   - 覆盖真实数据中稀有结构。

4. 项目测试资料：
   - Attention：小文件，适合快速回归。
   - Napkin：大文件，适合性能和真实复杂排版验收。

5. 公开 MER 数据：
   - IM2LATEX-100K 等主要是图像路线数据，但其中 LaTeX 公式可用于生成 PDF 结构训练样本。
   - CROHME/LgEval 的标签图思想可借鉴评估格式。

### 7.2 自动生成训练样本

训练样本生成流程：

1. 取 LaTeX 公式片段。
2. 规范化宏和环境，保留 display/inline 类型。
3. 编译成 PDF：
   - pdfLaTeX。
   - XeLaTeX。
   - LuaLaTeX。
   - 不同字体：Computer Modern、Latin Modern、STIX、Times、newtx、mathpazo 等。
   - 不同宏包：amsmath、mathtools、bm、bbm、mathrsfs 等。
4. 用 PyMuPDF/Poppler/pdfminer 抽取 glyph、font、bbox、vector。
5. 用 LaTeXML/KaTeX/自定义 LaTeX parser 得到目标 Presentation MathML 或 SLT。
6. 对齐 PDF glyph 与源 token/MathML node。
7. 生成图训练样本：
   - nodes。
   - candidate edges。
   - relation labels。
   - formula-level target。
   - diagnostics。

关键难点是第 6 步对齐。可以从简单场景开始：

- 单公式独立编译，页面上只有一个公式。
- 根据源 token 顺序和 PDF glyph 顺序做初对齐。
- 对宏展开产生的符号做映射。
- 对 `\frac`、`\sqrt`、上下标、矩阵等结构直接由源 AST 标注关系。

等模型稳定后，再扩展到整篇论文中的公式区域。

### 7.3 源 LaTeX 的歧义处理

LaTeX 不是唯一答案。比如：

- `x^{2}` 和 `x^2` 等价。
- `\mathrm{softmax}`、`\operatorname{softmax}`、`\text{softmax}` 在视觉上相近但语义略不同。
- `\left( ... \right)` 和普通括号视觉上可能一致。
- `\dfrac`、`\frac`、`\tfrac` 取决于 display style。
- 宏 `\dmodel` 可能渲染成 `d_{\mathrm{model}}` 或其他视觉结果。

因此训练目标应使用多层规范化：

- 结构目标：SLT/Presentation MathML。
- LaTeX 目标：规范化 canonical LaTeX。
- 源宏保留：只用于审计，不作为唯一正确答案。

生产输出应偏向通用 LaTeX，而不是试图恢复作者原始宏。原始宏恢复几乎不可能，也不是阅读/RAG 必需。

### 7.4 负样本和困难样本

如果目标是 99.999% accepted precision，负样本比正样本同样重要。

必须构造：

- 普通正文中的斜体变量误报。
- 图表标签中的数学符号。
- 公式编号。
- 表格中的数字和符号。
- 引用编号、页眉页脚、脚注。
- 多列布局边界。
- 字体乱码。
- 缺 ToUnicode。
- 低质量 OCR text layer。
- PPT 图片公式。
- 混合图片和文本公式。
- 行内小公式，如 `x_i`、`h_{t-1}`、`\mathbb{R}^n`。
- 数学字体：`\mathcal`、`\mathbb`、`\mathfrak`、`\mathbf`、`\boldsymbol`。

这些样本用于训练 abstention 和风险诊断，防止模型过度自信。

## 7A. 公开数据集与可迁移性

### 7A.1 CROHME

CROHME 是手写数学表达式识别领域最重要的数据集之一，提供表达式和结构标签，常用于 relation-level 和 expression-level 评估。它的价值在于：

- 有 Symbol Layout Tree / stroke relation 评估思想。
- 有 LgEval 等工具链。
- 对关系标签和结构评估有参考价值。

但 CROHME 是手写/笔迹场景，不是 PDF glyph graph。它不能直接训练 TinyBDMath 的 glyph/font/bbox 模型，但可以借鉴：

- 关系类别体系。
- tree edit / relation F1 指标。
- graph parsing 思路。

### 7A.2 IM2LATEX-100K / image-to-markup

IM2LATEX-100K 是图像公式到 LaTeX 的经典数据。它适合训练视觉模型，但对 TinyBDMath 的直接价值有限。不过其中的 LaTeX 公式可以重新编译成 PDF，生成结构层训练样本。

迁移方式：

- 取公式 LaTeX。
- 多字体/多 engine 编译。
- 抽取 glyph graph。
- 用源 LaTeX 生成 SLT/MathML label。

这样可以把视觉数据转化为结构数据。

### 7A.3 InftyCDB / printed expression datasets

InftyCDB 等 printed mathematical expression 数据集更接近印刷公式，但通常仍以图像或标注为主。它们可用于：

- 测试视觉 fallback。
- 对比 TinyBDMath 和图片 OCR。
- 获取复杂公式分布。

但如果没有原始 PDF glyph graph，它们不能直接提供 born-digital 训练输入。

### 7A.4 NTCIR MathIR / arXiv formula datasets

NTCIR MathIR、Tangent 等公式检索数据集基于 arXiv/MathML，适合研究公式检索和语义相似。它们对 RAG/GraphRAG 有价值，也可作为公式语义分布来源。

TinyBDMath 可利用它们的 MathML/LaTeX 公式生成训练样本，但仍需自己编译 PDF 并抽取 glyph graph。

### 7A.5 arXMLiv / ar5iv / LaTeXML corpus

这类资源是最重要的数据来源之一。它们处理大量 arXiv LaTeX 源码并生成 HTML/XML/MathML。优势：

- 分布接近真实 arXiv。
- 公式种类丰富。
- 可获取 Presentation MathML。
- 与项目目标高度一致。

限制：

- 转换失败和宏包支持问题。
- 生成的 MathML 与最终 PDF 排版不一定完全一致。
- 原始 arXiv 源码许可和批量下载需要谨慎。

### 7A.6 项目自有 Attention/Napkin 数据

Attention 和 Napkin 是项目本地验收数据，不应作为训练主数据。它们适合：

- 快速回归。
- 端到端性能测试。
- 源码对齐审计。
- 防止模型/规则过拟合检查。

不能用它们证明 99.999%，也不能为了它们写特化修复。

## 7B. 标注与对齐问题

### 7B.1 PDF glyph 到 LaTeX token 的对齐

训练 TinyBDMath 最难的不是模型，而是标签生成。需要把 PDF 中的 glyph 节点映射到源公式结构节点。

困难包括：

- 一个 LaTeX token 生成多个 glyph，如 `\notin`。
- 多个 LaTeX token 生成一个 glyph，如组合符。
- 宏展开改变 token。
- 字体命令不产生可见符号。
- spacing 命令影响位置但不产生 glyph。
- `\left...\right` 改变 delimiter 大小但结构 token 与 glyph 不一一对应。
- 矢量线段如分数线没有源字符 token。

解决路线：

- 第一阶段只做单公式独立编译，减少页面干扰。
- 使用 LaTeXML/KaTeX 得到结构树。
- 对 visible token 建立 canonical symbol 序列。
- 用 PDF glyph Unicode/font 与 visible token 对齐。
- 对无法对齐的样本标为 noisy，不进入高置信训练。

### 7B.2 弱监督与自监督

完全准确的标注成本高，可以引入弱监督：

- 由源 LaTeX 自动生成结构标签。
- 由几何 baseline 生成候选边。
- 由渲染器输出 bounding boxes 做辅助。
- 使用 consistency training：同一公式不同字体/engine 编译后结构应一致。
- 使用 contrastive learning：相同公式的不同 PDF 表示靠近，不同结构远离。

### 7B.3 噪声标签处理

自动生成数据一定有噪声。需要：

- 过滤低对齐率样本。
- 记录 alignment confidence。
- 训练时使用 label smoothing 或 sample weights。
- 单独保留 clean validation set。
- 高置信 accepted 门禁只基于 clean/人工审计集校准。

## 7C. 合成数据设计

合成数据不是为了作弊，而是覆盖结构空间。建议按 curriculum 生成：

### 7C.1 基础层

- 单变量、数字、运算符。
- 简单上下标。
- 简单分式。
- 简单根号。
- 常见希腊字母。

### 7C.2 组合层

- 上下标嵌套。
- 分式内分式。
- 根号内上下标。
- 大运算符上下限。
- 括号包围复杂表达式。

### 7C.3 复杂布局层

- matrix。
- cases。
- aligned。
- split。
- equation number。
- 多行推导。

### 7C.4 字体层

- mathbb。
- mathcal。
- mathfrak。
- mathbf。
- bm/boldsymbol。
- upright operators。

### 7C.5 PDF engine 层

- pdfLaTeX。
- XeLaTeX。
- LuaLaTeX。
- 不同字体包。
- 不同 PDF 版本。
- 带/不带 cmap/mmap。

合成数据必须和真实 arXiv 数据混合，否则模型会学到过于干净的分布。

## 7D. 训练集切分原则

不能随机按公式切分，否则同一模板/同一论文的相似公式可能泄漏到测试集。建议：

- 按 arXiv paper 切分。
- 按 subject 切分：cs、math、physics、stat。
- 按年份切分：旧 PDF 和新 PDF 分开。
- 按 engine/font 切分。
- 保留 out-of-distribution 测试集。

99.999% accepted precision 必须在未见过论文、未见过宏包组合、未见过字体组合上验证。

## 8. 验证器是 99.999% 的核心

小模型本身不可能裸奔达到 99.999%。真正的可靠性来自模型 + 验证器 + 多证据一致性。

### 8.1 符号覆盖验证

输出 LaTeX 解析成符号序列后，应与 PDF glyph 集合做对照：

- 主要符号是否全覆盖。
- 是否多生成 PDF 中不存在的符号。
- unknown glyph 是否被强行猜测。
- 字体语义是否有证据支持。
- 数字、变量、运算符数量是否一致。

如果模型输出新增了 PDF 没有的 `\sum`、`\int`、`\sqrt` 等结构，必须能在 PDF glyph/vector 中找到证据。

### 8.2 几何关系验证

将输出的 SLT 与原始 bbox 回比：

- 上标输出是否真的在右上方且字号/位置合理。
- 下标是否在右下方。
- 分数是否有水平线，分子分母是否在线上下。
- 根号是否有 radical glyph 或路径。
- 矩阵行列是否对齐。
- delimiter 是否成对且覆盖内部高度。
- 大运算符上下限是否位置匹配。

验证器不应该使用样本特化规则，但可以使用通用几何一致性约束。这不是“手写解析器”，因为它不生成结果，只用于拒绝明显不可靠结果。

### 8.3 渲染回比

把候选 LaTeX 用 KaTeX/MathJax/LaTeX 渲染成 bbox 或 SVG，再与 PDF 原区域比较：

- 符号相对布局是否一致。
- 公式宽高比例是否接近。
- 结构部件位置是否一致。
- 不要求像素完全一致，因为字体不同。

渲染回比适合做高置信 accepted 的最后门禁，但不应放在阅读热路径。可以后台批处理并缓存。

### 8.4 多抽取器一致性

同一公式区域可以从 PyMuPDF、Poppler、pdfminer 得到结构事实。如果三个引擎对字符和 bbox 基本一致，置信提升；如果差异大，降低置信。

这比多视觉 OCR 更适合 born-digital，因为它们都读取 PDF 结构，但实现细节不同，可以暴露 CMap、bbox、顺序和字体问题。

### 8.5 视觉候选作为反证

视觉模型不应替代结构层，但可以作为反证或候选：

- 如果 TinyBDMath 和 Pix2Text/Paddle/UniMERNet 在高质量 crop 上一致，置信提升。
- 如果视觉候选明显冲突，不应自动 accepted。
- 如果结构层乱码而视觉候选高置信，仍只能标记为视觉候选，除非通过其他门禁。

### 8.6 云端大模型的角色

云端模型适合做：

- 基于上下文判断候选公式是否语义合理。
- 对多个候选做解释性排序。
- 识别明显不合理的 LaTeX。
- 生成人类可读 review reason。

云端模型不适合做：

- 在没有 PDF/视觉证据时凭上下文猜公式。
- 直接 accepted。
- 替代结构验证器。

在 99.999% 目标下，云端应是 reviewer，不是 source of truth。

## 8A. 99.999% 准确率的统计与质量体系

### 8A.1 指标必须分母清楚

99.999% 这句话必须说明分母：

- 是所有 PDF 中真实存在的公式？
- 是所有被检测出的候选？
- 是所有自动 accepted 的公式？
- 是 display 公式还是 inline 公式？
- 是 LaTeX 字符串 exact，还是结构等价？
- 是在 Attention/Napkin 上，还是大规模真实 arXiv 上？

如果不定义分母，指标没有工程意义。

本文建议：

- candidate recall：尽量高，但不承诺 99.999%。
- accepted precision：长期目标 99.999%。
- accepted coverage：逐步提升。
- RAG contamination rate：必须极低。

### 8A.2 置信区间

如果测试 n 个 accepted 样本，错误 0 个，错误率上界并不是 0。rule of three 近似下，95% 置信的错误率上界约为 `3/n`。

要证明错误率小于 1e-5，即 precision 大于 99.999%，需要：

- 95% 置信：约 300,000 个零错误 accepted 样本。
- 99% 置信：约 460,000 个零错误 accepted 样本。

如果出现 1 个错误，需要更多样本才能证明同样目标。

因此短期报告只能说“设计目标和门禁朝 99.999% accepted precision”，不能说已达到。

### 8A.3 测试集必须独立

高精度声明需要：

- 不参与训练。
- 不参与阈值调参。
- 不来自同一论文模板。
- 覆盖不同年份、领域、字体、engine。
- 包含 hard negatives。
- 包含人工或强验证标注。

Attention/Napkin 是开发集和回归集，不是最终统计证明集。

### 8A.4 错误分级

不是所有错误同等严重。建议分级：

- Critical：符号、上下标、分式、矩阵结构错误，可能改变数学含义。
- Major：字体语义错误，如 `\mathbb{R}` 变 `R`。
- Minor：spacing、可选括号、等价宏差异。
- Cosmetic：LaTeX 风格不同但语义/渲染等价。

accepted precision 的 99.999% 应至少针对 Critical+Major。Cosmetic 可单独统计。

### 8A.5 Human-in-the-loop 与 gold set

极高精度需要人工 gold set。自动源码对齐和渲染验证不够，因为：

- 源 LaTeX 有宏歧义。
- 渲染等价不等于语义等价。
- 自动工具会有系统性错误。

建议建立分层 gold set：

- 1k 公式：人工精审，用于早期调试。
- 10k 公式：双人标注或强审计，用于阈值校准。
- 100k+ accepted：自动验证 + 抽样人工审计，用于长期质量。

### 8A.6 生产监控

上线后仍需监控：

- 每个模型版本的 accepted coverage。
- 用户纠错率。
- r3/人工复核推翻率。
- 每类公式错误率。
- 每类 PDF health 下的 accepted precision。

模型升级必须保留旧版本结果和 input hash，避免新模型覆盖旧 accepted。

## 8B. 安全门禁示例

自动 accepted 可以采用多级门禁：

1. PDF health gate：
   - Unicode coverage >= threshold。
   - unknown glyph rate <= threshold。
   - no severe font warning。

2. Detection gate：
   - formula region confidence high。
   - no table/prose contamination。

3. Model gate：
   - relation probabilities high。
   - no low-confidence critical edge。
   - model calibration valid for this PDF class。

4. Structure gate：
   - tree valid。
   - no impossible relation conflict。
   - delimiter/fraction/root constraints satisfied。

5. Evidence gate：
   - symbol coverage exact or near-exact。
   - geometry consistency high。
   - vector evidence for fraction/root where needed。

6. Cross-check gate：
   - optional Poppler/pdfminer agreement。
   - optional visual candidate agreement。
   - optional r3 reviewer no high risk.

7. Version gate：
   - model/verifier version allowed for auto accept。
   - evaluation record exists。

任何一关失败，只能 candidate-only。

## 9. 置信度和 accepted 策略

### 9.1 三层结果

所有公式结果分三层：

1. Raw evidence：
   - PDF glyph graph、bbox、font、vector。
   - 永远保留。

2. Candidate：
   - TinyBDMath、视觉工具、云端复核输出。
   - 可多个并存。
   - 默认不覆盖正文。

3. Accepted：
   - 通过严格门禁。
   - 可进入 RAG/GraphRAG。
   - 必须有 input hash、model version、verifier version、证据链。

### 9.2 accepted 门禁建议

自动 accepted 需要同时满足：

- PDF health 通过：ToUnicode/字符映射足够好。
- 模型置信高：关键边关系概率高。
- 结构合法：SLT 无冲突。
- 符号覆盖通过：无明显漏/增符号。
- 几何验证通过。
- 渲染回比通过。
- 多抽取器或候选一致性通过。
- 无高风险 warning。
- 当前模型版本在同分布验证集上达到门槛。

任一失败则 candidate-only。

### 9.3 coverage 策略

初期不要追求高 coverage。正确曲线是：

- 第一阶段 accepted coverage 可能只有 30%-50%，但 precision 极高。
- 第二阶段扩展到常见 display 公式。
- 第三阶段扩展到行内公式。
- 第四阶段扩展到矩阵、多行、cases、复杂字体。
- 第五阶段处理旧 PDF 和混合文档。

这比一开始承诺所有公式都自动转 LaTeX 更可靠。

## 10. 行内公式是重点难点

行内公式会极大影响阅读质量，因为它们数量多、短小、嵌在正文里，漏掉会让 RAG 和翻译质量下降。

行内公式难点：

- 公式和正文共享一行。
- 单字符变量很难区分普通斜体。
- 上下标字号很小，bbox 关系紧密。
- `h_{t-1}` 这类结构在文本抽取中可能变成 `ht−1`。
- 数学字体如 `\mathbb{R}`、`\mathbf{x}`、`\mathcal{L}` 必须保留语义。
- 标点和公式边界容易混淆。

推荐策略：

- r0 中保留 inline math candidate，不强行重写正文。
- 将 math-font span、字号差、bbox、source_context 写入 evidence。
- TinyBDMath 对短 inline 公式做关系解析，但严格 abstain。
- 对单字符变量，不应自动包成独立公式，除非上下文和字体证据足够。
- 对 `h_{t-1}`、`x_i`、`\mathbb{R}^d` 这类高频结构，模型应通过训练学到通用规律，而不是写固定词表。

验收必须单独统计 inline：

- inline recall。
- inline accepted precision。
- inline exact/semantic match。
- small formula error rate。
- math alphabet preservation rate。

## 11. 数学字体处理

数学字体不是装饰，它常常有语义：

- `\mathbf{x}` 表示向量。
- `\mathbb{R}` 表示实数集。
- `\mathcal{L}` 表示损失函数或拉格朗日量。
- `\mathsf`、`\mathtt`、`\mathfrak` 在某些领域有特定含义。
- bold Greek、bold italic、upright Greek 都可能有语义差异。

PDF 中通常不保留原始宏，但会保留字体名、glyph 形状、Unicode 数学字母或字体资源。TinyBDMath 应将数学字体作为 node feature 和输出候选的一部分：

- 如果 Unicode 已是 Mathematical Alphanumeric Symbols，如 U+1D400 系列，可直接映射。
- 如果 font name 明确指向 bbm/mathbb/mathcal/frak，应生成对应字体命令候选。
- 如果证据不充分，只输出普通符号并降低置信，或在 metadata 中记录 `font_semantics_uncertain`。

不能用固定词表说“R 一定是 `\mathbb{R}`”，必须基于字体/Unicode/bbox 证据。

## 12. 与现有项目架构的结合

### 12.1 当前多轮流程中的位置

TinyBDMath 应进入 r2，但它不是 OCR/MFR。建议将 r2 拆为两个子类：

- `r2_structural_high_precision`：非图像结构模型，优先跑。
- `r2_visual_high_precision`：视觉模型补救，只跑低置信或图片区域。

在数据库里可以仍使用 `r2_local_high_precision` round，但 result JSON 要标明：

- `backend_family: "born_digital_structure_model"`。
- `uses_ocr: false`。
- `input_hash` 来自 glyph graph，而不是 image hash。
- `model_version` 如 `tiny_bd_math_gnn_v0.1`。
- `verifier_version`。
- `accepted: false` 默认。

### 12.2 r0 与 TinyBDMath 的边界

r0 负责：

- 页面快扫。
- 提取 PDF facts。
- 公式区域候选。
- inline evidence。
- health diagnostics。
- 入队。

TinyBDMath 负责：

- 对 r0 候选区域做结构关系推断。
- 生成 SLT/LaTeX candidate。
- 输出置信和解释性边关系。

r0 不应继续膨胀成 LaTeX 重建器。

### 12.3 r3 云端输入

r3 prompt 应拿到：

- 原始 PDF evidence 摘要。
- TinyBDMath 的 LaTeX/SLT 候选。
- 关键边关系和置信。
- 视觉候选。
- verifier 通过/失败项。
- 上下文句子。

r3 输出：

- suggested_latex。
- confidence。
- reason。
- risks。
- recommended_action: accept/reject/needs_more_evidence。

但推荐 action 不等于自动 accepted。

### 12.4 RAG/GraphRAG

GraphRAG 中应区分：

- `formula_evidence_node`：PDF 原始证据。
- `formula_candidate_node`：候选公式。
- `formula_accepted_node`：通过门禁公式。
- `formula_relation_edge`：章节、定义、定理、引用、变量解释等关系。

知识库检索时：

- accepted 公式可进入正式索引。
- candidate 可作为低权重辅助，不参与严格数学事实回答。
- 低置信候选必须在回答中标注不确定，或不使用。

## 12A. 具体工程架构建议

### 12A.1 新增核心模块

建议新增：

- `src/core/pdf_glyph_graph.py`
  - 从 PyMuPDF/Poppler/pdfminer 输出统一 glyph graph。
  - 只做事实抽取和标准化。

- `src/core/symbol_identity_repair.py`
  - r0.5 符号身份修复。
  - AGL/texglyphlist/TeX encoding/font cmap lookup。
  - 同字体/code/outline 传播。
  - shape-based identity candidate 接口。
  - 输出 Enriched Glyph Graph。

- `src/core/math_structure_graph.py`
  - node/edge schema。
  - line-of-sight candidate edge 构建。
  - vector primitive 归一化。

- `src/core/tiny_bd_math_model.py`
  - 模型推理接口。
  - MLP/GNN/ONNX backend。
  - 不依赖 UI。

- `src/core/formula_structure_decoder.py`
  - relation logits -> SLT。
  - 约束解码。
  - SLT -> LaTeX。

- `src/core/formula_verifier.py`
  - 符号覆盖。
  - 几何一致性。
  - 渲染回比接口。
  - accepted gate。

- `tools/build_formula_graph_dataset.py`
  - LaTeX/PDF/glyph graph 数据生成。

- `tools/train_tiny_bd_math.py`
  - 训练脚本。

- `tools/evaluate_tiny_bd_math.py`
  - 评估和统计置信区间。

### 12A.2 数据库存储

建议在现有 formula index store 上扩展：

- glyph graph artifact 表：
  - `doc_hash`
  - `page_num`
  - `region_id`
  - `input_hash`
  - `extractor`
  - `extractor_version`
  - `graph_json`
  - `pdf_health_json`

- structure model result：
  - `candidate_id`
  - `stage = structural_precise`
  - `model`
  - `model_version`
  - `input_hash`
  - `slt_json`
  - `latex`
  - `relation_confidence`
  - `verifier_json`
  - `accepted`

- symbol identity repair artifact：
  - `doc_hash`
  - `region_id`
  - `glyph_graph_input_hash`
  - `repair_version`
  - `repair_profile`
  - `repaired_graph_json`
  - `unknown_before`
  - `unknown_after`
  - `conflict_count`
  - `repair_sources`

### 12A.3 异步调度

TinyBDMath 虽然轻量，也不应进入热路径。调度策略：

- 文档打开后 r0 快速入队。
- r0.5 对 unknown/high-risk glyph 区域优先运行，修复结果落库并按 input hash 跳过。
- 视口附近页面优先做 glyph graph。
- display 公式优先于 inline。
- 高风险公式进入 r2 structural。
- 低置信再进入 r2 visual。
- r3 只处理仍有价值的候选。

### 12A.4 与现有 r2 visual 工具区分

当前 r2 local precise 混有 Pix2Text/Paddle/MinerU 等视觉或页级工具。应在 result JSON 中明确：

- `backend_family: "pdf_structure"`。
- `backend_family: "visual_mfr"`。
- `backend_family: "document_parser"`。
- `uses_ocr: true/false`。
- `input_kind: "glyph_graph" | "image_crop" | "pdf_page"`。

这样 fusion 才能知道哪个候选来自结构证据，哪个来自图片 OCR。

### 12A.5 模型版本与跳过机制

每次模型/decoder/verifier 改变，都应改变 input 或 model hash：

- `extractor_version`
- `graph_schema_version`
- `model_version`
- `decoder_version`
- `verifier_version`
- `threshold_profile_version`

二次打开时，如果这些版本和 input hash 相同，直接跳过。若 threshold profile 更新，只需重跑 gate，不必重跑 glyph extraction。

## 12B. 开发里程碑：从可行到可用

### 12B.1 v0：无模型，graph schema 和 verifier

目标不是准确率，而是把数据打通：

- r0 生成 glyph graph。
- baseline 生成候选关系。
- verifier 能指出为什么不 accepted。
- Attention/Napkin 可视化审计。

### 12B.2 v1：MLP edge scorer

- 训练 pairwise MLP。
- 只覆盖简单 display 公式。
- accepted gate 极严。
- 目标：少量公式 accepted，但零 critical error。

### 12B.3 v2：GNN relation parser

- 引入 message passing。
- 覆盖分数、上下标、根号、大运算符。
- display 公式 coverage 提升。

### 12B.4 v3：inline 和数学字体

- 专门训练 inline。
- 加强 math alphabet。
- 保持 strict abstention。

### 12B.5 v4：复杂结构

- matrix、cases、aligned。
- 多行公式。
- equation number 分离。

### 12B.6 v5：规模化质量证明

- 大规模 arXiv 测试。
- accepted precision 统计置信区间。
- 用户纠错闭环。

## 12C. Office/PPT PDF 的专门处理策略

PPT 类 PDF 不能简单归入图片路线。它们的正确处理策略是“对象/区域级证据分流”。

### 12C.1 区域证据分类

每个公式候选区域应分类：

- `office_text_math_like`：存在可复制 glyph，字体/字号/bbox 有数学结构迹象。
- `office_vector_math_like`：大量矢量线/符号路径，文本不完整。
- `office_image_math_like`：区域主要是 image block。
- `office_mixed_math_like`：glyph、vector、image 混合。
- `office_omml_source_available`：若用户提供 PPTX/DOCX，可从 OMML 直接提取。

### 12C.2 结构层仍然可用的情况

如果 PPT PDF 中公式可复制，且 glyph identity 和 bbox 稳定，TinyBDMath 可以使用。区别是：

- 文本框绘制顺序更乱。
- 同一公式可能被拆成多个 text box。
- baseline 可能受缩放/旋转影响。
- 字体可能不是 TeX math font。
- 公式和装饰/动画元素混杂。

因此 Office/PPT PDF 应使用更保守的 accepted gate，更多进入 candidate-only。

### 12C.3 源文件优先

如果用户有 `.pptx` 或 `.docx`，优先解析源文件中的 OMML。OMML 本身比 PDF 更接近公式结构。推荐路线：

- 从 PPTX/DOCX zip 中读取 XML。
- 提取 `m:oMath` / `m:oMathPara`。
- 转换为 MathML/LaTeX。
- 与 PDF 页面对象位置做弱对齐。

这条路线不影响 PDF-only 场景，但对 Office 文档来源很有价值。

### 12C.4 Office/PPT PDF 的 r0 分诊指标

Office/PPT PDF 的结构层可用性不能只看“是否有文本层”，应使用更细指标：

- candidate region 内是否存在可复制字符。
- 字符是否主要是数学符号、变量、数字、运算符或括号。
- Unicode coverage 是否足够。
- glyph bbox 是否形成稳定 baseline、上下标或二维结构。
- 字体是否保留数学语义，或至少能区分正文/公式。
- 是否存在 image block 与 glyph 重叠。
- 是否存在旋转、非均匀缩放、透明度、clip path。
- 绘制顺序是否极度碎片化。
- 同一公式是否跨多个 text object。
- region 内 prose density 是否过高。

这些指标决定候选进入：

- `r2_structural_high_precision`。
- `r2_visual_high_precision`。
- `r2_mixed_office_review`。
- 或直接 candidate-only。

### 12C.5 OMML 的价值和限制

Office Math Markup Language(OMML) 比 PDF 更接近公式结构。若源文件可用，它可能直接包含分式、上下标、矩阵、根号等结构。对 PPT/Word 来源的资料，OMML 路线可能比 PDF 反推更准确。

但 OMML 也有边界：

- 用户未必提供源文件。
- PDF 页面对齐仍需做。
- PPT 中公式可能被转成图片后再插入。
- 公式可能经过手工拆分、缩放或组合。
- OMML 到 LaTeX 仍需转换和验证。

因此 OMML 应是可选高价值 evidence，不应替代 PDF-only 流程。

### 12C.6 PPT 可复制公式的 TinyBDMath 处理差异

对 PPT 可复制公式，TinyBDMath 仍可工作，但训练和 gate 要单独考虑：

- 字体集合不同于 TeX math fonts。
- 坐标可能来自文本框布局而不是 TeX box。
- 上下标位置和字号比例可能与 TeX 不同。
- 分式线/根号可能是 Office 绘制对象。
- 行内/显示公式边界和普通文本框关系更复杂。

因此应把 Office/PPT 作为单独 domain：

- `pdf_domain = latex_pdf | office_pdf | mixed_pdf | scanned_pdf`。
- 每个 domain 使用不同 threshold profile。
- 不同 domain 的 accepted precision 分开统计。

不能用 arXiv LaTeX PDF 的阈值直接接受 PPT PDF 公式。

## 13. 性能预估

### 13.1 热路径

阅读热路径只做：

- 基础 PDF 渲染。
- 文本/块解析。
- r0 轻量任务入队或小批事实抽取。

TinyBDMath 不应阻塞打开、滚动、缩放、双击翻译。

### 13.2 后台结构模型

结构模型的输入是几十到几百个 glyph node，而不是页面图像。推理成本比视觉模型低几个量级。

粗略预估：

- PDF page rawdict 抽取：取决于页复杂度，通常毫秒到几十毫秒级，复杂页更高。
- 公式区域图构建：毫秒级。
- 小 GNN 推理：单公式毫秒到几十毫秒级。
- verifier：轻量几何验证毫秒级；渲染回比更慢，适合后台。

100 页 arXiv 论文如果结构层良好，TinyBDMath 后台精扫理论上应做到秒级到几十秒级，而不是视觉路线的分钟级。真实速度取决于 Python 实现、数据库写入、公式数量和 verifier 强度。

### 13.3 内存

模型小，内存主要来自：

- PDF 文档对象。
- glyph graph JSON。
- 批量缓存。
- 渲染回比临时文件。

与 Pix2Text/MinerU/Paddle VLM 相比，结构小模型可控得多，适合 16G CPU 电脑。

## 13A. 轻量部署与运行时选择

### 13A.1 训练框架

训练阶段可以用 PyTorch + PyTorch Geometric 或 DGL。理由：

- 图学习组件成熟。
- 研究迭代快。
- 边分类和 message passing 实现方便。

但生产主程序不应依赖重训练框架。训练和推理要分离。

### 13A.2 推理框架

生产推理可选：

- ONNX Runtime CPU。
- 纯 NumPy MLP baseline。
- 轻量自实现 GNN 推理。
- TorchScript 作为过渡。

如果模型只是 MLP，ONNX/NumPy 都非常轻。如果是 GNN，ONNX 支持会复杂一些，因为动态图和稀疏操作不总是友好。可考虑：

- 固定最大节点数 padding。
- 使用 dense adjacency 小图推理。
- 或实现简单 GraphSAGE/GAT 前向。

公式区域通常节点数不大，dense 小矩阵也可接受。

### 13A.3 量化

可使用：

- dynamic INT8 quantization。
- float16 不一定适合 CPU。
- per-channel quantization 视框架支持。

GNN/MLP 对精度敏感性需评估。accepted gate 不应因为量化版本变化而共用同一 calibration。

### 13A.4 常驻 worker

即使模型小，也建议 worker 化：

- 避免 UI 线程阻塞。
- 批处理多个公式 graph。
- 独立崩溃恢复。
- 便于版本隔离。

与视觉工具不同，TinyBDMath worker 可以集成在主环境或轻量子进程；若依赖 PyTorch Geometric，则应隔离环境。

### 13A.5 性能目标

初步性能目标：

- glyph graph extraction：每页 < 50ms 作为长期目标，复杂页允许更高。
- graph construction：每公式 < 5ms。
- MLP inference：每公式 < 1ms。
- GNN inference：每公式 < 10ms。
- decoder + verifier light：每公式 < 10ms。
- render verifier：后台可慢，< 200ms/公式。

这些是目标，不是当前已证明指标。实际需要 benchmark。

## 13B. 与视觉路线的性能组合

结构模型跑得快，但覆盖有限；视觉模型慢但兜底强。推荐调度：

- A 类 PDF：先跑 TinyBDMath。
- TinyBDMath accepted：不跑视觉。
- TinyBDMath candidate-only 且区域重要：跑视觉候选。
- PDF health 差：直接视觉候选。
- 用户高精度模式：允许视觉全量。

这样能把昂贵视觉推理集中到真正需要的区域。

## 14. 研发阶段规划

### 阶段 0：问题收敛

目标：

- 明确 TinyBDMath 只处理结构层可读的 born-digital 公式。
- 明确 99.999% 是 accepted precision 目标，不是全量无条件自动转换目标。

产物：

- PDF health classifier。
- 公式类型 taxonomy。
- 评估指标定义。

### 阶段 1：数据与审计基线

目标：

- 从 Attention/Napkin 和一批 arXiv 论文生成 glyph graph。
- 建立 LaTeX 源码对齐审计。
- 统计 ToUnicode、字体、unknown glyph、inline/display 分布。

产物：

- glyph graph schema。
- source alignment report。
- hard negative set。
- baseline 错误分类。

### 阶段 2：结构图训练样本生成

目标：

- 用 LaTeX 公式自动生成 PDF + glyph graph + SLT/MathML label。
- 覆盖基础结构：right/sub/sup/fraction/sqrt/operator limits/delimiter/matrix。

产物：

- 训练集、验证集、测试集。
- canonical LaTeX/MathML 转换。
- relation label generator。

### 阶段 3：TinyBDMath v0

目标：

- 训练边关系 MLP/GNN。
- 输出 SLT 和 LaTeX candidate。
- 不自动 accepted。

产物：

- ONNX 模型。
- 推理 worker。
- result JSON schema。
- relation-level F1、SLT exact、LaTeX semantic score。

### 阶段 4：Verifier v0

目标：

- 实现符号覆盖、几何一致性、结构合法性验证。
- 建立 abstention 门禁。

产物：

- verifier report。
- accepted/candidate/rejected 三分输出。
- strict accepted precision 测试。

### 阶段 5：接入项目多轮流程

目标：

- TinyBDMath 作为 r2 结构候选 worker。
- r3 可读取 TinyBDMath evidence。
- r4/r5 只接收 accepted。

产物：

- r2_structural candidate results。
- Attention/Napkin 多轮准确率递增报告。
- 二次打开 input hash 跳过。

### 阶段 6：规模化验证

目标：

- 至少万级公式测试。
- 长期向 30 万 accepted 零错误样本推进。

产物：

- acceptance calibration。
- per-domain metrics。
- regression dashboard。
- failure taxonomy。

## 15. 风险清单

### 15.1 技术风险

- 源公式与 PDF glyph 对齐难。
- 旧 arXiv PDF 缺 ToUnicode。
- 字体宏恢复不唯一。
- 行内公式边界困难。
- 多行公式和矩阵结构复杂。
- 验证器过严导致 coverage 很低。
- 验证器过松导致 accepted precision 不够。
- LaTeX 语义等价评估困难。

### 15.2 工程风险

- 训练数据管线比模型更复杂。
- Windows 上 TeX/LaTeXML/Poppler 环境维护成本高。
- 大规模 arXiv 编译耗时和磁盘占用大。
- GPL/AGPL 工具可能影响分发策略。
- 云端模型不能作为开源可分发核心能力。
- 多工具候选融合容易变成复杂但不准。

### 15.3 产品风险

- 用户可能误以为所有公式都已完美还原。
- 低置信候选进入 RAG 会产生错误回答。
- 过度追求 99.999% 导致几乎没有 accepted coverage。
- 只在 Attention/Napkin 上调优导致泛化失败。

## 15A. 失败模式与缓解策略矩阵

| 失败模式 | 表现 | 检测信号 | 缓解 |
|---|---|---|---|
| ToUnicode 缺失 | 符号乱码 | unknown glyph rate 高 | 不 accepted，视觉候选 |
| 字体语义缺失 | `\mathbb{R}` 变 `R` | font semantics uncertain | 降置信，保留字体 evidence |
| inline 边界错误 | 正文被包进公式 | prose contamination | inline 专门模型 + context gate |
| 上下标误判 | `ht−1` 没还原 | script-size evidence 弱/冲突 | GNN + verifier，不确定候选 |
| 分数线漏检 | `a/b` 结构错误 | vector line missing | 多引擎 drawing 抽取 + 视觉反证 |
| equation number 混入 | 输出含 `(1)` | region right-side number | 编号检测，低置信隔离 |
| 矩阵列错 | array 对齐错误 | row/col consistency fail | matrix-specific decoder |
| 宏歧义 | canonical 与源码不同 | source exact low but render ok | 结构/渲染评估，不以源码 exact 唯一判定 |
| 视觉候选幻觉 | 输出不存在符号 | symbol coverage fail | 不 accepted |
| 云端猜测 | 根据上下文补符号 | no PDF evidence | prompt 和 gate 禁止 |

## 15B. 法律、许可与分发风险

### 15B.1 开源工具许可

MathSeer、SymbolScraper、LaTeXML、Poppler、PyMuPDF、pdfminer、DGL/PyG、ONNX Runtime 等工具许可不同。项目如果要开源分发，需要逐项确认：

- 是否允许商业/教育分发。
- 是否 GPL/AGPL 传染。
- 是否只能作为命令行外部工具调用。
- 模型权重许可。
- arXiv 源码和生成数据是否可再分发。

建议：

- 核心 TinyBDMath 自研，宽松许可。
- GPL 工具作为可选命令行 worker，不直接链接进核心库。
- 训练数据生成脚本开源，但不直接打包大规模 arXiv 派生数据，除非许可明确。

### 15B.2 云端依赖

云端模型不能作为开源可分发核心能力。它可以作为可选 r3 reviewer，但默认本地流程必须可运行。

### 15B.3 模型权重

如果训练自己的小模型：

- 记录训练数据来源。
- 记录许可。
- 提供 model card。
- 记录适用范围和已知失败模式。

## 15C. 伦理和用户信任

公式错误可能导致问答、学习笔记和知识图谱错误。UI 应清楚区分：

- 原 PDF 显示。
- 机器候选。
- 云端建议。
- 已接受结果。

不要把 candidate-only 包装成确定知识。

## 16. 开源与分发策略

如果目标是可开源、人人可用：

- TinyBDMath 模型必须本地可运行。
- 训练数据生成脚本可以开源，但 arXiv 源码/转换结果分发要注意许可。
- 不依赖云端 API 作为核心能力。
- 外部 GPL/AGPL 工具只能作为可选独立 worker 或研究参考，不能不加考虑地链接进主程序。
- 模型权重应尽量小，支持 CPU/ONNX/INT8。
- 视觉工具和云端工具作为可选增强。

## 17. 与全图片化路线的比较

全图片化路线的优点：

- 工程路径直观。
- 能处理扫描/PPT/图片公式。
- 现有工具多。
- 对缺 ToUnicode 的旧 PDF 有兜底价值。

缺点：

- 慢，尤其 CPU 上整页处理。
- 小公式和行内公式容易漏。
- 丢失 PDF 原始字符/字体/bbox 证据。
- 很难解释每个输出来自哪个 glyph。
- 高准确 accepted 更难证明。
- 对 born-digital arXiv 论文没有充分利用已存在的信息。

TinyBDMath 路线的优点：

- 极轻量。
- 可解释。
- 可审计。
- 可追溯到 PDF glyph。
- 适合高置信门禁。
- 与 RAG/GraphRAG 的证据链天然兼容。

缺点：

- 数据工程复杂。
- 不能处理扫描/PPT 图片公式。
- 初期覆盖率可能低。
- 缺成熟开箱工具，需要研发。

因此最佳策略不是二选一，而是结构主线 + 视觉兜底。

## 18. 对当前项目的具体建议

### 18.1 立即停止的方向

- 不要把 Pix2Text/MinerU 页级识别作为 born-digital 默认主线。
- 不要继续扩张手写 LaTeX 重建规则。
- 不要用云端模型直接猜公式并写入知识库。
- 不要用少量样本相似度宣称 99.999%。

### 18.2 应立即补的文档/设计

- `docs/tiny_born_digital_math_model_design.md`
- `docs/born_digital_formula_dataset_plan.md`
- `docs/formula_verifier_acceptance_gate.md`
- `docs/formula_accuracy_statistics.md`

### 18.3 应立即做的代码方向

- 完善 r0 glyph graph 输出 schema。
- 将 inline evidence 升级为统一 node/edge graph。
- 新增 `r2_structural_tiny_model` worker 接口，先用 fake/baseline 输出。
- 新增 verifier skeleton。
- 修改 fusion：区分 structural local precise 和 visual local precise。
- 测试：任何 visual 工具结果不得覆盖结构 accepted。

### 18.4 Attention/Napkin 验收

每轮验收应输出：

- display formula recall。
- inline formula recall。
- math alphabet preservation。
- relation F1。
- SLT exact。
- LaTeX semantic similarity。
- accepted precision。
- accepted coverage。
- candidate recall。
- latency。
- cache skip rate。
- hardcoding audit。

## 19. 推荐模型细节

### 19.1 Node feature

建议特征：

- normalized bbox：x0, y0, x1, y1, cx, cy, w, h。
- normalized font size。
- baseline offset。
- character class one-hot。
- Unicode codepoint embedding。
- font family embedding。
- math font family flags。
- extraction engine flags。
- unknown/missing mapping flags。
- vector/glyph type flag。

### 19.2 Edge feature

建议特征：

- dx, dy, angle, distance。
- x/y overlap。
- height ratio。
- baseline delta。
- font size ratio。
- line-of-sight flag。
- nearest-left/right/up/down rank。
- vector separator flag。
- same span/line flag。
- order delta。

### 19.3 Relation classes

第一版 relation classes：

- HORIZONTAL
- RSUB
- RSUP
- SUBSUP
- ABOVE
- BELOW
- NUMERATOR
- DENOMINATOR
- INSIDE
- RADICAL_BODY
- OPERATOR_UNDER
- OPERATOR_OVER
- MATRIX_RIGHT
- MATRIX_DOWN
- PUNCT
- NO_EDGE

后续再扩展 accent、overline、underline、arrow labels、cases、boxed、text run。

### 19.4 解码

推荐：

- 先按 edge logits 构造候选图。
- 对每个 node 选择父边，约束无环。
- 用 MST/maximum arborescence 得到主结构。
- 对 fraction/radical/matrix 使用局部约束修正。
- 输出 SLT。
- SLT 序列化 LaTeX。

这里的局部约束是数学结构合法性约束，不是样本特化规则。

### 19.5 校准

模型概率必须校准：

- temperature scaling。
- validation reliability diagram。
- per-relation threshold。
- per-PDF-health threshold。
- uncertainty from ensemble/dropout。

accepted 门禁不能只看 softmax 最大值。

## 20. 评估指标设计

### 20.1 模型内部指标

- node coverage。
- edge relation precision/recall/F1。
- SLT exact match。
- tree edit distance。
- formula structure exact。
- per-class F1：sub/sup/fraction/sqrt/matrix。

### 20.2 LaTeX 指标

- canonical string exact。
- token edit distance。
- normalized command recall。
- semantic equivalence judge。
- render similarity。

纯字符串相似度不能作为最终质量指标，因为 LaTeX 表达有多种等价写法。

### 20.3 产品指标

- accepted precision。
- accepted coverage。
- candidate recall。
- low-confidence non-overwrite rate。
- RAG contamination rate。
- second-open skip rate。
- per-page latency。
- background throughput。

### 20.4 统计证明

若要声称 accepted precision >= 99.999%，需要：

- 独立测试集。
- 样本量足够。
- 人工或强验证确认。
- 零错误或极少错误。
- 报告置信区间。

在没有 30 万级 accepted 验证前，只能说“朝该目标设计”，不能声称达到。

## 21. 最终建议

我建议项目把 born-digital PDF 公式的核心研发目标从“堆更多 OCR 工具”调整为：

> 构建一个基于 PDF glyph graph 的极轻量数学结构解析模型，用严格 verifier 实现高精度 accepted 门禁；视觉工具和云端模型作为候选补救与复核，不进入默认热路径，不污染知识库。

这条路线的技术难度主要在数据和验证，而不在模型规模。模型可以很小，但数据管线必须严谨。最重要的工程原则是：宁愿少 accepted，也不能错 accepted；宁愿保留候选，也不能污染 RAG。

如果继续执行，下一步最合理的具体工作不是安装更多视觉工具，而是：

1. 固化 glyph graph schema。
2. 从 Attention/Napkin 输出真实 glyph graph + 源码对齐报告。
3. 搭建 synthetic LaTeX -> PDF -> glyph graph -> SLT 数据生成器。
4. 训练一个最小 edge classifier baseline。
5. 实现 verifier。
6. 接入 r2 structural candidate-only。
7. 用严格统计报告 accepted precision 和 coverage。

## 21A. 研发决策树

如果目标是快速提升当前产品：

- 优先做 r0 glyph graph schema。
- 优先做 verifier 和 accepted gate。
- TinyBDMath 先用 MLP baseline。
- 视觉工具作为 fallback。

如果目标是论文/创新：

- 重点做 GNN/Graph Transformer。
- 做 arXiv LaTeX -> PDF -> graph 数据集。
- 与 LGAP/QD-GGA、视觉 MFR 做对比。
- 提出 accepted precision + abstention benchmark。

如果目标是实际 99.999%：

- 不追求全覆盖。
- 建立严格 accepted 门禁。
- 做大规模独立测试。
- 加入人工审计和生产纠错闭环。

## 21B. 下一步最小可执行计划

### 第 1 周：事实层

- 完成 glyph graph schema。
- 输出 Attention/Napkin 每个公式区域的 node/edge JSON。
- 统计 PDF health。
- 可视化 glyph graph。

### 第 2 周：数据层

- 建立 synthetic LaTeX formula compiler。
- 生成 1k 简单公式训练样本。
- 建立 SLT/LaTeX canonical target。
- 做 alignment report。

### 第 3 周：模型 v0

- 训练 MLP edge classifier。
- 实现 decoder。
- 在简单公式上验证 relation F1。
- 接入 r2 candidate-only。

### 第 4 周：verifier

- 实现 symbol coverage。
- 实现 geometry verifier。
- 实现 strict gate。
- Attention/Napkin 输出 accepted/candidate/rejected。

### 第 5-8 周：GNN 与规模化

- 训练小 GNN。
- 扩展到 fraction/sqrt/matrix/inline。
- 建立 10k+ 测试。
- 报告 accepted precision/coverage。

## 21C. 对 99.999% 的最终立场

在本项目当前阶段，不应承诺“所有公式 99.999% 自动还原”。应承诺并设计：

- 所有结果可追溯。
- 低置信不覆盖正文。
- 自动 accepted 极端保守。
- accepted precision 长期向 99.999% 证明。
- coverage 通过模型和数据逐步提升。

这是唯一既科学又工程可行的表述。

## 22. 参考资料

- MathSeer PDF 公式抽取框架：<https://shahayush.com/publications/2021-09-01-mathseer-pipeline>
- MathSeer / Graphics Extraction Pipeline GitLab：<https://gitlab.com/dprl/graphics-extraction>
- LGAP 论文 PDF：<https://www.cs.rit.edu/~rlaz/files/ICDAR_2023_Math_parsing.pdf>
- LGAP/QD-GGA parser GitLab：<https://gitlab.com/dprl/qdgga-parser>
- ChemScraper arXiv：<https://arxiv.org/abs/2311.12161>
- PyMuPDF text extraction docs：<https://pymupdf.readthedocs.io/en/latest/app1.html>
- PyMuPDF FAQ：<https://pymupdf.readthedocs.io/en/latest/faq/index.html>
- Unicode in PDF / ToUnicode 说明：<https://unicodefyi.com/guide/unicode-in-pdf/>
- PDF Association ToUnicode CMap 资料：<https://pdfa.org/understanding-the-pdf-file-format-to-unicode-mapping/>
- arXMLiv 项目：<https://kwarc.info/projects/arXMLiv/>
- ar5iv GitHub：<https://github.com/dginev/ar5iv>
- LaTeXML manual/examples：<https://math.nist.gov/~BMiller/LaTeXML/examples.html>
- pylatexenc LatexWalker：<https://pylatexenc.readthedocs.io/en/latest/latexwalker/>
- KaTeX parser source：<https://github.com/KaTeX/KaTeX>
- PDF 公式抽取 benchmark arXiv：<https://arxiv.org/abs/2512.09874>
- pdf-parse-bench GitHub：<https://github.com/phorn1/pdf-parse-bench>
- CROHME/LgEval 说明：<https://www.cs.rit.edu/~dprl/old/CROHMELib_LgEval_Doc.html>
- MathNet / printed mathematical expression recognition：<https://arxiv.org/abs/2404.13667>
- IM2LATEX-100K 原始 image-to-markup 项目：<https://github.com/harvardnlp/im2markup>
- PyTorch Geometric：<https://pytorch-geometric.readthedocs.io/>
- DGL：<https://www.dgl.ai/>
- ONNX Runtime quantization：<https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html>

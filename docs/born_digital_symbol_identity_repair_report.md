# Born-digital PDF 符号身份修复层 r0.5 深度调研报告

日期：2026-05-25

实现状态：r0.5 MVP 已落地到 `src/core/symbol_identity_repair.py`，并通过 `FormulaScanRound.SYMBOL_IDENTITY_REPAIR` 写入 `formula_round_jobs`。当前版本只做保守非视觉身份修复：已知 PDF Unicode 直接保留，`glyph_name` 走内置静态映射，同 `normalized_font+cid` 的已知锚点可传播；冲突或证据不足时保留 unknown 并写 warnings。同 input hash 二次运行跳过已完成记录。真实 AGL/texglyphlist/TeX encoding/fonttools cmap、outline/path shape classifier 仍是下一步。

## 1. 结论

此前 TinyBDMath 方案中有一个关键缺口：r0 能发现 ToUnicode 缺失、乱码、未知 glyph，但处理策略偏粗，容易把仍可在结构层修复的 PDF 过早交给视觉 OCR。正确路线是在 r0 PDF 事实抽取和 r2a TinyBDMath 结构解析之间加入一个独立的 r0.5 符号身份修复层。

r0.5 的目标不是“看图 OCR”，也不是凭上下文猜符号，而是尽可能利用 PDF 仍然保留的非视觉结构证据恢复符号身份：

- 字体名。
- glyph name。
- character code / CID。
- font encoding。
- ToUnicode / cmap / embedded font table。
- TeX 字体编码。
- 同文档重复出现模式。
- glyph bbox/advance/outline。
- vector path 轮廓。
- 公式结构角色。

结论：

- r0.5 必须纳入架构，否则 born-digital 路线会过早放弃大量旧 arXiv/pdfLaTeX/TeX 字体 PDF。
- 静态映射层成熟度最高，应优先实现。
- 上下文传播有价值，但必须保守，只能传播同字体/同 code/同轮廓或强锚点证据，不能用语言模型式猜测。
- glyph shape classifier 可行，尤其对 Type 3/path glyph 或缺 Unicode 的数学符号，但它仍是候选身份层，不应直接 accepted。
- r0.5 输出的是 Enriched Glyph Graph，附带 `identity_source`、`identity_confidence`、`repair_trace` 和候选列表，供 TinyBDMath、verifier 和 fusion 使用。

## 2. 为什么 r0.5 是关键瓶颈

Born-digital PDF 结构解析分两件事：

1. 符号身份：这个 glyph 到底是 `x`、`\sum`、`\int`、`\alpha` 还是未知？
2. 结构关系：这个符号和其他符号是什么关系，上标、下标、分子、分母还是同基线右邻？

TinyBDMath 主要解决第二件事。但如果第一件事缺失，结构模型只能知道“这里有一个未知符号”，无法输出可靠 LaTeX。把所有未知符号都直接转视觉路线会浪费 born-digital PDF 中还存在的大量证据。

典型场景：

- pdfLaTeX 旧论文使用 Computer Modern Type 1 字体，ToUnicode 不完整。
- PDF 文本复制时数学符号乱码，但 glyph name 或 font encoding 仍能恢复。
- 子集字体名被改写，但 font program 中仍有 glyph names/cmap。
- Type 3 字体没有 Unicode，但 glyph outline 非常清楚。
- 同一文档中某个 glyph code 有些地方映射成功，有些地方失败，可通过锚点传播修复。

这些都不应该直接进入视觉 OCR。

## 3. r0.5 的输入输出

### 3.1 输入

来自 r0 的 PDF facts：

- page id / block id / region id。
- glyph nodes：
  - raw text。
  - Unicode 或 unknown。
  - char code / CID。
  - glyph name。
  - font resource name。
  - font full name / base font。
  - font subtype。
  - embedded font bytes 引用。
  - bbox / origin / advance / font size。
  - span/line context。
- vector/path nodes：
  - path commands。
  - bbox。
  - stroke/fill。
- font diagnostics：
  - ToUnicode present。
  - encoding。
  - cmap availability。
  - subset prefix。
  - Type 1/Type 3/CIDFont。

### 3.2 输出

Enriched Glyph Graph：

```json
{
  "node_id": "p2_g153",
  "raw_identity": {
    "unicode": "",
    "glyph_name": "summation",
    "char_code": 80,
    "font_name": "ABCDEE+CMSY10"
  },
  "identity_candidates": [
    {
      "unicode": "∑",
      "latex": "\\sum",
      "source": "texglyphlist",
      "confidence": 0.99,
      "evidence": ["font_family=CMSY", "glyph_name=summation"]
    }
  ],
  "resolved_identity": {
    "unicode": "∑",
    "latex": "\\sum",
    "source": "static_map",
    "confidence": 0.99
  },
  "repair_trace": ["strip_subset_prefix", "glyph_name_lookup", "tex_font_family_match"],
  "warnings": []
}
```

重点是保留候选和证据，不要只写一个最终字符。

## 4. 模块一：静态映射修复

静态映射修复是 r0.5 的第一优先级，因为它快、可解释、工程风险低。

### 4.1 Adobe Glyph List

Adobe Glyph List(AGL) 是 glyph name 到 Unicode 的事实标准之一。常见 glyph name 如 `alpha`、`summation`、`integral`、`parenleft`、`minus` 等可通过 AGL 映射到 Unicode。

AGL 能解决的问题：

- PDF/字体中有 glyph name，但没有 ToUnicode。
- Type 1/CFF 字体保留 glyph name。
- TeX 字体符号名称可通过扩展表映射。

AGL 不能解决的问题：

- 字体只给 glyph id，没有可用 glyph name。
- glyph name 被子集化或私有化。
- char code 与 glyph name 之间映射丢失。
- 同名 glyph 在数学语义上需要字体上下文区分。

因此 AGL 是基础，不是万能修复器。

### 4.2 TeX glyph list

TeX 字体有大量传统 glyph name。`texglyphlist.txt` 补充了 TeX 特有 glyph name 到 Unicode 的映射。它常随 lcdf-typetools / TeX Live / CTAN 资源出现。

工程价值：

- 修复 Computer Modern / AMS symbols / TeX math fonts 中的符号名。
- 补足普通 AGL 对 TeX glyph 的覆盖。
- 可作为 SymbolScraper 类工具的映射来源之一。

### 4.3 enc-maps / TeX font encoding maps

TeX 字体编码文件提供 encoding slot 到 glyph name/Unicode 的映射。对旧 LaTeX PDF，它们非常重要。

可利用的信息：

- OT1 / T1 / OMS / OML / OMX / TS1 等 encoding。
- Computer Modern math italic / symbol / extension 字体。
- AMS symbol fonts。

注意：

- PDF 中的 char code 不一定直接等于 TeX encoding slot，需要结合 PDF font encoding。
- subset font 可能重编码。
- 必须从 PDF font dictionary 和 embedded font program 中确认 code -> glyph name。

### 4.4 fonttools

fonttools 是 Python 字体处理工具箱，可读取 TrueType/OpenType/CFF 等字体信息。对 r0.5 的价值：

- 读取 embedded font 的 cmap。
- 读取 glyph order / glyph names。
- 解析 CFF table。
- 提取 glyph outlines。
- 对字体 subset 做诊断。

fonttools 能修复的情况：

- font program 内有 cmap。
- glyph name 可从 CFF/Type 1 信息获得。
- glyph outline 可导出给 shape classifier。

限制：

- PDF 的 char code 到 glyph id 仍要从 PDF font encoding/CID mapping 获得。
- 很多 PDF subset font 可能剥离名字或 cmap。
- Type 3 font 不一定能由 fonttools 直接处理，因为 glyph 是 PDF content stream。

### 4.5 SymbolScraper

SymbolScraper 是 MathSeer 相关工具链中的关键组件，目标就是从 born-digital PDF 中抽取字符/符号及位置，尤其关注非 Latin 符号和数学符号 bbox。它基于 Java/PDFBox 生态，工程价值很高：

- 可作为独立后端对 PyMuPDF 抽取结果做交叉验证。
- 可借鉴其 TeX 字体/glyph 映射逻辑。
- 可将其输出转成 glyph graph JSON。

需要进一步本地验证：

- 当前仓库构建状态。
- Java 版本要求。
- 是否支持 Windows。
- 输出 JSON/文本格式。
- 对 Attention/Napkin/旧 arXiv 的表现。
- 许可证。

SymbolScraper 不应直接写入正文；它应作为 r0/r0.5 的可选 fact extractor。

### 4.6 PDFBox

PDFBox 是 Java PDF 处理库。SymbolScraper 基于 PDFBox，说明 PDFBox 在底层字体/编码抽取上有成熟能力。项目本体是 Python，但可以通过独立 Java worker 使用 PDFBox/SymbolScraper，避免把 Java 复杂性塞进 UI。

### 4.7 静态映射层建议顺序

优先级：

1. 已有 ToUnicode：直接使用，但记录 source。
2. font cmap：fonttools 读取 embedded font。
3. glyph name：AGL + texglyphlist。
4. TeX encoding maps：根据 font family + encoding + char code。
5. SymbolScraper/PDFBox cross-check。
6. 仍未知：进入上下文传播。

### 4.8 静态映射层风险

风险：

- 错把 glyph name 同名但语义不同的符号映射为同一 Unicode。
- 忽略 subset re-encoding。
- 把 Type 3 private glyph 当标准 glyph。
- 字体名识别过度依赖字符串。

缓解：

- 所有修复带 evidence。
- 多来源一致才高置信。
- 单来源修复先 candidate。
- 与 geometry/context verifier 结合。

## 5. 模块二：上下文身份传播

上下文传播不是猜测，而是在已有强证据基础上传播或约束未知符号身份。

### 5.1 同 code 同 font 传播

最安全策略：

- 同一 PDF font resource。
- 同一 char code / CID。
- 有些实例已通过 ToUnicode/fonttools/AGL 修复。
- 其他实例未知。

则可传播身份。

置信度可接近 1，但前提是 font resource 和 code 完全一致。

### 5.2 同 glyph outline 传播

如果 glyph code 不同，但 embedded font outline 完全一致或高度相似，可传播身份：

- 相同 outline hash。
- 相同 normalized path signature。
- 相同 advance/bbox。

适合 subset/re-encoding 场景。

### 5.3 几何锚点传播

用户补丁中提出的几何锚点传播很有价值，但要更保守。仅靠 `(w,h,font_size,baseline_offset)` 相似不够，因为不同符号可能 bbox 相近。建议条件：

- font family 一致。
- glyph code 或 outline signature 一致，或 glyph name 相似。
- bbox/advance 高相似。
- 出现多次且上下文一致。
- 不跨不同 font resource 随意传播。

KD-Tree/LSH 可用于加速，但核心是证据条件，不是搜索算法。

### 5.4 结构角色推断

结构角色推断可以输出：

- `role_candidate = superscript_body`
- `role_candidate = denominator`
- `role_candidate = operator_limit`
- `role_candidate = matrix_cell`

但它不能直接输出“这个符号是 `i`”或“这个符号是 `n`”。角色是结构信息，不是身份信息。

这对 TinyBDMath 很有帮助，因为未知符号即使身份未知，模型仍可知道它在公式中的结构位置。

### 5.5 统计共现先验

大规模 arXiv/MathML 可统计：

- 上标中常见符号。
- 求和下限常见模式。
- 微积分公式常见 token。
- 矩阵维度符号常见 token。

但这必须只作为低权重 prior。否则会变成语言模型猜符号，违背证据优先原则。

建议：

- 统计先验只生成 `identity_prior_distribution`。
- 不能单独 resolved identity。
- 只有与静态映射/outline/视觉候选一致时提升置信。

### 5.6 上下文传播输出

输出分两类：

- `identity_source = propagation`：有强锚点，传播具体 Unicode。
- `role_source = structural_inference`：只传播角色，不传播 Unicode。

必须区分这两类。

## 6. 模块三：glyph shape classifier

这是 r0.5 的第三层。当字体映射失败，但 PDF 保留 glyph outline 或 vector path 时，可以进行非像素化的符号形状分类。

### 6.1 这是不是 OCR？

它不是传统页面 OCR，因为输入不是页面图像，而是 PDF 字体轮廓或 Type 3 glyph path。但它仍是“shape-based identity recognition”。因此文档中应明确：

- 它属于 born-digital fallback。
- 不 rasterize 整页。
- 不放入热路径。
- 输出 identity candidate，不直接 accepted。

### 6.2 outline 来源

可能来源：

- fonttools 从 TrueType/CFF glyph 获取 outlines。
- PDF Type 3 glyph content stream。
- PyMuPDF/Poppler 提供的 drawing/path。
- raster fallback 只作为最后手段，不属于 r0.5 主路线。

### 6.3 模板匹配

模板匹配适合作为最早 baseline：

- 对常见数学符号建立 normalized path template。
- 对未知 glyph outline 做平移/缩放归一化。
- 比较 shape distance。
- 输出 top-N。

优点：

- 无需大量训练数据。
- 可解释。
- 极快。
- 适合 `∫`、`∑`、`√`、箭头、大括号等形状特征明显符号。

缺点：

- 字体差异大时模板库要扩充。
- 易混淆相似符号，如 `-`、`−`、`—`。
- 对字母类变量帮助有限。

### 6.4 几何特征 + SVM/MLP

特征：

- normalized width/height/aspect ratio。
- contour length。
- path command counts。
- stroke/fill。
- convex hull ratio。
- Hu moments。
- Zernike moments。
- crossing counts。
- endpoint count。
- curvature histogram。
- holes count。
- skeleton feature。

分类器：

- SVM。
- RandomForest/XGBoost。
- 2-3 层 MLP。

优点：

- 小于 100KB 的模型可行。
- CPU 快。
- 可输出 top-N 和置信。

缺点：

- 特征工程需要验证。
- 对字体域外泛化要测试。

### 6.5 DeepSVG / vector Transformer

DeepSVG/DeepVecFont 类模型能处理 SVG/path command sequence。它们更强，但也更重，数据需求更大。

对本项目建议：

- 不作为第一版。
- 可作为长期替代 shape classifier。
- 若使用，仍只输出候选，受 verifier 约束。

### 6.6 数学符号形状优势与限制

优势：

- 很多数学符号几何特征强，如 `∫`、`∑`、`√`、`∞`、`∂`。
- 大符号和运算符轮廓独特。
- PDF outline 比低分辨率图像干净。

限制：

- 字母变量类难区分，尤其 italic 字母。
- 字体风格变化大。
- `O`、`0`、`○`、`\circ` 容易混。
- `-`、`−`、`\bar`、fraction line 需要上下文。
- shape classifier 不能理解公式结构。

## 7. r0.5 融合策略

### 7.1 证据等级

建议身份证据等级：

1. `to_unicode`：PDF 原生 ToUnicode。
2. `font_cmap`：embedded font cmap。
3. `glyph_name_agl`：AGL/texglyphlist。
4. `tex_encoding_map`：TeX encoding maps。
5. `symbolscraper_crosscheck`：外部后端一致。
6. `same_font_code_propagation`。
7. `outline_hash_propagation`。
8. `shape_template`。
9. `shape_classifier`。
10. `statistical_prior_only`。

只有 1-7 在足够条件下可能高置信。8-9 通常为候选。10 不能单独解析身份。

### 7.2 冲突处理

如果不同证据冲突：

- 不直接选择最高分。
- 保留多个 candidates。
- 标记 `identity_conflict`。
- 降低 TinyBDMath accepted 可能性。
- 必要时进入视觉/云端复核。

### 7.3 与 TinyBDMath 的接口

TinyBDMath 输入中，每个 node 应包含：

- resolved unicode。
- top-N identity candidates。
- identity confidence。
- identity source。
- role candidates。
- repair warnings。

模型可把 identity uncertainty 作为特征。decoder/verifier 可拒绝身份不稳定的关键符号。

### 7.4 accepted gate

如果一个公式包含 r0.5 shape-classifier-only 的关键符号，一般不应自动 accepted，除非：

- shape confidence 极高。
- 多模板/多模型一致。
- 结构和上下文强支持。
- 渲染回比通过。
- 可选视觉候选一致。

## 8. 工程实施路线

### 8.1 MVP

第一版只做静态映射：

- 收集 AGL、texglyphlist、TeX encoding maps。
- 标准化字体名。
- 提取 glyph name / char code / font encoding。
- 修复 unknown glyph。
- 写入 Enriched Glyph Graph。

### 8.2 第二版

加入传播：

- same font + same code propagation。
- outline hash propagation。
- 文档级 identity cache。
- repair audit report。

### 8.3 第三版

加入 shape classifier：

- fonttools outline extraction。
- Type 3 path extraction。
- template matching baseline。
- SVM/MLP 训练。

### 8.4 第四版

接入 SymbolScraper/PDFBox：

- 独立 Java worker。
- 输出统一 glyph graph。
- 与 PyMuPDF cross-check。
- 用于旧 arXiv PDF 对照。

## 9. 测试与验收

### 9.1 单元测试

- AGL glyph name -> Unicode。
- texglyphlist glyph -> Unicode。
- subset font prefix stripping。
- same font/code propagation。
- conflict preservation。
- unknown 不被强行修复。

### 9.2 真实 PDF 测试

- Attention。
- Napkin。
- 旧 arXiv pdfLaTeX。
- 现代 LuaLaTeX/XeLaTeX。
- Office/PPT 可复制公式。
- Type 3 字体样本。

### 9.3 指标

- unknown glyph reduction rate。
- identity repair precision。
- identity repair recall。
- conflict rate。
- downstream TinyBDMath accuracy lift。
- visual fallback reduction。
- accepted precision impact。

### 9.4 重要门禁

如果 r0.5 修复导致 downstream 错误增加，必须降级为 candidate-only。r0.5 的目标不是最大化修复数量，而是最大化高置信非视觉证据利用率。

## 10. 风险与边界

### 10.1 不能过度传播

最危险的错误是把未知符号错误修复成具体字符，然后 TinyBDMath 结构解析再把它 accepted。必须保守。

### 10.2 shape classifier 不是万能

shape classifier 对符号类有效，对字母类变量风险高。应优先识别数学运算符、希腊字母、大符号、特殊符号，而不是所有字符。

### 10.3 静态映射依赖 PDF 编码

AGL/texglyphlist 需要 glyph name。若只有 char code，必须先知道 code -> glyph name。不能把 char code 直接当 Unicode。

### 10.4 TeX font family 识别不能只靠字符串

字体名可被 subset、改名或替换。应结合 font descriptor、glyph names、encoding、metrics。

## 11. 对主架构的修改建议

主架构应改为：

- r0：PDF facts extraction。
- r0.5：symbol identity repair。
- r1：cache-first OCR/MFR for image/scan/missing evidence。
- r2a：TinyBDMath structure parser on enriched glyph graph。
- r2b：visual/document parser fallback。
- r3：cloud semantic review。
- r4/r5：GraphRAG/knowledge accepted update。

r0.5 不应被隐藏在 r0 的 warnings 里，它应该是可审计、可测试、可缓存、可跳过的独立阶段。

## 12. 参考资料

- Adobe Glyph List：<https://github.com/adobe-type-tools/agl-aglfn>
- lcdf-typetools / TeX glyph list：<https://www.lcdf.org/type/>
- CTAN enc-maps：<https://ctan.org/pkg/enc-maps>
- fonttools：<https://github.com/fonttools/fonttools>
- SymbolScraper：<https://github.com/zanibbi/SymbolScraper>
- MathSeer software：<https://www.cs.rit.edu/~dprl/mathseer/software.html>
- Apache PDFBox：<https://pdfbox.apache.org/>
- ChemScraper：<https://arxiv.org/abs/2311.12161>
- DeepSVG：<https://arxiv.org/abs/2007.11301>
- DeepVecFont：<https://arxiv.org/abs/2110.06688>
- Hu moments / OpenCV shape descriptors：<https://docs.opencv.org/>

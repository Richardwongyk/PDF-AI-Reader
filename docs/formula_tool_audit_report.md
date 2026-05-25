# 已安装公式工具能力审计报告（born-digital 结构路线）

**日期：** 2026-05-25
**范围：** 审计项目中已安装工具对 **born-digital PDF** 的公式解析能力。Born-digital PDF 已有文本层、字体、glyph 坐标和矢量绘制信息，**必须走文档结构解析，不能走 OCR/图像识别路线**。

---

## 〇、前置分类：结构路线 vs 图像路线

在讨论任何工具之前，先明确一个根本分类：

| 路线 | 输入 | 原理 | 适用场景 |
|------|------|------|---------|
| **结构路线** | PDF 文本层、字体表、glyph 坐标、矢量路径 | 读取 PDF 文件内部的结构化数据 | born-digital PDF（有文本层的 PDF） |
| **图像路线** | PDF 页面渲染后的像素图（PNG/JPEG） | 对像素运行视觉模型（OCR/MFR/layout detection） | 扫描件、图片公式、无文本层区域 |

**Born-digital PDF 必须走结构路线。** 把有文本层的 PDF 渲染成图片再 OCR，等于抛弃了 PDF 文件中已有的精确数据（字体名、glyph 身份、矢量路径），让视觉模型去"猜"它本来可以直接读取的内容。这既不精确也不高效。

---

## 一、已安装工具的性质判定

### 1.1 结构路线工具

#### PyMuPDF (fitz) 1.27.2 — 主环境 `pdf_ai_reader_314`

**性质：纯结构路线。** 通过 MuPDF C 引擎直接读取 PDF 的文本层、字体资源、glyph 坐标、矢量路径。

**可用能力：**

| 能力 | API | 输出 | 当前使用 |
|------|-----|------|---------|
| 字符级结构提取 | `page.get_text("rawdict", flags=...)` | block/line/span/glyph 树，含 glyph 文本、CID、bbox、origin、synthetic 标记，以及 image 块和 vector 块 | **是 — `born_digital_math.py` 的核心输入** |
| 字体资源提取 | `page.get_fonts(full=True)` | 页面所有字体：xref、名称、类型、编码、是否嵌入 | **是 — `_page_fonts()` 使用** |
| 段落结构提取 | `page.get_text("dict")` | block/line/span 树（无 glyph 级信息） | **是 — `pdf_engine.py` `_extract_page_blocks()` 使用** |
| HTML 结构化输出 | `page.get_text("html")` | 每个 span 带 CSS font-family、font-size、left/top/width/height 的 HTML | **否** |
| XML 结构化输出 | `page.get_text("xml")` | 每个字符带 quad、font、color、size 的 XML | **否** |
| 段落/列表/标题识别 | `TEXT_COLLECT_STRUCTURE` + `TEXT_PARAGRAPH_BREAK` 标志 | type=2 结构块，含 `std` 标签（'H'/'LI'/'Div'） | **否** |
| 精确 ascend/descend 度量 | `TEXT_ACCURATE_ASCENDERS` + `TEXT_ACCURATE_SIDE_BEARINGS` 标志 | 更精确的字形度量数据 | **否** |
| PDF 底层对象访问 | `doc.xref_object()`, `doc.pdf_catalog()`, `page.get_contents()` | 原始 PDF 对象和内容流 | **否** |

**实测发现：**

1. **MuPDF 不提供任何原生数学公式提取能力。** 没有 `get_text("mathml")`，没有公式区域检测，没有 LaTeX 恢复。所有数学检测完全依赖项目自建的启发式层。

2. **HTML 输出可自动提取字体信息。** Attention 第 3 页的 `get_text("html")` 自动识别了 14 种字体变体（CMMI5/7/9/10、CMSY7/9/10、CMEX9、CMR6/7/9/10、NimbusRomNo9L），无需硬编码字体列表。当前 `_is_math_font_name()` 只覆盖了 10 种 family。

3. **测试 PDF 无语义标记。** Attention 论文（pdfTeX 1.40.25 生成）没有 `/StructTreeRoot`、没有 `/ActualText`、没有 MathML 标记。这是 LaTeX 生成 PDF 的普遍情况——排版引擎只输出排版后的 glyph 和坐标，不保留 LaTeX 源码的语义结构。

4. **`TEXT_COLLECT_STRUCTURE` 可识别标题/列表结构。** 开启后产生 `std='H'`（标题）和 `std='LI'`（列表项）块，有助于区分"章节标题"和"显示公式"（两者都可能居中、行短）。

#### Poppler pdftotext 4.00 — 系统安装（TeX Live）

**性质：纯结构路线。** 与 MuPDF 独立的 PDF 文本层解析器。

**当前使用：** 仅在 `tools/born_digital_math_audit.py` 中作为交叉审计对照工具（`pdftotext -bbox-layout`），不参与主流程。

**价值：** 当 MuPDF 和 Poppler 对同一页面的文本提取结果不一致时，可以定位 PDF 文本层的编码问题（CID 映射错误、ToUnicode 缺失等）。作为独立的第二意见来源有价值。

#### pdfminer.six / pdfplumber / PyMuPDF4LLM — 未安装或仅引用

- **PyMuPDF4LLM：** 项目文档提到可作为 Markdown 参考和图片占位检查，不能作为公式真实来源。未在主环境安装。
- **pdfminer.six / pdfplumber：** 文档提到可作为实验对照层。未安装。纯 Python PDF 解析器，结构与 MuPDF/Poppler 类似。

### 1.2 图像路线工具（均不能用于 born-digital 公式提取）

#### Pix2Text 1.1.6 — 主环境 + `pdf_tool_pix2text310`

**性质：纯图像路线。** 输入必须是渲染后的图片（PNG 文件路径或像素数据），不能读取 PDF 文本层。

| 组件 | 输入 | 输出 | 模型 |
|------|------|------|------|
| `MathFormulaDetector.detect()` | 页面渲染图片 | 公式 bbox 列表 | MFD-1.5-ONNX |
| `Pix2Text.recognize_formula()` | 裁剪后的公式图片 | LaTeX 字符串 | MFR-1.5-ONNX (LatexOCR) |
| `Pix2Text.recognize_page()` | 整页渲染图片 | 分类后的页面元素列表（含 FORMULA/TEXT/TITLE/FIGURE） | DocLayout + MFD + MFR + RapidOCR |

**实测 Attention 第 3 页 `recognize_page()` 结果：**
- 耗时：约 1.6-1.9 秒/页
- 公式检测：发现 1 个 `isolate_formula`，遗漏内联数学符号
- 公式 LaTeX：`\mathrm{A t t e n t i o n}(Q,K,V)=\mathrm{s o f t m a x}(\frac{Q K^{T}}{\sqrt{d_{k}}})V`（结构正确，但 `\mathrm{}` 内有字符间距问题）
- 文本 OCR 质量：大量乱码（"f the ales whethe ecint asnsdt"、"TCECRcNT3cmMRN5E2"、"W容EIdS上江海"）

**对 born-digital 的结论：** Pix2Text 所有组件都要求先将 PDF 渲染为像素图。对于已有文本层的 born-digital PDF，这是把精确的结构数据（glyph 的字体名、Unicode 码点、矢量路径）丢弃，让视觉模型从像素重新猜测。不应进入 born-digital 公式提取主路径。

#### PaddleOCR FormulaRecognition 3.5.0 — `pdf_tool_paddle310`

**性质：纯图像路线。** `FormulaRecognition.predict(input=paths, ...)` 要求输入裁剪后的公式图片文件路径。

- 当前仅使用 PP-FormulaNet_plus-S 模型
- M 和 L 变体可用但从未测试
- 主环境 `pdf_ai_reader_314` 缺少 `chardet` 依赖，无法直接调用
- **对 born-digital 的结论：同 Pix2Text，图像路线，不能用于 born-digital 公式提取。**

#### MinerU 3.1.15 — `pdf_tool_mineru310`

**性质：图像路线（以图像为主、文本为辅的混合管线）。** 这一点需要特别纠正——项目文档中关于 MinerU 的描述存在不准确之处。

**MinerU 的实际处理流程（来源：`mineru/backend/hybrid/hybrid_analyze.py` 源码）：**

```
1. load_images_from_pdf_doc() → 将 PDF 页面渲染为 200 DPI PIL 图像
2. predictor.batch_two_step_extract(images=images_pil_list) → VLM 视觉模型分析
3. _process_ocr_and_formulas(images_pil_list) → 
   a. layout_model.batch_predict(np_images) → 视觉布局检测
   b. mfr_model.batch_predict(images_mfd_res, np_images) → 图像→LaTeX 公式识别
   c. ocr_model.ocr(bgr_image) → 图像→文字 OCR
```

**关键事实：**
- 公式检测使用视觉布局模型（PPDocLayoutV2LayoutModel），输入是渲染后的页面图片
- 公式识别使用图像→LaTeX 模型（UniMERNet 或 PP-FormulaNet_plus_M），输入是裁剪后的公式图片
- `-m txt` 参数只控制非公式区域的文本是用 OCR 还是用 PDF 文本层，不影响公式检测和识别——公式管线始终是图像路线
- 单页耗时约 172 秒

**对 born-digital 的结论：** MinerU 的公式提取管线是图像路线。虽然它的文本提取部分（`-m txt`）可以走 PDF 文本层，但公式的检测和识别始终依赖渲染→视觉模型→图像→LaTeX。它不能替代 MuPDF 结构路线的 born-digital 公式检测。项目将其定位为"离线增强候选 worker"（r2）是正确的。之前报告中"MinerU 内部有自己的 PDF 解析器，不依赖 OCR，直接读取 PDF 文本层和矢量层"的描述**不准确**——只在文本区域为真，对公式不为真。

#### UniMERNet — `pdf_tool_pek310`（未安装）

- Worker 是存根，返回 `pek_unimernet_worker_not_implemented`
- 即使安装了也是纯图像路线（图像→LaTeX 模型）
- **对 born-digital 无价值。**

### 1.3 系统工具

| 工具 | 版本 | 路线 | 对 born-digital 的价值 |
|------|------|------|----------------------|
| **pandoc** | 2.12 | 格式转换（**不能读取 PDF**） | 无。pandoc 不直接读取 PDF 文件 |
| **pdflatex** | TeX Live | LaTeX→PDF 编译器 | 无。这是正向编译器，不能反向解析 |
| **pdftotext** | 4.00 | 结构路线 | 已有。作为 MuPDF 的独立交叉审计对照 |

---

## 二、核心结论：结构路线只有 MuPDF 一个可用工具

对于 born-digital PDF 公式提取，项目实际可用的工具矩阵：

| | 结构路线 | 图像路线 |
|---|---|---|
| **可用工具** | **MuPDF/PyMuPDF**（唯一） | Pix2Text、PaddleOCR、MinerU、UniMERNet |
| **能用于 born-digital 公式检测** | **是** | 否（但可用于扫描页补救） |
| **能用于 born-digital 公式 LaTeX 恢复** | **需自建**（MuPDF 无此能力） | 否（但可作为 r2 候选增强） |

**这意味着：所有 born-digital 公式的结构解析能力，必须建立在 MuPDF 提供的数据之上。不存在另一个"被忽视的结构路线银弹"。**

---

## 三、MuPDF 能力利用分析

既然 MuPDF 是唯一的 born-digital 结构数据源，问题变为：**当前代码是否充分挖掘了 MuPDF 能提供的所有结构信息？**

### 3.1 已充分利用的部分

- **rawdict 提取：** 使用了完整的标志集（`TEXTFLAGS_RAWDICT | TEXT_ACCURATE_BBOXES | TEXT_COLLECT_VECTORS | TEXT_USE_CID_FOR_UNKNOWN_UNICODE`），能提取 glyph、font、bbox、origin、vector、image
- **字体资源：** `page.get_fonts(full=True)` 已使用
- **矢量路径：** `TEXT_COLLECT_VECTORS` 已启用，分数线和根号线的检测依赖于此

### 3.2 未充分利用的部分

#### A. HTML 输出替代手写 rawdict 解析

当前 `born_digital_math.py` 约 1800 行，其中大量代码在做字体名提取、glyph 文本提取、空间位置计算。这些信息 MuPDF 的 `get_text("html")` 已经以 CSS 形式直接给出。

**对比：**

| 任务 | 当前做法 | HTML 输出方案 |
|------|---------|-------------|
| 数学字体检测 | 手写 `_is_math_font_name()` 硬编码 10 种 family | CSS `font-family` 直接给出字体全名，无需硬编码 |
| 字号对比 | 手动遍历 glyph 的 `size` 字段 | CSS `font-size` 直接给出 |
| 空间聚类 | 手动计算 `_union_bbox()` 等 | CSS `left`/`top`/`width`/`height` 直接定位 |
| 文本提取 | 手动处理 CID/编码/unknown glyph | HTML `textContent` |

**不是说要放弃 rawdict。** rawdict 提供了 glyph 级的 origin、synthetic 标记和 vector 块——这些是 HTML 输出不包含的，对于公式检测仍然必需。但字体提取、文本提取、空间定位这些"基础设施"完全可以用 HTML 输出来简化，把代码集中在真正需要 rawdict 的地方（矢量分析和 glyph 关系推断）。

#### B. `TEXT_COLLECT_STRUCTURE` 辅助公式区域分类

开启 `TEXT_COLLECT_STRUCTURE | TEXT_PARAGRAPH_BREAK` 标志后，rawdict 输出中会出现 type=2 的结构块：

```
Block 7: type=2, std=H      → 标题（"3.2.1 Scaled Dot-Product Attention"）
Block 33: type=2, std=LI    → 列表项
```

**价值：** 当前的 `DisplayFormulaSegmenter` 基于"行短 + 居中 + 数学密度"来判断是否为显示公式。但章节标题也"行短 + 居中"（没有数学密度）。type=2 结构块可以直接标记出标题和列表，帮助 `DisplayFormulaSegmenter` 排除这些非公式区域，减少误判。

#### C. 精确度量标志

`TEXT_ACCURATE_ASCENDERS` 和 `TEXT_ACCURATE_SIDE_BEARINGS` 可以提供更精确的字形度量。对于上下标检测（`_script_relation()` 依赖 glyph 的 bbox 和 center_y），更精确的度量可能提高检测准确率。

#### D. PDF 底层对象访问

`doc.xref_object()` 和 `page.get_contents()` 可以访问原始 PDF 对象和内容流。虽然测试 PDF 没有 `/ActualText` 或 `/StructTreeRoot`，但有些 PDF（特别是用 Adobe Acrobat 生成的）可能包含这些语义标记。当前代码完全没有检查这些。

---

## 四、`PdfFormulaSemanticReconstructor` 的定位

这是 r0 路线中唯一试图"从结构事实恢复 LaTeX"的组件。它的方法是从 glyph 坐标和矢量路径推断数学语义（分数、上下标、根号）。

**它做对了什么：**
- 分数检测（`_best_fraction_vector`）——利用 PDF 矢量层的分数线，这是图像路线拿不到的信息
- 根号检测（`_take_radical_glyphs`）——利用矢量层的根号条
- 上下标推断（`_script_relation`）——利用 glyph 的字号和位置关系

**它的问题：**
- 只覆盖了 7 种 Unicode→LaTeX 映射（`_glyph_to_latex`）
- 只覆盖了 10 种数学字体系列（`_is_math_font_name`）
- 无法处理复杂二维结构（矩阵、多行对齐、嵌套分式）
- Napkin 教材上召回率仅 1.9%

**项目文档自身的判断（`formula_extraction_research.md` 第 330 行）：**
> "轻量自写解析会继续扩大维护风险；源码宏、公式 AST 和二维结构恢复应优先接入成熟 LaTeX/PDF 解析库或经过验证的开源实现"

这一判断是正确的。`PdfFormulaSemanticReconstructor` 证明了"从 glyph+vector 推断 LaTeX"这个方向在局部可行（能恢复分数线和根号），但它不应该作为最终 LaTeX 输出的主要来源。它的价值在于**产生结构证据**（"这里有一条分数线矢量"、"这里有上下标字号关系"），这些证据应该传递给 r3 LLM 做语义层面的判断。

---

## 五、建议改进方向（均为结构路线）

### 优先级 1：r0 检测层重构——利用 MuPDF HTML 输出 + STRUCTURE 标志

**目标：** 简化检测代码，提升数学区域识别的准确率和字体覆盖率。

**具体改动：**
1. 用 `page.get_text("html")` 提取字体标注和空间定位，替代手写的字体名匹配和 bbox 计算
2. 用 `TEXT_COLLECT_STRUCTURE | TEXT_PARAGRAPH_BREAK` 标志获取 type=2 结构块，辅助 `DisplayFormulaSegmenter` 排除标题和列表
3. 保留 rawdict 提取用于矢量分析（分数线、根号线检测）和 glyph 级关系推断
4. `_is_math_font_name()` 的硬编码列表改为从 HTML 自动提取的字体名集合

**预期效果：** 检测代码从 1800 行缩减到约 600 行，数学字体检测不再受限于硬编码列表。

### 优先级 2：重新定位 `PdfFormulaSemanticReconstructor`——从"LaTeX 生成器"变为"结构证据生成器"

**目标：** 不再让它输出最终 LaTeX，而是输出结构化的证据描述。

**具体改动：**
1. 移除 `_render_glyphs()` / `_render_line()` / `_render_with_fraction()` 等 LaTeX 拼接逻辑
2. 改为输出 `FormulaEvidence` 结构：`{type: "fraction_vector", glyphs_above: [...], glyphs_below: [...], confidence: 0.9}`
3. 这些证据传递给 r3 LLM 作为额外的判断依据

**预期效果：** r3 拿到的不只是"某个 MFR 模型输出的 LaTeX 候选"，还包括"r0 检测到此处有一条分数线矢量"这样的硬证据，LLM 可以据此判断 MFR 候选是否正确。

### 优先级 3：增加 PDF 语义标记检查

**目标：** 对带有 ActualText 或结构树的 PDF（非 pdfTeX 生成），直接提取已有的语义信息。

**具体改动：**
1. 打开文档时检查 `doc.pdf_catalog()` 是否包含 `/StructTreeRoot`
2. 遍历结构树查找数学相关标记
3. 检查页面内容流中是否有 `/ActualText` 条目

**预期效果：** 对用 Adobe Acrobat 或其他支持 tagged PDF 的工具生成的 PDF，可能直接获取公式的原始 LaTeX 或 MathML。这类 PDF 目前占比不大，但零成本检查值得做。

### 优先级 4：Poppler 交叉审计常态化

**目标：** 用 Poppler 作为 MuPDF 文本提取的独立验证。

**具体改动：**
1. 对 born-digital 页面，异步运行 `pdftotext -bbox-layout` 获取独立文本+坐标
2. 与 MuPDF 的提取结果比对，差异处标记为 `poppler_disagree` 警告
3. 两方一致的 glyph 文本可提升置信度

### 优先级 5：真实数据集与小模型研发分层

**目标：** 让 TinyBDMath 训练集可复用、可审计、可扩展，同时不把 LaTeX 源码带入生产解析路径。

**当前实现：**
1. `src/core/latex_math_source_parser.py` 只在训练/审计路径扫描 LaTeX source gold，保留 delimiter、env、源码 offset 和上下文。
2. `tools/tinybdmath_sharded_dataset.py` 按页生成 Attention/Napkin PDF 候选分片，支持断点续跑、原子写入和 `preprocess_version`。
3. `tools/tinybdmath_gold_audit.py` 按 exact/near/weak/unmatched 与 `same_page_window/near_page_window/outside_page_window` 审计 PDF 候选标签。
4. PDF 候选匹配源码时优先使用 PDF 目录页锚点窗口；短公式和单字符公式也必须先受页窗口约束，避免全书范围误配。

**边界：** LaTeX 源码只用于数据集、训练和验收。真实用户打开普通 PDF 时没有源码，生产路径只能使用 PDF 结构事实、工具候选、r3 语义复核和人工/门禁 accepted 结果。

---

## 六、总结

1. **Born-digital PDF 的结构路线只有一个可用工具：MuPDF。** Pix2Text、PaddleOCR、MinerU、UniMERNet 均属于图像路线（需要将 PDF 渲染为像素图），不能进入 born-digital 公式提取主路径。

2. **MuPDF 不提供任何原生公式解析能力。** 没有 MathML 输出，没有公式检测，没有 LaTeX 恢复。所有公式提取能力必须自建在 MuPDF 提供的 glyph/字体/矢量数据之上。

3. **当前代码对 MuPDF 的利用率存在改进空间：** HTML 输出可简化字体和定位提取、STRUCTURE 标志可辅助公式区域分类、精确度量标志可提升上下标检测。但这些改进属于"工程简化"而非"能力突破"——born-digital 公式 LaTeX 恢复的核心难题（二维结构、嵌套、宏还原）不因此消失。

4. **`PdfFormulaSemanticReconstructor` 应重新定位：** 不做 LaTeX 生成，专做结构证据输出，作为 r3 LLM 语义审查的硬证据输入。这比继续扩大手写字符映射规则更可持续。

5. **当前还没有安装的结构路线公式解析工具。** 如果存在能直接从 PDF 结构层恢复 LaTeX/MathML 的成熟工具，安装并接入它将是 born-digital 路线最大的单点改进。在此之前，r0（检测）+ r3（LLM 语义审查，利用 r0 结构证据）是 born-digital 公式提取最合理的路线。

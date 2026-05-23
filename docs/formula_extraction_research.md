# 公式抽取与识别调研设计

## 当前结论

- 最新实现新增 display region 诊断层：对每个 born-digital 候选公式输出分类、风险、math density、operator/digit/vector/line 统计。该层只做审计和门禁，不生成 LaTeX，不把低置信结果写入默认路径。
- Attention 全量诊断审计约 2.254s：11 个候选，10 个 `formula_candidate`，1 个 `review`，主要风险是 `prose_like_region`。
- Napkin 前 120 页诊断审计约 21.002s：116 个候选，101 个 `formula_candidate`，15 个 `review`，风险包括 `table_or_text_like_region`、`tabular_alignment`、`prose_like_region`。
- 该结果说明当前瓶颈不是“是否找到数学区域”，而是二维布局树、表格/列表误吸、字体编码到数学语义的映射，以及源码页级对齐。

我们先把目标收窄到 **born-digital PDF**：PDF 本身有文本层、字体、glyph 坐标和矢量绘制信息时，公式必须优先走文档结构解析，不能走 OCR。OCR 只处理图片公式、扫描页、无文本层区域、乱码/缺失映射区域。

## 设计哲学与验收边界

- 先复用成熟 PDF 引擎和文档转换工具，再写项目适配层。
- 先抽取 PDF 事实，再讨论 LaTeX 恢复；没有证据的公式结构不能靠猜。
- 先审计、再集成；任何工具迁移都要先用 Attention/Napkin 源 LaTeX 和性能日志证明收益。
- 先保证默认阅读路径快，再把高精度任务放进后台修正轮。
- RAG/GraphRAG 与公式识别共享同一原则：基于全文证据、异步增强、失败可降级，不能用 UI 形式替代真实理解。

验收边界：

- born-digital 默认路径不得触发 OCR/MFR。
- 公式结果必须带定界符，低置信结果必须带 warnings。
- 新后端必须配置关闭时零加载、零 import 副作用。
- 重依赖只能作为离线/后台 worker，不能污染主环境。
- 真实问答分析使用 DeepSeek V4 Pro 时必须有可选真实云端测试。

关键结论：

- 不能继续把公式识别建立在正则、硬编码词表或单个启发式函数上。
- 不能宣称任意 PDF 都能还原原始 LaTeX。PDF 通常保存的是排版后的 glyph 和坐标，不保存 TeX AST；没有 `ActualText`、标签结构或可靠 ToUnicode 映射时，部分语义不可逆。
- 可恢复范围内必须追求极高速度和极高精度；不可恢复区域必须低置信标记、进入修正队列，不能伪造正确公式。
- Python 负责编排、缓存、任务队列、质量审计；热路径解析优先复用 MuPDF / Poppler 这类成熟 C/C++ PDF 引擎。
- 扫描/图片公式使用独立后端 worker，后台限流、缓存优先、可暂停、可恢复。

## 成熟工具边界

| 工具/路线 | 语言/形态 | 适合做什么 | 风险/不适合 | 决策 |
| --- | --- | --- | --- | --- |
| MuPDF / PyMuPDF | C 引擎 + Python binding | 高速读取 page/block/line/span/char bbox、字体、图像、矢量块；当前已安装并在热路径使用 | 不直接输出原始 LaTeX；需要我们把结构事实转换成统一中间表示 | **默认 born-digital 基础层** |
| Poppler `pdftotext -bbox-layout` | C++ 工具 | 作为独立交叉审计：读取 word/line bbox、检查 MuPDF 抽取偏差 | 不是公式语义引擎；进程调用不适合每次滚动热路径 | **审计/回归工具**，本机 TeX Live 已带 |
| pdfminer.six / pdfplumber | Python | 字符级 font/bbox 诊断，适合验证 MuPDF 的疑难样本 | Python 路径较慢；当前未安装；不直接解决 TeX 语义 | **实验对照层**，不默认安装 |
| PyMuPDF4LLM | Python | 阅读顺序、Markdown 对照、图片占位参考 | 公式常变成普通 italic/text 或 omitted picture | **辅助对照**，不能作为公式 truth |
| Marker / Docling / MinerU | Python/多模型 | 整页 PDF 到结构化 Markdown，适合离线全文重建、表格、复杂布局、扫描混合文档评估 | 依赖重、体积大，不能放交互热路径；公式质量必须用源码审计 | **离线增强候选 worker** |
| Pix2Text / PaddleOCR Formula / UniMERNet / RapidLaTeXOCR | Python/ONNX/Paddle | 图片公式、扫描公式 MFR；可在后台高精度模式使用 | 对 born-digital 文本公式不应优先使用；模型加载和 CPU/GPU 推理慢 | **OCR/MFR 后台层** |
| DeepSeek V4 Pro | 云端推理 | 基于全文证据做问答、解释、低置信公式修正、GraphRAG 抽取 | 不能替代 OCR 或 PDF 结构读取；不能无证据猜公式 | **分析/问答模型层** |
| C++17 / pybind11 | 本地 native 扩展 | bbox 匹配、批量相似度审计、hash、裁剪预处理等纯计算热点 | 不能在架构未稳定前下沉，否则会固化错误设计 | **性能热点加速层** |

## 本机可用状态

- 已可用：PyMuPDF / pymupdf4llm。
- 已可用：Poppler `pdftotext.exe` / `pdfinfo.exe`，来自 TeX Live。
- 未默认安装：pdfminer.six、pdfplumber、Marker、Docling、MinerU、PaddleOCR、RapidLaTeXOCR。
- 不新建慢速大环境作为默认动作；重依赖必须做成可选 worker，不污染主 Python 3.14 环境。

## 目标流水线

```text
PDF 导入
  -> MuPDF Raw Page Extractor
       -> chars / spans / fonts / glyph bbox / images / vector lines
       -> optional tagged PDF / ActualText when present
  -> BornDigitalMathStructureExtractor
       -> 只消费 PDF 结构事实
       -> 生成 MathRegion / MathGlyph / MathVector / TextRegion
       -> 不使用 OCR
  -> FormulaSemanticAdapter
       -> 尽量恢复可证据支持的 LaTeX 结构
       -> 无证据处输出 low_confidence / unknown_glyph / unmapped_font
  -> Base DocumentBlock + Knowledge Index
       -> 已确认文本和公式立即可检索
  -> Background Workers
       -> Poppler/pdfminer 交叉审计
       -> 图片/扫描公式 OCR
       -> 离线整页重建候选
       -> DeepSeek V4 Pro 基于证据的修正/解释
  -> 增量写回
       -> formula cache
       -> DocumentBlock
       -> KnowledgeEngine.upsert_blocks()
       -> GraphRAG 节点/边
```

## 中间表示边界

后续不再把 `spans -> bool` 作为核心接口，也不把文本正则当核心解析器。核心对象应表达 PDF 已经给出的事实：

```python
class MathRegion:
    page_num: int
    bbox: tuple[float, float, float, float]
    source: Literal["mupdf_raw", "poppler_audit", "tagged_pdf", "image_ocr"]
    kind: Literal["inline", "display", "image", "scan", "unknown"]
    confidence: float
    glyphs: list[MathGlyph]
    vectors: list[MathVector]
    warnings: list[str]
    needs_ocr: bool

class MathGlyph:
    text: str
    cid: int | None
    font: str
    size: float
    bbox: tuple[float, float, float, float]
    writing_mode: int

class FormulaResult:
    latex: str
    confidence: float
    evidence: list[str]
    warnings: list[str]
```

规则：

- `needs_ocr=False` 的 born-digital 区域不进入 OCR。
- `BlockType.FORMULA` 只接受高置信结果；低置信结果保留为候选和后台任务。
- 行内公式必须包裹为 `\(...\)`，行间公式必须包裹为 `$$...$$`。
- 数学字体符号、行内变量、行间公式输出时都必须带定界符。
- 任何未知 glyph、缺失 ToUnicode、CID 未映射、复杂二维结构未解析，都必须进入 warnings。

## Born-Digital 解析策略

这部分只做结构恢复，不做 OCR：

1. 读取 `rawdict` 字符级 bbox、font、size、writing mode。
2. 读取页面 draw/vector 信息，尤其是分式横线、根号线、矩阵/括号等矢量结构。
3. 如果 PDF 有 tag tree、`ActualText` 或可访问性文本，优先作为语义证据。
4. 对同一公式区域建立二维布局树：baseline、script zone、fraction zone、radical zone、large operator zone、matrix/table zone。
5. 只把布局树中有证据的部分转成 LaTeX；没有证据时保留文本或未知标记。
6. 使用 Poppler/pdfminer 作为抽取结果审计，不在热路径重复解析整页。

这里可以有策略参数，但不能写成按论文样本特化的硬编码。策略参数必须来自字体表、PDF 标准信号、布局事实或配置文件，并且要用 Attention/Napkin 源 LaTeX 回归验证。

## 图片/扫描公式策略

OCR/MFR 只在以下情况触发：

1. 区域没有可用文本层。
2. 页面或公式是图片/扫描内容。
3. 文本层为乱码、缺字、CID 无法映射。
4. 用户显式触发高精度精扫。
5. 问答证据引用该区域，但当前公式为空或低置信。

执行顺序：

```text
image_hash cache
  -> fast local backend
  -> confidence audit
  -> slow high-accuracy backend for repair queue
  -> optional cloud/high-end model only when configured
```

不能做：

- 打开 PDF 时同步全量 OCR。
- 对 born-digital 文本公式跑 OCR。
- 把长证明段落裁剪成公式送入 MFR。
- 用 LLM 在没有 PDF/OCR 证据时猜公式。

## 性能策略

默认阅读路径：

- 不加载 MFR/OCR 模型。
- 不运行全页重型布局重建。
- 使用 MuPDF C 引擎读取结构，结果进入页面缓存和知识库基础块。
- 后台任务小批量执行，严格预算，不能抢 UI 渲染、滚动、缩放。

后台增强路径：

- 导入时全篇页面入队，但不阻塞首屏。
- 视口、问答 evidence、用户点击解释的区域优先。
- 所有 OCR 和高精度重建结果写缓存，二次打开必须复用。
- 长文档如 Napkin 是性能门槛；不能因为少量公式提升牺牲整体交互。

C++17 只用于明确热点：

- 大规模 bbox overlap / interval tree。
- LaTeX 源码与 PDF 抽取结果相似度审计。
- 批量 hash、图像预处理、裁剪归一化。
- Python 必须保留 fallback 和测试，native 模块必须可选。

## 质量门禁

每轮工具迁移都必须跑源码对照，不以肉眼观感替代：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_latex_audit.py --case attention --quality-gate
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_latex_audit.py --case napkin --max-pages 120 --quality-gate
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_ocr_benchmark.py --case attention --max-pages 6 --sample-limit 8
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_ocr_benchmark.py --case napkin --start-page 60 --max-pages 20 --sample-limit 8
```

必须记录：

- 源 LaTeX 公式 recall / near-match / weak-match。
- PDF 误判正文为公式的比例。
- 未知 glyph / CID / 缺失 ToUnicode 比例。
- 平均耗时、P95 耗时、冷启动耗时、缓存命中耗时。
- Attention/Napkin 打开、滚动、缩放日志。

验收原则：

- born-digital 可恢复公式必须比当前 baseline 大幅提升。
- 性能不能比现有默认阅读路径更差。
- 当前门禁失败不能掩盖；失败就是下一轮优化依据。

## 版本和任务边界

- 不把 `测试资料/`、临时 benchmark 输出、缓存数据库纳入版本库。
- 不默认安装重依赖或创建新虚拟环境。
- 新后端必须隐藏在统一接口后，配置关闭时不 import、不加载模型。
- 每个后端缓存 key 必须含 `image_hash/model/model_version/preprocess_version`。
- 大改可以做，但必须拆小提交、可回滚、每步测试。
- 提交信息和提交日志只保留项目作者信息，不添加额外自动署名。

## 下一步

1. 删除/隔离正则和手写启发式公式重构实验，不让它进入主链路。已完成。
2. 新建 MuPDF-backed `BornDigitalMathStructureExtractor`，先输出事实型中间表示，不急着假装完整 LaTeX。已完成第一版事实层。
3. 对 Attention/Napkin 建立 born-digital 结构审计：glyph、font、bbox、vector、unknown glyph、源 LaTeX 对齐。进行中。
4. 加 Poppler `pdftotext -bbox-layout` 对照工具，定位 MuPDF 与 Poppler 差异。已完成第一版对照入口。
5. 基于事实层建立数学区域审计：只标记结构证据和风险，不输出伪 LaTeX。
6. 再决定是否评估 Marker/Docling/MinerU 离线 worker；任何重依赖都先跑独立 benchmark。
7. 保持 OCR 后台队列、缓存、限流策略，后续再评估高精度图片公式后端。

## 当前启动结果

- `MuPDFBornDigitalExtractor` 已能从 `rawdict` 抽取 glyph、font、bbox、line、span、image、vector 和 unknown glyph 统计。
- `tools/born_digital_math_audit.py` 已能输出 MuPDF rawdict 审计，并可选运行 Poppler `pdftotext -bbox-layout` 对照。
- Attention 第 3-4 页 MuPDF 审计约 0.206s，5602 glyph，9 vector，2 image，unknown glyph 为 0。
- Attention 第 3 页 Poppler 对照约 0.282s，可作为独立 word/bbox 审计，不进入滚动热路径。
- 当前事实层不生成 LaTeX，不做 OCR，不接入主解析，避免把未验证逻辑误认为生产公式识别。

## 结构证据审计结果

已新增三层审计输出：

- `math_evidence.regions`：单个数学证据片段，来自数学字体、数学符号、脚本字号、附近矢量线和未知 glyph。
- `math_evidence.clusters`：把空间相邻的数学证据片段聚成公式候选簇。
- `math_evidence.context_clusters`：实验性地吸纳相邻 roman glyph，用于观察源码匹配上限。

当前 Attention 第 3-4 页结果：

- MuPDF 结构抽取约 0.22s，仍然很快。
- evidence region 约 90 个，cluster 约 29 个。
- cluster 源码弱匹配从 0 提升到约 4 个；context cluster 能匹配到部分 FFN / MultiHead 公式片段。
- context cluster 也会误吸相邻正文，不能进入主链路。

结论：

- PDF 结构事实足够快，也确实能看到数学字体、分式线、根号、上下标和部分公式上下文。
- 仅靠孤立 glyph group 不够，必须做 display formula 的行/region 级空间聚类和二维布局树。
- 不应继续膨胀 bbox 吸上下文；下一步要做的是基于行、block、vector、数学字体密度和阅读顺序的结构化 formula region，而不是文本正则。

## Display Formula 区域分割结果

已新增 `DisplayFormulaRegion` 和 `DisplayFormulaSegmenter`：

- 输入仍只来自 PDF 结构事实：line/span/glyph/font/bbox/vector/image。
- 输出只表示显示公式候选区域、bbox、证据、置信度和原始文本顺序，不直接伪造 LaTeX。
- 分割策略分两步：先找主显示公式行，再吸附相邻根号、分母、上下标、右侧公式编号等结构部件。
- 策略基于数学字体密度、脚本字号、矢量线、正文栏宽、居中/缩进行，不使用论文专属词表或函数名硬编码。
- 作者脚注符号、正文宽度内联数学句子已加入回归测试，防止被提升为显示公式。

当前审计：

- Attention 前 6 页：MuPDF 事实层 + display region 审计约 0.593s，17816 glyph，unknown glyph 为 0，display region 7 个。
- Attention display-only 源码对照：8 个源码 display snippets 中，near match 3 个，weak match 4 个；`Attention`、`FFN`、`MultiHead`、`PE` 样例能整体对齐源码。
- Napkin 第 60-79 页：约 0.874s，30974 glyph，unknown glyph 为 0，display region 31 个。
- Napkin 对齐率仍低，主要原因是教材中大量显示数学与例题/定理文本混排，且 PDF glyph 文本不是 LaTeX 语义树；这需要下一步二维布局树和环境级对齐，不能靠继续扩大 bbox 或写词表解决。
- 新增 `DocumentChunker(enable_born_digital_math=True)` 可选入口，把 display region 追加为 `BlockType.FORMULA`，并保留 `source/confidence/evidence/semantic_recovery=pending` 元数据；默认仍关闭，不影响阅读热路径。
- 追加结构公式块时会把完全重叠的普通段落标记为 `shadowed_by=born_digital_display_formula`，知识库索引会跳过该 shadowed 段落，避免同一公式重复进入 RAG。
- Attention 全量、关闭旧 span 启发式、只启用 born-digital display：耗时约 2.189s，公式块 11 个，`source_weak_match_rate=0.069`，`low_similarity_pdf_rate=0.455`。相比旧 no-MFD baseline 更干净，但远未达质量门禁。
- Napkin 前 120 页、关闭旧 span 启发式、只启用 born-digital display：耗时约 17.823s，公式块 116 个，`source_weak_match_rate=0.037`，`low_similarity_pdf_rate=0.250`。性能不能进入默认热路径，只能作为后台/审计/后续增量索引候选。
- 新增 `PdfFormulaSemanticReconstructor` v1，可选从 display region 的 glyph/bbox/vector 事实恢复上下标、局部分式线、根号、常见 Unicode 数学符号和 upright 字母串；不使用样本词表，不使用 OCR。
- Attention 全量、纯 born-digital display + semantic v1：耗时约 2.276s，公式块 11 个，`common_source_command_recall=0.353`，`source_weak_match_rate=0.069`，`low_similarity_pdf_rate=0.455`。公式 1 可恢复 `\frac{Q K^{T}}{\sqrt{d_{k}}}` 结构。
- Napkin 前 120 页、semantic v1：耗时约 21.017s，公式块 116 个，`common_source_command_recall=0.019`，`source_weak_match_rate=0.035`，`low_similarity_pdf_rate=0.319`。教材混排仍会吸入正文词，不能默认启用。

当前边界：

- 这一步证明 born-digital PDF 结构路径足够快，也能把部分关键显示公式完整合成区域。
- 这一步还不是 99.99% LaTeX 还原；它是后续二维结构树和 LaTeX 语义恢复的事实基础。
- 当前区域文本不能直接作为最终公式 truth；进入主链路前必须带 confidence/warnings，并完成二维结构恢复、表格/列表过滤和质量门禁。
- 旧 span 级公式启发式会把表格数值、复杂证明句、列表项误判为公式；后续应逐步降级为 fallback，默认公式入口转向结构事实层。
- semantic v1 已证明命令恢复可量化提升，但仍不是高精度成品。下一步重点不是扩大字符规则，而是做 region 级类型判别、表格/列表/正文混排隔离、矩阵/对齐环境建模和 LaTeX 源码页级对齐审计。

## 参考资料

- PyMuPDF text extraction appendix: https://pymupdf.readthedocs.io/en/latest/app1.html
- PyMuPDF text extraction flags: https://pymupdf.readthedocs.io/en/latest/vars.html
- Poppler `pdftotext` man page: https://manpages.debian.org/testing/poppler-utils/pdftotext.1.en.html
- pdfminer.six documentation: https://pdfminersix.readthedocs.io/en/latest/
- pdfplumber: https://github.com/jsvine/pdfplumber
- Marker: https://github.com/datalab-to/marker
- Docling: https://github.com/docling-project/docling
- MinerU: https://github.com/opendatalab/MinerU
- PaddleOCR formula recognition: https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/formula_recognition.html
- RapidLaTeXOCR: https://github.com/RapidAI/RapidLaTeXOCR
- DeepSeek API docs: https://api-docs.deepseek.com/

# 公式抽取与识别调研设计

## 结论

当前“把一个文本块判定为公式/段落”的单函数启发式不够可靠，只能作为临时护栏。后续应迁移到分层公式抽取流水线：

1. **born-digital PDF 优先结构抽取**：用 PyMuPDF 字符、span、字体、bbox、行几何恢复行内数学片段和行间公式。此路径不跑 OCR，速度应接近普通文本解析。
2. **图片/扫描公式才进入 MFD/MFR**：先检测 bbox，再裁剪，再 OCR。OCR 是昂贵补救层，不再承担普通文本 PDF 的主要公式判断。
3. **全文导入即排队，后台增量补全**：打开文件后立即把全文页面和已有公式候选写入持久任务队列；基础阅读和基础知识库先可用，公式识别结果逐批写回缓存、DocumentBlock、知识库和后续图谱索引。
4. **所有公式必须带 LaTeX 定界符**：行内数学使用 `\(...\)`，行间公式使用 `$$...$$`。没有定界符的数学字体片段不能直接进入翻译/RAG。
5. **准确率必须用 LaTeX 源码审计**：Attention 与 Napkin 的 PDF 抽取结果要持续和 bundled LaTeX 源码对齐，不能只靠肉眼或单页样例判断。

## 当前证据

已完成的本地基准显示，当前瓶颈不是 PDF 解析和裁剪，而是 MFR 模型加载、推理和候选质量：

- Attention 抽样：解析 0.538s，裁剪 0.105s，Pix2Text 冷加载约 102.378s，4 个公式 OCR 91.690s，平均 22.922s/公式。
- Napkin page 60-79 抽样：解析 0.258s，裁剪 0.214s，冷加载 21.561s，2 个样本 OCR 13.946s，平均 6.973s/公式。
- 两份样本都出现正文/证明句被误判为公式，MFR 输出大量逐字母 `\mathrm{...}` 的问题。
- PyMuPDF4LLM 本地试跑：
  - Attention 第 3-4 页约 4.652s，能把数学变量标成 Markdown italic，但不能稳定恢复原始 LaTeX。
  - Napkin 第 61-63 页约 6.964s，会把一部分行间公式或图片公式输出成 omitted picture，占位有价值，但不能替代公式 OCR。

结论：继续收紧几个正则不能根治问题。应先把“候选生成、候选分类、公式恢复、OCR 补救”拆开，并让每层都有独立质量指标。

## 工具调研

| 工具/路线 | 适合做什么 | 不适合做什么 | 当前判断 |
| --- | --- | --- | --- |
| PyMuPDF `rawdict` / `dict` | 从 PDF 获取块、行、span、字体、bbox，部分模式可拿到字符级位置；已在项目热路径使用 | 不能直接还原 TeX 源码；需要自行做数学行/片段分类 | **默认基础层**。born-digital 公式判断应主要走这里 |
| PyMuPDF4LLM | 快速生成 Markdown，双栏和阅读顺序有参考价值 | 公式常以 italic/text 或 omitted picture 出现，不能作为公式 truth | **辅助对照层**。可用于段落顺序和图片占位，不替代公式 extractor |
| pdfplumber / pdfminer.six | 字符级对象、字体名和 bbox 信息成熟，适合做公式判断对照实验 | 当前未安装；新增依赖需评估性能和体积 | **候选实验层**。如果 PyMuPDF 字符信息不足，再引入对比 |
| Pix2Text MFD/MFR | 已可运行，能检测/识别部分图片公式，接入成本低 | CPU MFR 过慢；候选误判时准确率差；不能放同步路径 | **默认 OCR 兜底层**。只在后台/显式精扫中限额运行 |
| PaddleOCR FormulaRecognition | 官方有 `FormulaRecognition(model_name=...)` 与 `predict(input=..., batch_size=...)`，结果含 `rec_formula`；模型覆盖 `PP-FormulaNet_plus-S/M/L`、UniMERNet | `paddlepaddle` 当前不适配项目 Python 3.14 主环境；依赖大 | **可选高精度 worker**。先做独立环境审计，不进默认热路径 |
| RapidLaTeXOCR | ONNXRuntime/OpenVINO 路线更轻，理论上适合本地公式 OCR | 当前包对 Python 3.14 不友好，需要验证手动模型或独立 worker | **重点候选后端**。优先调研能否轻量接入 |
| Marker / MinerU | 整页 PDF 到 Markdown/结构化内容，可能更擅长复杂文档、表格、公式和扫描件 | 依赖重，适合离线索引，不适合滚动/双击热路径 | **离线增强层**。可作为“高质量全文重建”模式 |
| Nougat | 科学文档 OCR 到标记语言，适合整页学术文档实验 | 项目状态、速度、依赖都不适合交互热路径 | **研究对照层**，不作为默认实现 |
| DeepSeek V4 Pro | 适合全文理解、结构抽取、低置信度公式解释/纠错和知识图谱抽取 | 不适合替代 OCR 读取像素；不能凭空修复无证据公式 | **分析模型层**。用于低置信度修正和 GraphRAG，不直接当 OCR |

## 分层流水线

```text
PDF 导入
  -> PageRawExtractor
       -> blocks / lines / spans / chars / images
  -> FormulaCandidateExtractor
       -> inline_math_spans
       -> display_math_lines
       -> image_or_scanned_formula_regions
       -> rejected_prose_math_mentions
  -> Base DocumentBlock + KB upsert
       -> 文本、标题、章节、已确认公式立即可检索
  -> FormulaIndexQueue
       -> 页面级 MFD 任务
       -> 图片/扫描公式 OCR 任务
       -> 低置信度修正任务
  -> Background Formula Worker
       -> cache-only 回填
       -> 小批量 MFD
       -> 小批量 MFR
       -> DeepSeek V4 Pro 低置信度语义修正
  -> 增量写回
       -> formula_ocr_cache
       -> DocumentBlock
       -> KnowledgeEngine.upsert_blocks()
       -> GraphRAG formula/entity edges
```

## 候选数据结构

后续不应直接从 `spans -> bool`。应引入稳定中间结构，便于审计和替换算法：

```python
class FormulaCandidate:
    page_num: int
    bbox: tuple[float, float, float, float]
    text: str
    kind: Literal[
        "inline_text_math",
        "display_text_math",
        "image_formula",
        "scanned_formula",
        "maybe_formula",
    ]
    source: Literal["pymupdf_raw", "pymupdf4llm", "mfd", "manual"]
    confidence: float
    needs_ocr: bool
    features: dict[str, float | int | str]
```

关键点：

- `BlockType.FORMULA` 只能由高置信 display candidate 生成。
- 低置信候选先保留在 metadata 或任务表，不直接污染正文块。
- 行内数学片段保持在段落内，用 `\(...\)` 包裹，不把整段 proof/text 变成公式块。
- 图片/扫描公式才设置 `needs_ocr=True`。

## 公式判断特征

### 正向特征

- 数学字体比例：CM、CMMI、CMSY、STIX、XITS、Symbol、Euler、Math 等。
- 单字符变量密度：`x y n d M N R` 这类变量以独立 glyph/span 出现。
- 运算符密度：`= + - × · / | < > ≤ ≥ ∈ → ∞ ∑ ∫ √ ^ _`。
- 上下标/基线变化：同一行内存在明显 baseline 或字号阶梯。
- display 几何：短行、居中、大左右缩进、上下空白、独立行、编号靠右。
- LaTeX/Unicode 命令痕迹：PDF 文本中已有 `\frac`、`\sum` 或成块数学符号。
- 图像特征：小高度横向图片、黑白细线多、位于公式行位置。

### 反向特征

- 自然语言单词数高，尤其含 proof/example/definition/figure/table/where 等叙述词。
- 句读密度高，逗号和句号构成普通段落。
- bbox 宽度接近正文列宽且行数多。
- 图注、页眉、页脚、目录、参考文献。
- `_is_formula_from_spans` 这类规则只要出现反向特征，就不能直接升级为 `BlockType.FORMULA`。

### 决策方式

短期用可解释打分，不直接上黑盒分类：

```text
score = font_score
      + symbol_score
      + operator_score
      + geometry_score
      + baseline_score
      + image_score
      - prose_penalty
      - caption_penalty
```

阈值分三档：

- `score >= high`：生成公式块或行内数学包装。
- `low <= score < high`：进入候选表，等待上下文/后台修正。
- `score < low`：作为普通文本，但可记录 math mentions。

## OCR 策略

OCR 不应被“是否看起来像数学字体”触发，而应被这些条件触发：

1. 该区域没有可用文本层，但 MFD/图片特征显示是公式。
2. 有文本层但为图片占位或乱码。
3. 用户主动解释/精扫该公式。
4. 问答证据引用该区域且当前公式内容为空或低置信。
5. LaTeX 源码审计显示该页关键公式长期未恢复。

推理顺序：

```text
hash cache
  -> cheap crop normalization
  -> fast backend (Pix2Text / RapidLaTeXOCR / PP-FormulaNet_plus-S)
  -> confidence audit
  -> slow repair backend (PP-FormulaNet_plus-M/L or UniMERNet)
  -> DeepSeek V4 Pro context repair only when there is OCR/text evidence
```

不能做的事：

- 打开 Napkin 时同步全量 MFR。
- 把长 proof 段落整段裁剪送入 MFR。
- 用 LLM 在无 OCR/无文本证据时猜公式。

## 性能目标

| 场景 | 目标 |
| --- | --- |
| 打开 Attention | 首屏可交互不等待 MFR |
| 打开 Napkin | 首屏、滚动、缩放不因公式队列阻塞 |
| 普通滚动 | 不加载 OCR 模型，只查已有 block/cache |
| 双击翻译 | 公式保护和上下文检索不得等待未命中 OCR |
| 当前视口精扫 | 小批量、有进度、可取消，优先 cache hit |
| 二次打开 | 已识别公式直接从缓存和知识库恢复 |
| 后台扫描 | 低优先级、可暂停、可恢复，不抢 UI 线程 |

当前 Pix2Text MFR 的冷启动和 CPU 推理速度不达标，因此默认路径必须维持 `max_mfd_pages=0`、cache-first、background budget。

## 质量门禁

每轮算法或工具迁移都必须跑：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_latex_audit.py --case attention --quality-gate
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_latex_audit.py --case napkin --max-pages 120 --quality-gate
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_ocr_benchmark.py --case attention --max-pages 6 --sample-limit 8
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_ocr_benchmark.py --case napkin --start-page 60 --max-pages 20 --sample-limit 8
```

必须记录：

- `common_source_command_recall`
- `source_near_match_rate`
- `source_weak_match_rate`
- `average_best_similarity`
- `low_similarity_pdf_formula_count`
- 正文误判为公式的比例
- MFR 冷启动时间
- MFR 平均/分位耗时
- cache hit 耗时
- Napkin UI 打开、滚动、缩放日志

当前 baseline 失败是预期结果；门禁的作用是防止低质量结果被误判为完成。

## 版本和迁移约束

- 不把 `测试资料/` 纳入版本库。
- 不把临时 benchmark 输出纳入版本库。
- 不默认安装重依赖或创建新虚拟环境。
- 新后端必须隐藏在 `FormulaRecognizer` 抽象后面，配置关闭时不 import、不加载模型。
- 如需独立 worker，必须通过稳定 IPC 传入图片 bytes/路径和模型名，不能污染主 Python 3.14 环境。
- C++17 只下沉纯计算热点：bbox overlap、候选打分、批量 hash、裁剪预处理、公式审计相似度。UI、SQLite 任务状态、模型调用、RAG 编排留在 Python。

## 下一步落地顺序

1. 新增 `FormulaCandidateExtractor`，把现在的 `_is_formula_from_spans` 拆成特征提取和候选打分。
2. 对 Attention/Napkin 输出候选审计 JSON：每个公式块给出 features、score、kind、是否误判、最接近 LaTeX 源。
3. 改 DocumentChunker：行内数学只包裹 span，不把整段 proof 升级为公式块；独立 display 行才生成 `BlockType.FORMULA`。
4. 加 `pymupdf4llm` 对照工具，只用于评估 reading order 和 omitted image/formula regions。
5. 评估 RapidLaTeXOCR 和 PaddleOCR worker，先跑独立 benchmark，再决定是否作为可选高精度后端。
6. 将低置信公式候选写入后台任务表，识别成功后增量 upsert 到知识库。
7. 在 GraphRAG worker 中把公式、定理、证明、章节、引用关系作为节点/边抽取，失败时不影响基础 RAG。

## 参考资料

- PyMuPDF text extraction appendix: https://pymupdf.readthedocs.io/en/latest/app1.html
- PyMuPDF4LLM API: https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/api.html
- PaddleOCR Formula Recognition Pipeline: https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/formula_recognition.html
- PaddleOCR Formula Recognition Module: https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/module_usage/formula_recognition.html
- RapidLaTeXOCR: https://github.com/RapidAI/RapidLaTeXOCR
- pdfplumber: https://github.com/jsvine/pdfplumber
- Marker: https://github.com/datalab-to/marker
- MinerU: https://github.com/opendatalab/MinerU
- Nougat: https://github.com/facebookresearch/nougat
- DeepSeek API pricing/models: https://api-docs.deepseek.com/quick_start/pricing

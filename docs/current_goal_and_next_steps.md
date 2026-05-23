# 当前目标与执行方案

## 我们现在在做什么

当前项目不是单纯补一个功能，而是在把 PDF AI Reader 从原型推进到可验证的论文阅读工具。

核心目标有四个：

1. **公式识别更准**
   - PDF 中图片格式/扫描版公式要尽可能识别为 LaTeX。
   - 已有文本公式但不是 LaTeX 的块，要能重识别和纠错。

2. **性能不能牺牲**
   - 打开 PDF、滚动、缩放、翻译不能被 OCR 或索引任务拖慢。
   - 长文档如 Napkin 必须作为性能门槛。

3. **知识库/问答要真正基于全文**
   - 不是 UI 上有个问答框，而是检索全文证据、展示依据、按证据回答、支持追问。
   - 后续要支持更强 RAG / GraphRAG，而不是长期手写简单向量检索。

4. **版本控制和可回滚**
   - 每一阶段单独提交，能独立测试、独立回滚。
   - 所有提交信息不得出现共同贡献者相关字样。

## 已完成的检查点

### 全文问答与证据

已完成：

- 右侧全文问答入口。
- 证据树展示全文检索依据。
- 问答完成后生成追问建议。
- evidence 中包含 `retrieval_score / lexical_score / vector_score`。
- UI 展示“相关度”，不暴露原始向量 distance。

### 检索质量

已完成：

- 知识库检索不再只取 `top_k` 向量结果。
- 先扩大候选池，再按向量距离、关键词覆盖、章节/摘要/关键词元数据混合重排。
- 对 HashingEmbedding 兜底场景更稳定。

### 知识库构建性能

已完成：

- Chroma collection metadata 中写入基础块索引指纹、基础块数和 schema 版本。
- 手动“重建知识库”会先比较当前 `DocumentBlock` 指纹。
- 指纹一致时跳过删除 collection、重新 embedding 和批量 upsert。
- 允许后台公式 OCR 增量块存在于 collection 中，不因 collection 总块数大于基础块数而误判失配。

关键性能结论：

- Attention 第二次重建：日志构建耗时 0.0s，E2E 等待约 0.47s。
- Napkin 第二次重建：日志构建耗时约 0.4s，E2E 等待约 0.72s。
- Napkin 首次写入清单仍需全量构建，当前约 87s；后续应继续优化首次构建和 MFD 页面预算。

### RAG / GraphRAG 迁移

已完成：

- 新增 `rag` 配置段。
- 拆分 `cloud_translation` 和 `cloud_reasoning`。
- 写入迁移方案文档：[rag_graphrag_migration_plan.md](rag_graphrag_migration_plan.md)。
- 抽出 `KnowledgeIndexBackend`。
- 保留 `legacy_chroma` 默认后端。
- 新增版本隔离的 `llamaindex_chroma` 后端。
- 安装并登记 LlamaIndex 依赖。

关键性能结论：

- LlamaIndex 默认 `ChromaVectorStore.add(TextNode)` 写入路径很慢，不能放入热路径。
- 优化后，写入/检索热路径改回 Chroma 原生批量 upsert/query，LlamaIndex 保留为 schema/GraphRAG 编排层。
- 5000 块合成基准：Chroma 写入 batch 从 50 提到 512 后，约 16.2s 降到 9.9s。
- Napkin E2E：知识库构建从约 117s 降到约 83.9s，日志 ERROR/WARNING/CRITICAL 为 0。

### 公式 OCR 性能

已完成第一步：

- `MathOCR` 新增公式图片 hash 缓存。
- 缓存命中时不检查 Pix2Text 可用性、不加载模型、不推理。
- 单元测试验证缓存命中不会触发模型加载。
- `MathOCR.recognize_batch(..., max_uncached=N)` 支持缓存优先和未命中推理预算。
- 扫描版/图片公式的 MFR 已按页、置信度、面积做优先级排序，默认只识别少量高优先级未命中项。
- 未进入预算或识别失败的公式仍写入 `DocumentBlock`，标记 `needs_ocr=True`，为后台公式索引继续补扫保留稳定位置。
- 新增 `FormulaIndexFlow`，对 `needs_ocr=True` 的公式块做后台预算式 OCR。
- 公式后台识别成功后会刷新页面 block，并通过 `KnowledgeEngine.upsert_blocks()` 增量写回知识库。
- 如果公式识别早于基础知识库构建完成，主窗口会暂存增量块，等 `build_finished` 后统一写入，避免竞态导致全文问答漏掉公式。

## 异步公式索引与知识图谱规划

这个过程分成三条可独立回滚的后台流水线，不能混成一个同步任务。

```text
PDF 打开/滚动/缩放
  -> 快速块解析
  -> 渲染缓存/页面块缓存

全文 RAG 索引
  -> DocumentBlock 基础文本和已知公式
  -> 先让全文问答可用

公式索引 + GraphRAG
  -> 公式候选队列
  -> 缓存命中立即回填
  -> 未命中按优先级批量 MFR
  -> 公式结果增量 upsert 到知识库
  -> 抽取章节/概念/定理/公式/引用关系
  -> 形成可选知识图谱索引
```

优先级顺序：

1. 当前视口页和用户双击解释的公式。
2. 全文问答 evidence 涉及的页面。
3. 目录、标题、定理、证明附近的公式。
4. 后台空闲时按页扫描剩余候选。

关键约束：

- 首屏阅读、滚动、缩放永远不等待 MFR 或 GraphRAG。
- 基础知识库构建不等待全量公式 OCR。
- 后台公式识别必须全部写缓存，二次打开不重复推理。
- GraphRAG 只作为增强索引，失败不能影响基础全文问答。

## 为什么不能直接“知识库构建时全量扫描公式”

可以在知识库构建期间启动公式扫描，但不能把高精度 OCR 同步塞进知识库构建主流程。

原因：

- MFD 负责找公式 bbox，较快。
- MFR 负责把图片公式转 LaTeX，最慢。
- Napkin 这种 1000 页文档如果全量同步 MFR，会显著拖慢首屏、知识库构建和问答可用时间。

正确做法是拆成两条索引：

```text
文本/结构知识库
  -> 快速完成
  -> 让全文问答先可用

公式索引
  -> 后台异步
  -> 命中缓存立即返回
  -> 未命中按优先级批量 OCR
  -> 增量更新知识库和 UI

知识图谱索引
  -> 读取章节/概念/定理/公式/引用
  -> 异步抽取关系
  -> 增量服务 GraphRAG
```

## 公式识别目标方案

### 第一层：快速路径

打开 PDF 时只做：

- PyMuPDF 文本解析。
- 轻量公式启发式识别。
- 已有缓存结果回填。

目标：

- 首屏、滚动、缩放不变慢。
- 不因 OCR 模型加载阻塞 UI。

### 第二层：按需精扫

优先扫描：

- 当前视口页。
- 用户点击解释的公式块。
- 问答证据中引用的页面。
- 标题、定理、证明附近的公式。

目标：

- 用户正在看的内容优先精准。
- 不先扫用户可能永远看不到的页面。

### 第三层：后台批处理

后台处理：

- 空闲时继续扫描剩余页面。
- 长文档按页优先级队列运行。
- 结果写入公式 OCR 缓存。
- 已识别公式增量更新 `DocumentBlock` 和知识库索引。

目标：

- 文档越用越完整。
- 二次打开速度显著变快。

### 第四层：高精度增强引擎

候选方案：

- Pix2Text：继续作为本地默认 MFD/MFR。
- MinerU / Marker：评估科学 PDF 到结构化 Markdown/LaTeX 的整体提取能力。
- Mathpix：作为可选云端高精度公式 OCR，不默认启用，适合用户明确选择“高精度模式”。

原则：

- 本地模型优先保证隐私和成本。
- 云端高精度只作为可配置增强。
- 所有结果必须缓存。

## 下一步执行顺序

1. **提交当前公式 OCR 缓存改动**
   - 已通过 `pytest -q`。
   - 提交前后继续检查无共同贡献者字样。

2. **实现公式扫描任务队列**
   - 新增公式 OCR job model。
   - 按页、视口、问答证据、用户点击排序。
   - 每批限制 OCR 数量，避免 CPU 抢占阅读和渲染。

3. **把公式结果增量写回知识库**
   - 已有 `DocumentBlock` 更新。
   - 已新增 `KnowledgeEngine.upsert_blocks()` 和后端 `upsert_blocks()`。
   - 后台公式识别结果会增量 upsert 到当前知识库后端。
   - 全文问答可引用识别后的公式。

4. **评估 Qdrant hybrid**
   - 目标不是“换库”，而是更快更强的 dense+sparse 检索。
   - 必须用 Attention 和 Napkin E2E 证明性能不劣化。

5. **评估高精度公式引擎**
   - 对 Attention 和 Napkin 源 LaTeX 做公式 recall/precision 审计。
   - 只引入真正提升精度且可缓存、可限流的方案。

## 验收标准

- `pytest -q` 通过。
- Attention E2E 通过，日志无 ERROR/WARNING/CRITICAL。
- Napkin E2E 通过，长文档滚动、缩放、问答正常。
- 知识库构建时间不回退。
- 公式 OCR 缓存命中时不加载模型。
- 提交信息和提交日志不出现共同贡献者相关字样。

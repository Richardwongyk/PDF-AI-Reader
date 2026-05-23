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
   - 所有提交信息不得出现额外自动署名。

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

- Attention 第二次重建：日志构建耗时 0.0s，最新 E2E 等待约 0.29s。
- Napkin 第二次重建：日志构建耗时约 0.2s，最新 E2E 等待约 0.26s。
- Napkin 首次写入清单仍需全量构建，当前约 87s；后续应继续优化首次构建和 MFD 页面预算。

### RAG / GraphRAG 迁移

已完成：

- 新增 `rag` 配置段。
- 拆分 `cloud_translation` 和 `cloud_reasoning`。
- 修复 DeepSeek reasoning 模型名：配置层使用 LiteLLM 兼容的 `deepseek/deepseek-v4-pro`，
  旧的 `deepseek-v4-pro` 会在启动时自动规范化。
- 云端 key 查找支持同一 DeepSeek provider family 复用，`config.yaml` 里已有的
  `deepseek/deepseek-v4-flash` key 可用于 reasoning 模型。
- 新增可选真实云端 smoke test，设置 `PDF_AI_READER_RUN_CLOUD_TESTS=1` 时会使用当前
  `config.yaml` 调用 DeepSeek reasoning 模型验证问答链路。
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

### 公式 OCR / MFD 性能

已完成：

- `MathOCR` 新增公式图片 hash 缓存。
- 缓存命中时不检查 Pix2Text 可用性、不加载模型、不推理。
- 单元测试验证缓存命中不会触发模型加载。
- `MathOCR.recognize_batch(..., max_uncached=N)` 支持缓存优先和未命中推理预算。
- 默认交互式 PDF 解析把 MFD 页扫描预算设为 `max_mfd_pages=0`，打开文档、滚动、缩放、翻译不再等待重型页面公式检测。
- 显式精扫场景可以传入 `max_mfd_pages>0`，候选页已按图片、已有公式块、LaTeX/数学符号密度排序。
- 扫描版/图片公式的 MFR 已按页、置信度、面积做优先级排序；默认只走缓存回填，不对未命中图片即时推理。
- 未进入预算或识别失败的公式仍写入 `DocumentBlock`，标记 `needs_ocr=True`，为后台公式索引继续补扫保留稳定位置。
- 新增 `FormulaIndexFlow`，对 `needs_ocr=True` 的公式块做后台预算式 OCR。
- 新增 `FormulaIndexStore`，使用 SQLite 持久化 `doc_hash/block_id/page/bbox/priority/status/latex/image_hash/model/error/attempts`，支持 queued / running / done / failed / skipped 状态。
- `FormulaIndexStore` 同时持久化页面级 MFD 任务，导入 PDF 时把全文页码入队，用于发现图片/扫描版公式。
- 新增 `FormulaIndexScheduler` / `FormulaScanPolicy`，把视口页、全文问答 evidence 页、用户触发页转换为统一扫描计划。
- 默认阅读路径使用 cache-only 小批量扫描；只有显式高精度计划才允许 MFR 加载模型推理。
- 工具菜单和工具栏新增“公式精扫”入口，用户主动触发时只高精度扫描当前视口附近公式，避免误触发长文档全篇推理。
- 打开文档后立即持久化全文公式任务：已有公式块进入 OCR 队列，所有页进入页面级 MFD 队列；真正识别在后台空闲小批量执行。
- 页面级 MFD 后台批次会把新发现的图片/扫描公式写成 `DocumentBlock`，标记 `needs_ocr=True`，再进入公式 OCR 队列。
- 后台 background 扫描不连续 drain，只跑一小批并等待下一轮空闲定时器；显式当前视口高精度扫描才允许连续处理当前范围。
- 公式索引任务数据库写入 `data/formula_index_jobs.db`，已加入 `.gitignore`，不会进入版本库。
- 公式后台识别成功后会刷新页面 block，并通过 `KnowledgeEngine.upsert_blocks()` 增量写回知识库。
- 如果公式识别早于基础知识库构建完成，主窗口会暂存增量块，等 `build_finished` 后统一写入，避免竞态导致全文问答漏掉公式。

## 异步公式索引与知识图谱规划

这个过程分成三条可独立回滚的后台流水线，不能混成一个同步任务。

当前已经落地的是第一条和第二条的基础闭环：阅读路径保持轻量，全文 RAG 基础索引可快速可用，公式 OCR 与页面 MFD 任务已经在导入时持久化，视口/evidence/用户触发页已有统一调度策略，识别结果能增量 upsert 回知识库。还没有完成的是全篇高精度确认流、量化公式精度审计和 GraphRAG 图谱 worker。

```text
PDF 打开/滚动/缩放
  -> 快速块解析
  -> 渲染缓存/页面块缓存

全文 RAG 索引
  -> DocumentBlock 基础文本和已知公式
  -> 先让全文问答可用

公式索引 + GraphRAG
  -> 导入时全文页码进入 MFD 队列
  -> 导入时已有公式候选进入 MFR 队列
  -> 后台小批量 MFD 发现图片/扫描公式
  -> OCR 缓存命中立即回填
  -> 未命中按优先级批量 MFR/高精度后端
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

可以在知识库构建期间启动公式扫描。现在采用的是导入即排队：基础知识库先构建，全文页面 MFD 和公式 OCR 任务同时进入持久队列；但高精度 OCR 不同步塞进知识库构建主流程。

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
  -> 导入时全文页码/公式候选入队
  -> 后台异步小批次执行
  -> 命中缓存立即返回
  -> 未命中按优先级批量 OCR
  -> 增量更新知识库和 UI

知识图谱索引
  -> 读取章节/概念/定理/公式/引用
  -> 异步抽取关系
  -> 增量服务 GraphRAG
```

也就是说，“构建知识库时完成全部扫描”现在被拆成“导入时完成全文扫描任务排队 + 后台分批执行 + 错误修正轮”。基础全文索引先完成，公式扫描任务按优先级运行；每识别一批公式，就增量写回知识库和未来图谱索引。这样既能越扫越准，又不会让用户为了打开文档等完整 OCR。

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
- PaddleOCR PP-FormulaNet / PP-FormulaNet_plus：评估为本地高性能公式识别后端，优先测试 plus-S 的速度/精度平衡，再测试 plus-M/L 的高精度档。
- UniMERNet：作为复杂真实公式的高精度备选，适合低置信度修正轮。
- MinerU / Marker / Nougat：评估科学 PDF 到结构化 Markdown/LaTeX 的整体提取能力，不直接放入交互式逐公式热路径。
- Mathpix：作为可选云端高精度公式 OCR，不默认启用，适合用户明确选择“高精度模式”。

原则：

- 本地模型优先保证隐私和成本。
- 云端高精度只作为可配置增强。
- 所有结果必须缓存。

## 公式 OCR 性能与工具迁移策略

在不损失精度的前提下，大幅提升公式 OCR 性能主要靠管线优化，而不是简单降低模型大小。

已落地的无损优化：

- MFD 与 MFR 解耦：页面级 MFD 先找 bbox，MFR 只处理需要 LaTeX 的裁剪图。
- 导入时全篇排队，后台按页小批量执行，避免打开 PDF 时同步加载重模型。
- MFR 图片 hash 缓存：同一公式二次打开不再推理。
- MFR `max_uncached` 预算：默认交互路径只回填缓存，不让未命中图片抢 CPU。
- 后台 background 不连续 drain，防止长文档持续占满 CPU。
- Pix2Text MFD 检测器进程内复用，避免每个后台小批次重复初始化检测模型。
- MFD 结果做同页重叠框去重，新增图片/扫描公式保留各自 bbox，避免重复占位块污染 OCR 队列。

当前公式审计基线：

- Attention no-MFD：源码公式 180 个，PDF 公式块 23 个，源码常见命令恢复率 0，`source_near_match_rate=0.020`，`source_weak_match_rate=0.049`，平均相似度约 0.226。
- Attention no-MFD 当前会触发公式质量门禁失败，原因是 LaTeX 命令恢复率和弱匹配率都远低于最低阈值。
- Attention MFD 第 3-5 页：新增 2 个图片/扫描公式候选，均标记 `needs_ocr=True`，当前默认缓存优先路径没有得到 LaTeX；重复 bbox 写入问题已修复。
- Napkin 前 120 页 no-MFD：源码公式约 30929 个，PDF 公式块 726 个，源码常见命令恢复率 0，`source_near_match_rate=0.012`，`source_weak_match_rate=0.033`，说明现有抽取把大量数学文本保留为普通排版文本而不是 LaTeX。
- Napkin 审计瓶颈在源码公式与 PDF 公式相似度匹配，不在 PDF 解析；已用 token 倒排索引和候选数限制把前 80 页匹配从约 73s 降到约 29s。后续如果要全篇反复对比模型，可把这一段下沉到 C++17/pybind11。
- `tools/formula_latex_audit.py --quality-gate` 已加入硬门禁，默认要求 `common_source_command_recall >= 0.35`、`source_weak_match_rate >= 0.35`、`low_similarity_pdf_rate <= 0.60`。当前 baseline 应失败，后续接入 Paddle/UniMERNet/更好文本公式恢复后必须用该门禁证明提升。

可用 C++17 / Python C API 加速的边界：

- 适合下沉：页面 bbox overlap 匹配、公式裁剪前的图像预处理、LaTeX 源码与 PDF 抽取公式的大规模相似度审计、批量 hash/归一化这类纯计算热点。
- 不适合下沉：Qt UI 调度、SQLite 任务状态机、RAG 后端选择、模型加载和失败降级。这些仍保留在 Python，避免版本冲突和跨线程复杂度扩散。
- 接入方式优先 `pybind11` 或稳定 C ABI 小模块；每个 native 模块必须有 Python fallback、独立基准和 Attention/Napkin 回归测试。
- 只有当 Python 侧算法和数据结构稳定后再写 C++，否则会把错误架构固化成更难维护的二进制扩展。

下一步可插拔后端：

- `pix2text`: 继续作为当前默认后端，兼容已有缓存和代码路径。
- `paddle_formula`: 评估 PaddleOCR 3.x 的 PP-FormulaNet / PP-FormulaNet_plus。官方公式识别模块支持 `PP-FormulaNet_plus-S/M/L`、`UniMERNet` 和 `batch_size` 配置，适合作为本地高性能后端。
- `unimernet`: 作为复杂真实公式的高精度备选，适合放在低置信度修正轮。
- `nougat`: 适合整页科学 PDF 转 Markdown 的离线增强，不适合作为交互式逐公式 OCR 主链路。

迁移原则：

- 新后端必须实现同一个 `FormulaRecognizer` 接口，不能把 Paddle/UniMERNet 直接写死在 UI 或知识库流程里。
- `MathOCR` 已改为通过 `FormulaRecognizerRegistry` 创建后端，默认 `formula_ocr_backend=pix2text-mfr`；Paddle/UniMERNet 后续只需新增后端实现并复用现有缓存、限流和任务队列。
- 每个后端独立缓存 key 必须包含 `image_hash/model/model_version/preprocess_version`，避免新旧模型结果冲突。
- 默认模式必须保证 Attention/Napkin 打开、滚动、缩放性能不回退。
- 高精度模式允许更慢，但必须可暂停、可恢复、有进度、有低置信度修正队列。
- 引入新工具前必须跑 Attention/Napkin PDF 与 LaTeX 源公式的 recall/precision 审计，不以主观观感替代验收。

## 下一步执行顺序

1. **提交当前持久公式任务队列改动**
   - 已通过 `pytest -q`。
   - 需要通过 Attention / Napkin E2E。
   - 提交前后继续检查无额外自动署名。

2. **完善公式扫描调度器**
   - 持久任务表已完成。
   - 导入时全文页面级 MFD 任务和已有公式 OCR 任务已经入队。
   - 视口、问答 evidence、用户触发页已经统一生成扫描计划。
   - 后台空闲小批次扫描和当前视口高精度入口已完成。
   - 下一步补全篇高精度确认流和扫描进度/暂停 UI。
   - 每批继续动态限制 MFD/MFR 数量，避免 CPU 抢占阅读和渲染。
   - 二次打开时从任务表恢复 queued / failed 任务，done 任务直接依赖 OCR cache 和知识库 metadata。

3. **完善公式结果增量写回知识库**
   - 已有 `DocumentBlock` 更新。
   - 已新增 `KnowledgeEngine.upsert_blocks()` 和后端 `upsert_blocks()`。
   - 后台公式识别结果会增量 upsert 到当前知识库后端。
   - 全文问答可引用识别后的公式。
   - 下一步要把任务表 done 状态、缓存 hash 和知识库 metadata 串起来，避免重复扫描。

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
- 默认交互式解析不加载 MFD 页检测模型。
- 公式 OCR 缓存命中时不加载模型。
- 提交信息和提交日志不出现额外自动署名。

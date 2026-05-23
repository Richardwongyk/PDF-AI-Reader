# RAG / GraphRAG 迁移方案

## 目标

把当前“Chroma 向量检索 + 手写问答流程”升级为可扩展的全文理解架构，同时保留现有 PDF 解析、阅读 UI、测试桥和缓存机制。

核心约束：

- 桌面阅读、滚动、缩放、翻译不能因索引任务卡顿。
- `DocumentBlock` 继续作为 UI 与 PDF 定位的稳定边界。
- 每个迁移阶段必须能单独测试、提交、回滚。
- 新索引版本不能覆盖旧 Chroma 数据，避免用户知识库失效。
- 高质量全文问答、结构抽取、知识图谱抽取可走 `cloud_reasoning`，翻译和轻量任务可走 `cloud_translation`。

## 调研结论

### LlamaIndex

适合作为第一阶段 RAG 编排层。它的官方 RAG 文档把流程拆为加载、索引、检索、生成和评估；这与当前项目的 `DocumentBlock -> Chroma -> AskQuestionFlow -> QAService` 链路能直接对应。

采用方式：

- 先引入 `llama-index-core` 和 `llama-index-vector-stores-chroma`。
- 不让 LlamaIndex 接管 PDF 解析，PDF 解析仍由 PyMuPDF / Pix2Text 管线负责。
- 将 `DocumentBlock` 映射为 LlamaIndex Node，metadata 保留 `block_id/page/type/section/bbox/doc_hash`。
- 继续返回项目内部的 evidence dict，避免 UI 大改。

### Haystack

Haystack 的 pipeline、retriever、ranker 设计成熟，适合生产 RAG 流水线。但它对当前项目来说迁移面更大，并会引入额外 telemetry 相关依赖。它适合作为后续对照或服务化方案，不作为第一落点。

### Qdrant

Qdrant 适合第二阶段替换或并行 Chroma。它对本地持久化、payload filter、dense/sparse hybrid search 更友好，也更适合大型 PDF 知识库。当前 `qdrant-client[fastembed]` 在 Python 3.14 环境中 dry-run 可解析。

采用方式：

- 第一阶段仍用 Chroma，降低迁移风险。
- 第二阶段新增 `QdrantRepo`，数据目录使用 `data/knowledge_bases_qdrant_v1`。
- 通过 `rag.backend` 在 `legacy_chroma / llamaindex_chroma / qdrant` 间切换。

### Neo4j / GraphRAG

知识图谱应作为第三阶段，不应该阻塞 RAG 编排迁移。

优先路线：

- `LlamaIndex PropertyGraphIndex + Neo4j`：适合从 PDF 中抽取概念、公式、定理、章节、引用关系。
- Microsoft GraphRAG：适合全局社区摘要和大型语料多跳问答，但索引成本和工程侵入更高，后续作为高级模式评估。
- Neo4j GraphRAG Python：官方包目前文档标注 Python 3.10-3.13，项目当前是 Python 3.14，先不作为硬依赖。

## 当前架构基线

当前已完成的可回滚检查点：

- `KnowledgeEngine.retrieve()` 多取候选并做向量 + 关键词混合重排。
- evidence 中携带 `retrieval_score / lexical_score / vector_score`。
- UI 依据树展示“相关度”，不再暴露原始 distance。
- `AppConfig.rag` 已预留 RAG 后端和图谱开关。
- `ModelConfig.cloud_translation / cloud_reasoning` 已分离。

## 目标架构

```text
PDF / OCR / MFR
  -> DocumentBlock[]
  -> KnowledgeIndexFacade
       -> LegacyChromaIndexBackend
       -> LlamaIndexChromaBackend
       -> QdrantHybridBackend
       -> GraphIndexBackend
  -> RetrievalResult[]
  -> AskQuestionFlow
  -> QAService(cloud_reasoning)
  -> Evidence UI / answer / followups
```

### 稳定接口

新增 `KnowledgeIndexBackend` 抽象：

- `build(blocks, doc_hash, force_rebuild)`
- `retrieve(query, doc_hash, top_k, exclude_ids)`
- `check_exists(doc_hash)`
- `get_status(doc_hash)`
- `delete(doc_hash)`

现有 `KnowledgeEngine` 保持 Qt 信号和线程池职责，内部按 `config.rag.backend` 委托具体 backend。

## 迁移阶段

### Phase 1: LlamaIndex Chroma 后端

目标：

- 引入 LlamaIndex Node/VectorStoreIndex 编排。
- 底层仍用 Chroma，数据目录与 collection 名使用新版本：`pdf_li_v1_{doc_hash}`。
- 现有 `legacy_chroma` 保持默认，新增 `llamaindex_chroma` 可配置启用。
- 性能审计：直接使用 LlamaIndex ChromaVectorStore 写入已有 embedding 的构建耗时约为旧后端 4 倍，不能放入热路径。优化后写入/检索热路径改为 Chroma 原生批量 upsert/query，LlamaIndex 保留为 schema/GraphRAG 编排层；在 488 块合成 Attention 规模基准上，旧后端构建约 1.46s、检索约 0.035s，优化后 LlamaIndex 后端构建约 1.51s、检索约 0.036s，性能基本持平。

验收：

- `pytest -q` 通过。
- Attention E2E 通过，日志 ERROR/WARNING/CRITICAL 为 0。
- Napkin E2E 通过，知识库构建和全文问答耗时不显著劣化。

### Phase 2: Qdrant 混合检索

目标：

- 新增 Qdrant 本地后端。
- 支持 dense + lexical/sparse hybrid retrieval。
- 保留 Chroma 后端，允许配置回退。

验收：

- 1050 页 Napkin 构建时间不高于 Chroma 基线 1.25 倍。
- 全文问答仍返回 8 条 evidence，且每条可双击跳转。
- 没有 UI 卡顿或主线程阻塞。

### Phase 3: GraphRAG

目标：

- 抽取章节、概念、公式、定理、证明、实验、引用之间的关系。
- Graph 索引作为增量增强，不阻塞基础全文问答。
- 仅在 `rag.enable_graph_index=true` 时构建。

验收：

- 普通 RAG 可在无 Neo4j 环境下完全正常。
- GraphRAG 失败不会影响 PDF 打开、翻译、基础 QA。
- 图谱问答必须展示路径证据，不允许无来源回答。

## 版本与兼容策略

- `legacy_chroma` 数据保持原样，不做迁移写入。
- 新后端使用独立 collection/data dir：`li_v1`、`qdrant_v1`、`graph_v1`。
- `doc_hash` 仍是文档主键；索引版本作为 collection 前缀。
- `DocumentBlock.id` 是 UI 定位主键，任何新工具返回结果都必须映射回该 id。
- 配置缺字段时由 Pydantic 默认值补齐，旧 `config.yaml` 不需要手工编辑。
- 每阶段独立 git commit；提交信息不加入共同贡献者字样。

## 性能策略

- 索引构建继续在 `QThreadPool` 后台线程执行。
- Chroma 批量写入默认使用 512 块一批；5000 块合成基准从 batch=50 的约 16.2s 降至约 9.9s。
- 基础索引写入 `index_fingerprint/index_block_count/index_schema`，手动重建时如果当前 `DocumentBlock` 指纹一致则跳过全量 embedding/upsert。
- 增量公式 OCR 块允许追加到 collection；指纹只判断基础块集合，避免后台公式块导致无变化文档反复全量重建。
- 默认打开文档不阻塞等待 GraphRAG。
- 长文档默认先建基础向量/混合索引，图谱抽取排队或手动触发。
- 重排候选池由 `rag.candidate_pool` 控制，默认 48。
- evidence 输出由 `rag.final_evidence` 控制，默认 8。

## 模型策略

- `cloud_translation`: 翻译、轻量解释、低延迟任务。
- `cloud_reasoning`: 全文问答、结构化摘要、图谱抽取、多跳推理。
- 无 API Key 时仍使用 Mock，不影响本地索引和 E2E。
- 高质量模式可以配置为 DeepSeek v4 pro 类模型；具体模型名由 LiteLLM/provider 支持情况决定。

## 第一阶段落地任务

1. 新增 `src/core/knowledge_backends.py`，定义 backend 抽象和 legacy adapter。
2. 新增 LlamaIndex Chroma backend，先走项目现有 `EmbeddingService`，避免重复下载 embedding 模型。
3. `KnowledgeEngine` 改为 facade，保留原信号。
4. 增加 backend 选择测试、Node metadata 映射测试、旧配置兼容测试。
5. 在 `config.example.yaml` 中保留默认 `legacy_chroma`，避免未验证前影响用户。
6. LlamaIndex Chroma 默认写入路径不得使用；后端必须走项目优化过的原生批量写入路径。后续默认性能路径仍应评估 Qdrant hybrid，但 LlamaIndex 可以继续作为 GraphRAG/高级编排候选。

## 公式识别性能策略

公式识别不能以牺牲阅读体验为代价全量同步运行。后续改造按三层执行：

- 快速打开层：PDF 首屏、滚动、缩放只依赖 PyMuPDF 原生文本和已有缓存。
- 按需精扫层：只对视口附近、用户双击、问答证据涉及的疑似公式块触发 MFD/MFR。
- 后台批处理层：空闲时增量扫描，结果写入公式 OCR 缓存；长文档按页优先级队列运行，可暂停和恢复。

当前已落地的性能闸门：

- 公式图片先按 hash 查 `data/formula_ocr_cache.db`，缓存命中不加载 Pix2Text。
- `MathOCR.recognize_batch(..., max_uncached=N)` 可以限制本轮 MFR 推理的缓存未命中数量。
- MFD 找到的图片/扫描公式先按优先级进入有限 OCR 预算，其余保留 `needs_ocr=True` 占位，等待后台公式索引补扫。
- `FormulaIndexFlow` 已接入主窗口，后台补扫 `needs_ocr=True` 的公式块。
- 识别完成的公式通过 `KnowledgeEngine.upsert_blocks()` 增量写入当前知识库后端，不重建整个文档索引。
- 知识库未就绪时，公式增量块会先暂存，等基础索引构建完成后 flush。

待落地的异步索引层：

- 持久化 `FormulaIndexWorker` 任务表，字段包含 `doc_hash/page/bbox/block_id/image_hash/status/priority/latex/model/error`。
- `FormulaIndexScheduler`：把视口、点击解释、问答 evidence、后台空闲扫描合并成一个持久优先级队列。
- `GraphIndexWorker`：在 `rag.enable_graph_index=true` 时抽取章节、概念、定理、公式、引用关系；图谱失败只降级 GraphRAG，不影响基础 RAG。

验收门槛：

- 打开 Attention/Napkin 的首屏时间不因公式精扫增加。
- 同一公式二次打开必须命中缓存，不重复 MFR。
- E2E 日志不得出现 OCR 线程阻塞 UI 或渲染队列积压。

## 官方资料

- LlamaIndex RAG: https://docs.llamaindex.ai/en/stable/understanding/rag/
- LlamaIndex Neo4j Property Graph: https://docs.llamaindex.ai/en/stable/examples/property_graph/property_graph_neo4j/
- Haystack: https://docs.haystack.deepset.ai/docs/intro
- Haystack Retrievers: https://docs.haystack.deepset.ai/docs/retrievers
- Qdrant Hybrid Search: https://qdrant.tech/documentation/beginner-tutorials/hybrid-search-fastembed/
- Microsoft GraphRAG: https://microsoft.github.io/graphrag/get_started/
- Neo4j GraphRAG Python: https://neo4j.com/docs/neo4j-graphrag-python/current/

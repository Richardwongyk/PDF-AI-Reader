# TinyBDMath 公式恢复方案

最后更新：2026-06-02

本文只保留当前要执行的方案。旧的边分类器、弱标签、decoder 补丁、性能脚本
流水账都降级为历史基线，不再作为主路线。

## 1. 一句话原则

能让模型学会的，不放到后处理里补。

PDF 里每个小字、小符号、小横线，模型要直接判断：

- 它是不是公式的一部分。
- 它是正文符号、空格、噪声，还是根号线、分数线、括号等结构证据。
- 它跟谁组成上下标、分数、根号、括号、文本块。
- 多个 PDF 小块是不是其实共同表示一个数学符号。
- 哪些内容应该丢弃，哪些内容应该进入最终公式。

输出整理器，也就是代码里的 decoder，只做最后整理：把模型给出的公式结构树
写成 LaTeX，并在低置信时拒绝或只给候选。它不能猜，不能补洞，不能写
“如果看到这个就替换成那个”。

字体、Unicode、PDF 文本层、bbox、向量线条等都不是后处理规则，而是模型输入
证据。要把这些证据喂给模型，让模型用它们判断，而不是最后写死映射。

## 2. 当前状态

已经提交的基线：

- 旧关系打分链路可以全量跑完，速度已基本够用。
- 全量 29881 行 direct eval：decoded exact 约 0.523，near 约 0.660。
- 结论：性能不是主瓶颈，质量瓶颈在标签、模型输出、结构解码和验证。

当前未提交的第一版垂直切片：

- 公式结构树 schema：`src/core/tinybdmath_cslt_schema.py`
- 目标树生成：`src/core/tinybdmath_target_tree.py`
- PDF 到目标树对齐：`src/core/tinybdmath_alignment.py`
- 符号等价证据交集：`src/core/tinybdmath_symbol_equivalence.py`
- 图结构模型：`src/core/tinybdmath_graph_parser.py`
- 相关工具和测试。

2026-06-02 已按“TinyBDMath 内不写本地符号表”的边界重跑 200 行审计：

- target tree：200 行，199 成功。
- alignment：rows_with_hard_labels=148，avg_hard_alignment_rate=0.940763。
- audit：hard_row_rate=0.915，relation_row_rate=0.74，gate passed。
- 临时产物：`test_artifacts/tinybdmath_graph_parser_audit_200_no_local_symbol_map/`
  不提交。

主要失败桶：

- PDF 把一个符号拆成多个 glyph，例如某些箭头或近似等号。
- 根号、点乘等符号 identity 证据不足。
- 少量复杂源码环境 KaTeX 解析失败。
- 个别文本块、operator name、顺序冲突。

这些失败要进证据层或模型标签层解决，不能回到 alignment/decoder 写规则。

## 3. 必须遵守的边界

禁止：

- 用 OCR/MFR 解决 born-digital PDF 的主问题。
- 在 alignment、decoder、后处理里维护 TeX/Unicode 符号表。
- 把复合符号写成固定 glyph 序列。
- 在 decoder 里猜根号主体、分数上下、上下标组、文本组。
- 用字符串替换或样本正则伪装识别能力。
- 把低置信 TinyBDMath 结果写正文、FTS、向量库或 GraphRAG。

允许：

- 用 PDF 自带事实：文本层、glyph、font、bbox、vector、ActualText、ToUnicode。
- 用统一资源：font cmap、AGL、TeX glyph list、OpenType MATH、Unicode Math。
- 在训练和审计阶段用源码生成目标标签。
- 用通用数学排版约束检查模型输出。
- 低置信时拒绝或 candidate-only。

## 4. 关键分工

### 4.1 证据层

负责收集事实，不负责猜答案。

输入来自 PDF 和训练工具：

- 每个 glyph 的文本、字体、大小、位置、颜色、可见性。
- 每条向量线的位置和形状。
- PDF 的 ToUnicode、ActualText、glyph name、font cmap。
- 训练阶段的 KaTeX/MathML/源码目标树。

输出给模型：

- 这个小块可能是什么符号。
- 它是不是空白、噪声、结构线条或语义符号。
- 它有哪些可靠来源和置信度。

### 4.2 标签层

负责把训练样本标清楚。

每个 PDF 小块都要尽量有标签：

- 保留还是丢弃。
- 属于哪个公式结构。
- 和哪个小块有父子关系。
- 是上下标、分数、根号、括号、文本块，还是普通顺序。
- 多个小块是否组成一个符号或一组文本。

对齐失败不能藏起来。失败要分桶：

- 解析失败。
- PDF identity 不足。
- 字体或 cmap 缺证据。
- 文本组不连续。
- 二维结构复杂。
- 只能 ignore，不能作为 hard label。

### 4.3 模型层

负责学习主要判断，不能只给两两关系分数。

模型至少要学：

- 每个节点是不是语义节点。
- 每个节点的符号身份。
- 每个节点的父节点。
- 父子关系类型。
- 哪些节点组成同一组。
- 哪些线条是分数线、根号线、overline 等。
- 整个公式的置信度。

也就是说，模型输出的是“公式结构树”，不是一堆零散边。

### 4.4 输出整理层

只做通用整理：

- 检查结构树有没有环。
- 检查分数有没有分子分母。
- 检查根号有没有主体。
- 检查上下标有没有 base。
- 把合法结构树写成 LaTeX。
- 给出候选、证据、warning、拒绝原因。

不做：

- 不补字符串。
- 不猜缺失主体。
- 不为了评测好看改写结构。
- 不把低置信候选强行 accepted。

## 5. 当前实现策略

代码里把“公式结构树”叫 CSLT。这个名字只在代码和测试里使用，文档中理解成
“模型要输出的公式结构”即可。

当前第一版的做法：

1. 训练阶段用 KaTeX parse tree 生成目标公式结构树。
2. MathML 只作为目标侧符号身份别名证据，不进入生产推理。
3. PDF 侧身份来自 PDF 文本层和 symbol identity repair。
4. alignment 只消费两边的身份集合、bbox 和顺序，不临时调用 KaTeX，不维护符号表。
5. Graph Parser 学习从 PDF 图到公式结构树。
6. 多轮公式流水线里的 TinyBDMath 候选轮次，也就是代码里的 r2a，只接受
   Graph Parser 模型文件；没有模型文件时拒绝输出，不回退旧路线。

这条路的核心是：失败样例先变成标签和模型问题，再训练模型解决；不是在
decoder 里写补丁。

## 6. 下一步怎么做

### 第一阶段：扩大标签审计

目标：确认训练标签可信。

任务：

1. 用当前无本地符号表代码跑 2000 行 target tree 和 alignment。
2. 输出失败分桶。
3. 看清楚哪些是模型应该学的，哪些是证据层缺失，哪些应该 ignore。
4. 不训练大模型前，先确认标签质量。

验收：

- hard aligned rows 不低于 70%。
- sub/sup/fraction/radical/text/fence/operator 都有分项指标。
- artifact、空白、marker 不进入 hard label。

### 第二阶段：补模型要学的标签

目标：让模型能学，而不是让后处理猜。

任务：

1. 给每个 PDF 小块补 node mask 标签：保留、丢弃、结构线条、噪声。
2. 给 group/text/operator 补组边界标签。
3. 给根号线、分数线、overline 等补 vector role 标签。
4. 对复合 glyph 保留证据和 soft label，不写固定拆分表。

验收：

- 失败样例能在标签层表达清楚。
- 不再把空白或装饰 glyph 当成语义节点。
- 不再要求 decoder 猜结构。

### 第三阶段：训练图结构模型

目标：让模型直接输出公式结构。

任务：

1. 用 2000 行先训练 smoke。
2. 再扩大到全量训练。
3. 每轮输出公式级准确率，不只看局部关系分数。
4. 与旧 edge model 做对照。

验收：

- formula exact/near 超过旧 direct eval。
- `h_{t-1}`、根号、文本块不依赖 decoder 猜。
- relation recall 提升时 precision 不崩。
- 模型可导出独立 artifact，主程序环境不强制装 PyTorch。

### 第四阶段：约束整理和验证

目标：把模型输出变成可用候选。

任务：

1. 对模型输出做通用结构合法性检查。
2. 生成多个候选。
3. 用 verifier 检查符号覆盖、bbox 覆盖、布局一致性、置信度。
4. 低置信时明确拒绝。

验收：

- 明显错误能被拒绝。
- accepted 默认仍然保守。
- 所有候选都有证据和拒绝原因。

### 第五阶段：接入 r2a 和评估

目标：安全进入多轮公式流水线。

任务：

1. `TinyBDMathCandidateService` 使用 Graph Parser artifact。
2. r2a 只写 candidate-only。
3. accepted gate 单独校准。
4. r5 只消费 accepted 或人工 revision。
5. Attention/Napkin 限页和全量都跑评估。

验收：

- 二次打开同 input hash 跳过。
- 不加载 OCR/MFR。
- 不污染正文、FTS、向量库、GraphRAG。
- 有公式级 exact、near、结构编辑距离、accepted precision、abstain rate。

## 7. 测试门禁

当前必须跑：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest `
  tests/test_tinybdmath_symbol_equivalence.py `
  tests/test_tinybdmath_alignment.py `
  tests/test_tinybdmath_alignment_audit.py `
  tests/test_tinybdmath_target_tree.py `
  tests/test_tinybdmath_graph_parser.py `
  tests/test_tinybdmath_candidate_service.py `
  tests/test_formula_multiround_pipeline.py `
  tests/test_full_software_validation.py -q
```

当前已通过：49 passed。

硬编码残留搜索：

```powershell
rg -n "def _symbol_leaf_texts|mapping = \{|decomposed = \{|\\cong|\\pi|\\omega|\\epsilon|\\mapsto|\\ldots|\\cdots|_UNICODE_TO_LATEX" `
  src/core/tinybdmath_alignment.py `
  src/core/tinybdmath_target_tree.py `
  src/core/tinybdmath_symbol_equivalence.py `
  tests/test_tinybdmath_alignment.py `
  tests/test_tinybdmath_symbol_equivalence.py `
  tests/test_tinybdmath_target_tree.py -S
```

该搜索允许中心 `symbol_identity_repair` 或资源层有统一表；不允许 TinyBDMath
alignment、decoder、后处理路径有局部表。

## 8. 停止依赖的旧思路

停止：

- 只训练两两关系分类器就当完成。
- 只看 relation F1。
- 在 decoder 里补根号、分数、上下标、文本块。
- 通过 canonicalizer 放宽评测。
- 为性能继续堆 JSONL 脚本，却不改标签和模型。
- 用 OCR/MFR 覆盖 born-digital 事实。

保留为历史基线：

- 旧 edge model。
- 旧 weak relation labels。
- 旧 direct eval。
- 旧 structural candidate。

## 9. 接手判断标准

继续训练或接入前，先问三个问题：

1. 目标公式结构树能不能表达失败样例？
2. PDF 小块到目标结构的标签是否可信？
3. 模型是否已经直接学习这些结构，而不是 decoder 在猜？

这三个答案不是“是”之前，不要训练一晚上，也不要继续改 decoder。

# 当前目标与下一步

最后更新：2026-06-01

本文是新会话接手时的短入口。历史流水账已从本文删除，避免把过期状态、
旧指标和临时路线误当成下一步计划。更细的 TinyBDMath 深度方案见
`docs/tiny_born_digital_math_model_engineering.md`。

## 1. 当前真实目标

把 PDF AI Reader 做成高性能、可审计、可长期维护的论文阅读和全文理解工具。
当前最重要的未完成部分是 born-digital 公式恢复质量，而不是继续堆 OCR 或
只优化评分脚本。

核心目标：

1. born-digital PDF 默认走 PDF 结构事实：文本层、glyph、font、bbox、vector、
   ActualText、CMap、ToUnicode、OpenType MATH。
2. 图片/扫描公式才走 OCR/MFR，并且必须后台化、缓存化、可暂停、可恢复。
3. 公式结果必须能追溯到页码、bbox、PDF graph、模型版本、候选证据和审核状态。
4. RAG/GraphRAG 只消费 accepted 或人工 revision 的高置信内容。
5. 打开、滚动、缩放、翻译和基础问答不能等待公式重任务。

## 2. 当前代码与提交状态

最近已提交两类成果：

- `5b5b38e Optimize TinyBDMath relation scoring pipeline`
  - PyTorch batch/vectorized relation scoring。
  - compact score。
  - direct structural decode。
  - no-score-jsonl。
  - streaming structural/decoded eval。
  - 全量 direct eval：rows=29881，structural F1=0.315585，
    decoded exact=0.523242，near=0.659550。

- `a258fe2 Clean legacy TinyBDMath tooling and docs`
  - 删除旧 `realdata` 和旧 scoring 入口脚本。
  - 清理旧缓存/日志/临时 benchmark 输出。
  - 新增 `docs/workspace_inventory.md`。
  - 更新 README/AGENTS/TODO/交接文档。

当前还有一组未提交的 TinyBDMath 试验性代码改动：

- `src/core/tinybdmath_structural_candidate.py`
- `tools/tinybdmath_eval_decoded_latex.py`
- `tests/test_tinybdmath_structural_candidate.py`
- `tests/test_tinybdmath_eval_decoded_latex.py`

这组改动主要是全局 parent forest 约束和 decoded eval canonicalization 试验。
它能改善部分样例，但仍不能解决标签、对齐、文本组、artifact 和全局语法问题。
因此它只能作为 baseline/ablation，不能当成最终 decoder bug fix。

`测试资料/` 是用户资料，不提交、不清理、不移动。

## 3. 关键结论

### 3.1 性能不是当前主瓶颈

2026-06-01 的 full direct eval 已证明：

- scoring/eval 可以在全量 29881 行上运行。
- JSONL 中间文件和逐边 Python 循环已经不是唯一瓶颈。
- 继续只做 fast score 优化，不会把 decoded exact 从 52% 提到可用水平。

下一步必须改监督、模型、解码和 verifier。

### 3.2 旧 decoder 路线不够

在 decoder 里修 `\sqrt{d_k}`、`h_{t-1}`、`d_{\text{model}}` 这类个案，看起来
能提高少数样例，但本质是把模型和标签没学到的结构塞进后处理。

正确方向：

- 目标树必须能表达这些结构。
- alignment 必须能把 PDF glyph 对齐到目标树。
- 模型必须输出 parent/relation/group/text/artifact 等信息。
- decoder 只做通用合法性约束和 CSLT 序列化。

### 3.3 更高级模型的前提是更高级标签

直接换大模型或自由生成 LaTeX 不是最佳路线。born-digital PDF 已经有结构事实，
应先建立神经符号训练目标：

- Canonical Symbol Layout Tree。
- PDF graph 到 CSLT 的软/硬对齐。
- node semantic mask。
- graph parser。
- constrained decoder。
- layout verifier。

没有这些，模型变大只会更快学到弱标签噪声。

## 4. 不可违反边界

- 不用 OCR/MFR 解决 born-digital PDF 主问题。
- 不写样本特化正则、固定论文词表、输出字符串替换。
- 不在 decoder 中猜根号主体、分数主体、上下标组。
- 不把低置信 TinyBDMath 输出写 accepted。
- 不把候选直接写正文、FTS、向量库或 GraphRAG。
- 不把外部重工具混进主环境。
- 不提交 `测试资料/`、日志、缓存、临时 benchmark 输出。
- 提交信息不加任何额外署名或生成工具署名。

## 5. 下一步唯一主线

下一步不是继续训练旧 edge model，也不是继续修旧 decoder。主线是建立
神经符号公式恢复数据链：

```text
PDF graph
  -> observation cleaning
  -> target CSLT from source only for training/audit
  -> graph/tree alignment
  -> hard/soft structure labels
  -> graph parser
  -> constrained CSLT decode
  -> layout verifier
  -> candidate-only production output
  -> accepted gate / manual revision / r5 update
```

详细设计见 `docs/tiny_born_digital_math_model_engineering.md`。

## 6. 近期执行顺序

### Step 1：冻结旧试验为 baseline

任务：

- 明确全局 parent forest 试验不是最终修复。
- 不继续在旧 decoder 上叠补丁。
- 若保留代码，必须以 baseline/ablation 命名。
- 若不保留，提交前应撤掉这组试验性改动。

验收：

- 文档写清旧路线边界。
- git diff 中不混入“看似修好 bug 但实为后处理补丁”的改动。

### Step 2：定义 CSLT schema

任务：

- 新增 `src/core/tinybdmath_cslt_schema.py`。
- 支持 symbol、text_run、group、script、fraction、radical、accent、
  under_over、fence、matrix、equation_number、artifact。
- 支持 canonical JSON 序列化和 hash。
- 增加 schema 单元测试。

验收：

- `h_{t-1}`、`\sqrt{d_k}`、`d_{\text{model}}`、简单 fraction、
  简单 matrix 都能被 schema 表达。
- schema 不依赖源码字符串风格。

### Step 3：生成 target tree

任务：

- 新增 `tools/tinybdmath_build_target_trees.py`。
- 从 graph rows 的 `label_latex/raw_source_latex` 只在训练/审计阶段生成 CSLT。
- 第一版可复用 KaTeX parse tree 和 MathML。
- 复杂失败样例记录 parser warning，不丢行。

验收：

- 先跑 200 行。
- 输出 target tree JSONL、manifest、failure buckets。
- 目标树能表达当前失败样例。

### Step 4：实现 alignment

任务：

- 新增 `src/core/tinybdmath_alignment.py`。
- 实现 PDF leaf 到 CSLT leaf 的 matching。
- 实现 text run、script、fraction、radical 的 group alignment。
- 输出 hard/soft/ignore 标签。

验收：

- 200 行 alignment audit 可解释。
- top failures 有分桶原因。
- artifact/spacing/marker 不再无解释进入 hard supervision。

### Step 5：扩大到 2000 行

任务：

- 跑 2000 行 target tree + alignment。
- 输出 per-structure coverage。
- 与旧 weak relation labels 对比。

验收：

- hard aligned rows 达到可训练水平，目标先看 70% 以上。
- sub/sup/fraction/radical/text/fence/operator 至少都有分项指标。
- 未覆盖结构明确列入 ignore/abstain，而不是错误 hard label。

### Step 6：训练 Graph Parser M1

任务：

- 新增 `src/core/tinybdmath_graph_parser.py`。
- 用 PyTorch 训练 node mask、parent pointer、relation type。
- 处理类别不平衡：class weight、focal loss 或 balanced sampling。
- 导出主程序可读 artifact。

验收：

- 2000 行 dev formula exact/near 明显超过旧 direct eval 抽样基线。
- 失败样例不依赖 decoder 猜。
- 关系 recall 提升时 precision 不崩。

### Step 7：CSLT constrained decoder + verifier

任务：

- 新增 `src/core/tinybdmath_constrained_decode.py`。
- 新增 `src/core/tinybdmath_layout_verifier.py`。
- 输出 n-best CSLT/canonical LaTeX、confidence、warnings、blockers。

验收：

- verifier 能拒绝明显错误候选。
- 低置信输出仍 candidate-only。
- accepted gate 默认关闭或极保守。

### Step 8：r2a 集成与全量评估

任务：

- `TinyBDMathCandidateService` 支持新 graph parser artifact。
- evidence JSON 写入 graph/model/decode/verifier 版本。
- Attention/Napkin 限页 pipeline 验证二次打开跳过。
- 全量评估 formula-level exact/near/tree edit/render match。

验收：

- 不加载 OCR/MFR。
- 不污染正文/RAG/GraphRAG。
- 质量报告超过旧 direct eval，且分项可解释。

## 7. 当前推荐命令

轻量测试基线：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest `
  tests/test_external_formula_tools.py `
  tests/test_formula_index_flow.py `
  tests/test_born_digital_math.py `
  tests/test_formula_semantic_review.py `
  tests/test_smoke.py -q
```

TinyBDMath 旧 baseline 测试：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest `
  tests/test_tinybdmath_structural_candidate.py `
  tests/test_tinybdmath_latex_decoder.py `
  tests/test_tinybdmath_eval_decoded_latex.py -q
```

后续新增神经符号流水线后，应改用：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe `
  tools\tinybdmath_build_target_trees.py --limit 200 --output-dir test_artifacts\tinybdmath_cslt_target_200

C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe `
  tools\tinybdmath_align_targets.py --limit 200 --output-dir test_artifacts\tinybdmath_alignment_200

C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe `
  tools\tinybdmath_audit_alignment.py --input test_artifacts\tinybdmath_alignment_200\alignment_rows.jsonl
```

这些新命令目前是计划目标，未实现前不要假装已经跑过。

## 8. 停止做的事

- 不继续写旧 decoder 补丁。
- 不继续用 canonicalizer 掩盖结构错误。
- 不继续只追 scoring 吞吐。
- 不继续把 relation precision 当公式准确率。
- 不继续用 OCR/MFR 兜 born-digital 主问题。
- 不继续在文档里堆历史执行日志。

## 9. 提交前检查

提交前必须：

```powershell
git status --short
rg -n "额外署名|来源标记|生成工具署名|自动署名" . -S `
  -g '!测试资料/**' -g '!test_artifacts/**' -g '!logs/**'
```

不要提交：

- `测试资料/`
- `test_artifacts/`
- `logs/`
- `.pytest_cache/`
- 模型缓存
- 临时 benchmark 输出

## 10. 判断是否继续训练

除非同时满足以下条件，否则不要继续长时间训练：

1. CSLT target tree 已生成并审计。
2. PDF graph 到 CSLT alignment 已通过 200/2000 行审计。
3. hard/soft/ignore 标签分层明确。
4. 旧 baseline 对比指标已固定。
5. 训练目标是 graph parser，而不是旧 edge softmax。

如果这些条件不满足，训练一晚上只会放大错误标签和旧 decoder 的问题。

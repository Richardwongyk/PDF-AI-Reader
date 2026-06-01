# 新会话交接文档

最后更新：2026-06-01

本文件是新终端/新 AI 助手接手项目时的交接入口。旧版长篇流水账已删除，
历史执行记录从 git、`TODO.md` 和测试产物中查。当前真实下一步以本文和
`docs/current_goal_and_next_steps.md` 为准。

## 1. 必读顺序

1. `AGENTS.md`
2. `docs/current_goal_and_next_steps.md`
3. `docs/tiny_born_digital_math_model_engineering.md`
4. `docs/async_formula_indexing_design.md`
5. `docs/workspace_inventory.md`
6. `TODO.md` 只作为历史索引，不再作为精确下一步计划

不要先安装工具，不要先训练，不要先改 decoder。先确认工作树、环境、后台
进程和当前文档边界。

## 2. 当前真实状态

- 工作目录：`D:\程设大作业`。
- 主程序环境：`C:\Users\WYK\.conda\envs\pdf_ai_reader_314`。
- 训练环境可用：`C:\Users\WYK\.conda\envs\science`，不要把 torch 装进主环境。
- 隔离工具环境已存在：`pdf_tool_paddle310`、`pdf_tool_mineru310`、
  `pdf_tool_pix2text310`、`pdf_tool_magic310`、`pdf_tool_pek310`。
- 外部工具模型缓存统一放在仓库 `.tool_models/`，通过
  `PDF_AI_READER_TOOL_MODELS_DIR` 覆盖。
- `测试资料/` 是用户资料，不提交、不清理、不移动。
- `test_artifacts/`、`logs/`、缓存、临时 benchmark 输出不提交。

最近已提交：

- `5b5b38e Optimize TinyBDMath relation scoring pipeline`
- `a258fe2 Clean legacy TinyBDMath tooling and docs`

当前还可能存在未提交 TinyBDMath 试验代码：

- `src/core/tinybdmath_structural_candidate.py`
- `tools/tinybdmath_eval_decoded_latex.py`
- `tests/test_tinybdmath_structural_candidate.py`
- `tests/test_tinybdmath_eval_decoded_latex.py`

这些是全局 parent forest/decoded eval canonicalization 试验，只能作为 baseline
或 ablation，不能当成最终 decoder bug fix。提交前必须明确保留或撤掉，不要
和神经符号新路线混在一个提交里。

## 3. 当前核心结论

TinyBDMath 性能路径已跑通，但公式质量不达标：

- 全量 direct eval rows=29881。
- 耗时约 192.59s。
- structural F1=0.315585。
- decoded exact_match_rate=0.523242。
- decoded near_match_rate=0.659550。

这说明：

- fast score/streaming eval 已经够用来做全量实验。
- 继续只优化 JSONL、batch、direct decode 不会解决质量。
- 根因是监督、目标树、PDF/目标对齐、模型结构、约束解码和 verifier。

下一步主线已经改为神经符号公式恢复：

```text
PDF graph
  -> observation cleaning
  -> source-derived target CSLT for training/audit only
  -> graph/tree alignment
  -> hard/soft/ignore labels
  -> graph parser
  -> constrained CSLT decode
  -> layout verifier
  -> candidate-only r2a output
  -> accepted gate / manual revision / r5 update
```

详细方案见 `docs/tiny_born_digital_math_model_engineering.md`。

## 4. 禁止事项

禁止：

- 用 OCR/MFR 解决 born-digital PDF 主问题。
- 在 decoder 中为 `\sqrt`、`\frac`、上下标、文本组写样本特化补丁。
- 用固定论文词表、固定命令表、字符串替换或 ad hoc 正则伪装公式识别。
- 把 relation precision 当成 formula-level 准确率。
- 把低置信 TinyBDMath 输出写正文、FTS、向量库或 GraphRAG。
- 把 Paddle/MinerU/Pix2Text/UniMERNet 等重工具混进 UI 热路径或主环境。
- 提交 `测试资料/`、`test_artifacts/`、`logs/`、缓存、模型权重、临时产物。
- 提交信息添加额外署名、来源标记或生成工具署名。

允许：

- 用源码在训练/审计阶段生成 target tree。
- 用 Unicode Math、AGL、TeX glyph list、OpenType MATH、font cmap 等数据资源。
- 用通用数学排版语法约束。
- 用 verifier 做 evidence-based reject/abstain。
- 把旧 edge model 作为 baseline、pretrain 或 ablation。

## 5. 多轮公式解析要求

多轮公式解析不是“一次性同步扫描”。导入后可以尽早全篇入队，但每轮都必须
异步分批、结果落库、可恢复、二次打开跳过。

| 轮次 | 名称 | 目标 | 默认路径 |
| --- | --- | --- | --- |
| r0 | `r0_pdf_structure` | born-digital 快扫，抽取文本层、glyph、font、bbox、vector、图片和页级候选 | 不 OCR，不阻塞首屏 |
| r1 | `r1_cached_recognition` | 对图片/扫描/needs_ocr 候选先查缓存 | 未命中才推理，不能抢 UI |
| r2 | `r2_local_high_precision` | 本地高精度/多工具候选，处理低置信或用户显式精扫 | 独立 worker，结果 candidate-only |
| r2a | `r2a_tinybdmath_structural` | born-digital 非 OCR 结构候选 | candidate-only，不 accepted |
| r3 | `r3_cloud_semantic_review` | DeepSeek 等模型做语义校对建议 | 写 result JSON，不覆盖正文 |
| r4 | `r4_knowledge_graph` | 写公式/章节/定理/概念/引用图谱证据 | 异步，不阻塞基础问答 |
| r5 | `r5_knowledge_incremental_update` | accepted 变化后增量 upsert 知识库和 GraphRAG artifact | 只消费 accepted |

硬要求：

- 每轮任务必须有 `queued/running/done/failed/skipped`。
- 每轮输入必须有 hash。
- 同一 input hash、模型版本、预处理版本命中时必须跳过。
- 低置信结果只能写候选和 warnings。
- UI 热路径只读缓存，不做模型冷启动。
- r5 不能消费未审核 fusion、rejected 或低置信候选。

对应设计：`docs/async_formula_indexing_design.md`。

## 6. 下一步具体工作

### 6.1 不要继续做旧 decoder 补丁

如果看到全局 parent forest 试验，先把它标记为 baseline/ablation。不要继续往
旧 decoder 添加个案逻辑。

### 6.2 先做 CSLT schema

建议新增：

- `src/core/tinybdmath_cslt_schema.py`
- `tests/test_tinybdmath_cslt_schema.py`

必须能表达：

- `h_{t-1}`
- `\sqrt{d_k}`
- `d_{\text{model}}`
- 简单分数
- 简单矩阵/cases/aligned 的骨架
- artifact/spacing 节点

### 6.3 再做 target tree builder

建议新增：

- `src/core/tinybdmath_target_tree.py`
- `tools/tinybdmath_build_target_trees.py`

要求：

- 源码只用于训练/审计。
- parser backend/version/warnings 必须入 manifest。
- 解析失败保留 failure bucket，不丢样本。
- 输出 CSLT JSONL，不直接追作者源码 exact。

### 6.4 再做 alignment

建议新增：

- `src/core/tinybdmath_alignment.py`
- `src/core/tinybdmath_alignment_audit.py`
- `tools/tinybdmath_align_targets.py`
- `tools/tinybdmath_audit_alignment.py`

要求：

- 输出 hard/soft/ignore 标签。
- 对 artifact、spacing、marker、unknown 节点给出原因。
- 先跑 200 行审计，再扩大到 2000 行。
- 未通过 alignment audit 前不要训练大模型。

### 6.5 最后才训练 Graph Parser

建议新增：

- `src/core/tinybdmath_graph_parser.py`
- `tools/tinybdmath_train_graph_parser.py`

训练目标：

- node semantic mask。
- parent pointer。
- relation type。
- group boundary。
- vector/rule role。

训练前提：

- CSLT target tree 已通过审计。
- alignment rows 已通过 200/2000 行审计。
- hard/soft/ignore 分层明确。
- 旧 direct eval baseline 固定。

## 7. 环境检查命令

接手后先跑：

```powershell
git status --short
conda env list
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'pdf_tool_|pdf_formula_|mineru|magic-pdf|paddleocr|unimernet|conda.*create|tinybdmath|keep_awake' } |
  Select-Object ProcessId,Name,CommandLine
```

防休眠检查：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*keep_awake*' } |
  Select-Object ProcessId,Name,CommandLine
Get-Content logs\keep_awake_watchdog.log -Tail 20
```

如需启动防休眠：

```powershell
Start-Process -FilePath powershell.exe `
  -ArgumentList '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "D:\程设大作业\tools\keep_awake_watchdog.ps1" -IntervalSeconds 60 -WorkerIntervalSeconds 20' `
  -WindowStyle Hidden
```

这只能防普通空闲睡眠，不能防断电、电池耗尽、系统更新、用户手动关机或
系统策略强制锁定。

## 8. 测试基线

轻量接手测试：

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

新 CSLT/alignment 工具实现后，必须新增对应测试并把命令写回
`docs/current_goal_and_next_steps.md`。

## 9. 提交前检查

```powershell
git status --short
rg -n "额外署名|来源标记|生成工具署名|自动署名" . -S `
  -g '!测试资料/**' -g '!test_artifacts/**' -g '!logs/**'
```

提交只包含本次任务相关文件。不要把试验代码、文档重写、测试产物混在同一个
含义不清的提交里。

## 10. 何时可以说完成

不能因为“训练跑完”或“性能更快”就说完成。

阶段性完成条件：

1. CSLT schema 能表达主要失败结构。
2. target tree builder 有 200 行审计报告。
3. alignment 有 200/2000 行审计报告。
4. graph parser 在 formula-level eval 上超过旧 baseline。
5. constrained decoder/verifier 能拒绝明显错误候选。
6. r2a 仍 candidate-only，accepted gate 独立校准。

最终产品完成还需要 Attention/Napkin 大样本、交互 E2E、日志审计、RAG/GraphRAG
证据链和 accepted precision 统计置信区间，当前尚未达到。

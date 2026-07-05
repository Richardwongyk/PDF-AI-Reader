# Formula OCR Performance Design

本文件只保留 OCR/MFR 性能策略。普通 born-digital PDF 的公式判断走 PDF 结构事实、
CSLT alignment 和 Graph Parser；图片、扫描、无文本层或低证据区域才进入 OCR/MFR
候选轮。

2026-05-28 状态补充：OCR/MFR 仍只作为 r1/r2 候选后端；accepted/rejected
审核、manual revision、evidence 预览、PDF bbox 定位和 r5 写回已经接线，但这些不改变
OCR 边界。默认 born-digital 路径仍不能加载 OCR/MFR，r2/r2a 候选输出仍必须经过 fusion/门禁。

同日性能复盘补充：阅读器渲染优化不能用 OCR/MFR 或 tile-only 占位来掩盖页面不可见问题。Napkin
极大缩放快速滚动时出现黑底/空白页属于 UI P0，不是 OCR 后端问题；修复应在渲染 fallback 层完成。

## 目标

公式 OCR 的目标不是“同步把全书扫完”，而是在不破坏阅读体验的前提下，让公式识别越用越完整、越扫越准。普通文本 PDF 的公式应先通过字符、字体、bbox、行几何结构抽取；图片/扫描公式才进入 MFD/MFR。

硬约束：

- 默认打开、滚动、缩放、翻译不等待 MFD/MFR。
- 不把新虚拟环境作为主程序默认部署方案；第三方公式工具 worker 已从当前代码路线移除。
- 不把 PaddleOCR、MinerU、PDF-Extract-Kit、UniMERNet 等重依赖混进主环境。
- 任何新后端必须先通过 Attention/Napkin 的速度和 LaTeX 对齐审计。
- 同一图片、同一模型、同一预处理版本必须命中缓存，不能重复推理。
- OCR/MFR 只属于 r1/r2 补救轮；born-digital r0 默认不 OCR。

## 当前判断

当前主环境是 Python 3.14 + CPU PyTorch + ONNXRuntime，无 CUDA。PaddlePaddle 没有可用的 Python 3.14 wheel，因此 PaddleOCR 不能直接装进主环境。单独新建 Python 3.12 worker 能跑，但会增加部署复杂度和磁盘体积，不适合作为默认方案。

2026-07-05 状态补充：第三方公式工具 worker 和 Paddle 适配层已拆除。
当前代码不再自动发现或调用 `pdf_tool_*` 隔离环境。若未来单独评估第三方工具，
应作为仓库外离线研究，不再恢复为内置 r2 worker 通道。

2026-07-05 决策更新：第三方公式工具 worker 和 Paddle 适配层已拆除。后续默认路线应是：

1. 保留现有 Pix2Text/cnstd 栈作为默认后端。
2. born-digital 公式质量主线转到 TinyBDMath 本机结构模型。
3. 先优化裁剪、去重、批量、缓存、后台调度。
4. 不再通过本项目内置接口发现或调用 PaddleOCR、MinerU、PDF-Extract-Kit、UniMERNet 等第三方公式工具。

## 分层架构

```text
PDF 打开
  -> 快速 PyMuPDF 文本/块解析
  -> 基础全文索引可用
  -> 公式任务持久化入队

阅读路径
  -> 只查 OCR cache
  -> 不加载 MFR 模型

视口/问答 evidence/用户精扫
  -> MFD 找 bbox
  -> 裁剪公式图
  -> hash 去重
  -> 小批量 MFR
  -> 写 cache + DocumentBlock + 知识库增量 upsert

后台空闲
  -> 小批次页面扫描
  -> cache-only 回填优先
  -> 有预算才推理
  -> 可暂停/恢复
```

与多轮公式解析对应：

- r0：只做 PDF 结构快扫，不走 OCR/MFR。
- r1：缓存优先 OCR/MFR，处理图片/扫描/needs_ocr 候选。
- r2：本机/缓存候选复核低置信或用户精扫候选；第三方公式工具 worker 已移除。
- r3：云端语义复核只校对候选，不替代 OCR/MFR 或 PDF 结构证据。

## 后端策略

- `pix2text-mfr`: 默认后端。优点是当前环境已有、可运行、部署成本低。短期优化重点是管线，不是盲目换模型。
- 第三方公式工具 worker / PaddleOCR / MinerU / PEK / UniMERNet：已从当前代码路线移除，不再作为本项目内置候选后端。

## 性能优化优先级

1. **缓存命名空间**
   - `image_hash + backend + model + preprocess_version`。
   - 避免 S/M/L 模型互相污染结果。

2. **裁剪质量**
   - 对已有公式块使用更合理的 padding 和 DPI。
   - 对扫描公式使用 bbox 面积、置信度、页码优先级。
   - 裁剪前做 bbox 合并/去重，减少重复推理。

3. **批量推理**
   - 统一从队列收集小批公式图。
   - 同一批内按 image hash 去重，只推理唯一图片；未命中预算按唯一图片计，重复图复用同一次识别结果。
   - 模型加载只发生在显式精扫或后台非 cache-only 预算内。

4. **后台调度**
   - 默认 background 不连续 drain，防止长文档持续吃 CPU。
   - 当前视口和用户点击优先。
   - 问答 evidence 页次优先。
   - 其余页只在空闲时低优先级扫描。

5. **C++17 适用边界**
   - 适合：bbox overlap/去重、批量裁剪前的几何过滤、图像二值化/边界收紧、公式审计相似度匹配。
   - 不适合：UI 调度、SQLite 任务状态、模型调用、RAG 后端选择。
   - 原则：先稳定 Python 算法和测试，再把热点下沉到 pybind11/C API。

## 验收门禁

每个新后端或管线优化必须给出数字：

- Attention：公式样本 OCR 平均耗时、冷启动耗时、LaTeX 弱匹配率。
- Napkin 前 120 页：公式样本 OCR 平均耗时、审计耗时、弱匹配率。
- UI E2E：打开、滚动、缩放、翻译日志无 ERROR/WARNING/CRITICAL。
- 默认路径：未触发精扫时不得加载 MFR 模型。

不满足这些条件时，后端只能保持可选实验状态，不能进入默认策略。

## 下一步

1. 对现有本机候选路径和 TinyBDMath r2a 做大样本性能和质量对照，报告冷启动、批量、缓存命中、P95 和源码相似度。
2. 优化常驻 worker、批内 hash 去重、裁剪参数、后台 batch budget 和超时策略。
3. 对 r2 候选继续执行 `local_precise_degraded_against_born_digital` 检查，质量低于 born-digital 结构证据时不得进入 accepted。
4. 若图片公式 OCR fallback 仍达不到门禁，只保留为显式精扫或离线审计；默认程序继续保持轻量。

## 当前基准

命令：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_ocr_benchmark.py --case attention --max-pages 6 --sample-limit 4 --output test_artifacts\formula_ocr_benchmark_attention.json
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\formula_ocr_benchmark.py --case napkin --start-page 60 --max-pages 20 --sample-limit 2 --output test_artifacts\formula_ocr_benchmark_napkin_p60.json
```

结果：

- Attention：解析 0.538s，裁剪 0.105s，Pix2Text 可用性/冷加载 102.378s，4 个公式 OCR 91.690s，平均 22.922s/公式，临时缓存命中 0.001s。
- Napkin page 60-79：解析 0.258s，裁剪 0.214s，冷加载 21.561s，2 个样本 OCR 13.946s，平均 6.973s/公式，临时缓存命中约 0ms。
- 两份样本都出现正文被公式块误判的问题，导致 MFR 输出大量逐字母 `\mathrm{...}`，说明准确率瓶颈不仅是模型，也包括候选过滤和裁剪范围。
- 已收紧数学字体公式判定：自然语言证明句、图注、定义句不再仅因数学字体进入公式 OCR；Napkin page 60-79 公式候选从 135 降到 66。
- 所有 `BlockType.FORMULA` 写入解析结果、OCR 回填和知识库索引时都必须带 LaTeX 数学定界符；行间公式使用 `$$...$$`，段落里的数学字体 span 使用 `\(...\)`。

结论：

- 当前 Pix2Text MFR 不能进入同步交互路径。
- 解析/裁剪不是主要性能瓶颈；MFR 模型加载与推理是主要瓶颈。
- 缓存命中几乎零成本，因此导入即排队、后台小批、二次打开复用缓存是正确方向。
- 下一步应先优化公式候选过滤，避免把长正文段落送进 MFR；随后再做批内 hash 去重和更小裁剪框。
- 后续还需要做行级公式切分，避免整块表格行或混合自然语言数学句被整段包装为行间公式。

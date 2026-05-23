# Formula OCR Performance Design

## 目标

公式 OCR 的目标不是“同步把全书扫完”，而是在不破坏阅读体验的前提下，让公式识别越用越完整、越扫越准。

硬约束：

- 默认打开、滚动、缩放、翻译不等待 MFD/MFR。
- 不把新虚拟环境作为默认部署方案。
- 不默认引入 PaddleOCR 这类重依赖。
- 任何新后端必须先通过 Attention/Napkin 的速度和 LaTeX 对齐审计。
- 同一图片、同一模型、同一预处理版本必须命中缓存，不能重复推理。

## 当前判断

当前主环境是 Python 3.14 + CPU PyTorch + ONNXRuntime，无 CUDA。PaddlePaddle 没有可用的 Python 3.14 wheel，因此 PaddleOCR 不能直接装进主环境。单独新建 Python 3.12 worker 能跑，但会增加部署复杂度和磁盘体积，不适合作为默认方案。

PaddleOCR 官方公式模块提供 `FormulaRecognition(model_name=...)` 和 `predict(input=..., batch_size=...)`，输出字段为 `rec_formula`。官方数据里 `PP-FormulaNet_plus-S` 侧重速度和英文公式，CPU 高性能模式约 260ms/公式；`PP-FormulaNet_plus-M/L` 准确率更高但 CPU 推理明显更慢。RapidLaTeXOCR 使用 ONNXRuntime/OpenVINO，技术路线更轻，但当前包对 Python 3.14 不友好，不能无代价接入主环境。

所以默认路线应是：

1. 保留现有 Pix2Text/cnstd 栈作为默认后端。
2. 把 OCR 做成可插拔后端，已接入 `paddle_formula` 适配层但不默认启用。
3. 先优化裁剪、去重、批量、缓存、后台调度。
4. 只有在真实审计证明收益时，才启用 Paddle/ONNX 高精度后端。

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

## 后端策略

- `pix2text-mfr`: 默认后端。优点是当前环境已有、可运行、部署成本低。短期优化重点是管线，不是盲目换模型。
- `paddle_formula`: 已接入适配层。适合后续高精度实验，不默认启用。缓存命名空间包含模型名，例如 `paddle_formula:PP-FormulaNet_plus-S:png-v1`。
- `rapid_latex_ocr`: ONNXRuntime/OpenVINO 方向，适合未来轻量后端。需要解决 Python 3.14 包兼容或手动模型集成问题后再接入。
- `pix2tex/simple-latex-ocr/texify`: 目前依赖增量较大或 CPU 性能不确定，先不进入默认路线。

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
   - 同一批内按 image hash 去重，只推理唯一图片。
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

1. 修复并提交当前可插拔后端与缓存命名空间。
2. 增加 `tools/formula_ocr_benchmark.py`，从 Attention/Napkin 抽样裁剪公式图，输出冷启动、批量耗时、缓存命中和样例结果。
3. 基于基准结果优化 Pix2Text 管线：批内 hash 去重、裁剪参数、后台 batch budget。
4. 若 Pix2Text 准确率仍达不到门禁，再评估轻量 ONNX 后端或 Paddle 独立 worker；默认程序仍保持轻量。

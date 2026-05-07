# PDF AI Reader — 实施路线图与完成状态

> 基于 Plan `floating-gathering-rocket.md` 六、修订后的实施路线图
> 最后更新：2026-05-07

---

## P0：稳定性（不修会崩）

| # | 事项 | 状态 | 实现方式 | 与 Plan 差异 |
|---|------|------|---------|-------------|
| 1 | ChromaDB 读写锁 | ✅ 已完成 | `KnowledgeEngine` 使用 `QReadWriteLock`；`retrieve()` 加 `QReadLocker`；`_BuildWorker.run()` 加 `QWriteLocker` | 完全一致 |
| 2 | `_active_threads.remove` 安全检查 | ✅ 已完成 | `ai_engine.py`：`remove(t) if t in self._active_threads else None` | 完全一致 |
| 3 | `close_document()` 资源释放完整性 | ✅ 已完成 | 关闭流程：停止解析线程 → 断开信号 → close fitz.Document → deleteLater QPdfDocument → 取消异步渲染池 → 清空缓存。`MainWindow.closeEvent` 依次调用 `close_document()` → `KnowledgeEngine.close()` → `GlossaryManager.save()` | 完全一致 |

---

## P1：渲染与交互（决定能不能用）

| # | 事项 | 状态 | 实现方式 | 与 Plan 差异 |
|---|------|------|---------|-------------|
| 4 | **引入 QtPdf 矢量渲染** | ⚠️ 部分完成 | `DocumentEngine.get_page_pixmap()` 优先使用 `QPdfDocument.render()`（PDFium 引擎），自动回退 PyMuPDF。`DocumentEngine._on_parse_finished` 加载 `QPdfDocument`。状态栏显示当前渲染引擎名 | **未创建 `QtPdfPageWidget`**，未使用 `QPdfView`。原因：`QPdfView` 是整文档滚动组件，与裂缝 `SplitWidget`、`BlockOverlay` 的 QVBoxLayout 架构不兼容。当前方案是折中：PDFium 渲染 → QPixmap → QLabel，矢量品质等同原始目标，同时保留裂缝交互能力 |
| 5 | **渲染异步化** | ✅ 已完成 | `_PageRenderTask` (QRunnable) 在 `QThreadPool` 中独立渲染页面；通过 `page_rendered` Signal 回调主线程 | 完全一致。Plan 提到"QtPdf 自带异步，此条可能被 #4 消解"——实际未消解，因为 QPdfDocument 的 `render()` 是同步的 |
| 6 | **公式检测 I/O 优化** | ✅ 已完成 | `Pix2TextMFDDetector.detect_specific_pages()`：`pix.save(img_path)` → `io.BytesIO(pix.tobytes("png"))` 零磁盘 I/O | 完全一致 |
| 7 | **WebView 热备池** | ✅ 已完成 | 新建 `WebViewPool` 类：`acquire()` 优先从热备取 / `release()` 回收 / `prewarm()` 后台预热。折叠时截图冻结 + 回收 WebView，展开时从池获取。活跃 Chromium 进程上限 2 个 | **QWebChannel 实现有差异**：Plan 说"动态 setParent"，实际发现这会导致 Chromium IPC 崩溃。改为永久绑定 `QWebChannel` 到 WebView 实例，只通过 `swap_bridge()` 切换 bridge 对象。Pool 的 `acquire()` 用 `setHtml("")` 清空旧 DOM 防止内容泄漏 |
| 8 | **SplitWidget 展开/折叠动画** | ✅ 已完成 | `_animate_expand()` / `_animate_collapse()` 使用 `QPropertyAnimation` 动画化 `maximumHeight`，`OutCubic` 250ms / `InCubic` 200ms | 完全一致 |
| 9 | **暗黑主题 HTML 适配** | ✅ 已完成 | `markdown_template.html`：CSS 变量（`:root` / `body.dark` / `body.sepia`）+ `setTheme()` JS 函数。`theme.py`：`SPLIT_WIDGET_STYLE_DARK` + `get_split_style()`。主题默认为 `light`（浅紫渐变裂缝），系统原生窗口配色 | 默认主题为 `light` 而非 `dark`。原因：测试发现暗色裂缝背景 + 暗色 WebView 内容导致文字不可读。浅紫渐变裂缝在系统暗色窗口上可读性好 |
| 10 | **KaTeX 渲染后高度回调** | ✅ 已完成 | HTML 中 `ResizeObserver` 监听 body 高度 → `QWebChannel` bridge 实时推送到 Python `_HeightBridge.onHeightChanged()` Slot。`qwebchannel.js` 从 Qt 官方仓库下载到本地（`qrc:///` 协议在 PySide6 中不可用） | 完全一致 |

---

## P2：解析增强（让翻译和 QA 真正好用）

| # | 事项 | 状态 | 实现方式 | 与 Plan 差异 |
|---|------|------|---------|-------------|
| 11 | **引入 MFR（公式→LaTeX）** | ✅ 已完成 | 新建 `src/core/math_ocr.py`：`MathOCR` 类封装 Pix2Text MFR v1.5 模型（TrOCR 架构）。`recognize(image_bytes)` → LaTeX。CPU 优化：`OMP_NUM_THREADS` 多线程、`enable_table=False` 仅加载公式模块、`recognize_batch()` 批量推理、图片超 768px 自动缩放 | Plan 要求"评估 Pix2Text MFR 或 LaTeX-OCR"——最终选用 Pix2Text MFR v1.5（ONNX 推理快于 LaTeX-OCR 的 ViT 模型） |
| 12 | **MFR 与 MFD 管线整合** | ✅ 已完成 | MFR 从 `_ParseThread` 移出，改为 `MainWindow._run_mfr_thread()` 后台独立线程运行。处理所有 `BlockType.FORMULA` 块（chunker 检测的 + MFD 检测的），不再限于 MFD 标记。MFD 检测 BBox → 裁剪 PDF → MFR 识别 LaTeX → 注入 `$$...$$` 回 `block.content` | **差异 1**：MFR 在独立后台线程而非解析线程中运行，避免 Pix2Text 模型加载（46s）阻塞解析。**差异 2**：MFR 处理所有 FORMULA 块，不限于 MFD 标记的（chunker 通过数学字体检测的块也能被 MFR 处理） |
| 13 | **PyMuPDF4LLM 增强解析** | ✅ 已完成 | 新建 `PyMuPDF4LLMChunker` 类。`enhance_blocks()` 使用 `difflib.SequenceMatcher` 将 PyMuPDF4LLM 的 Markdown 输出对齐到现有 BBox 块（相似度 > 60% 即替换）。异步运行于后台线程 | **差异**：Plan 建议"作为 `FormulaDetector` 风格的抽象接口"——实际采用更简单的独立类 + 后台线程。不在解析线程中运行（避免 fitz 并发 segfault） |
| 14 | **MFD 结果驱动翻译** | ✅ 已完成 | `TranslationService._build_messages()`：当 `block_type == "formula"` 时，追加强化指令："当前段落已通过视觉模型识别为数学公式，严格保留所有公式占位符及其 LaTeX 代码"。`TextPreprocessor` 新增 `\[...\]` `\(...\)` LaTeX 标准分隔符支持 | 完全一致 |

---

## P3：工程化与性能

| # | 事项 | 状态 | 实现方式 | 与 Plan 差异 |
|---|------|------|---------|-------------|
| 15 | **启动懒加载** | ❌ 未完成 | — | 当前 `main.py` 顶层仍同步导入 `chromadb`, `litellm`, `ollama`。需将重型导入移到函数内部 |
| 16 | **MockLLMClient 的 UI 提示** | ❌ 未完成 | — | 当嵌入模型回退到 Mock 时，状态栏应显示提示 |
| 17 | **公式检测超时/中断** | ✅ 已完成 | `detect_specific_pages()` 每页检查 `QThread.currentThread().isInterruptionRequested()` | 完全一致 |
| 18 | **pytest 测试框架** | ❌ 未完成 | — | 需新建 `tests/` 目录，覆盖 TextPreprocessor、DocumentChunker 等纯逻辑函数 |

---

## 实施过程中发现并修复的额外 Bug

以下 bug 在实施 Plan 过程中被发现并修复，不在原始 Plan 的 18 项清单中：

| Bug | 根因 | 修复 |
|-----|------|------|
| 打开第二个 PDF 崩溃 (SIGSEGV) | `close_document()` 中 `thread.terminate()` 暴杀 ONNX 推理中的线程；QWebChannel 反复 `setWebChannel(None)` 导致 Chromium IPC 崩溃；`_trans_indicators` 中悬空 C++ QWidget | 移除所有 `terminate()`；永久绑定 QWebChannel 到 WebView；`_get_overlay()` / `clear()` 增加 `_isValid()` 守护 |
| 折叠再展开后缓存内容丢失 | `_freeze_webview` 回收 WebView 到池后，展开时获取全新空白 WebView | `_freeze_webview` 保存 `_current_answer` → `_cached_result`；`_thaw_webview` 设置 `_pending_js` 恢复 |
| "清除并关闭"按钮失效 | `_on_clear_close` 先清缓存再调 `close()`，`close()` 又 `_cached_result = _current_answer` 覆盖已清缓存 | 调换顺序：先 `close()` 再清缓存 |
| 关闭文档时翻译线程日志刷屏 | 关闭文档后 `_TranslationThread` 仍在运行，token 信号找不到已销毁的 SplitWidget | `_on_close_document` 遍历 `_active_threads` 全部 `quit()` + `wait()`；token handler 降级为 DEBUG |
| 配置损坏后静默失败 | YAML 解析异常被捕获后 `raw={}`，空 dict 通过 Pydantic 校验使用全部默认值 | 备份损坏配置 → 重建默认配置 |

---

## 关键架构决策记录

### 决策 1：QtPdf 与 PyMuPDF 的关系
**结论**：互补，不是替代。
- `QPdfDocument` / PDFium：负责**屏幕显示**（矢量渲染）
- `fitz.Document`：负责**数据提取**（文本块 BBox、TOC、元数据）
- 未使用 `QPdfView`（整文档滚动组件与裂缝架构冲突），而是 PDFium → QPixmap → QLabel

### 决策 2：Pix2Text MFD 保留，补充 MFR
**结论**：MFD 管线不动，在它下游加 MFR。
- `Pix2TextMFDDetector` 已能检测公式 BBox → 保留
- `MathOCR` 服务，输入公式图片 → 输出 LaTeX
- MFR 在独立后台线程运行，不阻塞解析

### 决策 3：PyMuPDF4LLM 作为可选增强，不做硬依赖
**理由**：AGPL-3.0 许可；对 LaTeX 编译的规范 PDF，现有 chunker 已可用。已安装并使用于后台异步增强。

### 决策 4：不动 SplitWidget 的整体架构
**结论**：`QVBoxLayout` + `QPropertyAnimation` 可以满足需求，不需要 QGraphicsScene。

### 决策 5：WebViewPool 的 QWebChannel 永久绑定
**结论**：QWebChannel 在 WebView 创建时绑定，不复用 `setWebChannel`。反复绑定导致 Chromium STATUS_BREAKPOINT 崩溃。只通过 `registerObject`/`deregisterObject` 动态切换 bridge。

### 决策 6：重活移出解析线程
**结论**：PyMuPDF4LLM、MFR 等耗时操作不在 `_ParseThread` 中运行。解析线程只做分块 + MFD（秒级完成），确保关闭文档时 `wait()` 不会超时。

---

## 验证清单

| 序号 | 验证方法 | 通过标准 | 状态 |
|------|---------|---------|------|
| V1 | 打开 100 页 PDF，快速滚动 | 无白块等待 > 200ms，无崩溃 | 未测 |
| V2 | 连续打开/关闭 10 个裂缝 | 内存增长 < 300MB，无 WebEngine 崩溃 | 未测 |
| V3 | 暗黑主题下查看裂缝内容 | 文字清晰可见 | ✅ 通过（浅紫渐变底色） |
| V4 | 打开含公式的论文 PDF | MFD 检测到公式 ≠ 0，MFR 输出有效 LaTeX | ⚠️ MFR 可检测 372 个公式块，但模型加载需 20s+，用户提前点击翻译时公式尚未识别 |
| V5 | 翻译含公式的段落 | 公式保留为 LaTeX 原样，仅周围文字被翻译 | ⚠️ 部分通过：`\(...\)` `\[...\]` 格式被保护，裸数学表达式（无 LaTeX 标记的）未被保护 |
| V6 | ChromaDB 构建+检索并发 | 无 `database is locked` | ✅ 通过（QReadWriteLock） |
| V7 | 启动计时 | < 2 秒到主窗口可见 | ❌ 未达标（约 13s，含 Qt WebEngine + chromadb + litellm 导入） |

---

## 已知待解决问题

1. **裸数学表达式翻译失败**：PDF 提取的 `xyxyx, x 2 y 9` 等无 LaTeX 标记的公式，`TextPreprocessor` 无法保护。MFR 可解决但加载太慢。需优化 MFR 加载速度或在翻译前等待 MFR 完成。
2. **MFR 模型加载慢**：Pix2Text 首次加载需 20-46s（含 layout+OCR+MFD+MFR 全家桶）。需只加载 MFR 子模块。
3. **P3 三项未完成**：启动懒加载、MockLLMClient UI 提示、pytest 测试框架。
4. **启动 ~13s**：含 Qt WebEngine 冷启动 + chromadb/litellm/ollama 全量导入。

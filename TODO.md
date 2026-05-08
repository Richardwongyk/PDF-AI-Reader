# PDF AI Reader — 全版本演化史 · 当前状态 · 重构路线图

> 基于 git 日志 67 次提交 + 7 个新增文件 + 5 个开源项目深度调研
> 最后更新：2026-05-08 (滚动性能优化完成，重构 Phase 1+2 收尾)

---

## 一、Git 演化时间线（按阶段）

### 阶段 0：原型 (1 commit)

| Commit | 说明 |
|--------|------|
| `f856085` | **项目初始化: PDF AI Reader 原型版**。极简信任模型：PyMuPDF 提取 → chunker 分块 → LLM 直接翻译。无 MFR、无 PyMuPDF4LLM、无公式拦截。TextPreprocessor 仅保护 `$$` 和 `$`。`_TranslationThread.run()` 发送空字符串（Bug：公式恢复从未执行）。 |

### 阶段 1：P0 稳定性 (2 commits)

| Commit | 说明 |
|--------|------|
| `6d30278` | P0/P1 关键 Bug 修复：ChromaDB 读写锁、`_active_threads.remove` 安全检查、`close_document()` 资源释放 |
| `10f5b04` | ConfigManager: YAML 解析失败时备份损坏配置 + 重建默认配置 |

### 阶段 2：P1 渲染与交互 (8 commits)

| Commit | 说明 |
|--------|------|
| `89b2d62` | **引入 QtPdf (PDFium) 矢量渲染**（后被移除，统一回退到 PyMuPDF） |
| `ff8c220` | 异步页面渲染：QThreadPool 后台 `_PageRenderTask` |
| `84fcb55` | SplitWidget: WebView 截图冻结机制 |
| `0c724d3` | **P1 #7: WebViewPool 热备池**。QWebChannel 永久绑定，`swap_bridge()` 动态切换 |
| `e13f6b3` | **P1 #10: QWebChannel 实时推送替代轮询**。`ResizeObserver` → `bridge.onHeightChanged()` Slot |
| `09c4f01` | 深色主题尝试（后被回退） |
| `e5ffeb1` | QWebChannel 修复: 本地 `qwebchannel.js` 替代 `qrc:///` |
| `5ada91d` | SplitWidget 初始化崩溃 + `_get_overlay` C++ 安全性 |

### 阶段 3：P2 解析增强 (5 commits)

| Commit | 说明 |
|--------|------|
| `641cedc` | **P2 #11: MathOCR 公式图片 → LaTeX**。Pix2Text MFR v1.5 (TrOCR/ONNX) |
| `9edb84d` | **P2 #12-14: MFR 管线整合 + PyMuPDF4LLM 增强 + 公式翻译驱动** |
| `7b99600` | 修复 PyMuPDF4LLMChunker logging 未导入 |
| `f38e84f` | MathOCR CPU 加速：OMP 多线程、批量推理 |
| `a7f760a` | 修复公式识别: 支持 `\(...\)` 和 `\[...\]` 分隔符 |

### 阶段 4：崩溃修复期 (18 commits)

| Commit | 说明 |
|--------|------|
| `77cd3c9` ~ `647adec` | 打开第二个 PDF SIGSEGV 系列修复（根因：`thread.terminate()` 暴杀 ONNX 推理线程 + QWebChannel 反复绑定 + 悬空 C++ 对象） |
| `d0a89b1` | 修复折叠再展开后缓存内容丢失 |
| `891e6f1` | 修复"清除并关闭"按钮失效 |

### 阶段 5：MFD/MFR 调参与回退 (7 commits)

| Commit | 说明 |
|--------|------|
| `f29fb78` ~ `fe219f5` | 三次 Revert：公式检测参数回退到原始值 |
| `be55d77` | WebViewPool 内容泄漏修复 |
| `fbb1bef` | MFR 处理所有 FORMULA 块，不限于 MFD 标记的 |

### 阶段 6：工程化 (3 commits)

| Commit | 说明 |
|--------|------|
| `e22f816` | 关闭文档时翻译线程未停止导致日志刷屏 |
| `a9cee0f` | 系统性重写 TODO.md：Plan 全量对照 |
| `c8d0071` | **P3 #15: 启动懒加载**。chromadb/litellm/ollama 导入移到函数内部 |

### 阶段 7：线程安全与公式优化 (2 commits, 本次会话早期)

| Commit | 说明 |
|--------|------|
| `5bbec09` | 线程安全优化: _PageRenderTask 共享文档、砍全局 MFR、MathOCR 加锁、PyMuPDF4LLM 改 QThread |
| `ced8d1f` | 回退 _on_block_translate 公式 OCR 拦截，回归第一版直接翻译 |

### 阶段 8：系统重构 — 四层架构基础设施 + 渲染管线重写 (16 commits, 2026-05-08)

**基础设施层 (3 commits)**：

| Commit | 说明 |
|--------|------|
| `0b0f4b3` | **重构 Phase 1+2: ServiceContainer 懒加载 + 四级错误处理 + 瓦片化渲染架构**。新建 7 文件 (+1818/-264)。 |
| `9482d54` | TODO.md + pack_code.py 排除开源借鉴目录 |
| `2751798` | **集成 AICache 到翻译流程** — 重复翻译零 LLM 调用 |

**渲染管线 (5 commits)**：

| Commit | 说明 |
|--------|------|
| `94549d6` | QPainter 绘制模式（借鉴 qpageview `AbstractRenderer.paint()`） |
| `058e52a` | 画质退化修复：paintEvent 直接绘制全页 pixmap |
| `482e3f0` | **DPR 清晰度 (第一轮)**：删除硬编码 DPR=2.0，渲染到屏幕物理 DPI |
| `25d4ab6` | **DPR 清晰度 (第二轮)**：使用真实屏幕 DPR，pixmap 1:1 物理像素映射 |
| `fbf3dbb` | **DPR 清晰度 (第三轮)**：分离 `_scale`(逻辑DPI) / `_dpi`(物理DPI) |

**预渲染 + 瓦片基础设施 (4 commits)**：

| Commit | 说明 |
|--------|------|
| `a979289` | **方向感知预渲染**（借鉴 Sioyek 趋势感知算法） |
| `2768a2a` | 瓦片渲染器：修正坐标映射 + 接入 paintEvent 信号链路 |
| `2a065fa` | 瓦片渲染日志升级到 INFO |
| `90fb36f` | 修复 TileRenderer 访问已关闭文档崩溃 |

**滚动性能优化 (4 commits)**：

| Commit | 说明 |
|--------|------|
| `97b219d` | 添加滚动 + 渲染路径性能计时日志 |
| `4f1d0f2` | **滚动卡顿 (第一轮)**：缓存 `_compute_page_y_offsets` |
| `e69a046` | **滚动卡顿 (第二轮)**：延迟卸载离开视口的页面 (5s 冷却) |
| `a29a118` | **滚动卡顿 (第三轮)**：缓存 `_get_page_height` |
| `6b1bf8f` | **滚动卡顿 (最终)**：_render_page 移除逐瓦片调度 |

**新增文件 (7 个)**：

| 文件 | 来源 | 用途 |
|------|------|------|
| `src/core/service_container.py` | 借鉴 PDFCrop | DI 容器，Instance/Singleton/Factory 三种生命周期 |
| `src/core/error_handler.py` | 借鉴 PDFCrop | ErrorSeverity 四级严重度 + 全局异常钩子 |
| `src/infra/__init__.py` | — | 基础设施层包 |
| `src/infra/page_cache.py` | 借鉴 PDFCrop | 线程安全 LRU 页面缓存 |
| `src/infra/ai_cache.py` | 全新 | SQLite 持久化 AI 结果缓存 |
| `src/infra/tile_cache.py` | 借鉴 qpageview | 256×256 瓦片 OrderedDict LRU 缓存 + 命中率统计 |
| `src/infra/tile_renderer.py` | 借鉴 qpageview + Syncfusion | 瓦片渲染引擎 + request_id 令牌取消 |

---

## 二、架构层次演化图

```
第一版 (f856085)              当前 (6b1bf8f — Phase 2 收尾)
═══════════════              ════════════════════════════════

PDF                            PDF
 │                              │
 ▼                              ▼
PyMuPDF 提取               PyMuPDF chunker
 │                              │
 ▼                              ├─ MFD 视觉检测 (Pix2Text YOLO)
DocumentChunker                  ├─ PyMuPDF4LLM 增强 (difflib 对齐)
 │  _is_formula_from_spans       │
 │                              ▼
 ▼                         DocumentBlock
用户点击翻译                     │
 │                         用户点击翻译
 ▼                              │
AIEngine                         ▼
 │ translate_block()         _on_block_translate()
 │ protect_formulas($$)          ├─ AICache 检查 (翻译缓存)
 │                              ├─ 直接翻译路径
 ▼                              │   └─ _on_block_explain 有 OCR 路径
LLM 流式                         ▼
 │                         AIEngine
 ▼                              │ translate_block()
_TranslationThread               │ protect_formulas($$+\[+\()  ← 新增 \[\] \(\) 保护
 │ finished_signal.emit("")     │
 │ (Bug: 公式从不恢复)           ▼
 ▼                         LLM 流式
UI 用 _current_answer            │
                                 ▼
                            _TranslationThread
                                 │ full_raw_text 积累
                                 │ _post_process() 恢复公式  ← 已修复
                                 │ finished_signal.emit(final_text)
                                 ▼
                            UI 渲染 (KaTeX)

═══════════════════          ════════════════════════════════
  Infrastructure                 Infrastructure (NEW)
  (none)                         
                                 src/core/
                                 ├─ ServiceContainer (DI 容器)
                                 └─ ErrorHandler (四级严重度)

                                 src/infra/
                                 ├─ PageCache (LRU 页面缓存)
                                 ├─ AICache (SQLite AI 结果缓存)
                                 ├─ TileCache (256×256 瓦片缓存)
                                 └─ TileRenderer (后台瓦片渲染+取消)

                                 渲染管线:
                                 ├─ 屏幕物理 DPI (1:1 像素映射)
                                 ├─ paintEvent + QPainter 绘制
                                 ├─ 方向感知预渲染 (Sioyek)
                                 ├─ Y 偏移/高度缓存 (<5ms)
                                 └─ 延迟卸载 (5s 冷却)
```

---

## 三、各代公式处理机制对比

| 特性 | 第一版 | 中期 | 当前 |
|------|--------|------|------|
| `$$`/`$` 保护 | ✅ | ✅ | ✅ |
| `\[`/`\(` 保护 | ❌ | ✅ | ✅ |
| 公式恢复 `_post_process` | ❌ | ❌ | ✅ |
| MFD 视觉检测 | ❌ | ✅ | ✅ |
| MFR 公式→LaTeX | ❌ | ✅ | ✅ |
| PyMuPDF4LLM 增强 | ❌ | ✅ | ✅ |
| 公式拦截阻塞翻译 | ❌ | ❌ | ❌ (已回退) |
| 按需 OCR | ❌ | ❌ | ✅ (仅 explain) |
| 翻译缓存 (AICache) | ❌ | ❌ | ✅ |

---

## 四、当前状态

### 已完成 ✅

| 项目 | 方式 |
|------|------|
| ServiceContainer 懒加载 | `build_services()`: 13s → 0.33s |
| 四级错误处理 | ErrorSeverity + 全局异常钩子 |
| PageCache 独立服务 | 线程安全 LRU (借鉴 PDFCrop) |
| AICache | SQLite 持久化翻译缓存 |
| TileCache + TileRenderer | 瓦片基础设施 (借鉴 qpageview) |
| DPR 清晰度 | 屏幕物理 DPI + 1:1 像素映射 |
| 方向感知预渲染 | Sioyek 趋势感知算法 |
| 滚动性能 | _update_visible_pages: 1763ms → 15-32ms |
| AICache 翻译集成 | 翻译请求 → 查缓存 → 命中直接显示 |
| 公式恢复修复 | `_post_process` 调用修复 |
| 智能路由回退 | 翻译路径恢复直接翻译 |

### 待完成

| # | 问题 | 优先级 |
|---|------|--------|
| 1 | **MFR 全家桶加载** — Pix2Text 模块参数不生效 | 中 |
| 2 | **瓦片渲染接入显示** — 逐瓦片后台渲染 → paintEvent | 中 |
| 3 | **应用层解耦** — OpenDocumentFlow / TranslateBlockFlow | 低 |
| 4 | **pytest 测试框架** — tests/ 目录 | 低 |
| 5 | **2K/4K 高分屏 DPR 测试** — overlay 定位验证 | 低 |

---

## 五、验证清单

| # | 验证方法 | 通过标准 | 状态 |
|------|---------|---------|------|
| V1 | 打开 100 页 PDF，快速滚动 | 无白块 > 200ms，无崩溃 | 未测 |
| V2 | 连续打开/关闭 10 个裂缝 | 内存增长 < 300MB | 未测 |
| V3 | 暗黑主题下查看裂缝内容 | 文字清晰可见 | ✅ |
| V4 | 打开含公式的论文 PDF | MFD 检测 ≠ 0 | ⚠️ MFR 加载 ~3s |
| V5 | 翻译含公式的段落 | 公式 LaTeX 原样保留 | ✅ |
| V6 | ChromaDB 构建+检索并发 | 无 database is locked | ✅ |
| V7 | build_services 计时 | < 1s | ✅ **0.33s** |
| V8 | 2K/4K 高分屏渲染 | overlay 定位准确 | 未测 |
| V9 | 主窗口可见计时 | < 12s | ✅ **~10s** |
| V10 | 滚动 _update_visible_pages | < 50ms | ✅ **15-32ms** |
| V11 | 翻译缓存命中 | AICache HIT 直接显示 | ✅ |
| V12 | 第二个 PDF 打开 | 无崩溃 | ✅ |

---

## 六、架构决策记录

### 决策 1-6：见前序版本（QtPdf→PyMuPDF、MathOCR 单例、PyMuPDF4LLM 防覆盖、动态缩放、_post_process 修复、智能路由回退）

### 决策 7：HybridModelRouter 三级路由未落地
**现状**：`local_client` 参数传入 `LiteLLMClient`，Ollama 仅在 `EmbeddingService` 中使用。

### 决策 8：SplitWidget 用 QWebEngineView 而非 QTextBrowser
**事实**：TDD 描述与实际实现不一致，QSS `QTextBrowser#result_area` 规则从未生效。

### 决策 9：ServiceContainer 替代 CoreServiceRegistry ✅
**结论**：pdfCrop 三级注册模式，`build_services()` 13s → 0.33s。

### 决策 10：paintEvent + QPainter 替代 QLabel ✅
**结论**：借鉴 qpageview `AbstractRenderer.paint()`，BlockOverlay 自动浮于 QPainter 之上。

### 决策 11：屏幕物理 DPI 渲染 ✅
**结论**：`_scale = logicalDpi/72` (widget 尺寸)，`_dpi = logicalDpi × DPR` (pixmap 渲染)。
pixmap 设置 `devicePixelRatio(DPR)`，QPainter 1:1 物理像素映射。

### 决策 12：四级错误严重度 ✅
**结论**：pdfCrop `ErrorSeverity` 枚举 + `ErrorHandler` 全局异常钩子。

### 决策 13：方向感知预渲染 ✅
**结论**：Sioyek 趋势感知：最近 3 次方向 → 趋势得分 → 动态预加载范围。

### 决策 14：滚动性能优化 ✅
**结论**：Y 偏移缓存 + 页面高度缓存 + 延迟卸载 (5s) + 热路径移除瓦片调度。
`_update_visible_pages`: 1763ms → 15-32ms。

### 决策 15：AICache 集成 ✅
**结论**：翻译请求先查 SQLite 缓存，命中直接显示（~5ms vs 3s LLM）。
翻译完成后自动存入缓存。缓存键: `(block_id, doc_hash, "translation")`。

---

## 七、关键 Bug 简史

| Bug | 根因 | 修复 |
|-----|------|------|
| 打开第二个 PDF 崩溃 (SIGSEGV) | `thread.terminate()` + QWebChannel 反复绑定 | `647adec` |
| 折叠再展开缓存丢失 | WebView 回收空白实例 | `d0a89b1` |
| "清除并关闭"按钮失效 | 先清缓存再 close() | `891e6f1` |
| 关闭文档翻译线程日志刷屏 | `_TranslationThread` 未停止 | `e22f816` |
| 配置损坏后静默失败 | YAML 异常后 `raw={}` | `10f5b04` |
| WebViewPool 内容泄漏 | DOM 残留 | `be55d77` |
| `【FORMULA_0】` 残留不渲染 | `_post_process` 从未调用 | `0b0f4b3` |
| 智能路由大量误判 | `is_bad_extraction` 五字符全无判断 | `ced8d1f` |
| DPR 2x → 画质退化 | 硬编码 2x 导致 QPainter 缩小采样 | `fbf3dbb` |
| 打开第二个 PDF 崩溃 | TileRenderer 访问已关闭文档 | `90fb36f` |
| 滚动卡顿 | `_compute_page_y_offsets` 触发 Qt 同步布局 | `6b1bf8f` |

---

## 八、性能变化实测

| 指标 | 重构前 | 重构后 |
|------|--------|--------|
| `build_services()` | ~13s (eager) | **0.33s** (懒加载) |
| 主窗口可见 | ~13s | ~10s (QWebEngine ~5s 不受控) |
| PDF 打开 (15页) | 0.1s | 0.08s |
| `_update_visible_pages` | ~80ms (估计) | **15-32ms** |
| 翻译响应 (首次) | ~3s (LLM) | ~3s (LLM) |
| 翻译响应 (缓存) | ~3s | **~5ms** (AICache HIT) |
| 方向预渲染命中率 | ~60% (估计) | ~85% (趋势感知) |
| 渲染 DPI | 硬编码 150 | **屏幕物理 DPI** (1:1 映射) |

---

## 九、开源项目借鉴对照

| 项目 | 借鉴内容 | 状态 |
|------|---------|------|
| **PDFCrop** | `container.py` → `service_container.py` | ✅ |
| **PDFCrop** | `error_handler.py` → `error_handler.py` | ✅ |
| **PDFCrop** | `page_cache.py` → `page_cache.py` | ✅ |
| **qpageview** | `cache.py` + `render.py` → `tile_cache.py` + `tile_renderer.py` | ✅ |
| **qpageview** | `AbstractRenderer.paint()` → `_LazyPageWidget.paintEvent()` | ✅ |
| **qpageview** | `PdfRenderer.draw()` 单 tile 模式 → 全页 pixmap 直接绘制 | ✅ |
| **qpageview** | `devicePixelRatioF()` ratio 机制 → 屏幕物理 DPI | ✅ |
| **Syncfusion** | Request cancellation → request_id 令牌 | ✅ |
| **Sioyek** | 方向感知预渲染 → 趋势得分 | ✅ |
| **Mad Professor** | 四层架构信号驱动协调器 | 待实现 |

---

## 十、实施路线图

### 已完成 ✅

| 阶段 | 内容 |
|------|------|
| Phase 1 | ServiceContainer + ErrorHandler + PageCache + AICache |
| Phase 2a | TileCache + TileRenderer 基础设施 |
| DPR 修复 | 屏幕物理 DPI + 1:1 像素映射 (3 轮) |
| 预渲染 | 方向感知预渲染 (Sioyek) |
| AICache | 翻译缓存集成 |
| 滚动优化 | Y 偏移缓存 + 高度缓存 + 延迟卸载 (4 轮) |

### 待完成

| 阶段 | 任务 | 优先级 | 预估工时 |
|------|------|--------|---------|
| 2b | 瓦片渲染接入 paintEvent（混合绘制） | 中 | 3天 |
| 3 | 应用层解耦（OpenDocumentFlow / TranslateBlockFlow） | 低 | 5天 |
| 4 | MFR 全家桶优化 + pytest + 高分屏测试 | 低 | 5天 |

---

## 十一、总结

**已交付 (16 commits，7 个新文件)**：
- DI 容器 + 懒加载 (`build_services`: 0.33s)
- 四级错误处理 + 全局异常钩子
- PageCache / AICache / TileCache / TileRenderer 四层缓存
- 屏幕物理 DPI 渲染 (1:1 像素映射)
- 方向感知预渲染 (Sioyek 趋势算法)
- 翻译结果缓存 (AICache → SQLite)
- 滚动性能优化 (_update_visible_pages: 15-32ms)
- 全部模块详细日志

**架构**：
```
src/
  core/   — 领域层 (TranslationService, QAService, DocumentChunker, ServiceContainer)
  infra/  — 基础设施层 [NEW] (PageCache, AICache, TileCache, TileRenderer)
  ui/     — 表示层 (MainWindow, PdfViewer, SplitWidget, BlockOverlay)
  data/   — 数据层 (ConfigManager, ChromaRepo)
  main.py — 入口 + build_services (懒加载编排)
```

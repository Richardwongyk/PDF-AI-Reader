# PDF 阅读器渲染与裂缝翻译框重构计划

## 结论

当前要根治“慢、卡、不清楚”，需要重构 PDF viewer 的渲染与布局边界；这会影响翻译框相关代码的实现位置，但不应改变用户看到的“页面在段落下边界裂开并插入翻译框”的产品形态。

裂缝插入不是可顺手删除的 UI 效果，而是核心交互合同。重构的目标是把它从当前混在页面渲染生命周期里的特殊分支，提升为稳定的 SplitLayer 状态层：页面怎么缓存、瓦片怎么替换、缩放怎么清晰化，都不能让裂缝翻译框丢失、错位或变成普通侧边栏。

## 当前问题

`src/ui/pdf_viewer.py` 现在同时承担这些职责：

- 视口滚动、页码定位和虚拟布局。
- 页面 widget 创建、复用、回收和 overlay 生命周期。
- 全页 pixmap 渲染、瓦片缓存、瓦片预取和缩放重渲染。
- 段落点击、裂缝插入、翻译框显示/隐藏和问答入口。
- 渲染完成回调、后台 tile 回调和缩放 debounce。

### 2026-05-31 当前实现架构快照

上一轮 bug fix 前后重新梳理了一遍当前 `PdfViewer` 架构。它还不是本文目标里的
`ViewportDocumentLayout + PageSurface + RenderScheduler + SplitLayer` 分层，而是一个过渡中的
混合实现：

- `_VirtualPageLayout` 是纯 Python 布局模型，保存每页基础高度、split 额外高度、start/end offset，
  并用二分定位回答 `page_at_y()` 和 `page_range_for_viewport()`。它决定滚动条总高度和可见页范围，
  但不拥有任何页面图像或 split widget 状态。
- QWidget layout 仍是实际挂载层。顶部/底部 spacer 撑出整篇文档高度，视口附近页面才插入真实
  widget；`_active_pages` 表示当前挂载/活动页，`_widget_pool` 和 `_page_containers` 复用普通页
  `_LazyPageWidget`。
- 普通页走 `_LazyPageWidget`。它可以画整页 pixmap，也可以在大页面路径下消费 tile cache；
  `_precise_render_pending` 和 `_request_precise_renders()` 负责缩放后的普通页清晰重渲染。
- 裂缝页没有独立 SplitLayer。当前状态分散在 `_page_segments[page_num]`、`_splits[split_id]`、
  `_split_pages`、`_pending_split_rerenders` 和 QWidget layout 中。一个裂缝页通常由上段 PDF widget、
  中间 `SplitWidget`、下段 PDF widget 三类 segment 共同组成。
- `_hide_split_page_from_layout()` 会把离屏裂缝页的 segment/split widget 从 QWidget layout 移除并隐藏，
  但保留 `_page_segments`、`_splits` 和 `_split_pages`，以便滚回视口时恢复。
- `_show_split_page_in_layout()` 如果发现该页在 `_pending_split_rerenders` 中，会先用
  `_fallback_pixmap_for_page()` 重建 segment，再把 segment 和 split widget 插回 layout。
- 当前“裂缝页是否可见/应优先渲染”的可靠判断不是 `page_num in _split_pages`，而是至少一个对应
  segment/split widget 仍在 QWidget layout 中。

这就是上一轮 bug 的架构背景：split 状态已经持久保存，但 split 渲染优先级仍混在缩放流程里，
如果只按 `_split_pages` 遍历，会把离屏裂缝页和当前可见裂缝页同等对待。

### 2026-05-31 上一轮 bug fix 定位

已修 bug：缩放时离屏裂缝页也会参与即时段内缩放和异步清晰重渲染，抢占当前 layout 中可见裂缝页
的渲染队列。长文档中打开多个裂缝翻译框后，这会放大缩放卡顿和可见页清晰化延迟。

修复方式：

- 新增 `_is_split_page_in_layout(page_num)`，遍历该页 segments 和 split widget，只有 widget 仍挂在
  QWidget layout 中时才认为该裂缝页当前可见/应优先处理。
- `_set_zoom()` 遍历 `_split_pages` 时，所有裂缝页仍加入 `_pending_split_rerenders`，保证状态不会丢；
  但只有 `_is_split_page_in_layout()` 为真的页会做即时 QLabel 缩放和立刻请求
  `request_page_render_async()`。
- 离屏裂缝页保持 stale pending，等 `_show_split_page_in_layout()` 恢复进 layout 时再用 fallback
  pixmap 重建并清除 pending。
- 裂缝段旧图即时缩放从 `SmoothTransformation` 改成 `FastTransformation`，连续缩放时降低 UI 线程成本。
- 缩放后恢复滚动位置的延迟回调改走 `_set_scroll_value_if_valid()`，防止 viewer 或 scrollbar 已销毁时
  callback 访问无效 Qt 对象。

新增回归测试：

- `tests/test_pdf_viewer_navigation.py::test_pdf_viewer_zoom_prioritizes_visible_split_pages` 构造一个当前
  layout 中的裂缝页和一个离屏裂缝页，验证缩放时只请求可见页渲染，同时两个页都保留 pending stale。

该修复只解决“离屏 split 抢当前页重渲染”这个确定 bug，不是大页面 tile-only 首帧黑底的完整止血。

这些状态互相穿插，导致几个直接后果：

- 大页进入 tile-only 路径时，首帧依赖 TileCache 命中；快速滚动、跳页或极大缩放下，tile miss 会让用户看到黑底/空白页。
- 全页 pixmap、低清 fallback、精确 pixmap 和 tile cache 没有统一的“可见层”合同，代码容易把“已调度渲染”误当作“已有可见内容”。
- 裂缝页面和普通页面走不同渲染分支，缩放后需要额外重建段 widget，容易出现翻译层丢失、错位或滚动定位不稳定。
- `PdfViewer` 太大，任何性能补丁都容易碰到翻译框、overlay、滚动定位或缓存回收。

## 开源项目可借鉴的点

本项目代码和已有调研已经吸收了几个成熟阅读器的方向，但目前只借了局部技巧，还没有形成稳定边界：

- qpageview 的核心价值是 renderer + cache + paint 的分层：页面绘制由 painter 消费缓存图像，页面不应拆成大量子控件。
- SumatraPDF 的核心价值是文档布局状态独立于控件树：滚动位置、页面矩形、缩放锚点和可见页计算应是纯模型结果。
- Sioyek 的核心价值是按阅读方向和视口趋势预取：预渲染应服务下一步可见内容，而不是盲目全量压队列。
- PDF.js / Okular 类阅读器的通用经验是渲染队列可取消、结果带版本号，过期渲染不能覆盖当前缩放或当前页面状态。

根治方案不是继续加更多 if，而是把这些原则落成项目内的明确模块边界。

## 核心不变量

后续所有实现必须守住这些不变量：

- 视口内页面不能空白。tile 未命中时必须先显示旧整页 pixmap、低清整页 fallback 或最近 snapshot。
- tile 只负责渐进清晰化，不能成为首帧唯一内容来源。
- 裂缝翻译框是阅读器主交互，不得迁移成侧边栏替代品。
- 裂缝状态必须按 `document_id + page_num + block_id + split_id` 持久跟踪，不能依赖当前 widget 是否还在控件树里。
- 缩放、滚动、跳页、页面回收、后台渲染完成都不能清掉用户已经打开的翻译框。
- OCR、公式索引、GraphRAG、云端校对不得进入打开、滚动、缩放和翻译热路径。

## 目标架构

### 1. ViewportDocumentLayout

把页面布局从 QWidget 树中抽出来，形成纯状态模型：

- 输入：页尺寸、缩放、页间距、打开的 split 状态、视口位置。
- 输出：每页逻辑 rect、每个 split row 的 rect、当前可见页范围、预取页范围。
- 责任：滚动锚点、缩放锚点、跳页定位、split 插入后的总高度计算。

验收点：给定同一组页面尺寸和 split 状态，布局结果确定；不需要真实 QPixmap 或后台线程即可单测。

### 2. PageSurface / PageImageStore

把页面图像分为三层：

- `base_fallback`：旧图、低清图或 snapshot，保证首帧可见。
- `precise_full`：当前缩放下的整页精确图，小页直接使用。
- `tiles`：大页局部高清图，只覆盖 fallback 中已完成的区域。

PageSurface 只回答“现在能画什么”，不负责启动重任务。PageImageStore 负责 LRU、DPI 版本、页面 hash 和内存预算。

验收点：任何可见页在 `tiles` 全 miss 时仍能画出 `base_fallback`；没有 fallback 时只允许显示浅灰占位，并且该状态不能被标记为 rendered。

### 3. RenderScheduler

统一管理全页和瓦片渲染：

- 每个请求带 `document_id`、`page_num`、`dpi`、`scale_generation` 和 `request_id`。
- 旧缩放、旧文档、旧页面状态的结果必须丢弃。
- 可见页优先，阅读方向预取次之，远离视口的请求可取消。
- 大页先请求低清 fallback，再请求可见 tiles，最后按空闲预算补齐周边 tiles。

验收点：快速滚动和连续缩放时，队列长度受控，过期结果不会覆盖当前画面。

### 4. SplitLayer

把裂缝翻译框从页面渲染分支里拆出来：

- SplitLayer 持有打开的翻译框、加载状态、错误状态、问答状态和对应 block。
- SplitLayer 参与布局高度计算，但不参与 PDF bitmap 渲染。
- 普通页面、被裂开的上半页、翻译框、下半页只是同一布局模型里的连续 visual items。
- 缩放时只重算 item rect；翻译框本身不因为页面 pixmap 重渲染而销毁。

验收点：双击段落打开翻译框后，连续缩放、跳页再跳回、快速滚动回收再显示，都能恢复同一个翻译框状态。

### 5. OverlayLayer

BlockOverlay、翻译标记、选择/高亮只依赖布局模型和 block bbox：

- Overlay 不保存页面渲染结果。
- 页面图像替换时 overlay 只重定位，不重建业务状态。
- overlay 点击只派发 block id，打开 split 的动作交给 SplitLayer。

验收点：精确 pixmap 或 tile 到达时不会造成 overlay 闪烁、丢失或重复连接信号。

## 预计性能收益

这些是工程估算，必须用 Attention/Napkin E2E 和日志指标验证，不能作为已达成结论。

| 项目 | 预计收益 | 原因 |
| --- | --- | --- |
| 极大缩放快速滚动首帧 | 空白/黑屏率降到 0 | 首帧不再依赖 tile cache 命中 |
| 滚动更新 P95 | 约 30%-60% 降低 | 可见页计算和 widget churn 减少，远页请求可取消 |
| 连续缩放即时反馈 | 保持在一帧到两帧内显示旧图 | 旧 pixmap/snapshot 直接作为 fallback 层 |
| 精确清晰化耗时 | 不一定变短，但感知卡顿显著降低 | 高清 tile 异步覆盖，不阻塞页面可见 |
| 内存峰值 | 约 30%-50% 降低 | PageImageStore 统一管理全页图和 tiles，按代际 LRU 回收 |
| 翻译框稳定性 | 明显提升 | split 状态脱离页面 pixmap 生命周期 |
| 代码维护成本 | 明显下降 | 渲染、布局、split、overlay 分层后，补丁不再互相踩 |

最重要的收益不是某个单点 benchmark 数字，而是交互稳定性：用户在滚动、跳页、缩放和打开翻译框时始终有可见页面，并且翻译框不因渲染策略变化而裂开错位。

## 分阶段实施

### Phase 0：P0 小补丁

目标：先止血，不做大重构。

- 大页面进入瓦片路径前，先尝试旧整页 pixmap 或低清整页 fallback。
- `_paint_tiles` 先画 fallback，再用已命中的 tiles 覆盖。
- fallback 不存在时，不得把页面标记为 rendered。
- 增加单元测试覆盖低清 fallback 仍进入 tile 覆盖路径，以及 tile miss 不清空整页 fallback。

### Phase 1：加合同测试

目标：先把核心交互固定下来，再移动代码。

- 测试 split 打开后缩放不丢。
- 测试 split 打开后页面回收再进入视口不丢。
- 测试 tile miss 时仍能看到 fallback。
- 测试跳页和快速滚动不会把旧缩放结果覆盖到新缩放页面。

### Phase 2：抽出布局模型

目标：让滚动、缩放、split 高度计算脱离 QWidget 树。

- 新增 `ViewportDocumentLayout`。
- `PdfViewer` 先双写当前布局结果和新模型结果，日志比对。
- 比对稳定后，让 `_update_visible_pages` 使用新模型。

### Phase 3：抽出 PageSurface 和 RenderScheduler

目标：统一全页图、低清图、tiles 和过期请求。

- 新增 `PageSurface` 数据对象。
- 新增 `PageImageStore` 管理图像版本和内存预算。
- RenderScheduler 统一发起 fallback/full/tile 请求。
- 删除散落在 `PdfViewer` 里的 `_rendered_pages` 与 tile/full 状态交叉判断。

### Phase 4：抽出 SplitLayer

目标：保住翻译框核心设计，同时让它不再绑死页面 pixmap 生命周期。

- Split 状态由 block id 和 split id 管理。
- 页面视觉 item 和 split item 统一进入布局模型。
- 翻译框 widget 只绑定 SplitLayer 状态，不绑定页面 segment widget。
- 缩放后只更新 split 所在 y 坐标和页面段 rect。

### Phase 5：清理旧路径

目标：删掉双路径，降低后续 bug 率。

- 删除裂缝页专用的临时 pixmap 重建路径。
- 删除旧的“大量页面子 widget 即状态”的逻辑。
- 保留清晰的日志指标和 E2E 门禁。

## 验收门槛

最低必须通过：

- `tests/test_tile_cache_performance.py`
- `tests/test_pdf_viewer_navigation.py`
- split/translation 相关新增回归测试
- `tools/e2e_pdf_workflow.py` 覆盖 Attention 与 Napkin 的滚动、跳页、缩放、双击翻译、隐藏/再打开、裂缝问答和日志审计

新增日志指标：

- `blank_visible_frame_count`
- `tile_miss_with_fallback_count`
- `fallback_render_ms`
- `visible_update_p95_ms`
- `render_queue_pending`
- `stale_render_dropped_count`
- `split_restore_ms`

Napkin 验收必须包含：

- 极大缩放约 5x 后快速滚动。
- 快速页码跳转到远页。
- 连续缩放后立即双击段落打开翻译框。
- 打开翻译框后滚出视口再滚回。
- tile cache 大量 eviction 时页面仍可见。

## 不做的事

- 不把翻译框改成侧边栏来规避裂缝布局问题。
- 不让 OCR/MFR、公式索引、GraphRAG 或云端模型进入阅读热路径。
- 不用更高 stress multiplier 掩盖 P0 可见性问题。
- 不在没有合同测试的情况下继续拆 `PdfViewer` 大块逻辑。

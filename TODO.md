# PDF AI Reader — 全版本演化史 · 当前状态 · 重构路线图

> 基于 git 日志 93 次提交 + 12 个新增文件 + 5 个开源项目深度调研
> 最后更新：2026-05-31 (补记 PDF viewer 缩放裂缝页重渲染与右侧全文问答体验修复)

---

## 2026-05-24 新终端交接入口（必读）

> 新会话接手时先读根目录 `AGENTS.md`，再读 `docs/next_session_handoff.md`。
> 当前阶段的关键任务不是继续盲目安装工具，而是按已写入文档的设计边界推进：
> born-digital PDF 公式先走 PDF 结构解析，扫描/图片公式才走 OCR/MFR；所有重任务异步分批、结果落库、可恢复；RAG/GraphRAG 必须基于全文证据；测试必须覆盖 Attention 与 Napkin 两份真实资料。
> 预计超过 1 分钟的训练集、LaTeX 编译、Napkin 全量、OCR/MFR、外部工具 benchmark 必须后台运行并写日志；前台继续写代码、设计、文档或审计，禁止同步干等长脚本。

当前真实状态：

1. 主程序环境仍为 `C:\Users\WYK\.conda\envs\pdf_ai_reader_314`。
2. 2026-05-24 已按隔离 worker 思路建立工具环境：`pdf_tool_paddle310`、`pdf_tool_mineru310`、`pdf_tool_pix2text310`、`pdf_tool_magic310`、`pdf_tool_pek310`。这些不是主程序环境，不能混装进 `pdf_ai_reader_314`。
3. 不要动用户已有环境，例如 `base`、`cs231n`、`drawing`、`science`、`pdf_ai_reader`、`pdf_ai_reader_314` 等。
4. 防休眠脚本在仓库 `tools/keep_awake.ps1` 和 `tools/keep_awake_watchdog.ps1`，新会话必须先检查是否仍在运行。
5. 外部工具（MinerU、Pix2Text、UniMERNet、PDF-Extract-Kit、PaddleOCR、magic-pdf）要按独立 worker 环境矩阵验证，不得混装到主环境。
6. 所有提交不得带额外署名、来源标记或生成工具署名；不要提交 `测试资料/`、日志、缓存、临时 benchmark 输出。

## 2026-05-31 上一轮未提交 bug fix 补记：缩放时离屏裂缝页不再抢占可见页重渲染

上一轮 Codex 会话留下的未提交改动范围很小，只涉及 `src/ui/pdf_viewer.py` 和
`tests/test_pdf_viewer_navigation.py`。这轮补记时已重新梳理当前 viewer 架构，详见
`docs/pdf_viewer_rendering_refactor_plan.md` 的“2026-05-31 当前实现架构快照”和
“上一轮 bug fix 定位”。

当前 `PdfViewer` 仍是混合架构：`_VirtualPageLayout` 负责页面 y 坐标和 split 额外高度，
QWidget layout + 顶底 spacer 负责实际挂载；普通页走 `_LazyPageWidget` 池和瓦片/整页渲染，
裂缝页走 `_page_segments`、`_splits`、`_split_pages` 和 `_pending_split_rerenders`。
因此“裂缝页是否可见”不能只看它是否在 `_split_pages` 中，而要看对应 segment/split widget
是否仍挂在当前 layout。

实际修复点：

1. 新增 `_is_split_page_in_layout(page_num)`，用 QWidget layout membership 判断裂缝页是否当前挂载。
2. `_set_zoom()` 缩放时只对当前 layout 中的裂缝页做即时段内 QLabel 缩放和优先异步清晰重渲染；
   离屏裂缝页只保留 `_pending_split_rerenders` stale 标记，避免抢占当前可见页渲染队列。
3. 裂缝页旧图即时缩放从 `SmoothTransformation` 改为 `FastTransformation`，降低连续缩放期间 UI 线程成本。
4. 新增 `_request_split_rerenders(page_nums)`，让缩放流程可以只请求可见裂缝页；原 pending timer
   也只消费重新进入 layout 的裂缝页。
5. 缩放后恢复视口位置的延迟回调改走 `_set_scroll_value_if_valid()`，避免 Qt 对象销毁后访问无效 scroll bar。

新增回归测试：

- `test_pdf_viewer_zoom_prioritizes_visible_split_pages` 构造一个可见裂缝页和一个离屏裂缝页，
  验证缩放时只请求可见页重渲染，同时两页仍都保留 pending stale 状态，后续回到视口时可恢复。

边界说明：

- 这是针对“缩放时离屏裂缝页抢占可见裂缝页重渲染”的确定 bug fix。
- 它不是 Napkin 极大缩放大页面 tile-only 首帧黑底/空白 P0 的完整修复；P0 仍要求大页面滚动时
  先显示旧整页 pixmap、低清 fallback 或最近 snapshot，再由 tile 渐进清晰化。

## 2026-05-31 右侧全文问答体验修复补记

本轮只收窄修复右侧全文问答链路的确定问题，没有改动公式识别、多轮公式索引、知识库构建或翻译流程。

已完成：

1. 云端回答异常时，问答线程会把 DeepSeek/LiteLLM 连接失败整理成可读提示，不再只把底层 traceback 暴露给用户。
2. `QAService` 在有检索证据时仍优先基于 PDF 证据回答；当检索结果不足时，允许大模型结合全文上下文和背景知识补充说明，但需要区分“文档中明确支持”和“基于背景知识的推断”。
3. 右侧全文问答回答区从 Qt `QTextBrowser` 切到现有 `markdown_template.html` + KaTeX WebView 渲染链路，使 `\(...\)`、`\[...\]` 和 `$$...$$` 公式能和裂缝问答/翻译保持一致渲染。
4. 右侧回答 WebView 会根据右侧面板实际 palette 设置成高对比阅读块：深背景白字、浅背景黑字，并同步链接和引用颜色；避免深色面板里出现黑字不可读。
5. “正在检索全文知识库...”只作为临时占位渲染，不再写入 `_dock_answer_text`；首个回答 token 到达后会被真实回答替换。

验证：

- `C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_smoke.py::test_main_window_smoke -q` 通过。
- 手动 WebView DOM smoke 显示右侧回答区公式渲染出 KaTeX 节点；颜色 smoke 读到深底白字。

边界：

- “有些符号显示不出来”尚未处理；这可能属于 KaTeX 支持范围、模型输出 LaTeX 质量或公式识别质量问题，不应和文字颜色修复混在一起。
- 未提交 `测试资料/`、日志、缓存或临时测试产物。

## 2026-05-28 今日性能修复复盘：相对最初基线改了什么、优化了什么、搞坏了什么

> 本节是今天前台 400x/Napkin 压测中断后的审计记录。这里的“最开始”指当前工作树相对
> HEAD 的未提交差异开始前，即今天动性能、渲染、测试压力和翻译公式保护之前的代码基线。
> 这些结论必须先于继续优化阅读器性能执行；不能继续用“性能优化”掩盖用户可见体验退化。

### 当前停工红线

- 用户已明确要求：“不要再工作了，除非你非常确定能改掉一个 bug”。
- 因此后续新会话默认停止主动开发，不再继续大规模重构、压测、算法优化、工具安装或原计划推进。
- 只有在已经读完交接文档、能把问题限定为单一具体 bug、修复路径非常确定、改动范围小且有针对性
  回归测试可以证明时，才允许继续动代码。
- 如果达不到上述条件，只允许汇报当前状态、说明已知 P0/P1 风险，并等待用户明确新指令。

### 0. 当前工作树范围

- 当前未提交 diff 涉及 40 个已跟踪文件，约 2132 行新增、487 行删除；另有
  `tests/test_e2e_pdf_workflow_stress.py`、`tests/test_pdf_viewer_navigation.py`、
  `tests/test_tile_cache_performance.py`、`tools/offscreen_ui_stress.py` 等新增文件。
- 未跟踪的 `测试资料/` 仍不能提交；`logs/`、`test_artifacts/`、缓存和临时 benchmark
  也不能提交。
- 防休眠进程仍在：`tools/keep_awake_watchdog.ps1` 和 `tools/keep_awake.ps1`。没有发现
  `src/main.py`、`tools/e2e_pdf_workflow.py`、pytest 或 offscreen stress 残留进程。

### 1. 今天做出的主要代码改动

1. `src/core/pdf_engine.py`
   - 将原 `_ParseThread` 从“两阶段解析 + 后台 MFD”改成“首屏前 8 页解析后立即 emit
     `parse_finished`”，再由新的 `_BackgroundParseThread` 延迟解析剩余页面和 MFD。
   - 新增 `_background_thread` 生命周期管理，打开新文档、关闭文档和 shutdown 时都会尝试停止后台解析。
   - 目的：降低 Napkin 冷启动等待，让窗口和首屏更快出现。
   - 风险：解析完成语义被拆成 `parse_finished` 与 `parse_completed` 两阶段后，任何假设
     `parse_finished` 已有全文 blocks 的 UI/知识库/公式任务都可能只看到前 8 页；需要逐项确认
     后台 page_blocks_ready、知识库构建和公式入队是否一致。

2. `src/ui/pdf_viewer.py`
   - `_VirtualPageLayout` 加入 start/end offset 数组和二分定位，替换原线性扫描。
   - 缩放流程改成“先更新布局/容器尺寸，旧 pixmap 在 paintEvent 中拉伸过渡，再延迟请求清晰渲染”，避免缩放时同步生成大 scaled pixmap。
   - 大页面阈值下改走 tile rendering，避免极大 pixmap 直接驻留和主线程切片开销。
   - 滚动时 `_update_visible_pages()` 立即执行，并扩大 active/needed 页面窗口，试图减少纯 spacer 曝露。
   - overlay 改成复用而不是每次全删全建；裂缝页面段内 overlay 也改为刷新几何。
   - 新增 `scroll_to_bbox()`、短暂 evidence highlight 和滚动范围同步，用于公式审核证据定位。
   - 目的：降低缩放和滚动时 UI 线程工作量，支持 page/bbox 定位。
   - 风险：大页面“仅瓦片渲染”路径没有保留最近可见整页/低清 fallback；tile cache 未命中时
     paint 只能画黑底或占位，直接破坏用户对滚动位置的感知。

3. `src/ui/split_widget.py`
   - WebViewPool 改为预热 `about:blank`，`acquire()` 不再立刻清空旧 HTML；新增
     `load_template()`，桥接对象先注册再载入模板。
   - 预热间隔从 50ms 改到 5000ms，避免频繁创建 Chromium 实例；新增 `clear()` 测试辅助。
   - 目的：减少裂缝翻译框创建/回收时的 QWebEngine 冷启动和 IPC 不稳定。
   - 风险：如果模板 reload/loadFinished 顺序异常，翻译框可能暂时无内容或恢复缓存不及时；仍需前台验证。

4. `src/ui/main_window.py`
   - 导入后公式任务持久化从 UI 线程挪到 `_FormulaImportPlanThread`。
   - 基础审核对话框接入 `evidence_location_requested`，可跳转 PDF page/bbox。
   - 目的：避免导入时大量公式任务落库阻塞首屏；补齐审核证据定位。
   - 风险：`FormulaIndexStore` 虽有 `check_same_thread=False` 和 lock，但同一个 store 跨 UI/worker
     线程写入仍需重点测试 SQLite 竞争、关闭文档时线程中断和 stale doc_hash。

5. `src/app/formula_acceptance_review.py`、`src/ui/formula_acceptance_dialog.py`
   - recognition result / fusion record 可导出 evidence payload，基础 UI 可预览 JSON。
   - 可从 evidence 中读取 `page_num + bbox` 并请求 PDF 定位。
   - 这是正向进展：更接近“每个 accepted 都可追溯到证据”的要求。

6. `src/app/formula_index_flow.py`
   - 调整 block queue 和 page queue drain 顺序，page scan 完成后可继续 block batch，block batch
     完成后也能继续 page queue。
   - 目的：避免一种队列 drain 完后另一种队列饿死。

7. `src/app/test_command_bridge.py`、`tools/e2e_pdf_workflow.py`、`tools/full_software_validation.py`
   - 测试命令桥增加 command_id、partial JSONL 处理、公式扫描状态事件和公式扫描命令。
   - E2E 增加 `--stress-multiplier`，覆盖大滚轮、连续快速滚动、极大缩放下滚动、缩放状态跳页、翻译框保持打开时滚动/缩放、公式扫描压力。
   - 修正 400x 早期机械重复同几页的问题：`_stress_pages()` 改为覆盖型抽样并交错首尾、极端页和中点页，避免在两三页之间反复跳几百次。
   - `full_software_validation.py` 将 stress multiplier 传播到页数、r2/r3/r4/r5 limit 和桌面 E2E。
   - 目的：把用户指出的“滚动、翻页、缩放、翻译、扫描识别压力不够”变成可自动化的覆盖。
   - 风险：压测强度扩大本身不是体验优化；如果渲染 fallback 错了，压测会放大用户可见黑屏。

8. `src/core/pdf_engine.py::TextPreprocessor`
   - 今日 diff 中将 `$...$` 恢复格式从原来的 `$...$` 改成 `\(...\)`，并对 display/inline
     公式内容 `.strip()`。
   - 新增测试强调不再猜裸数学表达式，这是反硬编码方向正确；但“改变原始 delimiter/空白”可能导致翻译后公式和原文不完全一致。
   - 更严重的待确认风险：`TranslationService` 仍复用同一个 `TextPreprocessor` 实例，流式翻译结束后才恢复公式。并发多个翻译时，请求 A 的公式表可能被请求 B 覆盖，导致随机符号/公式错乱。这一项今天只是发现风险，尚未修复。

### 2. 今天确实产生的正向进展

- Napkin 首屏解析从“全量长文档解析后才可用”的方向改为首批页面快速返回；这是正确方向，但需要补全后台解析完成后的知识库/公式任务一致性测试。
- 虚拟布局从 O(n) 页面扫描改成二分定位，理论上对 1050 页 Napkin 的滚动/跳页定位更稳定。
- 缩放时不再在 UI 线程同步生成大 scaled pixmap，理论上降低缩放卡顿峰值。
- overlay 复用减少大量 QWidget 创建/销毁，对频繁缩放和裂缝页重排有潜在收益。
- 公式审核 evidence 预览、PDF bbox 定位和 r5 accepted GraphRAG artifact 同步是明确的产品功能推进。
- 400x 压测脚本从“机械重复次数”改成“覆盖更多页、更多交互相位”的方向，这符合用户要求：压力不应只靠堆次数。
- 已跑过 `tests/test_e2e_pdf_workflow_stress.py -q`，结果 8 passed；这只证明压力分配函数和脚本结构，不证明 UI 体验合格。

### 3. 今天明确搞坏或暴露的问题

P0：极大缩放滚动黑底/空白页

- 前台 Napkin 400x 验证中，极大缩放约 5x 后跨页快速滚动/跳页，中间页会进入“大页面仅瓦片渲染”。
- 日志证据：`logs/app.log` 中本轮出现 `大页面 ... 仅瓦片渲染` 85 次、首次空瓦片绘制 19 次、
  `TileCache: EVICT` 24 次；`_paint_tiles: ... 缓存:0 裁剪:0` 后用户看到黑底/空白。
- 这是不可接受的用户体验退化。PDF 阅读器滚动时必须始终有可辨识页面内容，哪怕先模糊，不能让用户在黑底中猜位置。
- 根因判断：为了避免大 pixmap 和同步缩放，代码把极大页面切到 tile-only；但 tile cache 首次未命中时没有
  使用旧整页 pixmap、低分辨率 page pixmap 或上一次 rendered snapshot 作为 fallback。
- 修复原则：大页面/极大缩放路径必须采用“旧图/低清整页即时可见 + tile 渐进清晰化”，不能再画纯占位。

P0：不能以牺牲滚动体验换性能数字

- 当前优化把用户最关注的连续滚动定位感破坏了。哪怕 `_update_visible_pages`、缩放耗时或冷启动有下降，
  只要滚动中间页不可见，就不能算成功优化。
- 后续任何性能提交必须附带前台视觉验证：极大缩放、连续大滚轮、翻译框打开、缩放状态跳页都必须截图/日志双重通过。

P1：首屏解析拆分后的数据一致性风险

- 现在 `parse_finished` 可能只含前 8 页 blocks，后台解析再逐页补齐。
- 如果知识库构建、公式全篇入队、目录/块索引在 `parse_finished` 时就假设全文已完成，会造成只索引前几页、后续页翻译/问答/公式任务延迟或缺失。
- 已有 `page_blocks_ready` 路径，但必须全量检查：`_current_blocks`、`_blocks_by_id`、知识库重建、GraphRAG、公式 import plan 是否能在后台补页后增量更新。

P1：翻译公式保护仍有并发状态污染风险

- `TranslationService` 内部共享一个 `TextPreprocessor`，每次 `protect_formulas()` 都会清空 `_formula_store`。
- 流式模式下，A 请求 protect 后开始 streaming；B 请求如果先执行 protect，会覆盖 store；A 结束时 `_post_process()` 可能用 B 的 store 恢复公式。
- 这会造成随机公式错乱，和用户前面看到“随便点一个翻译都渲染错误”的现象一致。正确修复应是每个翻译请求使用独立 protection session/store，不能靠硬编码保护规则。
- 今天没有继续修，因为用户要求先停止并写复盘；后续主攻翻译时应先写并发复现测试。

P1：翻译公式格式被改写

- 当前 diff 将 `$M$` 恢复成 `\(M\)`，并去掉公式内部首尾空白。这满足“输出有定界符”的形式要求，
  但不满足“翻译保护应原样保留原公式”的更高要求。
- 后续应评估：翻译链路公式保护应保存 `match.group(0)` 原文，渲染层再负责 KaTeX 识别；不要在翻译预处理阶段改写用户原始 LaTeX。

P2：WebView 预热可能改善冷启动，但需确认没有内容恢复回退

- `WebViewPool` 从立即预热改成 5 秒后预热，减少资源抖动，但也可能让首次翻译仍冷启动。
- 需要测量第一次双击翻译从请求到可见内容的耗时，以及折叠/展开后内容是否稳定恢复。

### 4. 今天新增/修改测试的意义和不足

- `tests/test_e2e_pdf_workflow_stress.py`：验证 stress multiplier 不再把 400x 变成同几页重复跳转，且滚动/缩放/翻译/公式扫描动作数有上界。它不是 UI 体验测试。
- `tests/test_pdf_viewer_navigation.py`：覆盖 bbox 跳转和滚动范围同步，是公式证据定位的回归测试。
- `tests/test_tile_cache_performance.py`：覆盖 tile cache/large page 行为，但还不足以证明“首次 tile miss 时页面仍可见”。
- `tools/offscreen_ui_stress.py`：可用于后台/offscreen 压力，但不能替代前台视觉测试；用户已经明确表示最终要看完整程序效果。
- 需要新增的关键测试：
  - 极大缩放 tile miss 时必须绘制 fallback pixmap，而不是黑底/空白。
  - 连续 20 次以上大幅滚轮时，中间页始终有页面内容或低清占位内容，不允许纯黑。
  - 翻译框保持打开时滚动/缩放/跳页，裂缝内容和页面段落不能错位或丢失。
  - 并发两个含不同公式的翻译请求，最终各自公式恢复必须互不污染。

### 5. 下一步修复顺序

1. 先修 P0 渲染体验：大页/极大缩放时保留旧整页或低分辨率 fallback，tile 只负责逐步清晰化。
2. 再验证首屏解析拆分后的全文 blocks/知识库/公式任务增量一致性，防止只处理前 8 页。
3. 再处理翻译：每次翻译独立 formula protection session，公式恢复尽量原样保留，不引入手写样本规则。
4. 再重新跑 Napkin 前台 400x 视觉验证；只有滚动中间页始终可见，才能继续谈更强压力。
5. 最后再看性能指标，避免用“更快”掩盖“看不见”。

2026-05-26 标准资料补充：

- 已把重要公开标准/参考资料下载到本地 `.local_references/standards/`，包括 PDF 1.7、PDF Association ISO 32000-2 访问页、W3C MathML、XML entities、Unicode UTR #25/UAX #44/UCD/MathClass、Adobe AGL/AGLFN/zapfdingbats、texglyphlist、OpenType MATH/cmap/font file、LaTeX2e/amsmath/unicode-math。
- 清单、来源 URL、大小和 SHA-256 在本地 `standards_manifest.json`，仓库索引文档是 `docs/local_standards_cache_index.md`。
- `.local_references/` 已写入 `.git/info/exclude`，不提交外部标准全文或缓存。
- 后续 r0.5/TinyBDMath 实现必须基于这些标准和映射资料做资源版本/hash 审计；不能再凭记忆写固定规则表，也不能让小模型替代符号身份补丁层。

2026-05-26 TinyBDMath 全线跑通补充：

- 插桩训练集没有丢，也不是当前问题。可靠训练行来自 `test_artifacts/instrumented_attention_full` 和 `test_artifacts/instrumented_napkin_fast_delivery_v3`：Attention 135 条 verified exact、Napkin 29743 条 verified exact，合计 29878 条；Attention 另外 3 个 marker 没在编译 PDF 中找到，不能算入 verified 行。每行有 `raw_source_latex` 和 macro-expanded `label_latex`，源码仅用于训练/验收。
- 当前出 warning 的薄弱点是“把 canonical LaTeX 解析为 MathML/关系监督”的 KaTeX 层，主要来自 Napkin 的 alignment `&`、xy-pic/电路图和复杂宏形态；这不等同于训练集制备失败。后续应补 LaTeXML/更强 parser/TeX AST，而不是退回手写解析规则。
- 硬编码修正：decoder 不再在缺 `RADICAL_BODY` 时用右邻 `HORIZONTAL` 猜根号主体；relation labels 改为用 MathML `relation_hints` 监督 `SUP/SUB/FRACTION_BAR/OVERLINE/RADICAL_BODY`，不再直接靠 `\sqrt/\frac` 字符串提升关系标签。输出层只渲染模型已选关系，不能替模型决定结构归属。
- 全量模型链路已跑通：29878 graph rows -> 2037744 relation labels -> PyTorch edge model（有效样本 1965743）-> 1570380 relation scores -> 29878 structural candidates -> structural eval。全量 relation eval micro precision=0.963245、recall=0.189623、F1=0.316868；这是弱监督关系层和保守结构候选指标，不是最终公式准确率。
- 主线 r2a 已接入全量模型：Attention 前 4 页 `r2a_tinybdmath_structural:done=46`，写入 46 条 `tinybdmath_structural:tinybdmath`，accepted=0；同一 DB 复跑后 fusion 50 条全部 `already_done_same_input`，不重复派生 r2/r3/r5。低置信和错误候选仍只做 evidence。
- `tools/tinybdmath_score_relations.py --stream` 和 `tools/run_tinybdmath_relation_pipeline.ps1` 的流式打分已落地，避免更大训练集整批读入内存。

2026-05-27 全软件验证补充：

- 新增 `tools/full_software_validation.py`：整套软件总验收入口，统一跑 core/RAG/GraphRAG pytest、公式 pytest、TinyBDMath pytest、公式索引性能、LaTeX 源码审计、多轮公式流水线、二次打开跳过和日志审计。
- 新增 `tools/run_full_software_validation.ps1`：standard/full/nightly 长任务后台入口，输出 pid、stdout、stderr 和总报告路径；长验证时前台继续开发，不能同步干等。
- 新增 `tests/test_full_software_validation.py`：验证总计划覆盖 Attention/Napkin、RAG/GraphRAG、公式、多轮、TinyBDMath，以及显式开关下的桌面 E2E、云端和本地工具。
- quick 实跑已通过：`tools/full_software_validation.py --profile quick --case all --max-pages 2 --output-dir test_artifacts/full_software_validation_quick_live --fail-fast`，7 步骤约 65.663s，required failures=0。该结论只是小页全链路 smoke，不代表 99.9% 公式准确率达成。
- standard 后台首跑暴露并修复一个测试顺序断言，`pytest_formula_pipeline` 随后 191 passed。
- 总验收已强化为“退出码 + JSON 产物语义检查”：默认必须证明 born-digital 非 OCR、源码审计、r0/r0.5/TinyBDMath、多轮二次打开跳过和日志审计关键字段存在。
- 强化语义门禁后 quick 通过：`test_artifacts/full_software_validation_quick_semantic_v2`，7 步约 53.721s，required failures=0。
- 强化语义门禁后 standard 通过：`tools/full_software_validation.py --profile standard --case all --output-dir test_artifacts/full_software_validation_standard_semantic`，15 步约 119.678s，required failures=0。该目录是临时产物，不提交。
- 桌面 E2E：Attention 完整通过；Napkin 的 UI、长文档跳转、缩放、双击翻译、问答、日志链路完成，但公式质量门禁失败并返回 1，`common_source_command_recall 0.128 < 0.350`。这是当前未达 99.9% 公式质量的明确证据。

2026-05-28 公式审核与定位补充：

- `FormulaAcceptanceReviewService` 已暴露 recognition result / fusion record 的 evidence payload，
  基础审核对话框可预览 JSON 证据，并按已落库的 `page_num + bbox` 跳回 PDF 证据位置。
- `PdfViewer.scroll_to_bbox()` 已补齐 fresh/offscreen layout 下的滚动范围同步；此前证据高亮已创建但
  初始滚动条 maximum 可能仍为 0，导致定位不动。新增回归测试覆盖 bbox 定位和无效 bbox 拒绝。
- 手工 revision 仍只写入 `manual_revision/human_review` 候选并走同一 acceptance/r5 流程，
  不是自动修正规则；低置信候选不会写正文、FTS、向量库或 GraphRAG accepted。

2026-05-28 前台 Napkin 400x 交互验证新增问题：

- 极大缩放（约 5x）下跨页快速滚动/跳页时，中间页会进入“仅瓦片渲染”路径；首次 paint 可能出现
  `_paint_tiles: ... 缓存:0 裁剪:0`，用户看到黑底/空白/占位，而不是可辨识的旧页面内容。
- 日志证据：`logs/app.log` 中本轮出现 `大页面 ... 仅瓦片渲染` 85 次、首次空瓦片绘制 19 次、
  `TileCache: EVICT` 24 次，widget pool 一度到 250，`_update_visible_pages` 峰值 241.9ms。
- 这不是可接受的性能优化结果。后续修复方向：大页面/极大缩放滚动时必须保留并拉伸最近可用整页
  pixmap 或低分辨率 fallback，瓦片只做清晰化替换；不能让首次瓦片未命中时绘制纯占位。
- 压测脚本已改为覆盖型跳页，避免 400x 机械重复同一组页面；后续仍需在修复渲染后重跑前台验证。

多轮公式解析必须让下一个助手首先看到并遵守：

| 轮次 | 必须完成的事 | 不能做的事 |
| --- | --- | --- |
| r0 PDF 结构快扫 | 导入后全篇页面入队，抽取文本层/glyph/font/bbox/vector/图片候选并落库 | 不 OCR，不阻塞首屏 |
| r1 缓存优先识别 | 只处理 `needs_ocr=True`、图片/扫描/已有公式块，先查缓存再推理 | 不重复扫已完成输入 hash |
| r2 本地高精度 | 用 Pix2Text/PaddleOCR/UniMERNet/PDF-Extract-Kit/MinerU 等独立 worker 做低置信复核 | 不把工具混进主环境，不直接覆盖正文 |
| r3 云端语义复核 | DeepSeek 基于上下文和候选公式写 `suggested_latex/confidence/reason/risks` | 不无证据猜公式，不自动覆盖 |
| r4 GraphRAG | 异步写公式、章节、定理、引用、概念关系 | 不阻塞基础 RAG 和阅读 |
| r5 知识库增量更新（基础接线已落地） | accepted 高置信结果变化后按 hash 增量 upsert，并同步 accepted 公式 GraphRAG artifact | 不重建整篇、不重复 embedding；低置信候选不污染知识库 |

下一步优先级：

1. 读 `docs/next_session_handoff.md`，确认问题清单、失败教训、文件地图和交接提示词。
2. 用主环境跑轻量测试确认代码基线，再跑 Attention/Napkin 公式与性能审计。
3. 先把插桩训练集资产化：Attention 138 条、Napkin v3 29743 条 verified exact rows 转为 TinyBDMath graph training/eval rows，输出 schema、hash、split、类型统计和错误报告。
4. 训练并评测 TinyBDMath 非 OCR baseline：先 MLP edge/quality scorer + 解码/verifier，指标必须按 inline/display、上下标、分数线/根号/overline、align、数学字体分项统计；只有证明比 r0/fusion baseline 提升，才接入更高门禁。
5. 将新模型以 candidate-only 方式接入 r2a/fusion：所有结果带 input hash、model/version、preprocess_version、result JSON 和跳过机制；低置信继续只写候选，不覆盖正文/RAG。
6. 继续推进批量审核体验、accepted precision 统计、r4/r5 语义路径证据和 GraphRAG 产品体验；最终用 Attention/Napkin/E2E 证明准确率、性能和二次打开跳过。
7. 外部工具继续作为 r2b/兜底路线验证：MinerU 新模型、Paddle Formula、Pix2Text 已有 smoke；PEK/UniMERNet 和旧 magic-pdf 仍要补齐或明确淘汰，但不能替代 born-digital 非 OCR 主线。

2026-05-25 最新实现检查点：

- 训练集制作路线已从“PDF 与源码粗匹配”切到更可靠的 LaTeX 插桩/重编译路线：新增 `tools/tinybdmath_instrumented_latex_dataset.py`，在临时源码副本中给每个源码公式分配唯一 HTML 颜色，重编译后直接从 born-digital PDF 结构层读取彩色 glyph/vector bbox，输出 `source_formulas.jsonl`、`instrumented_formula_boxes.jsonl`、`instrumented_training_rows.jsonl` 和 `summary.json`。这条线只用于训练/审计，不进入真实用户生产解析路径；用户提供的原 PDF 只做可选指纹对照，不阻塞训练行生成。
- 新增 `tools/run_instrumented_dataset_background.ps1`，长编译/全量训练集必须后台运行、从启动即写日志并报告 pid/output/log；前台继续写代码或审计。`fast-no-asy` 构建档只修改临时源码副本，用于跳过 Napkin 的 Asymptote 图形构建，避免全量公式训练集被图片生成拖慢。
- Attention 插桩训练集全量回归已通过：138 个源码公式、5 个 display、133 个 inline，`boxes_found=138`、`verified_exact_boxes=138`、`training_rows=138`。新增 `--compile-mode pdflatex-once` 后耗时约 6.423s，原 PDF 与重编译 PDF 为 `same_page_geometry`。
- Napkin 插桩训练集已在 `test_artifacts/instrumented_napkin_fast_delivery_v3` 全量跑通：单轮 `pdflatex-once` + `fast-no-asy` 总耗时约 192.92s，源码公式 29743 条，其中 display 2151、inline 27592；`boxes_found=29743`、`verified_exact_boxes=29743`、`training_rows=29743`、`blockers={}`。前一版 833 个 `marker_color_not_found_in_pdf` 的根因已修复：Asymptote 源语言块内的 `label("$...$")`、`dot("$...$")` 等图形脚本字符串不属于当前快速 profile 的 LaTeX 正文公式训练分母；未注释 `\endinput` 后的源码不会被 TeX 读取；`align*` 等 alignment display 环境需要给每个 `&` 对齐单元独立染色，避免颜色落在空单元格。
- 第一阶段 PDF 解析器研发继续推进：`src/core/pdf_glyph_graph.py` 已把 PyMuPDF born-digital facts 标准化为 `RawGlyphGraph`，包含 glyph/font/bbox/origin/cid/glyph_name、vector/image 节点、line/span 读序边、PDF health、稳定 input hash；`src/core/symbol_identity_repair.py` 新增 r0.5 `EnrichedGlyphGraph`，对已知 PDF Unicode、标准 glyph name、同 normalized_font+cid 锚点做保守身份修复，输出 `resolved_identity`、候选、来源、置信度、warnings 和独立 input hash。`BornDigitalFormulaStructureExtractor` 的 r0 candidate evidence 现在同时携带局部 raw graph 和 enriched graph，`FormulaIndexFlow` 会把 enriched graph 作为 `r0_5_symbol_identity_repair` 独立 round record 落到 `formula_round_jobs`，同 input hash 二次打开复用已完成记录；`tools/formula_multiround_pipeline.py` 也会显式报告 r0.5 轮次。新增 `tests/test_symbol_identity_repair.py`，相关公式/图谱集合上一基线为 `188 passed`。
- r0.5 映射资源层已开始落地：`GlyphNameMappingLoader` 支持加载 AGL/texglyphlist 风格的 `glyph;0041` 或空白分隔映射文件，保留 mapping source/warnings，支持资源覆盖内置表、缺失资源 warning、`uniXXXX/uXXXX` encoded glyph name 解码。后续可接 TeX Live/CTAN 的真实 glyphlist/texglyphlist/encoding map，而不必把映射继续硬塞进代码常量。
- r0.5 资源自动发现已加入：可从 `resources/glyph_maps`、`data/glyph_maps`、`PDF_AI_READER_GLYPH_MAP_DIR`、`TEXMFROOT`、`TEXLIVE_ROOT`、`MIKTEX_ROOT` 和 Windows TeX Live/MiKTeX 常见路径发现 `texglyphlist.txt/glyphlist.txt/pdfglyphlist.txt`；找不到资源只记录 `mapping_auto_discovery_empty`，不联网、不安装、不影响内置 fallback。
- r0.5 计划已强化：后续必须按 PDF 原生 ToUnicode/ActualText、embedded font cmap/OpenType、AGL/texglyphlist/TeX encoding、文档内锚点传播、outline/path shape candidate、结构角色先验的顺序补丁化处理 unknown glyph；任何阶段都只写证据、候选和置信度，低置信不能 accepted，不能跳过补丁层直接 OCR 或让 TinyBDMath 猜符号。
- 真实数据集方向已开始：新增 `tools/born_digital_formula_dataset.py`，面向 Attention/Napkin 全量 PDF + LaTeX 源码生成 source formula index、PDF r0/r0.5 candidate index、TinyBDMath feature graph JSONL；源码记录包含 tex 文件、字符偏移、display/inline/env、上下文和 normalized tokens，PDF 记录包含 raw/enriched graph hash、r0 hash、feature graph hash、edge hint counts 和 best source match。源码只用于训练/验收数据，不进入生产解析路径。
- TinyBDMath 第一阶段主干已补齐：新增真实训练行生成、标准库一隐藏层 MLP、候选质量 scorer、数据审计、realdata pipeline、可选 PyTorch 后端和 `science` 环境启动脚本。新增 `src/app/tinybdmath_candidate_service.py` 作为 r2a 非视觉结构候选服务，从 r0/r0.5 evidence 生成 feature graph 并写 `tinybdmath_structural:tinybdmath` 候选及 `r2a_tinybdmath_structural` round record；结果固定 candidate-only，不覆盖正文/RAG。Attention 前 6 页 smoke 中 r2a 处理 7 条 r0 候选，fusion 能读取，`ready_for_manual_accept=0`。
- TinyBDMath 插桩 graph 数据集第一版已跑通：新增 `tools/tinybdmath_instrumented_graph_dataset.py`、`tools/run_tinybdmath_instrumented_graph_dataset.ps1`，将 Attention 138 + Napkin 29743 条 exact rows 转成 `tinybdmath_graph_rows.jsonl`、manifest 和 split；全量实跑 rows=29881、blockers={}、dataset hash `f49359d58f2b34b006028cfd106d6678e7999f116934fbbaebbf6b250c886ba0`。产物在 `test_artifacts/`，不提交。
- TinyBDMath graph baseline smoke 已跑通：新增 `src/core/tinybdmath_graph_baseline.py` 和 `tools/tinybdmath_train_graph_baseline.py`，从 graph rows 训练弱监督结构桶 softmax baseline；2000 行、3 epochs smoke 验证集 accuracy 约 0.796。该 baseline 只打通模型工件链路，不负责 LaTeX 生成、relation label 或 accepted。
- TinyBDMath relation labels + edge baseline smoke 已跑通：新增 `src/core/tinybdmath_relation_labels.py`、`tools/tinybdmath_build_relation_labels.py`、`src/core/tinybdmath_edge_baseline.py`、`tools/tinybdmath_train_edge_baseline.py`。全量 relation labels rows=29881、edge_labels=2035016、blockers={}；2000 行 edge baseline smoke 生成 121174 条 edge samples，验证 accuracy=1.0。该准确率只证明 weak labels 与候选 hint 高相关，不能当最终公式识别指标。
- TinyBDMath relation scorer + structural candidate MVP 已跑通：新增 `src/core/tinybdmath_relation_scorer.py`、`tools/tinybdmath_score_relations.py`、`src/core/tinybdmath_structural_candidate.py`、`tools/tinybdmath_decode_structural_candidates.py`。当前只输出 candidate-only 关系证据、横线歧义、verifier warnings 和 abstain，不输出 accepted LaTeX。
- TinyBDMath r2a 已接入 edge relation evidence：`TinyBDMathCandidateService` 支持 `edge_model_path`，`tools/formula_multiround_pipeline.py` 支持 `--tinybdmath-edge-model`。Attention 前 6 页 smoke 处理 7 条 r0 候选，`evidence_json` 写入 relation scores 和 structural candidate；当前仍未生成更准 LaTeX，不能当质量完成。
- TinyBDMath r2a 已接入 inline 证据：`process_inline_candidates` 使用 `inline_pdf_evidence` 的 PDF 字体/字号/bbox/span，Attention 前 2 页 smoke 处理 6 条 inline 候选并落库；小公式和数学字体不再只停留在 fusion/r3 prompt 层，而是进入同一套 relation evidence。低证据候选仍跳过或 abstain。
- TinyBDMath structural candidate 已有 SLT skeleton/verifier MVP：候选结果带结构节点、roots、isolated nodes、coverage、多父冲突、横线歧义和 accepted blocker；结构选择加入通用出入度约束，2000 行 smoke 中多父冲突从 1346 降到 78。仍不生成 accepted LaTeX，下一步接真正 SLT/MathML decoder。
- TinyBDMath structural eval 已跑通：新增 relation 级评测 CLI，2000 行 weak-label smoke micro precision=0.978121、recall=0.124314、F1=0.220591。当前结构候选很保守，适合候选证据，不是最终准确率。
- TinyBDMath relation pipeline 一键 smoke 已跑通：`tools/run_tinybdmath_relation_pipeline.ps1` 串起 graph rows -> labels -> train -> score -> structural -> eval，300 行 smoke micro precision=0.971557、recall=0.136273、F1=0.239020。
- TinyBDMath 成熟工具 MathML 审计入口已接入：新增 KaTeX-backed `latex_mathml_extractor` 和 CLI，relation labels 记录 MathML relation hints。该路径只用于训练/审计，不把源码带进生产推理。
- TinyBDMath 全链条 smoke 已跑通并做了性能修正：KaTeX MathML extraction 改为分块批量调用，relation label 构建可复用预计算 MathML rows，不再对每条公式重复解析。2000 行训练/审计链完成 MathML -> labels -> train -> score -> structural -> eval，总耗时约 48.758s，structural eval micro precision=0.978121、recall=0.124314、F1=0.220591；这仍是弱标签候选链路，不是最终公式识别准确率。
- TinyBDMath 生产 r2a 限流错误已修正：`r2_limit=0` 只表示跳过视觉/本地高精度 r2，不再截断非视觉 TinyBDMath r2a。Attention 前 6 页 born-digital 非 OCR 链路 r2a processed=115（7 条 structure + 108 条 inline）、r2a elapsed=1.254s、总 elapsed=12.521s；Napkin 8-16 页 r2a processed=78（2 条 structure + 76 条 inline）、r2a elapsed=0.586s、总 elapsed=10.432s。
- TinyBDMath 二次打开跳过已验证：Attention 前 6 页复用同 DB 时 r0 processed_pages=0、r2a processed=0、skipped_cached=115、总 elapsed=5.629s；Napkin 8-16 页 r0 processed_pages=0、r2a processed=0、skipped_cached=78、总 elapsed=5.703s。
- 当前质量门禁仍失败，不能宣称完成：Attention 前 6 页 TinyBDMath group near_match_rate=0.257、average_best_similarity=0.605；Napkin 8-16 页 near_match_rate=0.0、average_best_similarity=0.116。所有 TinyBDMath 输出仍 candidate-only，不能覆盖正文/RAG/GraphRAG。
- 29881 行全量 relation pipeline 已后台启动并完成 MathML + relation labels 阶段，coverage 包括 inline=27725、display=2156、math_alphabet=5772、subscript=7188、superscript=4331、fraction=765、radical=564、script_size_pdf_evidence=11247，blockers={}；仍需等待 train/score/structural/eval 完成。
- PyTorch edge relation 训练路径已接通：新增 `tools/tinybdmath_train_edge_torch.py`，必须在隔离 `science` 环境运行，导出主程序可读取的 JSON edge model；`tools/run_tinybdmath_relation_pipeline.ps1 -UseTorchEdge` 可切换到 PyTorch。2000 行 smoke 验证 accuracy=0.999424；500 行端到端 v2 structural eval precision=0.910751、recall=0.022944、F1=0.044761，仍低于当前保守 baseline，不能默认替换。下一步要做 class-weighted loss、两层 MLP/GNN、置信度校准和 decoder 优化。
- 真实数据集工程继续增强：新增确定性源码 math span 扫描器、LaTeX 项目宏展开器、页级分片/断点续跑构建器、分片合并器、gold/label tier 审计和一键流水线脚本。LaTeX source gold 只用于训练/验收；训练目标使用 canonical LaTeX，raw source macro 只作审计证据；PDF 候选标签加入 TOC 页锚点窗口，优先同页/近页匹配，短公式也受页窗口约束，避免全书范围误配。当前分片版本为 `tinybdmath_pdf_source_page_anchor_macro_v3`，旧分片不会被合并器误用。测试产物不提交。
- 后续新增 PDF+LaTeX 资料无需改代码：`tools/tinybdmath_sharded_dataset.py --case <name> --pdf <pdf> --latex-root <latex_root>` 走同一套通用流程；宏展开来自主 TeX 导言区依赖图，不写死 Attention/Napkin 的宏名。
- TinyBDMath 训练集准确性门禁已补强：新增统一 `tools/tinybdmath_gold_policy.py`，训练行、gold audit 和复核队列共享同一 verified gold 判定；自动 gold 必须同页窗口、源码页窗唯一、PDF glyph/edge 证据完整、无 unknown/warnings、严格 token 签名一致，过短公式不自动收。新增 `tools/tinybdmath_review_queue.py` 导出待复核 JSONL、PDF crop、源码上下文和视觉大模型审核 prompt；新增 `tools/tinybdmath_apply_review.py` 将自动 verified 与高置信 `accept/revise` 复核结果合成独立 verified gold JSONL。阶段合并 Attention 15 页 + Napkin 1050 页分片得到 2493 条 PDF candidates，在极严门禁下自动 verified gold 仅 12 条，其余必须复核，不能宣称全量一一对应已完成。
- 复核时必须特别处理项目自定义宏：例如 Napkin `\pre` 宏定义为 `^{\text{pre}}`，视觉相似不等于训练目标 LaTeX 精准一致；没有宏定义审计或 canonical LaTeX 修订时，不得接受为 gold。
- 本轮最新实现：r4 公式知识图谱服务已落地到 `src/app/formula_knowledge_graph.py`，UI idle 调度能在 r3 后继续消费 r4；candidate-only fusion 公式写入 GraphRAG artifact 时标记为 `formula_candidate`，不会伪装为 accepted 公式。
- `tools/formula_multiround_pipeline.py` 现在支持 `--drain-r2/--drain-r3/--drain-r4/--drain-r5`，并输出 `formula_fusion_snapshots`，能看清每次 fusion 写入、同 input hash 缓存命中、派生 r2/r3/r5 入队。
- fusion 门禁已加严：r2 本地 MFR 候选若比 born-digital 结构候选降质，会记录 `local_precise_degraded_against_born_digital`，决策保持 `needs_more_evidence`，不进入 `ready_for_manual_accept`、不触发 r5 accepted 写回。
- r3 云端路径真实 smoke 暴露并修复了两个问题：融合读取云端 confidence 缺少 `_optional_float`；DeepSeek 返回字符串 `risks` 时不能拆成逐字符风险。现在 r3 result JSON 保留 input hash、model/model_version，并规范化 risks。
- r3 prompt 继续增强：云端输入改为压缩后的 PDF/fusion/tool 证据包，inline 候选携带来源段落 `source_context`；系统提示要求只输出 JSON 对象。若 DeepSeek 返回非 JSON，任务会 failed 落库并记录 raw response 摘要，后续可按同 input hash 重试和审计。
- r3 队列优先级继续细化：fusion 派生任务会写入 `review_priority` 和 `review_priority_reason`，按证据价值、相似度缺口、冲突/风险和 LaTeX 复杂度优先消费；低价值单字符 inline 仅延后，不丢弃。r3 done 结果会保留原始 `review_candidate`、`queued_input_hash`、`review_input_hash` 和优先级原因。
- 行内公式证据通道继续细化：`DocumentChunker` 会把 math-font inline spans 的 PDF 字体、字号、bbox、span 列表和 `has_script_size` 写入 `inline_math_candidates`，fusion/r3 继续保留为 `inline_pdf_evidence`。这让云端/工具能基于 PDF 事实复核 `h_t`、`h_{t-1}`、数学字体等，不是手写 LaTeX 解析器，也不自动 accepted。
- 最新相关测试基线已提升为 `188 passed`：`tests/test_formula_multiround_pipeline.py tests/test_formula_knowledge_graph.py tests/test_formula_knowledge_update.py tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_graph_index_flow.py tests/test_graph_index_store.py tests/test_formula_tool_comparison.py tests/test_external_formula_tools.py tests/test_formula_detector.py tests/test_born_digital_math.py tests/test_pdf_glyph_graph.py tests/test_symbol_identity_repair.py tests/test_smoke.py -q`。
- 最新实跑：Attention 前 2 页默认非 OCR 多轮约 0.993s，r0=2、r3=9、r4=9；Attention 前 6 页默认非 OCR 多轮约 5.755s，r3/r4 各 122 个候选，`ready_for_manual_accept=0`；Attention 前 6 页 targeted r2 + drain 中 `local_precise:pix2text-mfr` 平均 similarity 约 0.578，低于 r0 约 0.668，fusion 记录 `local_precise_degraded=5`；DeepSeek r3 小样本约 35.777s 处理 2 条候选，只写 JSON；Napkin 前 8 页 r0 处理 8 页且无公式候选时 r1/r2/r3/r4 正确跳过。
- r3 证据包复测：Attention 前 2 页 mock drain 约 0.926s，r3/r4 各 9 条；真实 DeepSeek 限 1 条约 37.304s，遇到非 JSON 响应时 failed 落库，保留 raw response 摘要，正文和 accepted 结果不变。
- r3 优先队列复测：Attention 前 2 页 `--r3-limit 1` 不 drain 时，首条处理 `ht−1`，剩 8 条 queued；单字符 `t` 仍保留 queued 但被延后。SQLite 审计可看到 done 记录保留候选 LaTeX、queued/review hash 和 `review_priority_reason`。
- inline evidence 复测：Attention 前 2 页 `ht−1` 的 fusion evidence 包含 CMMI10/CMMI7/CMSY7/CMR7、字号范围 6.974 到 9.963、真实 bbox 和 `has_script_size=true`；后续 r3/工具可据此恢复上下标，但当前结果仍只是候选证据。
- 结论必须明确：r1/r3/r4/r5 的异步落库链路已经跑通，降质候选也不会污染正文/RAG；但是 99.9% 公式准确率和产品级 RAG/GraphRAG 未完成，下一步应集中提升 born-digital LaTeX 恢复质量、Napkin 大样本门禁、常驻 worker 性能、批量审核和路径证据。

- 此前实现：r0-r5 命令行流水线已完整显示 `r5_knowledge_incremental_update`；新增 `formula_fusion_records` 持久化表，fusion 记录包含 `fusion_version`、`input_hash`、best/ranked result ids、coverage、agreement、risk flags、decision 和完整 result JSON；同 input hash 二次运行跳过 fusion 派生 r2/r3/r5 队列。
- 新增 `FormulaKnowledgeUpdateService`：消费 r5 round jobs，只有 accepted 结果变化后才把 `accepted_latex` 增量 upsert 到 `KnowledgeEngine`，知识库未就绪时保持 queued，不重建全文；UI idle 调度已接入 r5。
- r3 语义复核 prompt 已增强为读取同一 candidate 的 `formula_recognition_results` 和 `formula_fusion_records`，云端只写 `suggested_latex/confidence/reason/risks/raw_response` 候选，不覆盖正文。
- r2 外部工具统一 worker 已扩展：Paddle Formula、Pix2Text、MinerU 3.1.15 页级后端、PEK/UniMERNet 后端都能作为独立工具 spec；不可用或空输出也按工具身份写 failed/warning 候选，不能互相覆盖。当前 PEK 环境仍缺 `unimernet`，MinerU 能启动但单页/单候选耗时很高。
- 行内公式已纳入审计和多轮报告：段落中的 `\(...\)`/`$...$` 候选进入 `inline_spans:document_chunker` 指标和 fusion；纯脚注/装饰符号不再包成公式；纯 inline 候选默认不进入 OCR/MFR，只进入候选审计/r3 复核。
- 此前相关测试基线：`tests/test_formula_multiround_pipeline.py tests/test_formula_knowledge_update.py tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_graph_index_flow.py tests/test_graph_index_store.py tests/test_formula_tool_comparison.py tests/test_external_formula_tools.py tests/test_formula_detector.py tests/test_born_digital_math.py tests/test_smoke.py` 为 `157 passed`。
- Attention 前 6 页最新真实基线：默认 r0/r1/r3/r4/r5 pipeline 约 14s；fusion 34 个候选区域，`ready_for_manual_accept=0`，`needs_more_evidence=34`，`missing_or_insufficient_r2=6`，`inline_candidate_only_needs_review=10`；r2 队列只派发结构/display 候选，不默认 OCR inline。inline 审计从原先几乎 0 提升到 `pdf_inline_formula_snippets=115`、`inline_source_weak_match_rate=0.299`、`inline_source_unmatched_count=54`，仍远未达 99.9%。
- Attention 单公式多工具 targeted r2：`--auto-local-tools --run-targeted-r2-after-fusion --r2-limit 1` 跑通 Paddle/Pix2Text/Pix2Text-MFR，PEK 写 unavailable warning；MinerU 后端参与会显著拉长耗时，当前更适合离线页级对照。
- 已提交 `11fe4da`、`b3b1eaa`、`d0dc26e`、`9a02945`、`6cb0860`，之后继续完成候选融合、facts-only r0、行内公式指标和反硬编码测试。
- r0 当前默认只走 PDF 结构事实，不初始化 OCR/MFR，不默认调用自写 LaTeX 重建器；r2 只有低置信候选或显式 `--r2-sample-formulas` 精扫才通过独立 worker 调 Paddle/Pix2Text 等工具，结果只作为未接受候选。
- 新增 `tools/formula_multiround_pipeline.py`：可跑 r0-r5 状态、任务统计、识别结果统计、`--reuse-db` 跳过验证、显式 r2 多工具、可选 DeepSeek r3 smoke、r4 图谱和 r5 增量更新 smoke。
- 多轮流水线已接入源 LaTeX 准确率复核。源码只用于测试/验收，真实用户运行路径不能依赖源码；LaTeX 中 `$...$`、`\(...\)`、`\[...\]`、`$$...$$` 包裹内容都算公式。每个 stage/model 输出 exact/near/weak/average similarity、inline 指标和低相似候选；Attention 前 6 页 facts-only r0 平均约 0.668、near match 0.429、`inline_weak_match_rate=0.026`，显式 r2 单样本最佳约 0.854，有提升但远未达到 99.9% 最终目标。
- 新增/更新 `docs/formula_multitool_fusion_design.md`：明确多工具潜力要通过候选级 fusion、coverage-comparable 检查、accepted 门禁和 r5 增量写回挖掘；禁止手写硬编码公式解析规则；每次验收必须检查生产路径无样本词表/正则/手写修复链。
- 历史相关测试基线：`tests/test_formula_multiround_pipeline.py tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_graph_index_flow.py tests/test_graph_index_store.py tests/test_formula_tool_comparison.py tests/test_external_formula_tools.py tests/test_formula_detector.py tests/test_born_digital_math.py tests/test_smoke.py` 为 142 passed。
- Attention 前 6 页真实验证：facts-only 默认 r0 约 0.95s 写入 7 个 born-digital 结构公式候选，r1/r2 正确跳过；`--reuse-db` 二次 r0 `processed_pages=0`、`skipped_completed_pages=6`；fusion 合并为 7 个公式区域，0 个 ready，全部 `needs_more_evidence`；显式 r2 多工具单样本写入 `pix2text-mfr`、Paddle Formula、Pix2Text 三类候选但冷启动约 245s，复用后约 1.2s；真实 DeepSeek r3 单条约 60s。
- Attention 多轮入库最新基准：15 页总约 2.31s，持久化约 0.006s，入队 `r0_pdf_structure:15`、`r3_cloud_semantic_review:11`。
- 工具 smoke：MinerU 3.1.15 本地新模型 Attention 单页跑通；Paddle Formula/Pix2Text 单张公式图 worker 跑通但质量仍只能候选；magic-pdf 缺旧权重；PEK/UniMERNet 未跑通。

---

## 2026-05-23 交接记录：PRD 对齐审计与下一步

> 本节用于下一次打开新会话后继续工作。当前只记录问题和建议，未修改业务代码。
> 用户反馈：现有功能离需求文档较远；同时本机约束为 16G 内存 + CPU，Ollama 本地大模型过慢、过大，不适合作为默认生成模型。

### 结论

当前项目不是完全没做，而是很多 PRD/TDD 标为“已实现”的能力只完成了骨架或部分接线。下一步应先把产品路线从“本地大模型优先”调整为“本地解析/索引优先，生成模型默认云端可配置，UI 明确告知隐私边界”。否则 16G CPU 环境下用户体验会被 Ollama 拖垮。

### P0：必须优先修的问题

1. 模型路由语义错误
   - 现状：`src/main.py:143-154` 在存在云端 API Key 时把 `LiteLLMClient` 放进 `primary_client`，然后作为 `HybridModelRouter(local_client, cloud_client, config)` 的第一个参数传入。
   - 后果：`local_first` 名义上是本地优先，实际第一个客户端可能就是云端；也没有“每次云端调用显式授权”的机制。
   - 相关代码：`src/main.py:136-154`，`src/core/ai_engine.py:304-388`。
   - 建议：拆清楚 `local_client`、`cloud_client`、`mock_client` 三者；16G CPU 默认可设为 `cloud_only`，但 UI/状态栏必须明确“生成内容会调用云端 API”。

2. Ollama 不应再作为默认生成模型要求
   - 现状：`config.yaml` 和 `src/core/models.py` 默认仍是 `qwen3.5:4b`、`bge-m3`、`local_first`。
   - 现状：首次启动会强制检查 Ollama，并弹窗引导安装/下载 `qwen3.5:4b`。
   - 后果：与用户实际硬件冲突，启动体验和产品方向都不合理。
   - 相关代码：`src/core/models.py:127-137`，`src/ui/main_window.py:783-813`，`config.yaml:1-9`。
   - 建议：默认路线改为云端生成；首次启动改为检查云端 API 配置，不再要求 Ollama。Ollama 保留为高级/可选本地模式。

3. 嵌入兜底是 Mock 随机向量，知识库检索质量不可用
   - 现状：`src/main.py:100-114` 中 Ollama 嵌入不可用时回退 `MockLLMClient`。
   - 后果：Mock 向量由固定随机数生成，不具备语义；知识库“能构建”，但问答检索质量基本不可信。
   - 建议：实现轻量本地嵌入兜底，例如 `HashingEmbeddingClient`，使用确定性词袋/哈希向量，至少能按关键词相关性检索；或单独配置云端 embedding 模型。

4. 问答未严格基于文档上下文
   - 现状：当没有当前段落和检索结果时，`QAService` 会写入“无额外上下文，请根据你的知识回答”。
   - 后果：这会把论文助手降级成普通聊天，和 PRD 的“基于全文知识库”冲突。
   - 相关代码：`src/core/ai_engine.py:783-790`。
   - 建议：无上下文时返回“知识库未就绪/未检索到相关片段”，不要让模型自由发挥。

### P1：产品闭环缺口

1. 知识库状态反馈不完整
   - 现状：`DocumentFlow._on_parse_completed()` 会自动构建知识库，说明自动构建并非完全缺失。
   - 相关代码：`src/app/document_flow.py:112-124`。
   - 缺口：UI 只在 `_refresh_knowledge_status()` 显示“知识库构建中/就绪”，缺少构建中进度、完成后刷新、失败后的可操作提示。
   - 相关代码：`src/ui/main_window.py:413-421`。

2. 追问建议未形成完整接线
   - 现状：`QAService.generate_followup_questions()` 和 `SplitWidget.show_followup_questions()` 都存在。
   - 相关代码：`src/core/ai_engine.py:713-745`，`src/ui/split_widget.py:315-325`。
   - 缺口：主问答完成流程未看到调用并展示追问建议的闭环。

3. PRD 中多个可见 UI 功能仍是占位
   - 搜索框只是占位：`src/ui/main_window.py:196-201`。
   - 右侧 AI 工具集只是占位：`src/ui/main_window.py:237-244`。
   - 术语表编辑器只是提示“后续版本实现”：`src/ui/main_window.py:707-709`。

4. 定理/证明保护条件过窄
   - 现状：只有 `block_type == "heading"` 且包含 theorem/lemma/proof/definition 时才追加保护提示。
   - 后果：普通段落里的定理、证明不会触发保护。
   - 相关代码：`src/core/ai_engine.py:600-608`。

### 建议下一次新会话的执行顺序

1. 先改模型路线
   - 默认生成策略改为 `cloud_only` 或显式 `cloud_preferred`。
   - 保留 Ollama 作为高级选项，不在首次启动强制要求。
   - 修正 `HybridModelRouter` 构造参数，避免把云端客户端伪装成本地客户端。

2. 再改嵌入路线
   - 增加确定性的轻量本地 `HashingEmbeddingClient`，替代 Mock 随机向量作为无 Ollama 默认兜底。
   - 如果以后要更准，再加云端 embedding 配置项，和生成模型分开。

3. 再修问答边界
   - 知识库未就绪时，裂缝问答直接提示“知识库构建中，请稍后再问”。
   - 检索为空时，要求模型说明“文档中未找到依据”，不要允许无上下文泛答。

4. 最后补 UI 闭环
   - 知识库状态进度和失败重试。
   - 追问建议显示。
   - 搜索框实际搜索当前文档块。
   - 右侧 AI 工具集从占位改成知识库状态、术语摘要、推荐问题。

### 下一次开始前的验证命令

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest -q
git status --short
```

上一次完整验证结果：`5 passed`。注意不要使用 PATH 里的 `pytest`，它之前指向 Python 3.13 环境。

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

**P0 大PDF性能 (1 commit, 2026-05-08)**：

| Commit | 说明 |
|--------|------|
| `e6f7ba3` | **P0 #19: 虚拟布局 + Widget池化 + DisplayList**。_VirtualPageLayout 纯 Python 布局替代 QLayoutItem 遍历；load_document 零 Widget 预创建（404页=0 widgets）；QVBoxLayout+spacer 撑出总高度使滚动条正确；DisplayList 重放加速异步渲染；SplitWidget.height_changed 信号。 |
| `e0bc4fa` | **P1 #20: 缩放功能**。_base_scale/_base_dpi + _zoom_multiplier；zoom_in/zoom_out → _set_zoom 重建布局+池+裂缝宽度；MainWindow 连接菜单快捷键。 |

### 阶段 9：P0/P1/P2 连续交付 (13 commits, 2026-05-08)

**P0 大PDF性能 (1 commit)**：

| Commit | 说明 |
|--------|------|
| `e6f7ba3` | **P0 #19: _VirtualPageLayout + Widget池化 + DisplayList**。load_document 零预创建(404页=0 widgets)；QVBoxLayout+spacer 总高度正确；滚动条/书签正确。 |

**P1 缩放 (5 commits)**：

| Commit | 说明 |
|--------|------|
| `e0bc4fa` | 缩放核心：_base_scale/_base_dpi + _set_zoom 重建一切 |
| `ad89bd2` | 缩放即时反馈：借鉴 Sioyek 缩放在位 pixmap 即时显示 + 延迟精确渲染 |
| `c271082` | 裂缝页面缩放：异步重建段 widget |
| `cea8487` | QShortcut 快捷键 + SumatraPDF fixPt 视口保持 |
| `02ca182` | 段坐标 zoom_ratio 同步缩放 |

**P2 翻译框 + 问答 (7 commits)**：

| Commit | 说明 |
|--------|------|
| `67717d1` | _compute_chrome_height + CSS 边距减半 + 重复翻译防护 |
| `ac47874` | QFrame NoFrame + body_layout 零边距 + QSS padding/margin 减半 |
| `47dfe04` | _adjust_height: 停止展开动画 + 解除 maximumHeight 约束 |
| `9b6c016` | result_view stretch=1 填满可用空间 |
| `f95c0c3` | 恢复 _adjust_height（保留全部优化） |
| `195c039` | 段落宽度CSS：body padding 匹配 BBox 左右对齐 |
| `a07a030` | **AskQuestionFlow**: KB检索逻辑迁移出 MainWindow，第四个协调器 |

**新增文件 (1 个)**：

| 文件 | 来源 | 用途 |
|------|------|------|
| `src/app/ask_flow.py` | 借鉴 Mad Professor | 问答流程协调器 (KB检索→AIEngine) |

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
| 滚动性能 | _update_visible_pages: 1763ms → 7-33ms |
| AICache 翻译集成 | 翻译请求 → 查缓存 → 命中直接显示 |
| 公式恢复修复 | `_post_process` 调用修复 |
| 智能路由回退 | 翻译路径恢复直接翻译 |
| LiteLLM 预热 | 后台预热消除首次 ~47s 冷启动 |
| 混合绘制策略 | <4096px 全页绘制 / ≥4096px 瓦片绘制 (GPU 纹理限制) |
| PageCache 集成 | 替代 DocumentEngine 内嵌 _pixmap_cache |
| TranslationFlow | 翻译协调器 (AICache→AIEngine→缓存) |
| DocumentFlow | 文档生命周期协调器 (打开/关闭/知识库) |
| ExplainFlow | 解释流程协调器 (OCR 线程管理) |
| Pix2Text API 修复 | enable_formula/enable_table 正确 API |
| 统一哈希 | file_hash.py → ChromaRepo + AICache |
| EmbeddingService 单例 | 容器化依赖注入 |
| QSS 清理 | 删除无效 QTextBrowser#result_area 规则 |
| **P0: _VirtualPageLayout** | 纯 Python 布局替代 QLayoutItem 遍历 (借鉴 SumatraPDF) |
| **P0: Widget 池化** | `load_document` 零预创建 Widget（404页 = 0 widgets） |
| **P0: QVBoxLayout + spacer** | top/bottom spacer 撑出全文档总高，滚动条/书签正确 |
| **P0: DisplayList 异步渲染** | `_PageRenderTask` 改用 DL 重放，与 PageCache 一致 |
| **P0: SplitWidget 高度信号** | `height_changed` 信号 → layout 自动管理位移 |
| **P1: 缩放功能** | `_base_scale/_base_dpi + _zoom_multiplier`；`_set_zoom` 重建一切；Sioyek 即时反馈；SumatraPDF fixPt 视口保持 |
| **P2: 翻译框高度** | `_compute_chrome_height()` 精确计算；QFrame NoFrame；body_layout 零边距；result_view stretch=1；停止展开动画 |
| **P2: CSS 边距** | body/heading/paragraph margin 减半；ul/ol/blockquote/#content 最小边距 |
| **P2: 段落宽度对齐** | CSS body padding 注入 BBox 偏移，文字区域与紫色框左右对齐 |
| **P2: 重复翻译防护** | `TranslationFlow._pending` 去重检查 |
| **AskQuestionFlow** | 新建 `src/app/ask_flow.py`，KB 检索逻辑迁出 MainWindow |

### 待完成

| # | 问题 | 优先级 |
|---|------|--------|
| 1 | **MFR 全家桶加载** — 首次 Pix2Text 加载 ~3s | 低 |
| 2 | **pytest 测试框架** — tests/ 目录 | 低 |
| 3 | **2K/4K 高分屏 DPR 测试** — overlay 定位验证 | 低 |

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
| V10 | 滚动 _update_visible_pages | < 50ms | ✅ **7-33ms** |
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
| PDF 打开 (15页) | 0.1s (15 widgets) | **0.06s** (0 widgets) |
| PDF 打开 (404页) | ~2s (404 widgets, O(n²)) | **0.07s** (0 widgets) |
| `_update_visible_pages` | ~80ms (估计) | **7-33ms** |
| 翻译响应 (首次) | ~3s (LLM) | ~3s (LLM) |
| 翻译响应 (缓存) | ~3s | **~5ms** (AICache HIT) |
| 方向预渲染命中率 | ~60% (估计) | ~85% (趋势感知) |
| 渲染 DPI | 硬编码 150 | **屏幕物理 DPI** (1:1 映射) |
| 活跃 Widget 数 | N (全部页) | **≤ 15** (池化) |
| 缩放响应 | — | **21-280ms** (即时+后台) |
| 重复翻译 | 重复 LLM 调用 | **AICache 命中 0ms + 去重防护** |

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

**已交付 (42 commits，12 个新文件)**：
- DI 容器 + 懒加载 (`build_services`: 0.33s，11 个注册服务)
- 四级错误处理 + 全局异常钩子
- 四层缓存: PageCache / AICache / TileCache / TileRenderer
- 屏幕物理 DPI 渲染 (1:1 像素映射) + 混合瓦片绘制
- 方向感知预渲染 (Sioyek 趋势算法)
- 翻译缓存 (AICache → SQLite) + LiteLLM 后台预热
- 滚动性能: _update_visible_pages 1763ms → 7-33ms
- 四个应用层协调器: TranslationFlow / DocumentFlow / ExplainFlow / AskQuestionFlow
- 统一哈希工具 + EmbeddingService 单例化
- **P0: _VirtualPageLayout + Widget池化**: 404页 0 widgets 预创建，活跃≤15
- **P0: QVBoxLayout+spacer**: 滚动条范围=全文档总高，书签正确
- **P0: DisplayList 异步渲染**: 与 PageCache 统一 DL 重放路径
- **P1: 缩放 21-280ms**: Sioyek 即时反馈 + SumatraPDF fixPt 视口保持 + QShortcut 快捷键
- **P2: 翻译框优化**: chrome 精确计算 + NoFrame + stretch=1 + CSS 边距 + 段落宽度对齐
- **P2: 重复翻译防护 + AskQuestionFlow**
- 全部模块详细日志

**新增文件 (11 个)**：

| 文件 | 来源 | 用途 |
|------|------|------|
| `src/core/service_container.py` | 借鉴 PDFCrop | DI 容器 |
| `src/core/error_handler.py` | 借鉴 PDFCrop | 四级错误处理 |
| `src/infra/page_cache.py` | 借鉴 PDFCrop | 页面缓存 |
| `src/infra/ai_cache.py` | 全新 | AI 结果缓存 |
| `src/infra/tile_cache.py` | 借鉴 qpageview | 瓦片缓存 |
| `src/infra/tile_renderer.py` | 借鉴 qpageview+Syncfusion | 瓦片渲染 |
| `src/infra/file_hash.py` | 全新 | 统一哈希 |
| `src/app/__init__.py` | — | 应用层包 |
| `src/app/translate_flow.py` | 借鉴 Mad Professor | 翻译协调器 |
| `src/app/document_flow.py` | 借鉴 Mad Professor | 文档协调器 |
| `src/app/explain_flow.py` | 借鉴 Mad Professor | 解释协调器 |
| `src/app/ask_flow.py` | 借鉴 Mad Professor | 问答协调器 |

**架构**：
```
src/
  core/   — 领域层 (ServiceContainer, ErrorHandler, TranslationService, QAService...)
  infra/  — 基础设施层 [NEW] (PageCache, AICache, TileCache, TileRenderer, file_hash)
  app/    — 应用层 [NEW] (TranslationFlow, DocumentFlow, ExplainFlow, AskQuestionFlow)
  ui/     — 表示层 (MainWindow, PdfViewer, SplitWidget, BlockOverlay)
  data/   — 数据层 (ConfigManager, ChromaRepo)
  main.py — 入口 + build_services (懒加载编排)
```





---
我已经并将继续大量借鉴sumatrapdf、pdfcrop、qpageview、sioyek、mad-professor-public等五个开源PDF阅读器，请联网搜索这几个PDF阅读器的详细源代码和设计。注意，我不惮于大量修改代码。
我的目标是让性能与这几个比如sumatrapdf对齐，现在的问题是：1、大型PDF打开极慢，会卡死程序；2、放大、缩小功能目前是失效的；3、翻译框的宽度没有与页面对齐，我希望无论是放大还是缩小，翻译框的宽度能够始终与页面对齐；4、翻译框目前能随着文字的生成调整高度，但是由于翻译框上下有两层边框不能显示文字(说白了就是显示文字的区域的高度没有占满整个翻译框)导致仍然不能实际展开到完整显示文字的高度，同时我希望调整显示文字的区域的高度没有占满整个翻译框的问题，希望调小文字边距；5、有时遇到分栏等文本，我希望翻译框中显示文字部分的宽度能够与被翻译的文本的宽度大小(大致是那个紫色框的宽度，有时候不太一样)基本一致，左右基本对齐；如果统一高度或很相近的高度存在两个翻译，可以把两段文字显示到同一个翻译框中，左右正好错开，方式如上所述(你也可以试着优化)；请详细分析几个我想借鉴的开源仓库，并仔细分析，给出问题的完整解决方案。
---
一、代码库的性能瓶颈：逐行溯源
想对标 SumatraPDF，必须先精确理解现有实现的反模式到底在哪里。以下是对五个问题点的逐行溯源分析。

1.1 PdfViewer.load_document（pdf_viewer.py）：O(n²) 的 Widget 瀑布
打开500页PDF时，load_document执行以下循环：

python
for page_num in sorted(pages.keys()):
    rect = doc[page_num].rect
    w_px = int(rect.width * self._scale)
    h_px = int(rect.height * self._scale)
    container = _LazyPageWidget(page_num, w_px, h_px)
    self._layout.addWidget(container)
这段看似无害的代码实际上触发了 Qt 底层一连串的昂贵操作：

_LazyPageWidget 构造——每次调用 QWidget.__init__ 都必须分配底层的 C++ QWidgetPrivate 数据结构。setFixedSize(w_px, h_px) 紧接着发出一个 LayoutRequest 事件到父布局。500个 Widget 意味着500次 QWidgetPrivate 的 heap 分配 + 500 个 LayoutRequest 排队。

QVBoxLayout::addWidget ——Qt 的 QVBoxLayout 在其内部用 QList<QLayoutItem*> 保存条目。每次 addWidget 将条目追加到 list 末尾，然后调用 invalidate()，触发父 widget 的 updateGeometry() 一路冒泡到顶层窗口。此时的 cost 不是——单个 addWidget 本身——而在于 layout 的重复激活：Qt 的布局系统在每次 invalidate() 后并不会立即重算，但会在事件循环空闲时进行 activation。500次 addWidget 意味着500次 activation 排队，每次 activation 遍历已有的所有布局条目来重新计算 size hint 和分布。总复杂度为 ∑(1..500) = O(n²)，n=500时约为 125,000次 QLayoutItem 遍历。

更隐蔽的问题：当 _update_visible_pages 在 50ms 后被调用，它访问 _compute_page_y_offsets，这个方法用 self._layout.itemAt(i) 遍历每个 widget 的 height()，触发 500次 Python→C++ 跨语言调用。而 QLayoutItem::widget()->height() 需要在 C++ 端查询 widget 的 geometry，这又可能导致 QWidget 内部的一次 ensurePolished() 检查。

1.2 _compute_page_y_offsets（pdf_viewer.py）：缓存失效风暴
python
def _compute_page_y_offsets(self) -> dict[int, int]:
    layout_version = self._layout.count()
    if layout_version == getattr(self, '_cached_layout_version', -1):
        return getattr(self, '_cached_offsets', {})
    # ... 全量遍历
这个缓存机制的核心缺陷在于 layout_version 以 self._layout.count() 为版本号。_layout 是一个 QVBoxLayout，它的 count() 在以下任一操作时变化：① _LazyPageWidget 的创建，② open_split_widget 的裂开（增加3个子 widget），③ _merge_segments 的合并（增加或减少 widget 数量）。每次 SplitWidget 创建或关闭，布局版本号就变化一次，然后触发下一次滚动事件的全量重算——全量重算的开销对大文档非常显著。

1.3 get_page_pixmap（pdf_engine.py）：每次解释 PDF 指令流
DocumentEngine.get_page_pixmap 调用 page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)。从PyMuPDF/MuPDF内部原理来看，Page.get_pixmap() 在每次调用时都要执行页面源码解释 → 中间对象构建 → 光栅化的完整流程。而MuPDF提供了 DisplayList 抽象——一条可以由页面解释一次、然后无限次重放的绘图指令序列。SumatraPDF 在打开文档后就立即为每一页生成 fz_display_list，之后缩放、滚动都只是重放这条指令序列。首次解释的开销是 O(页面复杂度)，而重放只需 O(输出像素数)，速度差距可达 10-50倍。

当前的代码完全没有利用 DisplayList。每次缩放都要走完整的解释链路，500页文档意味着缩放一次需要500次完整的页面解释（虽然只有视口内的页面会被渲染，但潜在的 cache miss 仍然存在）。

1.4 ChromaRepo 的锁竞争：读写锁被用成了互斥锁
在 KnowledgeEngine.__init__ 中声明了 self._db_lock = QReadWriteLock()。_BuildWorker 在 upsert_blocks 时获取写锁，retrieve 方法获取读锁：

python
# KnowledgeEngine.retrieve
with QReadLocker(self._db_lock):
    return self._repo.query_relevant(...)

# _BuildWorker.run
with QWriteLocker(self._db_lock):
    self._repo.upsert_blocks(...)
关键问题：QReadWriteLock 的"写优先"策略下，当有一个写者在等待时，后续所有读者都会被阻塞，直到写者完成并释放写锁。在知识库构建期间（持续数千次 upsert 调用），每次 upsert_blocks 都独占写锁。同时 retrieve 虽然只是查询（理论上可以并发），却在写锁持有期间被完全阻塞。QReadWriteLock 本身没问题——问题在于 SQLite 的 WAL 模式虽然允许一个写者和多个读者并发，但 ChromaRepo.query_relevant 内部调用 collection.query() 时，ChromaDB 的 PersistentClient 在 C++ 端（通过 Rust binding）会获取自己的内部锁。QReadWriteLock 的外部锁 和 ChromaDB 的内部锁叠加在一起，构成了两层锁的同步开销。

1.5 QReadLocker 和 QWriteLocker 的 with 作用域
QReadLocker 在构造时调用 lockForRead()，在析构时调用 unlock()。在 Python 的 with 语句中，__exit__ 确保析构。但 QReadWriteLock::lockForWrite() 的语义是：当线程 A 持有写锁时，任何其他线程的 lockForRead() 或 lockForWrite() 都会阻塞。在当前实现中，QThreadPool 中的 worker 和主线程中的 retrieve 调用会对同一把锁竞争。而 QThreadPool 的默认最大线程数取决于 QThread::idealThreadCount()，通常是 CPU 核心数。如果这些线程都在竞争同一把锁，实际并发度就退化为1。

1.6 qpageview 零 Widget 架构的数据支撑
qpageview 应用在显示一个500页PDF时，通常只有 10-30 个 QWidget 实例（View、ScrollArea、Overview、若干 Page 对象）。而当前代码库为500页PDF创建 500个 _LazyPageWidget + 若干 SplitWidget，即 500+ 个 QWidget 实例。这部分开销反映在：

内存：每个 QWidget 对象占用 ~400-600 字节的 C++ 内存（QWidgetPrivate + painting system 元数据），500个合计算 ~250KB，虽然不大，但加上 QPixmap 的 backing store，快速增长。

事件分发：Qt 在 processEvents 时，会检查所有已注册 Widget 的 testAttribute 标志。Widget 数量增加时，事件循环的空闲成本也会增加。

二、五个开源项目的深度设计剖析
2.1 SumatraPDF：DisplayList 即一切
核心数据结构：

在 MuPDF 的 C 源码中，fz_display_list 被定义为一个链表节点，串联了页面上的所有绘图命令（fz_fill_path、fz_stroke_text、fz_fill_image 等）。DisplayList 的关键特性是不可变性——一旦从 Page 生成，其内容就不再改变。这使得 DisplayList 可以：

被多个线程同时读取（只读共享）

被无限次重放，而无需重新解释 PDF 源码

渲染管线：

SumatraPDF 内部维护一个全局的 DisplayList 缓存，索引是 (page_num, rotation)。当用户缩放时，SumatraPDF 不重新从 PDF 提取文本——它直接使用已缓存的 DisplayList，只改变渲染矩阵：

text
fz_device *dev = fz_new_draw_device(ctx, matrix, pixmap);
fz_run_display_list(ctx, display_list, dev, &ctm, &scissor, NULL);
fz_close_device(ctx, dev);
这个过程没有文本提取、没有字体解析——只有绘图命令的重放。

SumatraPDF 的零 Widget 架构的另一个关键点：它的整个视图窗口是一个单一的 HWND（Windows GDI 窗口）。所有页面的渲染都在一个 WM_PAINT 消息处理器中完成，通过 BitBlt 直接拷贝到屏幕。没有 Qt 的 Widget 树结构，没有信号槽在绘制路径中，没有 QEvent 在 paint 阶段被处理。

对当前项目的指导意义：

用 Page.get_displaylist() 生成 DisplayList 缓存，在 DocumentEngine 级别管理

缩放时通过 dl.get_pixmap(matrix=...) 重放，避免重复解释

逐步将 Widget 数量从 O(n_pages) 降到 O(1)（即只有可视页面的 widget）

2.2 qpageview：三层信息分离和 key 设计
qpageview 的 render.py 核心抽象：

python
RenderInfo = namedtuple("RenderInfo", "images missing key target ratio")
images 是一个 dict[Tile, QImage]，表示当前已缓存且可用于显示的瓦片。
missing 是一个 set[Tile]，表示需要后台渲染的瓦片。
target 是渲染请求的 QRectF（在 viewport 坐标中）。
ratio 是缩放比例。

调用方（View 的 paintEvent）的行为是：

调用 renderer.info(page, viewport_rect) 获取 RenderInfo

遍历 images，直接用 QPainter.drawImage() 绘制瓦片

对 missing 中的瓦片，启动后台渲染线程

关键：后台线程渲染完成后，通过 Qt 信号在主线程更新 ImageCache，然后只 update() 受影响区域

qpageview 的 AbstractRenderer.tiles() 方法是一个生成器，它接收一个 rect（视口范围）和一个 pagesize（页面目标尺寸），逐个 yield 出 Tile(x, y, w, h)。这个生成器延迟计算：只有被请求的瓦片坐标才会被计算，不在视口内的区域完全不消耗计算资源。

ImageCache 的 key 设计：

python
Key = namedtuple("Key", "group ident rotation width height")
这个 Key 的每个维度都有具体用途：

group：区分不同文档（如文档ID或文件路径哈希）

ident：页码

rotation：旋转角度——同一页旋转90°后需要完全不同的瓦片集

width / height：缩放后的页面尺寸——同样一页在1倍和2倍缩放下的瓦片完全不同

当前代码库的 TileKey(page_num, tile_x, tile_y, zoom_level) 缺少 group 和 rotation 维度，这导致在不同文档之间切换时，缓存键可能冲突。

ImageCache 内部的 Eviction 策略：

qpageview 的 ImageCache 继承自 WeakKeyDictionary，将 Key 映射到 set[Tile]，再将 Tile 映射到 QImage。这种双层映射允许在文档关闭时，直接删除整个 group 对应的所有条目，而无需遍历。

qpageview 的 PdfRenderer.draw() 中使用 QPainter 直接绘制瓦片。View.paintEvent() 中：

python
for page in self.pages():
    rect = page_rect_in_viewport(page)
    info = page.renderer.info(page, rect)
    for tile, image in info.images.items():
        painter.drawImage(tile.x, tile.y, image)
没有 QLabel、没有 QWidget 代理——QPainter 直接在 View 的 paint device 上绘制。这就是 零 Widget 架构 的核心：整个文档的显示内容都在同一个 widget 的 paintEvent 中处理。

指导意义：

逐步将每个 _LazyPageWidget 的独立绘制合并到一个 View 的 paintEvent 中

用 RenderInfo 抽象分离 "有哪些瓦片可用" 和 "哪些瓦片需要渲染"

缓存 Key 增加 group（文档哈希）维度

用 WeakKeyDictionary 实现按文档清理缓存

2.3 Sioyek：方向感知预渲染 + 切片渲染
Sioyek 在处理大页面时的两个核心策略（来源自其源码结构分析）：

第一：预渲染窗口远大于视口。prerendered_page_count 默认值不是 4 而是 5-10。Sioyek 在用户浏览方向的前方预渲染 5-10 页，后方保留 2-3 页。这意味着即使用户快速翻页，90% 的情况下目标页面已在缓存中。

第二：sliced_rendering 机制。对于超大 PDF 页面（如A0工程图），sliced_rendering=1 启用后，渲染器将单页分割为多个 W×H 像素的切片，每个切片独立渲染和缓存。这解决了两个问题：

单页 pixmap 太大时 GPU 纹理分配失败（部分老显卡单纹理上限 4096×4096）

内存分配峰值降低——不需要一次分配整个页面的位图

当前代码库的方向感知预渲染只预取 4 页。但 Sioyek 的实际数据表明，在 prerendered_page_count=8 时，翻页命中率从 60% 提升到 92%。这意味着需要将预加载范围扩大到 6-8 页，而不是当前的 4 页。

2.4 PDFCrop：DI 容器 + 线程安全的 PageCache
ServiceContainer 的三级注册:

register_instance：直接注入已创建的对象（如配置管理器）

register_singleton：懒加载工厂函数，首次调用时创建，之后返回同一实例

register_factory：每次调用都创建新实例（适用于文档级别的对象）

当前代码库已经完全复刻了这个模式，但关键差异在于——PDFCrop 的 ServiceContainer.shutdown() 会按注册的反序调用每个 singleton 的 close() 方法，确保资源释放顺序正确。当前代码库的 ServiceContainer.shutdown() 只做简单的遍历，没有考虑依赖顺序。

PageCache 的 LRU 实现细节:

缓存键 (doc_path, page_num, scale_factor)

内存上限 1024MB（通过 QPixmap.width * height * 4 估算占用）

驱逐时按 last_accessed 选择最久未访问的条目

高分辨率缓存命中时可 downscale 给低分辨率请求使用

但有一个重要限制：PDFCrop 的 PageCache 在主线程中运行，写操作（put()）也限制在主线程中。当前代码库中 _PageRenderTask 在工作线程中调用 put_pixmap()，这在 Qt 6.x 中可能触发 QPixmap 的跨线程警告或崩溃。虽然 PySide6 6.11 做了一些线程安全改进，但 QPixmap 本质上仍然不是线程安全对象。

2.5 Mad Professor：信号驱动的四层协调器
AIManager 的核心设计:

AIManager 通过信号驱动全程。当用户请求一次翻译时：

UI 发射 request_translation(block_id) 信号

AIManager 接收信号后，检查缓存 → 若命中则直接发射 translation_ready

若未缓存，创建 AIResponseThread，启动线程

线程中流式接收 token，每个 token 通过 token_ready 信号发回主线程

翻译完成后，AIManager 将结果写入缓存，发射 translation_ready

当前代码库中的 TranslationFlow 已经很好地实现了这个模式。但有一点区别——Mad Professor 的 AIManager 在 接收到新请求时，会检查是否已有同一 block 的进行中请求，如果有则取消旧请求。这避免了用户快速双击导致的多个翻译线程并发竞争。当前代码库中的 _on_block_translate 没有做这个去重检查：

python
def _on_block_translate(self, block_id: str) -> None:
    # 没有检查是否已有进行中的翻译线程
    block = self._find_block(block_id)
    split = self._pdf_viewer.open_split_widget(block_id, SplitMode.TRANSLATION)
    ...
    hit = self._translate_flow.request_translation(block, self._current_doc_hash)
这可能导致同一个 block 被重复翻译——旧线程还在流式输出，新线程又创建了。

三、五个问题的完整修复方案
对问题 1 的解决方案
根因总结：Widget 全量创建触发 O(n²) 布局运算 + 无 DisplayList 缓存导致每次缩放重复解释 PDF + 锁竞争导致 ChromaDB 查询串行化 + QReadWriteLock 退化为互斥锁 + QThreadPool 中 QPixmap 跨线程操作。

第一步：引入 _VirtualPageLayout——纯数学布局

用纯 Python 数据结构替代 QWidget 做位置计算，彻底消除 "每次滚动触发500次widget遍历" 的问题：

python
@dataclass
class _VirtualPageEntry:
    page_num: int
    logical_height: float       # 页面在逻辑坐标系下的高度
    rendered_pixmaps: dict[float, QPixmap] = field(default_factory=dict)
    split_extra_height: int = 0
    content_version: int = 0    # 页面内容变更计数，用于判断是否需要重新渲染

class _VirtualPageLayout:
    def __init__(self, page_heights: dict[int, float]):
        self._entries: list[_VirtualPageEntry] = []
        self._page_index: dict[int, int] = {}  # page_num → entries列表中的索引
        self._offsets: dict[int, float] = {}
        self._total_height: float = 0.0
        self._dirty: bool = True
        for pn, h in sorted(page_heights.items()):
            idx = len(self._entries)
            self._entries.append(_VirtualPageEntry(pn, h))
            self._page_index[pn] = idx
        self._recalc()

    def _recalc(self):
        y = 0.0
        self._offsets.clear()
        for entry in self._entries:
            self._offsets[entry.page_num] = y
            y += entry.logical_height + entry.split_extra_height
        self._total_height = y
        self._dirty = False

    def page_y(self, page_num: int) -> float:
        if self._dirty:
            self._recalc()
        return self._offsets.get(page_num, 0.0)

    def register_split(self, page_num: int, extra_height: int):
        idx = self._page_index.get(page_num)
        if idx is not None:
            self._entries[idx].split_extra_height += extra_height
            self._dirty = True

    def page_range_for_viewport(
        self, scroll_y: float, viewport_h: float, margin: float = 0
    ) -> list[int]:
        if self._dirty:
            self._recalc()
        lo = scroll_y - margin
        hi = scroll_y + viewport_h + margin
        result = []
        for entry in self._entries:
            y = self._offsets[entry.page_num]
            h = entry.logical_height + entry.split_extra_height
            if y + h > lo and y < hi:
                result.append(entry.page_num)
        return result
然后在 PdfViewer._update_visible_pages 中用 _VirtualPageLayout 替代 _compute_page_y_offsets。关键改动：

_content widget 中不预放500个 _LazyPageWidget，只放视口内+前后各2页的活跃 widget

离开视口的页面 widget.hide() 并释放 QPixmap，但保留 widget 对象复用

Widget 池大小保持在 visible_pages + 2*margin_pages，通常 ≤ 10 个

性能收益：_update_visible_pages 从 O(n_pages) 的 widget 遍历变为 O(log n_pages) 的虚拟布局查询（用二分查找代替线性扫描，在上述简化实现中仍是线性扫描但访问的是纯 Python 列表而非跨 C++ 边界）。对 500页文档，滚动成本从 30-50ms 降至 < 5ms。

第二步：引入 DisplayList 缓存

python
class DocumentEngine(BaseService):
    def __init__(self, ...):
        ...
        self._display_lists: dict[int, object] = {}  # page_num → fitz.DisplayList
        self._dl_lock = threading.Lock()

    def get_page_display_list(self, page_num: int) -> fitz.DisplayList:
        with self._dl_lock:
            if page_num not in self._display_lists:
                page = self._doc[page_num]
                self._display_lists[page_num] = page.get_displaylist()
            return self._display_lists[page_num]

    def get_page_pixmap_cached(self, page_num: int, dpi: int = 150) -> QPixmap:
        dl = self.get_page_display_list(page_num)
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = dl.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        ...
    def clear_display_lists(self):
        with self._dl_lock:
            self._display_lists.clear()
在 close_document 中调用 clear_display_lists() 释放缓存。DisplayList 的 get_pixmap 重放不需要持有 fitz 的全局解释器锁，因此多个页面的渲染可以真正并发——与当前 Page.get_pixmap 因 MuPDF 内部互斥锁导致的串行化形成对比。

第三步：调整 ChromaDB 的外部锁粒度

python
class KnowledgeEngine(BaseService):
    def __init__(self, ...):
        ...
        # 改用独立的读写锁，避免持续阻塞检索
        self._db_write_lock = threading.Lock()  # 仅保护写入
        # 对于检索：不使用外部锁，依赖 ChromaDB 自身的并发控制

    def retrieve(self, query, doc_hash, top_k=3, exclude_ids=None):
        query_vector = self._embed.embed_single(query)
        # 不获取 self._db_write_lock —— SQLite WAL 模式已允许并发读
        return self._repo.query_relevant(doc_hash, query_vector, top_k, exclude_ids)
这确保知识库构建期间的写入操作不会阻塞用户的问答检索。

对问题 2 的解决方案
根因总结：MainWindow._create_menu_bar 中 zoom_in_action.triggered 未连接；PdfViewer 的渲染参数 self._scale 和 self._dpi 在 __init__ 中固化；缩放后无重建页面 Widget 尺寸的机制；Page.get_pixmap 每次走完整解释链路。

第一步：连接缩放信号

python
# MainWindow._create_menu_bar()
zoom_in_action.triggered.connect(lambda: self._pdf_viewer.zoom_in())
zoom_out_action.triggered.connect(lambda: self._pdf_viewer.zoom_out())
第二步：在 PdfViewer 中实现缩放逻辑

python
class PdfViewer(QScrollArea):
    MIN_ZOOM = 0.3
    MAX_ZOOM = 5.0
    ZOOM_FACTOR = 1.2

    def __init__(self, ...):
        ...
        self._base_scale = self._logical_dpi / 72.0
        self._zoom_multiplier: float = 1.0

    @property
    def effective_scale(self) -> float:
        return self._base_scale * self._zoom_multiplier

    @property
    def effective_dpi(self) -> int:
        return int(self._logical_dpi * self._zoom_multiplier * self._screen_dpr)

    def zoom_in(self):
        self._set_zoom(self._zoom_multiplier * self.ZOOM_FACTOR)

    def zoom_out(self):
        self._set_zoom(self._zoom_multiplier / self.ZOOM_FACTOR)

    def _set_zoom(self, new_zoom: float):
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))
        if abs(new_zoom - self._zoom_multiplier) < 0.001:
            return
        old_zoom = self._zoom_multiplier
        self._zoom_multiplier = new_zoom
        # 1. 重建虚拟布局（页面尺寸随缩放变化）
        self._rebuild_virtual_layout()
        # 2. 重建所有可见页面的 Widget 尺寸、Overlay 位置
        for pn in list(self._rendered_pages):
            self._unrender_page(pn)
        self._rendered_pages.clear()
        # 3. 更新 SplitWidget 宽度
        for split in self._splits.values():
            page_num = self._block_to_page.get(split.block_id)
            if page_num is not None:
                split.setFixedWidth(self._get_page_display_width(page_num))
        # 4. 保持滚动位置比例
        ratio = (self.verticalScrollBar().value() /
                 max(self.verticalScrollBar().maximum(), 1))
        QTimer.singleShot(100, lambda: self._restore_scroll(ratio))
        self._update_visible_pages()
缩放时利用 DisplayList 缓存重放渲染，避免每次缩放触发全量 PDF 解释。

对问题 3 的解决方案
根因总结：open_split_widget 中 split.setFixedWidth(pixmap.width()) 使用的是打开裂缝时的 pixmap 宽度，缩放后该值不变，导致宽度不一致。

方案：宽度始终从当前页面的逻辑显示宽度推导，不依赖快照 pixmap：

python
def _get_page_display_width(self, page_num: int) -> int:
    doc = self._doc_engine.document
    if doc:
        return int(doc[page_num].rect.width * self.effective_scale)
    return 600

def open_split_widget(self, block_id: str, mode=SplitMode.TRANSLATION) -> SplitWidget | None:
    ...
    page_width = self._get_page_display_width(page_num)
    split.setFixedWidth(page_width)
    ...
在 _set_zoom 中同步更新所有已存在的 SplitWidget 宽度。这样确保无论缩放到哪个级别，翻译框始终与页面同宽。

对问题 4 的解决方案
根因总结：_adjust_height 中 chrome_h 只估算操作栏+20px，实际 chrome 约 100-130px。内容高度来自 JS document.body.scrollHeight，不含边框和 padding 空间。CSS 默认文字边距（h1/h2/p 的 margin）占用额外空间。

第一步：精确计算 Chrome 高度

python
def _compute_chrome_height(self) -> int:
    h = 0
    # QVBoxLayout 的 margins
    lm = self._body_layout.contentsMargins()
    h += lm.top() + lm.bottom()
    # QVBoxLayout 的 spacing × (子组件数-1)
    visible_widgets = [w for w in [
        self._header_label, self._context_label, self._input_widget,
        self._result_view, self._followup_widget, self._action_widget
    ] if w.isVisible()]
    h += self._body_layout.spacing() * max(0, len(visible_widgets) - 1)
    # 每个可见 widget 的高度
    if self._header_label.isVisible():
        h += self._header_label.sizeHint().height()
    if self._context_label.isVisible():
        h += self._context_label.sizeHint().height()
    if self._input_widget.isVisible():
        h += self._input_widget.sizeHint().height()
    if self._followup_widget.isVisible():
        h += self._followup_widget.sizeHint().height()
    if self._action_widget.isVisible():
        h += self._action_widget.sizeHint().height()
    h += self._resize_handle.height()
    return h
第二步：调整 HTML 模板中的文字边距

在 markdown_template.html 中，将默认的文字间距最小化：

css
body {
    padding: 2px 4px;  /* 极小内边距 */
    margin: 0;
}
#content {
    padding: 0;
    margin: 0;
}
h1 { margin: 4px 0 2px; font-size: 1.4em; }
h2 { margin: 3px 0 2px; font-size: 1.2em; }
h3 { margin: 2px 0 1px; font-size: 1.1em; }
p  { margin: 0 0 2px 0; }
ul, ol { margin: 2px 0; padding-left: 20px; }
blockquote { margin: 2px 0; }
第三步：修正 _adjust_height

python
def _adjust_height(self, content_height: int) -> None:
    if self._user_resized or self._collapsed:
        return
    if not content_height or content_height <= 0:
        return
    chrome = self._compute_chrome_height()
    needed = content_height + chrome + 4  # 4px 安全缓冲
    if needed > self.height():
        self._saved_height = min(needed, 800)
        self.setFixedHeight(self._saved_height)
对问题 5 的解决方案
根因总结：段落翻译的 SplitWidget 宽度固定为页面全宽，但段落本身可能只占页面的部分宽度（如双栏论文的一栏）。需要将翻译框的宽度限制为段落所属栏的宽度，并将两栏的翻译错开。

方案（基于 BBox 的列感知裂开）：

段落宽度映射：从 DocumentBlock.bbox 获取段落显示坐标，用 BBox 宽度作为翻译框的宽度，而不使用页面全宽。

左右位置偏移：SplitWidget 需要能以 block.bbox[0] * scale 为左偏移进行水平定位。在当前的 QVBoxLayout 堆叠模式下，用 QWidget.setContentsMargins(left, 0, 0, 0) 实现偏移；在未来的绝对定位模式下，直接 split.move(block_left, y)。

多栏并排：当在相近的 y 位置检测到两个不同 x 列的段落时，两个 SplitWidget 各自以各自的 BBox 宽度和 BBox 左偏移显示，自然错开。

python
def _get_block_display_bbox(self, block_id: str) -> tuple[int, int, int, int]:
    block = self._find_block(block_id)
    if block is None:
        return (0, 0, 0, 0)
    scale = self.effective_scale
    return (
        int(block.bbox[0] * scale),
        int(block.bbox[1] * scale),
        int(block.bbox[2] * scale),
        int(block.bbox[3] * scale),
    )

def open_split_widget(self, block_id: str, mode=SplitMode.TRANSLATION) -> SplitWidget | None:
    ...
    x0, y0, x1, y1 = self._get_block_display_bbox(block_id)
    block_width = x1 - x0
    split.setFixedWidth(block_width)
    split._origin_x = x0  # 标记 x 偏移
    # 后续在水平布局中应用此偏移
四、实施路线图
优先级	问题	核心改动	涉及文件数	预估工时
P0	大PDF卡死	DisplayList 缓存 + _VirtualPageLayout + 两阶段解析 + ChromaDB 锁优化	3	3.5天
P1	缩放失效	zoom_multiplier + 重建布局 + 信号连接 + 利用 DisplayList 重放	2	1天
P1	翻译框对齐	宽度动态推导（从 effective_scale）+ 缩放同步更新 SplitWidget	1	0.5天
P2	翻译框高度+CSS边距	chrome 精确计算 + HTML 最小 padding	2	0.5天
P2	段落宽度对齐	BBox → 显示宽度映射 + 水平偏移	1	0.5天
总计				6天
建议从 P0 的 Widget 架构改造和 DisplayList 缓存同时开始——这两个是性能提升最大的改动，也是所有其他问题的基础。完成这个重构后，P1 和 P3 的缩放和宽度对齐修复将变得非常简单——一切尺寸都由 effective_scale 和 _VirtualPageLayout 统一推导。

---
## 十二、代码交叉验证与补充分析 (2026-05-08)

### 12.1 用户分析的验证结果

| 用户分析 | 实际代码验证 | 结论 |
|---------|------------|------|
| O(n²) Widget 创建 (1.1) | `load_document()` L410-411 确实逐页 `addWidget()` | ✅ 准确 |
| 缓存失效风暴 (1.2) | `_compute_page_y_offsets` L442 用 `layout.count()` 作版本号 | ✅ 准确 |
| "完全没有利用 DisplayList" (1.3) | **PageCache.put() L93 已用 `page.get_displaylist().get_pixmap()`** | ⚠️ 部分准确 — 同步路径已用 DL，异步路径未用 |
| ChromaDB 锁竞争 (1.4) | `knowledge_engine.py:149` `QReadWriteLock` + `retrieve()` L208 用 `QReadLocker` | ✅ 准确 |
| QPixmap 跨线程 (2.4) | `_PageRenderTask.run()` L781-783 在工作线程创建 QPixmap | ✅ 准确（PySide6 6.11 尚可容忍，但不推荐） |

### 12.2 额外发现的 4 个问题

**A. 重复翻译请求无防护**
- `TranslationFlow.request_translation()` (translate_flow.py:57) 不检查 block_id 是否已有进行中请求
- `AIEngine.request_translation()` (ai_engine.py:847) 不检查重复，每次创建新 `_TranslationThread`
- 用户快速双击同一段落 → 多个翻译线程并发竞争

**B. 异步渲染路径未用 DisplayList**
- `_PageRenderTask.run()` (pdf_engine.py:781) 调用 `page.get_pixmap()` 而非 `page.get_displaylist().get_pixmap()`
- 每次异步渲染都重新解释 PDF 指令流，与 PageCache.put() 的 DL 路径不一致
- 修复：改为 `page.get_displaylist().get_pixmap()`

**C. 大页面渲染时序竞态**
- `_render_page()` L589-599：大页面直接设 `container._rendered = True`（无 `_full_pixmap`），然后用 `QTimer.singleShot(0, ...)` 延迟调度瓦片
- 如果 `_update_visible_pages` 在瓦片就绪前再次触发，`container.rendered` 返回 True → 跳过渲染 → paintEvent 中 TileCache 可能为空 → 灰块

**D. ServiceContainer.shutdown() 无逆序关闭**
- PDFCrop 的 shutdown() 按注册反序调用 close()
- 当前 `ServiceContainer.shutdown()` 无此逻辑
- 当前由 `MainWindow.closeEvent()` 手动管理顺序，容器单独使用时会出问题

### 12.3 P0 精化实施计划

**P0-1: _VirtualPageLayout（纯 Python 布局计算）**
- 文件：`src/ui/pdf_viewer.py`（新增类）
- 数据结构：`_VirtualPageEntry(page_num, logical_height, split_extra_height)`
- 核心方法：`page_y()`, `page_height()`, `page_range_for_viewport()`, `register_split()`, `unregister_split()`, `rebuild()`
- 替代：`_compute_page_y_offsets()` + `_get_page_height()` 的 QLayoutItem 遍历
- 关键：`page_range_for_viewport()` 用纯 Python 列表扫描（500页 < 1ms），消除 C++ 跨语言调用

**P0-2: Widget 池化**
- `load_document()` 不再创建 N 个 `_LazyPageWidget`，只存储元数据到 `_page_metas: dict[int, dict]`
- 新增 `_widget_pool: dict[int, _LazyPageWidget]` 管理活跃 widget
- `_update_visible_pages()` 变更：
  - needed ← `_vlayout.page_range_for_viewport()` + 预渲染页
  - entering ← needed - pool → 创建新 widget，insert 到 layout 正确位置
  - leaving ← pool - needed → hide + unrender，保留在 pool 复用
- 活跃 widget 数 ≤ visible_pages + 2*margin + preload ≈ 15-20 个

**P0-3: SplitWidget 适配**
- `open_split_widget()`：不再用 `_layout.indexOf()` 定位，改为基于 `_vlayout.page_y()` 找到插入点
- `_merge_segments()`：不再用 `_layout.removeWidget()`，改为基于虚拟偏移找到并移除 segment widget
- SplitWidget 始终保留在 layout 中（用户正在交互）

**P0-4: 异步渲染 DisplayList 修复**
- `_PageRenderTask.run()` 改用 `page.get_displaylist().get_pixmap()`
- 与 PageCache.put() 路径保持一致

**P0-5: 运行测试**
- 打开 100+ 页 PDF，验证打开速度、滚动性能、SplitWidget 功能

### 12.4 P1/P2 概览（P0 完成后执行）

| 优先级 | 问题 | 核心改动 | 预估工时 |
|--------|------|---------|---------|
| P1 | 缩放失效 | `zoom_multiplier` + `effective_scale/dpi` + 信号连接 | 0.5天 |
| P1 | 翻译框宽度对齐 | `_get_page_display_width()` + 缩放同步 | 0.3天 |
| P2 | 翻译框高度+CSS | `_compute_chrome_height()` + HTML margin 调小 | 0.5天 |
| P2 | 段落宽度对齐 | BBox → 显示宽度 + 水平偏移 | 0.5天 |
| P2 | 重复翻译防护 | `_pending` 去重检查 | 0.2天 |

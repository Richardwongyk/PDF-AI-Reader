"""
PDF 阅读器 —— 瓦片化渲染 + 点击段落时页面在该段落下边界裂开。
支持同页多段裂开（裂开后的上下半页各自仍可再裂开）。

瓦片化渲染 —— 借鉴 qpageview (frescobaldi/qpageview) 的 AbstractRenderer.paint():
  - paintEvent 中 QPainter 直接绘制瓦片，不创建 QLabel 小部件
  - 全页 pixmap 渲染后切片存入 TileCache（瞬时，无需后台逐瓦片渲染）
  - 后续可扩展为后台逐瓦片渲染（当前已为 TileRenderer 预留接口）
  - BlockOverlay 作为子 QWidget 自动浮于 QPainter 绘制内容之上
"""

from __future__ import annotations

import logging
import time
from collections import deque

from shiboken6 import isValid as _isValid
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from src.core.models import DocumentBlock, ParseResult, SplitMode, UIConfig
from src.core.pdf_engine import DocumentEngine
from src.infra.tile_renderer import TileRenderer, TILE_SIZE
from src.infra.tile_cache import TileKey
from src.ui.paragraph_widget import BlockOverlay
from src.ui.split_widget import SplitWidget

_logger = logging.getLogger(__name__)


class _LazyPageWidget(QWidget):
    """瓦片化延迟加载页面容器 — 借鉴 qpageview 的 QPainter 绘制模式。

    未渲染时 QPainter 绘制浅灰占位文字，渲染后 QPainter 绘制瓦片。
    BlockOverlay 作为子 QWidget 自动浮于 QPainter 绘制内容之上。
    """

    def __init__(self, page_num: int, width_px: int, height_px: int) -> None:
        super().__init__()
        self.page_num = page_num
        self._page_w = width_px
        self._page_h = height_px
        self.setFixedSize(width_px, height_px)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        self._rendered = False
        self._overlays: dict[str, BlockOverlay] = {}
        self._full_pixmap: QPixmap | None = None  # 全页 pixmap（借鉴 qpageview 单 tile 模式）
        self._tile_cache: object | None = None     # TileCache 引用，render() 时切片存入
        self._zoom: float = 1.0
        _logger.debug("_LazyPageWidget.__init__: p%d (%dx%d)", page_num, width_px, height_px)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def render(
        self,
        pixmap: QPixmap,
        blocks: list[DocumentBlock],
        scale: float,
        connect_cb: callable,
        tile_cache: object | None = None,
    ) -> None:
        """渲染页面：将全页 pixmap 切片存入 TileCache，触发 paintEvent 重绘。

        Args:
            pixmap: DocumentEngine 渲染的全页 QPixmap (2x DPR)。
            blocks: 该页的 DocumentBlock 列表。
            scale: 逻辑坐标缩放因子 (= dpi / 72.0)。
            connect_cb: 连接 BlockOverlay 信号的回调。
            tile_cache: TileCache 实例（可选，用于瓦片切片存储）。
        """
        t0 = time.perf_counter()
        self._rendered = True
        self._full_pixmap = pixmap
        self._tile_cache = tile_cache
        self._zoom = scale

        # 全页 pixmap 切片存入 TileCache（后台缓存预热，不参与显示）
        if tile_cache is not None and not pixmap.isNull():
            sliced = self._slice_pixmap_to_tiles(pixmap)
            _logger.debug("_LazyPageWidget.render: p%d 切片 %d 瓦片 → TileCache (%.1fms)",
                          self.page_num, sliced, (time.perf_counter() - t0) * 1000)

        # 触发 paintEvent → QPainter 直接绘制全页 pixmap（画质无损）
        self.update()

        # 创建 BlockOverlay（与旧版完全一致）
        for b in blocks:
            bx0, by0, bx1, by1 = b.bbox
            sx0 = int(bx0 * scale)
            sy0 = int(by0 * scale)
            sw = max(int((bx1 - bx0) * scale), 1)
            sh = max(int((by1 - by0) * scale), 1)
            if sy0 + sh <= 0 or sy0 >= self.height():
                continue
            ov = BlockOverlay(b)
            ov.setParent(self)
            ov.setGeometry(sx0, max(sy0, 0), sw,
                           max(min(sy0 + sh, self.height()) - max(sy0, 0), 1))
            ov.raise_()
            ov.show()
            connect_cb(ov)
            self._overlays[b.id] = ov

        _logger.debug("_LazyPageWidget.render: p%d 完成 (%d blocks, %.1fms)",
                      self.page_num, len(self._overlays), (time.perf_counter() - t0) * 1000)

    def unrender(self) -> list[str]:
        """释放全页 pixmap + overlay，返回被清除的 block_id 列表。"""
        self._rendered = False
        self._full_pixmap = None
        self._tile_cache = None
        self.update()  # 触发 paintEvent → 绘制占位符
        cleared = list(self._overlays.keys())
        for ov in self._overlays.values():
            ov.deleteLater()
        self._overlays.clear()
        _logger.debug("_LazyPageWidget.unrender: p%d 清除 %d 个 overlay", self.page_num, len(cleared))
        return cleared

    @property
    def rendered(self) -> bool:
        return self._rendered

    def overlay(self, block_id: str) -> BlockOverlay | None:
        return self._overlays.get(block_id)

    # ------------------------------------------------------------------
    # QPainter 绘制 — 借鉴 qpageview AbstractRenderer.paint()
    # ------------------------------------------------------------------

    def paintEvent(self, event: object) -> None:
        """使用 QPainter 绘制 — 借鉴 qpageview AbstractRenderer.paint()。

        当前：全页 pixmap 直接绘制（画质无损，1:1 像素映射）。
        后台：_TileRenderTask 逐瓦片渲染 → TileCache → tile_ready → update()。
        渐进切换：当 TileCache 中瓦片覆盖率足够高时，切换为逐瓦片绘制。
        """
        painter = QPainter(self)

        if self._rendered and self._full_pixmap is not None and not self._full_pixmap.isNull():
            painter.drawPixmap(0, 0, self._full_pixmap)
        else:
            self._draw_placeholder(painter)

        painter.end()

    def _draw_placeholder(self, painter: QPainter) -> None:
        """绘制未渲染状态的占位符。"""
        painter.fillRect(self.rect(), QColor("#e8e8e8"))
        painter.setPen(QColor("#aaa"))
        font = painter.font()
        font.setPointSize(18)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "…")

    # ------------------------------------------------------------------
    # 全页 pixmap → 瓦片切片（瞬时操作）
    # ------------------------------------------------------------------

    def _slice_pixmap_to_tiles(self, pixmap: QPixmap) -> int:
        """将全页 QPixmap 按 TileSize 网格切片，存入 TileCache。

        借鉴思路：qpageview 的 AbstractRenderer.tiles() + ImageCache.addtile()。
        但 qpageview 是在后台线程逐瓦片渲染；我们这里是先拿到全页 pixmap，
        再在 GUI 线程做切片（copy() 开销极小，< 1ms per tile）。

        Returns:
            切片的瓦片数量。
        """
        tile_px = int(TILE_SIZE * self._zoom)
        if tile_px <= 0:
            return 0

        cols = (self._page_w // tile_px) + 1
        rows = (self._page_h // tile_px) + 1
        count = 0
        dpr = pixmap.devicePixelRatio()

        for row in range(rows):
            for col in range(cols):
                key = TileKey(page_num=self.page_num, tile_x=col, tile_y=row,
                              zoom_level=self._zoom)

                # QPixmap.copy() 使用物理像素坐标
                phys_x = int(col * tile_px * dpr)
                phys_y = int(row * tile_px * dpr)
                phys_w = int(min(tile_px, self._page_w - col * tile_px) * dpr)
                phys_h = int(min(tile_px, self._page_h - row * tile_px) * dpr)

                tile_pm = pixmap.copy(phys_x, phys_y, phys_w, phys_h)
                tile_pm.setDevicePixelRatio(dpr)
                self._tile_cache.put(key, tile_pm, render_ms=0.0)
                count += 1

        return count


class PdfViewer(QScrollArea):
    block_double_clicked = Signal(str)
    block_translate_requested = Signal(str)
    block_question_requested = Signal(str)
    block_explain_requested = Signal(str)
    viewport_changed = Signal(int, int)
    split_close_requested = Signal(str)

    def __init__(self, doc_engine: DocumentEngine, config: UIConfig) -> None:
        super().__init__()
        self._doc_engine = doc_engine
        self._config = config
        # 借鉴 qpageview：widget 尺寸用逻辑 DPI，pixmap 渲染用物理 DPI
        screen = QApplication.primaryScreen()
        self._screen_dpr = screen.devicePixelRatio() if screen else 1.0
        self._logical_dpi = screen.logicalDotsPerInch() if screen else 96
        # 物理 DPI — 用于 PyMuPDF 渲染（像素 = widget逻辑 × DPR = backing store 物理像素）
        self._dpi = int(self._logical_dpi * self._screen_dpr)
        # 逻辑 DPI — 用于 widget / overlay 尺寸（与 pixmap 设置 DPR 后的逻辑尺寸匹配）
        self._scale = self._logical_dpi / 72.0
        self._all_blocks: list[DocumentBlock] = []
        self._page_segments: dict[int, list[dict]] = {}
        self._splits: dict[str, SplitWidget] = {}
        self._block_to_page: dict[str, int] = {}
        self._overlays: dict[str, BlockOverlay] = {}
        self._trans_indicators: dict[str, QWidget] = {}

        # 瓦片化渲染器（借鉴 qpageview + Syncfusion），使用屏幕物理 DPI
        self._tile_renderer = TileRenderer(dpi=self._dpi)
        self._tile_cache = self._tile_renderer.cache
        # 瓦片就绪时触发所在页面重绘（借鉴 qpageview callback 模式）
        self._tile_renderer.tile_ready.connect(self._on_tile_ready)
        _logger.info("PdfViewer: TileRenderer 就绪 (cache=%dMB max, dpi=%d)",
                     self._tile_cache._max_size / (1024 * 1024), self._dpi)

        # 方向感知预渲染（借鉴 Sioyek 趋势感知算法）
        self._scroll_history: deque[int] = deque(maxlen=3)  # 最近 3 次方向 (+1/-1)
        self._last_scroll_value: int = 0

        # 懒加载相关
        self._page_containers: dict[int, _LazyPageWidget] = {}
        self._rendered_pages: set[int] = set()
        self._split_pages: set[int] = set()
        self._viewport_timer = QTimer(self)
        self._viewport_timer.setSingleShot(True)
        self._viewport_timer.setInterval(80)
        self._viewport_timer.timeout.connect(self._update_visible_pages)

        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(10, 6, 10, 6)
        self._layout.setSpacing(0)
        self.setWidget(self._content)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundRole(QPalette.ColorRole.Base)

        # 异步渲染：连接 DocumentEngine 的完成信号
        self._doc_engine.page_rendered.connect(self._on_page_rendered_async)

        app = QApplication.instance()
        if app:
            app.setStyleSheet(app.styleSheet() + " QToolTip { color: #4a3f6b; }")

    # ── 页面段构建（始终渲染，用于裂开后的上下半页） ──

    def _build_segment_widget(
        self, pixmap: QPixmap, y0: int, y1: int, blocks: list[DocumentBlock]
    ) -> QWidget:
        """创建裁切图片 + 透明叠加层的段组件（始终渲染）。"""
        h = max(y1 - y0, 1)
        dpr = pixmap.devicePixelRatio()

        if not blocks:
            w = QWidget()
            w.setFixedSize(pixmap.width(), h)
            return w

        # QPixmap.copy() 使用物理像素坐标
        phys_y0 = int(y0 * dpr)
        phys_h = int(h * dpr)
        cropped = pixmap.copy(0, phys_y0, pixmap.width() * dpr, phys_h)
        cropped.setDevicePixelRatio(dpr)

        w = QWidget()
        w.setFixedSize(pixmap.width(), h)
        label = QLabel(w)
        label.setPixmap(cropped)
        label.move(0, 0)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        for b in blocks:
            bx0, by0, bx1, by1 = b.bbox
            sx0 = int(bx0 * self._scale)
            sy0 = int(by0 * self._scale) - y0
            sw = max(int((bx1 - bx0) * self._scale), 1)
            sh = max(int((by1 - by0) * self._scale), 1)
            if sy0 + sh <= 0 or sy0 >= h:
                continue
            ov = BlockOverlay(b)
            ov.setParent(w)
            ov.setGeometry(sx0, max(sy0, 0), sw,
                           max(min(sy0 + sh, h) - max(sy0, 0), 1))
            ov.raise_()
            ov.show()
            self._connect_overlay(ov)
        return w

    # ── 文档加载 ──

    def load_document(self, result: ParseResult) -> None:
        t0 = time.perf_counter()
        _logger.info("PdfViewer.load_document: START (pages=%d, blocks=%d)",
                     result.page_count, len(result.blocks))
        self.clear()
        self._all_blocks = result.blocks
        if not self._all_blocks:
            return

        doc = self._doc_engine.document
        self._tile_renderer.set_document(doc)

        # 按页分组
        pages: dict[int, list[DocumentBlock]] = {}
        for b in self._all_blocks:
            pages.setdefault(b.page_num, []).append(b)
            self._block_to_page[b.id] = b.page_num

        for page_num in sorted(pages.keys()):
            if doc and page_num < doc.page_count:
                rect = doc[page_num].rect
                w_px = int(rect.width * self._scale)
                h_px = int(rect.height * self._scale)
            else:
                w_px, h_px = 600, 800

            container = _LazyPageWidget(page_num, w_px, h_px)
            self._layout.addWidget(container)
            self._page_containers[page_num] = container
            self._page_segments[page_num] = [{
                "y0": 0, "y1": h_px,
                "blocks": pages[page_num], "widget": container,
            }]

        self._layout.addStretch()

        if not hasattr(self, '_scroll_connected'):
            self.verticalScrollBar().valueChanged.connect(self._on_scroll)
            self._scroll_connected = True

        QTimer.singleShot(50, self._update_visible_pages)
        _logger.info("PdfViewer.load_document: DONE (%.2fs)", time.perf_counter() - t0)

    # ── 懒加载核心 ──

    def _on_scroll(self, value: int) -> None:
        # 追踪翻页方向（借鉴 Sioyek 趋势感知）
        if value > self._last_scroll_value:
            self._scroll_history.append(1)   # 向下/向前
        elif value < self._last_scroll_value:
            self._scroll_history.append(-1)  # 向上/向后
        self._last_scroll_value = value

        self._viewport_timer.start()
        self.viewport_changed.emit(value, self.verticalScrollBar().maximum())

    def _compute_page_y_offsets(self) -> dict[int, int]:
        """计算每个页面的 Y 偏移（缓存版：仅在 layout 变化时重算）。"""
        layout_version = self._layout.count()
        if layout_version == getattr(self, '_cached_layout_version', -1):
            return getattr(self, '_cached_offsets', {})

        offsets: dict[int, int] = {}
        y = self._layout.contentsMargins().top()
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            w = item.widget()
            if w is None:
                continue
            found = False
            for page_num, segs in self._page_segments.items():
                for seg in segs:
                    if seg.get("widget") is w:
                        if page_num not in offsets:
                            offsets[page_num] = y
                        found = True
                        break
                if found:
                    break
            if not found and isinstance(w, SplitWidget):
                pass
            y += w.height()
        self._cached_layout_version = layout_version
        self._cached_offsets = offsets
        return offsets

    def _update_visible_pages(self) -> None:
        """根据滚动位置决定哪些页面需要渲染/释放。

        离开视口的页面：取消待渲染瓦片，释放 overlay。
        进入视口的页面：触发全页 pixmap 异步渲染。
        """
        t0 = time.perf_counter()
        if not self._page_containers:
            return

        scroll_y = self.verticalScrollBar().value()
        viewport_h = self.viewport().height()
        if viewport_h <= 0:
            return

        for page_num in list(self._rendered_pages):
            if page_num not in self._page_containers:
                self._rendered_pages.discard(page_num)

        offsets = self._compute_page_y_offsets()

        needed: set[int] = set()
        for page_num in self._page_containers:
            y = offsets.get(page_num, 0)
            page_h = self._get_page_height(page_num)
            page_bottom = y + page_h
            margin = viewport_h
            if page_bottom >= scroll_y - margin and y <= scroll_y + viewport_h + margin:
                needed.add(page_num)

        needed |= self._split_pages

        # ── 方向感知预渲染（借鉴 Sioyek 趋势感知算法） ──
        # 计算趋势得分：最近 3 次方向，正向+1 反向-1
        trend = sum(self._scroll_history)
        # 找到当前视口中心所在的页面
        viewport_center = scroll_y + viewport_h // 2
        current_page = 0
        for page_num, y in sorted(offsets.items()):
            if y <= viewport_center:
                current_page = page_num

        max_page = max(self._page_containers.keys()) if self._page_containers else 0
        preload_count = 4  # 预加载页数

        if trend >= 2:
            # 正向翻页趋势 → 预加载后续页
            for p in range(current_page + 1, min(max_page + 1, current_page + 1 + preload_count)):
                if p in self._page_containers:
                    needed.add(p)
            _logger.debug("PdfViewer: 正向趋势 (score=%d) → 预加载 p%d→p%d",
                          trend, current_page + 1,
                          min(max_page, current_page + preload_count))
        elif trend <= -2:
            # 反向翻页趋势 → 预加载前序页
            for p in range(max(0, current_page - preload_count), current_page):
                if p in self._page_containers:
                    needed.add(p)
            _logger.debug("PdfViewer: 反向趋势 (score=%d) → 预加载 p%d→p%d",
                          trend, max(0, current_page - preload_count), current_page - 1)

        # 离开视口 → 取消瓦片 + 释放
        for page_num in (self._rendered_pages - needed):
            if page_num in self._page_containers:
                _logger.debug("PdfViewer: p%d 离开视口，释放", page_num)
                self._tile_renderer.cancel_page(page_num)
                self._unrender_page(page_num)

        # 进入视口 → 触发全页渲染
        for page_num in (needed - self._rendered_pages):
            if page_num in self._page_containers:
                _logger.debug("PdfViewer: p%d 进入视口，触发渲染", page_num)
                self._render_page(page_num)

        if needed != self._rendered_pages:
            _logger.debug("PdfViewer: 视口更新 %s → %s",
                          sorted(self._rendered_pages), sorted(needed))

        self._rendered_pages = needed
        elapsed = (time.perf_counter() - t0) * 1000
        if elapsed > 5:  # 只记录 >5ms 的调用
            _logger.info("PdfViewer: _update_visible_pages 耗时 %.1fms (needed=%d)",
                         elapsed, len(needed))

    def _get_page_height(self, page_num: int) -> int:
        segs = self._page_segments.get(page_num, [])
        h = 0
        for seg in segs:
            w = seg.get("widget")
            if w:
                h += w.height()
        for block_id, split in self._splits.items():
            if self._block_to_page.get(block_id) == page_num:
                if split.isVisible():
                    h += split.height()
        return h

    def _render_page(self, page_num: int) -> None:
        """请求全页异步渲染 + 逐瓦片后台渲染。

        全页 pixmap 用于立即显示（回退），瓦片渲染完成后
        paintEvent 逐步切换到瓦片绘制（借鉴 qpageview 渐进过渡）。
        """
        container = self._page_containers.get(page_num)
        if container is None or container.rendered:
            return

        segs = self._page_segments.get(page_num, [])
        if not segs:
            return

        if len(segs) == 1 and segs[0].get("widget") is container:
            _logger.info("PdfViewer: _render_page p%d → 全页 + 瓦片渲染", page_num)
            # 全页 pixmap（立即显示）
            self._doc_engine.request_page_render_async(page_num, dpi=self._dpi)
            # 逐瓦片后台渲染（渐进过渡）
            page_rect = container.rect()
            self._tile_renderer.request_tiles_for_page(page_num, page_rect, self._scale)

    def _on_tile_ready(self, key: TileKey, pixmap: QPixmap) -> None:
        """瓦片渲染完成 → 触发对应页面重绘（借鉴 qpageview callback 模式）。"""
        container = self._page_containers.get(key.page_num)
        if container is not None and container.rendered:
            container.update()

    def _on_page_rendered_async(self, page_num: int, qpixmap: object) -> None:
        """全页 pixmap 渲染完成 → 切片到 TileCache → 触发 QPainter 重绘。"""
        container = self._page_containers.get(page_num)
        if container is None or container.rendered:
            return
        if not _isValid(container):
            return

        segs = self._page_segments.get(page_num, [])
        if len(segs) != 1 or segs[0].get("widget") is not container:
            return

        blocks = segs[0]["blocks"]
        pixmap = qpixmap if isinstance(qpixmap, QPixmap) else None
        if pixmap is None or pixmap.isNull():
            _logger.warning("PdfViewer: p%d pixmap 为空，跳过渲染", page_num)
            return

        # 设置正确的屏幕 DPR，使 QPainter 做 1:1 物理像素映射
        pixmap.setDevicePixelRatio(self._screen_dpr)
        t_render = time.perf_counter()
        container.render(pixmap, blocks, self._scale, self._connect_overlay,
                         tile_cache=self._tile_cache)
        render_ms = (time.perf_counter() - t_render) * 1000
        _logger.info("PdfViewer: p%d 全页 pixmap 就绪 (%dx%d, DPR=%.1f) → 切片+绘制 (%.1fms)",
                     page_num, pixmap.width(), pixmap.height(),
                     self._screen_dpr, render_ms)
        for b in blocks:
            if b.id in self._trans_indicators:
                self._set_translation_marker(b.id, True)

    def _unrender_page(self, page_num: int) -> None:
        container = self._page_containers.get(page_num)
        if container is None or not container.rendered:
            return

        for block_id, indicator in list(self._trans_indicators.items()):
            if self._block_to_page.get(block_id) == page_num:
                indicator.deleteLater()
                del self._trans_indicators[block_id]

        cleared = container.unrender()
        for block_id in cleared:
            self._overlays.pop(block_id, None)
        _logger.debug("PdfViewer: p%d unrender 完成", page_num)

    # ── 翻译指示器 ──

    def _set_translation_marker(self, block_id: str, has: bool) -> None:
        ov = self._get_overlay(block_id)
        if not ov:
            return

        old = self._trans_indicators.pop(block_id, None)
        if old and _isValid(old):
            old.deleteLater()

        if has:
            parent_w = ov.parentWidget()
            if parent_w:
                y = ov.geometry().y()
                h = max(ov.geometry().height(), 20)
                indicator = QWidget(parent_w)
                indicator.setGeometry(0, y, 3, h)
                indicator.setStyleSheet("background: #a0c4f0;")
                indicator.setToolTip("已翻译 — 双击原文展开")
                indicator.show()
                self._trans_indicators[block_id] = indicator

    def _get_overlay(self, block_id: str) -> BlockOverlay | None:
        ov = self._overlays.get(block_id)
        if ov:
            if _isValid(ov):
                return ov
            self._overlays.pop(block_id, None)
        for container in self._page_containers.values():
            ov = container.overlay(block_id)
            if ov and _isValid(ov):
                return ov
        return None

    # ── 裂缝操作 ──

    def open_split_widget(
        self, block_id: str, mode: SplitMode = SplitMode.TRANSLATION
    ) -> SplitWidget | None:
        existing = self._splits.get(block_id)
        if existing is not None:
            if existing.collapsed:
                existing.expand()
            return existing

        page_num = self._block_to_page.get(block_id)
        if page_num is None:
            return None

        if page_num not in self._rendered_pages:
            self._render_page(page_num)
            self._rendered_pages.add(page_num)

        segs = self._page_segments.get(page_num, [])
        seg_idx = -1
        for i, seg in enumerate(segs):
            if "split_id" in seg:
                continue
            for b in seg["blocks"]:
                if b.id == block_id:
                    seg_idx = i
                    break
            if seg_idx >= 0:
                break
        if seg_idx < 0:
            return None

        seg = segs[seg_idx]
        old_widget = seg["widget"]
        all_blocks = seg["blocks"]
        pixmap = self._doc_engine.get_page_pixmap(page_num, dpi=self._dpi)
        if pixmap is None:
            return None

        block = self._find_block(block_id)
        if block is None:
            return None
        cut_y = int(block.bbox[3] * self._scale) + 2
        cut_y = max(cut_y, seg["y0"] + 10)
        cut_y = min(cut_y, seg["y1"] - 10)

        above_blocks = [b for b in all_blocks if b.bbox[3] * self._scale <= cut_y + 5]
        below_blocks = [b for b in all_blocks if b.bbox[1] * self._scale >= cut_y - 5]

        block_px_h = int((block.bbox[3] - block.bbox[1]) * self._scale)
        split = SplitWidget(
            block, mode=mode, position="below",
            block_pixel_height=max(block_px_h, 60),
            page_width=pixmap.width(),
        )
        split.setFixedWidth(pixmap.width())
        split.question_submitted.connect(lambda q, bid=block_id: self._on_split_q(q, bid))
        split.translation_requested.connect(self.block_translate_requested.emit)
        split.close_requested.connect(lambda bid=block_id: self._on_clear_close(bid))

        old_idx = self._layout.indexOf(old_widget)

        if isinstance(old_widget, _LazyPageWidget):
            for b_id in list(old_widget._overlays.keys()):
                self._overlays.pop(b_id, None)
            self._page_containers.pop(page_num, None)
            for b_id in list(self._trans_indicators.keys()):
                if self._block_to_page.get(b_id) == page_num:
                    del self._trans_indicators[b_id]
        else:
            for child in old_widget.findChildren(BlockOverlay):
                self._overlays.pop(child.block_id, None)
        old_widget.hide()
        self._layout.removeWidget(old_widget)

        top_w = self._build_segment_widget(pixmap, seg["y0"], cut_y, above_blocks)
        self._layout.insertWidget(old_idx, top_w)

        self._layout.insertWidget(old_idx + 1, split)

        bot_w = self._build_segment_widget(pixmap, cut_y, seg["y1"], below_blocks)
        self._layout.insertWidget(old_idx + 2, bot_w)

        split.open(mode)
        self._splits[block_id] = split
        self._cached_layout_version = -1  # 失效 Y 偏移缓存
        _logger.info("裂缝已打开: block=%s page=%d mode=%s", block_id, page_num, mode.value)
        self._split_pages.add(page_num)
        self._set_translation_marker(block_id, True)
        old_widget.deleteLater()

        new_segs = segs[:seg_idx] + [
            {"y0": seg["y0"], "y1": cut_y, "blocks": above_blocks, "widget": top_w},
            {"split_id": block_id},
            {"y0": cut_y, "y1": seg["y1"], "blocks": below_blocks, "widget": bot_w},
        ] + segs[seg_idx + 1:]
        self._page_segments[page_num] = new_segs

        QApplication.processEvents()
        self.ensureVisible(0, split.y(), 50, 80)
        return split

    def find_split_widget(self, block_id: str) -> SplitWidget | None:
        return self._splits.get(block_id)

    def scroll_to_page(self, page_num: int) -> None:
        offsets = self._compute_page_y_offsets()
        y = offsets.get(page_num)
        if y is not None:
            self.verticalScrollBar().setValue(y)

    # ── 主题 ──

    def apply_theme_to_splits(self, theme: str) -> None:
        for split in self._splits.values():
            split.apply_theme(theme)

    # ── 清理 ──

    def clear(self) -> None:
        _logger.info("PdfViewer.clear: 开始 (%d splits, %d pages, %d tiles)",
                     len(self._splits), len(self._page_containers),
                     self._tile_cache.tile_count)
        for block_id, s in list(self._splits.items()):
            s.close()
            s.deleteLater()
        self._splits.clear()
        self._page_segments.clear()
        self._block_to_page.clear()
        self._overlays.clear()
        for ind in list(self._trans_indicators.values()):
            if _isValid(ind):
                ind.deleteLater()
        self._trans_indicators.clear()
        self._all_blocks.clear()
        self._page_containers.clear()
        self._rendered_pages.clear()
        self._split_pages.clear()
        self._tile_renderer.clear()
        self._scroll_history.clear()
        self._last_scroll_value = 0
        self._cached_layout_version = -1
        self._cached_offsets = {}
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        _logger.info("PdfViewer.clear: 完成")

    # ── Overlay 连接 ──

    def _connect_overlay(self, ov: BlockOverlay) -> None:
        self._overlays[ov.block_id] = ov
        ov.double_clicked.connect(self.block_double_clicked.emit)
        ov.translate_requested.connect(self.block_translate_requested.emit)
        ov.question_requested.connect(self.block_question_requested.emit)
        ov.explain_requested.connect(self.block_explain_requested.emit)

    def _find_block(self, block_id: str) -> DocumentBlock | None:
        for b in self._all_blocks:
            if b.id == block_id:
                return b
        return None

    # ── 内部槽 ──

    def _on_split_q(self, question: str, block_id: str) -> None:
        pass

    def _on_clear_close(self, block_id: str) -> None:
        self._set_translation_marker(block_id, False)
        s = self._splits.pop(block_id, None)
        if s is None:
            return
        page_num = self._block_to_page.get(block_id)
        if page_num is not None:
            self._merge_segments(page_num, block_id)
            has_other_splits = any(
                self._block_to_page.get(bid) == page_num
                for bid in self._splits
            )
            if not has_other_splits:
                self._split_pages.discard(page_num)
        s.close()
        s.deleteLater()
        self._cached_layout_version = -1  # 失效 Y 偏移缓存
        self.split_close_requested.emit(block_id)

    def _merge_segments(self, page_num: int, split_id: str) -> None:
        segs = self._page_segments.get(page_num, [])
        for i, seg in enumerate(segs):
            if seg.get("split_id") == split_id:
                if i > 0 and i + 1 < len(segs):
                    prev_seg = segs[i - 1]
                    next_seg = segs[i + 1]
                    pixmap = self._doc_engine.get_page_pixmap(page_num, dpi=self._dpi)
                    if pixmap:
                        merged_blocks = prev_seg["blocks"] + next_seg["blocks"]
                        merged_w = self._build_segment_widget(
                            pixmap, prev_seg["y0"], next_seg["y1"], merged_blocks
                        )
                        pw = prev_seg["widget"]
                        nw = next_seg["widget"]
                        for child in pw.findChildren(BlockOverlay) + nw.findChildren(BlockOverlay):
                            self._overlays.pop(child.block_id, None)
                        idx_p = self._layout.indexOf(pw)
                        pw.hide(); self._layout.removeWidget(pw); pw.deleteLater()
                        nw.hide(); self._layout.removeWidget(nw); nw.deleteLater()
                        self._layout.insertWidget(idx_p, merged_w)
                        new_segs = segs[:i - 1] + [{
                            "y0": prev_seg["y0"], "y1": next_seg["y1"],
                            "blocks": merged_blocks, "widget": merged_w,
                        }] + segs[i + 2:]
                        self._page_segments[page_num] = new_segs
                        self._page_containers.pop(page_num, None)
                        self._rendered_pages.discard(page_num)
                break

"""
PDF 阅读器 —— 瓦片化渲染 + 点击段落时页面在该段落下边界裂开。
支持同页多段裂开（裂开后的上下半页各自仍可再裂开）。

瓦片化渲染 —— 借鉴 qpageview (frescobaldi/qpageview) 的 AbstractRenderer.paint():
  - paintEvent 中 QPainter 直接绘制瓦片，不创建 QLabel 小部件
  - 全页 pixmap 渲染后切片存入 TileCache（瞬时，无需后台逐瓦片渲染）
  - 后续可扩展为后台逐瓦片渲染（当前已为 TileRenderer 预留接口）
  - BlockOverlay 作为子 QWidget 自动浮于 QPainter 绘制内容之上
"""

import logging
import time
from bisect import bisect_left, bisect_right
from collections.abc import Callable
from collections import deque
from dataclasses import dataclass

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


# =============================================================================
# _VirtualPageLayout — 纯 Python 虚拟页面布局
# =============================================================================
# 借鉴 SumatraPDF 零 Widget 架构：页面位置计算不应依赖 QWidget 树。
# 用纯 Python 数据结构替代 QLayoutItem 遍历，消除跨 C++ 边界的 O(n) 调用。

@dataclass
class _VirtualPageEntry:
    page_num: int
    logical_height: float
    split_extra_height: float = 0.0


class _VirtualPageLayout:
    """纯 Python 虚拟页面布局。

    替代 _compute_page_y_offsets() + _get_page_height() 中的 QLayoutItem 遍历。
    对 500 页文档，所有操作 < 1ms（纯 Python 列表扫描，无 C++ 跨语言调用）。
    """

    def __init__(self, page_heights: dict[int, float]) -> None:
        self._entries: list[_VirtualPageEntry] = []
        self._page_index: dict[int, int] = {}
        self._offsets: dict[int, float] = {}
        self._start_offsets: list[float] = []
        self._end_offsets: list[float] = []
        self._total_height: float = 0.0
        self._dirty: bool = True
        for pn, h in sorted(page_heights.items()):
            idx = len(self._entries)
            self._entries.append(_VirtualPageEntry(pn, h))
            self._page_index[pn] = idx
        self._recalc()

    def _recalc(self) -> None:
        y = 0.0
        self._offsets.clear()
        self._start_offsets.clear()
        self._end_offsets.clear()
        for entry in self._entries:
            self._offsets[entry.page_num] = y
            self._start_offsets.append(y)
            y += entry.logical_height + entry.split_extra_height
            self._end_offsets.append(y)
        self._total_height = y
        self._dirty = False

    @property
    def total_height(self) -> float:
        if self._dirty:
            self._recalc()
        return self._total_height

    def page_y(self, page_num: int) -> float:
        if self._dirty:
            self._recalc()
        return self._offsets.get(page_num, 0.0)

    def page_height(self, page_num: int) -> float:
        idx = self._page_index.get(page_num)
        if idx is not None:
            e = self._entries[idx]
            return e.logical_height + e.split_extra_height
        return 0.0

    def register_split(self, page_num: int, extra_height: float) -> None:
        idx = self._page_index.get(page_num)
        if idx is not None:
            self._entries[idx].split_extra_height += extra_height
            self._dirty = True

    def set_split_extra(self, page_num: int, extra_height: float) -> None:
        idx = self._page_index.get(page_num)
        if idx is not None:
            self._entries[idx].split_extra_height = max(0.0, extra_height)
            self._dirty = True

    def unregister_split(self, page_num: int, extra_height: float) -> None:
        idx = self._page_index.get(page_num)
        if idx is not None:
            self._entries[idx].split_extra_height = max(
                0.0, self._entries[idx].split_extra_height - extra_height
            )
            self._dirty = True

    def rebuild(self, page_heights: dict[int, float]) -> None:
        """重建布局（缩放后页面尺寸变化时调用）。"""
        split_extras = {
            entry.page_num: entry.split_extra_height
            for entry in self._entries
            if entry.split_extra_height > 0
        }
        self._entries.clear()
        self._page_index.clear()
        self._offsets.clear()
        self._start_offsets.clear()
        self._end_offsets.clear()
        for pn, h in sorted(page_heights.items()):
            idx = len(self._entries)
            self._entries.append(_VirtualPageEntry(pn, h, split_extras.get(pn, 0.0)))
            self._page_index[pn] = idx
        self._dirty = True

    def page_at_y(self, y: float) -> int | None:
        """返回指定纵向位置所在页面，使用二分定位。"""
        if self._dirty:
            self._recalc()
        if not self._entries:
            return None
        idx = bisect_right(self._start_offsets, y) - 1
        if idx < 0:
            return self._entries[0].page_num
        if idx >= len(self._entries):
            return self._entries[-1].page_num
        return self._entries[idx].page_num

    def page_range_for_viewport(
        self, scroll_y: float, viewport_h: float, margin: float = 0.0
    ) -> list[int]:
        """返回视口内（含 margin）的页面列表，按页码排序。"""
        if self._dirty:
            self._recalc()
        lo = scroll_y - margin
        hi = scroll_y + viewport_h + margin
        start_idx = bisect_right(self._end_offsets, lo)
        end_idx = bisect_left(self._start_offsets, hi)
        if end_idx < start_idx:
            return []
        return [entry.page_num for entry in self._entries[start_idx:end_idx]]


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
        connect_cb: Callable[[BlockOverlay], None],
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

        # Small pages are drawn directly from the full pixmap. Pre-slicing them
        # into TileCache only adds UI-thread work during scroll/zoom storms.
        if tile_cache is not None and self._should_use_tiles(pixmap):
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

    def _should_use_tiles(self, pixmap: QPixmap) -> bool:
        if pixmap.isNull():
            return False
        phys_w = pixmap.width() * pixmap.devicePixelRatio()
        phys_h = pixmap.height() * pixmap.devicePixelRatio()
        return max(phys_w, phys_h) >= 4096

    def _should_paint_tiles(self, pixmap: QPixmap) -> bool:
        if pixmap.isNull():
            return self._page_w >= 4096 or self._page_h >= 4096
        phys_w = pixmap.width() * pixmap.devicePixelRatio()
        phys_h = pixmap.height() * pixmap.devicePixelRatio()
        return max(phys_w, phys_h, self._page_w, self._page_h) >= 4096

    # ------------------------------------------------------------------
    # QPainter 绘制 — 借鉴 qpageview AbstractRenderer.paint()
    # ------------------------------------------------------------------

    def paintEvent(self, event: object) -> None:
        """混合绘制策略 —— 借鉴 qpageview AbstractRenderer.paint()。

        - 全页 pixmap < 4096 物理像素 → 直接绘制（快速，画质无损）
        - 全页 pixmap ≥ 4096 物理像素 → 瓦片网格绘制（避免 GPU 纹理溢出）
        - 全页 pixmap 未就绪 → 尝试瓦片缓存 → 灰色占位
        """
        painter = QPainter(self)

        if not self._rendered:
            self._draw_placeholder(painter)
            painter.end()
            return

        pix = self._full_pixmap
        if pix is not None and not pix.isNull():
            if not self._should_paint_tiles(pix):
                # 小页面 → 全页绘制（快速路径）
                self._draw_full_pixmap(painter, pix)
                painter.end()
                return

        # 大页面或无全页 pixmap → 瓦片路径
        if self._tile_cache is not None and self._zoom > 0:
            tile_px = int(TILE_SIZE * self._zoom)
            if tile_px > 0 and (self._page_w > tile_px or self._page_h > tile_px):
                self._paint_tiles(painter, tile_px)
                painter.end()
                return

        # 最终回退
        if pix is not None and not pix.isNull():
            self._draw_full_pixmap(painter, pix)
        else:
            self._draw_placeholder(painter)
        painter.end()

    def _draw_full_pixmap(self, painter: QPainter, pixmap: QPixmap) -> None:
        if pixmap.width() == self.width() and pixmap.height() == self.height():
            painter.drawPixmap(0, 0, pixmap)
            return
        painter.drawPixmap(self.rect(), pixmap)

    def _paint_tiles(self, painter: QPainter, tile_px: int) -> None:
        """绘制瓦片网格 —— 借鉴 qpageview info() + paint()。

        对每个瓦片：
        1. 查 TileCache → 命中 → painter.drawPixmap() 直接绘制
        2. TileCache 未命中但有全页 pixmap → 保持已绘制的整页 fallback
        3. 全页 pixmap 也未就绪 → 灰色占位
        """
        has_full_fallback = self._full_pixmap is not None and not self._full_pixmap.isNull()
        if has_full_fallback:
            self._draw_full_pixmap(painter, self._full_pixmap)

        cols = (self._page_w // tile_px) + 1
        rows = (self._page_h // tile_px) + 1
        drawn, cached, fallback = 0, 0, 1 if has_full_fallback else 0

        for row in range(rows):
            for col in range(cols):
                key = TileKey(page_num=self.page_num, tile_x=col, tile_y=row,
                              zoom_level=self._zoom)
                x = col * tile_px
                y = row * tile_px
                w = min(tile_px, self._page_w - x)
                h = min(tile_px, self._page_h - y)

                tile_pm = self._tile_cache.get(key)
                if tile_pm is not None:
                    painter.drawPixmap(x, y, w, h, tile_pm)
                    cached += 1
                elif not has_full_fallback:
                    painter.fillRect(x, y, w, h, QColor("#e8e8e8"))

                drawn += 1

        if drawn > 0:
            _logger.info("_LazyPageWidget._paint_tiles: p%d 绘制 %d 瓦片 %dx%d (缓存:%d 背景:%d)",
                         self.page_num, drawn, cols, rows, cached, fallback)

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
        # 基础值（常量，不变）
        self._base_scale = self._logical_dpi / 72.0
        self._base_dpi = int(self._logical_dpi * self._screen_dpr)
        # 缩放倍数
        self._zoom_multiplier: float = 1.0
        self._scale = self._base_scale * self._zoom_multiplier
        self._dpi = int(self._base_dpi * self._zoom_multiplier)
        self._all_blocks: list[DocumentBlock] = []
        self._page_segments: dict[int, list[dict]] = {}
        self._splits: dict[str, SplitWidget] = {}
        self._page_rects_pt: dict[int, tuple[float, float]] = {}
        self._block_to_page: dict[str, int] = {}
        self._blocks_by_id: dict[str, DocumentBlock] = {}
        self._parsed_block_pages: set[int] = set()
        self._requested_block_pages: set[int] = set()
        self._overlays: dict[str, BlockOverlay] = {}
        self._trans_indicators: dict[str, QWidget] = {}
        self._evidence_highlight: QWidget | None = None

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

        # 虚拟布局 + Widget 池化（借鉴 SumatraPDF 零 Widget 架构）
        self._vlayout: _VirtualPageLayout | None = None
        self._widget_pool: dict[int, _LazyPageWidget] = {}  # page_num → widget（含隐藏的）
        self._page_metas: dict[int, dict] = {}  # page_num → {width, height, blocks}
        self._active_pages: set[int] = set()
        self._precise_render_pending: set[int] = set()
        self._widget_page_map: dict[QWidget, int] = {}

        # 懒加载相关
        self._page_containers: dict[int, _LazyPageWidget] = {}
        self._rendered_pages: set[int] = set()
        self._split_pages: set[int] = set()
        self._precise_render_timer = QTimer(self)
        self._precise_render_timer.setSingleShot(True)
        self._precise_render_timer.setInterval(180)
        self._precise_render_timer.timeout.connect(self._request_precise_renders)
        self._pending_split_rerenders: set[int] = set()
        self._split_rerender_timer = QTimer(self)
        self._split_rerender_timer.setSingleShot(True)
        self._split_rerender_timer.setInterval(180)
        self._split_rerender_timer.timeout.connect(self._request_pending_split_rerenders)
        self._viewport_timer = QTimer(self)
        self._viewport_timer.setSingleShot(True)
        self._viewport_timer.setInterval(80)
        self._viewport_timer.timeout.connect(self._update_visible_pages)

        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(10, 6, 10, 6)
        self._layout.setSpacing(0)

        # 顶部/底部 spacer：撑出全部页面的总高度，使滚动条范围 = 文档总长
        self._top_spacer = QWidget()
        self._top_spacer.setFixedHeight(0)
        self._layout.addWidget(self._top_spacer)

        self._bottom_spacer = QWidget()
        self._layout.addWidget(self._bottom_spacer)

        self._layout.addStretch()

        self.setWidget(self._content)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setBackgroundRole(QPalette.ColorRole.Base)

        # 异步渲染：连接 DocumentEngine 的完成信号
        self._doc_engine.page_rendered.connect(self._on_page_rendered_async)

        app = QApplication.instance()
        if app and "PDF_AI_READER_TOOLTIP_STYLE" not in app.styleSheet():
            app.setStyleSheet(
                app.styleSheet()
                + """
                /* PDF_AI_READER_TOOLTIP_STYLE */
                QToolTip {
                    color: #ffffff;
                    background-color: #111827;
                    border: 1px solid #f8fafc;
                    padding: 4px 6px;
                }
                """
            )

    # ── 页面段构建（始终渲染，用于裂开后的上下半页） ──

    def _build_segment_widget(
        self,
        pixmap: QPixmap,
        y0: int,
        y1: int,
        blocks: list[DocumentBlock],
        page_num: int | None = None,
    ) -> QWidget:
        """创建裁切图片 + 透明叠加层的段组件（始终渲染）。"""
        h = max(y1 - y0, 1)
        dpr = pixmap.devicePixelRatio()
        segment_page = page_num if page_num is not None else self._segment_page_from_blocks(blocks)

        if not blocks:
            w = QWidget()
            w.setFixedSize(pixmap.width(), h)
            if segment_page is not None:
                self._remember_widget_page(w, segment_page)
            return w

        # QPixmap.copy() 使用物理像素坐标；这是翻译裂缝的正确性路径。
        phys_y0 = int(y0 * dpr)
        phys_h = int(h * dpr)
        cropped = pixmap.copy(0, phys_y0, int(pixmap.width() * dpr), phys_h)
        cropped.setDevicePixelRatio(dpr)

        w = QWidget()
        w.setFixedSize(pixmap.width(), h)
        if segment_page is not None:
            self._remember_widget_page(w, segment_page)
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

    def _segment_page_from_blocks(self, blocks: list[DocumentBlock]) -> int | None:
        return blocks[0].page_num if blocks else None

    # ── 文档加载 ──

    def load_document(self, result: ParseResult) -> None:
        t0 = time.perf_counter()
        _logger.info("PdfViewer.load_document: START (pages=%d, blocks=%d)",
                     result.page_count, len(result.blocks))
        self.clear()
        self._all_blocks = list(result.blocks)

        doc = self._doc_engine.document
        self._tile_renderer.set_document(doc)

        # 按页分组
        pages: dict[int, list[DocumentBlock]] = {}
        for b in self._all_blocks:
            pages.setdefault(b.page_num, []).append(b)
            self._block_to_page[b.id] = b.page_num
            self._blocks_by_id[b.id] = b
        self._parsed_block_pages = set(getattr(result, "parsed_pages", []))
        if not self._parsed_block_pages:
            self._parsed_block_pages = set(pages.keys())

        # 构建虚拟布局（纯 Python，不创建 QWidget）
        page_heights: dict[int, float] = {}
        page_count = result.page_count
        if doc:
            page_count = doc.page_count
        for page_num in range(page_count):
            if doc and page_num < doc.page_count:
                rect = doc[page_num].rect
                rect_w_pt = float(rect.width)
                rect_h_pt = float(rect.height)
            else:
                rect_w_pt, rect_h_pt = 600.0 / self._scale, 800.0 / self._scale
            self._page_rects_pt[page_num] = (rect_w_pt, rect_h_pt)
            w_px = int(rect_w_pt * self._scale)
            h_px = int(rect_h_pt * self._scale)
            page_blocks = pages.get(page_num, [])
            page_heights[page_num] = float(h_px)
            self._page_metas[page_num] = {
                "width": w_px, "height": h_px, "blocks": page_blocks,
                "blocks_ready": page_num in self._parsed_block_pages,
            }
            self._page_segments[page_num] = [{
                "y0": 0, "y1": h_px,
                "y0_pt": 0.0, "y1_pt": h_px / self._scale if self._scale else 0.0,
                "blocks": page_blocks, "widget": None,  # 按需创建
            }]

        self._vlayout = _VirtualPageLayout(page_heights)
        self._sync_content_size(max(self.viewport().height(), 1))
        _logger.info("PdfViewer.load_document: _VirtualPageLayout 已构建 (%d pages, total_h=%.0f)",
                     len(page_heights), self._vlayout.total_height)

        if not hasattr(self, '_scroll_connected'):
            self.verticalScrollBar().valueChanged.connect(self._on_scroll)
            self._scroll_connected = True

        self._request_page_blocks_around(0, radius=4)
        QTimer.singleShot(50, self._update_visible_pages)
        _logger.info("PdfViewer.load_document: DONE (%.2fs, %d pages, 0 widgets created)",
                     time.perf_counter() - t0, len(page_heights))

    def update_page_blocks(self, page_num: int, blocks: list[DocumentBlock]) -> None:
        """后台解析补齐某页 blocks 后刷新交互热区。"""
        meta = self._page_metas.get(page_num)
        if meta is None:
            return

        self._requested_block_pages.discard(page_num)
        page_blocks = list(blocks)
        meta["blocks"] = page_blocks
        meta["blocks_ready"] = True
        self._parsed_block_pages.add(page_num)
        known = set(self._blocks_by_id)
        for block in page_blocks:
            self._block_to_page[block.id] = page_num
            if block.id not in known:
                self._all_blocks.append(block)
                known.add(block.id)
            self._blocks_by_id[block.id] = block

        segs = self._page_segments.get(page_num, [])
        if (
            len(segs) == 1
            and "split_id" not in segs[0]
        ):
            segs[0]["blocks"] = page_blocks
        else:
            # 已有裂缝时不重建段结构，避免打断当前交互。
            return

        container = self._page_containers.get(page_num)
        if container is not None and container.rendered:
            self._refresh_container_overlays(container, page_blocks)
            _logger.info("PdfViewer: p%d blocks 已补齐，刷新 %d 个 overlay",
                         page_num, len(container._overlays))
            return

        pooled = self._widget_pool.get(page_num)
        if pooled is not None and pooled.rendered:
            self._refresh_container_overlays(pooled, page_blocks)

    def _refresh_container_overlays(
        self, container: _LazyPageWidget, blocks: list[DocumentBlock]
    ) -> None:
        """重建页面 widget 上的 BlockOverlay。"""
        keep: set[str] = set()
        for block in blocks:
            bx0, by0, bx1, by1 = block.bbox
            sx0 = int(bx0 * self._scale)
            sy0 = int(by0 * self._scale)
            sw = max(int((bx1 - bx0) * self._scale), 1)
            sh = max(int((by1 - by0) * self._scale), 1)
            if sy0 + sh <= 0 or sy0 >= container.height():
                continue
            keep.add(block.id)
            ov = container._overlays.get(block.id)
            if ov is not None and _isValid(ov) and ov.block is not block:
                self._overlays.pop(block.id, None)
                ov.deleteLater()
                ov = None
            if ov is None or not _isValid(ov):
                ov = BlockOverlay(block)
                ov.setParent(container)
                self._connect_overlay(ov)
            ov.setGeometry(
                sx0,
                max(sy0, 0),
                sw,
                max(min(sy0 + sh, container.height()) - max(sy0, 0), 1),
            )
            ov.raise_()
            ov.show()
            container._overlays[block.id] = ov
            self._overlays[block.id] = ov

        for block_id, ov in list(container._overlays.items()):
            if block_id in keep:
                continue
            self._overlays.pop(block_id, None)
            if _isValid(ov):
                ov.deleteLater()
            container._overlays.pop(block_id, None)

    def _refresh_segment_overlays(
        self,
        widget: QWidget,
        blocks: list[DocumentBlock],
        y0: int,
        height: int,
    ) -> None:
        """复用裂缝段内 overlay，只更新几何，避免缩放时批量重建 QWidget。"""
        existing = {
            child.block_id: child
            for child in widget.findChildren(BlockOverlay)
            if _isValid(child)
        }
        keep: set[str] = set()
        seg_h = max(height, 1)
        for block in blocks:
            bx0, by0, bx1, by1 = block.bbox
            sx0 = int(bx0 * self._scale)
            sy0 = int(by0 * self._scale) - y0
            sw = max(int((bx1 - bx0) * self._scale), 1)
            sh = max(int((by1 - by0) * self._scale), 1)
            if sy0 + sh <= 0 or sy0 >= seg_h:
                continue
            keep.add(block.id)
            ov = existing.get(block.id)
            if ov is not None and ov.block is not block:
                self._overlays.pop(block.id, None)
                ov.deleteLater()
                ov = None
            if ov is None or not _isValid(ov):
                ov = BlockOverlay(block)
                ov.setParent(widget)
                self._connect_overlay(ov)
            ov.setGeometry(
                sx0,
                max(sy0, 0),
                sw,
                max(min(sy0 + sh, seg_h) - max(sy0, 0), 1),
            )
            ov.raise_()
            ov.show()
            self._overlays[block.id] = ov

        for block_id, ov in existing.items():
            if block_id in keep:
                continue
            self._overlays.pop(block_id, None)
            if _isValid(ov):
                ov.deleteLater()

    def _clear_container_overlays(self, container: _LazyPageWidget) -> None:
        """清理页面 widget 上的 BlockOverlay，并同步全局索引。"""
        for block_id, ov in list(container._overlays.items()):
            self._overlays.pop(block_id, None)
            if _isValid(ov):
                ov.deleteLater()
        container._overlays.clear()

    # ── 懒加载核心（Widget 池化 + 虚拟布局）──

    def _on_scroll(self, value: int) -> None:
        # 追踪翻页方向（借鉴 Sioyek 趋势感知）
        if value > self._last_scroll_value:
            self._scroll_history.append(1)   # 向下/向前
        elif value < self._last_scroll_value:
            self._scroll_history.append(-1)  # 向上/向后
        self._last_scroll_value = value

        if self._precise_render_pending:
            self._precise_render_timer.start(350)
        if self._pending_split_rerenders:
            self._split_rerender_timer.start(350)
        # Keep page widgets in the layout immediately during real wheel scrolls.
        # Deferring this leaves the viewport over spacer/background between events.
        self._update_visible_pages()
        self._viewport_timer.start()
        self.viewport_changed.emit(value, self.verticalScrollBar().maximum())

    def _compute_page_y_offsets(self) -> dict[int, int]:
        """计算每个页面的 Y 偏移（委托 _VirtualPageLayout，纯 Python ~0.1ms）。"""
        if self._vlayout is None:
            return {}
        if self._vlayout._dirty:
            self._vlayout._recalc()
        return {pn: int(self._vlayout.page_y(pn)) for pn in self._page_metas}

    def _get_page_height(self, page_num: int) -> int:
        """获取页面高度（委托 _VirtualPageLayout）。"""
        if self._vlayout is None:
            return 0
        return int(self._vlayout.page_height(page_num))

    # ── Widget 池化辅助方法（QVBoxLayout + spacer 撑高）──

    def _layout_index_for_page(self, page_num: int) -> int:
        """在 layout 中查找 page_num 应插入的索引（跳过 top_spacer[0]）。"""
        idx = 1  # 始终在 top_spacer 之后
        for i in range(1, self._layout.count()):
            item = self._layout.itemAt(i)
            w = item.widget()
            if w is None or w is self._bottom_spacer:
                continue
            widget_page = self._widget_page_map.get(w)
            if widget_page is None:
                widget_page = getattr(w, "_page_num", None)
            if widget_page is not None and widget_page < page_num:
                idx = i + 1
        return idx

    def _remember_widget_page(self, widget: QWidget, page_num: int) -> None:
        widget._page_num = page_num
        self._widget_page_map[widget] = page_num

    def _forget_widget_page(self, widget: QWidget | None) -> None:
        if widget is not None:
            self._widget_page_map.pop(widget, None)

    def _ensure_page_widget(self, page_num: int) -> _LazyPageWidget | None:
        """确保 page_num 的 _LazyPageWidget 存在且位于 layout 正确位置。"""
        if page_num in self._split_pages:
            return None

        meta = self._page_metas.get(page_num)
        if meta is None:
            return None

        container = self._widget_pool.get(page_num)
        if container is not None:
            if container.width() != meta["width"] or container.height() != meta["height"]:
                container.setFixedSize(meta["width"], meta["height"])
            if container.rendered and abs(container._zoom - self._scale) > 0.001:
                cleared = container.unrender()
                for block_id in cleared:
                    self._overlays.pop(block_id, None)
                self._rendered_pages.discard(page_num)
                self._precise_render_pending.discard(page_num)
            if not container.isVisible():
                insert_idx = self._layout_index_for_page(page_num)
                self._layout.insertWidget(insert_idx, container)
                container.show()
                _logger.debug("PdfViewer: p%d 从池复用 → layout[%d]", page_num, insert_idx)
            self._page_containers[page_num] = container
            self._active_pages.add(page_num)
            self._remember_widget_page(container, page_num)
            for seg in self._page_segments.get(page_num, []):
                if seg.get("widget") is None and "split_id" not in seg:
                    seg["widget"] = container
                    break
            if not container.rendered:
                container._rendered = False
            return container

        container = _LazyPageWidget(page_num, meta["width"], meta["height"])
        self._widget_pool[page_num] = container
        self._page_containers[page_num] = container
        self._active_pages.add(page_num)
        self._remember_widget_page(container, page_num)
        for seg in self._page_segments.get(page_num, []):
            if seg.get("widget") is None and "split_id" not in seg:
                seg["widget"] = container
                break

        insert_idx = self._layout_index_for_page(page_num)
        self._layout.insertWidget(insert_idx, container)
        _logger.debug("PdfViewer: p%d 新 widget → layout[%d] (%dx%d)",
                     page_num, insert_idx, meta["width"], meta["height"])
        return container

    def _recycle_page_widget(self, page_num: int) -> None:
        """隐藏并卸载页面 widget，保留在池中。"""
        if page_num in self._split_pages:
            return

        container = self._widget_pool.get(page_num)
        if container is None:
            self._remove_segment_widgets(page_num)
            return

        self._tile_renderer.cancel_page(page_num)

        if container.rendered:
            cleared = container.unrender()
            for block_id in cleared:
                self._overlays.pop(block_id, None)

        for block_id, indicator in list(self._trans_indicators.items()):
            if self._block_to_page.get(block_id) == page_num:
                indicator.deleteLater()
                del self._trans_indicators[block_id]

        container.hide()
        self._layout.removeWidget(container)
        self._forget_widget_page(container)
        self._rendered_pages.discard(page_num)
        self._active_pages.discard(page_num)
        self._precise_render_pending.discard(page_num)
        self._page_containers.pop(page_num, None)
        for seg in self._page_segments.get(page_num, []):
            if seg.get("widget") is container:
                seg["widget"] = None

        _logger.debug("PdfViewer: p%d 回收 → 池中隐藏 (pool=%d)",
                     page_num, len(self._widget_pool))

    def _hide_page_from_layout(self, page_num: int) -> None:
        """立即从 layout 移除页面 widget（保留 pixmap 在池中以便快速复用）。"""
        container = self._widget_pool.get(page_num)
        if container is None:
            self._remove_segment_widgets(page_num)
            return
        container.hide()
        self._layout.removeWidget(container)
        self._forget_widget_page(container)
        self._active_pages.discard(page_num)
        self._page_containers.pop(page_num, None)
        for seg in self._page_segments.get(page_num, []):
            if seg.get("widget") is container:
                seg["widget"] = None
        _logger.debug("PdfViewer: p%d 移出 layout (保留 pixmap)", page_num)

    def _remove_segment_widgets(self, page_num: int) -> None:
        """移除裂缝段类页面 widget。用于关闭裂缝后的回收兼容路径。"""
        for seg in self._page_segments.get(page_num, []):
            if "split_id" in seg:
                continue
            widget = seg.get("widget")
            if widget is None:
                continue
            for child in widget.findChildren(BlockOverlay):
                self._overlays.pop(child.block_id, None)
                if _isValid(child):
                    child.deleteLater()
            self._forget_widget_page(widget)
            widget.hide()
            self._layout.removeWidget(widget)
            widget.deleteLater()
            seg["widget"] = None
        self._active_pages.discard(page_num)
        self._rendered_pages.discard(page_num)
        self._precise_render_pending.discard(page_num)
        self._page_containers.pop(page_num, None)
        _logger.debug("PdfViewer: p%d 移出 layout (segment widgets)", page_num)

    def _unrender_pooled_page(self, page_num: int) -> None:
        """释放池中页面的 pixmap 和 overlay（5s 冷却后调用）。"""
        container = self._widget_pool.get(page_num)
        if container is None:
            return
        self._tile_renderer.cancel_page(page_num)
        if container.rendered:
            cleared = container.unrender()
            for block_id in cleared:
                self._overlays.pop(block_id, None)
        self._rendered_pages.discard(page_num)
        self._precise_render_pending.discard(page_num)
        for block_id, indicator in list(self._trans_indicators.items()):
            if self._block_to_page.get(block_id) == page_num:
                indicator.deleteLater()
                del self._trans_indicators[block_id]
        _logger.debug("PdfViewer: p%d 释放 pixmap", page_num)

    def _adjust_spacers(self, needed_pages: set[int]) -> None:
        """根据当前可见页面调整 spacer 高度，使 layout 总高 = _vlayout 总高。

        这个布局模型只有 top/bottom 两个 spacer，因此传入页面必须是
        视口附近的连续区间。远处已打开的裂缝页要隐藏保留状态，不能混入
        needed_pages，否则中间页面高度会从真实 QWidget 布局里消失。
        """
        if not needed_pages or self._vlayout is None:
            return
        sorted_pages = sorted(needed_pages)
        first_page = sorted_pages[0]
        top_h = int(self._vlayout.page_y(first_page))
        self._top_spacer.setFixedHeight(max(0, top_h))

        last_page = sorted_pages[-1]
        last_bottom = self._vlayout.page_y(last_page) + self._vlayout.page_height(last_page)
        bottom_h = int(self._vlayout.total_height - last_bottom)
        self._bottom_spacer.setFixedHeight(max(0, bottom_h))
        self._sync_content_size(max(self.viewport().height(), 1))

    def _capture_scroll_anchor(self) -> tuple[int | None, float]:
        """Capture the page-relative top-of-viewport position before layout changes."""
        if self._vlayout is None:
            return (None, 0.0)
        scroll_y = float(self.verticalScrollBar().value())
        page_num = self._vlayout.page_at_y(scroll_y)
        if page_num is None:
            return (None, scroll_y)
        return (page_num, scroll_y - self._vlayout.page_y(page_num))

    def _restore_scroll_anchor(self, anchor: tuple[int | None, float]) -> None:
        """Restore the same virtual document position after split/layout height changes."""
        if self._vlayout is None:
            return
        page_num, offset = anchor
        if page_num is None:
            return
        viewport_h = max(self.viewport().height(), 1)
        self._sync_scroll_range(viewport_h)
        target = int(self._vlayout.page_y(page_num) + offset)
        sb = self.verticalScrollBar()
        sb.setValue(max(0, min(target, sb.maximum())))

    def _hide_split_page_from_layout(self, page_num: int) -> None:
        """Remove an offscreen split page's widgets while preserving SplitWidget state."""
        segs = self._page_segments.get(page_num, [])
        for seg in segs:
            if "split_id" in seg:
                widget = self._splits.get(seg["split_id"])
            else:
                widget = seg.get("widget")
            if widget is None:
                continue
            if self._layout.indexOf(widget) >= 0:
                self._layout.removeWidget(widget)
            widget.hide()
            self._forget_widget_page(widget)
        self._active_pages.discard(page_num)
        self._page_containers.pop(page_num, None)
        self._precise_render_pending.discard(page_num)

    def _is_split_page_in_layout(self, page_num: int) -> bool:
        """Return whether any widget for a split page is currently mounted."""
        for seg in self._page_segments.get(page_num, []):
            if "split_id" in seg:
                widget = self._splits.get(seg["split_id"])
            else:
                widget = seg.get("widget")
            if widget is not None and self._layout.indexOf(widget) >= 0:
                return True
        return False

    def _show_split_page_in_layout(self, page_num: int) -> None:
        """Restore a previously hidden split page into the QWidget layout."""
        if page_num in self._pending_split_rerenders:
            pixmap = self._fallback_pixmap_for_page(page_num)
            if pixmap is not None and not pixmap.isNull():
                self._rebuild_split_segments_from_pixmap(
                    page_num, pixmap, only_if_in_layout=False)
                self._pending_split_rerenders.discard(page_num)
        segs = self._page_segments.get(page_num, [])
        if not segs:
            return
        insert_idx = self._layout_index_for_page(page_num)
        restored = False
        for seg in segs:
            if "split_id" in seg:
                widget = self._splits.get(seg["split_id"])
            else:
                widget = seg.get("widget")
            if widget is None:
                continue
            current_idx = self._layout.indexOf(widget)
            if current_idx < 0:
                self._layout.insertWidget(insert_idx, widget)
                insert_idx += 1
            else:
                insert_idx = current_idx + 1
            widget.show()
            self._remember_widget_page(widget, page_num)
            restored = True
        if restored:
            self._active_pages.add(page_num)
            self._rendered_pages.add(page_num)

    # ── 视口更新 ──

    def _update_visible_pages(self) -> None:
        """根据滚动位置 + 虚拟布局决定哪些页面需要渲染/回收。

        核心优化：
        - 用 _VirtualPageLayout 替代 QLayoutItem 遍历（纯 Python ~0.1ms）
        - Widget 池化：仅视口内 + margin 的页面持有活跃 widget（≤15 个）
        - 离开视口的页面隐藏并回收，保留 widget 对象复用
        """
        t0 = time.perf_counter()
        if not self._page_metas or self._vlayout is None:
            return

        scroll_y = self.verticalScrollBar().value()
        viewport_h = self.viewport().height()
        if viewport_h <= 0:
            return

        visible_now: set[int] = set(
            self._vlayout.page_range_for_viewport(float(scroll_y), float(viewport_h), 0.0)
        )
        # 保留足够的真实页面 widget。PDF 阅读器滚动时中间页必须可见，
        # 不能为了减少 widget 数量让视口落到纯背景或占位上。
        margin = viewport_h * 2.0
        needed: set[int] = set(
            self._vlayout.page_range_for_viewport(float(scroll_y), float(viewport_h), float(margin))
        )
        needed |= visible_now

        # ── 方向感知预渲染（借鉴 Sioyek 趋势感知算法）──
        trend = sum(self._scroll_history)
        viewport_center = scroll_y + viewport_h // 2
        current_page = self._vlayout.page_at_y(float(viewport_center))
        if current_page is None:
            current_page = 0
        max_page = max(self._page_metas.keys()) if self._page_metas else 0
        preload_count = 3 if self._zoom_multiplier > 1.0 else 4

        if trend >= 2:
            for p in range(current_page + 1, min(max_page + 1, current_page + 1 + preload_count)):
                if p in self._page_metas:
                    needed.add(p)
                    self._request_page_blocks(p)
        elif trend <= -2:
            for p in range(max(0, current_page - preload_count), current_page):
                if p in self._page_metas:
                    needed.add(p)
                    self._request_page_blocks(p)

        # ── 进入视口：创建/复用 widget → 请求后台渲染 ──
        for page_num in sorted(needed - self._active_pages):
            if page_num not in self._page_metas:
                continue
            if page_num in self._split_pages:
                self._show_split_page_in_layout(page_num)
                continue
            _logger.info("PdfViewer: p%d 进入视口，触发渲染", page_num)
            self._request_page_blocks(page_num)
            container = self._ensure_page_widget(page_num)
            if container is not None:
                self._render_page(page_num)

        # 已在视口内但 blocks 仍为空的页面，也持续请求解析。
        for page_num in sorted(needed):
            if page_num in self._page_metas and not self._page_metas[page_num].get("blocks_ready", False):
                self._request_page_blocks(page_num)

        # ── 离开视口：立即从 layout 移除（保持 spacer 计算正确），延迟释放 pixmap ──
        now = time.perf_counter()
        if not hasattr(self, '_page_last_seen'):
            self._page_last_seen: dict[int, float] = {}
        for page_num in list(self._active_pages - needed):
            if page_num not in self._page_metas:
                continue
            if page_num in self._split_pages:
                self._hide_split_page_from_layout(page_num)
                continue
            if page_num not in self._page_last_seen:
                # 首次离开 → 立即从 layout 移除，保留 pixmap 以便快速回滚
                self._page_last_seen[page_num] = now
                self._hide_page_from_layout(page_num)
            elif now - self._page_last_seen[page_num] > 5.0:
                # 5s 冷却后 → 释放 pixmap
                self._unrender_pooled_page(page_num)
                self._page_last_seen.pop(page_num, None)
        for page_num in needed:
            self._page_last_seen.pop(page_num, None)

        self._adjust_spacers(needed)
        elapsed = (time.perf_counter() - t0) * 1000
        if elapsed > 5:
            _logger.info("PdfViewer: _update_visible_pages 耗时 %.1fms (needed=%d, pool=%d)",
                         elapsed, len(needed), len(self._widget_pool))

    def visible_pages(self, margin_pages: bool = False) -> set[int]:
        """Return current viewport pages without triggering rendering."""
        if not self._vlayout:
            return set()
        viewport_h = self.viewport().height()
        if viewport_h <= 0:
            return set()
        margin = viewport_h if margin_pages else 0
        return set(
            self._vlayout.page_range_for_viewport(
                float(self.verticalScrollBar().value()),
                float(viewport_h),
                float(margin),
            )
        )

    # ── 页面渲染 ──

    def _render_page(self, page_num: int) -> None:
        """请求页面渲染。大页面先显示整页 fallback，再逐瓦片后台清晰化。"""
        container = self._page_containers.get(page_num)
        if container is None:
            return
        if container.rendered and page_num not in self._precise_render_pending:
            return

        segs = self._page_segments.get(page_num, [])
        if not segs:
            return

        if len(segs) == 1 and segs[0].get("widget") is container:
            doc = self._doc_engine.document
            is_large = False
            if doc and page_num < doc.page_count:
                rect = doc[page_num].rect
                phys_w = rect.width * self._dpi / 72.0
                phys_h = rect.height * self._dpi / 72.0
                is_large = max(phys_w, phys_h) > 4096

            if is_large:
                _logger.info("PdfViewer: p%d 大页面 (%.0fx%.0f) → fallback + 瓦片渲染",
                             page_num, phys_w, phys_h)
                self._tile_renderer.set_dpi(self._dpi)
                container._tile_cache = self._tile_cache
                container._zoom = self._scale

                fallback = self._fallback_pixmap_for_page(page_num)
                if fallback is not None and not fallback.isNull():
                    fallback.setDevicePixelRatio(self._screen_dpr)
                    container._rendered = True
                    container._full_pixmap = fallback
                    self._rendered_pages.add(page_num)
                    self._precise_render_pending.discard(page_num)
                    self._refresh_container_overlays(container, segs[0]["blocks"])
                elif container._full_pixmap is None or container._full_pixmap.isNull():
                    container._rendered = False
                    self._rendered_pages.discard(page_num)
                    self._doc_engine.request_page_render_async(
                        page_num, dpi=min(self._dpi, self.FALLBACK_DPI)
                    )

                page_rect = container.rect()
                QTimer.singleShot(0, lambda p=page_num, r=page_rect:
                    self._tile_renderer.request_tiles_for_page(p, r, self._scale))
            else:
                self._doc_engine.request_page_render_async(page_num, dpi=self._dpi)

    def _fallback_pixmap_for_page(self, page_num: int) -> QPixmap | None:
        """Return a bounded whole-page fallback so tile-first pages are never blank."""
        container = self._page_containers.get(page_num) or self._widget_pool.get(page_num)
        if container is not None and container._full_pixmap is not None and not container._full_pixmap.isNull():
            return container._full_pixmap
        getter = getattr(self._doc_engine, "get_page_pixmap", None)
        if not callable(getter):
            return None
        try:
            return getter(page_num, dpi=min(self._dpi, self.FALLBACK_DPI))
        except Exception:
            _logger.debug("PdfViewer: p%d fallback pixmap 获取失败", page_num, exc_info=True)
            return None

    def _rebuild_split_segments_from_pixmap(
        self,
        page_num: int,
        pixmap: QPixmap,
        *,
        only_if_in_layout: bool,
    ) -> int:
        """Rebuild split page image segments for the current zoom, preserving split widgets."""
        segs = self._page_segments.get(page_num, [])
        pixmap.setDevicePixelRatio(self._screen_dpr)
        rebuilt = 0
        for seg in segs:
            if "split_id" in seg:
                continue
            old_w = seg.get("widget")
            if old_w is None:
                continue
            idx = self._layout.indexOf(old_w)
            if idx < 0 and only_if_in_layout:
                continue
            for child in old_w.findChildren(BlockOverlay):
                self._overlays.pop(child.block_id, None)
                if _isValid(child):
                    child.deleteLater()
            self._forget_widget_page(old_w)
            old_w.hide()
            if idx >= 0:
                self._layout.removeWidget(old_w)
            old_w.deleteLater()
            new_w = self._build_segment_widget(
                pixmap, seg["y0"], seg["y1"], seg["blocks"], page_num=page_num)
            if idx >= 0:
                self._layout.insertWidget(idx, new_w)
            else:
                new_w.hide()
            seg["widget"] = new_w
            rebuilt += 1
        return rebuilt

    def _on_tile_ready(self, key: TileKey, pixmap: QPixmap) -> None:
        """瓦片渲染完成 → 触发对应页面重绘（借鉴 qpageview callback 模式）。"""
        container = self._page_containers.get(key.page_num)
        if container is not None and container.rendered:
            container.update()

    def _on_page_rendered_async(self, page_num: int, qpixmap: object) -> None:
        """全页 pixmap 渲染完成 → 切片到 TileCache → 触发 QPainter 重绘。"""
        pixmap = qpixmap if isinstance(qpixmap, QPixmap) else None
        if pixmap is None or pixmap.isNull():
            return

        # 裂缝页面缩放异步渲染 → 重建段 widget
        pending_splits = getattr(self, '_pending_split_rerenders', set())
        if page_num in pending_splits:
            self._rebuild_split_segments(page_num, pixmap)
            self._pending_split_rerenders.discard(page_num)
            return

        container = self._page_containers.get(page_num)
        if container is None:
            self._precise_render_pending.discard(page_num)
            return
        if container.rendered and page_num not in self._precise_render_pending:
            return
        if not _isValid(container):
            return

        segs = self._page_segments.get(page_num, [])
        if len(segs) != 1 or segs[0].get("widget") is not container:
            self._precise_render_pending.discard(page_num)
            return

        blocks = segs[0]["blocks"]
        pixmap.setDevicePixelRatio(self._screen_dpr)
        t_render = time.perf_counter()
        if container.rendered:
            self._clear_container_overlays(container)
        container.render(pixmap, blocks, self._scale, self._connect_overlay,
                         tile_cache=self._tile_cache)
        self._rendered_pages.add(page_num)
        self._precise_render_pending.discard(page_num)
        render_ms = (time.perf_counter() - t_render) * 1000
        _logger.info("PdfViewer: p%d 全页 pixmap 就绪 (%dx%d, DPR=%.1f) → 切片+绘制 (%.1fms)",
                     page_num, pixmap.width(), pixmap.height(),
                     self._screen_dpr, render_ms)
        for b in blocks:
            if b.id in self._trans_indicators:
                self._set_translation_marker(b.id, True)

    def _rebuild_split_segments(self, page_num: int, pixmap: QPixmap) -> None:
        """缩放后异步重建裂缝页面的段 widget。"""
        rebuilt = self._rebuild_split_segments_from_pixmap(
            page_num, pixmap, only_if_in_layout=False)
        _logger.info("PdfViewer: p%d 裂缝段异步重建完成 (%dx%d, segments=%d)",
                     page_num, pixmap.width(), pixmap.height(), rebuilt)

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
        # 也检查 widget 池中隐藏的 widget
        for container in self._widget_pool.values():
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
            try:
                page_num = int(block_id.split("_", 1)[0][1:])
            except (ValueError, IndexError):
                return None
            self._request_page_blocks(page_num)
            return None

        # 确保页面 widget 存在且已渲染。未渲染完成时先触发渲染，等下一次交互。
        container = self._ensure_page_widget(page_num)
        if container is not None and page_num not in self._rendered_pages:
            self._render_page(page_num)
            return None

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
        scroll_anchor = self._capture_scroll_anchor()
        all_blocks = seg["blocks"]
        pixmap = self._doc_engine.get_page_pixmap(page_num, dpi=self._dpi)
        if pixmap is None or pixmap.isNull():
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
        page_display_w = self._page_metas[page_num]["width"] if page_num in self._page_metas else pixmap.width()
        split = SplitWidget(
            block, mode=mode, position="below",
            block_pixel_height=max(block_px_h, 60),
            page_width=page_display_w,
        )
        split.setFixedWidth(page_display_w)
        # 段落内边距：使文字区域左右与紫色框对齐
        rect = self._page_rects_pt.get(page_num)
        if rect:
            page_pt_w = rect[0]
            left_pad = int(block.bbox[0] * self._scale)
            right_pad = int((page_pt_w - block.bbox[2]) * self._scale)
            split.set_content_padding(left_pad, right_pad)
        split.question_submitted.connect(lambda q, bid=block_id: self._on_split_q(q, bid))
        split.translation_requested.connect(self.block_translate_requested.emit)
        split.close_requested.connect(lambda bid=block_id: self._on_clear_close(bid))

        # Layout 操作：移除老 widget，插入段+裂缝+段
        old_idx = self._layout.indexOf(old_widget)

        if isinstance(old_widget, _LazyPageWidget):
            for b_id in list(old_widget._overlays.keys()):
                self._overlays.pop(b_id, None)
            self._page_containers.pop(page_num, None)
            self._widget_pool.pop(page_num, None)
            self._precise_render_pending.discard(page_num)
            self._forget_widget_page(old_widget)
            for b_id in list(self._trans_indicators.keys()):
                if self._block_to_page.get(b_id) == page_num:
                    del self._trans_indicators[b_id]
        else:
            for child in old_widget.findChildren(BlockOverlay):
                self._overlays.pop(child.block_id, None)
                if _isValid(child):
                    child.deleteLater()
            self._forget_widget_page(old_widget)
        old_widget.hide()
        self._layout.removeWidget(old_widget)
        old_widget.deleteLater()

        top_w = self._build_segment_widget(
            pixmap, seg["y0"], cut_y, above_blocks, page_num=page_num)
        self._layout.insertWidget(old_idx, top_w)

        self._layout.insertWidget(old_idx + 1, split)

        bot_w = self._build_segment_widget(
            pixmap, cut_y, seg["y1"], below_blocks, page_num=page_num)
        self._layout.insertWidget(old_idx + 2, bot_w)

        split.open(mode)
        self._splits[block_id] = split
        _logger.info("裂缝已打开: block=%s page=%d mode=%s", block_id, page_num, mode.value)
        self._split_pages.add(page_num)
        self._set_translation_marker(block_id, True)

        # 更新虚拟布局 + 连接高度变化信号
        split._page_num = page_num
        self._remember_widget_page(split, page_num)
        split._prev_split_h = float(split._saved_height)
        if self._vlayout:
            self._vlayout.register_split(page_num, split._prev_split_h)
        split.height_changed.connect(self._on_split_height_changed)

        new_segs = segs[:seg_idx] + [
            {
                "y0": seg["y0"], "y1": cut_y,
                "y0_pt": seg["y0"] / self._scale if self._scale else 0.0,
                "y1_pt": cut_y / self._scale if self._scale else 0.0,
                "blocks": above_blocks, "widget": top_w,
            },
            {"split_id": block_id},
            {
                "y0": cut_y, "y1": seg["y1"],
                "y0_pt": cut_y / self._scale if self._scale else 0.0,
                "y1_pt": seg["y1"] / self._scale if self._scale else 0.0,
                "blocks": below_blocks, "widget": bot_w,
            },
        ] + segs[seg_idx + 1:]
        self._page_segments[page_num] = new_segs

        # 调整 spacer 保持滚动条范围正确
        self._active_pages.add(page_num)
        self._rendered_pages.add(page_num)
        self._adjust_spacers(self._active_pages | {page_num})
        self._restore_scroll_anchor(scroll_anchor)

        return split

    def find_split_widget(self, block_id: str) -> SplitWidget | None:
        return self._splits.get(block_id)

    def _request_page_blocks(self, page_num: int) -> None:
        meta = self._page_metas.get(page_num)
        if meta is None or meta.get("blocks_ready", False):
            return
        try:
            if self._doc_engine.has_page_blocks(page_num):
                meta["blocks_ready"] = True
                blocks = self._doc_engine.get_page_blocks(page_num)
                QTimer.singleShot(
                    0,
                    lambda pn=page_num, bs=blocks: self.update_page_blocks(pn, bs),
                )
                return
        except AttributeError:
            pass
        if page_num in self._requested_block_pages:
            return
        try:
            self._requested_block_pages.add(page_num)
            self._doc_engine.request_page_blocks_async(page_num)
        except AttributeError:
            self._requested_block_pages.discard(page_num)
            return

    def _request_page_blocks_around(self, page_num: int, radius: int = 2) -> None:
        max_page = max(self._page_metas.keys()) if self._page_metas else -1
        for pn in range(max(0, page_num - radius), min(max_page, page_num + radius) + 1):
            self._request_page_blocks(pn)

    def scroll_to_page(self, page_num: int) -> None:
        if self._vlayout is None:
            return
        self._request_page_blocks(page_num)
        y = int(self._vlayout.page_y(page_num))
        viewport_h = max(self.viewport().height(), 1)
        self._sync_content_size(viewport_h)
        self._scroll_history.clear()
        self.verticalScrollBar().setValue(y)
        if page_num in self._page_metas and page_num not in self._split_pages:
            container = self._ensure_page_widget(page_num)
            if container is not None:
                self._render_page(page_num)
        self._update_visible_pages()
        _logger.info("PdfViewer: scroll_to_page p%d → y=%d (max=%d)",
                     page_num, y, self.verticalScrollBar().maximum())

    def scroll_to_bbox(
        self,
        page_num: int,
        bbox: tuple[float, float, float, float] | list[float],
    ) -> bool:
        """Scroll to a PDF-space bbox and show a short-lived evidence highlight."""
        if self._vlayout is None or page_num not in self._page_metas:
            return False
        try:
            x0, y0, x1, y1 = (float(value) for value in bbox)
        except (TypeError, ValueError):
            return False
        if x1 <= x0 or y1 <= y0:
            return False

        self._request_page_blocks_around(page_num)
        self._scroll_history.clear()
        if page_num not in self._split_pages:
            container = self._ensure_page_widget(page_num)
            if container is not None:
                self._render_page(page_num)
        self._adjust_spacers(self._active_pages | {page_num})

        viewport_h = max(self.viewport().height(), 1)
        self._sync_scroll_range(viewport_h)
        page_y = self._vlayout.page_y(page_num)
        target_y = int(page_y + y0 * self._scale - viewport_h * 0.25)
        self.verticalScrollBar().setValue(max(0, target_y))
        self._scroll_horizontally_to_pdf_x(x0, x1)
        self._show_evidence_highlight(page_num, (x0, y0, x1, y1))
        QTimer.singleShot(0, self._update_visible_pages)
        _logger.info(
            "PdfViewer: scroll_to_bbox p%d bbox=(%.1f,%.1f,%.1f,%.1f) → y=%d",
            page_num,
            x0,
            y0,
            x1,
            y1,
            max(0, target_y),
        )
        return True

    def _show_evidence_highlight(
        self,
        page_num: int,
        bbox: tuple[float, float, float, float],
    ) -> None:
        parent, y_offset = self._highlight_parent_for_bbox(page_num, bbox)
        if parent is None:
            return
        self._clear_evidence_highlight()
        x0, y0, x1, y1 = bbox
        x = int(x0 * self._scale)
        y = int(y0 * self._scale) - y_offset
        w = max(int((x1 - x0) * self._scale), 3)
        h = max(int((y1 - y0) * self._scale), 3)
        highlight = QWidget(parent)
        highlight.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        highlight.setObjectName("formula_evidence_highlight")
        highlight.setGeometry(x, y, w, h)
        highlight.setStyleSheet(
            "background-color: rgba(47, 128, 237, 40);"
            "border: 2px solid #2f80ed;"
        )
        highlight.raise_()
        highlight.show()
        self._evidence_highlight = highlight
        QTimer.singleShot(2500, self._clear_evidence_highlight)

    def _highlight_parent_for_bbox(
        self,
        page_num: int,
        bbox: tuple[float, float, float, float],
    ) -> tuple[QWidget | None, int]:
        if page_num not in self._split_pages:
            parent = self._page_containers.get(page_num) or self._widget_pool.get(page_num)
            return parent, 0
        y0 = int(bbox[1] * self._scale)
        y1 = int(bbox[3] * self._scale)
        for seg in self._page_segments.get(page_num, []):
            if "split_id" in seg:
                continue
            widget = seg.get("widget")
            if widget is None:
                continue
            seg_y0 = int(seg.get("y0", 0))
            seg_y1 = int(seg.get("y1", 0))
            if y1 >= seg_y0 and y0 <= seg_y1:
                return widget, seg_y0
        return None, 0

    def _clear_evidence_highlight(self) -> None:
        highlight = self._evidence_highlight
        self._evidence_highlight = None
        if highlight is not None and _isValid(highlight):
            highlight.deleteLater()

    def _content_min_width(self) -> int:
        if not self._page_metas:
            return max(self.viewport().width(), 1)
        margins = self._layout.contentsMargins()
        max_page_w = max(int(meta.get("width", 0) or 0) for meta in self._page_metas.values())
        return max(1, max_page_w + margins.left() + margins.right())

    def _sync_content_size(self, viewport_h: int) -> None:
        if self._vlayout is None:
            return
        viewport_w = max(self.viewport().width(), 1)
        total_h = max(int(self._vlayout.total_height), viewport_h)
        content_w = max(self._content_min_width(), viewport_w)
        self._content.setMinimumWidth(content_w)
        self._content.setMinimumHeight(total_h)
        self._content.resize(content_w, total_h)
        self._layout.activate()
        self._content.adjustSize()
        self.verticalScrollBar().setRange(0, max(0, total_h - viewport_h))
        self.horizontalScrollBar().setRange(0, max(0, content_w - viewport_w))

    def _sync_scroll_range(self, viewport_h: int) -> None:
        self._sync_content_size(viewport_h)

    def _scroll_horizontally_to_pdf_x(self, x0: float, x1: float) -> None:
        hbar = self.horizontalScrollBar()
        if hbar is None or not _isValid(hbar):
            return
        viewport_w = max(self.viewport().width(), 1)
        margins = self._layout.contentsMargins()
        target_left = int(margins.left() + x0 * self._scale - viewport_w * 0.15)
        target_right = int(margins.left() + x1 * self._scale - viewport_w * 0.85)
        if target_right > hbar.value():
            hbar.setValue(max(0, target_right))
        elif target_left < hbar.value():
            hbar.setValue(max(0, target_left))

    # ── 缩放 ──

    MIN_ZOOM: float = 0.3
    MAX_ZOOM: float = 5.0
    ZOOM_STEP: float = 1.2
    FALLBACK_DPI: int = 144

    def zoom_in(self) -> None:
        """放大一级。"""
        self._set_zoom(self._zoom_multiplier * self.ZOOM_STEP)

    def zoom_out(self) -> None:
        """缩小一级。"""
        self._set_zoom(self._zoom_multiplier / self.ZOOM_STEP)

    def _set_zoom(self, new_zoom: float) -> None:
        """设置缩放倍数 — 借鉴 Sioyek try_closest_rendered_page 即时反馈策略。

        不销毁 widget，而是：
        1. 立即更新页面布局，paintEvent 临时拉伸旧 pixmap 显示
        2. 后台异步渲染精确缩放（≈100ms）
        3. 渲染完成后自动替换为清晰 pixmap
        """
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, new_zoom))
        if abs(new_zoom - self._zoom_multiplier) < 0.001:
            return

        t0 = time.perf_counter()
        _logger.info("PdfViewer: 缩放 %.2f → %.2f", self._zoom_multiplier, new_zoom)

        # 保存视口中心的内容位置（借鉴 SumatraPDF fixPt）
        sb = self.verticalScrollBar()
        viewport_h = self.viewport().height()
        center_y = sb.value() + viewport_h // 2
        center_page: int | None = None
        center_offset: float = 0.0
        if self._vlayout:
            center_page = self._vlayout.page_at_y(float(center_y))
            if center_page is not None:
                center_offset = center_y - self._vlayout.page_y(center_page)

        # 更新缩放因子
        old_zoom = self._zoom_multiplier
        self._zoom_multiplier = new_zoom
        self._scale = self._base_scale * new_zoom
        self._dpi = int(self._base_dpi * new_zoom)
        # 重建页面元数据尺寸 + 虚拟布局
        page_heights: dict[int, float] = {}
        for pn, meta in self._page_metas.items():
            rect_w_pt, rect_h_pt = self._page_rects_pt.get(pn, (600.0 / self._scale, 800.0 / self._scale))
            w = int(rect_w_pt * self._scale)
            h = int(rect_h_pt * self._scale)
            meta["width"] = w
            meta["height"] = h
            page_heights[pn] = float(h)
            for seg in self._page_segments.get(pn, []):
                if "split_id" not in seg:
                    if seg.get("y0_pt") is None:
                        seg["y0_pt"] = seg.get("y0", 0) / (self._base_scale * old_zoom)
                    if seg.get("y1_pt") is None:
                        seg["y1_pt"] = seg.get("y1", h) / (self._base_scale * old_zoom)
                    seg["y0"] = int(seg["y0_pt"] * self._scale)
                    seg["y1"] = int(seg["y1_pt"] * self._scale)
        self._vlayout.rebuild(page_heights)
        self._sync_content_size(viewport_h)

        # 处理已渲染的页面：复用旧 pixmap 过渡显示 + 清除旧 overlay + 请求精确渲染。
        # 不在 UI 线程生成 scaled QPixmap；paintEvent 会把旧图拉伸到新 widget 尺寸。
        rerender_count = 0
        for pn in list(self._active_pages):
            container = self._widget_pool.get(pn)
            if container is None or pn in self._split_pages:
                continue
            meta = self._page_metas[pn]
            container.setFixedSize(meta["width"], meta["height"])

            if container._full_pixmap and not container._full_pixmap.isNull():
                container._zoom = self._scale
                container._page_w = meta["width"]
                container._page_h = meta["height"]
                container._tile_cache = None
                container._rendered = True
                blocks = self._page_segments.get(pn, [{}])[0].get("blocks", [])
                self._refresh_container_overlays(container, blocks)
                container.update()
            else:
                self._clear_container_overlays(container)
                container._rendered = False

            # 标记需要重新渲染，延迟请求精确 DPI 渲染（避免同步 container.render 阻塞主线程）
            self._precise_render_pending.add(pn)
            rerender_count += 1

        # 连续缩放期间只更新布局和旧图即时显示，等用户停顿后再合并请求清晰渲染。
        self._precise_render_timer.start(180)

        # 处理裂缝页面：仅当前 layout 中的裂缝页做即时占位缩放和优先重建。
        # 离屏裂缝页只标记 stale，避免抢当前页 UI 和渲染队列。
        visible_split_rerenders: set[int] = set()
        for pn in list(self._split_pages):
            split_in_layout = self._is_split_page_in_layout(pn)
            segs = self._page_segments.get(pn, [])
            for i, seg in enumerate(segs):
                if "split_id" in seg:
                    split = self._splits.get(seg["split_id"])
                    if split:
                        split.setFixedWidth(self._page_metas[pn]["width"])
                    continue
                if not split_in_layout:
                    continue
                old_w = seg.get("widget")
                if old_w is None:
                    continue
                seg_h = max(seg["y1"] - seg["y0"], 1)
                for label in old_w.findChildren(QLabel):
                    if label.pixmap() and not label.pixmap().isNull():
                        old_pm = label.pixmap()
                        scaled = old_pm.scaled(
                            self._page_metas[pn]["width"],
                            seg_h,
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.FastTransformation,
                        )
                        scaled.setDevicePixelRatio(self._screen_dpr)
                        label.setPixmap(scaled)
                        label.resize(self._page_metas[pn]["width"], seg_h)
                old_w.setFixedSize(self._page_metas[pn]["width"], seg_h)
                self._refresh_segment_overlays(old_w, seg.get("blocks", []), seg["y0"], seg_h)
            # 异步渲染 → 完成后在 _on_page_rendered_async 中重建段
            self._pending_split_rerenders.add(pn)
            if split_in_layout:
                visible_split_rerenders.add(pn)
        if visible_split_rerenders:
            self._request_split_rerenders(visible_split_rerenders)

        # 调整 spacer 高度
        self._adjust_spacers(self._active_pages)

        # 恢复视口位置：保持缩放前视口中心的内容在同一位置（借鉴 SumatraPDF fixPt）
        if center_page is not None:
            new_center_y = int(self._vlayout.page_y(center_page) + center_offset)
            target_y = max(0, new_center_y - viewport_h // 2)
            QTimer.singleShot(50, lambda y=target_y: self._set_scroll_value_if_valid(y))

        elapsed = (time.perf_counter() - t0) * 1000
        _logger.info("PdfViewer: 缩放完成 (%.1fms, 即时显示 %d 页, 后台渲染 %d 页)",
                     elapsed, rerender_count, rerender_count)

    def _set_scroll_value_if_valid(self, value: int) -> None:
        """Set scrollbar value from delayed callbacks only while Qt objects are alive."""
        if not _isValid(self):
            return
        sb = self.verticalScrollBar()
        if sb is not None and _isValid(sb):
            sb.setValue(value)

    def _request_precise_renders(self) -> None:
        """为缩放后标记的页面请求精确 DPI 渲染（延迟执行，不阻塞主线程）。"""
        for pn in list(self._precise_render_pending):
            container = self._widget_pool.get(pn)
            if container is None or pn in self._split_pages:
                self._precise_render_pending.discard(pn)
                continue
            self._render_page(pn)

    def _request_pending_split_rerenders(self) -> None:
        """为裂缝页请求最终清晰渲染；连续缩放期间自动合并请求。"""
        if not self._pending_split_rerenders:
            return
        visible_pages = {
            pn for pn in self._pending_split_rerenders
            if self._is_split_page_in_layout(pn)
        }
        self._request_split_rerenders(visible_pages)

    def _request_split_rerenders(self, page_nums: set[int]) -> None:
        if not page_nums:
            return
        self._tile_renderer.set_dpi(self._dpi)
        for pn in sorted(page_nums):
            if pn not in self._pending_split_rerenders:
                continue
            self._doc_engine.request_page_render_async(pn, dpi=self._dpi)

    # ── 主题 ──

    def apply_theme_to_splits(self, theme: str) -> None:
        for split in self._splits.values():
            split.apply_theme(theme)

    # ── 清理 ──

    def clear(self) -> None:
        _logger.info("PdfViewer.clear: 开始 (%d splits, %d pool, %d tiles)",
                     len(self._splits), len(self._widget_pool),
                     self._tile_cache.tile_count)
        for block_id, s in list(self._splits.items()):
            try:
                s.height_changed.disconnect(self._on_split_height_changed)
            except Exception:
                pass
            s.close()
            s.deleteLater()
        self._splits.clear()
        self._page_segments.clear()
        self._block_to_page.clear()
        self._blocks_by_id.clear()
        self._parsed_block_pages.clear()
        self._overlays.clear()
        for ind in list(self._trans_indicators.values()):
            if _isValid(ind):
                ind.deleteLater()
        self._trans_indicators.clear()
        self._clear_evidence_highlight()
        self._all_blocks.clear()
        self._page_rects_pt.clear()
        self._page_containers.clear()
        self._rendered_pages.clear()
        self._active_pages.clear()
        self._precise_render_pending.clear()
        self._precise_render_timer.stop()
        self._pending_split_rerenders.clear()
        self._split_rerender_timer.stop()
        self._requested_block_pages.clear()
        self._widget_page_map.clear()
        self._split_pages.clear()
        self._tile_renderer.clear()
        self._scroll_history.clear()
        self._last_scroll_value = 0
        self._vlayout = None
        self._page_metas.clear()
        for w in list(self._widget_pool.values()):
            if _isValid(w):
                w.deleteLater()
        self._widget_pool.clear()
        # 清理 layout 中所有 widget（保留 spacer 和 stretch）
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w and w is not self._top_spacer and w is not self._bottom_spacer:
                w.deleteLater()
        # 重新添加 spacer 和 stretch
        self._top_spacer = QWidget()
        self._top_spacer.setFixedHeight(0)
        self._layout.addWidget(self._top_spacer)
        self._bottom_spacer = QWidget()
        self._layout.addWidget(self._bottom_spacer)
        self._layout.addStretch()
        _logger.info("PdfViewer.clear: 完成")

    # ── Overlay 连接 ──

    def _connect_overlay(self, ov: BlockOverlay) -> None:
        self._overlays[ov.block_id] = ov
        ov.double_clicked.connect(self.block_double_clicked.emit)
        ov.translate_requested.connect(self.block_translate_requested.emit)
        ov.question_requested.connect(self.block_question_requested.emit)
        ov.explain_requested.connect(self.block_explain_requested.emit)

    def _find_block(self, block_id: str) -> DocumentBlock | None:
        return self._blocks_by_id.get(block_id)

    # ── 内部槽 ──

    def _on_split_q(self, question: str, block_id: str) -> None:
        pass

    def _on_split_height_changed(self, new_height: int) -> None:
        """裂缝高度变化 → 更新 _vlayout + spacer（layout 自动处理 widget 位移）。"""
        split = self.sender()
        if not isinstance(split, SplitWidget):
            return
        page_num = getattr(split, '_page_num', None)
        if page_num is None:
            return

        delta = float(new_height) - split._prev_split_h
        if delta == 0:
            return

        scroll_anchor = self._capture_scroll_anchor()
        if self._vlayout:
            total_split_h = sum(
                float(s.height() if s.isVisible() else getattr(s, "_saved_height", 0))
                for bid, s in self._splits.items()
                if self._block_to_page.get(bid) == page_num
            )
            self._vlayout.set_split_extra(page_num, total_split_h)

        split._prev_split_h = float(new_height)

        # 调整 spacer 以适应新的总高度
        self._adjust_spacers(self._active_pages)
        self._restore_scroll_anchor(scroll_anchor)

    def _on_clear_close(self, block_id: str) -> None:
        scroll_anchor = self._capture_scroll_anchor()
        self._set_translation_marker(block_id, False)
        s = self._splits.pop(block_id, None)
        if s is None:
            return
        self._forget_widget_page(s)
        try:
            s.height_changed.disconnect(self._on_split_height_changed)
        except Exception:
            pass
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
        # 更新虚拟布局（layout 自动处理 widget 位移）
        if self._vlayout and page_num is not None:
            total_split_h = sum(
                float(other.height() if other.isVisible() else getattr(other, "_saved_height", 0))
                for bid, other in self._splits.items()
                if self._block_to_page.get(bid) == page_num
            )
            self._vlayout.set_split_extra(page_num, total_split_h)
        self._adjust_spacers(self._active_pages)
        self._restore_scroll_anchor(scroll_anchor)
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
                            pixmap, prev_seg["y0"], next_seg["y1"], merged_blocks,
                            page_num=page_num,
                        )
                        pw = prev_seg["widget"]
                        nw = next_seg["widget"]
                        for child in pw.findChildren(BlockOverlay) + nw.findChildren(BlockOverlay):
                            self._overlays.pop(child.block_id, None)
                            if _isValid(child):
                                child.deleteLater()
                        idx_p = self._layout.indexOf(pw)
                        self._forget_widget_page(pw)
                        self._forget_widget_page(nw)
                        pw.hide(); self._layout.removeWidget(pw); pw.deleteLater()
                        nw.hide(); self._layout.removeWidget(nw); nw.deleteLater()
                        self._layout.insertWidget(idx_p, merged_w)
                        new_segs = segs[:i - 1] + [{
                            "y0": prev_seg["y0"], "y1": next_seg["y1"],
                            "y0_pt": prev_seg["y0"] / self._scale if self._scale else 0.0,
                            "y1_pt": next_seg["y1"] / self._scale if self._scale else 0.0,
                            "blocks": merged_blocks, "widget": merged_w,
                        }] + segs[i + 2:]
                        self._page_segments[page_num] = new_segs
                        self._page_containers.pop(page_num, None)
                        self._rendered_pages.add(page_num)
                        self._active_pages.add(page_num)
                break

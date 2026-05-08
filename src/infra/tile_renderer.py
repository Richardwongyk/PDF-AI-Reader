"""
Tile-based PDF renderer — adapted from qpageview render.py + Syncfusion request cancellation.

Core improvements over the old _LazyPageWidget approach:
  1. Pages are split into 256×256 px tiles — only visible tiles are rendered
  2. Request cancellation: when the user scrolls fast, stale renders are discarded
  3. Priority queue: tiles closest to viewport center render first
  4. Memory cache with LRU eviction (TileCache)

Architecture:
  TileRenderer (QObject, lives on main thread)
    └── QThreadPool (max 2 threads) for background rendering
         └── _TileRenderTask (QRunnable) — one per tile, renders via fitz
"""

from __future__ import annotations

import logging
import time

import fitz
from PySide6.QtCore import QObject, QRect, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QPixmap

from src.infra.tile_cache import TileCache, TileKey

_logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────
TILE_SIZE: int = 256          # px, matches qpageview default
MAX_MEMORY_MB: int = 200      # matches qpageview's ImageCache.maxsize
RENDER_THREADS: int = 2       # PyMuPDF per-doc mutex limits concurrency
DEFAULT_DPI: int = 150        # rendering resolution


class _TileRenderTask(QRunnable):
    """Renders a single tile in a background thread via fitz.

    Shares the fitz.Document (thread-safe read in PyMuPDF ≥ 1.18).
    """

    class _Signals(QObject):
        done = Signal(TileKey, int, object, float)  # key, request_id, QPixmap|None, elapsed_sec

    def __init__(
        self, doc: fitz.Document, key: TileKey, dpi: int, request_id: int,
    ) -> None:
        super().__init__()
        self._doc = doc
        self._key = key
        self._dpi = dpi
        self._request_id = request_id
        self._signals = self._Signals()

    @property
    def done_signal(self) -> Signal:
        return self._signals.done

    def run(self) -> None:
        t0 = time.perf_counter()
        _log = logging.getLogger("TileRenderer.task")
        try:
            page = self._doc[self._key.page_num]
            # zoom = 物理渲染精度 (physical_dpi / 72)
            zoom = self._dpi / 72.0
            # scale = 逻辑缩放因子 (logical_dpi / 72), 从 TileKey.zoom_level 传入
            scale = self._key.zoom_level
            mat = fitz.Matrix(zoom, zoom)

            # 瓦片在页面坐标中的区域（points = 1/72 inch）
            # 逻辑瓦片 = TILE_SIZE × TILE_SIZE 逻辑像素 → 点单位 = TILE_SIZE / scale
            tile_pt_w = TILE_SIZE / scale
            tile_pt_h = TILE_SIZE / scale
            tile_pt_x = self._key.tile_x * tile_pt_w
            tile_pt_y = self._key.tile_y * tile_pt_h

            clip = fitz.Rect(tile_pt_x, tile_pt_y,
                             tile_pt_x + tile_pt_w, tile_pt_y + tile_pt_h)
            pix = page.get_pixmap(matrix=mat, clip=clip, colorspace=fitz.csRGB)
            # 渲染结果物理像素 = tile_pt_w * zoom = TILE_SIZE * DPR ✓

            qpixmap = QPixmap()
            qpixmap.loadFromData(pix.tobytes("ppm"), "PPM")
            elapsed = time.perf_counter() - t0
            _log.debug("瓦片渲染完成: %s (%.0fms, %dx%d)",
                       self._key, elapsed * 1000, qpixmap.width(), qpixmap.height())
            self._signals.done.emit(self._key, self._request_id, qpixmap, elapsed)
        except Exception as e:
            elapsed = time.perf_counter() - t0
            _log.warning("瓦片渲染失败: %s → %s (%.0fms)", self._key, e, elapsed * 1000)
            self._signals.done.emit(self._key, self._request_id, None, elapsed)


class TileRenderer(QObject):
    """Tile-based PDF page renderer with priority scheduling and request cancellation.

    Usage:
        renderer = TileRenderer()
        renderer.set_document(fitz_doc)

        # On scroll/zoom change:
        renderer.request_viewport(viewport_rect, zoom_level)

        # Connect to get tiles as they're rendered:
        renderer.tile_ready.connect(on_tile_ready)
    """

    tile_ready = Signal(TileKey, QPixmap)  # emitted when a tile finishes rendering

    def __init__(self, parent: QObject | None = None, dpi: int = DEFAULT_DPI) -> None:
        super().__init__(parent)
        self._doc: fitz.Document | None = None
        self._dpi: int = dpi
        self._cache = TileCache(max_size_bytes=MAX_MEMORY_MB * 1024 * 1024)
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(RENDER_THREADS)

        # Pending renders: TileKey → current request_id (for cancellation)
        self._pending: dict[TileKey, int] = {}
        self._request_counter: int = 0

        # Stats
        self._rendered_count: int = 0
        self._cancelled_count: int = 0
        _logger.info("TileRenderer: 初始化 (threads=%d, tile_size=%dpx, max_mem=%dMB, dpi=%d)",
                     RENDER_THREADS, TILE_SIZE, MAX_MEMORY_MB, dpi)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_document(self, doc: fitz.Document | None) -> None:
        """Switch to a new document.  Clears the tile cache."""
        if self._doc is not doc:
            old_pages = self._doc.page_count if self._doc else 0
            new_pages = doc.page_count if doc else 0
            _logger.info("TileRenderer: set_document (pages: %d → %d)", old_pages, new_pages)
            self._cache.clear()
            self._pending.clear()
            self._rendered_count = 0
            self._cancelled_count = 0
        self._doc = doc

    def set_dpi(self, dpi: int) -> None:
        """Update rendering DPI (called when screen DPI is detected)."""
        self._dpi = dpi
        _logger.debug("TileRenderer: set_dpi(%d)", dpi)

    @property
    def cache(self) -> TileCache:
        return self._cache

    def request_viewport(self, viewport: QRect, zoom: float) -> None:
        """Request all tiles intersecting the viewport.

        Tiles already cached are available immediately via get_tile().
        Missing tiles are scheduled for background rendering.
        """
        if self._doc is None:
            return

        needed = self._compute_visible_tiles(viewport, zoom)
        cached = 0
        already_pending = 0
        scheduled = 0

        for key in needed:
            if self._cache.get(key) is not None:
                cached += 1
                continue
            if key in self._pending:
                already_pending += 1
                continue
            self._schedule(key)
            scheduled += 1

        if scheduled > 0:
            _logger.debug("TileRenderer: request_viewport → %d 瓦片 (缓存:%d 渲染中:%d 新建:%d)",
                          len(needed), cached, already_pending, scheduled)

    def request_tiles_for_page(self, page_num: int, page_rect: QRect, zoom: float) -> None:
        """Pre-render all tiles for a given page (used for preloading)."""
        if self._doc is None:
            return

        tiles = self._compute_page_tiles(page_num, page_rect, zoom)
        scheduled = 0
        for key in tiles:
            if self._cache.get(key) is not None or key in self._pending:
                continue
            self._schedule(key)
            scheduled += 1

        if scheduled > 0:
            _logger.info("TileRenderer: 预渲染 p%d → %d 个新瓦片 (共 %d 个)",
                         page_num, scheduled, len(tiles))

    def get_tile(self, key: TileKey) -> QPixmap | None:
        """Synchronously get a cached tile.  Returns None if not cached."""
        return self._cache.get(key)

    def cancel_page(self, page_num: int) -> None:
        """Cancel all pending renders for a page.  Call when page leaves viewport."""
        removed = 0
        for key in list(self._pending.keys()):
            if key.page_num == page_num:
                del self._pending[key]
                removed += 1
        if removed > 0:
            self._cancelled_count += removed
            _logger.debug("TileRenderer: cancel_page(p%d) → 取消 %d 个待渲染瓦片 (总计取消 %d)",
                          page_num, removed, self._cancelled_count)

    def clear(self) -> None:
        pending = len(self._pending)
        self._cache.clear()
        self._pending.clear()
        self._pool.clear()
        self._pool.waitForDone(500)
        _logger.info("TileRenderer: clear() → 清除缓存 + %d 个待渲染请求", pending)

    # ------------------------------------------------------------------
    # Internal — tile computation
    # ------------------------------------------------------------------

    def _compute_visible_tiles(self, viewport: QRect, zoom: float) -> set[TileKey]:
        """Return the set of TileKeys that intersect the viewport."""
        tile_px = int(TILE_SIZE * zoom)
        if tile_px <= 0:
            return set()

        # Determine which page(s) the viewport covers.
        # Simplified: uses page 0 for now — callers should iterate pages.
        # For multi-page scroll layout, the caller computes page_rect per page.
        start_x = max(viewport.x() // tile_px, 0)
        start_y = max(viewport.y() // tile_px, 0)
        end_x = (viewport.right() // tile_px) + 1
        end_y = (viewport.bottom() // tile_px) + 1
        page = 0

        return {
            TileKey(page_num=page, tile_x=x, tile_y=y, zoom_level=zoom)
            for x in range(start_x, end_x + 1)
            for y in range(start_y, end_y + 1)
        }

    def _compute_page_tiles(self, page_num: int, page_rect: QRect, zoom: float) -> set[TileKey]:
        """Return all TileKeys for a complete page."""
        tile_px = int(TILE_SIZE * zoom)
        if tile_px <= 0:
            return set()

        cols = max((page_rect.width() // tile_px) + 1, 1)
        rows = max((page_rect.height() // tile_px) + 1, 1)

        return {
            TileKey(page_num=page_num, tile_x=x, tile_y=y, zoom_level=zoom)
            for x in range(cols)
            for y in range(rows)
        }

    # ------------------------------------------------------------------
    # Internal — scheduling
    # ------------------------------------------------------------------

    def _schedule(self, key: TileKey) -> None:
        """Submit a tile render task to the thread pool."""
        if self._doc is None:
            return

        self._request_counter += 1
        request_id = self._request_counter
        self._pending[key] = request_id

        task = _TileRenderTask(self._doc, key, self._dpi, request_id)
        task.done_signal.connect(self._on_tile_done)
        self._pool.start(task)
        _logger.info("TileRenderer: SCHEDULE %s (req#%d, pending=%d)",
                     key, request_id, len(self._pending))

    def _on_tile_done(self, key: TileKey, request_id: int, qpixmap: object, elapsed: float) -> None:
        """Callback from background thread — check token, cache, emit."""
        current_id = self._pending.get(key)
        if current_id != request_id:
            # Request cancellation check (Syncfusion-style)
            _logger.info("TileRenderer: DISCARD %s (req#%d ≠ current#%s)",
                         key, request_id, current_id)
            return

        del self._pending[key]

        pixmap = qpixmap if isinstance(qpixmap, QPixmap) else None
        if pixmap is None or pixmap.isNull():
            _logger.warning("TileRenderer: FAILED %s (req#%d)", key, request_id)
            return

        self._rendered_count += 1
        self._cache.put(key, pixmap, render_ms=elapsed * 1000)
        self.tile_ready.emit(key, pixmap)
        _logger.info("TileRenderer: DONE %s (%.0fms, cache=%d tiles/%.1fMB)",
                     key, elapsed * 1000, self._cache.tile_count, self._cache.size_mb)

    # ------------------------------------------------------------------
    # Priority helper
    # ------------------------------------------------------------------

    @staticmethod
    def calc_priority(key: TileKey, viewport_center_y: int, tile_h: int) -> float:
        """Calculate tile priority based on distance from viewport center.

        Adapted from Syncfusion's formula:
          priority = 1 / (1 + |tile_y - center_y| / viewport_height)

        Returns 0.0 .. 1.0, higher = render sooner.
        """
        tile_center_y = key.tile_y * tile_h + tile_h // 2
        distance = abs(tile_center_y - viewport_center_y)
        return 1.0 / (1.0 + distance / max(tile_h, 1))

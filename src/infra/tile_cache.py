"""
Memory tile cache with LRU eviction — adapted from qpageview (frescobaldi/qpageview).

Key differences from qpageview:
  - Cache key is TileKey (page_num, tile_x, tile_y, zoom_level) instead of qpageview's
    Key(group, ident, rotation, width, height).  Our TileKey is simpler because we
    always render at fixed DPI with PyMuPDF and don't need the rotation dimension.
  - Uses a flat OrderedDict for O(1) LRU moves instead of qpageview's
    WeakKeyDictionary tree with full-sort-on-eviction.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import NamedTuple

from PySide6.QtGui import QPixmap

_logger = logging.getLogger(__name__)


class TileKey(NamedTuple):
    """Globally unique tile identifier."""
    page_num: int
    tile_x: int
    tile_y: int
    zoom_level: float

    def __hash__(self) -> int:
        return hash((self.page_num, self.tile_x, self.tile_y, int(self.zoom_level * 100)))

    def __repr__(self) -> str:
        return f"TileKey(p{self.page_num}_x{self.tile_x}_y{self.tile_y}_z{self.zoom_level:.2f})"


class TileEntry:
    """A cached tile with metadata for LRU eviction."""
    __slots__ = ("pixmap", "byte_size", "last_access", "render_ms")

    def __init__(self, pixmap: QPixmap, render_ms: float = 0.0) -> None:
        self.pixmap = pixmap
        self.byte_size = pixmap.width() * pixmap.height() * 4  # RGBA estimate
        self.last_access = time.time()
        self.render_ms = render_ms


class TileCache:
    """In-memory tile cache with generational LRU eviction.

    Adapted from qpageview's ImageCache.  Uses OrderedDict for O(1) LRU moves.
    """

    def __init__(self, max_size_bytes: int = 200 * 1024 * 1024) -> None:
        self._max_size: int = max_size_bytes
        self._current_size: int = 0
        self._cache: OrderedDict[TileKey, TileEntry] = OrderedDict()
        self._hits: int = 0
        self._misses: int = 0
        self._evictions: int = 0
        _logger.info("TileCache: 初始化 (max=%.1fMB)", max_size_bytes / (1024 * 1024))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: TileKey) -> QPixmap | None:
        """Return cached pixmap or None.  Updates LRU position on hit."""
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None
        self._hits += 1
        entry.last_access = time.time()
        self._cache.move_to_end(key)
        _logger.debug("TileCache: HIT %s (render_ms=%.0f, hit_rate=%.1f%%)",
                      key, entry.render_ms,
                      100 * self._hits / (self._hits + self._misses) if (self._hits + self._misses) > 0 else 0)
        return entry.pixmap

    def put(self, key: TileKey, pixmap: QPixmap, render_ms: float = 0.0) -> None:
        """Insert a tile into the cache.  Evicts old entries if over capacity."""
        entry = TileEntry(pixmap, render_ms)

        if key in self._cache:
            old = self._cache[key]
            self._current_size -= old.byte_size
            _logger.debug("TileCache: REPLACE %s (%.0fms)", key, render_ms)
        else:
            _logger.debug("TileCache: PUT %s (%.0fms, size=%d)", key, render_ms, self.tile_count + 1)

        self._cache[key] = entry
        self._cache.move_to_end(key)
        self._current_size += entry.byte_size

        self._evict_if_needed()

    def invalidate_page(self, page_num: int) -> None:
        """Remove all tiles for a specific page."""
        to_remove = [k for k in self._cache if k.page_num == page_num]
        if not to_remove:
            return
        freed = 0
        for key in to_remove:
            freed += self._cache[key].byte_size
            del self._cache[key]
        self._current_size -= freed
        _logger.debug("TileCache: invalidate_page(%d) → 清除 %d 个瓦片, 释放 %.1fKB",
                      page_num, len(to_remove), freed / 1024.0)

    def clear(self) -> None:
        count = len(self._cache)
        self._cache.clear()
        self._current_size = 0
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        _logger.info("TileCache: clear() → 清除 %d 个瓦片", count)

    @property
    def size_mb(self) -> float:
        return self._current_size / (1024 * 1024)

    @property
    def tile_count(self) -> int:
        return len(self._cache)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_if_needed(self) -> None:
        """Evict oldest-accessed tiles until we're at 90% capacity."""
        if self._current_size <= self._max_size:
            return

        target = int(self._max_size * 0.9)
        evicted = 0
        freed_bytes = 0
        while self._current_size > target and self._cache:
            key, entry = next(iter(self._cache.items()))
            self._current_size -= entry.byte_size
            freed_bytes += entry.byte_size
            del self._cache[key]
            evicted += 1

        self._evictions += evicted
        _logger.info("TileCache: EVICT %d 个瓦片 (释放 %.1fKB, 当前 %.1fMB/%d 个)",
                     evicted, freed_bytes / 1024.0, self.size_mb, self.tile_count)

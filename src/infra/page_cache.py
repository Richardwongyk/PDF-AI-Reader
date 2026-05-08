"""
Thread-safe LRU page pixmap cache — adapted from PDFCrop (inoueakimitsu/pdfcrop).

Key design decisions:
  - Cache key = f"{doc_path}_{page_num}_{scale_factor}"
  - High-res → low-res downscale on cache hit (avoid re-rendering)
  - Size-based LRU eviction when approaching max_cache_size (default 1 GB)
  - threading.Lock for concurrent access from QThreadPool workers
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import fitz
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

_logger = logging.getLogger(__name__)


class PageCache:
    """Standalone LRU cache for rendered QPixmap pages.

    Registered as a singleton in ServiceContainer so all render paths
    share the same cache.
    """

    def __init__(self, max_cache_size_mb: float = 1024.0) -> None:
        self.cache: dict[str, QPixmap] = {}
        self._lock = threading.Lock()
        self.max_size_mb = max_cache_size_mb
        self.current_size_mb: float = 0.0
        self.last_accessed: dict[str, float] = {}
        self._hits: int = 0
        self._misses: int = 0
        _logger.info("PageCache: 初始化 (max=%.0fMB)", max_cache_size_mb)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_cache_key(self, doc_path: str, page_num: int, scale_factor: float) -> str:
        return f"{doc_path}_{page_num}_{scale_factor}"

    def get(self, doc_path: str, page_num: int, scale_factor: float) -> QPixmap | None:
        """Return cached pixmap or None. Falls back to downscaling a higher-res cache hit."""
        key = self.get_cache_key(doc_path, page_num, scale_factor)

        with self._lock:
            if key in self.cache:
                self._hits += 1
                self.last_accessed[key] = time.time()
                _logger.debug("PageCache: HIT %s", key)
                return self.cache[key]

            # Try downscaling from higher resolution
            prefix = f"{doc_path}_{page_num}_"
            for existing_key in list(self.cache.keys()):
                if not existing_key.startswith(prefix):
                    continue
                try:
                    existing_scale = float(existing_key.rsplit("_", 1)[1])
                except (ValueError, IndexError):
                    continue
                if existing_scale > scale_factor:
                    self._hits += 1
                    hi_res = self.cache[existing_key]
                    ratio = scale_factor / existing_scale
                    scaled = hi_res.scaled(
                        int(hi_res.width() * ratio),
                        int(hi_res.height() * ratio),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    self._insert(key, scaled)
                    _logger.debug("PageCache: HIT %s (downscaled from %.2f)", key, existing_scale)
                    return scaled

        self._misses += 1
        _logger.debug("PageCache: MISS %s (hits=%d misses=%d)", key, self._hits, self._misses)
        return None

    def put(self, doc_path: str, page_num: int, page: fitz.Page, scale_factor: float = 1.0) -> QPixmap:
        """Render a fitz.Page and cache the resulting QPixmap."""
        key = self.get_cache_key(doc_path, page_num, scale_factor)
        t0 = time.perf_counter()

        mat = fitz.Matrix(scale_factor, scale_factor)
        pix = page.get_displaylist().get_pixmap(matrix=mat, alpha=False)
        img_data = pix.tobytes("ppm")
        qimage = QImage.fromData(img_data)
        pixmap = QPixmap.fromImage(qimage)
        render_ms = (time.perf_counter() - t0) * 1000

        with self._lock:
            estimated_mb = (pix.width * pix.height * 4) / (1024 * 1024)
            evicted = 0
            while self.current_size_mb + estimated_mb > self.max_size_mb and self.cache:
                self._evict_oldest()
                evicted += 1
            self._insert(key, pixmap)
            self.current_size_mb += estimated_mb
            self.last_accessed[key] = time.time()

        _logger.debug("PageCache: PUT %s (%.0fms, %.1fMB, evicted=%d, total=%.1fMB/%d)",
                      key, render_ms, estimated_mb, evicted, self.current_size_mb, len(self.cache))
        return pixmap

    def clear_document(self, doc_path: str) -> None:
        """Remove all cached pages for a specific document."""
        prefix = f"{doc_path}_"
        with self._lock:
            removed = 0
            for key in list(self.cache.keys()):
                if key.startswith(prefix):
                    self._remove_key(key)
                    removed += 1
        _logger.info("PageCache: clear_document(%s) → 删除 %d 页 (剩余 %.1fMB/%d)",
                     Path(doc_path).name, removed, self.current_size_mb, len(self.cache))

    def clear(self) -> None:
        """Clear the entire cache."""
        with self._lock:
            count = len(self.cache)
            self.cache.clear()
            self.last_accessed.clear()
            self.current_size_mb = 0.0
            self._hits = 0
            self._misses = 0
        _logger.info("PageCache: clear() → 删除 %d 页", count)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _insert(self, key: str, pixmap: QPixmap) -> None:
        self.cache[key] = pixmap

    def _remove_key(self, key: str) -> None:
        if key in self.cache:
            pixmap = self.cache[key]
            mb = (pixmap.width() * pixmap.height() * 4) / (1024 * 1024)
            self.current_size_mb -= mb
            del self.cache[key]
            self.last_accessed.pop(key, None)

    def _evict_oldest(self) -> None:
        if not self.last_accessed:
            return
        oldest_key = min(self.last_accessed.items(), key=lambda x: x[1])[0]
        self._remove_key(oldest_key)

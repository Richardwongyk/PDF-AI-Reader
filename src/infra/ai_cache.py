"""
SQLite-backed persistent cache for AI results (translation, OCR, summary).

Motivation: avoid repeated LLM calls when the user opens/closes the same
document or re-clicks the same paragraph.  Typical cache hit saves 2-5 s.

Cache key: (block_id, doc_hash, result_type)
"""

from __future__ import annotations

import logging
import sqlite3

from src.infra.file_hash import compute_sha256
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


class AICache:
    """Persistent cache for AI-generated results.

    Thread-safe via SQLite's built-in serialized mode (check_same_thread=False).
    """

    def __init__(self, db_path: str = "data/ai_cache.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_cache (
                block_id    TEXT NOT NULL,
                doc_hash    TEXT NOT NULL,
                result_type TEXT NOT NULL CHECK(result_type IN ('translation','ocr','summary','answer')),
                content     TEXT NOT NULL,
                model       TEXT DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (block_id, doc_hash, result_type)
            )
        """)
        self._conn.commit()
        self._hits: int = 0
        self._misses: int = 0
        _logger.info("AICache: 初始化 %s", db_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def hash_file(filepath: str) -> str:
        """Compute SHA-256 hash of a file for cache invalidation."""
        t0 = time.perf_counter()
        result = compute_sha256(filepath)
        _logger.debug("AICache: hash_file(%s) = %s (%.2fs)", Path(filepath).name, result[:16], time.perf_counter() - t0)
        return result

    def get(self, block_id: str, doc_hash: str, result_type: str) -> str | None:
        """Return cached result or None."""
        row = self._conn.execute(
            "SELECT content, model, created_at FROM ai_cache WHERE block_id=? AND doc_hash=? AND result_type=?",
            (block_id, doc_hash, result_type),
        ).fetchone()
        if row is None:
            self._misses += 1
            _logger.debug("AICache: MISS %s/%s/%s", block_id, doc_hash[:12], result_type)
            return None

        self._hits += 1
        hr = 100 * self._hits / (self._hits + self._misses) if (self._hits + self._misses) > 0 else 0
        _logger.info("AICache: HIT  %s/%s/%s (model=%s, created=%s, hit_rate=%.0f%%)",
                     block_id, doc_hash[:12], result_type, row[1], row[2], hr)
        return row[0]

    def put(self, block_id: str, doc_hash: str, result_type: str, content: str, model: str = "") -> None:
        """Insert or update a cached result."""
        _logger.info("AICache: PUT %s/%s/%s (model=%s, len=%d)",
                     block_id, doc_hash[:12], result_type, model, len(content))
        self._conn.execute(
            """INSERT OR REPLACE INTO ai_cache
               (block_id, doc_hash, result_type, content, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (block_id, doc_hash, result_type, content, model, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def remove(self, block_id: str, doc_hash: str, result_type: str) -> None:
        """Remove a single cache entry."""
        _logger.debug("AICache: REMOVE %s/%s/%s", block_id, doc_hash[:12], result_type)
        self._conn.execute(
            "DELETE FROM ai_cache WHERE block_id=? AND doc_hash=? AND result_type=?",
            (block_id, doc_hash, result_type),
        )
        self._conn.commit()

    def clear_document(self, doc_hash: str) -> None:
        """Remove all cached results for a specific document."""
        count = self._conn.execute(
            "SELECT COUNT(*) FROM ai_cache WHERE doc_hash=?", (doc_hash,)
        ).fetchone()[0]
        self._conn.execute("DELETE FROM ai_cache WHERE doc_hash=?", (doc_hash,))
        self._conn.commit()
        _logger.info("AICache: clear_document(%s) → 删除 %d 条缓存", doc_hash[:12], count)

    def clear_all(self) -> None:
        """Remove every cached entry."""
        count = self._conn.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0]
        self._conn.execute("DELETE FROM ai_cache")
        self._conn.commit()
        _logger.info("AICache: clear_all() → 删除 %d 条缓存", count)

    def stats(self, doc_hash: str | None = None) -> dict[str, Any]:
        """Return hit/miss stats for monitoring."""
        total = self._hits + self._misses
        base = {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0.0,
        }
        if doc_hash:
            rows = self._conn.execute(
                "SELECT result_type, COUNT(*) FROM ai_cache WHERE doc_hash=? GROUP BY result_type",
                (doc_hash,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT result_type, COUNT(*) FROM ai_cache GROUP BY result_type"
            ).fetchall()
        base["by_type"] = {r[0]: r[1] for r in rows}
        return base

    def close(self) -> None:
        _logger.info("AICache: close() — hits=%d misses=%d hit_rate=%.0f%%",
                     self._hits, self._misses,
                     100 * self._hits / (self._hits + self._misses) if (self._hits + self._misses) > 0 else 0)
        self._conn.close()

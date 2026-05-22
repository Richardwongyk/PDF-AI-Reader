"""
SQLite-backed persistent cache for AI results (translation, OCR, summary).

Motivation: avoid repeated LLM calls when the user opens/closes the same
document or re-clicks the same paragraph.  Typical cache hit saves 2-5 s.

Cache key: (block_id, doc_hash, result_type, content_hash)
"""

import logging
import sqlite3
import hashlib

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
                content_hash TEXT NOT NULL DEFAULT '',
                content     TEXT NOT NULL,
                model       TEXT DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (block_id, doc_hash, result_type, content_hash)
            )
        """)
        self._conn.commit()
        self._migrate_content_hash()
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

    @staticmethod
    def hash_text(content: str) -> str:
        """Compute a stable content hash for cache invalidation."""
        return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()

    def get(
        self, block_id: str, doc_hash: str, result_type: str,
        content_hash: str = "",
    ) -> str | None:
        """Return cached result or None."""
        row = self._conn.execute(
            """
            SELECT content, model, created_at FROM ai_cache
            WHERE block_id=? AND doc_hash=? AND result_type=? AND content_hash=?
            """,
            (block_id, doc_hash, result_type, content_hash),
        ).fetchone()
        if row is None:
            if result_type == "translation" and content_hash:
                legacy = self._get_legacy_translation(block_id, doc_hash)
                if legacy is not None:
                    return legacy
            self._misses += 1
            _logger.debug(
                "AICache: MISS %s/%s/%s/%s",
                block_id, doc_hash[:12], result_type, content_hash[:12],
            )
            return None

        self._hits += 1
        hr = 100 * self._hits / (self._hits + self._misses) if (self._hits + self._misses) > 0 else 0
        _logger.info(
            "AICache: HIT  %s/%s/%s/%s (model=%s, created=%s, hit_rate=%.0f%%)",
            block_id, doc_hash[:12], result_type, content_hash[:12], row[1], row[2], hr,
        )
        return row[0]

    def _get_legacy_translation(self, block_id: str, doc_hash: str) -> str | None:
        """Best-effort fallback for pre-content-hash translation rows."""
        row = self._conn.execute(
            """
            SELECT content, model, created_at FROM ai_cache
            WHERE block_id=? AND doc_hash=? AND result_type='translation'
              AND content_hash=''
            """,
            (block_id, doc_hash),
        ).fetchone()
        if row is None:
            return None
        self._hits += 1
        hr = 100 * self._hits / (self._hits + self._misses) if (self._hits + self._misses) > 0 else 0
        _logger.info(
            "AICache: HIT legacy %s/%s/translation (model=%s, created=%s, hit_rate=%.0f%%)",
            block_id, doc_hash[:12], row[1], row[2], hr,
        )
        return row[0]

    def put(
        self, block_id: str, doc_hash: str, result_type: str, content: str,
        model: str = "", content_hash: str = "",
    ) -> None:
        """Insert or update a cached result."""
        _logger.info(
            "AICache: PUT %s/%s/%s/%s (model=%s, len=%d)",
            block_id, doc_hash[:12], result_type, content_hash[:12], model, len(content),
        )
        self._conn.execute(
            """INSERT OR REPLACE INTO ai_cache
               (block_id, doc_hash, result_type, content_hash, content, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                block_id, doc_hash, result_type, content_hash, content, model,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def remove(
        self, block_id: str, doc_hash: str, result_type: str,
        content_hash: str = "",
    ) -> None:
        """Remove a single cache entry."""
        _logger.debug(
            "AICache: REMOVE %s/%s/%s/%s",
            block_id, doc_hash[:12], result_type, content_hash[:12],
        )
        self._conn.execute(
            """
            DELETE FROM ai_cache
            WHERE block_id=? AND doc_hash=? AND result_type=? AND content_hash=?
            """,
            (block_id, doc_hash, result_type, content_hash),
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

    def _migrate_content_hash(self) -> None:
        """Add content_hash to older cache DBs without preserving unsafe legacy hits."""
        columns = self._conn.execute("PRAGMA table_info(ai_cache)").fetchall()
        column_names = {row[1] for row in columns}
        if "content_hash" in column_names:
            return

        self._conn.execute("ALTER TABLE ai_cache RENAME TO ai_cache_legacy")
        self._conn.execute("""
            CREATE TABLE ai_cache (
                block_id    TEXT NOT NULL,
                doc_hash    TEXT NOT NULL,
                result_type TEXT NOT NULL CHECK(result_type IN ('translation','ocr','summary','answer')),
                content_hash TEXT NOT NULL DEFAULT '',
                content     TEXT NOT NULL,
                model       TEXT DEFAULT '',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (block_id, doc_hash, result_type, content_hash)
            )
        """)
        self._conn.execute("""
            INSERT OR REPLACE INTO ai_cache
            (block_id, doc_hash, result_type, content_hash, content, model, created_at)
            SELECT block_id, doc_hash, result_type, '', content, model, created_at
            FROM ai_cache_legacy
            WHERE result_type != 'translation'
        """)
        self._conn.execute("DROP TABLE ai_cache_legacy")
        self._conn.commit()

"""Persistent formula OCR task queue.

The queue records what should be scanned, what has already been recognized,
and which tasks should be retried later. It deliberately stores metadata only;
image extraction and OCR still run in ``FormulaIndexFlow`` workers so the UI
thread remains out of the hot path.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from src.core.models import DocumentBlock

FormulaTaskStatus = Literal["queued", "running", "done", "failed", "skipped"]
FormulaPageScanStatus = Literal["queued", "running", "done", "failed", "skipped"]


@dataclass(frozen=True)
class FormulaIndexTask:
    """SQLite-backed formula OCR job."""

    doc_hash: str
    filepath: str
    block_id: str
    page_num: int
    bbox: tuple[float, float, float, float]
    priority: float
    status: FormulaTaskStatus
    content_hash: str
    image_hash: str = ""
    latex: str = ""
    model: str = ""
    error: str = ""
    attempts: int = 0
    updated_at: str = ""


@dataclass(frozen=True)
class FormulaPageScanTask:
    """SQLite-backed page-level formula detection job."""

    doc_hash: str
    filepath: str
    page_num: int
    priority: float
    status: FormulaPageScanStatus
    error: str = ""
    attempts: int = 0
    updated_at: str = ""


class FormulaIndexStore:
    """Persist pending formula OCR jobs in SQLite."""

    def __init__(self, db_path: str = "data/formula_index_jobs.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS formula_index_jobs (
                doc_hash     TEXT NOT NULL,
                filepath     TEXT NOT NULL,
                block_id     TEXT NOT NULL,
                page_num     INTEGER NOT NULL,
                bbox_json    TEXT NOT NULL,
                priority     REAL NOT NULL DEFAULT 0,
                status       TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                image_hash   TEXT NOT NULL DEFAULT '',
                latex        TEXT NOT NULL DEFAULT '',
                model        TEXT NOT NULL DEFAULT '',
                error        TEXT NOT NULL DEFAULT '',
                attempts     INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                PRIMARY KEY (doc_hash, block_id)
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_formula_jobs_status "
            "ON formula_index_jobs(doc_hash, status, priority DESC, page_num ASC)"
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS formula_page_scan_jobs (
                doc_hash   TEXT NOT NULL,
                filepath   TEXT NOT NULL,
                page_num   INTEGER NOT NULL,
                priority   REAL NOT NULL DEFAULT 0,
                status     TEXT NOT NULL,
                error      TEXT NOT NULL DEFAULT '',
                attempts   INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (doc_hash, page_num)
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_formula_page_scan_status "
            "ON formula_page_scan_jobs(doc_hash, status, priority DESC, page_num ASC)"
        )
        self._conn.commit()
        self._lock = threading.Lock()

    def enqueue_blocks(
        self,
        doc_hash: str,
        filepath: str,
        blocks: list[DocumentBlock],
        priority_pages: set[int] | None = None,
    ) -> int:
        """Insert or refresh queued jobs for OCR-pending formula blocks."""
        if not doc_hash or not filepath or not blocks:
            return 0
        priority_pages = priority_pages or set()
        now = _now()
        inserted = 0
        with self._lock:
            for block in blocks:
                content_hash = self.content_hash(block)
                priority = self.priority_for_block(block, priority_pages)
                bbox_json = json.dumps(list(block.bbox), separators=(",", ":"))
                row = self._conn.execute(
                    """SELECT status, content_hash FROM formula_index_jobs
                       WHERE doc_hash=? AND block_id=?""",
                    (doc_hash, block.id),
                ).fetchone()
                if row and row[0] == "done" and row[1] == content_hash:
                    continue
                self._conn.execute(
                    """INSERT INTO formula_index_jobs
                       (doc_hash, filepath, block_id, page_num, bbox_json, priority,
                        status, content_hash, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                       ON CONFLICT(doc_hash, block_id) DO UPDATE SET
                         filepath=excluded.filepath,
                         page_num=excluded.page_num,
                         bbox_json=excluded.bbox_json,
                         priority=max(formula_index_jobs.priority, excluded.priority),
                         status=CASE
                           WHEN formula_index_jobs.status='done'
                            AND formula_index_jobs.content_hash=excluded.content_hash
                           THEN 'done'
                           ELSE 'queued'
                         END,
                         content_hash=excluded.content_hash,
                         error='',
                         updated_at=excluded.updated_at""",
                    (
                        doc_hash,
                        filepath,
                        block.id,
                        block.page_num,
                        bbox_json,
                        priority,
                        content_hash,
                        now,
                        now,
                    ),
                )
                inserted += 1
            self._conn.commit()
        return inserted

    def enqueue_pages(
        self,
        doc_hash: str,
        filepath: str,
        pages: list[int] | range,
        priority_pages: set[int] | None = None,
    ) -> int:
        """Insert or refresh page-level MFD jobs for a document."""
        if not doc_hash or not filepath:
            return 0
        page_nums: list[int] = []
        for page in pages:
            try:
                page_num = int(page)
            except (TypeError, ValueError):
                continue
            if page_num >= 0:
                page_nums.append(page_num)
        page_nums = sorted(set(page_nums))
        if not page_nums:
            return 0
        priority_pages = priority_pages or set()
        now = _now()
        inserted = 0
        with self._lock:
            for page_num in page_nums:
                priority = self.priority_for_page(page_num, priority_pages)
                row = self._conn.execute(
                    """SELECT status FROM formula_page_scan_jobs
                       WHERE doc_hash=? AND page_num=?""",
                    (doc_hash, page_num),
                ).fetchone()
                self._conn.execute(
                    """INSERT INTO formula_page_scan_jobs
                       (doc_hash, filepath, page_num, priority, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'queued', ?, ?)
                       ON CONFLICT(doc_hash, page_num) DO UPDATE SET
                         filepath=excluded.filepath,
                         priority=max(formula_page_scan_jobs.priority, excluded.priority),
                         status=CASE
                           WHEN formula_page_scan_jobs.status='done'
                           THEN 'done'
                           ELSE 'queued'
                         END,
                         error='',
                         updated_at=excluded.updated_at""",
                    (doc_hash, filepath, page_num, priority, now, now),
                )
                if not row or str(row[0]) != "done":
                    inserted += 1
            self._conn.commit()
        return inserted

    def mark_running(self, doc_hash: str, block_ids: list[str]) -> None:
        self._update_status(doc_hash, block_ids, "running", increment_attempts=True)

    def mark_done(
        self,
        doc_hash: str,
        block_id: str,
        latex: str,
        image_hash: str,
        model: str = "pix2text-mfr",
    ) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """UPDATE formula_index_jobs
                   SET status='done', latex=?, image_hash=?, model=?, error='', updated_at=?
                   WHERE doc_hash=? AND block_id=?""",
                (latex, image_hash, model, now, doc_hash, block_id),
            )
            self._conn.commit()

    def mark_failed(self, doc_hash: str, block_id: str, error: str) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """UPDATE formula_index_jobs
                   SET status='failed', error=?, updated_at=?
                   WHERE doc_hash=? AND block_id=?""",
                (error[:500], now, doc_hash, block_id),
            )
            self._conn.commit()

    def mark_skipped(self, doc_hash: str, block_id: str, reason: str) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """UPDATE formula_index_jobs
                   SET status='skipped', error=?, updated_at=?
                   WHERE doc_hash=? AND block_id=?""",
                (reason[:500], now, doc_hash, block_id),
            )
            self._conn.commit()

    def mark_pages_running(self, doc_hash: str, page_nums: list[int]) -> None:
        self._update_page_status(doc_hash, page_nums, "running", increment_attempts=True)

    def mark_pages_done(self, doc_hash: str, page_nums: list[int]) -> None:
        self._update_page_status(doc_hash, page_nums, "done")

    def mark_page_failed(self, doc_hash: str, page_num: int, error: str) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """UPDATE formula_page_scan_jobs
                   SET status='failed', error=?, updated_at=?
                   WHERE doc_hash=? AND page_num=?""",
                (error[:500], now, doc_hash, page_num),
            )
            self._conn.commit()

    def counts(self, doc_hash: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT status, COUNT(*) FROM formula_index_jobs
                   WHERE doc_hash=? GROUP BY status""",
                (doc_hash,),
            ).fetchall()
        return {str(status): int(count) for status, count in rows}

    def pending_count(self, doc_hash: str) -> int:
        counts = self.counts(doc_hash)
        return counts.get("queued", 0) + counts.get("running", 0)

    def page_counts(self, doc_hash: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT status, COUNT(*) FROM formula_page_scan_jobs
                   WHERE doc_hash=? GROUP BY status""",
                (doc_hash,),
            ).fetchall()
        return {str(status): int(count) for status, count in rows}

    def page_pending_count(self, doc_hash: str) -> int:
        counts = self.page_counts(doc_hash)
        return counts.get("queued", 0) + counts.get("running", 0)

    def list_tasks(
        self,
        doc_hash: str,
        statuses: set[str] | None = None,
        limit: int = 100,
    ) -> list[FormulaIndexTask]:
        params: list[object] = [doc_hash]
        where = "doc_hash=?"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where += f" AND status IN ({placeholders})"
            params.extend(sorted(statuses))
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT doc_hash, filepath, block_id, page_num, bbox_json,
                           priority, status, content_hash, image_hash, latex,
                           model, error, attempts, updated_at
                    FROM formula_index_jobs
                    WHERE {where}
                    ORDER BY priority DESC, page_num ASC, block_id ASC
                    LIMIT ?""",
                params,
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def list_page_tasks(
        self,
        doc_hash: str,
        statuses: set[str] | None = None,
        limit: int = 100,
    ) -> list[FormulaPageScanTask]:
        params: list[object] = [doc_hash]
        where = "doc_hash=?"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where += f" AND status IN ({placeholders})"
            params.extend(sorted(statuses))
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT doc_hash, filepath, page_num, priority, status,
                           error, attempts, updated_at
                    FROM formula_page_scan_jobs
                    WHERE {where}
                    ORDER BY priority DESC, page_num ASC
                    LIMIT ?""",
                params,
            ).fetchall()
        return [
            FormulaPageScanTask(
                doc_hash=str(row[0]),
                filepath=str(row[1]),
                page_num=int(row[2]),
                priority=float(row[3]),
                status=str(row[4]),  # type: ignore[arg-type]
                error=str(row[5]),
                attempts=int(row[6]),
                updated_at=str(row[7]),
            )
            for row in rows
        ]

    def _update_status(
        self,
        doc_hash: str,
        block_ids: list[str],
        status: FormulaTaskStatus,
        increment_attempts: bool = False,
    ) -> None:
        if not doc_hash or not block_ids:
            return
        now = _now()
        attempts_expr = "attempts + 1" if increment_attempts else "attempts"
        with self._lock:
            self._conn.executemany(
                f"""UPDATE formula_index_jobs
                    SET status=?, attempts={attempts_expr}, updated_at=?
                    WHERE doc_hash=? AND block_id=?""",
                [(status, now, doc_hash, block_id) for block_id in block_ids],
            )
            self._conn.commit()

    def _update_page_status(
        self,
        doc_hash: str,
        page_nums: list[int],
        status: FormulaPageScanStatus,
        increment_attempts: bool = False,
    ) -> None:
        if not doc_hash or not page_nums:
            return
        now = _now()
        attempts_expr = "attempts + 1" if increment_attempts else "attempts"
        with self._lock:
            self._conn.executemany(
                f"""UPDATE formula_page_scan_jobs
                    SET status=?, attempts={attempts_expr}, updated_at=?
                    WHERE doc_hash=? AND page_num=?""",
                [(status, now, doc_hash, int(page_num)) for page_num in page_nums],
            )
            self._conn.commit()

    @staticmethod
    def content_hash(block: DocumentBlock) -> str:
        digest = hashlib.sha256()
        digest.update(block.id.encode("utf-8", errors="ignore"))
        digest.update(str(block.page_num).encode("ascii"))
        digest.update("|".join(f"{value:.3f}" for value in block.bbox).encode("ascii"))
        digest.update((block.content or "").encode("utf-8", errors="ignore"))
        return digest.hexdigest()

    @staticmethod
    def priority_for_block(block: DocumentBlock, priority_pages: set[int]) -> float:
        page_boost = 1000.0 if block.page_num in priority_pages else 0.0
        score = float(block.metadata.get("formula_score", 0.0) or 0.0)
        area = max((block.bbox[2] - block.bbox[0]) * (block.bbox[3] - block.bbox[1]), 0.0)
        return page_boost + score * 100.0 + min(area / 1000.0, 100.0) - block.page_num * 0.001

    @staticmethod
    def priority_for_page(page_num: int, priority_pages: set[int]) -> float:
        page_boost = 1000.0 if page_num in priority_pages else 0.0
        return page_boost - int(page_num) * 0.001

    @staticmethod
    def _row_to_task(row: tuple[object, ...]) -> FormulaIndexTask:
        bbox_values = json.loads(str(row[4]))
        bbox = tuple(float(value) for value in bbox_values)
        return FormulaIndexTask(
            doc_hash=str(row[0]),
            filepath=str(row[1]),
            block_id=str(row[2]),
            page_num=int(row[3]),
            bbox=bbox,  # type: ignore[arg-type]
            priority=float(row[5]),
            status=str(row[6]),  # type: ignore[arg-type]
            content_hash=str(row[7]),
            image_hash=str(row[8]),
            latex=str(row[9]),
            model=str(row[10]),
            error=str(row[11]),
            attempts=int(row[12]),
            updated_at=str(row[13]),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

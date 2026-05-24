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
from enum import StrEnum
from pathlib import Path
from typing import Literal

from src.core.models import DocumentBlock

FormulaTaskStatus = Literal["queued", "running", "done", "failed", "skipped"]
FormulaPageScanStatus = Literal["queued", "running", "done", "failed", "skipped"]
FormulaRoundTarget = Literal["block", "page"]


class FormulaScanRound(StrEnum):
    """Persisted stages for multi-pass formula parsing."""

    PDF_STRUCTURE = "r0_pdf_structure"
    CACHED_RECOGNITION = "r1_cached_recognition"
    LOCAL_HIGH_PRECISION = "r2_local_high_precision"
    CLOUD_SEMANTIC_REVIEW = "r3_cloud_semantic_review"
    KNOWLEDGE_GRAPH = "r4_knowledge_graph"
    KNOWLEDGE_INCREMENTAL_UPDATE = "r5_knowledge_incremental_update"


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
    scan_round: str = FormulaScanRound.CACHED_RECOGNITION.value
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
    scan_round: str = FormulaScanRound.PDF_STRUCTURE.value
    error: str = ""
    attempts: int = 0
    updated_at: str = ""


@dataclass(frozen=True)
class FormulaRoundRecord:
    """Generic persisted record for one formula parsing round and target."""

    doc_hash: str
    filepath: str
    scan_round: str
    target_type: FormulaRoundTarget
    target_id: str
    page_num: int
    priority: float
    status: FormulaTaskStatus
    result_json: dict[str, object]
    elapsed_ms: int = 0
    error: str = ""
    attempts: int = 0
    updated_at: str = ""


@dataclass(frozen=True)
class FormulaRecognitionRecord:
    """One persisted output from a formula extraction or recognition backend."""

    result_id: str
    candidate_id: str
    doc_hash: str
    stage: str
    model: str
    model_version: str
    preprocess_version: str
    input_hash: str
    latex: str
    normalized_latex: str
    score: float | None
    duration_ms: int
    peak_memory_mb: float | None
    warnings: tuple[str, ...]
    evidence: dict[str, object]
    accepted: bool
    created_at: str = ""


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
                scan_round   TEXT NOT NULL DEFAULT 'r1_cached_recognition',
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
                scan_round TEXT NOT NULL DEFAULT 'r0_pdf_structure',
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
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS formula_round_jobs (
                doc_hash    TEXT NOT NULL,
                filepath    TEXT NOT NULL,
                scan_round  TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_id   TEXT NOT NULL,
                page_num    INTEGER NOT NULL,
                priority    REAL NOT NULL DEFAULT 0,
                status      TEXT NOT NULL,
                result_json TEXT NOT NULL DEFAULT '{}',
                elapsed_ms  INTEGER NOT NULL DEFAULT 0,
                error       TEXT NOT NULL DEFAULT '',
                attempts    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (doc_hash, scan_round, target_type, target_id)
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_formula_round_jobs_status "
            "ON formula_round_jobs(doc_hash, scan_round, status, priority DESC, page_num ASC)"
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS formula_recognition_results (
                result_id          TEXT PRIMARY KEY,
                candidate_id       TEXT NOT NULL,
                doc_hash           TEXT NOT NULL,
                stage              TEXT NOT NULL,
                model              TEXT NOT NULL,
                model_version      TEXT NOT NULL DEFAULT '',
                preprocess_version TEXT NOT NULL DEFAULT '',
                input_hash         TEXT NOT NULL,
                latex              TEXT NOT NULL DEFAULT '',
                normalized_latex   TEXT NOT NULL DEFAULT '',
                score              REAL,
                duration_ms        INTEGER NOT NULL DEFAULT 0,
                peak_memory_mb     REAL,
                warnings_json      TEXT NOT NULL DEFAULT '[]',
                evidence_json      TEXT NOT NULL DEFAULT '{}',
                accepted           INTEGER NOT NULL DEFAULT 0,
                created_at         TEXT NOT NULL,
                UNIQUE(doc_hash, candidate_id, stage, model, model_version, preprocess_version, input_hash)
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_formula_recognition_lookup "
            "ON formula_recognition_results(doc_hash, candidate_id, stage, accepted, created_at DESC)"
        )
        self._migrate_schema()
        self._conn.commit()
        self._lock = threading.Lock()

    def enqueue_blocks(
        self,
        doc_hash: str,
        filepath: str,
        blocks: list[DocumentBlock],
        priority_pages: set[int] | None = None,
        scan_round: str | FormulaScanRound = FormulaScanRound.CACHED_RECOGNITION,
    ) -> int:
        """Insert or refresh queued jobs for OCR-pending formula blocks."""
        if not doc_hash or not filepath or not blocks:
            return 0
        priority_pages = priority_pages or set()
        round_name = _round_value(scan_round)
        now = _now()
        inserted = 0
        with self._lock:
            for block in blocks:
                content_hash = self.content_hash(block)
                priority = self.priority_for_block(block, priority_pages)
                bbox_json = json.dumps(list(block.bbox), separators=(",", ":"))
                round_row = self._conn.execute(
                    """SELECT status, result_json FROM formula_round_jobs
                       WHERE doc_hash=? AND scan_round=? AND target_type='block' AND target_id=?""",
                    (doc_hash, round_name, block.id),
                ).fetchone()
                if round_row and str(round_row[0]) == "done":
                    try:
                        round_payload = json.loads(str(round_row[1] or "{}"))
                    except json.JSONDecodeError:
                        round_payload = {}
                    if isinstance(round_payload, dict) and round_payload.get("content_hash") == content_hash:
                        continue
                row = self._conn.execute(
                    """SELECT status, content_hash, scan_round FROM formula_index_jobs
                       WHERE doc_hash=? AND block_id=?""",
                    (doc_hash, block.id),
                ).fetchone()
                if (
                    row
                    and row[0] == "done"
                    and row[1] == content_hash
                    and row[2] == round_name
                ):
                    self._enqueue_round_job_locked(
                        doc_hash=doc_hash,
                        filepath=filepath,
                        scan_round=round_name,
                        target_type="block",
                        target_id=block.id,
                        page_num=block.page_num,
                        priority=priority,
                        status="done",
                        now=now,
                    )
                    continue
                self._conn.execute(
                    """INSERT INTO formula_index_jobs
                       (doc_hash, filepath, block_id, page_num, bbox_json, priority,
                        status, content_hash, scan_round, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                       ON CONFLICT(doc_hash, block_id) DO UPDATE SET
                         filepath=excluded.filepath,
                         page_num=excluded.page_num,
                         bbox_json=excluded.bbox_json,
                         priority=max(formula_index_jobs.priority, excluded.priority),
                         status=CASE
                           WHEN formula_index_jobs.status='done'
                            AND formula_index_jobs.content_hash=excluded.content_hash
                            AND formula_index_jobs.scan_round=excluded.scan_round
                           THEN 'done'
                           ELSE 'queued'
                         END,
                         content_hash=excluded.content_hash,
                         scan_round=excluded.scan_round,
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
                        round_name,
                        now,
                        now,
                    ),
                )
                self._enqueue_round_job_locked(
                    doc_hash=doc_hash,
                    filepath=filepath,
                    scan_round=round_name,
                    target_type="block",
                    target_id=block.id,
                    page_num=block.page_num,
                    priority=priority,
                    status="queued",
                    now=now,
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
        scan_round: str | FormulaScanRound = FormulaScanRound.PDF_STRUCTURE,
    ) -> int:
        """Insert or refresh page-level MFD jobs for a document."""
        if not doc_hash or not filepath:
            return 0
        round_name = _round_value(scan_round)
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
                       (doc_hash, filepath, page_num, priority, status, scan_round, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
                       ON CONFLICT(doc_hash, page_num) DO UPDATE SET
                         filepath=excluded.filepath,
                         priority=max(formula_page_scan_jobs.priority, excluded.priority),
                         status=CASE
                           WHEN formula_page_scan_jobs.status='done'
                           THEN 'done'
                           ELSE 'queued'
                         END,
                         scan_round=excluded.scan_round,
                         error='',
                         updated_at=excluded.updated_at""",
                    (doc_hash, filepath, page_num, priority, round_name, now, now),
                )
                status = "done" if row and str(row[0]) == "done" else "queued"
                self._enqueue_round_job_locked(
                    doc_hash=doc_hash,
                    filepath=filepath,
                    scan_round=round_name,
                    target_type="page",
                    target_id=str(page_num),
                    page_num=page_num,
                    priority=priority,
                    status=status,
                    now=now,
                )
                if not row or str(row[0]) != "done":
                    inserted += 1
            self._conn.commit()
        return inserted

    def enqueue_round_records(
        self,
        doc_hash: str,
        filepath: str,
        scan_round: str | FormulaScanRound,
        target_type: FormulaRoundTarget,
        targets: list[DocumentBlock] | list[int],
        priority_pages: set[int] | None = None,
        status: FormulaTaskStatus = "queued",
    ) -> int:
        """Persist non-OCR round work such as semantic review or graph updates."""
        if not doc_hash or not filepath or not targets:
            return 0
        priority_pages = priority_pages or set()
        round_name = _round_value(scan_round)
        now = _now()
        inserted = 0
        with self._lock:
            for target in targets:
                if target_type == "block":
                    if not isinstance(target, DocumentBlock):
                        continue
                    target_id = target.id
                    page_num = target.page_num
                    priority = self.priority_for_block(target, priority_pages)
                else:
                    try:
                        page_num = int(target)
                    except (TypeError, ValueError):
                        continue
                    if page_num < 0:
                        continue
                    target_id = str(page_num)
                    priority = self.priority_for_page(page_num, priority_pages)
                row = self._conn.execute(
                    """SELECT status FROM formula_round_jobs
                       WHERE doc_hash=? AND scan_round=? AND target_type=? AND target_id=?""",
                    (doc_hash, round_name, target_type, target_id),
                ).fetchone()
                if row and str(row[0]) == "done":
                    continue
                self._enqueue_round_job_locked(
                    doc_hash=doc_hash,
                    filepath=filepath,
                    scan_round=round_name,
                    target_type=target_type,
                    target_id=target_id,
                    page_num=page_num,
                    priority=priority,
                    status=status,
                    now=now,
                )
                inserted += 1
            self._conn.commit()
        return inserted

    def mark_running(
        self,
        doc_hash: str,
        block_ids: list[str],
        scan_round: str | FormulaScanRound | None = None,
    ) -> None:
        self._update_status(
            doc_hash,
            block_ids,
            "running",
            increment_attempts=True,
            scan_round=scan_round,
        )

    def mark_done(
        self,
        doc_hash: str,
        block_id: str,
        latex: str,
        image_hash: str,
        model: str = "pix2text-mfr",
        scan_round: str | FormulaScanRound | None = None,
    ) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """UPDATE formula_index_jobs
                   SET status='done', latex=?, image_hash=?, model=?, error='', updated_at=?
                   WHERE doc_hash=? AND block_id=?""",
                (latex, image_hash, model, now, doc_hash, block_id),
            )
            self._update_round_job_for_block_locked(
                doc_hash,
                block_id,
                "done",
                scan_round=scan_round,
                now=now,
                result_json={
                    "latex": latex,
                    "image_hash": image_hash,
                    "model": model,
                },
            )
            self._conn.commit()

    def mark_failed(
        self,
        doc_hash: str,
        block_id: str,
        error: str,
        scan_round: str | FormulaScanRound | None = None,
    ) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """UPDATE formula_index_jobs
                   SET status='failed', error=?, updated_at=?
                   WHERE doc_hash=? AND block_id=?""",
                (error[:500], now, doc_hash, block_id),
            )
            self._update_round_job_for_block_locked(
                doc_hash,
                block_id,
                "failed",
                scan_round=scan_round,
                now=now,
                error=error,
            )
            self._conn.commit()

    def mark_skipped(
        self,
        doc_hash: str,
        block_id: str,
        reason: str,
        scan_round: str | FormulaScanRound | None = None,
    ) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """UPDATE formula_index_jobs
                   SET status='skipped', error=?, updated_at=?
                   WHERE doc_hash=? AND block_id=?""",
                (reason[:500], now, doc_hash, block_id),
            )
            self._update_round_job_for_block_locked(
                doc_hash,
                block_id,
                "skipped",
                scan_round=scan_round,
                now=now,
                error=reason,
            )
            self._conn.commit()

    def mark_pages_running(
        self,
        doc_hash: str,
        page_nums: list[int],
        scan_round: str | FormulaScanRound | None = None,
    ) -> None:
        self._update_page_status(
            doc_hash,
            page_nums,
            "running",
            increment_attempts=True,
            scan_round=scan_round,
        )

    def mark_pages_done(
        self,
        doc_hash: str,
        page_nums: list[int],
        scan_round: str | FormulaScanRound | None = None,
    ) -> None:
        self._update_page_status(doc_hash, page_nums, "done", scan_round=scan_round)

    def mark_page_failed(
        self,
        doc_hash: str,
        page_num: int,
        error: str,
        scan_round: str | FormulaScanRound | None = None,
    ) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """UPDATE formula_page_scan_jobs
                   SET status='failed', error=?, updated_at=?
                   WHERE doc_hash=? AND page_num=?""",
                (error[:500], now, doc_hash, page_num),
            )
            self._update_round_job_for_page_locked(
                doc_hash,
                int(page_num),
                "failed",
                scan_round=scan_round,
                now=now,
                error=error,
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
        scan_round: str | FormulaScanRound | None = None,
    ) -> list[FormulaIndexTask]:
        params: list[object] = [doc_hash]
        where = "doc_hash=?"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where += f" AND status IN ({placeholders})"
            params.extend(sorted(statuses))
        if scan_round is not None:
            where += " AND scan_round=?"
            params.append(_round_value(scan_round))
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT doc_hash, filepath, block_id, page_num, bbox_json,
                           priority, status, content_hash, image_hash, latex,
                           model, error, attempts, updated_at, scan_round
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
        scan_round: str | FormulaScanRound | None = None,
    ) -> list[FormulaPageScanTask]:
        params: list[object] = [doc_hash]
        where = "doc_hash=?"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where += f" AND status IN ({placeholders})"
            params.extend(sorted(statuses))
        if scan_round is not None:
            where += " AND scan_round=?"
            params.append(_round_value(scan_round))
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT doc_hash, filepath, page_num, priority, status,
                           error, attempts, updated_at, scan_round
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
                scan_round=str(row[8]),
            )
            for row in rows
        ]

    def round_counts(
        self,
        doc_hash: str,
        scan_round: str | FormulaScanRound | None = None,
    ) -> dict[str, int]:
        params: list[object] = [doc_hash]
        where = "doc_hash=?"
        if scan_round is not None:
            where += " AND scan_round=?"
            params.append(_round_value(scan_round))
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT scan_round || ':' || status, COUNT(*)
                    FROM formula_round_jobs
                    WHERE {where}
                    GROUP BY scan_round, status""",
                params,
            ).fetchall()
        return {str(name): int(count) for name, count in rows}

    def round_pending_count(
        self,
        doc_hash: str,
        scan_round: str | FormulaScanRound | None = None,
    ) -> int:
        """Return queued/running generic round records for one document."""
        counts = self.round_counts(doc_hash, scan_round=scan_round)
        return sum(
            count
            for name, count in counts.items()
            if name.endswith(":queued") or name.endswith(":running")
        )

    def list_round_records(
        self,
        doc_hash: str,
        statuses: set[str] | None = None,
        scan_round: str | FormulaScanRound | None = None,
        limit: int = 100,
    ) -> list[FormulaRoundRecord]:
        params: list[object] = [doc_hash]
        where = "doc_hash=?"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where += f" AND status IN ({placeholders})"
            params.extend(sorted(statuses))
        if scan_round is not None:
            where += " AND scan_round=?"
            params.append(_round_value(scan_round))
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT doc_hash, filepath, scan_round, target_type, target_id,
                           page_num, priority, status, result_json, elapsed_ms,
                           error, attempts, updated_at
                    FROM formula_round_jobs
                    WHERE {where}
                    ORDER BY scan_round ASC, priority DESC, page_num ASC, target_id ASC
                    LIMIT ?""",
                params,
            ).fetchall()
        return [self._row_to_round_record(row) for row in rows]

    def mark_round_running(
        self,
        doc_hash: str,
        scan_round: str | FormulaScanRound,
        target_type: FormulaRoundTarget,
        target_ids: list[str],
    ) -> None:
        """Mark generic round records as running and increment attempts."""
        if not doc_hash or not target_ids:
            return
        now = _now()
        round_name = _round_value(scan_round)
        with self._lock:
            self._conn.executemany(
                """UPDATE formula_round_jobs
                   SET status='running', attempts=attempts+1, error='', updated_at=?
                   WHERE doc_hash=? AND scan_round=? AND target_type=? AND target_id=?""",
                [
                    (now, doc_hash, round_name, target_type, str(target_id))
                    for target_id in target_ids
                ],
            )
            self._conn.commit()

    def mark_round_done(
        self,
        doc_hash: str,
        scan_round: str | FormulaScanRound,
        target_type: FormulaRoundTarget,
        target_id: str,
        result_json: dict[str, object],
        elapsed_ms: int = 0,
    ) -> None:
        """Persist a generic round result without touching DocumentBlock content."""
        now = _now()
        payload = json.dumps(result_json, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._conn.execute(
                """UPDATE formula_round_jobs
                   SET status='done', result_json=?, elapsed_ms=?, error='', updated_at=?
                   WHERE doc_hash=? AND scan_round=? AND target_type=? AND target_id=?""",
                (
                    payload,
                    max(0, int(elapsed_ms)),
                    now,
                    doc_hash,
                    _round_value(scan_round),
                    target_type,
                    str(target_id),
                ),
            )
            self._conn.commit()

    def mark_round_failed(
        self,
        doc_hash: str,
        scan_round: str | FormulaScanRound,
        target_type: FormulaRoundTarget,
        target_id: str,
        error: str,
        elapsed_ms: int = 0,
        status: FormulaTaskStatus = "failed",
    ) -> None:
        """Persist a generic round failure or skip reason."""
        now = _now()
        with self._lock:
            self._conn.execute(
                """UPDATE formula_round_jobs
                   SET status=?, elapsed_ms=?, error=?, updated_at=?
                   WHERE doc_hash=? AND scan_round=? AND target_type=? AND target_id=?""",
                (
                    status,
                    max(0, int(elapsed_ms)),
                    error[:500],
                    now,
                    doc_hash,
                    _round_value(scan_round),
                    target_type,
                    str(target_id),
                ),
            )
            self._conn.commit()

    def put_recognition_result(
        self,
        *,
        doc_hash: str,
        candidate_id: str,
        stage: str,
        model: str,
        input_hash: str,
        latex: str = "",
        normalized_latex: str = "",
        model_version: str = "",
        preprocess_version: str = "",
        score: float | None = None,
        duration_ms: int = 0,
        peak_memory_mb: float | None = None,
        warnings: list[str] | tuple[str, ...] | None = None,
        evidence: dict[str, object] | None = None,
        accepted: bool = False,
    ) -> str:
        """Persist one backend result and return its stable result id."""
        if not doc_hash or not candidate_id or not stage or not model or not input_hash:
            raise ValueError("doc_hash, candidate_id, stage, model, and input_hash are required")
        now = _now()
        result_id = self.recognition_result_id(
            doc_hash=doc_hash,
            candidate_id=candidate_id,
            stage=stage,
            model=model,
            model_version=model_version,
            preprocess_version=preprocess_version,
            input_hash=input_hash,
        )
        warning_values = [str(item) for item in (warnings or ()) if str(item)]
        payload_warnings = json.dumps(warning_values, ensure_ascii=False, separators=(",", ":"))
        payload_evidence = json.dumps(evidence or {}, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            if accepted:
                self._conn.execute(
                    """UPDATE formula_recognition_results
                       SET accepted=0
                       WHERE doc_hash=? AND candidate_id=?""",
                    (doc_hash, candidate_id),
                )
            self._conn.execute(
                """INSERT INTO formula_recognition_results
                   (result_id, candidate_id, doc_hash, stage, model, model_version,
                    preprocess_version, input_hash, latex, normalized_latex, score,
                    duration_ms, peak_memory_mb, warnings_json, evidence_json,
                    accepted, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(doc_hash, candidate_id, stage, model, model_version,
                               preprocess_version, input_hash) DO UPDATE SET
                     latex=excluded.latex,
                     normalized_latex=excluded.normalized_latex,
                     score=excluded.score,
                     duration_ms=excluded.duration_ms,
                     peak_memory_mb=excluded.peak_memory_mb,
                     warnings_json=excluded.warnings_json,
                     evidence_json=excluded.evidence_json,
                     accepted=excluded.accepted,
                     created_at=excluded.created_at""",
                (
                    result_id,
                    candidate_id,
                    doc_hash,
                    stage,
                    model,
                    model_version,
                    preprocess_version,
                    input_hash,
                    latex,
                    normalized_latex or latex,
                    score,
                    max(0, int(duration_ms)),
                    peak_memory_mb,
                    payload_warnings,
                    payload_evidence,
                    1 if accepted else 0,
                    now,
                ),
            )
            self._conn.commit()
        return result_id

    def get_recognition_result(
        self,
        *,
        doc_hash: str,
        candidate_id: str,
        stage: str,
        model: str,
        input_hash: str,
        model_version: str = "",
        preprocess_version: str = "",
    ) -> FormulaRecognitionRecord | None:
        """Return a cached backend result for the exact input/tool identity."""
        with self._lock:
            row = self._conn.execute(
                """SELECT result_id, candidate_id, doc_hash, stage, model,
                          model_version, preprocess_version, input_hash, latex,
                          normalized_latex, score, duration_ms, peak_memory_mb,
                          warnings_json, evidence_json, accepted, created_at
                   FROM formula_recognition_results
                   WHERE doc_hash=? AND candidate_id=? AND stage=? AND model=?
                     AND model_version=? AND preprocess_version=? AND input_hash=?""",
                (
                    doc_hash,
                    candidate_id,
                    stage,
                    model,
                    model_version,
                    preprocess_version,
                    input_hash,
                ),
            ).fetchone()
        return self._row_to_recognition_record(row) if row else None

    def list_recognition_results(
        self,
        doc_hash: str,
        candidate_id: str | None = None,
        stage: str | None = None,
        accepted: bool | None = None,
        limit: int = 100,
    ) -> list[FormulaRecognitionRecord]:
        """List persisted recognition outputs for audit and backend comparison."""
        params: list[object] = [doc_hash]
        where = "doc_hash=?"
        if candidate_id is not None:
            where += " AND candidate_id=?"
            params.append(candidate_id)
        if stage is not None:
            where += " AND stage=?"
            params.append(stage)
        if accepted is not None:
            where += " AND accepted=?"
            params.append(1 if accepted else 0)
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT result_id, candidate_id, doc_hash, stage, model,
                           model_version, preprocess_version, input_hash, latex,
                           normalized_latex, score, duration_ms, peak_memory_mb,
                           warnings_json, evidence_json, accepted, created_at
                    FROM formula_recognition_results
                    WHERE {where}
                    ORDER BY created_at DESC
                    LIMIT ?""",
                params,
            ).fetchall()
        return [self._row_to_recognition_record(row) for row in rows]

    def _update_status(
        self,
        doc_hash: str,
        block_ids: list[str],
        status: FormulaTaskStatus,
        increment_attempts: bool = False,
        scan_round: str | FormulaScanRound | None = None,
    ) -> None:
        if not doc_hash or not block_ids:
            return
        now = _now()
        attempts_expr = "attempts + 1" if increment_attempts else "attempts"
        round_name = _round_value(scan_round) if scan_round is not None else None
        with self._lock:
            self._conn.executemany(
                f"""UPDATE formula_index_jobs
                    SET status=?, attempts={attempts_expr}, updated_at=?
                    WHERE doc_hash=? AND block_id=?""",
                [(status, now, doc_hash, block_id) for block_id in block_ids],
            )
            for block_id in block_ids:
                self._update_round_job_for_block_locked(
                    doc_hash,
                    block_id,
                    status,
                    scan_round=round_name,
                    now=now,
                    increment_attempts=increment_attempts,
                )
            self._conn.commit()

    def _update_page_status(
        self,
        doc_hash: str,
        page_nums: list[int],
        status: FormulaPageScanStatus,
        increment_attempts: bool = False,
        scan_round: str | FormulaScanRound | None = None,
    ) -> None:
        if not doc_hash or not page_nums:
            return
        now = _now()
        attempts_expr = "attempts + 1" if increment_attempts else "attempts"
        round_name = _round_value(scan_round) if scan_round is not None else None
        with self._lock:
            self._conn.executemany(
                f"""UPDATE formula_page_scan_jobs
                    SET status=?, attempts={attempts_expr}, updated_at=?
                    WHERE doc_hash=? AND page_num=?""",
                [(status, now, doc_hash, int(page_num)) for page_num in page_nums],
            )
            for page_num in page_nums:
                self._update_round_job_for_page_locked(
                    doc_hash,
                    int(page_num),
                    status,
                    scan_round=round_name,
                    now=now,
                    increment_attempts=increment_attempts,
                )
            self._conn.commit()

    def _migrate_schema(self) -> None:
        """Add multi-round columns when opening an older task database."""
        self._ensure_column(
            "formula_index_jobs",
            "scan_round",
            "TEXT NOT NULL DEFAULT 'r1_cached_recognition'",
        )
        self._ensure_column(
            "formula_page_scan_jobs",
            "scan_round",
            "TEXT NOT NULL DEFAULT 'r0_pdf_structure'",
        )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            str(row[1])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _enqueue_round_job_locked(
        self,
        doc_hash: str,
        filepath: str,
        scan_round: str,
        target_type: FormulaRoundTarget,
        target_id: str,
        page_num: int,
        priority: float,
        status: FormulaTaskStatus,
        now: str,
        result_json: dict[str, object] | None = None,
        error: str = "",
    ) -> None:
        payload = json.dumps(result_json or {}, ensure_ascii=False, separators=(",", ":"))
        self._conn.execute(
            """INSERT INTO formula_round_jobs
               (doc_hash, filepath, scan_round, target_type, target_id, page_num,
                priority, status, result_json, error, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(doc_hash, scan_round, target_type, target_id) DO UPDATE SET
                 filepath=excluded.filepath,
                 page_num=excluded.page_num,
                 priority=max(formula_round_jobs.priority, excluded.priority),
                 status=excluded.status,
                 result_json=CASE
                   WHEN excluded.result_json!='{}' THEN excluded.result_json
                   ELSE formula_round_jobs.result_json
                 END,
                 error=excluded.error,
                 updated_at=excluded.updated_at""",
            (
                doc_hash,
                filepath,
                scan_round,
                target_type,
                target_id,
                int(page_num),
                float(priority),
                status,
                payload,
                error[:500],
                now,
                now,
            ),
        )

    def _update_round_job_for_block_locked(
        self,
        doc_hash: str,
        block_id: str,
        status: FormulaTaskStatus,
        scan_round: str | FormulaScanRound | None,
        now: str,
        result_json: dict[str, object] | None = None,
        error: str = "",
        increment_attempts: bool = False,
    ) -> None:
        row = self._conn.execute(
            """SELECT filepath, page_num, priority, scan_round, content_hash
               FROM formula_index_jobs
               WHERE doc_hash=? AND block_id=?""",
            (doc_hash, block_id),
        ).fetchone()
        if not row:
            return
        round_name = _round_value(scan_round) if scan_round is not None else str(row[3])
        attempts_expr = "attempts + 1" if increment_attempts else "attempts"
        payload_dict = dict(result_json or {})
        payload_dict.setdefault("content_hash", str(row[4]))
        payload = json.dumps(payload_dict, ensure_ascii=False, separators=(",", ":"))
        self._conn.execute(
            f"""INSERT INTO formula_round_jobs
               (doc_hash, filepath, scan_round, target_type, target_id, page_num,
                priority, status, result_json, error, created_at, updated_at)
               VALUES (?, ?, ?, 'block', ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(doc_hash, scan_round, target_type, target_id) DO UPDATE SET
                 filepath=excluded.filepath,
                 page_num=excluded.page_num,
                 priority=max(formula_round_jobs.priority, excluded.priority),
                 status=excluded.status,
                 result_json=CASE
                   WHEN excluded.result_json!='{{}}' THEN excluded.result_json
                   ELSE formula_round_jobs.result_json
                 END,
                 error=excluded.error,
                 attempts={attempts_expr},
                 updated_at=excluded.updated_at""",
            (
                doc_hash,
                str(row[0]),
                round_name,
                block_id,
                int(row[1]),
                float(row[2]),
                status,
                payload,
                error[:500],
                now,
                now,
            ),
        )

    def _update_round_job_for_page_locked(
        self,
        doc_hash: str,
        page_num: int,
        status: FormulaPageScanStatus,
        scan_round: str | FormulaScanRound | None,
        now: str,
        result_json: dict[str, object] | None = None,
        error: str = "",
        increment_attempts: bool = False,
    ) -> None:
        row = self._conn.execute(
            """SELECT filepath, priority, scan_round
               FROM formula_page_scan_jobs
               WHERE doc_hash=? AND page_num=?""",
            (doc_hash, page_num),
        ).fetchone()
        if not row:
            return
        round_name = _round_value(scan_round) if scan_round is not None else str(row[2])
        attempts_expr = "attempts + 1" if increment_attempts else "attempts"
        payload = json.dumps(result_json or {}, ensure_ascii=False, separators=(",", ":"))
        self._conn.execute(
            f"""INSERT INTO formula_round_jobs
               (doc_hash, filepath, scan_round, target_type, target_id, page_num,
                priority, status, result_json, error, created_at, updated_at)
               VALUES (?, ?, ?, 'page', ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(doc_hash, scan_round, target_type, target_id) DO UPDATE SET
                 filepath=excluded.filepath,
                 page_num=excluded.page_num,
                 priority=max(formula_round_jobs.priority, excluded.priority),
                 status=excluded.status,
                 result_json=CASE
                   WHEN excluded.result_json!='{{}}' THEN excluded.result_json
                   ELSE formula_round_jobs.result_json
                 END,
                 error=excluded.error,
                 attempts={attempts_expr},
                 updated_at=excluded.updated_at""",
            (
                doc_hash,
                str(row[0]),
                round_name,
                str(page_num),
                int(page_num),
                float(row[1]),
                status,
                payload,
                error[:500],
                now,
                now,
            ),
        )

    @staticmethod
    def content_hash(block: DocumentBlock) -> str:
        digest = hashlib.sha256()
        digest.update(block.id.encode("utf-8", errors="ignore"))
        digest.update(str(block.page_num).encode("ascii"))
        digest.update("|".join(f"{value:.3f}" for value in block.bbox).encode("ascii"))
        digest.update((block.content or "").encode("utf-8", errors="ignore"))
        return digest.hexdigest()

    @staticmethod
    def recognition_result_id(
        *,
        doc_hash: str,
        candidate_id: str,
        stage: str,
        model: str,
        model_version: str,
        preprocess_version: str,
        input_hash: str,
    ) -> str:
        digest = hashlib.sha256()
        for value in (
            doc_hash,
            candidate_id,
            stage,
            model,
            model_version,
            preprocess_version,
            input_hash,
        ):
            digest.update(str(value).encode("utf-8", errors="ignore"))
            digest.update(b"\0")
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
            scan_round=str(row[14]),
        )

    @staticmethod
    def _row_to_round_record(row: tuple[object, ...]) -> FormulaRoundRecord:
        try:
            result_json = json.loads(str(row[8] or "{}"))
        except json.JSONDecodeError:
            result_json = {}
        if not isinstance(result_json, dict):
            result_json = {}
        return FormulaRoundRecord(
            doc_hash=str(row[0]),
            filepath=str(row[1]),
            scan_round=str(row[2]),
            target_type=str(row[3]),  # type: ignore[arg-type]
            target_id=str(row[4]),
            page_num=int(row[5]),
            priority=float(row[6]),
            status=str(row[7]),  # type: ignore[arg-type]
            result_json=result_json,
            elapsed_ms=int(row[9]),
            error=str(row[10]),
            attempts=int(row[11]),
            updated_at=str(row[12]),
        )

    @staticmethod
    def _row_to_recognition_record(row: tuple[object, ...]) -> FormulaRecognitionRecord:
        try:
            warnings_json = json.loads(str(row[13] or "[]"))
        except json.JSONDecodeError:
            warnings_json = []
        if not isinstance(warnings_json, list):
            warnings_json = []
        try:
            evidence_json = json.loads(str(row[14] or "{}"))
        except json.JSONDecodeError:
            evidence_json = {}
        if not isinstance(evidence_json, dict):
            evidence_json = {}
        score = row[10]
        peak_memory_mb = row[12]
        return FormulaRecognitionRecord(
            result_id=str(row[0]),
            candidate_id=str(row[1]),
            doc_hash=str(row[2]),
            stage=str(row[3]),
            model=str(row[4]),
            model_version=str(row[5]),
            preprocess_version=str(row[6]),
            input_hash=str(row[7]),
            latex=str(row[8]),
            normalized_latex=str(row[9]),
            score=float(score) if score is not None else None,
            duration_ms=int(row[11]),
            peak_memory_mb=float(peak_memory_mb) if peak_memory_mb is not None else None,
            warnings=tuple(str(item) for item in warnings_json),
            evidence=evidence_json,
            accepted=bool(row[15]),
            created_at=str(row[16]),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round_value(scan_round: str | FormulaScanRound) -> str:
    return scan_round.value if isinstance(scan_round, FormulaScanRound) else str(scan_round)

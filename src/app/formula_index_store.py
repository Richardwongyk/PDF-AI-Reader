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

from src.core.models import DocumentBlock, wrap_math_text

FormulaTaskStatus = Literal["queued", "running", "done", "failed", "skipped"]
FormulaPageScanStatus = Literal["queued", "running", "done", "failed", "skipped"]
FormulaRoundTarget = Literal["block", "page"]
FormulaAcceptanceAction = Literal["accept", "reject"]


class FormulaScanRound(StrEnum):
    """Persisted stages for multi-pass formula parsing."""

    PDF_STRUCTURE = "r0_pdf_structure"
    SYMBOL_IDENTITY_REPAIR = "r0_5_symbol_identity_repair"
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


@dataclass(frozen=True)
class FormulaAcceptanceDecision:
    """Auditable accept/reject event for one persisted formula candidate."""

    decision_id: str
    doc_hash: str
    candidate_id: str
    result_id: str
    action: FormulaAcceptanceAction
    decision_source: str
    decider: str
    reason: str
    accepted_latex: str
    previous_result_id: str
    input_hash: str
    payload: dict[str, object]
    created_at: str = ""


@dataclass(frozen=True)
class FormulaFusionRecord:
    """Persisted candidate-fusion gate for one formula region."""

    fusion_id: str
    doc_hash: str
    candidate_id: str
    fusion_version: str
    input_hash: str
    best_result_id: str
    ranked_result_ids: tuple[str, ...]
    coverage: float
    agreement_score: float
    source_similarity: float
    syntax_valid: bool
    risk_flags: tuple[str, ...]
    accepted_gate: dict[str, object]
    decision: str
    result_json: dict[str, object]
    created_at: str = ""
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
                scan_round   TEXT NOT NULL DEFAULT 'r1_cached_recognition',
                image_hash   TEXT NOT NULL DEFAULT '',
                latex        TEXT NOT NULL DEFAULT '',
                model        TEXT NOT NULL DEFAULT '',
                error        TEXT NOT NULL DEFAULT '',
                attempts     INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                PRIMARY KEY (doc_hash, scan_round, block_id)
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
                PRIMARY KEY (doc_hash, scan_round, page_num)
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
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS formula_acceptance_decisions (
                decision_id        TEXT PRIMARY KEY,
                doc_hash           TEXT NOT NULL,
                candidate_id       TEXT NOT NULL,
                result_id          TEXT NOT NULL,
                action             TEXT NOT NULL,
                decision_source    TEXT NOT NULL DEFAULT '',
                decider            TEXT NOT NULL DEFAULT '',
                reason             TEXT NOT NULL DEFAULT '',
                accepted_latex     TEXT NOT NULL DEFAULT '',
                previous_result_id TEXT NOT NULL DEFAULT '',
                input_hash         TEXT NOT NULL DEFAULT '',
                payload_json       TEXT NOT NULL DEFAULT '{}',
                created_at         TEXT NOT NULL
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_formula_acceptance_lookup "
            "ON formula_acceptance_decisions(doc_hash, candidate_id, created_at DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_formula_acceptance_result "
            "ON formula_acceptance_decisions(result_id, created_at DESC)"
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS formula_fusion_records (
                fusion_id           TEXT PRIMARY KEY,
                doc_hash            TEXT NOT NULL,
                candidate_id        TEXT NOT NULL,
                fusion_version      TEXT NOT NULL,
                input_hash          TEXT NOT NULL,
                best_result_id      TEXT NOT NULL DEFAULT '',
                ranked_result_ids_json TEXT NOT NULL DEFAULT '[]',
                coverage            REAL NOT NULL DEFAULT 0,
                agreement_score     REAL NOT NULL DEFAULT 0,
                source_similarity   REAL NOT NULL DEFAULT 0,
                syntax_valid        INTEGER NOT NULL DEFAULT 0,
                risk_flags_json     TEXT NOT NULL DEFAULT '[]',
                accepted_gate_json  TEXT NOT NULL DEFAULT '{}',
                decision            TEXT NOT NULL DEFAULT 'candidate_only',
                result_json         TEXT NOT NULL DEFAULT '{}',
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL,
                UNIQUE(doc_hash, candidate_id, fusion_version, input_hash)
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_formula_fusion_lookup "
            "ON formula_fusion_records(doc_hash, decision, updated_at DESC)"
        )
        self._migrate_schema()
        self._ensure_indexes()
        self._conn.commit()
        self._lock = threading.Lock()

    def close(self) -> None:
        """Close the SQLite connection."""
        with self._lock:
            self._conn.close()

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
                       WHERE doc_hash=? AND scan_round=? AND block_id=?""",
                    (doc_hash, round_name, block.id),
                ).fetchone()
                if (
                    row
                    and row[1] == content_hash
                    and row[2] == round_name
                    and row[0] in {"queued", "running", "done"}
                ):
                    if str(row[0]) == "done":
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
                       ON CONFLICT(doc_hash, scan_round, block_id) DO UPDATE SET
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
                       WHERE doc_hash=? AND scan_round=? AND page_num=?""",
                    (doc_hash, round_name, page_num),
                ).fetchone()
                self._conn.execute(
                    """INSERT INTO formula_page_scan_jobs
                       (doc_hash, filepath, page_num, priority, status, scan_round, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)
                       ON CONFLICT(doc_hash, scan_round, page_num) DO UPDATE SET
                         filepath=excluded.filepath,
                         priority=max(formula_page_scan_jobs.priority, excluded.priority),
                         status=CASE
                           WHEN formula_page_scan_jobs.status='done'
                           THEN 'done'
                           ELSE 'queued'
                         END,
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
        result_json_by_target: dict[str, dict[str, object]] | None = None,
    ) -> int:
        """Persist non-OCR round work such as semantic review or graph updates."""
        if not doc_hash or not filepath or not targets:
            return 0
        priority_pages = priority_pages or set()
        result_json_by_target = result_json_by_target or {}
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
                result_payload = result_json_by_target.get(target_id)
                row = self._conn.execute(
                    """SELECT status, result_json FROM formula_round_jobs
                       WHERE doc_hash=? AND scan_round=? AND target_type=? AND target_id=?""",
                    (doc_hash, round_name, target_type, target_id),
                ).fetchone()
                if row and str(row[0]) == "done":
                    if not result_payload:
                        continue
                    try:
                        existing_payload = json.loads(str(row[1] or "{}"))
                    except json.JSONDecodeError:
                        existing_payload = {}
                    if not isinstance(existing_payload, dict):
                        existing_payload = {}
                    if existing_payload.get("input_hash") == result_payload.get("input_hash"):
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
                    result_json=result_payload,
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
        model_version: str = "",
        preprocess_version: str = "",
        score: float | None = None,
        warnings: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        now = _now()
        round_name = _round_value(scan_round) if scan_round is not None else None
        where = "doc_hash=? AND block_id=?"
        params: tuple[object, ...] = (doc_hash, block_id)
        if round_name is not None:
            where += " AND scan_round=?"
            params = (doc_hash, block_id, round_name)
        with self._lock:
            self._conn.execute(
                f"""UPDATE formula_index_jobs
                   SET status='done', latex=?, image_hash=?, model=?, error='', updated_at=?
                   WHERE {where}""",
                (latex, image_hash, model, now, *params),
            )
            self._update_round_job_for_block_locked(
                doc_hash,
                block_id,
                "done",
                scan_round=scan_round,
                now=now,
                result_json={
                    "stage": _round_value(scan_round) if scan_round is not None else "",
                    "latex": latex,
                    "input_hash": image_hash,
                    "image_hash": image_hash,
                    "model": model,
                    "model_version": model_version,
                    "preprocess_version": preprocess_version,
                    "score": score,
                    "warnings": [str(item) for item in (warnings or []) if str(item)],
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
        round_name = _round_value(scan_round) if scan_round is not None else None
        where = "doc_hash=? AND block_id=?"
        params: tuple[object, ...] = (doc_hash, block_id)
        if round_name is not None:
            where += " AND scan_round=?"
            params = (doc_hash, block_id, round_name)
        with self._lock:
            self._conn.execute(
                f"""UPDATE formula_index_jobs
                   SET status='failed', error=?, updated_at=?
                   WHERE {where}""",
                (error[:500], now, *params),
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
        round_name = _round_value(scan_round) if scan_round is not None else None
        where = "doc_hash=? AND block_id=?"
        params: tuple[object, ...] = (doc_hash, block_id)
        if round_name is not None:
            where += " AND scan_round=?"
            params = (doc_hash, block_id, round_name)
        with self._lock:
            self._conn.execute(
                f"""UPDATE formula_index_jobs
                   SET status='skipped', error=?, updated_at=?
                   WHERE {where}""",
                (reason[:500], now, *params),
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
        round_name = _round_value(scan_round) if scan_round is not None else None
        where = "doc_hash=? AND page_num=?"
        params: tuple[object, ...] = (doc_hash, int(page_num))
        if round_name is not None:
            where += " AND scan_round=?"
            params = (doc_hash, int(page_num), round_name)
        with self._lock:
            self._conn.execute(
                f"""UPDATE formula_page_scan_jobs
                   SET status='failed', error=?, updated_at=?
                   WHERE {where}""",
                (error[:500], now, *params),
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

    def get_recognition_result_by_id(self, result_id: str) -> FormulaRecognitionRecord | None:
        """Return a recognition result by its stable id."""
        if not result_id:
            return None
        with self._lock:
            row = self._conn.execute(
                """SELECT result_id, candidate_id, doc_hash, stage, model,
                          model_version, preprocess_version, input_hash, latex,
                          normalized_latex, score, duration_ms, peak_memory_mb,
                          warnings_json, evidence_json, accepted, created_at
                   FROM formula_recognition_results
                   WHERE result_id=?""",
                (str(result_id),),
            ).fetchone()
        return self._row_to_recognition_record(row) if row else None

    def set_recognition_acceptance(
        self,
        *,
        doc_hash: str,
        result_id: str,
        accepted: bool,
        filepath: str = "",
        decision_source: str = "manual",
        decider: str = "",
        reason: str = "",
        payload: dict[str, object] | None = None,
    ) -> FormulaAcceptanceDecision:
        """Accept or reject one persisted recognition result with audit history.

        Accepting a result clears other accepted results for the same candidate
        and, when a filepath is provided, queues r5 incremental knowledge update
        work for the accepted revision. Rejecting only clears this result.
        """
        if not doc_hash or not result_id:
            raise ValueError("doc_hash and result_id are required")
        now = _now()
        with self._lock:
            row = self._conn.execute(
                """SELECT result_id, candidate_id, doc_hash, stage, model,
                          model_version, preprocess_version, input_hash, latex,
                          normalized_latex, score, duration_ms, peak_memory_mb,
                          warnings_json, evidence_json, accepted, created_at
                   FROM formula_recognition_results
                   WHERE doc_hash=? AND result_id=?""",
                (doc_hash, result_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"formula recognition result not found: {result_id}")
            record = self._row_to_recognition_record(row)
            previous_row = self._conn.execute(
                """SELECT result_id FROM formula_recognition_results
                   WHERE doc_hash=? AND candidate_id=? AND accepted=1
                   ORDER BY created_at DESC LIMIT 1""",
                (doc_hash, record.candidate_id),
            ).fetchone()
            previous_result_id = str(previous_row[0]) if previous_row else ""
            action: FormulaAcceptanceAction = "accept" if accepted else "reject"
            display = not _evidence_is_inline(record.evidence)
            accepted_latex = (
                wrap_math_text(record.latex or record.normalized_latex, display=display)
                if accepted
                else ""
            )
            if accepted and not accepted_latex:
                raise ValueError(f"accepted formula result has empty latex: {result_id}")
            decision_payload: dict[str, object] = {
                **(payload or {}),
                "result": {
                    "stage": record.stage,
                    "model": record.model,
                    "model_version": record.model_version,
                    "preprocess_version": record.preprocess_version,
                    "input_hash": record.input_hash,
                    "score": record.score,
                    "warnings": list(record.warnings),
                },
            }
            r5_input_hash = (
                self.acceptance_input_hash(
                    doc_hash=record.doc_hash,
                    candidate_id=record.candidate_id,
                    result_id=record.result_id,
                    result_input_hash=record.input_hash,
                    accepted_latex=accepted_latex,
                )
                if accepted
                else ""
            )
            decision_id = self.acceptance_decision_id(
                doc_hash=record.doc_hash,
                candidate_id=record.candidate_id,
                result_id=record.result_id,
                action=action,
                decision_source=decision_source,
                previous_result_id=previous_result_id,
                input_hash=r5_input_hash or record.input_hash,
                created_at=now,
            )
            try:
                if accepted:
                    self._conn.execute(
                        """UPDATE formula_recognition_results
                           SET accepted=0
                           WHERE doc_hash=? AND candidate_id=?""",
                        (record.doc_hash, record.candidate_id),
                    )
                    self._conn.execute(
                        """UPDATE formula_recognition_results
                           SET accepted=1
                           WHERE doc_hash=? AND result_id=?""",
                        (record.doc_hash, record.result_id),
                    )
                else:
                    self._conn.execute(
                        """UPDATE formula_recognition_results
                           SET accepted=0
                           WHERE doc_hash=? AND result_id=?""",
                        (record.doc_hash, record.result_id),
                    )
                self._conn.execute(
                    """INSERT INTO formula_acceptance_decisions
                       (decision_id, doc_hash, candidate_id, result_id, action,
                        decision_source, decider, reason, accepted_latex,
                        previous_result_id, input_hash, payload_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        decision_id,
                        record.doc_hash,
                        record.candidate_id,
                        record.result_id,
                        action,
                        str(decision_source or ""),
                        str(decider or ""),
                        str(reason or ""),
                        accepted_latex,
                        previous_result_id,
                        r5_input_hash or record.input_hash,
                        json.dumps(decision_payload, ensure_ascii=False, separators=(",", ":")),
                        now,
                    ),
                )
                if accepted and filepath:
                    self._enqueue_acceptance_r5_locked(
                        filepath=filepath,
                        record=record,
                        accepted_latex=accepted_latex,
                        decision_id=decision_id,
                        decision_source=str(decision_source or ""),
                        reason=str(reason or ""),
                        r5_input_hash=r5_input_hash,
                        now=now,
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return FormulaAcceptanceDecision(
            decision_id=decision_id,
            doc_hash=record.doc_hash,
            candidate_id=record.candidate_id,
            result_id=record.result_id,
            action=action,
            decision_source=str(decision_source or ""),
            decider=str(decider or ""),
            reason=str(reason or ""),
            accepted_latex=accepted_latex,
            previous_result_id=previous_result_id,
            input_hash=r5_input_hash or record.input_hash,
            payload=decision_payload,
            created_at=now,
        )

    def accept_recognition_result(
        self,
        *,
        doc_hash: str,
        result_id: str,
        filepath: str = "",
        decision_source: str = "manual",
        decider: str = "",
        reason: str = "",
        payload: dict[str, object] | None = None,
    ) -> FormulaAcceptanceDecision:
        """Accept a recognition result and enqueue r5 when filepath is known."""
        return self.set_recognition_acceptance(
            doc_hash=doc_hash,
            result_id=result_id,
            accepted=True,
            filepath=filepath,
            decision_source=decision_source,
            decider=decider,
            reason=reason,
            payload=payload,
        )

    def reject_recognition_result(
        self,
        *,
        doc_hash: str,
        result_id: str,
        decision_source: str = "manual",
        decider: str = "",
        reason: str = "",
        payload: dict[str, object] | None = None,
    ) -> FormulaAcceptanceDecision:
        """Reject a recognition result without queuing knowledge writeback."""
        return self.set_recognition_acceptance(
            doc_hash=doc_hash,
            result_id=result_id,
            accepted=False,
            decision_source=decision_source,
            decider=decider,
            reason=reason,
            payload=payload,
        )

    def list_acceptance_decisions(
        self,
        doc_hash: str,
        candidate_id: str | None = None,
        result_id: str | None = None,
        limit: int = 100,
    ) -> list[FormulaAcceptanceDecision]:
        """List formula acceptance/rejection audit events."""
        params: list[object] = [doc_hash]
        where = "doc_hash=?"
        if candidate_id is not None:
            where += " AND candidate_id=?"
            params.append(candidate_id)
        if result_id is not None:
            where += " AND result_id=?"
            params.append(result_id)
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT decision_id, doc_hash, candidate_id, result_id,
                           action, decision_source, decider, reason,
                           accepted_latex, previous_result_id, input_hash,
                           payload_json, created_at
                    FROM formula_acceptance_decisions
                    WHERE {where}
                    ORDER BY created_at DESC
                    LIMIT ?""",
                params,
            ).fetchall()
        return [self._row_to_acceptance_decision(row) for row in rows]

    def accept_fusion_record(
        self,
        *,
        doc_hash: str,
        fusion_id: str,
        filepath: str = "",
        decision_source: str = "manual_fusion_review",
        decider: str = "",
        reason: str = "",
        allow_not_ready: bool = False,
    ) -> FormulaAcceptanceDecision:
        """Accept the best candidate from a persisted fusion record.

        If the fusion best result is already a recognition result, this method
        accepts that result directly. Otherwise it persists a synthetic reviewed
        result from the fusion payload and accepts that revision.
        """
        fusion = self.get_fusion_record_by_id(fusion_id)
        if fusion is None or fusion.doc_hash != doc_hash:
            raise ValueError(f"formula fusion record not found: {fusion_id}")
        if (
            not allow_not_ready
            and fusion.decision not in {"ready_for_manual_accept", "auto_accept_allowed"}
        ):
            raise ValueError(
                f"fusion record is not ready for acceptance: {fusion.decision}"
            )
        existing = self.get_recognition_result_by_id(fusion.best_result_id)
        if existing is not None and existing.doc_hash == doc_hash:
            return self.accept_recognition_result(
                doc_hash=doc_hash,
                result_id=existing.result_id,
                filepath=filepath,
                decision_source=decision_source,
                decider=decider,
                reason=reason,
                payload={
                    "fusion_id": fusion.fusion_id,
                    "fusion_version": fusion.fusion_version,
                    "fusion_input_hash": fusion.input_hash,
                    "fusion_decision": fusion.decision,
                },
            )

        best_latex = _best_latex_from_fusion_payload(fusion.result_json)
        if not best_latex:
            raise ValueError(f"fusion record has no acceptable latex: {fusion_id}")
        ranked = fusion.result_json.get("ranked_candidates", [])
        best_candidate = ranked[0] if isinstance(ranked, list) and ranked else {}
        if not isinstance(best_candidate, dict):
            best_candidate = {}
        synthetic_input_hash = self.acceptance_input_hash(
            doc_hash=doc_hash,
            candidate_id=fusion.candidate_id,
            result_id=fusion.best_result_id or fusion.fusion_id,
            result_input_hash=fusion.input_hash,
            accepted_latex=best_latex,
        )
        synthetic_result_id = self.put_recognition_result(
            doc_hash=doc_hash,
            candidate_id=fusion.candidate_id,
            stage="manual_fusion_acceptance",
            model="formula_fusion_gate",
            model_version=fusion.fusion_version,
            preprocess_version="manual-review-v1",
            input_hash=synthetic_input_hash,
            latex=best_latex,
            normalized_latex=best_latex,
            score=fusion.source_similarity,
            warnings=list(fusion.risk_flags),
            evidence={
                "source": "formula_fusion_record",
                "source_stage": str(best_candidate.get("stage", "") or ""),
                "fusion_id": fusion.fusion_id,
                "fusion_version": fusion.fusion_version,
                "fusion_input_hash": fusion.input_hash,
                "fusion_best_result_id": fusion.best_result_id,
                "fusion_decision": fusion.decision,
                "accepted_gate": fusion.accepted_gate,
                "best_candidate": best_candidate,
            },
            accepted=False,
        )
        return self.accept_recognition_result(
            doc_hash=doc_hash,
            result_id=synthetic_result_id,
            filepath=filepath,
            decision_source=decision_source,
            decider=decider,
            reason=reason,
            payload={
                "fusion_id": fusion.fusion_id,
                "fusion_version": fusion.fusion_version,
                "fusion_input_hash": fusion.input_hash,
                "fusion_decision": fusion.decision,
                "synthetic_from_fusion": True,
            },
        )

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

    def put_fusion_record(
        self,
        *,
        doc_hash: str,
        candidate_id: str,
        fusion_version: str,
        input_hash: str,
        best_result_id: str = "",
        ranked_result_ids: list[str] | tuple[str, ...] | None = None,
        coverage: float = 0.0,
        agreement_score: float = 0.0,
        source_similarity: float = 0.0,
        syntax_valid: bool = False,
        risk_flags: list[str] | tuple[str, ...] | None = None,
        accepted_gate: dict[str, object] | None = None,
        decision: str = "candidate_only",
        result_json: dict[str, object] | None = None,
    ) -> str:
        """Persist one candidate-fusion gate and return its stable id."""
        if not doc_hash or not candidate_id or not fusion_version or not input_hash:
            raise ValueError("doc_hash, candidate_id, fusion_version, and input_hash are required")
        now = _now()
        fusion_id = self.fusion_record_id(
            doc_hash=doc_hash,
            candidate_id=candidate_id,
            fusion_version=fusion_version,
            input_hash=input_hash,
        )
        ranked_ids = [str(item) for item in (ranked_result_ids or ()) if str(item)]
        risks = [str(item) for item in (risk_flags or ()) if str(item)]
        accepted_payload = accepted_gate or {}
        result_payload = result_json or {}
        with self._lock:
            existing = self._conn.execute(
                """SELECT created_at FROM formula_fusion_records
                   WHERE doc_hash=? AND candidate_id=? AND fusion_version=? AND input_hash=?""",
                (doc_hash, candidate_id, fusion_version, input_hash),
            ).fetchone()
            created_at = str(existing[0]) if existing else now
            self._conn.execute(
                """INSERT INTO formula_fusion_records
                   (fusion_id, doc_hash, candidate_id, fusion_version, input_hash,
                    best_result_id, ranked_result_ids_json, coverage,
                    agreement_score, source_similarity, syntax_valid,
                    risk_flags_json, accepted_gate_json, decision, result_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(doc_hash, candidate_id, fusion_version, input_hash)
                   DO UPDATE SET
                     best_result_id=excluded.best_result_id,
                     ranked_result_ids_json=excluded.ranked_result_ids_json,
                     coverage=excluded.coverage,
                     agreement_score=excluded.agreement_score,
                     source_similarity=excluded.source_similarity,
                     syntax_valid=excluded.syntax_valid,
                     risk_flags_json=excluded.risk_flags_json,
                     accepted_gate_json=excluded.accepted_gate_json,
                     decision=excluded.decision,
                     result_json=excluded.result_json,
                     updated_at=excluded.updated_at""",
                (
                    fusion_id,
                    doc_hash,
                    candidate_id,
                    fusion_version,
                    input_hash,
                    best_result_id,
                    json.dumps(ranked_ids, ensure_ascii=False, separators=(",", ":")),
                    float(coverage),
                    float(agreement_score),
                    float(source_similarity),
                    1 if syntax_valid else 0,
                    json.dumps(risks, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(accepted_payload, ensure_ascii=False, separators=(",", ":")),
                    str(decision or "candidate_only"),
                    json.dumps(result_payload, ensure_ascii=False, separators=(",", ":")),
                    created_at,
                    now,
                ),
            )
            self._conn.commit()
        return fusion_id

    def get_fusion_record(
        self,
        *,
        doc_hash: str,
        candidate_id: str,
        fusion_version: str,
        input_hash: str,
    ) -> FormulaFusionRecord | None:
        """Return a cached fusion gate for the exact candidate input."""
        with self._lock:
            row = self._conn.execute(
                """SELECT fusion_id, doc_hash, candidate_id, fusion_version,
                          input_hash, best_result_id, ranked_result_ids_json,
                          coverage, agreement_score, source_similarity,
                          syntax_valid, risk_flags_json, accepted_gate_json,
                          decision, result_json, created_at, updated_at
                   FROM formula_fusion_records
                   WHERE doc_hash=? AND candidate_id=? AND fusion_version=? AND input_hash=?""",
                (doc_hash, candidate_id, fusion_version, input_hash),
            ).fetchone()
        return self._row_to_fusion_record(row) if row else None

    def get_fusion_record_by_id(self, fusion_id: str) -> FormulaFusionRecord | None:
        """Return a persisted fusion gate by its stable id."""
        if not fusion_id:
            return None
        with self._lock:
            row = self._conn.execute(
                """SELECT fusion_id, doc_hash, candidate_id, fusion_version,
                          input_hash, best_result_id, ranked_result_ids_json,
                          coverage, agreement_score, source_similarity,
                          syntax_valid, risk_flags_json, accepted_gate_json,
                          decision, result_json, created_at, updated_at
                   FROM formula_fusion_records
                   WHERE fusion_id=?""",
                (str(fusion_id),),
            ).fetchone()
        return self._row_to_fusion_record(row) if row else None

    def list_fusion_records(
        self,
        doc_hash: str,
        candidate_id: str | None = None,
        decision: str | None = None,
        limit: int = 100,
    ) -> list[FormulaFusionRecord]:
        """List persisted formula fusion gates for audit and skip checks."""
        params: list[object] = [doc_hash]
        where = "doc_hash=?"
        if candidate_id is not None:
            where += " AND candidate_id=?"
            params.append(candidate_id)
        if decision is not None:
            where += " AND decision=?"
            params.append(decision)
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT fusion_id, doc_hash, candidate_id, fusion_version,
                          input_hash, best_result_id, ranked_result_ids_json,
                          coverage, agreement_score, source_similarity,
                          syntax_valid, risk_flags_json, accepted_gate_json,
                          decision, result_json, created_at, updated_at
                   FROM formula_fusion_records
                   WHERE {where}
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                params,
            ).fetchall()
        return [self._row_to_fusion_record(row) for row in rows]

    def fusion_counts(self, doc_hash: str) -> dict[str, int]:
        """Return decision counts for persisted fusion gates."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT decision, COUNT(*) FROM formula_fusion_records
                   WHERE doc_hash=? GROUP BY decision""",
                (doc_hash,),
            ).fetchall()
        return {str(decision): int(count) for decision, count in rows}

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
        where = "doc_hash=? AND block_id=?"
        if round_name is not None:
            where += " AND scan_round=?"
        with self._lock:
            self._conn.executemany(
                f"""UPDATE formula_index_jobs
                    SET status=?, attempts={attempts_expr}, updated_at=?
                    WHERE {where}""",
                [
                    (status, now, doc_hash, block_id, round_name)
                    if round_name is not None
                    else (status, now, doc_hash, block_id)
                    for block_id in block_ids
                ],
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
        where = "doc_hash=? AND page_num=?"
        if round_name is not None:
            where += " AND scan_round=?"
        with self._lock:
            self._conn.executemany(
                f"""UPDATE formula_page_scan_jobs
                    SET status=?, attempts={attempts_expr}, updated_at=?
                    WHERE {where}""",
                [
                    (status, now, doc_hash, int(page_num), round_name)
                    if round_name is not None
                    else (status, now, doc_hash, int(page_num))
                    for page_num in page_nums
                ],
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

    def _enqueue_acceptance_r5_locked(
        self,
        *,
        filepath: str,
        record: FormulaRecognitionRecord,
        accepted_latex: str,
        decision_id: str,
        decision_source: str,
        reason: str,
        r5_input_hash: str,
        now: str,
    ) -> None:
        job_row = self._conn.execute(
            """SELECT page_num, bbox_json, priority FROM formula_index_jobs
               WHERE doc_hash=? AND block_id=?""",
            (record.doc_hash, record.candidate_id),
        ).fetchone()
        page_num = _record_page_num(record)
        bbox = _record_bbox(record)
        priority = 0.0
        if job_row is not None:
            try:
                page_num = int(job_row[0])
            except (TypeError, ValueError):
                pass
            try:
                bbox_values = json.loads(str(job_row[1] or "[]"))
                if isinstance(bbox_values, list) and len(bbox_values) == 4:
                    bbox = tuple(float(value) for value in bbox_values)  # type: ignore[assignment]
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            try:
                priority = float(job_row[2])
            except (TypeError, ValueError):
                priority = 0.0
        if page_num < 0:
            page_num = 0
        payload: dict[str, object] = {
            "stage": FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE.value,
            "input_hash": r5_input_hash,
            "fusion_version": "manual_acceptance_v1",
            "best_result_id": record.result_id,
            "accepted_latex": accepted_latex,
            "candidate_id": record.candidate_id,
            "page_num": page_num,
            "bbox": list(bbox),
            "acceptance_decision_id": decision_id,
            "acceptance_source": decision_source,
            "acceptance_reason": reason,
            "accepted_result": {
                "stage": record.stage,
                "model": record.model,
                "model_version": record.model_version,
                "preprocess_version": record.preprocess_version,
                "input_hash": record.input_hash,
                "score": record.score,
            },
        }
        row = self._conn.execute(
            """SELECT status, result_json FROM formula_round_jobs
               WHERE doc_hash=? AND scan_round=? AND target_type='block' AND target_id=?""",
            (
                record.doc_hash,
                FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE.value,
                record.candidate_id,
            ),
        ).fetchone()
        if row and str(row[0]) == "done":
            try:
                existing_payload = json.loads(str(row[1] or "{}"))
            except json.JSONDecodeError:
                existing_payload = {}
            if isinstance(existing_payload, dict) and existing_payload.get("input_hash") == r5_input_hash:
                return
        self._enqueue_round_job_locked(
            doc_hash=record.doc_hash,
            filepath=filepath,
            scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE.value,
            target_type="block",
            target_id=record.candidate_id,
            page_num=page_num,
            priority=priority,
            status="queued",
            now=now,
            result_json=payload,
        )

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
        self._ensure_primary_key(
            "formula_index_jobs",
            ("doc_hash", "scan_round", "block_id"),
        )
        self._ensure_primary_key(
            "formula_page_scan_jobs",
            ("doc_hash", "scan_round", "page_num"),
        )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            str(row[1])
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _ensure_primary_key(self, table: str, columns: tuple[str, ...]) -> None:
        info = self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        pk_columns = tuple(
            str(row[1])
            for row in sorted((row for row in info if int(row[5] or 0) > 0), key=lambda row: int(row[5]))
        )
        if pk_columns == columns:
            return
        temp_table = f"{table}_old_pk"
        self._conn.execute(f"ALTER TABLE {table} RENAME TO {temp_table}")
        if table == "formula_index_jobs":
            self._create_formula_index_jobs_table(table)
        elif table == "formula_page_scan_jobs":
            self._create_formula_page_scan_jobs_table(table)
        else:
            raise ValueError(f"unsupported primary key migration table: {table}")
        column_names = [str(row[1]) for row in info]
        selected_columns = ", ".join(column_names)
        inserted_columns = ", ".join(column_names)
        self._conn.execute(
            f"""INSERT OR REPLACE INTO {table} ({inserted_columns})
                SELECT {selected_columns} FROM {temp_table}"""
        )
        self._conn.execute(f"DROP TABLE {temp_table}")

    def _ensure_indexes(self) -> None:
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_formula_jobs_status "
            "ON formula_index_jobs(doc_hash, status, priority DESC, page_num ASC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_formula_page_scan_status "
            "ON formula_page_scan_jobs(doc_hash, status, priority DESC, page_num ASC)"
        )

    def _create_formula_index_jobs_table(self, table: str = "formula_index_jobs") -> None:
        self._conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
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
                PRIMARY KEY (doc_hash, scan_round, block_id)
            )
        """)

    def _create_formula_page_scan_jobs_table(self, table: str = "formula_page_scan_jobs") -> None:
        self._conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {table} (
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
                PRIMARY KEY (doc_hash, scan_round, page_num)
            )
        """)

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
        round_name = _round_value(scan_round) if scan_round is not None else None
        where = "doc_hash=? AND block_id=?"
        params: tuple[object, ...] = (doc_hash, block_id)
        if round_name is not None:
            where += " AND scan_round=?"
            params = (doc_hash, block_id, round_name)
        row = self._conn.execute(
            """SELECT filepath, page_num, priority, scan_round, content_hash
               FROM formula_index_jobs
               WHERE {where}""".format(where=where),
            params,
        ).fetchone()
        if not row:
            return
        round_name = round_name or str(row[3])
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
        round_name = _round_value(scan_round) if scan_round is not None else None
        where = "doc_hash=? AND page_num=?"
        params: tuple[object, ...] = (doc_hash, page_num)
        if round_name is not None:
            where += " AND scan_round=?"
            params = (doc_hash, page_num, round_name)
        row = self._conn.execute(
            """SELECT filepath, priority, scan_round
               FROM formula_page_scan_jobs
               WHERE {where}""".format(where=where),
            params,
        ).fetchone()
        if not row:
            return
        round_name = round_name or str(row[2])
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
    def fusion_record_id(
        *,
        doc_hash: str,
        candidate_id: str,
        fusion_version: str,
        input_hash: str,
    ) -> str:
        digest = hashlib.sha256()
        for value in (
            doc_hash,
            candidate_id,
            fusion_version,
            input_hash,
        ):
            digest.update(str(value).encode("utf-8", errors="ignore"))
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def acceptance_input_hash(
        *,
        doc_hash: str,
        candidate_id: str,
        result_id: str,
        result_input_hash: str,
        accepted_latex: str,
    ) -> str:
        digest = hashlib.sha256()
        for value in (
            "formula_acceptance_r5_v1",
            doc_hash,
            candidate_id,
            result_id,
            result_input_hash,
            accepted_latex,
        ):
            digest.update(str(value).encode("utf-8", errors="ignore"))
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def acceptance_decision_id(
        *,
        doc_hash: str,
        candidate_id: str,
        result_id: str,
        action: str,
        decision_source: str,
        previous_result_id: str,
        input_hash: str,
        created_at: str,
    ) -> str:
        digest = hashlib.sha256()
        for value in (
            "formula_acceptance_decision_v1",
            doc_hash,
            candidate_id,
            result_id,
            action,
            decision_source,
            previous_result_id,
            input_hash,
            created_at,
        ):
            digest.update(str(value).encode("utf-8", errors="ignore"))
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def priority_for_block(block: DocumentBlock, priority_pages: set[int]) -> float:
        page_boost = 1000.0 if block.page_num in priority_pages else 0.0
        review_priority = block.metadata.get("semantic_review_priority")
        if review_priority is not None:
            try:
                review_score = float(review_priority)
            except (TypeError, ValueError):
                review_score = 0.0
            return page_boost + review_score - block.page_num * 0.001
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

    @staticmethod
    def _row_to_acceptance_decision(row: tuple[object, ...]) -> FormulaAcceptanceDecision:
        try:
            payload = json.loads(str(row[11] or "{}"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return FormulaAcceptanceDecision(
            decision_id=str(row[0]),
            doc_hash=str(row[1]),
            candidate_id=str(row[2]),
            result_id=str(row[3]),
            action=str(row[4]),  # type: ignore[arg-type]
            decision_source=str(row[5]),
            decider=str(row[6]),
            reason=str(row[7]),
            accepted_latex=str(row[8]),
            previous_result_id=str(row[9]),
            input_hash=str(row[10]),
            payload=payload,
            created_at=str(row[12]),
        )

    @staticmethod
    def _row_to_fusion_record(row: tuple[object, ...]) -> FormulaFusionRecord:
        try:
            ranked_ids = json.loads(str(row[6] or "[]"))
        except json.JSONDecodeError:
            ranked_ids = []
        if not isinstance(ranked_ids, list):
            ranked_ids = []
        try:
            risk_flags = json.loads(str(row[11] or "[]"))
        except json.JSONDecodeError:
            risk_flags = []
        if not isinstance(risk_flags, list):
            risk_flags = []
        try:
            accepted_gate = json.loads(str(row[12] or "{}"))
        except json.JSONDecodeError:
            accepted_gate = {}
        if not isinstance(accepted_gate, dict):
            accepted_gate = {}
        try:
            result_json = json.loads(str(row[14] or "{}"))
        except json.JSONDecodeError:
            result_json = {}
        if not isinstance(result_json, dict):
            result_json = {}
        return FormulaFusionRecord(
            fusion_id=str(row[0]),
            doc_hash=str(row[1]),
            candidate_id=str(row[2]),
            fusion_version=str(row[3]),
            input_hash=str(row[4]),
            best_result_id=str(row[5]),
            ranked_result_ids=tuple(str(item) for item in ranked_ids if str(item)),
            coverage=float(row[7]),
            agreement_score=float(row[8]),
            source_similarity=float(row[9]),
            syntax_valid=bool(row[10]),
            risk_flags=tuple(str(item) for item in risk_flags if str(item)),
            accepted_gate=accepted_gate,
            decision=str(row[13]),
            result_json=result_json,
            created_at=str(row[15]),
            updated_at=str(row[16]),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round_value(scan_round: str | FormulaScanRound) -> str:
    return scan_round.value if isinstance(scan_round, FormulaScanRound) else str(scan_round)


def _evidence_is_inline(evidence: dict[str, object]) -> bool:
    source = str(evidence.get("source", "") or "").lower()
    stage = str(evidence.get("source_stage", evidence.get("stage", "")) or "").lower()
    return "inline" in source or "inline" in stage


def _record_page_num(record: FormulaRecognitionRecord) -> int:
    candidates: list[object] = [record.evidence.get("page_num")]
    details = record.evidence.get("details")
    if isinstance(details, dict):
        candidates.append(details.get("page_num"))
    r0_region = record.evidence.get("r0_region")
    if isinstance(r0_region, dict):
        candidates.append(r0_region.get("page_num"))
    inline_pdf = record.evidence.get("inline_pdf_evidence")
    if isinstance(inline_pdf, dict):
        candidates.append(inline_pdf.get("page_num"))
    for value in candidates:
        try:
            page_num = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if page_num >= 0:
            return page_num
    return 0


def _record_bbox(record: FormulaRecognitionRecord) -> tuple[float, float, float, float]:
    candidates: list[object] = [record.evidence.get("bbox")]
    details = record.evidence.get("details")
    if isinstance(details, dict):
        candidates.append(details.get("bbox"))
    r0_region = record.evidence.get("r0_region")
    if isinstance(r0_region, dict):
        candidates.append(r0_region.get("bbox"))
    inline_pdf = record.evidence.get("inline_pdf_evidence")
    if isinstance(inline_pdf, dict):
        candidates.append(inline_pdf.get("bbox"))
    for value in candidates:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            continue
        try:
            return tuple(float(item) for item in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            continue
    return (0.0, 0.0, 0.0, 0.0)


def _best_latex_from_fusion_payload(payload: dict[str, object]) -> str:
    ranked = payload.get("ranked_candidates", [])
    if isinstance(ranked, list):
        for item in ranked:
            if not isinstance(item, dict):
                continue
            if str(item.get("result_id", "") or "") == str(payload.get("best_result_id", "") or ""):
                latex = str(item.get("latex", "") or "").strip()
                if latex:
                    return latex
        if ranked:
            first = ranked[0]
            if isinstance(first, dict):
                latex = str(first.get("latex", "") or "").strip()
                if latex:
                    return latex
    return str(payload.get("best_latex", "") or "").strip()

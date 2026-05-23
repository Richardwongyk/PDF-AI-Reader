"""Persistent GraphRAG indexing task queue.

The store only records block-level graph extraction work and extracted graph
artifacts. Actual extraction backends remain pluggable workers, so graph
indexing can stay disabled by default and outside the reading hot path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Literal

from src.core.models import DocumentBlock


GraphTaskStatus = Literal["queued", "running", "done", "failed", "skipped"]


@dataclass(frozen=True)
class GraphIndexTask:
    doc_hash: str
    filepath: str
    block_id: str
    page_num: int
    block_type: str
    priority: float
    status: GraphTaskStatus
    content_hash: str
    extractor: str = ""
    node_count: int = 0
    edge_count: int = 0
    error: str = ""
    attempts: int = 0
    updated_at: str = ""


class GraphIndexStore:
    """SQLite-backed queue for optional GraphRAG extraction workers."""

    def __init__(self, db_path: str = "data/graph_index_jobs.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS graph_index_jobs (
                doc_hash     TEXT NOT NULL,
                filepath     TEXT NOT NULL,
                block_id     TEXT NOT NULL,
                page_num     INTEGER NOT NULL,
                block_type   TEXT NOT NULL,
                priority     REAL NOT NULL DEFAULT 0,
                status       TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                extractor    TEXT NOT NULL DEFAULT '',
                node_count   INTEGER NOT NULL DEFAULT 0,
                edge_count   INTEGER NOT NULL DEFAULT 0,
                error        TEXT NOT NULL DEFAULT '',
                attempts     INTEGER NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                PRIMARY KEY (doc_hash, block_id)
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_graph_jobs_status "
            "ON graph_index_jobs(doc_hash, status, priority DESC, page_num ASC)"
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS graph_index_artifacts (
                doc_hash   TEXT NOT NULL,
                block_id   TEXT NOT NULL,
                extractor  TEXT NOT NULL,
                nodes_json TEXT NOT NULL DEFAULT '[]',
                edges_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (doc_hash, block_id, extractor)
            )
        """)
        self._conn.commit()
        self._lock = threading.Lock()

    def enqueue_blocks(
        self,
        doc_hash: str,
        filepath: str,
        blocks: list[DocumentBlock],
        priority_pages: set[int] | None = None,
    ) -> int:
        if not doc_hash or not filepath or not blocks:
            return 0
        priority_pages = priority_pages or set()
        now = _now()
        inserted = 0
        with self._lock:
            for block in blocks:
                content_hash = self.content_hash(block)
                row = self._conn.execute(
                    """SELECT status, content_hash FROM graph_index_jobs
                       WHERE doc_hash=? AND block_id=?""",
                    (doc_hash, block.id),
                ).fetchone()
                if row and row[0] == "done" and row[1] == content_hash:
                    continue
                self._conn.execute(
                    """INSERT INTO graph_index_jobs
                       (doc_hash, filepath, block_id, page_num, block_type, priority,
                        status, content_hash, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                       ON CONFLICT(doc_hash, block_id) DO UPDATE SET
                         filepath=excluded.filepath,
                         page_num=excluded.page_num,
                         block_type=excluded.block_type,
                         priority=max(graph_index_jobs.priority, excluded.priority),
                         status=CASE
                           WHEN graph_index_jobs.status='done'
                            AND graph_index_jobs.content_hash=excluded.content_hash
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
                        block.block_type.value,
                        self.priority_for_block(block, priority_pages),
                        content_hash,
                        now,
                        now,
                    ),
                )
                if not row or row[0] != "done" or row[1] != content_hash:
                    inserted += 1
            self._conn.commit()
        return inserted

    def mark_running(self, doc_hash: str, block_ids: list[str]) -> None:
        self._update_status(doc_hash, block_ids, "running", increment_attempts=True)

    def mark_done(
        self,
        doc_hash: str,
        block_id: str,
        extractor: str,
        nodes: list[dict[str, object]],
        edges: list[dict[str, object]],
    ) -> None:
        now = _now()
        nodes_json = json.dumps(nodes, ensure_ascii=False, separators=(",", ":"))
        edges_json = json.dumps(edges, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._conn.execute(
                """INSERT INTO graph_index_artifacts
                   (doc_hash, block_id, extractor, nodes_json, edges_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(doc_hash, block_id, extractor) DO UPDATE SET
                     nodes_json=excluded.nodes_json,
                     edges_json=excluded.edges_json,
                     updated_at=excluded.updated_at""",
                (doc_hash, block_id, extractor, nodes_json, edges_json, now),
            )
            self._conn.execute(
                """UPDATE graph_index_jobs
                   SET status='done', extractor=?, node_count=?, edge_count=?,
                       error='', updated_at=?
                   WHERE doc_hash=? AND block_id=?""",
                (extractor, len(nodes), len(edges), now, doc_hash, block_id),
            )
            self._conn.commit()

    def mark_failed(self, doc_hash: str, block_id: str, error: str) -> None:
        self._set_error_status(doc_hash, block_id, "failed", error)

    def mark_skipped(self, doc_hash: str, block_id: str, reason: str) -> None:
        self._set_error_status(doc_hash, block_id, "skipped", reason)

    def list_tasks(
        self,
        doc_hash: str,
        statuses: set[str] | None = None,
        limit: int = 100,
    ) -> list[GraphIndexTask]:
        params: list[object] = [doc_hash]
        where = "doc_hash=?"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            where += f" AND status IN ({placeholders})"
            params.extend(sorted(statuses))
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT doc_hash, filepath, block_id, page_num, block_type,
                           priority, status, content_hash, extractor, node_count,
                           edge_count, error, attempts, updated_at
                    FROM graph_index_jobs
                    WHERE {where}
                    ORDER BY priority DESC, page_num ASC, block_id ASC
                    LIMIT ?""",
                params,
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def counts(self, doc_hash: str) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT status, COUNT(*) FROM graph_index_jobs
                   WHERE doc_hash=? GROUP BY status""",
                (doc_hash,),
            ).fetchall()
        return {str(status): int(count) for status, count in rows}

    def pending_count(self, doc_hash: str) -> int:
        counts = self.counts(doc_hash)
        return counts.get("queued", 0) + counts.get("running", 0)

    def artifacts(self, doc_hash: str, block_id: str) -> list[dict[str, object]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT extractor, nodes_json, edges_json, updated_at
                   FROM graph_index_artifacts
                   WHERE doc_hash=? AND block_id=?
                   ORDER BY extractor ASC""",
                (doc_hash, block_id),
            ).fetchall()
        return [
            {
                "extractor": str(row[0]),
                "nodes": json.loads(str(row[1])),
                "edges": json.loads(str(row[2])),
                "updated_at": str(row[3]),
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _update_status(
        self,
        doc_hash: str,
        block_ids: list[str],
        status: GraphTaskStatus,
        increment_attempts: bool = False,
    ) -> None:
        if not doc_hash or not block_ids:
            return
        now = _now()
        attempts_expr = "attempts + 1" if increment_attempts else "attempts"
        with self._lock:
            self._conn.executemany(
                f"""UPDATE graph_index_jobs
                    SET status=?, attempts={attempts_expr}, updated_at=?
                    WHERE doc_hash=? AND block_id=?""",
                [(status, now, doc_hash, block_id) for block_id in block_ids],
            )
            self._conn.commit()

    def _set_error_status(
        self,
        doc_hash: str,
        block_id: str,
        status: GraphTaskStatus,
        error: str,
    ) -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """UPDATE graph_index_jobs
                   SET status=?, error=?, updated_at=?
                   WHERE doc_hash=? AND block_id=?""",
                (status, error[:500], now, doc_hash, block_id),
            )
            self._conn.commit()

    @staticmethod
    def content_hash(block: DocumentBlock) -> str:
        digest = hashlib.sha256()
        digest.update(block.id.encode("utf-8", errors="ignore"))
        digest.update(str(block.page_num).encode("ascii"))
        digest.update(block.block_type.value.encode("ascii"))
        digest.update("|".join(f"{value:.3f}" for value in block.bbox).encode("ascii"))
        digest.update((block.content or "").encode("utf-8", errors="ignore"))
        return digest.hexdigest()

    @staticmethod
    def priority_for_block(block: DocumentBlock, priority_pages: set[int]) -> float:
        page_boost = 1000.0 if block.page_num in priority_pages else 0.0
        type_boost = {
            "heading": 60.0,
            "formula": 50.0,
            "paragraph": 20.0,
            "table": 15.0,
            "image": 5.0,
        }.get(block.block_type.value, 0.0)
        theorem_boost = 40.0 if block.metadata.get("is_theorem") else 0.0
        return page_boost + type_boost + theorem_boost - block.page_num * 0.001

    @staticmethod
    def _row_to_task(row: tuple[object, ...]) -> GraphIndexTask:
        return GraphIndexTask(
            doc_hash=str(row[0]),
            filepath=str(row[1]),
            block_id=str(row[2]),
            page_num=int(row[3]),
            block_type=str(row[4]),
            priority=float(row[5]),
            status=str(row[6]),  # type: ignore[arg-type]
            content_hash=str(row[7]),
            extractor=str(row[8]),
            node_count=int(row[9]),
            edge_count=int(row[10]),
            error=str(row[11]),
            attempts=int(row[12]),
            updated_at=str(row[13]),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

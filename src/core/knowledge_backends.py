"""Knowledge index backend interfaces and adapters."""

from __future__ import annotations

import math
import hashlib
import re
import sqlite3
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.models import DocumentBlock, KnowledgeStatus, document_block_index_text

if TYPE_CHECKING:
    from src.data.chroma_repo import ChromaRepo


ProgressCallback = Callable[[int, int], None]


class KnowledgeIndexBackend(ABC):
    """Storage/retrieval backend used by KnowledgeEngine."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable backend name for logs and configuration."""
        ...

    @abstractmethod
    def build(
        self,
        blocks: list[DocumentBlock],
        doc_hash: str,
        force_rebuild: bool,
        emit_progress: ProgressCallback,
    ) -> None:
        """Build or rebuild a document index."""
        ...

    @abstractmethod
    def retrieve(
        self,
        query: str,
        query_vector: list[float],
        doc_hash: str,
        top_k: int,
        exclude_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve raw candidate blocks."""
        ...

    @abstractmethod
    def upsert_blocks(self, blocks: list[DocumentBlock], doc_hash: str) -> None:
        """Incrementally insert or update blocks without rebuilding the full index."""
        ...

    @abstractmethod
    def delete(self, doc_hash: str) -> None:
        """Delete a document index."""
        ...

    @abstractmethod
    def exists(self, doc_hash: str) -> bool:
        """Return True when a document index exists."""
        ...

    @abstractmethod
    def status(self, doc_hash: str) -> KnowledgeStatus:
        """Return document index status."""
        ...

    def index_matches(self, blocks: list[DocumentBlock], doc_hash: str) -> bool:
        """Return True when the persisted index fingerprint matches blocks."""
        return False

    def close(self) -> None:
        """Release backend resources."""


class LegacyChromaBackend(KnowledgeIndexBackend):
    """Current Chroma backend behind the new backend interface."""

    _BATCH_SIZE = 512

    def __init__(
        self,
        repo: ChromaRepo,
        embed_texts: Callable[[list[str]], list[list[float]]],
    ) -> None:
        self._repo = repo
        self._embed_texts = embed_texts

    @property
    def name(self) -> str:
        return "legacy_chroma"

    def build(
        self,
        blocks: list[DocumentBlock],
        doc_hash: str,
        force_rebuild: bool,
        emit_progress: ProgressCallback,
    ) -> None:
        if self.exists(doc_hash) and self.index_matches(blocks, doc_hash):
            emit_progress(len(blocks), len(blocks))
            return

        if force_rebuild or self.exists(doc_hash):
            self.delete(doc_hash)

        total = len(blocks)
        emit_progress(0, total)
        if total == 0:
            return

        text_contents = [document_block_index_text(block) for block in blocks]
        vectors = self._embed_texts(text_contents)
        block_ids = [block.id for block in blocks]
        metadatas = [_block_metadata(block) for block in blocks]

        for batch_start in range(0, total, self._BATCH_SIZE):
            batch_end = min(batch_start + self._BATCH_SIZE, total)
            self._repo.upsert_blocks(
                doc_hash,
                block_ids=block_ids[batch_start:batch_end],
                documents=text_contents[batch_start:batch_end],
                vectors=vectors[batch_start:batch_end],
                metadatas=metadatas[batch_start:batch_end],
            )
            emit_progress(batch_end, total)
        self._repo.update_collection_metadata(
            doc_hash,
            _index_metadata(self.name, blocks),
        )

    def upsert_blocks(self, blocks: list[DocumentBlock], doc_hash: str) -> None:
        total = len(blocks)
        if total == 0:
            return
        text_contents = [document_block_index_text(block) for block in blocks]
        vectors = self._embed_texts(text_contents)
        for batch_start in range(0, total, self._BATCH_SIZE):
            batch_end = min(batch_start + self._BATCH_SIZE, total)
            batch_blocks = blocks[batch_start:batch_end]
            self._repo.upsert_blocks(
                doc_hash,
                block_ids=[block.id for block in batch_blocks],
                documents=text_contents[batch_start:batch_end],
                vectors=vectors[batch_start:batch_end],
                metadatas=[_block_metadata(block) for block in batch_blocks],
            )

    def retrieve(
        self,
        query: str,
        query_vector: list[float],
        doc_hash: str,
        top_k: int,
        exclude_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._repo.query_relevant(
            doc_hash,
            query_vector,
            top_k=top_k,
            exclude_ids=exclude_ids,
        )

    def delete(self, doc_hash: str) -> None:
        self._repo.delete_collection(doc_hash)

    def exists(self, doc_hash: str) -> bool:
        return self._repo.collection_exists(doc_hash)

    def index_matches(self, blocks: list[DocumentBlock], doc_hash: str) -> bool:
        if not self.exists(doc_hash):
            return False
        try:
            metadata = self._repo.get_collection_metadata(doc_hash)
        except Exception:
            return False
        return _metadata_matches_blocks(metadata, self.name, blocks)

    def status(self, doc_hash: str) -> KnowledgeStatus:
        exists = self.exists(doc_hash)
        total_blocks = 0
        if exists:
            try:
                collection = self._repo.get_collection(doc_hash)
                total_blocks = collection.count()
            except Exception:
                total_blocks = 0
        return KnowledgeStatus(
            doc_hash=doc_hash,
            collection_name=f"pdf_{doc_hash}",
            is_ready=exists,
            total_blocks=total_blocks,
            embedded_blocks=total_blocks,
        )

    def close(self) -> None:
        self._repo.close()


class LlamaIndexChromaBackend(KnowledgeIndexBackend):
    """LlamaIndex Chroma backend with version-isolated collections."""

    COLLECTION_PREFIX = "pdf_li_v1_"
    _BATCH_SIZE = 512

    def __init__(
        self,
        repo: ChromaRepo,
        embed_texts: Callable[[list[str]], list[list[float]]],
    ) -> None:
        self._repo = repo
        self._embed_texts = embed_texts

    @property
    def name(self) -> str:
        return "llamaindex_chroma"

    def build(
        self,
        blocks: list[DocumentBlock],
        doc_hash: str,
        force_rebuild: bool,
        emit_progress: ProgressCallback,
    ) -> None:
        if self.exists(doc_hash) and self.index_matches(blocks, doc_hash):
            emit_progress(len(blocks), len(blocks))
            return

        if force_rebuild or self.exists(doc_hash):
            self.delete(doc_hash)

        total = len(blocks)
        emit_progress(0, total)
        if total == 0:
            return

        collection = self._repo.create_or_get_collection(doc_hash, self.COLLECTION_PREFIX)
        for batch_start in range(0, total, self._BATCH_SIZE):
            batch_end = min(batch_start + self._BATCH_SIZE, total)
            batch_blocks = blocks[batch_start:batch_end]
            batch_texts = [document_block_index_text(block) for block in batch_blocks]
            vectors = self._embed_texts(batch_texts)
            collection.upsert(
                ids=[block.id for block in batch_blocks],
                documents=batch_texts,
                embeddings=vectors,
                metadatas=[
                    {
                        **_block_metadata(block),
                        "block_id": block.id,
                        "index_backend": self.name,
                    }
                    for block in batch_blocks
                ],
            )
            emit_progress(batch_end, total)
        self._repo.update_collection_metadata(
            doc_hash,
            _index_metadata(self.name, blocks),
            self.COLLECTION_PREFIX,
        )

    def upsert_blocks(self, blocks: list[DocumentBlock], doc_hash: str) -> None:
        total = len(blocks)
        if total == 0:
            return
        collection = self._repo.create_or_get_collection(doc_hash, self.COLLECTION_PREFIX)
        for batch_start in range(0, total, self._BATCH_SIZE):
            batch_end = min(batch_start + self._BATCH_SIZE, total)
            batch_blocks = blocks[batch_start:batch_end]
            batch_texts = [document_block_index_text(block) for block in batch_blocks]
            vectors = self._embed_texts(batch_texts)
            collection.upsert(
                ids=[block.id for block in batch_blocks],
                documents=batch_texts,
                embeddings=vectors,
                metadatas=[
                    {
                        **_block_metadata(block),
                        "block_id": block.id,
                        "index_backend": self.name,
                    }
                    for block in batch_blocks
                ],
            )

    def retrieve(
        self,
        query: str,
        query_vector: list[float],
        doc_hash: str,
        top_k: int,
        exclude_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []

        collection = self._repo.get_collection(doc_hash, self.COLLECTION_PREFIX)
        collection_count = collection.count()
        if collection_count <= 0:
            return []
        fetch_count = min(top_k + len(exclude_ids or []), collection_count)

        result: dict[str, Any] = collection.query(
            query_embeddings=[query_vector],
            n_results=fetch_count,
            include=["documents", "metadatas", "distances"],
        )
        entries: list[dict[str, Any]] = []
        if not result.get("ids") or not result["ids"][0]:
            return entries
        for index, block_id in enumerate(result["ids"][0]):
            if exclude_ids and block_id in exclude_ids:
                continue
            entries.append({
                "id": block_id,
                "document": result["documents"][0][index] if result.get("documents") else "",
                "metadata": result["metadatas"][0][index] if result.get("metadatas") else {},
                "distance": result["distances"][0][index] if result.get("distances") else 0.0,
            })
            if len(entries) >= top_k:
                break
        return entries

    def delete(self, doc_hash: str) -> None:
        self._repo.delete_collection(doc_hash, self.COLLECTION_PREFIX)

    def exists(self, doc_hash: str) -> bool:
        return self._repo.collection_exists(doc_hash, self.COLLECTION_PREFIX)

    def index_matches(self, blocks: list[DocumentBlock], doc_hash: str) -> bool:
        if not self.exists(doc_hash):
            return False
        try:
            metadata = self._repo.get_collection_metadata(doc_hash, self.COLLECTION_PREFIX)
        except Exception:
            return False
        return _metadata_matches_blocks(metadata, self.name, blocks)

    def status(self, doc_hash: str) -> KnowledgeStatus:
        exists = self.exists(doc_hash)
        total_blocks = 0
        if exists:
            try:
                total_blocks = self._repo.get_collection(
                    doc_hash, self.COLLECTION_PREFIX
                ).count()
            except Exception:
                total_blocks = 0
        return KnowledgeStatus(
            doc_hash=doc_hash,
            collection_name=f"{self.COLLECTION_PREFIX}{doc_hash}",
            is_ready=exists,
            total_blocks=total_blocks,
            embedded_blocks=total_blocks,
        )

    def close(self) -> None:
        self._repo.close()

    def _vector_store(self, doc_hash: str) -> Any:
        """Return a LlamaIndex vector store for future graph/RAG composition."""
        try:
            from llama_index.vector_stores.chroma import ChromaVectorStore
        except ImportError as exc:
            raise RuntimeError(
                "llamaindex_chroma 后端需要安装 llama-index-core "
                "和 llama-index-vector-stores-chroma。"
            ) from exc
        collection = self._repo.create_or_get_collection(doc_hash, self.COLLECTION_PREFIX)
        return ChromaVectorStore(chroma_collection=collection)

    @staticmethod
    def _to_node(block: DocumentBlock, vector: list[float]) -> Any:
        try:
            from llama_index.core.schema import TextNode
        except ImportError as exc:
            raise RuntimeError("llamaindex_chroma 后端缺少 llama-index-core。") from exc
        node = TextNode(
            id_=block.id,
            text=document_block_index_text(block),
            metadata={
                **_block_metadata(block),
                "block_id": block.id,
            },
        )
        node.embedding = vector
        return node


class SQLiteFtsBackend(KnowledgeIndexBackend):
    """Fast local full-text backend based on SQLite FTS5.

    This backend is intentionally lexical. It is a lightweight baseline for
    large documents when real semantic embeddings are not available, and it keeps
    the same evidence shape as vector backends so the UI does not change.
    """

    _SCHEMA_VERSION = "fts_v1"
    _BATCH_SIZE = 2000

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return "sqlite_fts"

    def build(
        self,
        blocks: list[DocumentBlock],
        doc_hash: str,
        force_rebuild: bool,
        emit_progress: ProgressCallback,
    ) -> None:
        if self.exists(doc_hash) and self.index_matches(blocks, doc_hash):
            emit_progress(len(blocks), len(blocks))
            return

        total = len(blocks)
        emit_progress(0, total)
        path = self._db_path(doc_hash)
        with sqlite3.connect(path) as conn:
            self._init_db(conn)
            conn.execute("DELETE FROM blocks")
            conn.execute("DELETE FROM block_fts")
            conn.execute("DELETE FROM meta")
            now = time.time()
            for batch_start in range(0, total, self._BATCH_SIZE):
                batch = blocks[batch_start:batch_start + self._BATCH_SIZE]
                rows = [
                    (
                        block.id,
                        block.page_num,
                        block.block_type.value,
                        block.section_title,
                        _format_float(block.bbox[0]),
                        _format_float(block.bbox[1]),
                        _format_float(block.bbox[2]),
                        _format_float(block.bbox[3]),
                        document_block_index_text(block),
                        str(block.metadata.get("summary", "")),
                        self._keywords_text(block),
                        bool(block.metadata.get("needs_ocr", False)),
                        str(block.metadata.get("formula_detector", "")),
                        str(block.metadata.get("formula_ocr", "")),
                        str(block.metadata.get("latex_source", "")),
                        str(block.metadata.get("source", "")),
                    )
                    for block in batch
                ]
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO blocks(
                        id, page, type, section, bbox_x0, bbox_y0, bbox_x1, bbox_y1,
                        document, summary, keywords, needs_ocr, formula_detector,
                        formula_ocr, latex_source, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.executemany(
                    """
                    INSERT INTO block_fts(id, document, section, summary, keywords)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            block.id,
                            document_block_index_text(block),
                            block.section_title,
                            str(block.metadata.get("summary", "")),
                            self._keywords_text(block),
                        )
                        for block in batch
                    ],
                )
                emit_progress(min(batch_start + len(batch), total), total)
            metadata = _index_metadata(self.name, blocks)
            metadata["updated_at"] = str(now)
            conn.executemany(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                [(key, str(value)) for key, value in metadata.items()],
            )
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", ("schema", self._SCHEMA_VERSION))
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", ("block_count", str(total)))
            conn.commit()

    def retrieve(
        self,
        query: str,
        query_vector: list[float],
        doc_hash: str,
        top_k: int,
        exclude_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if top_k <= 0 or not self.exists(doc_hash):
            return []
        match_query = self._fts_query(query)
        if not match_query:
            return self._recent_blocks(doc_hash, top_k, exclude_ids)
        with sqlite3.connect(self._db_path(doc_hash)) as conn:
            conn.row_factory = sqlite3.Row
            exclude = set(exclude_ids or [])
            rows = conn.execute(
                """
                SELECT
                    b.id, b.page, b.type, b.section, b.bbox_x0, b.bbox_y0, b.bbox_x1, b.bbox_y1,
                    b.document, b.summary, b.keywords, b.needs_ocr, b.formula_detector,
                    b.formula_ocr, b.latex_source, b.source,
                    bm25(block_fts, 0.0, 10.0, 3.0, 2.0, 2.0) AS rank
                FROM block_fts
                JOIN blocks b ON b.id = block_fts.id
                WHERE block_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match_query, max(top_k + len(exclude), top_k)),
            ).fetchall()
        entries: list[dict[str, Any]] = []
        for row in rows:
            if row["id"] in exclude:
                continue
            entries.append(self._row_to_entry(row, distance=self._fts_rank_distance(row["rank"], len(entries))))
            if len(entries) >= top_k:
                break
        if not entries and self._should_use_recent_fallback(query):
            return self._recent_blocks(doc_hash, top_k, exclude_ids)
        return entries

    def upsert_blocks(self, blocks: list[DocumentBlock], doc_hash: str) -> None:
        if not blocks:
            return
        path = self._db_path(doc_hash)
        with sqlite3.connect(path) as conn:
            self._init_db(conn)
            for block in blocks:
                row = (
                    block.id,
                    block.page_num,
                    block.block_type.value,
                    block.section_title,
                    _format_float(block.bbox[0]),
                    _format_float(block.bbox[1]),
                    _format_float(block.bbox[2]),
                    _format_float(block.bbox[3]),
                    document_block_index_text(block),
                    str(block.metadata.get("summary", "")),
                    self._keywords_text(block),
                    bool(block.metadata.get("needs_ocr", False)),
                    str(block.metadata.get("formula_detector", "")),
                    str(block.metadata.get("formula_ocr", "")),
                    str(block.metadata.get("latex_source", "")),
                    str(block.metadata.get("source", "")),
                )
                conn.execute(
                    """
                    INSERT OR REPLACE INTO blocks(
                        id, page, type, section, bbox_x0, bbox_y0, bbox_x1, bbox_y1,
                        document, summary, keywords, needs_ocr, formula_detector,
                        formula_ocr, latex_source, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                conn.execute("DELETE FROM block_fts WHERE id = ?", (block.id,))
                conn.execute(
                    """
                    INSERT INTO block_fts(id, document, section, summary, keywords)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (block.id, row[8], row[3], row[9], row[10]),
                )
            conn.commit()

    def delete(self, doc_hash: str) -> None:
        path = self._db_path(doc_hash)
        for candidate in (path, path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm")):
            if candidate.exists():
                candidate.unlink()

    def exists(self, doc_hash: str) -> bool:
        path = self._db_path(doc_hash)
        if not path.exists():
            return False
        try:
            with sqlite3.connect(path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
                rows = conn.execute("SELECT key, value FROM meta").fetchall()
            metadata = {str(key): str(value) for key, value in rows}
            return self._has_complete_index_metadata(int(count), metadata)
        except (sqlite3.DatabaseError, TypeError, ValueError):
            return False

    def index_matches(self, blocks: list[DocumentBlock], doc_hash: str) -> bool:
        metadata = self._metadata(doc_hash)
        return _metadata_matches_blocks(metadata, self.name, blocks)

    def status(self, doc_hash: str) -> KnowledgeStatus:
        total = 0
        ready = False
        path = self._db_path(doc_hash)
        if path.exists():
            try:
                with sqlite3.connect(path) as conn:
                    total = int(conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0])
                    rows = conn.execute("SELECT key, value FROM meta").fetchall()
                metadata = {str(key): str(value) for key, value in rows}
                ready = self._has_complete_index_metadata(total, metadata)
            except (sqlite3.DatabaseError, TypeError, ValueError):
                total = 0
                ready = False
        return KnowledgeStatus(
            doc_hash=doc_hash,
            collection_name=f"sqlite_fts_{doc_hash}",
            is_ready=ready,
            total_blocks=total,
            embedded_blocks=total,
        )

    def close(self) -> None:
        return None

    def _db_path(self, doc_hash: str) -> Path:
        safe_hash = re.sub(r"[^A-Za-z0-9_-]+", "_", doc_hash)
        return self._base_dir / f"{safe_hash}.db"

    def _init_db(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blocks(
                id TEXT PRIMARY KEY,
                page INTEGER NOT NULL,
                type TEXT NOT NULL,
                section TEXT NOT NULL DEFAULT '',
                bbox_x0 TEXT NOT NULL,
                bbox_y0 TEXT NOT NULL,
                bbox_x1 TEXT NOT NULL,
                bbox_y1 TEXT NOT NULL,
                document TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                keywords TEXT NOT NULL DEFAULT '',
                needs_ocr INTEGER NOT NULL DEFAULT 0,
                formula_detector TEXT NOT NULL DEFAULT '',
                formula_ocr TEXT NOT NULL DEFAULT '',
                latex_source TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS block_fts USING fts5(
                id UNINDEXED,
                document,
                section,
                summary,
                keywords,
                tokenize='unicode61'
            )
            """
        )
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def _metadata(self, doc_hash: str) -> dict[str, Any]:
        if not self._db_path(doc_hash).exists():
            return {}
        try:
            with sqlite3.connect(self._db_path(doc_hash)) as conn:
                rows = conn.execute("SELECT key, value FROM meta").fetchall()
        except sqlite3.DatabaseError:
            return {}
        return {str(key): value for key, value in rows}

    def _recent_blocks(
        self,
        doc_hash: str,
        top_k: int,
        exclude_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        with sqlite3.connect(self._db_path(doc_hash)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT
                    id, page, type, section, bbox_x0, bbox_y0, bbox_x1, bbox_y1,
                    document, summary, keywords, needs_ocr, formula_detector,
                    formula_ocr, latex_source, source
                FROM blocks ORDER BY page, id LIMIT ?
                """,
                (max(top_k + len(exclude_ids or []), top_k),),
            ).fetchall()
        excluded = set(exclude_ids or [])
        entries: list[dict[str, Any]] = []
        for row in rows:
            if row["id"] in excluded:
                continue
            entries.append(self._row_to_entry(row, distance=1.0))
            if len(entries) >= top_k:
                break
        return entries

    @staticmethod
    def _keywords_text(block: DocumentBlock) -> str:
        keywords = block.metadata.get("keywords", "")
        if isinstance(keywords, list):
            return " ".join(str(item) for item in keywords)
        return str(keywords or "")

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", str(query or "").lower())
        cleaned = [token for token in tokens if token]
        return " OR ".join(cleaned[:32])

    @staticmethod
    def _should_use_recent_fallback(query: str) -> bool:
        text = str(query or "")
        has_cjk = re.search(r"[\u4e00-\u9fff]", text) is not None
        has_ascii_word = re.search(r"[A-Za-z0-9_]+", text) is not None
        return has_cjk and not has_ascii_word

    @staticmethod
    def _fts_rank_distance(rank: Any, index: int) -> float:
        """Convert SQLite FTS BM25 order into a small distance-like value."""
        try:
            rank_value = abs(float(rank))
        except (TypeError, ValueError):
            rank_value = 0.0
        return min(4.0, rank_value + max(index, 0) * 0.08)

    def _has_complete_index_metadata(self, block_count: int, metadata: dict[str, Any]) -> bool:
        if block_count <= 0:
            return False
        try:
            indexed_blocks = int(metadata.get("index_block_count", "0"))
        except (TypeError, ValueError):
            return False
        return (
            str(metadata.get("schema")) == self._SCHEMA_VERSION
            and str(metadata.get("index_backend")) == self.name
            and str(metadata.get("index_schema")) == "blocks_v1"
            and indexed_blocks > 0
        )

    @staticmethod
    def _row_to_entry(row: sqlite3.Row, distance: float) -> dict[str, Any]:
        return {
            "id": row["id"],
            "document": row["document"],
            "metadata": {
                "page": int(row["page"]),
                "type": row["type"],
                "section": row["section"],
                "summary": row["summary"],
                "keywords": row["keywords"],
                "bbox": ",".join((row["bbox_x0"], row["bbox_y0"], row["bbox_x1"], row["bbox_y1"])),
                "needs_ocr": bool(row["needs_ocr"]),
                "formula_detector": row["formula_detector"],
                "formula_ocr": row["formula_ocr"],
                "latex_source": row["latex_source"],
                "source": row["source"],
            },
            "distance": distance,
        }


def create_knowledge_backend(
    backend_name: str,
    repo: ChromaRepo | None,
    embed_texts: Callable[[list[str]], list[list[float]]],
    sqlite_dir: str | Path | None = None,
) -> KnowledgeIndexBackend:
    """Create a configured knowledge backend."""
    normalized = backend_name.strip().lower()
    if normalized == "sqlite_fts":
        return SQLiteFtsBackend(sqlite_dir or Path("data") / "knowledge_bases_fts")
    if repo is None:
        raise ValueError(f"知识库后端 {backend_name} 需要 ChromaRepo")
    if normalized == "legacy_chroma":
        return LegacyChromaBackend(repo, embed_texts)
    if normalized == "llamaindex_chroma":
        return LlamaIndexChromaBackend(repo, embed_texts)
    raise ValueError(f"未知知识库后端: {backend_name}")


def _block_metadata(block: DocumentBlock) -> dict[str, Any]:
    keywords = block.metadata.get("keywords", "")
    if isinstance(keywords, list):
        keywords = ", ".join(str(item) for item in keywords)
    bbox = ",".join(_format_float(value) for value in block.bbox)
    return {
        "page": block.page_num,
        "type": block.block_type.value,
        "section": block.section_title,
        "summary": block.metadata.get("summary", ""),
        "keywords": str(keywords or ""),
        "bbox": bbox,
        "needs_ocr": bool(block.metadata.get("needs_ocr", False)),
        "formula_detector": str(block.metadata.get("formula_detector", "")),
        "formula_ocr": str(block.metadata.get("formula_ocr", "")),
        "latex_source": str(block.metadata.get("latex_source", "")),
        "source": str(block.metadata.get("source", "")),
    }


def build_blocks_fingerprint(blocks: list[DocumentBlock]) -> str:
    """Stable fingerprint for the current knowledge-indexable block content."""
    digest = hashlib.sha256()
    for block in sorted(blocks, key=lambda item: item.id):
        digest.update(block.id.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update(str(block.page_num).encode("ascii", errors="ignore"))
        digest.update(b"\0")
        digest.update(block.block_type.value.encode("ascii", errors="ignore"))
        digest.update(b"\0")
        digest.update(block.content.encode("utf-8", errors="ignore"))
        digest.update(b"\0")
        digest.update(str(bool(block.metadata.get("needs_ocr", False))).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _index_metadata(backend_name: str, blocks: list[DocumentBlock]) -> dict[str, Any]:
    return {
        "index_backend": backend_name,
        "index_fingerprint": build_blocks_fingerprint(blocks),
        "index_block_count": len(blocks),
        "index_schema": "blocks_v1",
    }


def _metadata_matches_blocks(
    metadata: dict[str, Any],
    backend_name: str,
    blocks: list[DocumentBlock],
) -> bool:
    return (
        str(metadata.get("index_backend", "")) == backend_name
        and str(metadata.get("index_schema", "")) == "blocks_v1"
        and int(metadata.get("index_block_count", -1)) >= len(blocks)
        and str(metadata.get("index_fingerprint", "")) == build_blocks_fingerprint(blocks)
    )


def _format_float(value: float) -> str:
    if math.isfinite(value):
        return f"{value:.2f}"
    return "0.00"

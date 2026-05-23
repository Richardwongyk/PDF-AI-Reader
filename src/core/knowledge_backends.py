"""Knowledge index backend interfaces and adapters."""

from __future__ import annotations

import math
import hashlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from src.core.models import DocumentBlock, KnowledgeStatus, document_block_index_text
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
        if not force_rebuild and self.exists(doc_hash):
            emit_progress(len(blocks), len(blocks))
            return

        if force_rebuild:
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
        if not force_rebuild and self.exists(doc_hash):
            emit_progress(len(blocks), len(blocks))
            return

        if force_rebuild:
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


def create_knowledge_backend(
    backend_name: str,
    repo: ChromaRepo,
    embed_texts: Callable[[list[str]], list[list[float]]],
) -> KnowledgeIndexBackend:
    """Create a configured knowledge backend."""
    normalized = backend_name.strip().lower()
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

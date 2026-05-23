from __future__ import annotations

import pytest

from src.core.knowledge_backends import (
    LlamaIndexChromaBackend,
    LegacyChromaBackend,
    _block_metadata,
    create_knowledge_backend,
)
from src.core.models import BlockType, DocumentBlock, KnowledgeStatus


class _Repo:
    def __init__(self) -> None:
        self.upserts: list[dict[str, object]] = []
        self.deleted: list[str] = []
        self.exists = False
        self.count = 0

    def collection_exists(self, doc_hash: str) -> bool:
        return self.exists

    def delete_collection(self, doc_hash: str) -> None:
        self.deleted.append(doc_hash)
        self.exists = False

    def upsert_blocks(
        self,
        doc_hash: str,
        block_ids: list[str],
        documents: list[str],
        vectors: list[list[float]],
        metadatas: list[dict[str, object]] | None = None,
    ) -> None:
        self.upserts.append({
            "doc_hash": doc_hash,
            "block_ids": block_ids,
            "documents": documents,
            "vectors": vectors,
            "metadatas": metadatas,
        })
        self.exists = True
        self.count += len(block_ids)

    def query_relevant(self, **kwargs: object) -> list[dict[str, object]]:
        return [{"id": "p0_b0", "document": "attention", "metadata": {}, "distance": 0.1}]

    def get_collection(self, doc_hash: str) -> object:
        count = self.count

        class _Collection:
            def count(self) -> int:
                return count

        return _Collection()

    def close(self) -> None:
        pass


def _block(block_id: str = "p0_b0") -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="Scaled dot-product attention uses queries and keys.",
        bbox=(1.0, 2.0, 3.0, 4.0),
        section_title="Attention",
        metadata={"summary": "attention summary", "keywords": ["attention", "keys"]},
    )


def test_block_metadata_preserves_pdf_location_and_keywords() -> None:
    metadata = _block_metadata(_block())

    assert metadata["page"] == 0
    assert metadata["type"] == "paragraph"
    assert metadata["section"] == "Attention"
    assert metadata["keywords"] == "attention, keys"
    assert metadata["bbox"] == "1.00,2.00,3.00,4.00"


def test_legacy_backend_builds_with_progress_and_status() -> None:
    repo = _Repo()
    backend = LegacyChromaBackend(
        repo,  # type: ignore[arg-type]
        embed_texts=lambda texts: [[1.0, 0.0] for _ in texts],
    )
    progress: list[tuple[int, int]] = []

    backend.build([_block(), _block("p0_b1")], "doc-1", True, lambda c, t: progress.append((c, t)))

    assert repo.deleted == ["doc-1"]
    assert len(repo.upserts) == 1
    assert progress == [(0, 2), (2, 2)]
    status = backend.status("doc-1")
    assert isinstance(status, KnowledgeStatus)
    assert status.is_ready is True
    assert status.total_blocks == 2


def test_legacy_backend_upserts_formula_ocr_metadata() -> None:
    repo = _Repo()
    embedded_texts: list[list[str]] = []
    backend = LegacyChromaBackend(
        repo,  # type: ignore[arg-type]
        embed_texts=lambda texts: embedded_texts.append(texts) or [[0.5, 0.5] for _ in texts],
    )
    formula = DocumentBlock(
        id="p1_b2",
        page_num=1,
        block_type=BlockType.FORMULA,
        content=r"\frac{a}{b}",
        bbox=(10, 20, 30, 40),
        metadata={
            "needs_ocr": False,
            "formula_detector": "pix2text-mfd",
            "formula_ocr": "pix2text-mfr",
            "latex_source": "background_formula_index",
            "source": "image_or_scan",
        },
    )

    backend.upsert_blocks([formula], "doc-1")

    assert embedded_texts == [[r"\frac{a}{b}"]]
    assert repo.upserts[-1]["block_ids"] == ["p1_b2"]
    metadata = repo.upserts[-1]["metadatas"][0]  # type: ignore[index]
    assert metadata["type"] == "formula"
    assert metadata["needs_ocr"] is False
    assert metadata["formula_ocr"] == "pix2text-mfr"
    assert metadata["latex_source"] == "background_formula_index"


def test_backend_factory_rejects_unknown_backend() -> None:
    repo = _Repo()

    with pytest.raises(ValueError):
        create_knowledge_backend("unknown", repo, lambda texts: [])  # type: ignore[arg-type]


def test_llamaindex_backend_is_versioned_but_not_default() -> None:
    repo = _Repo()
    backend = LlamaIndexChromaBackend(repo, lambda texts: [])  # type: ignore[arg-type]

    assert backend.name == "llamaindex_chroma"


def test_backend_factory_creates_llamaindex_backend() -> None:
    repo = _Repo()
    backend = create_knowledge_backend(
        "llamaindex_chroma",
        repo,  # type: ignore[arg-type]
        lambda texts: [],
    )

    assert backend.name == "llamaindex_chroma"

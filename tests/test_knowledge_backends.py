from __future__ import annotations

import pytest

from src.core.knowledge_backends import (
    LlamaIndexChromaBackend,
    LegacyChromaBackend,
    SQLiteFtsBackend,
    _block_metadata,
    build_blocks_fingerprint,
    create_knowledge_backend,
)
from src.core.models import BlockType, DocumentBlock, KnowledgeStatus


class _Repo:
    def __init__(self) -> None:
        self.upserts: list[dict[str, object]] = []
        self.deleted: list[str] = []
        self.exists = False
        self.count = 0
        self.metadata: dict[str, object] = {}

    def collection_exists(self, doc_hash: str, collection_prefix: str | None = None) -> bool:
        return self.exists

    def delete_collection(self, doc_hash: str, collection_prefix: str | None = None) -> None:
        self.deleted.append(doc_hash)
        self.exists = False
        self.metadata = {}

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
        repo = self

        class _Collection:
            def count(self) -> int:
                return repo.count

            @property
            def metadata(self) -> dict[str, object]:
                return repo.metadata

            def modify(self, metadata: dict[str, object]) -> None:
                repo.metadata = metadata

        return _Collection()

    def update_collection_metadata(
        self,
        doc_hash: str,
        metadata: dict[str, object],
        collection_prefix: str | None = None,
    ) -> None:
        self.metadata = dict(metadata)

    def get_collection_metadata(
        self,
        doc_hash: str,
        collection_prefix: str | None = None,
    ) -> dict[str, object]:
        return dict(self.metadata)

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


def test_blocks_fingerprint_changes_when_indexable_content_changes() -> None:
    block = _block()
    original = build_blocks_fingerprint([block])
    block.content = "Different content"

    assert build_blocks_fingerprint([block]) != original


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


def test_legacy_backend_index_metadata_omits_chroma_distance_config() -> None:
    repo = _Repo()
    backend = LegacyChromaBackend(
        repo,  # type: ignore[arg-type]
        embed_texts=lambda texts: [[1.0, 0.0] for _ in texts],
    )

    backend.build([_block()], "doc-1", True, lambda c, t: None)

    assert "hnsw:space" not in repo.metadata
    assert repo.metadata["index_schema"] == "blocks_v1"


def test_legacy_backend_skips_force_rebuild_when_fingerprint_matches() -> None:
    repo = _Repo()
    backend = LegacyChromaBackend(
        repo,  # type: ignore[arg-type]
        embed_texts=lambda texts: [[1.0, 0.0] for _ in texts],
    )
    blocks = [_block(), _block("p0_b1")]
    progress: list[tuple[int, int]] = []

    backend.build(blocks, "doc-1", True, lambda c, t: progress.append((c, t)))
    backend.build(blocks, "doc-1", True, lambda c, t: progress.append((c, t)))

    assert repo.deleted == ["doc-1"]
    assert len(repo.upserts) == 1
    assert progress[-1] == (2, 2)


def test_legacy_backend_skips_when_incremental_formula_blocks_exist() -> None:
    repo = _Repo()
    backend = LegacyChromaBackend(
        repo,  # type: ignore[arg-type]
        embed_texts=lambda texts: [[1.0, 0.0] for _ in texts],
    )
    blocks = [_block(), _block("p0_b1")]

    backend.build(blocks, "doc-1", True, lambda c, t: None)
    repo.count += 2
    repo.metadata["index_block_count"] = 4
    backend.build(blocks, "doc-1", True, lambda c, t: None)

    assert len(repo.upserts) == 1
    assert repo.deleted == ["doc-1"]


def test_legacy_backend_force_rebuilds_when_fingerprint_changes() -> None:
    repo = _Repo()
    backend = LegacyChromaBackend(
        repo,  # type: ignore[arg-type]
        embed_texts=lambda texts: [[1.0, 0.0] for _ in texts],
    )
    blocks = [_block()]
    changed = [_block()]
    changed[0].content = "Updated attention content"

    backend.build(blocks, "doc-1", True, lambda c, t: None)
    backend.build(changed, "doc-1", True, lambda c, t: None)

    assert repo.deleted == ["doc-1", "doc-1"]
    assert len(repo.upserts) == 2
    assert repo.upserts[-1]["documents"] == ["Updated attention content"]


def test_legacy_backend_skips_nonforced_rebuild_when_index_exists() -> None:
    repo = _Repo()
    backend = LegacyChromaBackend(
        repo,  # type: ignore[arg-type]
        embed_texts=lambda texts: [[1.0, 0.0] for _ in texts],
    )
    blocks = [_block(), _block("p0_b1")]
    progress: list[tuple[int, int]] = []

    backend.build(blocks, "doc-1", True, lambda c, t: progress.append((c, t)))
    backend.build(blocks, "doc-1", False, lambda c, t: progress.append((c, t)))

    assert repo.deleted == ["doc-1"]
    assert len(repo.upserts) == 1
    assert progress[-1] == (2, 2)


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

    assert embedded_texts == [[f"$$\n{r'\frac{a}{b}'}\n$$"]]
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


def test_backend_factory_requires_repo_for_chroma_backends() -> None:
    with pytest.raises(ValueError):
        create_knowledge_backend(
            "legacy_chroma",
            None,
            lambda texts: [],
        )


def test_sqlite_fts_backend_builds_and_retrieves(tmp_path) -> None:
    backend = SQLiteFtsBackend(tmp_path)
    blocks = [
        _block("p0_b0"),
        DocumentBlock(
            id="p1_b0",
            page_num=1,
            block_type=BlockType.PARAGRAPH,
            content="Riemannian curvature tensor and manifold geodesics.",
            bbox=(0, 0, 1, 1),
            section_title="Geometry",
        ),
    ]
    progress: list[tuple[int, int]] = []

    backend.build(blocks, "doc-fts", True, lambda c, t: progress.append((c, t)))
    results = backend.retrieve(
        "How does attention use keys?",
        query_vector=[],
        doc_hash="doc-fts",
        top_k=1,
    )

    assert progress[-1] == (2, 2)
    assert backend.exists("doc-fts") is True
    assert backend.status("doc-fts").total_blocks == 2
    assert results[0]["id"] == "p0_b0"
    assert results[0]["metadata"]["page"] == 0


def test_sqlite_fts_backend_skips_matching_rebuild(tmp_path) -> None:
    backend = SQLiteFtsBackend(tmp_path)
    blocks = [_block("p0_b0"), _block("p0_b1")]
    progress: list[tuple[int, int]] = []

    backend.build(blocks, "doc-fts", True, lambda c, t: progress.append((c, t)))
    backend.build(blocks, "doc-fts", True, lambda c, t: progress.append((c, t)))

    assert progress[-1] == (2, 2)
    assert backend.status("doc-fts").total_blocks == 2


def test_backend_factory_creates_sqlite_fts_backend(tmp_path) -> None:
    backend = create_knowledge_backend(
        "sqlite_fts",
        None,
        lambda texts: [],
        sqlite_dir=tmp_path,
    )

    assert backend.name == "sqlite_fts"

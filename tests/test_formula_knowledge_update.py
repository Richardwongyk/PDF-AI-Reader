from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.app.formula_knowledge_update import FormulaKnowledgeUpdateService
from src.core.models import BlockType, DocumentBlock


class _KnowledgeEngine:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists
        self.upserts: list[tuple[str, list[DocumentBlock]]] = []

    def check_exists(self, doc_hash: str) -> bool:
        return self.exists

    def upsert_blocks(self, blocks: list[DocumentBlock], doc_hash: str) -> None:
        self.upserts.append((doc_hash, [block.model_copy(deep=True) for block in blocks]))


def _formula(block_id: str = "p0_b1", content: str = r"$$old$$") -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        page_num=0,
        block_type=BlockType.FORMULA,
        content=content,
        bbox=(0, 0, 100, 20),
    )


def test_formula_knowledge_update_upserts_accepted_latex_and_marks_done(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
        "block",
        [block],
        result_json_by_target={
            block.id: {
                "input_hash": "fusion-hash",
                "best_result_id": "r2-result",
                "fusion_version": "formula_candidate_fusion_v1",
                "accepted_latex": r"$$\alpha+\beta$$",
            }
        },
    )
    engine = _KnowledgeEngine(exists=True)
    service = FormulaKnowledgeUpdateService(store, engine)

    result = service.run_batch("doc-1", [block], limit=1)

    assert result.done == 1
    assert engine.upserts[0][0] == "doc-1"
    assert engine.upserts[0][1][0].content == r"$$\alpha+\beta$$"
    assert engine.upserts[0][1][0].metadata["formula_r5_accepted"] is True
    records = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    )
    assert records[0].status == "done"
    assert records[0].result_json["input_hash"] == "fusion-hash"
    assert records[0].result_json["content_hash"]


def test_formula_knowledge_update_defers_until_base_index_exists(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
        "block",
        [block],
        result_json_by_target={block.id: {"input_hash": "fusion-hash"}},
    )
    engine = _KnowledgeEngine(exists=False)
    service = FormulaKnowledgeUpdateService(store, engine)

    result = service.run_batch("doc-1", [block], limit=1)

    assert result.deferred == 1
    assert engine.upserts == []
    records = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    )
    assert records[0].status == "queued"


def test_formula_knowledge_update_skips_missing_formula_block(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
        "block",
        [block],
        result_json_by_target={block.id: {"input_hash": "fusion-hash"}},
    )
    service = FormulaKnowledgeUpdateService(store, _KnowledgeEngine(exists=True))

    result = service.run_batch("doc-1", [], limit=1)

    assert result.skipped == 1
    records = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    )
    assert records[0].status == "skipped"
    assert records[0].error == "missing_formula_block"

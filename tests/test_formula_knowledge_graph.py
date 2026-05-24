from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.app.formula_knowledge_graph import FormulaKnowledgeGraphService
from src.app.graph_index_store import GraphIndexStore
from src.core.models import BlockType, DocumentBlock


def _formula(block_id: str = "p0_b1", content: str = r"$$E=mc^2$$") -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        page_num=0,
        block_type=BlockType.FORMULA,
        content=content,
        bbox=(10, 20, 100, 50),
        section_title="Energy",
    )


def test_formula_knowledge_graph_records_r4_round_and_graph_artifact(tmp_path) -> None:
    formula_store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    graph_store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    block = _formula()
    service = FormulaKnowledgeGraphService(formula_store, graph_store)

    queued = service.enqueue_formula_blocks("paper.pdf", "doc-1", [block])
    result = service.run_batch("doc-1", "paper.pdf", [block], limit=1)

    assert queued == 1
    assert result.done == 1
    records = formula_store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_GRAPH,
    )
    assert len(records) == 1
    assert records[0].status == "done"
    assert records[0].result_json["input_hash"]
    assert records[0].result_json["model_version"] == "structural_v1"
    assert records[0].result_json["node_count"] >= 1
    assert graph_store.counts("doc-1") == {"done": 1}
    artifact = graph_store.artifacts("doc-1", block.id)[0]
    assert artifact["extractor"] == "structural_v1"
    assert any(node.get("type") == "formula" for node in artifact["nodes"])


def test_formula_knowledge_graph_records_candidate_only_artifact(tmp_path) -> None:
    formula_store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    graph_store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    block = _formula("p0_b0_inline_0", r"x_i").model_copy(
        update={
            "metadata": {
                "candidate_only": True,
                "source": "formula_fusion_graph_candidate",
                "fusion_decision": "needs_more_evidence",
                "fusion_input_hash": "fusion-hash",
            }
        },
        deep=True,
    )
    service = FormulaKnowledgeGraphService(formula_store, graph_store)

    queued = service.enqueue_fusion_candidates("paper.pdf", "doc-1", [block])
    result = service.run_batch("doc-1", "paper.pdf", [block], limit=1)

    assert queued == 1
    assert result.done == 1
    record = formula_store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_GRAPH,
    )[0]
    assert record.result_json["candidate_only"] is True
    assert record.result_json["fusion_decision"] == "needs_more_evidence"
    artifact = graph_store.artifacts("doc-1", block.id)[0]
    assert any(node.get("type") == "formula_candidate" for node in artifact["nodes"])


def test_formula_knowledge_graph_skips_same_input_after_done(tmp_path) -> None:
    formula_store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    graph_store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    block = _formula()
    service = FormulaKnowledgeGraphService(formula_store, graph_store)

    assert service.enqueue_formula_blocks("paper.pdf", "doc-1", [block]) == 1
    assert service.run_batch("doc-1", "paper.pdf", [block], limit=1).done == 1

    assert service.enqueue_formula_blocks("paper.pdf", "doc-1", [block]) == 0
    assert formula_store.round_counts("doc-1", FormulaScanRound.KNOWLEDGE_GRAPH) == {
        "r4_knowledge_graph:done": 1,
    }


def test_formula_knowledge_graph_requeues_when_formula_content_changes(tmp_path) -> None:
    formula_store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    service = FormulaKnowledgeGraphService(formula_store, GraphIndexStore(str(tmp_path / "graph_jobs.db")))
    block = _formula(content=r"$$old$$")

    service.enqueue_formula_blocks("paper.pdf", "doc-1", [block])
    service.run_batch("doc-1", "paper.pdf", [block], limit=1)

    changed = _formula(content=r"$$new$$")
    assert service.enqueue_formula_blocks("paper.pdf", "doc-1", [changed]) == 1
    assert service.pending_count("doc-1") == 1


def test_formula_knowledge_graph_skips_missing_formula_block(tmp_path) -> None:
    formula_store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    graph_store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    block = _formula()
    service = FormulaKnowledgeGraphService(formula_store, graph_store)
    service.enqueue_formula_blocks("paper.pdf", "doc-1", [block])

    result = service.run_batch("doc-1", "paper.pdf", [], limit=1)

    assert result.skipped == 1
    records = formula_store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_GRAPH,
    )
    assert records[0].status == "skipped"
    assert records[0].error == "missing_formula_block"

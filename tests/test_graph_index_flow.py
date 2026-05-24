from src.app.graph_index_flow import (
    GraphIndexFlow,
    StructuralGraphExtractor,
    run_graph_index_batch,
)
from src.app.graph_index_store import GraphIndexStore
from src.core.models import BlockType, DocumentBlock


def _block(
    block_id: str,
    block_type: BlockType,
    content: str,
    page_num: int = 0,
    section_title: str = "",
) -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        page_num=page_num,
        block_type=block_type,
        content=content,
        bbox=(0, 0, 100, 20),
        section_title=section_title,
    )


def test_structural_graph_extractor_records_formula_and_section_facts() -> None:
    block = _block(
        "p2_b4",
        BlockType.FORMULA,
        r"$$E = mc^2$$",
        page_num=2,
        section_title="Relativity",
    )
    result = StructuralGraphExtractor().extract("doc-1", block)

    node_types = {node["type"] for node in result.nodes}
    edge_types = {edge["type"] for edge in result.edges}

    assert {"document", "page", "block", "section", "formula"} <= node_types
    assert "expresses_formula" in edge_types
    assert any(node.get("latex") == r"$$E = mc^2$$" for node in result.nodes)


def test_structural_graph_extractor_marks_candidate_only_formula_facts() -> None:
    block = DocumentBlock(
        id="p0_b0_inline_0",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"x_i",
        bbox=(0, 0, 10, 10),
        metadata={
            "candidate_only": True,
            "source": "formula_fusion_graph_candidate",
            "fusion_decision": "needs_more_evidence",
            "fusion_input_hash": "fusion-hash",
        },
    )

    result = StructuralGraphExtractor().extract("doc-1", block)

    formula_nodes = [node for node in result.nodes if node.get("type") == "formula_candidate"]
    assert len(formula_nodes) == 1
    assert formula_nodes[0]["candidate_only"] is True
    assert formula_nodes[0]["fusion_input_hash"] == "fusion-hash"
    assert any(edge["type"] == "suggests_formula_candidate" for edge in result.edges)


def test_run_graph_index_batch_persists_artifacts(tmp_path) -> None:
    store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    blocks = [
        _block("p0_b0", BlockType.HEADING, "Attention", section_title="Attention"),
        _block("p0_b1", BlockType.PARAGRAPH, "Queries, keys, and values.", section_title="Attention"),
        _block("p0_b2", BlockType.FORMULA, r"$$\mathrm{softmax}(QK^T)V$$", section_title="Attention"),
    ]

    result = run_graph_index_batch(
        store,
        "paper.pdf",
        "doc-1",
        blocks,
        batch_budget=2,
    )

    assert result["queued"] == 3
    assert result["processed"] == 2
    assert result["pending"] == 1
    artifact = store.artifacts("doc-1", "p0_b0")[0]
    assert artifact["extractor"] == "structural_v1"
    assert artifact["nodes"]
    assert artifact["edges"]


def test_graph_index_flow_respects_disabled_default(tmp_path) -> None:
    store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    flow = GraphIndexFlow(store=store, enabled=False)

    started = flow.enqueue_document(
        "paper.pdf",
        "doc-1",
        [_block("p0_b0", BlockType.PARAGRAPH, "content")],
    )

    assert started is False
    assert store.counts("doc-1") == {}

import json

from tools import formula_acceptance_review

from src.app.formula_acceptance_review import FormulaAcceptanceReviewService
from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.app.formula_knowledge_update import FormulaKnowledgeUpdateService
from src.app.graph_index_store import GraphIndexStore
from src.core.models import BlockType, DocumentBlock


class _KnowledgeEngine:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists
        self.upserts: list[tuple[str, list[DocumentBlock]]] = []

    def check_exists(self, doc_hash: str) -> bool:
        return self.exists

    def upsert_blocks(self, blocks: list[DocumentBlock], doc_hash: str) -> None:
        self.upserts.append((doc_hash, [block.model_copy(deep=True) for block in blocks]))


class _FailingExtractor:
    name = "failing_extractor"

    def extract(self, doc_hash: str, block: DocumentBlock):  # noqa: ANN001
        raise RuntimeError("graph unavailable")


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


def test_acceptance_decision_switches_accepted_result_and_queues_r5(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    first_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="pdf_structure",
        model="pymupdf",
        input_hash="first-input",
        latex=r"\alpha+\beta",
        score=0.91,
        evidence={"page_num": 0, "bbox": list(block.bbox)},
    )
    second_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="local_precise",
        model="fake-tool",
        input_hash="second-input",
        latex=r"\gamma+\delta",
        score=0.98,
        evidence={"page_num": 0, "bbox": list(block.bbox)},
    )

    first_decision = store.accept_recognition_result(
        doc_hash="doc-1",
        result_id=first_id,
        filepath="paper.pdf",
        decision_source="test",
        reason="baseline",
    )
    second_decision = store.accept_recognition_result(
        doc_hash="doc-1",
        result_id=second_id,
        filepath="paper.pdf",
        decision_source="test",
        reason="revision",
    )

    accepted = store.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)
    assert [record.result_id for record in accepted] == [second_id]
    assert second_decision.previous_result_id == first_id
    decisions = store.list_acceptance_decisions("doc-1", candidate_id=block.id)
    assert [decision.result_id for decision in decisions] == [second_id, first_id]
    records = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    )
    assert len(records) == 1
    assert records[0].status == "queued"
    assert records[0].result_json["best_result_id"] == second_id
    assert records[0].result_json["accepted_latex"] == "$$\n\\gamma+\\delta\n$$"
    assert records[0].result_json["acceptance_decision_id"] == second_decision.decision_id
    assert first_decision.accepted_latex == "$$\n\\alpha+\\beta\n$$"


def test_acceptance_review_service_lists_accepts_and_rejects(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    result_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="local_precise",
        model="fake-tool",
        input_hash="image-input",
        latex=r"\alpha+\beta",
        score=0.99,
        evidence={"page_num": 0, "bbox": list(block.bbox)},
    )
    service = FormulaAcceptanceReviewService(store)

    listed = service.list_results("doc-1", limit=10)
    accepted = service.accept_result(
        "doc-1",
        result_id=result_id,
        filepath="paper.pdf",
        source="unit_test",
        reason="source aligned",
    )
    rejected = service.reject_result(
        "doc-1",
        result_id=result_id,
        source="unit_test",
        reason="manual rollback",
    )
    decisions = service.list_decisions("doc-1", candidate_id=block.id)

    assert listed["count"] == 1
    assert listed["results"][0]["result_id"] == result_id
    assert accepted["decision"]["action"] == "accept"
    assert rejected["decision"]["action"] == "reject"
    assert decisions["count"] == 2
    assert [item["action"] for item in decisions["decisions"]] == ["reject", "accept"]


def test_acceptance_review_service_revises_result_and_queues_r5(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    result_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="local_precise",
        model="fake-tool",
        input_hash="image-input",
        latex=r"\alpha+\beta",
        score=0.72,
        evidence={"page_num": 0, "bbox": list(block.bbox)},
    )
    service = FormulaAcceptanceReviewService(store)

    revised = service.revise_result(
        "doc-1",
        result_id=result_id,
        revised_latex=r"\alpha+\gamma",
        filepath="paper.pdf",
        source="unit_revision",
        reason="manual correction",
    )

    accepted = store.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)
    assert len(accepted) == 1
    assert accepted[0].result_id == revised["revision_result_id"]
    assert accepted[0].stage == "manual_revision"
    assert accepted[0].latex == r"\alpha+\gamma"
    decision = store.list_acceptance_decisions("doc-1", candidate_id=block.id)[0]
    assert decision.payload["manual_revision"] is True
    assert decision.payload["source_result_id"] == result_id
    records = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    )
    assert len(records) == 1
    assert records[0].result_json["accepted_latex"] == "$$\n\\alpha+\\gamma\n$$"


def test_acceptance_review_service_revises_fusion_and_queues_r5(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    fusion_id = store.put_fusion_record(
        doc_hash="doc-1",
        candidate_id=block.id,
        fusion_version="fusion-v1",
        input_hash="fusion-input",
        best_result_id="",
        ranked_result_ids=[],
        coverage=1.0,
        agreement_score=0.8,
        source_similarity=0.7,
        syntax_valid=True,
        risk_flags=["needs_revision"],
        accepted_gate={"passed": False},
        decision="needs_more_evidence",
        result_json={
            "best_latex": r"\theta+\phi",
            "ranked_candidates": [
                {
                    "latex": r"\theta+\phi",
                    "evidence": {"page_num": 0, "bbox": list(block.bbox)},
                }
            ],
        },
    )
    service = FormulaAcceptanceReviewService(store)

    revised = service.revise_fusion(
        "doc-1",
        fusion_id=fusion_id,
        revised_latex=r"\theta-\phi",
        filepath="paper.pdf",
        source="unit_revision_fusion",
        reason="manual correction",
    )

    accepted = store.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)
    assert accepted[0].result_id == revised["revision_result_id"]
    assert accepted[0].latex == r"\theta-\phi"
    assert accepted[0].warnings == ("needs_revision",)
    decision = store.list_acceptance_decisions("doc-1", candidate_id=block.id)[0]
    assert decision.payload["fusion_id"] == fusion_id
    assert decision.payload["manual_revision"] is True


def test_acceptance_r5_update_upserts_manual_decision_metadata(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    result_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="local_precise",
        model="fake-tool",
        input_hash="image-input",
        latex=r"\alpha+\beta",
        score=0.99,
        evidence={"page_num": 0, "bbox": list(block.bbox)},
    )
    decision = store.accept_recognition_result(
        doc_hash="doc-1",
        result_id=result_id,
        filepath="paper.pdf",
        decision_source="manual_cli",
        decider="tester",
        reason="matches source",
    )
    engine = _KnowledgeEngine(exists=True)
    service = FormulaKnowledgeUpdateService(store, engine)

    result = service.run_batch("doc-1", [block], limit=1)

    assert result.done == 1
    assert engine.upserts[0][1][0].content == "$$\n\\alpha+\\beta\n$$"
    metadata = engine.upserts[0][1][0].metadata
    assert metadata["formula_r5_accepted"] is True
    assert metadata["formula_r5_acceptance_decision_id"] == decision.decision_id
    assert metadata["formula_r5_acceptance_source"] == "manual_cli"


def test_acceptance_r5_update_syncs_graph_artifact(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    graph_store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
        "block",
        [block],
        result_json_by_target={
            block.id: {
                "input_hash": "manual-accept-input",
                "accepted_latex": r"$$\alpha+\beta$$",
                "acceptance_decision_id": "decision-1",
                "acceptance_source": "manual_cli",
            }
        },
    )
    engine = _KnowledgeEngine(exists=True)
    service = FormulaKnowledgeUpdateService(store, engine, graph_store=graph_store)

    result = service.run_batch("doc-1", [block], limit=1)

    assert result.done == 1
    assert result.graph_synced == 1
    artifact = graph_store.artifacts("doc-1", block.id)[0]
    assert artifact["extractor"] == "structural_v1"
    assert any(node.get("type") == "formula" for node in artifact["nodes"])
    assert not any(node.get("type") == "formula_candidate" for node in artifact["nodes"])
    record = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    )[0]
    assert record.result_json["graph_synced"] is True
    assert record.result_json["graph_artifact_key"]["block_id"] == block.id


def test_acceptance_r5_update_keeps_kb_upsert_when_graph_sync_fails(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    graph_store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    block = _formula()
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
        "block",
        [block],
        result_json_by_target={
            block.id: {
                "input_hash": "manual-accept-input",
                "accepted_latex": r"$$\alpha+\beta$$",
            }
        },
    )
    engine = _KnowledgeEngine(exists=True)
    service = FormulaKnowledgeUpdateService(
        store,
        engine,
        graph_store=graph_store,
        graph_extractor=_FailingExtractor(),
    )

    result = service.run_batch("doc-1", [block], limit=1)

    assert result.done == 1
    assert result.graph_failed == 1
    assert engine.upserts[0][1][0].content == r"$$\alpha+\beta$$"
    record = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    )[0]
    assert record.status == "done"
    assert record.result_json["graph_failed"] is True
    assert "graph unavailable" in record.result_json["graph_error"]


def test_formula_knowledge_update_rebuilds_missing_block_from_acceptance_payload(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula(block_id="fusion_candidate")
    store.enqueue_round_records(
        "doc-1",
        "paper.pdf",
        FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
        "block",
        [block],
        result_json_by_target={
            block.id: {
                "input_hash": "manual-accept-input",
                "best_result_id": "synthetic-result",
                "fusion_version": "manual_acceptance_v1",
                "accepted_latex": r"$$\theta+\phi$$",
                "candidate_id": block.id,
                "page_num": 3,
                "bbox": [10, 20, 110, 40],
                "acceptance_decision_id": "decision-1",
                "acceptance_source": "manual_cli_fusion",
            }
        },
    )
    engine = _KnowledgeEngine(exists=True)
    service = FormulaKnowledgeUpdateService(store, engine)

    result = service.run_batch("doc-1", [], limit=1)

    assert result.done == 1
    upserted = engine.upserts[0][1][0]
    assert upserted.id == "fusion_candidate"
    assert upserted.page_num == 3
    assert upserted.bbox == (10.0, 20.0, 110.0, 40.0)
    assert upserted.content == r"$$\theta+\phi$$"
    assert upserted.metadata["formula_r5_payload_block"] is True
    assert upserted.metadata["formula_r5_acceptance_decision_id"] == "decision-1"


def test_reject_recognition_result_clears_acceptance_without_r5(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    result_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="local_fast",
        model="pix2text",
        input_hash="image-input",
        latex=r"\alpha+\beta",
        accepted=True,
        evidence={"page_num": 0, "bbox": list(block.bbox)},
    )

    decision = store.reject_recognition_result(
        doc_hash="doc-1",
        result_id=result_id,
        decision_source="test",
        reason="bad crop",
    )

    assert decision.action == "reject"
    assert store.list_recognition_results("doc-1", candidate_id=block.id, accepted=True) == []
    assert store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    ) == []


def test_formula_acceptance_review_cli_lists_and_accepts(tmp_path, capsys) -> None:
    db_path = tmp_path / "formula_jobs.db"
    store = FormulaIndexStore(str(db_path))
    block = _formula()
    result_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="local_precise",
        model="fake-tool",
        input_hash="image-input",
        latex=r"\alpha+\beta",
        evidence={"page_num": 0, "bbox": list(block.bbox)},
    )
    store.close()

    rc = formula_acceptance_review.main(
        [
            "--db",
            str(db_path),
            "--doc-hash",
            "doc-1",
            "list",
            "--candidate-id",
            block.id,
        ]
    )
    assert rc == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["results"][0]["result_id"] == result_id

    rc = formula_acceptance_review.main(
        [
            "--db",
            str(db_path),
            "--doc-hash",
            "doc-1",
            "accept",
            "--result-id",
            result_id,
            "--filepath",
            "paper.pdf",
            "--reason",
            "manual pass",
        ]
    )
    assert rc == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["decision"]["action"] == "accept"

    check = FormulaIndexStore(str(db_path))
    assert check.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)[0].result_id == result_id
    assert check.round_pending_count("doc-1", FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE) == 1


def test_formula_acceptance_review_cli_revises_result(tmp_path, capsys) -> None:
    db_path = tmp_path / "formula_jobs.db"
    store = FormulaIndexStore(str(db_path))
    block = _formula()
    result_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="local_precise",
        model="fake-tool",
        input_hash="image-input",
        latex=r"\alpha+\beta",
        evidence={"page_num": 0, "bbox": list(block.bbox)},
    )
    store.close()

    rc = formula_acceptance_review.main(
        [
            "--db",
            str(db_path),
            "--doc-hash",
            "doc-1",
            "revise",
            "--result-id",
            result_id,
            "--latex",
            r"\alpha+\gamma",
            "--filepath",
            "paper.pdf",
            "--reason",
            "manual correction",
        ]
    )

    assert rc == 0
    revised = json.loads(capsys.readouterr().out)
    check = FormulaIndexStore(str(db_path))
    accepted = check.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)
    assert accepted[0].result_id == revised["revision_result_id"]
    assert accepted[0].stage == "manual_revision"
    assert accepted[0].latex == r"\alpha+\gamma"
    decision = check.list_acceptance_decisions("doc-1", candidate_id=block.id)[0]
    assert decision.payload["manual_revision"] is True
    assert decision.payload["source_result_id"] == result_id
    assert check.round_pending_count("doc-1", FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE) == 1


def test_accept_fusion_record_creates_audited_result_and_queues_r5(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    fusion_id = store.put_fusion_record(
        doc_hash="doc-1",
        candidate_id=block.id,
        fusion_version="fusion-test-v1",
        input_hash="fusion-input",
        best_result_id="synthetic-best",
        ranked_result_ids=["synthetic-best"],
        coverage=1.0,
        agreement_score=1.0,
        source_similarity=0.99,
        syntax_valid=True,
        accepted_gate={"passed": True, "reasons": []},
        decision="ready_for_manual_accept",
        result_json={
            "best_result_id": "synthetic-best",
            "best_latex": r"\theta+\phi",
            "ranked_candidates": [
                {
                    "result_id": "synthetic-best",
                    "stage": "parsed_blocks",
                    "model": "document_chunker",
                    "latex": r"\theta+\phi",
                }
            ],
        },
    )

    decision = store.accept_fusion_record(
        doc_hash="doc-1",
        fusion_id=fusion_id,
        filepath="paper.pdf",
        decision_source="test_fusion",
        reason="fusion gate passed",
    )

    assert decision.action == "accept"
    assert decision.candidate_id == block.id
    assert decision.payload["synthetic_from_fusion"] is True
    accepted = store.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)
    assert len(accepted) == 1
    assert accepted[0].stage == "manual_fusion_acceptance"
    assert accepted[0].latex == r"\theta+\phi"
    records = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    )
    assert records[0].result_json["best_result_id"] == accepted[0].result_id
    assert records[0].result_json["accepted_latex"] == "$$\n\\theta+\\phi\n$$"


def test_accept_fusion_record_requires_ready_decision_by_default(tmp_path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    fusion_id = store.put_fusion_record(
        doc_hash="doc-1",
        candidate_id=block.id,
        fusion_version="fusion-test-v1",
        input_hash="fusion-input",
        best_result_id="synthetic-best",
        decision="needs_more_evidence",
        result_json={"best_latex": r"\theta+\phi"},
    )

    try:
        store.accept_fusion_record(doc_hash="doc-1", fusion_id=fusion_id, filepath="paper.pdf")
    except ValueError as exc:
        assert "not ready" in str(exc)
    else:
        raise AssertionError("needs_more_evidence fusion should not be accepted by default")

    decision = store.accept_fusion_record(
        doc_hash="doc-1",
        fusion_id=fusion_id,
        filepath="paper.pdf",
        allow_not_ready=True,
        reason="explicit override",
    )
    assert decision.action == "accept"


def test_formula_acceptance_review_cli_ready_and_accept_fusion(tmp_path, capsys) -> None:
    db_path = tmp_path / "formula_jobs.db"
    store = FormulaIndexStore(str(db_path))
    block = _formula()
    fusion_id = store.put_fusion_record(
        doc_hash="doc-1",
        candidate_id=block.id,
        fusion_version="fusion-test-v1",
        input_hash="fusion-input",
        best_result_id="synthetic-best",
        ranked_result_ids=["synthetic-best"],
        source_similarity=0.99,
        decision="ready_for_manual_accept",
        result_json={
            "best_latex": r"\alpha+\beta",
            "ranked_candidates": [{"result_id": "synthetic-best", "latex": r"\alpha+\beta"}],
        },
    )
    store.close()

    rc = formula_acceptance_review.main(
        [
            "--db",
            str(db_path),
            "--doc-hash",
            "doc-1",
            "ready",
        ]
    )
    assert rc == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["fusion_records"][0]["fusion_id"] == fusion_id

    rc = formula_acceptance_review.main(
        [
            "--db",
            str(db_path),
            "--doc-hash",
            "doc-1",
            "accept-fusion",
            "--fusion-id",
            fusion_id,
            "--filepath",
            "paper.pdf",
            "--reason",
            "manual fusion pass",
        ]
    )
    assert rc == 0
    accepted = json.loads(capsys.readouterr().out)
    assert accepted["decision"]["action"] == "accept"

    check = FormulaIndexStore(str(db_path))
    assert check.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)
    assert check.round_pending_count("doc-1", FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE) == 1


def test_formula_acceptance_review_cli_revises_fusion(tmp_path, capsys) -> None:
    db_path = tmp_path / "formula_jobs.db"
    store = FormulaIndexStore(str(db_path))
    block = _formula()
    fusion_id = store.put_fusion_record(
        doc_hash="doc-1",
        candidate_id=block.id,
        fusion_version="fusion-test-v1",
        input_hash="fusion-input",
        best_result_id="synthetic-best",
        ranked_result_ids=["synthetic-best"],
        source_similarity=0.41,
        risk_flags=["needs_revision"],
        decision="needs_more_evidence",
        result_json={
            "best_latex": r"\alpha+\beta",
            "ranked_candidates": [
                {
                    "result_id": "synthetic-best",
                    "latex": r"\alpha+\beta",
                    "evidence": {"page_num": 0, "bbox": list(block.bbox)},
                }
            ],
        },
    )
    store.close()

    rc = formula_acceptance_review.main(
        [
            "--db",
            str(db_path),
            "--doc-hash",
            "doc-1",
            "revise-fusion",
            "--fusion-id",
            fusion_id,
            "--latex",
            r"\alpha-\beta",
            "--filepath",
            "paper.pdf",
            "--reason",
            "manual fusion correction",
        ]
    )

    assert rc == 0
    revised = json.loads(capsys.readouterr().out)
    check = FormulaIndexStore(str(db_path))
    accepted = check.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)
    assert accepted[0].result_id == revised["revision_result_id"]
    assert accepted[0].stage == "manual_revision"
    assert accepted[0].latex == r"\alpha-\beta"
    assert accepted[0].warnings == ("needs_revision",)
    decision = check.list_acceptance_decisions("doc-1", candidate_id=block.id)[0]
    assert decision.payload["fusion_id"] == fusion_id
    assert decision.payload["manual_revision"] is True
    assert check.round_pending_count("doc-1", FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE) == 1


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

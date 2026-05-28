from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.app.formula_acceptance_review import FormulaAcceptanceReviewService
from src.app.formula_index_store import FormulaIndexStore, FormulaScanRound
from src.core.models import BlockType, DocumentBlock
from src.ui.formula_acceptance_dialog import FormulaAcceptanceDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _formula() -> DocumentBlock:
    return DocumentBlock(
        id="p0_b1",
        page_num=0,
        block_type=BlockType.FORMULA,
        content=r"$$old$$",
        bbox=(0, 0, 10, 10),
    )


def test_formula_acceptance_dialog_accepts_and_rejects_result(tmp_path) -> None:
    _app()
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
    dialog = FormulaAcceptanceDialog(service, "doc-1", "paper.pdf")

    dialog._result_table.selectRow(0)
    dialog._reason.setPlainText("matches source")
    dialog._accept_selected_result()
    dialog.refresh()
    dialog._result_table.selectRow(0)
    dialog._reason.setPlainText("rollback")
    dialog._reject_selected_result()

    decisions = store.list_acceptance_decisions("doc-1", candidate_id=block.id)
    assert [decision.action for decision in decisions] == ["reject", "accept"]
    records = store.list_round_records(
        "doc-1",
        scan_round=FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE,
    )
    assert len(records) == 1
    assert records[0].result_json["best_result_id"] == result_id


def test_formula_acceptance_dialog_revises_result(tmp_path) -> None:
    _app()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    result_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="local_precise",
        model="fake-tool",
        input_hash="image-input",
        latex=r"\alpha+\beta",
        score=0.81,
        evidence={"page_num": 0, "bbox": list(block.bbox)},
    )
    service = FormulaAcceptanceReviewService(store)
    dialog = FormulaAcceptanceDialog(service, "doc-1", "paper.pdf")

    dialog._result_table.selectRow(0)
    dialog._revision_latex.setText(r"\alpha+\gamma")
    dialog._reason.setPlainText("manual correction")
    dialog._revise_selected_result()

    accepted = store.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)
    assert accepted[0].stage == "manual_revision"
    assert accepted[0].latex == r"\alpha+\gamma"
    decision = store.list_acceptance_decisions("doc-1", candidate_id=block.id)[0]
    assert decision.payload["manual_revision"] is True
    assert decision.payload["source_result_id"] == result_id
    assert store.round_pending_count("doc-1", FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE) == 1


def test_formula_acceptance_dialog_previews_and_locates_result_evidence(tmp_path) -> None:
    _app()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    result_id = store.put_recognition_result(
        doc_hash="doc-1",
        candidate_id=block.id,
        stage="local_precise",
        model="fake-tool",
        input_hash="image-input",
        latex=r"\alpha+\beta",
        evidence={"page_num": 0, "bbox": [1, 2, 30, 12]},
    )
    service = FormulaAcceptanceReviewService(store)
    dialog = FormulaAcceptanceDialog(service, "doc-1", "paper.pdf")
    located: list[tuple[int, object]] = []
    dialog.evidence_location_requested.connect(lambda page, bbox: located.append((page, bbox)))

    dialog._tabs.setCurrentWidget(dialog._result_table)
    dialog._result_table.selectRow(0)
    dialog._preview_selected_evidence()
    dialog._locate_selected_evidence()

    assert result_id in dialog._evidence.toPlainText()
    assert located == [(0, [1.0, 2.0, 30.0, 12.0])]


def test_formula_acceptance_dialog_accepts_ready_fusion(tmp_path) -> None:
    _app()
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
        agreement_score=1.0,
        source_similarity=0.99,
        syntax_valid=True,
        risk_flags=[],
        accepted_gate={"passed": True, "reasons": []},
        decision="ready_for_manual_accept",
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
    dialog = FormulaAcceptanceDialog(service, "doc-1", str(Path("paper.pdf")))

    dialog._ready_table.selectRow(0)
    dialog._reason.setPlainText("ready fusion")
    dialog._accept_selected_fusion()

    assert store.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)
    assert store.round_pending_count("doc-1", FormulaScanRound.KNOWLEDGE_INCREMENTAL_UPDATE) == 1
    assert store.list_acceptance_decisions("doc-1", candidate_id=block.id)[0].payload["fusion_id"] == fusion_id


def test_formula_acceptance_dialog_revises_not_ready_fusion(tmp_path) -> None:
    _app()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    fusion_id = store.put_fusion_record(
        doc_hash="doc-1",
        candidate_id=block.id,
        fusion_version="fusion-v1",
        input_hash="fusion-input",
        best_result_id="",
        ranked_result_ids=[],
        coverage=0.7,
        agreement_score=0.6,
        source_similarity=0.5,
        syntax_valid=False,
        risk_flags=["needs_more_evidence"],
        accepted_gate={"passed": False, "reasons": ["needs_more_evidence"]},
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
    dialog = FormulaAcceptanceDialog(service, "doc-1", str(Path("paper.pdf")))

    assert dialog._ready_table.rowCount() == 1
    dialog._ready_table.selectRow(0)
    dialog._revision_latex.setText(r"\theta-\phi")
    dialog._reason.setPlainText("manual fusion correction")
    dialog._revise_selected_fusion()

    accepted = store.list_recognition_results("doc-1", candidate_id=block.id, accepted=True)
    assert accepted[0].stage == "manual_revision"
    assert accepted[0].latex == r"\theta-\phi"
    assert accepted[0].warnings == ("needs_more_evidence",)
    decision = store.list_acceptance_decisions("doc-1", candidate_id=block.id)[0]
    assert decision.payload["fusion_id"] == fusion_id
    assert decision.payload["manual_revision"] is True


def test_formula_acceptance_dialog_previews_and_locates_fusion_evidence(tmp_path) -> None:
    _app()
    store = FormulaIndexStore(str(tmp_path / "formula_jobs.db"))
    block = _formula()
    fusion_id = store.put_fusion_record(
        doc_hash="doc-1",
        candidate_id=block.id,
        fusion_version="fusion-v1",
        input_hash="fusion-input",
        best_result_id="",
        ranked_result_ids=[],
        source_similarity=0.5,
        decision="needs_more_evidence",
        result_json={
            "best_latex": r"\theta+\phi",
            "ranked_candidates": [
                {
                    "latex": r"\theta+\phi",
                    "evidence": {"page_num": 0, "bbox": [4, 5, 40, 16]},
                }
            ],
        },
    )
    service = FormulaAcceptanceReviewService(store)
    dialog = FormulaAcceptanceDialog(service, "doc-1", str(Path("paper.pdf")))
    located: list[tuple[int, object]] = []
    dialog.evidence_location_requested.connect(lambda page, bbox: located.append((page, bbox)))

    dialog._tabs.setCurrentWidget(dialog._ready_table)
    dialog._ready_table.selectRow(0)
    dialog._preview_selected_evidence()
    dialog._locate_selected_evidence()

    assert fusion_id in dialog._evidence.toPlainText()
    assert located == [(0, [4.0, 5.0, 40.0, 16.0])]

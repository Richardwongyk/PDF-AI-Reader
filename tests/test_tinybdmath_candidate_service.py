from pathlib import Path

from src.app.formula_index_store import FormulaIndexStore
from src.app.tinybdmath_candidate_service import TINYBDMATH_PREPROCESS_VERSION, TinyBDMathCandidateService
from src.core.tinybdmath_edge_baseline import train_edge_baseline


def test_tinybdmath_candidate_service_writes_relation_evidence(tmp_path: Path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula.db"))
    store.put_recognition_result(
        doc_hash="doc",
        candidate_id="c1",
        stage="pdf_structure",
        model="pymupdf_born_digital_structure",
        model_version="v",
        preprocess_version="p",
        input_hash="r0",
        latex="ht",
        normalized_latex="ht",
        score=0.5,
        duration_ms=0,
        warnings=[],
        evidence={
            "details": {
                "text": "ht",
                "region": {"page_num": 1, "bbox": [0, 0, 20, 10]},
                "raw_glyph_graph": {"input_hash": "raw", "glyphs": []},
                "enriched_glyph_graph": {
                    "input_hash": "enriched",
                    "raw_input_hash": "raw",
                    "summary": {"unknown_after": 0, "repaired_count": 0},
                    "glyphs": [
                        _glyph("g0", "h", 0, 0, 8, 10, size=10),
                        _glyph("g1", "t", 8, 4, 11, 10, size=7),
                    ],
                },
            }
        },
        accepted=False,
    )
    model, _report = train_edge_baseline([
        _edge_sample("e1", "subscript_zone", "SUB"),
        _edge_sample("e2", "right_neighbor", "HORIZONTAL"),
        _edge_sample("e3", "far_context", "NO_RELATION"),
    ], epochs=2)
    edge_model_path = tmp_path / "edge_model.json"
    model.save(edge_model_path)

    summary = TinyBDMathCandidateService(store, edge_model_path=edge_model_path).process_doc("doc", filepath="fake.pdf")

    assert summary["processed"] == 1
    result = store.get_recognition_result(
        doc_hash="doc",
        candidate_id="c1",
        stage="tinybdmath_structural",
        model="tinybdmath",
        model_version="tinybdmath_feature_audit_no_model_v0",
        preprocess_version=TINYBDMATH_PREPROCESS_VERSION,
        input_hash=store.list_recognition_results("doc", stage="tinybdmath_structural")[0].input_hash,
    )
    assert result is not None
    assert result.accepted is False
    assert result.evidence["relation_scoring"]["model_version"] == model.version
    assert result.evidence["structural_candidate"]["candidate_only"] is True
    assert result.evidence["structural_candidate"]["selected_relations"]
    assert result.evidence["decoded_latex"]["candidate_only"] is True


def test_tinybdmath_candidate_service_processes_inline_pdf_evidence(tmp_path: Path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula.db"))
    model, _report = train_edge_baseline([
        _edge_sample("e1", "subscript_zone", "SUB"),
        _edge_sample("e2", "right_neighbor", "HORIZONTAL"),
        _edge_sample("e3", "far_context", "NO_RELATION"),
    ], epochs=2)
    edge_model_path = tmp_path / "edge_model.json"
    model.save(edge_model_path)

    summary = TinyBDMathCandidateService(store, edge_model_path=edge_model_path).process_inline_candidates(
        "doc",
        [
            {
                "candidate_id": "p0_b1_inline_0",
                "latex": "ht",
                "page_num": 1,
                "bbox": [0, 0, 12, 12],
                "inline_pdf_evidence": {
                    "has_script_size": True,
                    "bbox": [0, 0, 12, 12],
                    "spans": [
                        {"text": "h", "font": "CMMI10", "size": 10, "bbox": [0, 0, 8, 10]},
                        {"text": "t", "font": "CMMI7", "size": 7, "bbox": [8, 4, 11, 10]},
                    ],
                },
            }
        ],
        filepath="fake.pdf",
    )

    assert summary["processed"] == 1
    result = store.list_recognition_results("doc", candidate_id="p0_b1_inline_0", stage="tinybdmath_structural")[0]
    assert result.evidence["source"] == "tinybdmath_r2a_inline_structural_candidate"
    assert result.evidence["inline_pdf_evidence"]["has_script_size"] is True
    assert result.evidence["relation_scoring"]["relation_scores"]
    assert result.evidence["structural_candidate"]["candidate_only"] is True
    assert result.evidence["decoded_latex"]["candidate_only"] is True


def test_tinybdmath_candidate_service_carries_vector_rule_nodes(tmp_path: Path) -> None:
    store = FormulaIndexStore(str(tmp_path / "formula.db"))
    store.put_recognition_result(
        doc_hash="doc",
        candidate_id="frac1",
        stage="pdf_structure",
        model="pymupdf_born_digital_structure",
        model_version="v",
        preprocess_version="p",
        input_hash="r0",
        latex="ab",
        normalized_latex="ab",
        score=0.5,
        duration_ms=0,
        warnings=[],
        evidence={
            "details": {
                "text": "ab",
                "region": {"page_num": 1, "bbox": [0, 0, 30, 30]},
                "raw_glyph_graph": {
                    "input_hash": "raw",
                    "glyphs": [],
                    "vectors": [{"node_id": "v0", "kind": "vector", "page_num": 1, "bbox": [0, 10, 24, 10.4]}],
                },
                "enriched_glyph_graph": {
                    "input_hash": "enriched",
                    "raw_input_hash": "raw",
                    "summary": {"unknown_after": 0, "repaired_count": 0},
                    "glyphs": [
                        _glyph("g0", "a", 4, 1, 9, 8, size=10),
                        _glyph("g1", "b", 4, 14, 9, 22, size=10),
                    ],
                },
            }
        },
        accepted=False,
    )
    model, _report = train_edge_baseline([
        _edge_sample("e1", "above_rule_candidate", "ABOVE"),
        _edge_sample("e2", "below_rule_candidate", "BELOW"),
        _edge_sample("e3", "fraction_bar_candidate", "FRACTION_BAR"),
        _edge_sample("e4", "far_context", "NO_RELATION"),
    ], epochs=2)
    edge_model_path = tmp_path / "edge_model.json"
    model.save(edge_model_path)

    summary = TinyBDMathCandidateService(store, edge_model_path=edge_model_path).process_doc("doc", filepath="fake.pdf")

    assert summary["processed"] == 1
    result = store.list_recognition_results("doc", candidate_id="frac1", stage="tinybdmath_structural")[0]
    assert result.evidence["vectors"]
    assert any(edge["hint"] == "fraction_bar_candidate" for edge in result.evidence["candidate_edges"])
    assert result.evidence["decoded_latex"]["latex"]


def _glyph(node_id: str, text: str, x0: float, y0: float, x1: float, y1: float, *, size: float) -> dict:
    return {
        "node_id": node_id,
        "raw": {
            "node_id": node_id,
            "text": text,
            "bbox": [x0, y0, x1, y1],
            "font": "CMMI10",
            "normalized_font": "CMMI10",
            "size": size,
            "page_num": 1,
            "is_unknown": False,
        },
        "resolved_identity": {
            "unicode": text,
            "latex": text,
            "source": "pdf_unicode",
            "confidence": 1.0,
        },
    }


def _edge_sample(edge_id: str, hint: str, label: str) -> dict:
    return {
        "row_id": "r",
        "edge_id": edge_id,
        "hint": hint,
        "label": label,
        "features": {
            "dx_over_height": 0.4,
            "dy_over_height": 0.5 if hint == "subscript_zone" else 0.0,
            "x_overlap": 0.0,
            "y_overlap": 0.5,
            "size_ratio": 0.8,
            "same_font": 1,
        },
    }

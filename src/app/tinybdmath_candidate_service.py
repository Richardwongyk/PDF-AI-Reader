"""TinyBDMath r2a structural candidate service.

This service consumes r0/r0.5 born-digital evidence already persisted in
``formula_recognition_results`` and writes TinyBDMath structural candidates back to
the same candidate table.  It is non-visual, candidate-only, and cacheable by
input hash/model version.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from src.app.formula_index_store import FormulaIndexStore
from src.core.models import BlockType, DocumentBlock
from src.core.tinybdmath_features import TINYBDMATH_FEATURE_SCHEMA_VERSION, TinyBDFeatureExtractor
from src.core.tinybdmath_graph_parser import TinyBDGraphParser, graph_parser_predictions_to_structural_candidate
from src.core.tinybdmath_latex_decoder import decode_latex_candidate


TINYBDMATH_R2A_ROUND = "r2a_tinybdmath_structural"
TINYBDMATH_STRUCTURAL_STAGE = "tinybdmath_structural"
TINYBDMATH_PREPROCESS_VERSION = "tinybdmath_feature_row_from_r0_evidence_v3_graph_parser"
TINYBDMATH_GRAPH_PARSER_REQUIRED_VERSION = "tinybdmath_graph_parser_required_v1"


class TinyBDMathCandidateService:
    """Create TinyBDMath r2a candidate evidence from born-digital PDF facts."""

    def __init__(
        self,
        store: FormulaIndexStore,
        *,
        graph_parser_model_path: Path | None = None,
    ) -> None:
        self._store = store
        self._graph_parser_model_path = graph_parser_model_path
        self._feature_extractor = TinyBDFeatureExtractor()
        self._graph_parser = (
            TinyBDGraphParser.load(graph_parser_model_path)
            if graph_parser_model_path is not None and graph_parser_model_path.exists()
            else None
        )

    def process_doc(self, doc_hash: str, *, filepath: str = "", limit: int = 64) -> dict[str, Any]:
        started = time.perf_counter()
        processed = 0
        skipped_cached = 0
        skipped_no_evidence = 0
        failed = 0
        records = self._store.list_recognition_results(
            doc_hash,
            stage="pdf_structure",
            limit=max(1, int(limit)),
        )
        for record in records:
            try:
                payload = self._candidate_payload(record.evidence)
                if payload is None:
                    skipped_no_evidence += 1
                    continue
                input_hash = _json_hash(
                    {
                        "source_input_hash": record.input_hash,
                        "feature_hash": payload["feature_graph_hash"],
                        "model_version": self.model_version,
                        "graph_parser_model_version": self.graph_parser_model_version,
                        "preprocess_version": TINYBDMATH_PREPROCESS_VERSION,
                    }
                )
                cached = self._store.get_recognition_result(
                    doc_hash=doc_hash,
                    candidate_id=record.candidate_id,
                    stage=TINYBDMATH_STRUCTURAL_STAGE,
                    model="tinybdmath",
                    model_version=self.model_version,
                    preprocess_version=TINYBDMATH_PREPROCESS_VERSION,
                    input_hash=input_hash,
                )
                if cached is not None:
                    skipped_cached += 1
                    continue
                result = self._score_payload(record.candidate_id, payload)
                decoded = result.get("decoded_latex", {})
                warnings = list(result["warnings"])
                if self._graph_parser is None:
                    warnings.append("tinybdmath_graph_parser_model_missing")
                self._store.put_recognition_result(
                    doc_hash=doc_hash,
                    candidate_id=record.candidate_id,
                    stage=TINYBDMATH_STRUCTURAL_STAGE,
                    model="tinybdmath",
                    model_version=self.model_version,
                    preprocess_version=TINYBDMATH_PREPROCESS_VERSION,
                    input_hash=input_hash,
                    latex=record.latex,
                    normalized_latex=record.normalized_latex or record.latex,
                    score=result["score"],
                    duration_ms=0,
                    warnings=warnings,
                    evidence={
                        "source": "tinybdmath_r2a_structural_candidate",
                        "source_stage": record.stage,
                        "source_result_id": record.result_id,
                        "source_input_hash": record.input_hash,
                        "feature_schema_version": TINYBDMATH_FEATURE_SCHEMA_VERSION,
                        **payload,
                        "graph_parser": result["graph_parser"],
                        "relation_scoring": result["relation_scoring"],
                        "structural_candidate": result["structural_candidate"],
                        "decoded_latex": decoded,
                        "candidate_only": True,
                        "notes": [
                            "TinyBDMath r2a uses born-digital PDF structure evidence only.",
                            "This result does not overwrite text, RAG, or accepted formula content.",
                        ],
                    },
                    accepted=False,
                )
                self._persist_round_done(
                    doc_hash=doc_hash,
                    filepath=filepath,
                    candidate_id=record.candidate_id,
                    payload=payload,
                    input_hash=input_hash,
                    result=result,
                    warnings=warnings,
                )
                processed += 1
            except Exception:
                failed += 1
        return {
            "stage": TINYBDMATH_STRUCTURAL_STAGE,
            "model": "tinybdmath",
            "model_version": self.model_version,
            "graph_parser_model_version": self.graph_parser_model_version,
            "preprocess_version": TINYBDMATH_PREPROCESS_VERSION,
            "records_seen": len(records),
            "processed": processed,
            "skipped_cached": skipped_cached,
            "skipped_no_evidence": skipped_no_evidence,
            "failed": failed,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    def process_inline_candidates(
        self,
        doc_hash: str,
        inline_candidates: list[dict[str, Any]],
        *,
        filepath: str = "",
        limit: int = 64,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        processed = 0
        skipped_cached = 0
        skipped_no_evidence = 0
        failed = 0
        for item in inline_candidates[: max(0, int(limit))]:
            try:
                candidate_id = str(item.get("candidate_id", "") or "")
                if not candidate_id:
                    skipped_no_evidence += 1
                    continue
                payload = self._inline_candidate_payload(item)
                if payload is None:
                    skipped_no_evidence += 1
                    continue
                input_hash = _json_hash(
                    {
                        "candidate_id": candidate_id,
                        "source_input_hash": payload["source_input_hash"],
                        "feature_hash": payload["feature_graph_hash"],
                        "model_version": self.model_version,
                        "graph_parser_model_version": self.graph_parser_model_version,
                        "preprocess_version": TINYBDMATH_PREPROCESS_VERSION,
                    }
                )
                cached = self._store.get_recognition_result(
                    doc_hash=doc_hash,
                    candidate_id=candidate_id,
                    stage=TINYBDMATH_STRUCTURAL_STAGE,
                    model="tinybdmath",
                    model_version=self.model_version,
                    preprocess_version=TINYBDMATH_PREPROCESS_VERSION,
                    input_hash=input_hash,
                )
                if cached is not None:
                    skipped_cached += 1
                    continue
                result = self._score_payload(candidate_id, payload)
                decoded = result.get("decoded_latex", {})
                warnings = list(result["warnings"])
                if self._graph_parser is None:
                    warnings.append("tinybdmath_graph_parser_model_missing")
                latex = str(item.get("latex", "") or "")
                self._store.put_recognition_result(
                    doc_hash=doc_hash,
                    candidate_id=candidate_id,
                    stage=TINYBDMATH_STRUCTURAL_STAGE,
                    model="tinybdmath",
                    model_version=self.model_version,
                    preprocess_version=TINYBDMATH_PREPROCESS_VERSION,
                    input_hash=input_hash,
                    latex=latex,
                    normalized_latex=latex,
                    score=result["score"],
                    duration_ms=0,
                    warnings=warnings,
                    evidence={
                        "source": "tinybdmath_r2a_inline_structural_candidate",
                        "source_stage": "inline_spans",
                        "feature_schema_version": TINYBDMATH_FEATURE_SCHEMA_VERSION,
                        **payload,
                        "graph_parser": result["graph_parser"],
                        "relation_scoring": result["relation_scoring"],
                        "structural_candidate": result["structural_candidate"],
                        "decoded_latex": decoded,
                        "candidate_only": True,
                        "notes": [
                            "TinyBDMath inline r2a uses born-digital PDF span/font/bbox evidence only.",
                            "This result does not overwrite text, RAG, or accepted formula content.",
                        ],
                    },
                    accepted=False,
                )
                self._persist_round_done(
                    doc_hash=doc_hash,
                    filepath=filepath,
                    candidate_id=candidate_id,
                    payload=payload,
                    input_hash=input_hash,
                    result=result,
                    warnings=warnings,
                )
                processed += 1
            except Exception:
                failed += 1
        return {
            "stage": TINYBDMATH_STRUCTURAL_STAGE,
            "model": "tinybdmath",
            "model_version": self.model_version,
            "graph_parser_model_version": self.graph_parser_model_version,
            "preprocess_version": TINYBDMATH_PREPROCESS_VERSION,
            "records_seen": len(inline_candidates[: max(0, int(limit))]),
            "processed": processed,
            "skipped_cached": skipped_cached,
            "skipped_no_evidence": skipped_no_evidence,
            "failed": failed,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    @property
    def model_version(self) -> str:
        if self._graph_parser is None:
            return TINYBDMATH_GRAPH_PARSER_REQUIRED_VERSION
        return self._graph_parser.artifact.model_version

    @property
    def graph_parser_model_version(self) -> str:
        if self._graph_parser is None:
            return "tinybdmath_no_graph_parser_model_v0"
        return self._graph_parser.artifact.model_version

    def _candidate_payload(self, evidence: dict[str, object]) -> dict[str, Any] | None:
        details = evidence.get("details", {})
        if not isinstance(details, dict):
            return None
        enriched = details.get("enriched_glyph_graph", {})
        raw = details.get("raw_glyph_graph", {})
        if not isinstance(enriched, dict) or not isinstance(raw, dict):
            return None
        glyphs = enriched.get("glyphs", [])
        vectors = raw.get("vectors", [])
        if not isinstance(glyphs, list):
            return None
        if not isinstance(vectors, list):
            vectors = []
        enriched_hash = str(enriched.get("input_hash", "") or "")
        feature_graph = self._feature_extractor.extract_from_enriched_json(
            enriched_input_hash=enriched_hash,
            glyphs_json=glyphs,
            vectors_json=vectors,
        )
        summary = enriched.get("summary", {})
        if not isinstance(summary, dict):
            summary = {}
        edge_counts = _edge_hint_counts(feature_graph)
        glyph_count = len(feature_graph.glyphs)
        edge_count = len(feature_graph.edges)
        structural_signal = (
            edge_counts.get("subscript_zone", 0)
            + edge_counts.get("superscript_zone", 0)
            + edge_counts.get("above_zone", 0)
            + edge_counts.get("below_zone", 0)
            + edge_counts.get("overlap_zone", 0)
        )
        unknown_after = _int(summary.get("unknown_after"))
        return {
            "feature_graph_hash": feature_graph.input_hash,
            "enriched_input_hash": enriched_hash,
            "raw_input_hash": str(enriched.get("raw_input_hash", "") or raw.get("input_hash", "") or ""),
            "glyph_count": glyph_count,
            "edge_count": edge_count,
            "edge_hint_counts": edge_counts,
            "candidate_edges": [edge.to_json() if hasattr(edge, "to_json") else _edge_to_json(edge) for edge in feature_graph.edges],
            "glyphs": [_glyph_to_json(glyph) for glyph in feature_graph.glyphs],
            "vectors": [_vector_to_json(vector) for vector in feature_graph.vectors],
            "feature_density": round(edge_count / glyph_count, 6) if glyph_count else 0.0,
            "structural_signal_count": structural_signal,
            "unknown_glyph_rate": round(unknown_after / glyph_count, 6) if glyph_count else 0.0,
            "repaired_count": _int(summary.get("repaired_count")),
            "pdf_text": _pdf_text_from_details(details, raw),
            "feature_graph_warnings": list(feature_graph.warnings),
            "enriched_summary": summary,
            "r0_region": details.get("region", {}) if isinstance(details.get("region"), dict) else {},
            "diagnostics": details.get("diagnostics", {}) if isinstance(details.get("diagnostics"), dict) else {},
        }

    def _inline_candidate_payload(self, item: dict[str, Any]) -> dict[str, Any] | None:
        evidence = item.get("inline_pdf_evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        spans = evidence.get("spans", [])
        if not isinstance(spans, list) or not spans:
            return None
        glyphs_json = _inline_spans_to_enriched_glyphs(spans, page_num=_int(item.get("page_num")))
        if not glyphs_json:
            return None
        source_input_hash = _json_hash(
            {
                "candidate_id": str(item.get("candidate_id", "") or ""),
                "latex": str(item.get("latex", "") or ""),
                "page_num": _int(item.get("page_num")),
                "bbox": item.get("bbox"),
                "inline_pdf_evidence": evidence,
            }
        )
        feature_graph = self._feature_extractor.extract_from_enriched_json(
            enriched_input_hash=source_input_hash,
            glyphs_json=glyphs_json,
        )
        edge_counts = _edge_hint_counts(feature_graph)
        glyph_count = len(feature_graph.glyphs)
        edge_count = len(feature_graph.edges)
        structural_signal = (
            edge_counts.get("subscript_zone", 0)
            + edge_counts.get("superscript_zone", 0)
            + edge_counts.get("above_zone", 0)
            + edge_counts.get("below_zone", 0)
            + edge_counts.get("overlap_zone", 0)
        )
        return {
            "source_input_hash": source_input_hash,
            "feature_graph_hash": feature_graph.input_hash,
            "enriched_input_hash": source_input_hash,
            "raw_input_hash": source_input_hash,
            "glyph_count": glyph_count,
            "edge_count": edge_count,
            "edge_hint_counts": edge_counts,
            "candidate_edges": [_edge_to_json(edge) for edge in feature_graph.edges],
            "glyphs": [_glyph_to_json(glyph) for glyph in feature_graph.glyphs],
            "vectors": [_vector_to_json(vector) for vector in feature_graph.vectors],
            "feature_density": round(edge_count / glyph_count, 6) if glyph_count else 0.0,
            "structural_signal_count": structural_signal,
            "unknown_glyph_rate": 0.0,
            "repaired_count": 0,
            "pdf_text": "".join(str(span.get("text", "") or "") for span in spans if isinstance(span, dict)).strip(),
            "feature_graph_warnings": list(feature_graph.warnings),
            "enriched_summary": {
                "source": "inline_pdf_evidence",
                "spans": len(spans),
                "has_script_size": bool(evidence.get("has_script_size")),
            },
            "r0_region": {
                "page_num": _int(item.get("page_num")),
                "bbox": item.get("bbox") if isinstance(item.get("bbox"), list) else evidence.get("bbox", []),
            },
            "diagnostics": {"source_stage": "inline_spans"},
            "inline_pdf_evidence": evidence,
        }

    def _score_payload(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        warnings = []
        if payload["unknown_glyph_rate"] > 0:
            warnings.append("unknown_glyphs_remaining")
        if payload["edge_count"] <= 0:
            warnings.append("empty_or_edge_less_feature_graph")
        graph_parser = self._graph_parser_payload(candidate_id, payload)
        structural_candidate = graph_parser_predictions_to_structural_candidate(graph_parser)
        relation_scoring = self._relation_payload(graph_parser, structural_candidate)
        decoded_latex = decode_latex_candidate(
            list(payload.get("glyphs", [])),
            structural_candidate,
            vectors=list(payload.get("vectors", [])),
            fallback_text=str(payload.get("pdf_text", "") or ""),
        ).to_json()
        return {
            "score": float(
                decoded_latex.get("layout_confidence")
                or decoded_latex.get("confidence")
                or 0.0
            ),
            "warnings": warnings + list(structural_candidate.get("verifier_warnings", [])) + list(decoded_latex.get("warnings", [])),
            "graph_parser": graph_parser,
            "relation_scoring": relation_scoring,
            "structural_candidate": structural_candidate,
            "decoded_latex": decoded_latex,
        }

    def _relation_payload(self, predictions: dict[str, Any], structural: dict[str, Any]) -> dict[str, Any]:
        return {
            "candidate_only": True,
            "model_version": str(predictions.get("model_version", "") or self.graph_parser_model_version),
            "source": "tinybdmath_graph_parser_m1",
            "relation_scores": [],
            "graph_parser_predictions": predictions.get("predictions", []),
            "verifier_warnings": list(structural.get("verifier_warnings", [])),
        }

    def _graph_parser_payload(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self._graph_parser is None:
            return {
                "candidate_only": True,
                "model_version": "tinybdmath_no_graph_parser_model_v0",
                "predictions": [],
                "warnings": ["tinybdmath_graph_parser_model_missing"],
            }
        graph_row = {
            "row_id": candidate_id,
            "case": "runtime",
            "kind": "candidate",
            "page_num": _int((payload.get("r0_region") or {}).get("page_num") if isinstance(payload.get("r0_region"), dict) else 0),
            "input_hash": str(payload.get("feature_graph_hash", "") or ""),
            "glyph_nodes": payload.get("glyphs", []),
            "vector_nodes": payload.get("vectors", []),
        }
        return self._graph_parser.predict_row(graph_row)

    def _persist_round_done(
        self,
        *,
        doc_hash: str,
        filepath: str,
        candidate_id: str,
        payload: dict[str, Any],
        input_hash: str,
        result: dict[str, Any],
        warnings: list[str],
    ) -> None:
        if not filepath:
            return
        try:
            block = DocumentBlock(
                id=candidate_id,
                page_num=_int((payload.get("r0_region") or {}).get("page_num") if isinstance(payload.get("r0_region"), dict) else 0),
                block_type=BlockType.FORMULA,
                content=str(payload.get("pdf_text", "") or ""),
                bbox=_bbox_from_payload(payload),
                metadata={
                    "source": "tinybdmath_r2a_structural_candidate",
                    "candidate_only": True,
                },
            )
        except Exception:
            return
        round_payload: dict[str, object] = {
            "stage": TINYBDMATH_STRUCTURAL_STAGE,
            "scan_round": TINYBDMATH_R2A_ROUND,
            "input_hash": input_hash,
            "model": "tinybdmath",
            "model_version": self.model_version,
            "preprocess_version": TINYBDMATH_PREPROCESS_VERSION,
            "candidate_id": candidate_id,
            "feature_graph_hash": str(payload.get("feature_graph_hash", "") or ""),
            "graph_parser": result.get("graph_parser", {}),
            "relation_scoring": result.get("relation_scoring", {}),
            "structural_candidate": result.get("structural_candidate", {}),
            "decoded_latex": result.get("decoded_latex", {}),
            "warnings": warnings,
            "candidate_only": True,
        }
        self._store.enqueue_round_records(
            doc_hash,
            filepath,
            TINYBDMATH_R2A_ROUND,
            "block",
            [block],
            result_json_by_target={candidate_id: round_payload},
        )
        self._store.mark_round_done(
            doc_hash,
            TINYBDMATH_R2A_ROUND,
            "block",
            candidate_id,
            round_payload,
        )


def _edge_hint_counts(feature_graph: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in getattr(feature_graph, "edges", ()):
        hint = str(getattr(edge, "hint", "") or "")
        if hint:
            counts[hint] = counts.get(hint, 0) + 1
    return dict(sorted(counts.items()))


def _edge_to_json(edge: Any) -> dict[str, Any]:
    return {
        "edge_id": str(getattr(edge, "edge_id", "") or ""),
        "source": str(getattr(edge, "source", "") or ""),
        "target": str(getattr(edge, "target", "") or ""),
        "hint": str(getattr(edge, "hint", "") or ""),
        "features": dict(getattr(edge, "features", {}) or {}),
    }


def _glyph_to_json(glyph: Any) -> dict[str, Any]:
    return {
        "node_id": str(getattr(glyph, "node_id", "") or ""),
        "unicode": str(getattr(glyph, "unicode", "") or ""),
        "latex": str(getattr(glyph, "latex", "") or ""),
        "font": str(getattr(glyph, "font", "") or ""),
        "size": _float(getattr(glyph, "size", 0.0)),
        "bbox": [float(value) for value in getattr(glyph, "bbox", (0.0, 0.0, 0.0, 0.0))],
        "page_num": _int(getattr(glyph, "page_num", 0)),
        "identity_source": str(getattr(glyph, "identity_source", "") or ""),
        "identity_confidence": _float(getattr(glyph, "identity_confidence", 0.0)),
    }


def _vector_to_json(vector: Any) -> dict[str, Any]:
    return {
        "node_id": str(getattr(vector, "node_id", "") or ""),
        "node_type": str(getattr(vector, "node_type", "") or "vector"),
        "vector_type": str(getattr(vector, "vector_type", "") or "vector"),
        "bbox": [float(value) for value in getattr(vector, "bbox", (0.0, 0.0, 0.0, 0.0))],
        "center": [float(value) for value in getattr(vector, "center", (0.0, 0.0))],
        "width": _float(getattr(vector, "width", 0.0)),
        "height": _float(getattr(vector, "height", 0.0)),
        "aspect_ratio": _float(getattr(vector, "aspect_ratio", 0.0)),
        "page_num": _int(getattr(vector, "page_num", 0)),
        "is_horizontal_rule_candidate": bool(getattr(vector, "is_horizontal_rule_candidate", False)),
        "is_vertical_rule_candidate": bool(getattr(vector, "is_vertical_rule_candidate", False)),
    }


def _inline_spans_to_enriched_glyphs(spans: list[Any], *, page_num: int) -> list[dict[str, Any]]:
    glyphs: list[dict[str, Any]] = []
    for index, span in enumerate(spans):
        if not isinstance(span, dict):
            continue
        bbox = span.get("bbox", [])
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            continue
        text = str(span.get("text", "") or "").strip()
        if not text:
            continue
        node_id = f"ig{index:04d}"
        glyphs.append(
            {
                "node_id": node_id,
                "raw": {
                    "node_id": node_id,
                    "text": text,
                    "bbox": [float(value) for value in bbox],
                    "font": str(span.get("font", "") or ""),
                    "normalized_font": str(span.get("font", "") or ""),
                    "size": _float(span.get("size")),
                    "page_num": page_num,
                    "is_unknown": False,
                },
                "resolved_identity": {
                    "unicode": text,
                    "latex": text,
                    "source": "inline_pdf_text",
                    "confidence": 0.95,
                },
            }
        )
    return glyphs


def _pdf_text_from_details(details: dict[str, object], raw: dict[str, object]) -> str:
    text = str(details.get("text", "") or "")
    if text:
        return text
    glyphs = raw.get("glyphs", [])
    if not isinstance(glyphs, list):
        return ""
    return "".join(str(item.get("text", "") or "") for item in glyphs if isinstance(item, dict))


def _bbox_from_payload(payload: dict[str, Any]) -> tuple[float, float, float, float]:
    region = payload.get("r0_region", {})
    bbox = region.get("bbox", ()) if isinstance(region, dict) else ()
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return (0.0, 0.0, 0.0, 0.0)
    try:
        return tuple(float(value) for value in bbox)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0, 0.0)


def _json_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8",
        errors="ignore",
    )
    return hashlib.sha256(encoded).hexdigest()


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

"""TinyBDMath r2a structural candidate service.

This service consumes r0/r0.5 born-digital evidence already persisted in
``formula_recognition_results`` and writes TinyBDMath quality candidates back to
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
from src.core.tinybdmath_scorer import TinyBDCandidateQualityScorer


TINYBDMATH_R2A_ROUND = "r2a_tinybdmath_structural"
TINYBDMATH_STRUCTURAL_STAGE = "tinybdmath_structural"
TINYBDMATH_PREPROCESS_VERSION = "tinybdmath_feature_row_from_r0_evidence_v1"
TINYBDMATH_NO_MODEL_VERSION = "tinybdmath_feature_audit_no_model_v0"


class TinyBDMathCandidateService:
    """Create TinyBDMath r2a candidate evidence from born-digital PDF facts."""

    def __init__(
        self,
        store: FormulaIndexStore,
        *,
        model_path: Path | None = None,
    ) -> None:
        self._store = store
        self._model_path = model_path
        self._feature_extractor = TinyBDFeatureExtractor()
        self._scorer = (
            TinyBDCandidateQualityScorer.from_model_path(model_path)
            if model_path is not None and model_path.exists()
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
                warnings = list(result["warnings"])
                if self._scorer is None:
                    warnings.append("tinybdmath_model_missing_feature_audit_only")
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
                        "quality": result["quality"],
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
            "preprocess_version": TINYBDMATH_PREPROCESS_VERSION,
            "records_seen": len(records),
            "processed": processed,
            "skipped_cached": skipped_cached,
            "skipped_no_evidence": skipped_no_evidence,
            "failed": failed,
            "elapsed_ms": int((time.perf_counter() - started) * 1000),
        }

    @property
    def model_version(self) -> str:
        if self._scorer is None:
            return TINYBDMATH_NO_MODEL_VERSION
        return self._scorer.model.model_version

    def _candidate_payload(self, evidence: dict[str, object]) -> dict[str, Any] | None:
        details = evidence.get("details", {})
        if not isinstance(details, dict):
            return None
        enriched = details.get("enriched_glyph_graph", {})
        raw = details.get("raw_glyph_graph", {})
        if not isinstance(enriched, dict) or not isinstance(raw, dict):
            return None
        glyphs = enriched.get("glyphs", [])
        if not isinstance(glyphs, list):
            return None
        enriched_hash = str(enriched.get("input_hash", "") or "")
        feature_graph = self._feature_extractor.extract_from_enriched_json(
            enriched_input_hash=enriched_hash,
            glyphs_json=glyphs,
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

    def _score_payload(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = {
            "candidate_id": candidate_id,
            "glyph_count": payload["glyph_count"],
            "edge_count": payload["edge_count"],
            "edge_hint_counts": payload["edge_hint_counts"],
            "feature_density": payload["feature_density"],
            "structural_signal_count": payload["structural_signal_count"],
            "unknown_glyph_rate": payload["unknown_glyph_rate"],
            "repaired_count": payload["repaired_count"],
            "pdf_text": payload["pdf_text"],
        }
        if self._scorer is None:
            warnings = []
            if payload["unknown_glyph_rate"] > 0:
                warnings.append("unknown_glyphs_remaining")
            if payload["edge_count"] <= 0:
                warnings.append("empty_or_edge_less_feature_graph")
            return {
                "score": None,
                "warnings": warnings,
                "quality": {
                    "candidate_id": candidate_id,
                    "model_version": TINYBDMATH_NO_MODEL_VERSION,
                    "predicted_label": "feature_audit_only",
                    "confidence": 0.0,
                    "probabilities": {},
                    "gate": {"accepted_candidate": False, "reason": "no_model"},
                    "feature_summary": {
                        "glyph_count": payload["glyph_count"],
                        "edge_count": payload["edge_count"],
                        "unknown_glyph_rate": payload["unknown_glyph_rate"],
                    },
                },
            }
        score = self._scorer.score_row(row)
        return {
            "score": score.confidence,
            "warnings": list(score.warnings),
            "quality": score.to_json(),
        }

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
            "quality": result.get("quality", {}),
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

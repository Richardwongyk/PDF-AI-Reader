"""Weak relation labels for TinyBDMath graph rows.

This module creates auditable weak labels from exact instrumented graph rows.
It is a training-data bridge, not a production formula recognizer.  Labels are
accepted for supervision only when generic PDF geometry and generic LaTeX
structure evidence agree; otherwise edges remain ``ignore``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from src.core.latex_mathml_extractor import KaTeXMathMLExtractor


RELATION_LABEL_SCHEMA_VERSION = "tinybdmath_relation_labels_v2_vector_rule_radical"
RELATION_LABEL_VERSION = "weak_mathml_geometry_v3_relation_model_supervision"

RELATION_CLASSES = (
    "HORIZONTAL",
    "SUP",
    "SUB",
    "ABOVE",
    "BELOW",
    "FRACTION_BAR",
    "OVERLINE",
    "RADICAL_BODY",
    "NO_RELATION",
    "IGNORE",
)


@dataclass(frozen=True)
class RelationLabelBuildResult:
    rows: list[dict[str, Any]]
    manifest: dict[str, Any]


def build_relation_label_dataset(
    rows: list[dict[str, Any]],
    *,
    mathml_batch_size: int = 512,
    mathml_by_row_id: dict[str, dict[str, Any]] | None = None,
) -> RelationLabelBuildResult:
    labeled_rows: list[dict[str, Any]] = []
    blockers: Counter[str] = Counter()
    label_counts: Counter[str] = Counter()
    quality_counts: Counter[str] = Counter()
    coverage_counts: Counter[str] = Counter()
    mathml_relation_counts: Counter[str] = Counter()
    if mathml_by_row_id is None:
        mathml_rows = _mathml_hints_batch(rows, batch_size=mathml_batch_size)
    else:
        mathml_rows = [_mathml_from_precomputed(row, mathml_by_row_id) for row in rows]
    for row, mathml in zip(rows, mathml_rows):
        labeled = label_graph_row(row, mathml=mathml)
        labeled_rows.append(labeled)
        blockers.update(labeled.get("weak_label_blockers", []))
        label_counts.update(label["label"] for label in labeled.get("edge_labels", []))
        quality_counts.update(label["quality"] for label in labeled.get("edge_labels", []))
        coverage_counts.update(labeled.get("coverage_tags", []))
        mathml_relation_counts.update(labeled.get("mathml_relation_hints", {}))
    manifest = {
        "schema_version": RELATION_LABEL_SCHEMA_VERSION,
        "label_version": RELATION_LABEL_VERSION,
        "rows": len(labeled_rows),
        "edge_labels": sum(len(row.get("edge_labels", [])) for row in labeled_rows),
        "label_counts": dict(sorted(label_counts.items())),
        "quality_counts": dict(sorted(quality_counts.items())),
        "coverage_tags": dict(sorted(coverage_counts.items())),
        "mathml_relation_hints": dict(sorted(mathml_relation_counts.items())),
        "mathml_batch_size": max(1, int(mathml_batch_size or 512)),
        "mathml_source": "precomputed" if mathml_by_row_id is not None else "katex_batch",
        "blockers": dict(sorted(blockers.items())),
        "notes": [
            "Weak labels are for supervised bootstrapping only.",
            "IGNORE and weak labels must not be used as accepted formula output.",
            "Production parsing still requires r0.5 evidence, model confidence, decoder and verifier.",
        ],
    }
    return RelationLabelBuildResult(rows=labeled_rows, manifest=manifest)


def label_graph_row(row: dict[str, Any], *, mathml: dict[str, Any] | None = None) -> dict[str, Any]:
    tags = set(str(tag) for tag in row.get("coverage_tags", []) if tag)
    label_latex = str(row.get("label_latex", "") or "")
    glyphs = {str(glyph.get("node_id", "")): glyph for glyph in row.get("glyph_nodes", []) if isinstance(glyph, dict)}
    vectors = {str(vector.get("node_id", "")): vector for vector in row.get("vector_nodes", []) if isinstance(vector, dict)}
    edge_labels: list[dict[str, Any]] = []
    blockers: list[str] = []
    mathml = mathml or _mathml_hints(label_latex, display_mode=str(row.get("kind", "")) == "display")
    relation_hints = {
        str(key): int(value)
        for key, value in mathml.get("relation_hints", {}).items()
        if _int(value) > 0
    }
    if not glyphs and not vectors:
        blockers.append("empty_graph")
    for edge in row.get("candidate_edges", []):
        if not isinstance(edge, dict):
            continue
        edge_labels.append(_label_edge(edge, glyphs, vectors, tags, relation_hints))
    labeled = {
        "schema_version": RELATION_LABEL_SCHEMA_VERSION,
        "label_version": RELATION_LABEL_VERSION,
        "row_id": row.get("row_id", ""),
        "case": row.get("case", ""),
        "kind": row.get("kind", ""),
        "page_num": row.get("page_num"),
        "label_latex": label_latex,
        "coverage_tags": sorted(tags),
        "mathml_relation_hints": relation_hints,
        "mathml_node_counts": mathml["node_counts"],
        "mathml_warnings": mathml["warnings"],
        "edge_labels": edge_labels,
        "weak_label_blockers": sorted(set(blockers)),
        "input_hash": row.get("input_hash", ""),
    }
    labeled["label_hash"] = _stable_hash(labeled)
    return labeled


_KATEX_EXTRACTOR: KaTeXMathMLExtractor | None = None


def _mathml_hints(label_latex: str, *, display_mode: bool) -> dict[str, Any]:
    global _KATEX_EXTRACTOR
    if _KATEX_EXTRACTOR is None:
        _KATEX_EXTRACTOR = KaTeXMathMLExtractor()
    extracted = _KATEX_EXTRACTOR.extract(label_latex, display_mode=display_mode)
    return {
        "relation_hints": extracted.relation_hints,
        "node_counts": extracted.node_counts,
        "warnings": list(extracted.warnings),
    }


def _mathml_hints_batch(rows: list[dict[str, Any]], *, batch_size: int = 512) -> list[dict[str, Any]]:
    global _KATEX_EXTRACTOR
    if _KATEX_EXTRACTOR is None:
        _KATEX_EXTRACTOR = KaTeXMathMLExtractor()
    chunk_size = max(1, int(batch_size or 512))
    output: list[dict[str, Any]] = []
    for offset in range(0, len(rows), chunk_size):
        chunk = rows[offset : offset + chunk_size]
        items = [
            {
                "latex": str(row.get("label_latex", "") or ""),
                "display_mode": str(row.get("kind", "")) == "display",
            }
            for row in chunk
        ]
        for extracted in _KATEX_EXTRACTOR.extract_batch(items):
            output.append(
                {
                    "relation_hints": extracted.relation_hints,
                    "node_counts": extracted.node_counts,
                    "warnings": list(extracted.warnings),
                }
            )
    return output


def _mathml_from_precomputed(row: dict[str, Any], mathml_by_row_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    row_id = str(row.get("row_id", "") or "")
    value = mathml_by_row_id.get(row_id, {})
    extraction = value.get("mathml_extraction", {}) if isinstance(value, dict) else {}
    if not isinstance(extraction, dict):
        extraction = {}
    return {
        "relation_hints": extraction.get("relation_hints", {}) if isinstance(extraction.get("relation_hints", {}), dict) else {},
        "node_counts": extraction.get("node_counts", {}) if isinstance(extraction.get("node_counts", {}), dict) else {},
        "warnings": extraction.get("warnings", []) if isinstance(extraction.get("warnings", []), list) else [],
    }


def _label_edge(
    edge: dict[str, Any],
    glyphs: dict[str, dict[str, Any]],
    vectors: dict[str, dict[str, Any]],
    tags: set[str],
    relation_hints: dict[str, int],
) -> dict[str, Any]:
    hint = str(edge.get("hint", ""))
    source_id = str(edge.get("source", ""))
    target_id = str(edge.get("target", ""))
    source = glyphs.get(source_id) or vectors.get(source_id) or {}
    target = glyphs.get(target_id) or vectors.get(target_id) or {}
    label = "IGNORE"
    quality = "ignored"
    evidence: list[str] = []
    if hint == "right_neighbor":
        label, quality = "HORIZONTAL", "weak"
        evidence.append("pdf_right_neighbor")
    elif hint == "superscript_zone" and relation_hints.get("SUP", 0) > 0:
        label, quality = "SUP", "weak"
        evidence.append("mathml_sup_relation_hint")
    elif hint == "subscript_zone" and relation_hints.get("SUB", 0) > 0:
        label, quality = "SUB", "weak"
        evidence.append("mathml_sub_relation_hint")
    elif hint == "above_zone":
        label, quality = "ABOVE", "weak"
        evidence.append("pdf_above_zone")
    elif hint == "below_zone":
        label, quality = "BELOW", "weak"
        evidence.append("pdf_below_zone")
    elif hint == "fraction_bar_candidate" and relation_hints.get("FRACTION_BAR", 0) > 0:
        label, quality = "FRACTION_BAR", "weak"
        evidence.append("mathml_fraction_and_pdf_rule")
    elif hint == "overline_candidate" and relation_hints.get("OVERLINE", 0) > 0:
        label, quality = "OVERLINE", "weak"
        evidence.append("mathml_overline_accent_and_pdf_rule")
    elif hint in {"above_rule_candidate", "below_rule_candidate"} and relation_hints.get("FRACTION_BAR", 0) > 0:
        label, quality = ("ABOVE" if hint == "above_rule_candidate" else "BELOW"), "weak"
        evidence.append("glyph_near_fraction_rule")
    elif hint == "far_context":
        label, quality = "NO_RELATION", "negative"
        evidence.append("far_context_negative")
    if source.get("is_script_size") and label in {"SUP", "SUB"}:
        evidence.append("script_size_pdf_evidence")
        quality = "medium"
    if hint == "radical_body_candidate" and relation_hints.get("RADICAL_BODY", 0) > 0 and _looks_like_radical(source, target):
        label, quality = "RADICAL_BODY", "weak"
        evidence.append("mathml_radical_and_pdf_root_glyph")
    return {
        "edge_id": edge.get("edge_id", ""),
        "source": source_id,
        "target": target_id,
        "hint": hint,
        "label": label,
        "quality": quality,
        "evidence": sorted(set(evidence)),
    }


def _looks_like_radical(source: dict[str, Any], target: dict[str, Any]) -> bool:
    source_text = str(source.get("text", ""))
    return source_text in {"√", "sqrt"} and bool(target)


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def read_graph_rows(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                rows.append(value)
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def read_mathml_rows(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                row_id = str(value.get("row_id", "") or "")
                if row_id:
                    rows[row_id] = value
    return rows


def write_relation_label_dataset(result: RelationLabelBuildResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows_path = output_dir / "tinybdmath_relation_label_rows.jsonl"
    with rows_path.open("w", encoding="utf-8") as handle:
        for row in result.rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    (output_dir / "tinybdmath_relation_label_manifest.json").write_text(
        json.dumps(result.manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _stable_hash(payload: Any) -> str:
    import hashlib

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

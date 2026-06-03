from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.latex_mathml_extractor import read_jsonl
from src.core.tinybdmath_alignment import TinyBDAlignmentBuilder
from src.core.tinybdmath_target_tree import TinyBDTargetTreeBuilder


STRUCTURE_SCOPE_AUDIT_SCHEMA_VERSION = "tinybdmath_ai_math_structure_scope_audit_v1"

M0_COVERAGE_TAGS = (
    "symbol",
    "text_run",
    "sequence",
    "group",
    "spacing_artifact",
    "script",
    "prescript",
    "under_over",
    "accent_annotation",
    "fraction",
    "radical",
    "fence",
    "enclosure",
    "matrix_grid",
    "aligned_display",
    "equation_tag",
    "operator",
    "style_variant",
    "mathvariant",
    "font_identity",
)

_IMAGE_CANDIDATE_KINDS = {"image_formula", "scanned_formula", "diagram"}
_IMAGE_DOCUMENT_KINDS = {"scanned", "image_only"}
_UNSUPPORTED_CANDIDATE_KINDS = {"algorithm_block", "diagram"}
_UNSUPPORTED_DOMAINS = {"possible_chemistry", "diagram"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit AI/Math TinyBDMath CSLT structure coverage before training.")
    parser.add_argument("--graph-rows", type=Path, required=True)
    parser.add_argument("--target-trees", type=Path)
    parser.add_argument("--alignment-rows", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--latex-key", default="label_latex")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--min-hard-rate", type=float, default=0.70)
    parser.add_argument("--min-ready-row-rate", type=float, default=0.0)
    args = parser.parse_args()

    graph_rows = read_jsonl(args.graph_rows, limit=args.limit)
    target_rows = read_jsonl(args.target_trees, limit=args.limit) if args.target_trees else None
    alignment_rows = read_jsonl(args.alignment_rows, limit=args.limit) if args.alignment_rows else None
    report = audit_structure_scope_rows(
        graph_rows,
        target_rows=target_rows,
        alignment_rows=alignment_rows,
        latex_key=args.latex_key,
        limit=args.limit,
        min_hard_rate=args.min_hard_rate,
        min_ready_row_rate=args.min_ready_row_rate,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(_manifest_without_rows(report), ensure_ascii=False, indent=2))
    return 0 if report["gate"]["passed"] else 1


def audit_structure_scope_rows(
    graph_rows: list[dict[str, Any]],
    *,
    target_rows: list[dict[str, Any]] | None = None,
    alignment_rows: list[dict[str, Any]] | None = None,
    latex_key: str = "label_latex",
    limit: int = 100,
    min_hard_rate: float = 0.70,
    min_ready_row_rate: float = 0.0,
) -> dict[str, Any]:
    selected = graph_rows[: max(0, int(limit))] if limit > 0 else list(graph_rows)
    generated_target_manifest: dict[str, Any] | None = None
    generated_alignment_manifest: dict[str, Any] | None = None
    if target_rows is None:
        target_rows, generated_target_manifest = TinyBDTargetTreeBuilder().build_rows(
            selected,
            latex_key=latex_key,
        )
    if alignment_rows is None:
        alignment_rows, generated_alignment_manifest = TinyBDAlignmentBuilder().align_rows(selected, target_rows)

    targets_by_id = {str(row.get("row_id", "") or ""): row for row in target_rows}
    alignments_by_id = {str(row.get("row_id", "") or ""): row for row in alignment_rows}
    audits = [
        _audit_one_row(
            graph_row,
            target_row=targets_by_id.get(str(graph_row.get("row_id", "") or "")),
            alignment_row=alignments_by_id.get(str(graph_row.get("row_id", "") or "")),
            latex_key=latex_key,
            min_hard_rate=min_hard_rate,
        )
        for graph_row in selected
    ]

    bucket_counts = Counter(str(item["bucket"]) for item in audits)
    coverage_counts: Counter[str] = Counter()
    ready_coverage_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    blocker_counts: Counter[str] = Counter()
    parser_type_counts: Counter[str] = Counter()
    mathml_tag_counts: Counter[str] = Counter()
    for item in audits:
        coverage_counts.update(str(tag) for tag in item.get("coverage_tags", []))
        reason_counts.update(str(reason) for reason in item.get("reasons", []))
        blocker_counts.update(str(tag) for tag in item.get("blocker_tags", []))
        parser = item.get("parser_evidence", {}) if isinstance(item.get("parser_evidence"), dict) else {}
        parser_type_counts.update(
            {
                str(key): int(value)
                for key, value in (parser.get("katex_type_counts", {}) or {}).items()
                if _int(value) > 0
            }
        )
        mathml_tag_counts.update(
            {
                str(key): int(value)
                for key, value in (parser.get("mathml_tag_counts", {}) or {}).items()
                if _int(value) > 0
            }
        )
        if item.get("bucket") == "ready_for_model":
            ready_coverage_counts.update(str(tag) for tag in item.get("coverage_tags", []))

    row_count = len(audits)
    ready_row_rate = (bucket_counts["ready_for_model"] / row_count) if row_count else 0.0
    gate_failures: list[str] = []
    if row_count <= 0:
        gate_failures.append("no_audit_rows")
    if ready_row_rate < min_ready_row_rate:
        gate_failures.append(f"ready_row_rate {ready_row_rate:.3f} < {min_ready_row_rate:.3f}")
    return {
        "schema_version": STRUCTURE_SCOPE_AUDIT_SCHEMA_VERSION,
        "rows": row_count,
        "latex_key": latex_key,
        "limit": int(limit),
        "min_hard_rate": float(min_hard_rate),
        "bucket_counts": dict(sorted(bucket_counts.items())),
        "coverage_counts": dict(sorted(coverage_counts.items())),
        "ready_coverage_counts": dict(sorted(ready_coverage_counts.items())),
        "missing_m0_coverage_tags": [tag for tag in M0_COVERAGE_TAGS if coverage_counts[tag] <= 0],
        "missing_ready_coverage_tags": [tag for tag in M0_COVERAGE_TAGS if ready_coverage_counts[tag] <= 0],
        "reason_counts": dict(sorted(reason_counts.items())),
        "blocker_counts": dict(sorted(blocker_counts.items())),
        "parser_type_counts": dict(sorted(parser_type_counts.items())),
        "mathml_tag_counts": dict(sorted(mathml_tag_counts.items())),
        "blocked_rows": [
            {
                "row_id": item.get("row_id", ""),
                "bucket": item.get("bucket", ""),
                "blocker_tags": item.get("blocker_tags", []),
                "reasons": item.get("reasons", []),
                "target_tree_tags": item.get("target_tree_tags", []),
                "parser_evidence": item.get("parser_evidence", {}),
                "metrics": item.get("metrics", {}),
                "latex": item.get("latex", ""),
            }
            for item in audits
            if item.get("bucket") != "ready_for_model"
        ],
        "generated_target_manifest": generated_target_manifest,
        "generated_alignment_manifest": generated_alignment_manifest,
        "row_audits": audits,
        "gate": {
            "passed": not gate_failures,
            "min_ready_row_rate": float(min_ready_row_rate),
            "ready_row_rate": round(ready_row_rate, 6),
            "failures": gate_failures,
        },
        "notes": [
            "This is a training/audit scope report, not production formula recognition.",
            "Source LaTeX is used only to build or audit target labels.",
            "Rows blocked by schema, identity, image routing, or unsupported routes must not be fixed in decoder.",
        ],
    }


def _audit_one_row(
    graph_row: dict[str, Any],
    *,
    target_row: dict[str, Any] | None,
    alignment_row: dict[str, Any] | None,
    latex_key: str,
    min_hard_rate: float,
) -> dict[str, Any]:
    row_id = str(graph_row.get("row_id", "") or (target_row or {}).get("row_id", "") or "")
    latex = str(graph_row.get(latex_key, "") or (target_row or {}).get("latex", "") or "")
    target_tree = (target_row or {}).get("target_tree") if isinstance(target_row, dict) else None
    target_tags = _target_tree_coverage_tags(target_tree if isinstance(target_tree, dict) else None)
    coverage_tags = sorted(target_tags)
    route_reasons = _route_unsupported_reasons(graph_row)
    image_reasons = _image_route_reasons(graph_row)
    target_warnings = [str(item) for item in (target_row or {}).get("warnings", []) if item]
    alignment_warnings = [str(item) for item in (alignment_row or {}).get("warnings", []) if item]
    parser_summary = (target_row or {}).get("parser_summary", {}) if isinstance(target_row, dict) else {}
    parser_evidence = _parser_evidence(parser_summary if isinstance(parser_summary, dict) else {})
    schema_reasons = _schema_blocking_reasons(
        target_tree if isinstance(target_tree, dict) else None,
        target_warnings=target_warnings,
    )
    identity_reasons = _identity_blocking_reasons(
        alignment_row,
        min_hard_rate=min_hard_rate,
    )
    has_pdf_glyphs = bool(_glyph_nodes(graph_row))
    evidence_quality = _evidence_quality(graph_row, alignment_row, has_pdf_glyphs=has_pdf_glyphs)

    reasons: list[str] = []
    bucket = "ready_for_model"
    if route_reasons:
        bucket = "route_unsupported"
        reasons.extend(route_reasons)
    elif image_reasons:
        bucket = "needs_image_mfr"
        reasons.extend(image_reasons)
    elif not latex.strip():
        bucket = "abstain"
        reasons.append("missing_latex_label")
    elif target_tree is None:
        bucket = "needs_schema_extension" if target_warnings else "abstain"
        reasons.extend(target_warnings or ["missing_target_tree"])
    elif schema_reasons:
        bucket = "needs_schema_extension"
        reasons.extend(schema_reasons)
    elif not has_pdf_glyphs:
        bucket = "abstain"
        reasons.append("missing_pdf_glyph_nodes")
    elif identity_reasons:
        bucket = "needs_identity_repair"
        reasons.extend(identity_reasons)

    secondary_buckets = _secondary_buckets(route_reasons, image_reasons, schema_reasons, identity_reasons, has_pdf_glyphs)
    blocker_tags = _blocker_tags(
        bucket=bucket,
        route_reasons=route_reasons,
        image_reasons=image_reasons,
        schema_reasons=schema_reasons,
        identity_reasons=identity_reasons,
        target_warnings=target_warnings,
        alignment_warnings=alignment_warnings,
        parser_evidence=parser_evidence,
        has_pdf_glyphs=has_pdf_glyphs,
    )
    checks = {
        "pdf_evidence": "present" if has_pdf_glyphs else "missing",
        "cslt_m0": "blocked" if schema_reasons or target_tree is None else "expressed",
        "target_parser": "passed" if target_tree is not None else "failed",
        "alignment": _alignment_status(alignment_row, min_hard_rate=min_hard_rate),
        "verifier": "pending",
    }
    return {
        "row_id": row_id,
        "bucket": bucket,
        "secondary_buckets": secondary_buckets,
        "blocker_tags": blocker_tags,
        "reasons": sorted(set(reasons)),
        "coverage_tags": coverage_tags,
        "target_tree_tags": sorted(target_tags),
        "latex": latex,
        "parser_evidence": parser_evidence,
        "route": {
            "domain": str(graph_row.get("domain", "math_ai") or "math_ai"),
            "document_kind": str(graph_row.get("document_kind", "born_digital") or "born_digital"),
            "candidate_kind": str(graph_row.get("candidate_kind", graph_row.get("kind", "unknown")) or "unknown"),
            "evidence_quality": evidence_quality,
            "recommended_route": _recommended_route(bucket),
            "accepted_policy": "candidate_only" if bucket in {"ready_for_model", "needs_identity_repair"} else "abstain",
        },
        "checks": checks,
        "metrics": {
            "glyph_nodes": len(_glyph_nodes(graph_row)),
            "vector_nodes": len(graph_row.get("vector_nodes", []) or []),
            "target_nodes": len((target_tree or {}).get("nodes", []) if isinstance(target_tree, dict) else []),
            "target_edges": len((target_tree or {}).get("edges", []) if isinstance(target_tree, dict) else []),
            "hard_alignment_rate": _alignment_hard_rate(alignment_row),
            "unmatched_target_nodes": _unmatched_target_count(alignment_row),
            "relation_labels": len((alignment_row or {}).get("relation_labels", []) or []) if isinstance(alignment_row, dict) else 0,
            "structure_labels": len((alignment_row or {}).get("structure_labels", []) or []) if isinstance(alignment_row, dict) else 0,
        },
        "warnings": {
            "target": target_warnings,
            "alignment": alignment_warnings,
        },
    }


def _target_tree_coverage_tags(target_tree: dict[str, Any] | None) -> set[str]:
    if not target_tree:
        return set()
    tags: set[str] = set()
    nodes = [item for item in target_tree.get("nodes", []) or [] if isinstance(item, dict)]
    edges = [item for item in target_tree.get("edges", []) or [] if isinstance(item, dict)]
    child_counts = Counter(str(item.get("source", "") or "") for item in edges if item.get("relation") in {"child", "next"})
    semantic_nodes = 0
    for node in nodes:
        node_type = str(node.get("node_type", "") or "")
        attrs = node.get("attrs", {}) if isinstance(node.get("attrs"), dict) else {}
        if node_type == "symbol":
            tags.add("symbol")
            semantic_nodes += 1
            if str(attrs.get("katex_type", "") or "") == "op":
                tags.add("operator")
            if attrs.get("identity_aliases") or attrs.get("family"):
                tags.add("font_identity")
        elif node_type == "text_run":
            tags.add("text_run")
            semantic_nodes += max(1, len(str(node.get("value", "") or "")))
            if attrs.get("operator") or str(attrs.get("katex_type", "") or "") == "operatorname":
                tags.add("operator")
        elif node_type == "group":
            tags.add("group")
            role = str(attrs.get("role", "") or "")
            if role.startswith("font:") or attrs.get("font"):
                tags.update({"mathvariant", "font_identity"})
            if role.startswith("styling:"):
                tags.add("style_variant")
            if role.startswith("mclass:") or attrs.get("atom_class"):
                tags.add("style_variant")
            if role == "color" or role.startswith("layout:"):
                tags.add("style_variant")
            if role.startswith("layout:"):
                tags.add("spacing_artifact")
            if role == "enclosure":
                tags.add("enclosure")
            if role in {"matrix_row", "matrix_cell"}:
                tags.add("matrix_grid")
        elif node_type == "script":
            tags.add("script")
        elif node_type == "fraction":
            tags.add("fraction")
        elif node_type == "radical":
            tags.add("radical")
        elif node_type == "accent":
            tags.add("accent_annotation")
        elif node_type == "under_over":
            tags.add("under_over")
        elif node_type == "fence":
            tags.add("fence")
        elif node_type == "matrix":
            tags.add("matrix_grid")
            if str(attrs.get("display_container", "") or "") == "aligned_display":
                tags.add("aligned_display")
        elif node_type == "equation_number":
            tags.add("equation_tag")
        elif node_type == "artifact":
            reason = str(attrs.get("reason", "") or "")
            if reason in {"spacing", "kern", "phantom", "hphantom", "vphantom", "smash", "lap"}:
                tags.add("spacing_artifact")
            if reason == "enclosure":
                tags.add("enclosure")
    if semantic_nodes > 1 or any(count > 1 for count in child_counts.values()):
        tags.add("sequence")
    return tags


def _schema_blocking_reasons(
    target_tree: dict[str, Any] | None,
    *,
    target_warnings: list[str],
) -> list[str]:
    reasons: list[str] = []
    reasons.extend(warning for warning in target_warnings if warning.startswith("target_tree_unsupported_katex_type"))
    reasons.extend(warning for warning in target_warnings if warning.endswith("_missing_body") or warning.endswith("_missing_base"))
    for node in (target_tree or {}).get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        attrs = node.get("attrs", {}) if isinstance(node.get("attrs"), dict) else {}
        reason = str(attrs.get("reason", "") or "")
        if reason.startswith("unsupported"):
            reasons.append(f"unsupported_target_artifact:{reason}")
        if reason == "katex_error_color":
            reasons.append("katex_error_color_node")
    return sorted(set(reasons))


def _identity_blocking_reasons(alignment_row: dict[str, Any] | None, *, min_hard_rate: float) -> list[str]:
    if not isinstance(alignment_row, dict):
        return ["alignment_row_missing"]
    reasons: list[str] = []
    hard_rate = _alignment_hard_rate(alignment_row)
    if hard_rate < min_hard_rate:
        reasons.append(f"hard_alignment_rate_below_min:{hard_rate:.3f}")
    if _unmatched_target_count(alignment_row) > 0:
        reasons.append("unmatched_target_nodes")
    for warning in alignment_row.get("warnings", []) or []:
        text = str(warning)
        if text.startswith("alignment_"):
            reasons.append(text)
    return sorted(set(reasons))


def _route_unsupported_reasons(graph_row: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    candidate_kind = str(graph_row.get("candidate_kind", graph_row.get("kind", "")) or "").lower()
    domain = str(graph_row.get("domain", "") or "").lower()
    recommended_route = str(graph_row.get("recommended_route", "") or "").lower()
    if candidate_kind in _UNSUPPORTED_CANDIDATE_KINDS:
        reasons.append(f"route_candidate_kind:{candidate_kind}")
    if domain in _UNSUPPORTED_DOMAINS:
        reasons.append(f"route_domain:{domain}")
    if recommended_route == "unsupported":
        reasons.append("route_metadata:unsupported")
    return sorted(set(reasons))


def _image_route_reasons(graph_row: dict[str, Any]) -> list[str]:
    candidate_kind = str(graph_row.get("candidate_kind", graph_row.get("kind", "")) or "").lower()
    document_kind = str(graph_row.get("document_kind", "") or "").lower()
    reasons: list[str] = []
    if candidate_kind in _IMAGE_CANDIDATE_KINDS:
        reasons.append(f"image_candidate_kind:{candidate_kind}")
    if document_kind in _IMAGE_DOCUMENT_KINDS:
        reasons.append(f"image_document_kind:{document_kind}")
    if graph_row.get("needs_ocr") is True:
        reasons.append("needs_ocr")
    return sorted(set(reasons))


def _secondary_buckets(
    route_reasons: list[str],
    image_reasons: list[str],
    schema_reasons: list[str],
    identity_reasons: list[str],
    has_pdf_glyphs: bool,
) -> list[str]:
    buckets: list[str] = []
    if route_reasons:
        buckets.append("route_unsupported")
    if image_reasons:
        buckets.append("needs_image_mfr")
    if schema_reasons:
        buckets.append("needs_schema_extension")
    if identity_reasons:
        buckets.append("needs_identity_repair")
    if not has_pdf_glyphs:
        buckets.append("abstain")
    return sorted(set(buckets))


def _blocker_tags(
    *,
    bucket: str,
    route_reasons: list[str],
    image_reasons: list[str],
    schema_reasons: list[str],
    identity_reasons: list[str],
    target_warnings: list[str],
    alignment_warnings: list[str],
    parser_evidence: dict[str, Any],
    has_pdf_glyphs: bool,
) -> list[str]:
    tags: set[str] = set()
    if bucket == "ready_for_model":
        return []
    if route_reasons:
        tags.add("route_metadata")
    if image_reasons:
        tags.add("image_or_scanned")
    if not has_pdf_glyphs:
        tags.add("missing_pdf_glyphs")
    if any(reason.startswith("katex_parse_error") for reason in schema_reasons + target_warnings):
        tags.add("target_parser_failure")
    if any("unsupported_katex_type:cr" in reason for reason in schema_reasons + target_warnings):
        tags.add("target_linebreak_or_alignment")
    if any(reason.startswith("target_tree_unsupported_katex_type") for reason in schema_reasons + target_warnings):
        tags.add("target_builder_unsupported")
    if any(reason.startswith("unsupported_target_artifact") for reason in schema_reasons):
        tags.add("target_builder_unsupported")
    if identity_reasons:
        tags.add("pdf_to_target_alignment")
    if any("alignment_low_hard_coverage" == reason for reason in identity_reasons + alignment_warnings):
        tags.add("low_hard_alignment")
    if any("unmatched_target" in reason for reason in identity_reasons + alignment_warnings):
        tags.add("unmatched_target_identity")
    katex_counts = parser_evidence.get("katex_type_counts", {}) if isinstance(parser_evidence, dict) else {}
    mathml_counts = parser_evidence.get("mathml_tag_counts", {}) if isinstance(parser_evidence, dict) else {}
    if bucket == "needs_identity_repair":
        if _int(katex_counts.get("sqrt")) or _int(mathml_counts.get("msqrt")):
            tags.add("identity_radical_or_rule")
        if _int(katex_counts.get("genfrac")) or _int(mathml_counts.get("mfrac")):
            tags.add("identity_fraction_layout")
        if _int(katex_counts.get("text")) or _int(mathml_counts.get("mtext")):
            tags.add("identity_text_run")
        if _int(katex_counts.get("atom")) or _int(mathml_counts.get("mo")):
            tags.add("identity_operator_or_punctuation")
    return sorted(tags)


def _alignment_status(alignment_row: dict[str, Any] | None, *, min_hard_rate: float) -> str:
    if not isinstance(alignment_row, dict):
        return "missing"
    if _alignment_hard_rate(alignment_row) < min_hard_rate:
        return "low_hard_coverage"
    if _unmatched_target_count(alignment_row) > 0:
        return "unmatched_targets"
    return "passed"


def _evidence_quality(graph_row: dict[str, Any], alignment_row: dict[str, Any] | None, *, has_pdf_glyphs: bool) -> str:
    if not has_pdf_glyphs:
        return "low"
    hard_rate = _alignment_hard_rate(alignment_row)
    if hard_rate >= 0.90:
        return "high"
    if hard_rate >= 0.70:
        return "medium"
    return "low"


def _recommended_route(bucket: str) -> str:
    if bucket == "needs_image_mfr":
        return "image_mfr"
    if bucket == "route_unsupported":
        return "unsupported"
    if bucket == "needs_identity_repair":
        return "cloud_review"
    if bucket == "abstain":
        return "unsupported"
    return "pdf_graph"


def _alignment_hard_rate(alignment_row: dict[str, Any] | None) -> float:
    if not isinstance(alignment_row, dict):
        return 0.0
    stats = alignment_row.get("stats", {}) if isinstance(alignment_row.get("stats"), dict) else {}
    return _float(stats.get("hard_alignment_rate"))


def _unmatched_target_count(alignment_row: dict[str, Any] | None) -> int:
    if not isinstance(alignment_row, dict):
        return 0
    stats = alignment_row.get("stats", {}) if isinstance(alignment_row.get("stats"), dict) else {}
    count = stats.get("unmatched_target_nodes")
    if count is not None:
        try:
            return int(count)
        except (TypeError, ValueError):
            pass
    return len(alignment_row.get("unmatched_target_nodes", []) or [])


def _glyph_nodes(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in row.get("glyph_nodes", []) or row.get("glyphs", []) or [] if isinstance(item, dict)]


def _parser_evidence(parser_summary: dict[str, Any]) -> dict[str, Any]:
    katex_counts = parser_summary.get("katex_type_counts", {})
    family_counts = parser_summary.get("katex_family_counts", {})
    mathml_counts = parser_summary.get("mathml_tag_counts", {})
    return {
        "katex_type_counts": dict(katex_counts) if isinstance(katex_counts, dict) else {},
        "katex_family_counts": dict(family_counts) if isinstance(family_counts, dict) else {},
        "mathml_tag_counts": dict(mathml_counts) if isinstance(mathml_counts, dict) else {},
    }


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _manifest_without_rows(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "row_audits"}


if __name__ == "__main__":
    raise SystemExit(main())

"""Strict label-quality policy for TinyBDMath source/PDF matches.

This module is intentionally small and dependency-free.  It centralizes the
definition of "automatically trusted gold" so audit reports, training row
exports, and review queues cannot silently drift apart.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


SourceKey = tuple[str, str]
SourceScopeKey = tuple[str, str, int | None, int | None]


def label_tier(row: dict[str, Any]) -> str:
    """Bucket a PDF/source match by string similarity only."""

    sim = float_value(row.get("best_source_similarity"))
    if sim >= 0.999:
        return "exact_label"
    if sim >= 0.92:
        return "near_label"
    if sim >= 0.55:
        return "weak_label"
    return "unmatched_label"


def source_maps(rows: list[dict[str, Any]]) -> tuple[dict[SourceKey, dict[str, Any]], Counter[SourceScopeKey]]:
    return source_by_key(rows), source_scope_counts(rows)


def source_by_key(rows: list[dict[str, Any]]) -> dict[SourceKey, dict[str, Any]]:
    result: dict[SourceKey, dict[str, Any]] = {}
    for row in rows:
        result[(str(row.get("case", "")), str(row.get("source_id", "")))] = row
    return result


def source_scope_counts(rows: list[dict[str, Any]]) -> Counter[SourceScopeKey]:
    counts: Counter[SourceScopeKey] = Counter()
    for row in rows:
        normalized = str(row.get("normalized", "")).strip()
        if normalized:
            counts[source_scope_key(row)] += 1
    return counts


def source_scope_key(row: dict[str, Any]) -> SourceScopeKey:
    return (
        str(row.get("case", "")),
        str(row.get("normalized", "")).strip(),
        optional_int(row.get("pdf_page_window_start")),
        optional_int(row.get("pdf_page_window_end")),
    )


def verified_gold_blockers(
    row: dict[str, Any],
    source_by_id: dict[SourceKey, dict[str, Any]] | None = None,
    source_window_counts: Counter[SourceScopeKey] | None = None,
) -> list[str]:
    """Return reasons a candidate must not be treated as automatic gold.

    An empty list means the row is allowed into the automatically verified
    gold subset.  This is deliberately stricter than a normal useful training
    row: ambiguous repeated source formulas, page-window mismatches, missing
    structure, unrepaired glyphs, and warnings all block automatic trust.
    """

    reasons: list[str] = []
    if float_value(row.get("best_source_similarity")) < 0.999:
        reasons.append("not_exact_similarity")
    if str(row.get("page_match", "")) != "same_page_window":
        reasons.append("not_same_page_window")

    summary = row.get("enriched_summary", {})
    if not isinstance(summary, dict):
        summary = {}
    if int_value(row.get("glyph_count")) <= 0:
        reasons.append("empty_glyph_graph")
    if int_value(row.get("edge_count")) <= 0:
        reasons.append("empty_feature_edges")
    if int_value(summary.get("unknown_after")) > 0:
        reasons.append("unrepaired_unknown_glyph")
    if _has_warnings(row.get("warnings", [])):
        reasons.append("candidate_warnings_present")

    case = str(row.get("case", ""))
    source_id = str(row.get("best_source_id", ""))
    if not source_id:
        reasons.append("missing_source_row")
        return _dedupe(reasons)
    if source_by_id is None or source_window_counts is None:
        reasons.append("source_uniqueness_not_checked")
        return _dedupe(reasons)

    source = source_by_id.get((case, source_id))
    if source is None:
        reasons.append("missing_source_row")
        return _dedupe(reasons)
    if source_window_counts.get(source_scope_key(source), 0) != 1:
        reasons.append("source_not_unique_in_page_window")
    if not _candidate_in_source_window(row, source):
        reasons.append("source_page_window_mismatch")
    macro_warnings = row.get("source_macro_expansion_warnings", []) or source.get("macro_expansion_warnings", [])
    if _has_warnings(macro_warnings):
        reasons.append("source_macro_expansion_incomplete")
    source_signature = strict_token_signature(
        str(source.get("canonical_latex", "") or row.get("best_source_latex", "") or source.get("latex", ""))
    )
    candidate_signature = strict_token_signature(str(row.get("r0_latex", "") or row.get("pdf_text", "")))
    if _signature_token_count(source_signature) < 2:
        reasons.append("source_too_short_for_auto_gold")
    if not source_signature or candidate_signature != source_signature:
        reasons.append("strict_token_signature_mismatch")
    return _dedupe(reasons)


def is_verified_gold(
    row: dict[str, Any],
    source_by_id: dict[SourceKey, dict[str, Any]] | None = None,
    source_window_counts: Counter[SourceScopeKey] | None = None,
) -> bool:
    return not verified_gold_blockers(row, source_by_id, source_window_counts)


def policy_description() -> dict[str, str]:
    return {
        "similarity": "best_source_similarity >= 0.999",
        "page_match": "same_page_window",
        "source_scope": "matched source normalized formula is unique inside the same case/page window",
        "pdf_evidence": "glyph_count > 0, edge_count > 0, unknown_after == 0",
        "warnings": "candidate warnings must be empty",
        "source_maps": "source row and source page-window uniqueness must be available",
        "strict_signature": "r0_latex strict token signature must equal the matched source LaTeX signature",
        "minimum_size": "single-token/too-short formulas require review instead of automatic gold",
    }


def strict_token_signature(value: str) -> str:
    """Loss-averse token signature for automatic-gold gating.

    This is a QA filter, not a formula parser.  It intentionally avoids broad
    semantic equivalence: LaTeX commands remain command tokens and Unicode
    symbols remain symbol tokens, so lossy matches are pushed to review.
    """

    tokens: list[str] = []
    text = value.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    index = 0
    while index < len(text):
        ch = text[index]
        if ch.isspace():
            index += 1
            continue
        if ch == "\\":
            end = index + 1
            while end < len(text) and text[end].isalpha():
                end += 1
            if end == index + 1 and end < len(text):
                end += 1
            tokens.append(text[index:end])
            index = end
            continue
        if ch.isalnum():
            end = index + 1
            while end < len(text) and text[end].isalnum():
                end += 1
            tokens.append(text[index:end].lower())
            index = end
            continue
        tokens.append(ch)
        index += 1
    return " ".join(tokens)


def float_value(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def int_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _candidate_in_source_window(row: dict[str, Any], source: dict[str, Any]) -> bool:
    page = int_value(row.get("page_num")) + 1
    start = optional_int(source.get("pdf_page_window_start")) or optional_int(source.get("pdf_page_hint"))
    end = optional_int(source.get("pdf_page_window_end"))
    if start is None:
        return False
    if end is None:
        return page >= start
    return start <= page <= end


def _has_warnings(value: object) -> bool:
    if isinstance(value, list):
        return bool(value)
    return bool(value)


def _dedupe(reasons: list[str]) -> list[str]:
    return sorted(set(reasons))


def _signature_token_count(signature: str) -> int:
    return len([token for token in signature.split(" ") if token])

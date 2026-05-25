"""Export TinyBDMath candidate review packages.

This tool is for building a trustworthy matching set, not for production PDF
parsing.  It turns uncertain PDF/source matches into auditable JSONL items and
optional PDF crop images that a human reviewer or vision model can inspect.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.formula_latex_audit import _cases
from tools.tinybdmath_gold_policy import (
    label_tier,
    policy_description,
    source_maps,
    verified_gold_blockers,
)


SCHEMA_VERSION = "tinybdmath_review_queue_v1"


@dataclass(frozen=True)
class ReviewQueueSummary:
    schema_version: str
    dataset_dir: str
    output_dir: str
    focus: str
    include_images: bool
    candidates_seen: int
    review_items: int
    image_items: int
    verified_gold_skipped: int
    blockers: dict[str, int]
    tiers: dict[str, int]
    page_matches: dict[str, int]
    queue_path: str
    decision_template_path: str
    policy: dict[str, str]


def build_review_queue(
    *,
    dataset_dir: Path,
    output_dir: Path,
    case_name: str = "all",
    focus: str = "blocked",
    limit: int = 0,
    include_images: bool = False,
    dpi: int = 180,
    pad: float = 8.0,
) -> ReviewQueueSummary:
    sources = _read_jsonl(dataset_dir / "source_formulas.jsonl")
    candidates = _read_jsonl(dataset_dir / "pdf_candidates.jsonl")
    source_by_key, source_scope_counts = source_maps(sources)
    available_cases = {case.name: case for case in _cases()}
    source_lookup = {(str(row.get("case", "")), str(row.get("source_id", ""))): row for row in sources}

    output_dir.mkdir(parents=True, exist_ok=True)
    image_dir = output_dir / "images"
    queue: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    blocker_counts: dict[str, int] = {}
    tier_counts: dict[str, int] = {}
    page_match_counts: dict[str, int] = {}
    verified_gold_skipped = 0
    image_items = 0

    for candidate in candidates:
        case = str(candidate.get("case", ""))
        if case_name != "all" and case != case_name:
            continue
        blockers = verified_gold_blockers(candidate, source_by_key, source_scope_counts)
        tier = label_tier(candidate)
        page_match = str(candidate.get("page_match", "")) or "missing"
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        page_match_counts[page_match] = page_match_counts.get(page_match, 0) + 1
        for reason in blockers:
            blocker_counts[reason] = blocker_counts.get(reason, 0) + 1
        if not _include_for_focus(focus, blockers, tier, page_match):
            if not blockers:
                verified_gold_skipped += 1
            continue

        source = source_lookup.get((case, str(candidate.get("best_source_id", ""))), {})
        item = _review_item(candidate, source, blockers, tier)
        if include_images and case in available_cases:
            crop = _write_crop(
                pdf=available_cases[case].pdf,
                candidate=candidate,
                output_dir=image_dir,
                dpi=dpi,
                pad=pad,
            )
            if crop:
                item["pdf_crop_image"] = crop
                image_items += 1
        queue.append(item)
        decisions.append(_decision_template(item))
        if limit > 0 and len(queue) >= limit:
            break

    queue_path = output_dir / "review_queue.jsonl"
    decision_template_path = output_dir / "review_decisions_template.jsonl"
    _write_jsonl(queue_path, queue)
    _write_jsonl(decision_template_path, decisions)
    summary = ReviewQueueSummary(
        schema_version=SCHEMA_VERSION,
        dataset_dir=str(dataset_dir),
        output_dir=str(output_dir),
        focus=focus,
        include_images=include_images,
        candidates_seen=len(candidates),
        review_items=len(queue),
        image_items=image_items,
        verified_gold_skipped=verified_gold_skipped,
        blockers=dict(sorted(blocker_counts.items())),
        tiers=dict(sorted(tier_counts.items())),
        page_matches=dict(sorted(page_match_counts.items())),
        queue_path=str(queue_path),
        decision_template_path=str(decision_template_path),
        policy=policy_description(),
    )
    (output_dir / "summary.json").write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _include_for_focus(focus: str, blockers: list[str], tier: str, page_match: str) -> bool:
    if focus == "all":
        return True
    if focus == "blocked":
        return bool(blockers)
    if focus == "near":
        return tier == "near_label"
    if focus == "weak":
        return tier in {"weak_label", "unmatched_label"}
    if focus == "page_mismatch":
        return page_match != "same_page_window"
    return bool(blockers)


def _review_item(
    candidate: dict[str, Any],
    source: dict[str, Any],
    blockers: list[str],
    tier: str,
) -> dict[str, Any]:
    case = str(candidate.get("case", ""))
    candidate_id = str(candidate.get("candidate_id", ""))
    review_id = f"{case}:{candidate_id}"
    return {
        "schema_version": SCHEMA_VERSION,
        "review_id": review_id,
        "case": case,
        "candidate_id": candidate_id,
        "page_num_zero_based": _int(candidate.get("page_num")),
        "page_num_one_based": _int(candidate.get("page_num")) + 1,
        "bbox": _bbox(candidate.get("bbox")),
        "label_tier": tier,
        "verified_gold": not blockers,
        "verified_gold_blockers": blockers,
        "page_match": str(candidate.get("page_match", "")),
        "best_source_similarity": candidate.get("best_source_similarity", 0.0),
        "pdf_text": str(candidate.get("pdf_text", "")),
        "r0_latex": str(candidate.get("r0_latex", "")),
        "r0_score": candidate.get("r0_score", 0.0),
        "training_target_latex": str(candidate.get("best_source_latex", "")),
        "raw_source_latex": str(candidate.get("best_source_raw_latex", "")),
        "source": _source_payload(source),
        "pdf_evidence": {
            "glyph_count": _int(candidate.get("glyph_count")),
            "edge_count": _int(candidate.get("edge_count")),
            "edge_hint_counts": candidate.get("edge_hint_counts", {}),
            "enriched_summary": candidate.get("enriched_summary", {}),
            "warnings": candidate.get("warnings", []),
            "source_macro_expansion_warnings": candidate.get("source_macro_expansion_warnings", []),
            "raw_graph_hash": candidate.get("raw_graph_hash", ""),
            "enriched_graph_hash": candidate.get("enriched_graph_hash", ""),
            "feature_graph_hash": candidate.get("feature_graph_hash", ""),
        },
        "review_options": {
            "allowed_decisions": ["accept", "reject", "revise", "uncertain"],
            "accept_rule": "Use only when the PDF crop and source LaTeX match exactly enough for the chosen label policy, including any required macro-definition checks.",
            "revise_rule": "Use reviewer_latex when the PDF crop is readable but the source match needs canonical LaTeX, macro expansion, or correction.",
            "macro_rule": "If source LaTeX uses project-specific macros, do not accept by visual similarity alone; verify the macro definition or revise to canonical LaTeX.",
        },
        "model_review_prompt": _model_prompt(candidate, source, blockers),
    }


def _source_payload(source: dict[str, Any]) -> dict[str, Any]:
    if not source:
        return {}
    return {
        "source_id": source.get("source_id", ""),
        "kind": source.get("kind", ""),
        "latex": source.get("latex", ""),
        "canonical_latex": source.get("canonical_latex", source.get("latex", "")),
        "normalized": source.get("normalized", ""),
        "macro_expansion_version": source.get("macro_expansion_version", ""),
        "macro_expansion_applied": source.get("macro_expansion_applied", []),
        "macro_expansion_warnings": source.get("macro_expansion_warnings", []),
        "env": source.get("env", ""),
        "delimiter": source.get("delimiter", ""),
        "tex_path": source.get("tex_path", ""),
        "tex_order": source.get("tex_order", 0),
        "char_start": source.get("char_start", 0),
        "char_end": source.get("char_end", 0),
        "context_before": source.get("context_before", ""),
        "context_after": source.get("context_after", ""),
        "pdf_page_hint": source.get("pdf_page_hint", None),
        "pdf_page_window_start": source.get("pdf_page_window_start", None),
        "pdf_page_window_end": source.get("pdf_page_window_end", None),
    }


def _decision_template(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "tinybdmath_review_decision_v1",
        "review_id": item["review_id"],
        "case": item["case"],
        "candidate_id": item["candidate_id"],
        "decision": "",
        "reviewer": "",
        "reviewer_latex": "",
        "confidence": "",
        "notes": "",
        "source_latex": item.get("source", {}).get("canonical_latex", item.get("source", {}).get("latex", "")),
        "raw_source_latex": item.get("source", {}).get("latex", ""),
        "r0_latex": item.get("r0_latex", ""),
        "pdf_crop_image": item.get("pdf_crop_image", ""),
    }


def _model_prompt(candidate: dict[str, Any], source: dict[str, Any], blockers: list[str]) -> str:
    return (
        "You are reviewing a born-digital PDF formula dataset item. "
        "Use the attached PDF crop if available, plus the PDF text/LaTeX evidence, "
        "to decide whether the source LaTeX is an exact supervised label for the visible formula. "
        "Do not treat project-specific macros as correct merely because they look similar after rendering; "
        "if a macro definition is required and not provided, return uncertain or revise to canonical LaTeX. "
        "Return JSON only with decision=accept|reject|revise|uncertain, reviewer_latex when revised, "
        "confidence in [0,1], and a short reason. "
        f"PDF page one-based: {_int(candidate.get('page_num')) + 1}. "
        f"PDF text: {str(candidate.get('pdf_text', ''))[:300]!r}. "
        f"R0 LaTeX: {str(candidate.get('r0_latex', ''))[:300]!r}. "
        f"Canonical source LaTeX: {str(source.get('canonical_latex', source.get('latex', '')))[:500]!r}. "
        f"Raw source LaTeX: {str(source.get('latex', ''))[:500]!r}. "
        f"Macro expansion warnings: {source.get('macro_expansion_warnings', [])}. "
        f"Blockers: {blockers}."
    )


def _write_crop(
    *,
    pdf: Path,
    candidate: dict[str, Any],
    output_dir: Path,
    dpi: int,
    pad: float,
) -> str:
    bbox = _bbox(candidate.get("bbox"))
    if len(bbox) != 4:
        return ""
    page_num = _int(candidate.get("page_num"))
    output_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_filename(
        f"{candidate.get('case', '')}_{candidate.get('candidate_id', '')}_p{page_num + 1}.png"
    )
    path = output_dir / name
    try:
        with fitz.open(str(pdf)) as doc:
            if page_num < 0 or page_num >= doc.page_count:
                return ""
            page = doc[page_num]
            rect = fitz.Rect(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
            clip = fitz.Rect(
                max(page.rect.x0, rect.x0 - pad),
                max(page.rect.y0, rect.y0 - pad),
                min(page.rect.x1, rect.x1 + pad),
                min(page.rect.y1, rect.y1 + pad),
            )
            if clip.is_empty or clip.is_infinite:
                return ""
            zoom = max(36, int(dpi)) / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, colorspace=fitz.csRGB, alpha=False)
            pix.save(str(path))
    except Exception as exc:
        return f"crop_error:{exc}"
    return str(path)


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "crop.png"


def _bbox(value: object) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return []
    result: list[float] = []
    for item in value:
        try:
            result.append(round(float(item), 3))
        except (TypeError, ValueError):
            return []
    return result


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--case", choices=["all", "attention", "napkin"], default="all")
    parser.add_argument("--focus", choices=["blocked", "near", "weak", "page_mismatch", "all"], default="blocked")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--include-images", action="store_true")
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--pad", type=float, default=8.0)
    args = parser.parse_args()

    summary = build_review_queue(
        dataset_dir=args.dataset_dir,
        output_dir=args.output_dir,
        case_name=args.case,
        focus=args.focus,
        limit=max(0, int(args.limit)),
        include_images=bool(args.include_images),
        dpi=max(36, int(args.dpi)),
        pad=max(0.0, float(args.pad)),
    )
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

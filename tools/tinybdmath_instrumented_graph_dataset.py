"""Convert exact instrumented LaTeX/PDF rows into TinyBDMath graph rows.

The input is produced by ``tools/tinybdmath_instrumented_latex_dataset.py``.
Those rows already have exact source LaTeX labels and exact PDF color-capture
bboxes.  This script keeps that gold alignment intact and converts each formula
into a reusable graph-style dataset for TinyBDMath relation/quality models.

It does not train a model and it does not use source LaTeX in production
parsing.  Source LaTeX is used here only as dataset supervision.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


GRAPH_DATASET_SCHEMA_VERSION = "tinybdmath_instrumented_graph_dataset_v1"
GRAPH_ROW_SCHEMA_VERSION = "tinybdmath_instrumented_graph_row_v1"
SPLIT_SCHEMA_VERSION = "tinybdmath_instrumented_graph_split_v1"

DEFAULT_RESOURCE_VERSIONS = {
    "mathml_target": "Presentation MathML / SLT planning target",
    "unicode_mathclass": "local_cache:unicode/math/MathClass-15.txt",
    "unicode_mathclassex": "local_cache:unicode/math/MathClassEx-15.txt",
    "unicode_ucd": "local_cache:unicode/ucd",
    "glyph_agl": "local_cache:glyph/adobe/glyphlist.txt",
    "glyph_aglfn": "local_cache:glyph/adobe/aglfn.txt",
    "glyph_texglyphlist": "local_cache:glyph/tex/texglyphlist.txt",
    "opentype_math": "local_cache:opentype/math-table.html",
}

MATH_FONT_MARKERS = (
    "CMEX",
    "CMMI",
    "CMSY",
    "MSAM",
    "MSBM",
    "LMMath",
    "Math",
    "STIX",
    "XITS",
    "CambriaMath",
)

RELATION_HINTS = (
    "right_neighbor",
    "superscript_zone",
    "subscript_zone",
    "above_zone",
    "below_zone",
    "overlap_zone",
    "far_context",
    "above_rule_candidate",
    "below_rule_candidate",
    "fraction_bar_candidate",
    "overline_candidate",
    "radical_body_candidate",
)


@dataclass(frozen=True)
class InstrumentedGraphBuildResult:
    rows: list[dict[str, Any]]
    manifest: dict[str, Any]
    split: dict[str, Any]


def build_graph_dataset(
    inputs: list[Path],
    *,
    split_seed: str = "tinybdmath-instrumented-v1",
    train_ratio: float = 0.8,
    validation_ratio: float = 0.1,
    limit: int = 0,
) -> InstrumentedGraphBuildResult:
    source_files = [_source_file_payload(path) for path in inputs]
    rows: list[dict[str, Any]] = []
    blockers: Counter[str] = Counter()
    for input_index, path in enumerate(inputs):
        for line_index, raw in enumerate(_read_jsonl(path)):
            row, row_blockers = _graph_row(raw, input_path=path, input_index=input_index, line_index=line_index)
            if row is None:
                blockers.update(row_blockers)
                continue
            rows.append(row)
            blockers.update(row_blockers)
            if limit > 0 and len(rows) >= limit:
                break
        if limit > 0 and len(rows) >= limit:
            break
    split = build_split(rows, seed=split_seed, train_ratio=train_ratio, validation_ratio=validation_ratio)
    manifest = _manifest(inputs, source_files, rows, split, blockers)
    return InstrumentedGraphBuildResult(rows=rows, manifest=manifest, split=split)


def write_graph_dataset(result: InstrumentedGraphBuildResult, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "tinybdmath_graph_rows.jsonl", result.rows)
    _write_json(output_dir / "tinybdmath_graph_manifest.json", result.manifest)
    _write_json(output_dir / "tinybdmath_graph_split.json", result.split)


def build_split(
    rows: list[dict[str, Any]],
    *,
    seed: str = "tinybdmath-instrumented-v1",
    train_ratio: float = 0.8,
    validation_ratio: float = 0.1,
) -> dict[str, Any]:
    train_ratio = max(0.0, min(1.0, float(train_ratio)))
    validation_ratio = max(0.0, min(1.0 - train_ratio, float(validation_ratio)))
    groups: dict[str, list[str]] = defaultdict(list)
    group_meta: dict[str, dict[str, Any]] = {}
    for row in rows:
        group_key = _split_group_key(row)
        groups[group_key].append(str(row["row_id"]))
        group_meta.setdefault(
            group_key,
            {
                "case": row.get("case", ""),
                "tex_path": row.get("tex_path", ""),
                "page_num": row.get("page_num"),
            },
        )
    split_rows = {"train": [], "validation": [], "test": []}
    split_groups = {"train": [], "validation": [], "test": []}
    for group_key in sorted(groups):
        value = int(_stable_hash({"seed": seed, "group": group_key})[:12], 16) / float(0xFFFFFFFFFFFF)
        if value < train_ratio:
            split_name = "train"
        elif value < train_ratio + validation_ratio:
            split_name = "validation"
        else:
            split_name = "test"
        split_groups[split_name].append({"group_key": group_key, **group_meta[group_key], "rows": len(groups[group_key])})
        split_rows[split_name].extend(groups[group_key])
    return {
        "schema_version": SPLIT_SCHEMA_VERSION,
        "split_seed": seed,
        "train_ratio": train_ratio,
        "validation_ratio": validation_ratio,
        "test_ratio": round(1.0 - train_ratio - validation_ratio, 6),
        "row_counts": {key: len(value) for key, value in split_rows.items()},
        "group_counts": {key: len(value) for key, value in split_groups.items()},
        "rows": {key: sorted(value) for key, value in split_rows.items()},
        "groups": split_groups,
    }


def _graph_row(
    raw: dict[str, Any],
    *,
    input_path: Path,
    input_index: int,
    line_index: int,
) -> tuple[dict[str, Any] | None, list[str]]:
    blockers: list[str] = []
    if raw.get("verified_exact_box") is not True:
        blockers.append("not_verified_exact_box")
    label = str(raw.get("label_latex", "") or "")
    if not label.strip():
        blockers.append("missing_label_latex")
    bbox = _bbox(raw.get("bbox"))
    if bbox is None:
        blockers.append("missing_or_invalid_bbox")
    glyphs = [_glyph_node(item, index) for index, item in enumerate(_list(raw.get("sampled_glyphs")))]
    glyphs = [item for item in glyphs if item is not None]
    vectors = [_vector_node(item, index) for index, item in enumerate(_list(raw.get("sampled_vectors")))]
    vectors = [item for item in vectors if item is not None]
    if not glyphs and not vectors:
        blockers.append("empty_graph_nodes")
    if blockers:
        return None, blockers

    candidate_edges = _candidate_edges(glyphs, vectors)
    coverage = _coverage_tags(raw, glyphs, vectors, label)
    row_id = _stable_hash(
        {
            "schema": GRAPH_ROW_SCHEMA_VERSION,
            "input": str(input_path),
            "input_index": input_index,
            "line": line_index,
            "case": raw.get("case", ""),
            "source_id": raw.get("source_id", ""),
            "marker_id": raw.get("marker_id", ""),
            "label": label,
            "bbox": bbox,
            "glyphs": glyphs,
            "vectors": vectors,
        }
    )
    row = {
        "schema_version": GRAPH_ROW_SCHEMA_VERSION,
        "row_id": row_id,
        "case": str(raw.get("case", "") or ""),
        "source_id": str(raw.get("source_id", "") or ""),
        "marker_id": str(raw.get("marker_id", "") or ""),
        "kind": str(raw.get("kind", "") or ""),
        "label_latex": label,
        "raw_source_latex": str(raw.get("raw_source_latex", "") or ""),
        "label_source": str(raw.get("label_source", "") or "latex_source_macro_expanded"),
        "pdf_window_source": str(raw.get("pdf_window_source", "") or "instrumented_latex_color"),
        "compiled_pdf": str(raw.get("compiled_pdf", "") or ""),
        "target_pdf": str(raw.get("target_pdf", "") or ""),
        "coordinate_baseline": str(raw.get("coordinate_baseline", "") or "compiled_instrumented_pdf"),
        "tex_path": str(raw.get("tex_path", "") or ""),
        "page_num": _optional_int(raw.get("page_num")),
        "bbox": bbox,
        "glyph_nodes": glyphs,
        "vector_nodes": vectors,
        "candidate_edges": candidate_edges,
        "coverage_tags": coverage,
        "graph_stats": _graph_stats(glyphs, vectors, candidate_edges, coverage),
        "resource_versions": dict(DEFAULT_RESOURCE_VERSIONS),
        "source_file": str(input_path),
        "source_line": line_index + 1,
    }
    row["input_hash"] = _stable_hash({key: row[key] for key in row if key not in {"input_hash"}})
    return row, []


def _glyph_node(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    bbox = _bbox(item.get("bbox"))
    if bbox is None:
        return None
    text = str(item.get("text", "") or "")
    font = str(item.get("font", "") or item.get("font_name", "") or "")
    size = _float(item.get("size"))
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    return {
        "node_id": f"g{index:04d}",
        "node_type": "glyph",
        "text": text,
        "unicode": text if len(text) == 1 else "",
        "font": font,
        "normalized_font": _normalize_font(font),
        "size": round(size, 6),
        "bbox": bbox,
        "center": [round((bbox[0] + bbox[2]) / 2.0, 6), round((bbox[1] + bbox[3]) / 2.0, 6)],
        "width": round(width, 6),
        "height": round(height, 6),
        "is_math_font": _is_math_font(font),
        "is_script_size": False,
        "raw": _compact_raw(item),
    }


def _vector_node(item: dict[str, Any], index: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    bbox = _bbox(item.get("bbox"))
    if bbox is None:
        return None
    width = max(0.0, bbox[2] - bbox[0])
    height = max(0.0, bbox[3] - bbox[1])
    kind = str(item.get("type", "") or item.get("kind", "") or "vector")
    aspect = width / max(height, 1e-6)
    return {
        "node_id": f"v{index:04d}",
        "node_type": "vector",
        "vector_type": kind,
        "bbox": bbox,
        "center": [round((bbox[0] + bbox[2]) / 2.0, 6), round((bbox[1] + bbox[3]) / 2.0, 6)],
        "width": round(width, 6),
        "height": round(height, 6),
        "aspect_ratio": round(aspect, 6),
        "is_horizontal_rule_candidate": aspect >= 6.0 and height <= max(width * 0.08, 2.0),
        "raw": _compact_raw(item),
    }


def _candidate_edges(glyphs: list[dict[str, Any]], vectors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    nonempty = [glyph for glyph in glyphs if str(glyph.get("text", "")).strip()]
    if nonempty:
        reference_size = max([float(glyph["size"]) for glyph in nonempty if float(glyph.get("size", 0.0)) > 0.0], default=0.0)
        if reference_size > 0:
            for glyph in glyphs:
                glyph["is_script_size"] = float(glyph.get("size", 0.0)) <= reference_size * 0.82
    ordered_x = sorted(glyphs, key=lambda item: (float(item["bbox"][0]), float(item["center"][1]), str(item["node_id"])))
    x0_values = [float(item["bbox"][0]) for item in ordered_x]
    index_by_id = {str(item["node_id"]): index for index, item in enumerate(ordered_x)}
    for source in glyphs:
        ranked: list[dict[str, Any]] = []
        for target in _local_glyph_targets(source, ordered_x, x0_values, index_by_id):
            if source["node_id"] == target["node_id"]:
                continue
            hint = _glyph_relation_hint(source, target)
            if hint is None:
                continue
            ranked.append(_edge(source, target, hint))
        ranked.sort(key=_edge_rank)
        edges.extend(ranked[:12])
        radical_ranked = [
            _edge(source, target, "radical_body_candidate")
            for target in glyphs
            if source["node_id"] != target["node_id"] and _radical_body_hint(source, target)
        ]
        radical_ranked.sort(key=_edge_rank)
        edges.extend(radical_ranked[:12])
    for vector in vectors:
        if not vector.get("is_horizontal_rule_candidate"):
            continue
        above = [glyph for glyph in glyphs if _glyph_overlaps_rule(glyph, vector) and glyph["center"][1] < vector["center"][1]]
        below = [glyph for glyph in glyphs if _glyph_overlaps_rule(glyph, vector) and glyph["center"][1] > vector["center"][1]]
        for glyph in sorted(above, key=lambda item: (item["center"][0], item["center"][1], item["node_id"]))[:24]:
            edges.append(_edge(vector, glyph, "above_rule_candidate"))
        for glyph in sorted(below, key=lambda item: (item["center"][0], item["center"][1], item["node_id"]))[:24]:
            edges.append(_edge(vector, glyph, "below_rule_candidate"))
        if above and below:
            edges.append(_rule_summary_edge(vector, above, below, "fraction_bar_candidate"))
        elif above:
            edges.append(_rule_summary_edge(vector, above, [], "overline_candidate"))
    return _dedupe_edges(edges)


def _local_glyph_targets(
    source: dict[str, Any],
    ordered_x: list[dict[str, Any]],
    x0_values: list[float],
    index_by_id: dict[str, int],
) -> list[dict[str, Any]]:
    if len(ordered_x) <= 72:
        return ordered_x
    sx0, _sy0, sx1, _sy1 = [float(value) for value in source["bbox"]]
    height = max(float(source.get("height", 0.0)), 1.0)
    left = sx0 - 4.0 * height
    right = sx1 + 4.0 * height
    start = max(0, bisect.bisect_left(x0_values, left) - 8)
    end = min(len(ordered_x), bisect.bisect_right(x0_values, right) + 8)
    pool: dict[str, dict[str, Any]] = {
        str(item["node_id"]): item
        for item in ordered_x[start:end]
    }
    source_index = index_by_id.get(str(source.get("node_id", "")), 0)
    for item in ordered_x[max(0, source_index - 36) : min(len(ordered_x), source_index + 48)]:
        pool[str(item["node_id"])] = item
    overlap_end = bisect.bisect_right(x0_values, sx1)
    overlap = [
        item
        for item in ordered_x[:overlap_end]
        if float(item["bbox"][2]) >= sx0
    ]
    overlap.sort(key=lambda item: (abs(float(item["center"][1]) - float(source["center"][1])), float(item["center"][0]), str(item["node_id"])))
    for item in overlap[:36]:
        pool[str(item["node_id"])] = item
    return list(pool.values())


def _glyph_relation_hint(source: dict[str, Any], target: dict[str, Any]) -> str | None:
    sx0, sy0, sx1, sy1 = [float(value) for value in source["bbox"]]
    tx0, ty0, tx1, ty1 = [float(value) for value in target["bbox"]]
    sheight = max(float(source.get("height", 0.0)), 1.0)
    theight = max(float(target.get("height", 0.0)), 1.0)
    height = max(sheight, theight, 1.0)
    horizontal_gap = tx0 - sx1
    dy = float(target["center"][1]) - float(source["center"][1])
    x_overlap = _overlap_ratio((sx0, sx1), (tx0, tx1))
    y_overlap = _overlap_ratio((sy0, sy1), (ty0, ty1))
    if horizontal_gap >= -0.25 * height and horizontal_gap <= 2.8 * height:
        if theight <= sheight * 0.92 and dy < -0.12 * height:
            return "superscript_zone"
        if theight <= sheight * 0.92 and dy > 0.12 * height:
            return "subscript_zone"
        if y_overlap >= 0.35:
            return "right_neighbor"
    if x_overlap >= 0.08 and abs(dy) <= 2.4 * height:
        if dy < -0.35 * height:
            return "above_zone"
        if dy > 0.35 * height:
            return "below_zone"
        return "overlap_zone"
    if abs(horizontal_gap) <= 2.8 * height and abs(dy) <= 2.4 * height:
        return "far_context"
    return None


def _radical_body_hint(source: dict[str, Any], target: dict[str, Any]) -> bool:
    if str(source.get("text", "") or "") not in {"√", "sqrt"}:
        return False
    if float(target["center"][0]) <= float(source["center"][0]):
        return False
    height = max(float(source.get("height", 0.0)), float(target.get("height", 0.0)), 1.0)
    horizontal_gap = float(target["bbox"][0]) - float(source["bbox"][2])
    if horizontal_gap > height * 4.0:
        return False
    y_overlap = _overlap_ratio((source["bbox"][1], source["bbox"][3]), (target["bbox"][1], target["bbox"][3]))
    return y_overlap >= 0.05 or abs(float(target["center"][1]) - float(source["center"][1])) <= height * 1.6


def _edge(source: dict[str, Any], target: dict[str, Any], hint: str) -> dict[str, Any]:
    dx = float(target["center"][0]) - float(source["center"][0])
    dy = float(target["center"][1]) - float(source["center"][1])
    height = max(float(source.get("height", 0.0)), float(target.get("height", 0.0)), 1.0)
    width = max(float(source.get("width", 0.0)), float(target.get("width", 0.0)), 1.0)
    source_size = float(source.get("size", 0.0) or source.get("height", 0.0) or 1.0)
    target_size = float(target.get("size", 0.0) or target.get("height", 0.0) or 1.0)
    horizontal_gap = float(target["bbox"][0]) - float(source["bbox"][2])
    vertical_gap = max(float(target["bbox"][1]) - float(source["bbox"][3]), float(source["bbox"][1]) - float(target["bbox"][3]), 0.0)
    return {
        "edge_id": f"{source['node_id']}->{target['node_id']}:{hint}",
        "source": source["node_id"],
        "target": target["node_id"],
        "hint": hint,
        "features": {
            "dx_over_height": round(dx / height, 6),
            "dy_over_height": round(dy / height, 6),
            "horizontal_gap_over_height": round(horizontal_gap / height, 6),
            "vertical_gap_over_height": round(vertical_gap / height, 6),
            "x_overlap": round(_overlap_ratio((source["bbox"][0], source["bbox"][2]), (target["bbox"][0], target["bbox"][2])), 6),
            "y_overlap": round(_overlap_ratio((source["bbox"][1], source["bbox"][3]), (target["bbox"][1], target["bbox"][3])), 6),
            "size_ratio": round(target_size / max(source_size, 1e-6), 6),
            "width_ratio": round(float(target.get("width", 0.0)) / width, 6),
            "height_ratio": round(float(target.get("height", 0.0)) / height, 6),
            "same_font": int(bool(source.get("normalized_font")) and source.get("normalized_font") == target.get("normalized_font")),
            "source_unknown": int(not bool(str(source.get("text", "") or "").strip())),
            "target_unknown": int(not bool(str(target.get("text", "") or "").strip())),
            "source_is_script_size": int(bool(source.get("is_script_size"))),
            "target_is_script_size": int(bool(target.get("is_script_size"))),
        },
    }


def _rule_summary_edge(
    vector: dict[str, Any],
    above: list[dict[str, Any]],
    below: list[dict[str, Any]],
    hint: str,
) -> dict[str, Any]:
    return {
        "edge_id": f"{vector['node_id']}:{hint}",
        "source": vector["node_id"],
        "target": vector["node_id"],
        "hint": hint,
        "features": {
            "dx_over_height": 0.0,
            "dy_over_height": 0.0,
            "horizontal_gap_over_height": 0.0,
            "vertical_gap_over_height": 0.0,
            "x_overlap": 1.0,
            "y_overlap": 1.0,
            "size_ratio": 1.0,
            "width_ratio": 1.0,
            "height_ratio": 1.0,
            "same_font": 0,
            "source_unknown": 1,
            "target_unknown": 1,
            "source_is_script_size": 0,
            "target_is_script_size": 0,
            "above_count": len(above),
            "below_count": len(below),
            "rule_width": vector["width"],
            "rule_height": vector["height"],
            "rule_aspect_ratio": vector["aspect_ratio"],
        },
    }


def _coverage_tags(
    raw: dict[str, Any],
    glyphs: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    label: str,
) -> list[str]:
    # These tags are dataset audit buckets only. They are intentionally derived
    # from generic LaTeX/PDF evidence and must not be used as production formula
    # reconstruction rules.
    tags: set[str] = set()
    kind = str(raw.get("kind", "") or "")
    if kind:
        tags.add(kind)
    if "_" in label:
        tags.add("subscript")
    if "^" in label:
        tags.add("superscript")
    if "_" in label and "^" in label:
        tags.add("subsup")
    if r"\frac" in label or r"\dfrac" in label or r"\tfrac" in label:
        tags.add("fraction")
    if r"\sqrt" in label:
        tags.add("radical")
    if any(token in label for token in (r"\bar", r"\overline", r"\widehat", r"\hat", r"\tilde", r"\vec")):
        tags.add("accent_or_overline")
    if any(token in label for token in (r"\sum", r"\prod", r"\lim", r"\int", r"\bigcup", r"\bigcap")):
        tags.add("large_operator")
    if any(token in label for token in (r"\begin{matrix", r"\begin{pmatrix", r"\begin{bmatrix", r"\begin{array")):
        tags.add("matrix_or_array")
    if r"\begin{cases" in label:
        tags.add("cases")
    if any(token in label for token in (r"\begin{align", r"\begin{aligned", r"\begin{gather", r"\begin{split")):
        tags.add("alignment")
    if any(token in label for token in (r"\mathbb", r"\mathcal", r"\mathfrak", r"\mathbf", r"\bm", r"\boldsymbol")):
        tags.add("math_alphabet")
    if any(token in label for token in (r"\text", r"\mathrm", r"\operatorname")):
        tags.add("text_run")
    if any(vector.get("is_horizontal_rule_candidate") for vector in vectors):
        tags.add("horizontal_rule")
    if any(glyph.get("is_script_size") for glyph in glyphs):
        tags.add("script_size_pdf_evidence")
    if any(glyph.get("is_math_font") for glyph in glyphs):
        tags.add("math_font_pdf_evidence")
    if len([glyph for glyph in glyphs if str(glyph.get("text", "")).strip()]) <= 1:
        tags.add("single_glyph_or_empty_text")
    return sorted(tags)


def _graph_stats(
    glyphs: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    coverage: list[str],
) -> dict[str, Any]:
    hints = Counter(str(edge.get("hint", "")) for edge in edges)
    fonts = Counter(str(glyph.get("normalized_font", "")) for glyph in glyphs if glyph.get("normalized_font"))
    return {
        "glyph_count": len(glyphs),
        "vector_count": len(vectors),
        "edge_count": len(edges),
        "edge_hint_counts": dict(sorted(hints.items())),
        "font_count": len(fonts),
        "top_fonts": fonts.most_common(12),
        "math_font_glyphs": sum(1 for glyph in glyphs if glyph.get("is_math_font")),
        "script_size_glyphs": sum(1 for glyph in glyphs if glyph.get("is_script_size")),
        "horizontal_rule_candidates": sum(1 for vector in vectors if vector.get("is_horizontal_rule_candidate")),
        "coverage_tags": coverage,
    }


def _manifest(
    inputs: list[Path],
    source_files: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    split: dict[str, Any],
    blockers: Counter[str],
) -> dict[str, Any]:
    cases = Counter(str(row.get("case", "")) for row in rows)
    kinds = Counter(str(row.get("kind", "")) for row in rows)
    coverage = Counter(tag for row in rows for tag in row.get("coverage_tags", []))
    edge_hints = Counter(
        hint
        for row in rows
        for hint, count in row.get("graph_stats", {}).get("edge_hint_counts", {}).items()
        for _ in range(int(count))
    )
    glyph_counts = [int(row.get("graph_stats", {}).get("glyph_count", 0)) for row in rows]
    vector_counts = [int(row.get("graph_stats", {}).get("vector_count", 0)) for row in rows]
    return {
        "schema_version": GRAPH_DATASET_SCHEMA_VERSION,
        "row_schema_version": GRAPH_ROW_SCHEMA_VERSION,
        "inputs": [str(path) for path in inputs],
        "source_files": source_files,
        "rows": len(rows),
        "cases": dict(sorted(cases.items())),
        "kinds": dict(sorted(kinds.items())),
        "coverage_tags": dict(sorted(coverage.items())),
        "edge_hint_totals": dict(sorted(edge_hints.items())),
        "blockers": dict(sorted(blockers.items())),
        "glyph_count": _summary(glyph_counts),
        "vector_count": _summary(vector_counts),
        "split_row_counts": split.get("row_counts", {}),
        "resource_versions": dict(DEFAULT_RESOURCE_VERSIONS),
        "dataset_hash": _stable_hash(
            {
                "schema": GRAPH_DATASET_SCHEMA_VERSION,
                "source_files": source_files,
                "row_hashes": [row.get("input_hash", "") for row in rows],
                "resource_versions": DEFAULT_RESOURCE_VERSIONS,
            }
        ),
        "notes": [
            "LaTeX source labels are dataset supervision only, never production inference input.",
            "Rows are exact instrumented PDF/source alignments; relation labels still require later MathML/SLT alignment.",
            "Coverage tags are audit buckets, not handwritten production formula recognition rules.",
        ],
    }


def _summary(values: list[int]) -> dict[str, Any]:
    if not values:
        return {"min": 0, "max": 0, "avg": 0.0, "p95": 0}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return {
        "min": ordered[0],
        "max": ordered[-1],
        "avg": round(sum(values) / len(values), 3),
        "p95": ordered[p95_index],
    }


def _source_file_payload(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "bytes": path.stat().st_size if path.exists() else 0,
        "sha256": _file_hash(path) if path.exists() else "",
    }


def _split_group_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            str(row.get("case", "")),
            str(row.get("tex_path", "")),
            str(row.get("page_num", "")),
        ]
    )


def _edge_rank(edge: dict[str, Any]) -> tuple[int, float, str]:
    priority = {
        "right_neighbor": 0,
        "superscript_zone": 1,
        "subscript_zone": 1,
        "radical_body_candidate": 1,
        "above_zone": 2,
        "below_zone": 2,
        "overlap_zone": 3,
        "far_context": 4,
        "above_rule_candidate": 5,
        "below_rule_candidate": 5,
        "fraction_bar_candidate": 6,
        "overline_candidate": 6,
    }.get(str(edge.get("hint", "")), 9)
    features = edge.get("features", {})
    if not isinstance(features, dict):
        features = {}
    dx = abs(_float(features.get("dx_over_height")))
    dy = abs(_float(features.get("dy_over_height")))
    return (priority, dx + dy, str(edge.get("edge_id", "")))


def _glyph_overlaps_rule(glyph: dict[str, Any], vector: dict[str, Any]) -> bool:
    return _overlap_ratio((glyph["bbox"][0], glyph["bbox"][2]), (vector["bbox"][0], vector["bbox"][2])) >= 0.05


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for edge in edges:
        result.setdefault(str(edge.get("edge_id", "")), edge)
    return [result[key] for key in sorted(result)]


def _overlap_ratio(first: tuple[float, float] | list[float], second: tuple[float, float] | list[float]) -> float:
    left = max(float(first[0]), float(second[0]))
    right = min(float(first[1]), float(second[1]))
    overlap = max(0.0, right - left)
    denom = max(min(float(first[1]) - float(first[0]), float(second[1]) - float(second[0])), 1e-6)
    return overlap / denom


def _normalize_font(font: str) -> str:
    value = str(font or "")
    if "+" in value and len(value.split("+", 1)[0]) <= 8:
        value = value.split("+", 1)[1]
    return value.replace(" ", "")


def _is_math_font(font: str) -> bool:
    compact = _normalize_font(font)
    return any(marker in compact for marker in MATH_FONT_MARKERS)


def _bbox(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        bbox = [round(float(item), 6) for item in value]
    except (TypeError, ValueError):
        return None
    if bbox[2] < bbox[0] or bbox[3] < bbox[1]:
        return None
    return bbox


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _compact_raw(item: dict[str, Any]) -> dict[str, Any]:
    keep = {}
    for key in ("text", "font", "size", "bbox", "type", "kind", "color", "stroke", "fill"):
        if key in item:
            keep[key] = item[key]
    return keep


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                yield value


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8",
        errors="ignore",
    )
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", required=True, help="instrumented_training_rows.jsonl")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--split-seed", default="tinybdmath-instrumented-v1")
    parser.add_argument("--train-ratio", type=float, default=0.8)
    parser.add_argument("--validation-ratio", type=float, default=0.1)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    result = build_graph_dataset(
        args.input,
        split_seed=args.split_seed,
        train_ratio=args.train_ratio,
        validation_ratio=args.validation_ratio,
        limit=args.limit,
    )
    write_graph_dataset(result, args.output_dir)
    print(json.dumps(result.manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

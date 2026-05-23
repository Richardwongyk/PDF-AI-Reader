"""Audit born-digital PDF structure exposed by MuPDF.

This tool is diagnostic. It extracts PDF facts for formula work without OCR and
without guessing LaTeX from regular expressions.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any
import xml.etree.ElementTree as ET

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.born_digital_math import BornDigitalMathAuditor, BornDigitalPage, MuPDFBornDigitalExtractor
from tools.formula_latex_audit import (
    _best_formula_matches,
    _extract_source_formulas,
)


def _default_cases() -> dict[str, Path]:
    test_dir = ROOT / "测试资料"
    return {
        "attention": test_dir / "Attention is all you need.pdf",
        "napkin": test_dir / "Napkin.pdf",
    }


def _default_latex_roots() -> dict[str, Path]:
    test_dir = ROOT / "测试资料"
    return {
        "attention": test_dir / "Attention is all you need LaTeX源代码和资料，用于与PDF版是否扫描正确进行对照",
        "napkin": test_dir / "Napkin LaTeX源代码，用于和原版PDF对照",
    }


def _font_counts(pages: list[BornDigitalPage]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for page in pages:
        for region in page.regions:
            for line in region.lines:
                for span in line.spans:
                    counts[span.font] += len(span.glyphs)
    return counts


def _font_resources(pages: list[BornDigitalPage]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for page in pages:
        for font in page.fonts:
            seen.setdefault(
                font.name,
                {
                    "name": font.name,
                    "type": font.font_type,
                    "extension": font.extension,
                    "encoding": font.encoding,
                    "resource_names": set(),
                    "pages": set(),
                },
            )
            seen[font.name]["resource_names"].add(font.resource_name)
            seen[font.name]["pages"].add(page.page_num)
    result: list[dict[str, Any]] = []
    for item in seen.values():
        result.append({
            "name": item["name"],
            "type": item["type"],
            "extension": item["extension"],
            "encoding": item["encoding"],
            "resource_names": sorted(item["resource_names"]),
            "pages": sorted(item["pages"]),
        })
    return sorted(result, key=lambda item: item["name"])


def _sample_regions(pages: list[BornDigitalPage], limit: int) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for page in pages:
        for region in page.regions:
            if region.kind != "text" or not region.text.strip():
                continue
            samples.append({
                "page": page.page_num,
                "bbox": region.bbox,
                "text": region.text[:240],
                "line_count": len(region.lines),
                "glyph_count": sum(len(span.glyphs) for line in region.lines for span in line.spans),
                "fonts": sorted({span.font for line in region.lines for span in line.spans})[:8],
            })
            if len(samples) >= limit:
                return samples
    return samples


def _math_evidence_regions(pages: list[BornDigitalPage]) -> list[Any]:
    auditor = BornDigitalMathAuditor()
    return [region for page in pages for region in auditor.evidence_regions(page)]


def _math_evidence_clusters(pages: list[BornDigitalPage]) -> list[Any]:
    auditor = BornDigitalMathAuditor()
    return [cluster for page in pages for cluster in auditor.evidence_clusters(page)]


def _math_context_clusters(pages: list[BornDigitalPage]) -> list[Any]:
    auditor = BornDigitalMathAuditor()
    return [cluster for page in pages for cluster in auditor.contextual_clusters(page)]


def _display_formula_regions(pages: list[BornDigitalPage]) -> list[Any]:
    auditor = BornDigitalMathAuditor()
    return [region for page in pages for region in auditor.display_formula_regions(page)]


def _math_evidence_summary(
    evidence_regions: list[Any],
    evidence_clusters: list[Any],
    context_clusters: list[Any],
    display_regions: list[Any],
    sample_limit: int,
) -> dict[str, Any]:
    evidence_counts: Counter[str] = Counter()
    for region in evidence_regions:
        evidence_counts.update(region.evidence)
    return {
        "region_count": len(evidence_regions),
        "cluster_count": len(evidence_clusters),
        "context_cluster_count": len(context_clusters),
        "display_region_count": len(display_regions),
        "evidence_counts": dict(sorted(evidence_counts.items())),
        "samples": [region.to_json() for region in evidence_regions[:sample_limit]],
        "cluster_samples": [cluster.to_json() for cluster in evidence_clusters[:sample_limit]],
        "context_cluster_samples": [cluster.to_json() for cluster in context_clusters[:sample_limit]],
        "display_region_samples": [region.to_json() for region in display_regions[:sample_limit]],
    }


def _latex_source_report(
    latex_root: Path | None,
    evidence_regions: list[Any],
    evidence_clusters: list[Any],
    context_clusters: list[Any],
    display_regions: list[Any],
    sample_limit: int,
) -> dict[str, Any] | None:
    if latex_root is None:
        return None
    if not latex_root.exists():
        return {"available": False, "reason": f"missing latex root: {latex_root}"}
    source_display, source_inline, tex_file_count = _extract_source_formulas(latex_root)
    source_formulas = source_display + source_inline
    evidence_texts = [region.text for region in evidence_regions if region.text.strip()]
    cluster_texts = [cluster.text for cluster in evidence_clusters if cluster.text.strip()]
    context_texts = [cluster.text for cluster in context_clusters if cluster.text.strip()]
    display_texts = [region.text for region in display_regions if region.text.strip()]
    matches, low_pdf, metrics = _best_formula_matches(
        source_formulas,
        evidence_texts,
        max_sources=5000,
        max_candidates_per_source=80,
    )
    cluster_matches, cluster_low_pdf, cluster_metrics = _best_formula_matches(
        source_formulas,
        cluster_texts,
        max_sources=5000,
        max_candidates_per_source=80,
    )
    context_matches, context_low_pdf, context_metrics = _best_formula_matches(
        source_formulas,
        context_texts,
        max_sources=5000,
        max_candidates_per_source=80,
    )
    display_matches, display_low_pdf, display_metrics = _best_formula_matches(
        source_formulas,
        display_texts,
        max_sources=5000,
        max_candidates_per_source=80,
    )
    display_source_matches, display_source_low_pdf, display_source_metrics = _best_formula_matches(
        source_display,
        display_texts,
        max_sources=5000,
        max_candidates_per_source=80,
    )
    return {
        "available": True,
        "latex_root": str(latex_root),
        "tex_file_count": tex_file_count,
        "source_formula_count": len(source_formulas),
        "source_display_count": len(source_display),
        "source_inline_count": len(source_inline),
        "evidence_text_count": len(evidence_texts),
        "cluster_text_count": len(cluster_texts),
        "context_cluster_text_count": len(context_texts),
        "display_region_text_count": len(display_texts),
        "source_near_match_rate": round(float(metrics["near_rate"]), 4),
        "source_weak_match_rate": round(float(metrics["weak_rate"]), 4),
        "average_best_similarity": round(float(metrics["average"]), 4),
        "source_near_match_count": int(metrics["near"]),
        "source_weak_match_count": int(metrics["weak"]),
        "cluster_source_near_match_rate": round(float(cluster_metrics["near_rate"]), 4),
        "cluster_source_weak_match_rate": round(float(cluster_metrics["weak_rate"]), 4),
        "cluster_average_best_similarity": round(float(cluster_metrics["average"]), 4),
        "cluster_source_near_match_count": int(cluster_metrics["near"]),
        "cluster_source_weak_match_count": int(cluster_metrics["weak"]),
        "context_source_near_match_rate": round(float(context_metrics["near_rate"]), 4),
        "context_source_weak_match_rate": round(float(context_metrics["weak_rate"]), 4),
        "context_average_best_similarity": round(float(context_metrics["average"]), 4),
        "context_source_near_match_count": int(context_metrics["near"]),
        "context_source_weak_match_count": int(context_metrics["weak"]),
        "display_source_near_match_rate": round(float(display_metrics["near_rate"]), 4),
        "display_source_weak_match_rate": round(float(display_metrics["weak_rate"]), 4),
        "display_average_best_similarity": round(float(display_metrics["average"]), 4),
        "display_source_near_match_count": int(display_metrics["near"]),
        "display_source_weak_match_count": int(display_metrics["weak"]),
        "display_only_near_match_rate": round(float(display_source_metrics["near_rate"]), 4),
        "display_only_weak_match_rate": round(float(display_source_metrics["weak_rate"]), 4),
        "display_only_average_best_similarity": round(float(display_source_metrics["average"]), 4),
        "display_only_near_match_count": int(display_source_metrics["near"]),
        "display_only_weak_match_count": int(display_source_metrics["weak"]),
        "sample_matches": matches[:sample_limit],
        "sample_cluster_matches": cluster_matches[:sample_limit],
        "sample_context_matches": context_matches[:sample_limit],
        "sample_display_matches": display_matches[:sample_limit],
        "sample_display_only_matches": display_source_matches[:sample_limit],
        "sample_low_similarity_evidence": low_pdf[:sample_limit],
        "sample_low_similarity_clusters": cluster_low_pdf[:sample_limit],
        "sample_low_similarity_context_clusters": context_low_pdf[:sample_limit],
        "sample_low_similarity_display_regions": display_low_pdf[:sample_limit],
        "sample_low_similarity_display_only_regions": display_source_low_pdf[:sample_limit],
    }


def audit_pdf(
    pdf_path: Path,
    start_page: int,
    max_pages: int,
    sample_limit: int,
    latex_root: Path | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    doc = fitz.open(pdf_path)
    try:
        extractor = MuPDFBornDigitalExtractor()
        pages = extractor.extract_document(doc, start_page=start_page, max_pages=max_pages)
    finally:
        doc.close()

    elapsed = time.perf_counter() - started
    warnings = Counter(warning for page in pages for warning in page.warnings)
    evidence_regions = _math_evidence_regions(pages)
    evidence_clusters = _math_evidence_clusters(pages)
    context_clusters = _math_context_clusters(pages)
    display_regions = _display_formula_regions(pages)
    report = {
        "pdf": str(pdf_path),
        "elapsed_sec": round(elapsed, 3),
        "pages": len(pages),
        "start_page": start_page,
        "glyph_count": sum(page.glyph_count for page in pages),
        "unknown_glyph_count": sum(page.unknown_glyph_count for page in pages),
        "vector_count": sum(page.vector_count for page in pages),
        "image_count": sum(page.image_count for page in pages),
        "warnings": dict(warnings),
        "top_fonts": _font_counts(pages).most_common(20),
        "font_resources": _font_resources(pages),
        "math_evidence": _math_evidence_summary(
            evidence_regions,
            evidence_clusters,
            context_clusters,
            display_regions,
            sample_limit,
        ),
        "samples": _sample_regions(pages, sample_limit),
    }
    source_report = _latex_source_report(
        latex_root,
        evidence_regions,
        evidence_clusters,
        context_clusters,
        display_regions,
        sample_limit,
    )
    if source_report is not None:
        report["latex_source_alignment"] = source_report
    return report


def audit_poppler(pdf_path: Path, start_page: int, max_pages: int, sample_limit: int) -> dict[str, Any]:
    pdftotext = _find_pdftotext()
    if pdftotext is None:
        return {"available": False, "reason": "pdftotext not found"}

    started = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmpdir:
        output = Path(tmpdir) / "bbox.html"
        cmd = [
            str(pdftotext),
            "-f",
            str(start_page + 1),
            "-l",
            str(start_page + max_pages),
            "-bbox-layout",
            "-enc",
            "UTF-8",
            str(pdf_path),
            str(output),
        ]
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            return {
                "available": True,
                "returncode": completed.returncode,
                "stderr": completed.stderr[-1000:],
            }
        root = ET.parse(output).getroot()

    elapsed = time.perf_counter() - started
    words: list[dict[str, Any]] = []
    for page in root.iter():
        if not page.tag.endswith("page"):
            continue
        page_num = int(page.attrib.get("number", "1")) - 1
        for word in page.iter():
            if not word.tag.endswith("word"):
                continue
            text = "".join(word.itertext())
            words.append({
                "page": page_num + start_page,
                "bbox": (
                    float(word.attrib.get("xMin", 0)),
                    float(word.attrib.get("yMin", 0)),
                    float(word.attrib.get("xMax", 0)),
                    float(word.attrib.get("yMax", 0)),
                ),
                "text": text,
            })
    return {
        "available": True,
        "elapsed_sec": round(elapsed, 3),
        "word_count": len(words),
        "samples": words[:sample_limit],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=sorted(_default_cases()), help="Bundled test PDF case.")
    parser.add_argument("--pdf", type=Path, help="PDF path. Overrides --case.")
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=6)
    parser.add_argument("--sample-limit", type=int, default=8)
    parser.add_argument("--poppler", action="store_true", help="Also run Poppler pdftotext -bbox-layout audit.")
    parser.add_argument("--latex-root", type=Path, help="Optional LaTeX source root for evidence-text alignment.")
    parser.add_argument("--no-source", action="store_true", help="Disable default bundled LaTeX source alignment.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = _default_cases()
    if args.pdf is not None:
        pdf_path = args.pdf
    elif args.case:
        pdf_path = cases[args.case]
    else:
        raise SystemExit("Provide --case or --pdf.")
    latex_root: Path | None = args.latex_root
    if latex_root is None and args.case and not args.no_source:
        latex_root = _default_latex_roots()[args.case]

    report = audit_pdf(
        pdf_path=pdf_path,
        start_page=max(args.start_page, 0),
        max_pages=max(args.max_pages, 1),
        sample_limit=max(args.sample_limit, 0),
        latex_root=latex_root,
    )
    if args.poppler:
        report["poppler"] = audit_poppler(
            pdf_path=pdf_path,
            start_page=max(args.start_page, 0),
            max_pages=max(args.max_pages, 1),
            sample_limit=max(args.sample_limit, 0),
        )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


def _find_pdftotext() -> Path | None:
    candidates = [
        shutil.which("pdftotext"),
        r"D:\texlive\bin\windows\pdftotext.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.exists():
            return path
    return None


if __name__ == "__main__":
    raise SystemExit(main())

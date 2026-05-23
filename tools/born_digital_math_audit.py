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


def _default_cases() -> dict[str, Path]:
    test_dir = ROOT / "测试资料"
    return {
        "attention": test_dir / "Attention is all you need.pdf",
        "napkin": test_dir / "Napkin.pdf",
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


def _math_evidence_summary(pages: list[BornDigitalPage], sample_limit: int) -> dict[str, Any]:
    auditor = BornDigitalMathAuditor()
    evidence_regions = [region for page in pages for region in auditor.evidence_regions(page)]
    evidence_counts: Counter[str] = Counter()
    for region in evidence_regions:
        evidence_counts.update(region.evidence)
    return {
        "region_count": len(evidence_regions),
        "evidence_counts": dict(sorted(evidence_counts.items())),
        "samples": [region.to_json() for region in evidence_regions[:sample_limit]],
    }


def audit_pdf(pdf_path: Path, start_page: int, max_pages: int, sample_limit: int) -> dict[str, Any]:
    started = time.perf_counter()
    doc = fitz.open(pdf_path)
    try:
        extractor = MuPDFBornDigitalExtractor()
        pages = extractor.extract_document(doc, start_page=start_page, max_pages=max_pages)
    finally:
        doc.close()

    elapsed = time.perf_counter() - started
    warnings = Counter(warning for page in pages for warning in page.warnings)
    return {
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
        "math_evidence": _math_evidence_summary(pages, sample_limit),
        "samples": _sample_regions(pages, sample_limit),
    }


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

    report = audit_pdf(
        pdf_path=pdf_path,
        start_page=max(args.start_page, 0),
        max_pages=max(args.max_pages, 1),
        sample_limit=max(args.sample_limit, 0),
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

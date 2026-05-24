"""Benchmark formula OCR on bundled Attention/Napkin fixtures.

This tool measures the real local OCR path without changing application state:
- parse PDF pages into DocumentBlock formula candidates,
- crop formula images from their PDF bboxes,
- run the selected MathOCR backend with persistent cache disabled,
- measure a separate cache-hit path with a temporary cache.

It is intentionally small and diagnostic; accuracy gates remain in
``tools/formula_latex_audit.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.formula_detector import Pix2TextMFDDetector
from src.core.math_ocr import MathOCR, _FormulaOcrCache
from src.core.models import BlockType, DocumentBlock
from src.core.pdf_engine import DocumentChunker
from tools.formula_latex_audit import _cases


@dataclass
class FormulaSample:
    block_id: str
    page_num: int
    bbox: tuple[float, float, float, float]
    source_text: str
    words: int
    math_markers: int
    selection_reason: str
    image_bytes: int
    latex: str
    score: float | None
    warnings: list[str]


@dataclass
class BenchmarkReport:
    case: str
    pdf: str
    backend: str
    requested_backend: str
    model: str
    status: str
    error: str
    pages_scanned: int
    formula_blocks: int
    image_samples: int
    parse_sec: float
    crop_sec: float
    availability_sec: float
    ocr_sec: float
    ocr_per_formula_sec: float
    temp_cache_hit_sec: float
    temp_cache_hit_per_formula_sec: float
    cached_samples: int
    model_samples: int
    samples: list[FormulaSample]


class _NoCache:
    """Cache stub for measuring true OCR cost."""

    @staticmethod
    def hash_image(image_bytes: bytes) -> str:
        return f"{len(image_bytes)}:{hash(image_bytes)}"

    def get(self, image_hash: str, model: str = "") -> None:
        return None

    def put(self, image_hash: str, latex: str, model: str) -> None:
        return None


def _select_cases(case_name: str) -> list[Any]:
    cases = _cases()
    if case_name == "all":
        return cases
    return [case for case in cases if case.name == case_name]


def _parse_formula_blocks(
    pdf: Path,
    start_page: int,
    max_pages: int,
    sample_limit: int,
    pure_formula_only: bool,
) -> tuple[int, list[DocumentBlock], float]:
    chunker = DocumentChunker()
    blocks: list[DocumentBlock] = []
    started = time.perf_counter()
    doc = fitz.open(pdf)
    try:
        first_page = max(0, min(start_page, doc.page_count))
        last_page = min(doc.page_count, first_page + max_pages) if max_pages > 0 else doc.page_count
        for page_num in range(first_page, last_page):
            page_blocks = [
                block for block in chunker.chunk_page(doc, page_num)
                if block.block_type == BlockType.FORMULA
            ]
            if pure_formula_only:
                page_blocks = [
                    block for block in page_blocks
                    if _formula_sample_profile(block).selection_reason == "pure_formula_like"
                ]
            blocks.extend(page_blocks)
            if len(blocks) >= sample_limit * 3:
                break
        return max(0, last_page - first_page), blocks, time.perf_counter() - started
    finally:
        doc.close()


def _crop_samples(pdf: Path, blocks: list[DocumentBlock], sample_limit: int, dpi: int) -> tuple[list[bytes], list[DocumentBlock], float]:
    images: list[bytes] = []
    image_blocks: list[DocumentBlock] = []
    started = time.perf_counter()
    doc = fitz.open(pdf)
    try:
        for block in blocks:
            if len(images) >= sample_limit:
                break
            try:
                image = Pix2TextMFDDetector._crop_bbox_image(
                    doc,
                    block.page_num,
                    block.bbox,
                    dpi=dpi,
                    pad=6.0,
                )
            except Exception:
                image = b""
            if image:
                images.append(image)
                image_blocks.append(block)
        return images, image_blocks, time.perf_counter() - started
    finally:
        doc.close()


@dataclass(frozen=True)
class _FormulaSampleProfile:
    words: int
    math_markers: int
    selection_reason: str


def _formula_sample_profile(block: DocumentBlock) -> _FormulaSampleProfile:
    text = (block.content or "").strip()
    words = len(re.findall(r"[A-Za-z]{3,}", text))
    math_markers = sum(text.count(ch) for ch in "=+-*/^_()[]{}∈≤≥×·√∑∫→∞")
    has_latex_command = bool(re.search(r"\\[A-Za-z]+", text))
    has_sentence_punctuation = bool(re.search(r"[.!?。！？]", text))
    if has_latex_command and words <= 6:
        reason = "pure_formula_like"
    elif words <= 1 and math_markers >= 2 and not has_sentence_punctuation:
        reason = "pure_formula_like"
    elif words <= 8 and "=" in text and not has_sentence_punctuation:
        reason = "mixed_but_short"
    else:
        reason = "mixed_text_formula"
    return _FormulaSampleProfile(
        words=words,
        math_markers=math_markers,
        selection_reason=reason,
    )


def _benchmark_case(
    case: Any,
    backend: str,
    model: str,
    start_page: int,
    max_pages: int,
    sample_limit: int,
    dpi: int,
    pure_formula_only: bool,
) -> BenchmarkReport:
    pages_scanned, formula_blocks, parse_sec = _parse_formula_blocks(
        case.pdf,
        start_page=start_page,
        max_pages=max_pages,
        sample_limit=sample_limit,
        pure_formula_only=pure_formula_only,
    )
    images, image_blocks, crop_sec = _crop_samples(
        case.pdf,
        formula_blocks,
        sample_limit=sample_limit,
        dpi=dpi,
    )

    MathOCR._instance = None
    MathOCR.set_default_backend_config(backend, model_name=model)
    ocr = MathOCR()
    ocr._cache = _NoCache()

    started = time.perf_counter()
    try:
        available = ocr.is_available
        availability_error = ""
    except Exception as exc:
        available = False
        availability_error = str(exc)
    availability_sec = time.perf_counter() - started
    if not available or not images:
        ocr_results = []
        ocr_sec = 0.0
    else:
        started = time.perf_counter()
        ocr_results = ocr.recognize_batch_with_metadata(
            images,
            max_uncached=len(images),
        )
        ocr_sec = time.perf_counter() - started
    if len(ocr_results) < len(images):
        from src.core.formula_recognizers import FormulaRecognitionResult
        ocr_results.extend(
            FormulaRecognitionResult(latex="") for _ in range(len(images) - len(ocr_results))
        )

    temp_cache_dir = Path(tempfile.mkdtemp(prefix="formula_ocr_benchmark_", dir=ROOT / "test_artifacts"))
    temp_cache = _FormulaOcrCache(str(temp_cache_dir / "formula_ocr_cache.db"))
    for image, result in zip(images, ocr_results, strict=False):
        latex = result.latex
        if latex:
            temp_cache.put(temp_cache.hash_image(image), latex, ocr._cache_namespace())
    ocr._cache = temp_cache
    started = time.perf_counter()
    ocr.recognize_batch(images, max_uncached=0)
    temp_cache_hit_sec = time.perf_counter() - started

    samples: list[FormulaSample] = []
    cached_samples = 0
    for block, image, result in zip(image_blocks, images, ocr_results, strict=False):
        profile = _formula_sample_profile(block)
        warnings = list(result.warnings)
        if "cache_hit" in warnings:
            cached_samples += 1
        samples.append(
            FormulaSample(
                block_id=block.id,
                page_num=block.page_num,
                bbox=block.bbox,
                source_text=(block.content or "")[:180],
                words=profile.words,
                math_markers=profile.math_markers,
                selection_reason=profile.selection_reason,
                image_bytes=len(image),
                latex=(result.latex or "")[:240],
                score=result.score,
                warnings=warnings,
            )
        )
    count = max(len(images), 1)
    return BenchmarkReport(
        case=case.name,
        pdf=str(case.pdf),
        backend=ocr.backend_name,
        requested_backend=backend,
        model=model,
        status="ok" if available else "skipped",
        error="" if available else (availability_error or "backend_unavailable"),
        pages_scanned=pages_scanned,
        formula_blocks=len(formula_blocks),
        image_samples=len(images),
        parse_sec=round(parse_sec, 3),
        crop_sec=round(crop_sec, 3),
        availability_sec=round(availability_sec, 3),
        ocr_sec=round(ocr_sec, 3),
        ocr_per_formula_sec=round(ocr_sec / count, 3),
        temp_cache_hit_sec=round(temp_cache_hit_sec, 3),
        temp_cache_hit_per_formula_sec=round(temp_cache_hit_sec / count, 4),
        cached_samples=cached_samples,
        model_samples=max(len(images) - cached_samples, 0),
        samples=samples,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark local formula OCR on bundled fixtures.")
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="all")
    parser.add_argument("--backend", default="pix2text-mfr")
    parser.add_argument(
        "--backends",
        default="",
        help="Comma-separated recognizer backends to benchmark; overrides --backend.",
    )
    parser.add_argument("--model", default="PP-FormulaNet_plus-S")
    parser.add_argument("--start-page", type=int, default=0)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--sample-limit", type=int, default=8)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument(
        "--pure-formula-only",
        action="store_true",
        help="Only benchmark formula blocks that look like pure formula crops.",
    )
    parser.add_argument("--output", default="test_artifacts/formula_ocr_benchmark.json")
    args = parser.parse_args()

    backends = [
        item.strip()
        for item in (args.backends or args.backend).split(",")
        if item.strip()
    ]
    reports = []
    for case in _select_cases(args.case):
        for backend in backends:
            reports.append(
                _benchmark_case(
                    case,
                    backend=backend,
                    model=args.model,
                    start_page=max(0, args.start_page),
                    max_pages=max(1, args.max_pages),
                    sample_limit=max(1, args.sample_limit),
                    dpi=max(96, args.dpi),
                    pure_formula_only=bool(args.pure_formula_only),
                )
            )
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "args": vars(args),
        "reports": [asdict(report) for report in reports],
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

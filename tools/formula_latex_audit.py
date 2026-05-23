"""Audit PDF formula extraction against bundled LaTeX sources.

The report is intentionally diagnostic. It does not claim exact source/PDF
alignment; it gives a reproducible baseline for:
- how many formula-like snippets exist in the LaTeX source,
- how many formula/image blocks the current PDF parser finds,
- how many scanned/image formulas still need OCR,
- which frequent source math commands are missing from extracted PDF text.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.formula_detector import Pix2TextMFDDetector
from src.core.models import BlockType
from src.core.pdf_engine import DocumentChunker


DISPLAY_ENVS = (
    "equation",
    "equation*",
    "align",
    "align*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "split",
    "cases",
)

MATH_COMMAND_RE = re.compile(r"\\[A-Za-z]+")
SOURCE_FORMULA_PATTERNS = [
    re.compile(r"\\\[(.+?)\\\]", re.DOTALL),
    re.compile(r"\$\$(.+?)\$\$", re.DOTALL),
    re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.DOTALL),
]


@dataclass
class CasePaths:
    name: str
    pdf: Path
    latex_root: Path


@dataclass
class FormulaReport:
    name: str
    pdf: str
    latex_root: str
    elapsed_sec: float
    pages: int
    source_tex_files: int
    source_formula_snippets: int
    source_display_snippets: int
    source_inline_snippets: int
    source_top_commands: list[tuple[str, int]]
    pdf_blocks: int
    pdf_formula_blocks: int
    pdf_image_blocks: int
    pdf_scanned_formula_blocks: int
    pdf_ocr_formula_blocks: int
    pdf_needs_ocr_blocks: int
    pdf_top_commands: list[tuple[str, int]]
    missing_common_source_commands: list[str]
    recovered_common_source_commands: list[str]
    common_source_command_recall: float
    sample_source_formulas: list[str]
    sample_pdf_formulas: list[str]
    sample_needs_ocr_blocks: list[dict[str, Any]]


def _cases() -> list[CasePaths]:
    test_dir = ROOT / "测试资料"
    return [
        CasePaths(
            name="attention",
            pdf=test_dir / "Attention is all you need.pdf",
            latex_root=test_dir / "Attention is all you need LaTeX源代码和资料，用于与PDF版是否扫描正确进行对照",
        ),
        CasePaths(
            name="napkin",
            pdf=test_dir / "Napkin.pdf",
            latex_root=test_dir / "Napkin LaTeX源代码，用于和原版PDF对照",
        ),
    ]


def _strip_comments(tex: str) -> str:
    lines: list[str] = []
    for line in tex.splitlines():
        escaped = False
        kept: list[str] = []
        for ch in line:
            if ch == "%" and not escaped:
                break
            kept.append(ch)
            escaped = ch == "\\" and not escaped
            if ch != "\\":
                escaped = False
        lines.append("".join(kept))
    return "\n".join(lines)


def _extract_source_formulas(latex_root: Path) -> tuple[list[str], list[str], int]:
    display: list[str] = []
    inline: list[str] = []
    tex_files = [p for p in latex_root.rglob("*.tex") if p.is_file()]
    for path in tex_files:
        try:
            text = _strip_comments(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for env in DISPLAY_ENVS:
            escaped_env = re.escape(env)
            pattern = re.compile(
                rf"\\begin\{{{escaped_env}\}}(.+?)\\end\{{{escaped_env}\}}",
                re.DOTALL,
            )
            display.extend(m.group(1).strip() for m in pattern.finditer(text))
        display.extend(m.group(1).strip() for m in SOURCE_FORMULA_PATTERNS[0].finditer(text))
        display.extend(m.group(1).strip() for m in SOURCE_FORMULA_PATTERNS[1].finditer(text))
        inline.extend(m.group(1).strip() for m in SOURCE_FORMULA_PATTERNS[2].finditer(text))
    return display, inline, len(tex_files)


def _command_counts(snippets: list[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for snippet in snippets:
        counts.update(MATH_COMMAND_RE.findall(snippet))
    return counts


def _parse_pdf_blocks(pdf: Path, run_mfd: bool, mfd_pages: list[int] | None) -> tuple[int, list[Any]]:
    doc = fitz.open(pdf)
    try:
        chunker = DocumentChunker()
        blocks = chunker.chunk(doc)
        if run_mfd:
            detector = Pix2TextMFDDetector(dpi=200)
            if mfd_pages is not None:
                formulas = detector.detect_specific_pages(doc, mfd_pages)
                original = detector.detect_specific_pages
                detector.detect_specific_pages = lambda _doc, _pages: formulas  # type: ignore[method-assign]
                try:
                    blocks = detector.apply_to_blocks(blocks, doc)
                finally:
                    detector.detect_specific_pages = original  # type: ignore[method-assign]
            else:
                blocks = detector.apply_to_blocks(blocks, doc)
        return doc.page_count, blocks
    finally:
        doc.close()


def _sample(items: list[str], limit: int = 8) -> list[str]:
    compact = [" ".join(item.split()) for item in items if item and item.strip()]
    return [item[:240] for item in compact[:limit]]


def _audit_case(case: CasePaths, run_mfd: bool, mfd_pages: list[int] | None) -> FormulaReport:
    start = time.perf_counter()
    if not case.pdf.exists():
        raise FileNotFoundError(case.pdf)
    if not case.latex_root.exists():
        raise FileNotFoundError(case.latex_root)

    source_display, source_inline, tex_files = _extract_source_formulas(case.latex_root)
    source_formulas = source_display + source_inline
    source_commands = _command_counts(source_formulas)

    page_count, blocks = _parse_pdf_blocks(case.pdf, run_mfd=run_mfd, mfd_pages=mfd_pages)
    formula_blocks = [b for b in blocks if b.block_type == BlockType.FORMULA]
    image_blocks = [b for b in blocks if b.block_type == BlockType.IMAGE]
    scanned_blocks = [
        b for b in formula_blocks
        if b.metadata.get("source") == "image_or_scan"
    ]
    ocr_blocks = [
        b for b in formula_blocks
        if b.metadata.get("mfr_recognized") or b.metadata.get("formula_ocr")
    ]
    needs_ocr = [
        b for b in formula_blocks
        if b.metadata.get("needs_ocr")
    ]
    pdf_commands = _command_counts([b.content for b in formula_blocks])
    source_common = {
        cmd for cmd, count in source_commands.items()
        if count >= 2 and cmd not in {r"\label", r"\ref", r"\cite", r"\begin", r"\end"}
    }
    recovered = sorted(cmd for cmd in source_common if cmd in pdf_commands)
    missing = sorted(cmd for cmd in source_common if cmd not in pdf_commands)[:40]
    recall = len(recovered) / len(source_common) if source_common else 1.0

    return FormulaReport(
        name=case.name,
        pdf=str(case.pdf),
        latex_root=str(case.latex_root),
        elapsed_sec=round(time.perf_counter() - start, 3),
        pages=page_count,
        source_tex_files=tex_files,
        source_formula_snippets=len(source_formulas),
        source_display_snippets=len(source_display),
        source_inline_snippets=len(source_inline),
        source_top_commands=source_commands.most_common(25),
        pdf_blocks=len(blocks),
        pdf_formula_blocks=len(formula_blocks),
        pdf_image_blocks=len(image_blocks),
        pdf_scanned_formula_blocks=len(scanned_blocks),
        pdf_ocr_formula_blocks=len(ocr_blocks),
        pdf_needs_ocr_blocks=len(needs_ocr),
        pdf_top_commands=pdf_commands.most_common(25),
        missing_common_source_commands=missing,
        recovered_common_source_commands=recovered,
        common_source_command_recall=round(recall, 3),
        sample_source_formulas=_sample(source_formulas),
        sample_pdf_formulas=_sample([b.content for b in formula_blocks]),
        sample_needs_ocr_blocks=[
            {
                "id": b.id,
                "page": b.page_num + 1,
                "bbox": b.bbox,
                "content": b.content[:120],
                "metadata": b.metadata,
            }
            for b in needs_ocr[:10]
        ],
    )


def _parse_page_list(value: str) -> list[int] | None:
    if not value:
        return None
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start = int(start_s)
            end = int(end_s)
            pages.update(range(start - 1, end))
        else:
            pages.add(int(part) - 1)
    return sorted(p for p in pages if p >= 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit formula extraction against LaTeX sources.")
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="all")
    parser.add_argument("--mfd", action="store_true", help="Run Pix2Text MFD/MFR on the selected pages.")
    parser.add_argument(
        "--mfd-pages",
        default="",
        help="1-based page list/ranges for --mfd, for example '1,3,8-10'. Empty means all pages.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "test_artifacts" / "formula_audit.json",
    )
    args = parser.parse_args()

    selected = [case for case in _cases() if args.case in ("all", case.name)]
    mfd_pages = _parse_page_list(args.mfd_pages)
    reports = [_audit_case(case, run_mfd=args.mfd, mfd_pages=mfd_pages) for case in selected]
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mfd_enabled": args.mfd,
        "mfd_pages": [p + 1 for p in mfd_pages] if mfd_pages is not None else None,
        "reports": [asdict(report) for report in reports],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

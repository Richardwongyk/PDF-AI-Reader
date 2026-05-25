"""Born-digital PDF structure extraction for math-aware indexing.

This module only records facts exposed by the PDF engine: glyph text/CID,
fonts, bounding boxes, spans, lines, vector blocks, and images. It deliberately
does not guess LaTeX from prose with regular expressions.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

import fitz


RegionKind = Literal["text", "image", "vector"]
WarningCode = Literal["unknown_glyph", "empty_text_block"]
MathEvidence = Literal["math_font", "math_symbol", "script_size", "near_vector", "unknown_glyph"]
DisplayFormulaEvidence = Literal[
    "math_font",
    "math_symbol",
    "script_size",
    "near_vector",
    "unknown_glyph",
    "math_density",
    "centered_line",
    "short_line",
    "equation_label",
]


@dataclass(frozen=True)
class PdfFontResource:
    xref: int
    extension: str
    font_type: str
    name: str
    resource_name: str
    encoding: str
    embedded: int


@dataclass(frozen=True)
class PdfGlyph:
    text: str
    font: str
    size: float
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float] | None = None
    cid: int | None = None
    synthetic: bool = False
    glyph_name: str = ""

    @property
    def is_unknown(self) -> bool:
        return self.text in {"", "\ufffd", "�"} or self.text.startswith("cid:")


@dataclass(frozen=True)
class PdfSpan:
    text: str
    font: str
    size: float
    bbox: tuple[float, float, float, float]
    glyphs: tuple[PdfGlyph, ...]


@dataclass(frozen=True)
class PdfLine:
    text: str
    bbox: tuple[float, float, float, float]
    writing_mode: int
    direction: tuple[float, float]
    spans: tuple[PdfSpan, ...]


@dataclass(frozen=True)
class PdfRegion:
    page_num: int
    kind: RegionKind
    bbox: tuple[float, float, float, float]
    text: str = ""
    lines: tuple[PdfLine, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BornDigitalPage:
    page_num: int
    page_size: tuple[float, float]
    regions: tuple[PdfRegion, ...]
    warnings: tuple[str, ...]
    fonts: tuple[PdfFontResource, ...] = ()
    extractor: str = "pymupdf_rawdict"

    @property
    def glyph_count(self) -> int:
        return sum(len(span.glyphs) for region in self.regions for line in region.lines for span in line.spans)

    @property
    def unknown_glyph_count(self) -> int:
        return sum(
            1
            for region in self.regions
            for line in region.lines
            for span in line.spans
            for glyph in span.glyphs
            if glyph.is_unknown
        )

    @property
    def vector_count(self) -> int:
        return sum(1 for region in self.regions if region.kind == "vector")

    @property
    def image_count(self) -> int:
        return sum(1 for region in self.regions if region.kind == "image")

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MathEvidenceRegion:
    page_num: int
    bbox: tuple[float, float, float, float]
    text: str
    evidence: tuple[MathEvidence, ...]
    fonts: tuple[str, ...]
    glyph_count: int
    unknown_glyph_count: int
    vector_count: int
    source: str = "pdf_structure_evidence"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MathEvidenceCluster:
    page_num: int
    bbox: tuple[float, float, float, float]
    text: str
    region_count: int
    vector_count: int
    evidence: tuple[MathEvidence, ...]
    source: str = "pdf_structure_cluster"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DisplayFormulaRegion:
    page_num: int
    bbox: tuple[float, float, float, float]
    text: str
    line_count: int
    vector_count: int
    evidence: tuple[DisplayFormulaEvidence, ...]
    confidence: float
    source: str = "pdf_structure_display_region"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaSemanticResult:
    latex: str
    confidence: float
    evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    source: str = "pdf_structure_semantic_layout_v1"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaRegionDiagnostics:
    page_num: int
    bbox: tuple[float, float, float, float]
    text: str
    glyph_count: int
    math_glyph_count: int
    math_density: float
    roman_letter_count: int
    operator_count: int
    digit_count: int
    vector_count: int
    line_count: int
    line_alignment_spread: float
    classification: str
    risks: tuple[str, ...]
    evidence: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FormulaSegmentationConfig:
    """Page-relative knobs for born-digital display formula segmentation."""

    vector_margin: float = 4.0
    min_math_density: float = 0.12
    strong_formula_density: float = 0.32
    centered_tolerance_ratio: float = 0.13
    max_seed_width_ratio: float = 0.74
    max_seed_body_width_ratio: float = 0.72
    max_full_width_ratio: float = 0.86
    min_indent_ratio: float = 0.06
    max_merge_vertical_gap_ratio: float = 0.95
    max_merge_horizontal_gap_ratio: float = 0.34
    min_confidence: float = 0.50


class MuPDFBornDigitalExtractor:
    """Extract raw born-digital structure with MuPDF/PyMuPDF."""

    def __init__(self, flags: int | None = None) -> None:
        self.flags = flags if flags is not None else _rawdict_flags()

    def extract_page(self, page: fitz.Page, page_num: int) -> BornDigitalPage:
        raw: dict[str, Any] = page.get_text("rawdict", flags=self.flags)
        regions: list[PdfRegion] = []
        warnings: set[str] = set()

        for block in raw.get("blocks", []):
            region = self._extract_region(block, page_num)
            if region is None:
                continue
            if region.kind == "text" and not region.text.strip():
                warnings.add("empty_text_block")
            if any(glyph.is_unknown for line in region.lines for span in line.spans for glyph in span.glyphs):
                warnings.add("unknown_glyph")
            regions.append(region)

        return BornDigitalPage(
            page_num=page_num,
            page_size=(float(page.rect.width), float(page.rect.height)),
            regions=tuple(regions),
            warnings=tuple(sorted(warnings)),
            fonts=_page_fonts(page),
        )

    def extract_document(
        self,
        doc: fitz.Document,
        start_page: int = 0,
        max_pages: int | None = None,
    ) -> list[BornDigitalPage]:
        end_page = doc.page_count if max_pages is None else min(doc.page_count, start_page + max_pages)
        return [self.extract_page(doc[page_num], page_num) for page_num in range(start_page, end_page)]

    def _extract_region(self, block: dict[str, Any], page_num: int) -> PdfRegion | None:
        block_type = int(block.get("type", -1))
        bbox = _bbox_tuple(block.get("bbox", (0, 0, 0, 0)))
        if block_type == 0:
            lines = tuple(self._extract_line(line) for line in block.get("lines", []))
            text = "\n".join(line.text for line in lines if line.text)
            return PdfRegion(page_num=page_num, kind="text", bbox=bbox, text=text, lines=lines)
        if block_type == 1:
            return PdfRegion(
                page_num=page_num,
                kind="image",
                bbox=bbox,
                metadata={
                    "width": block.get("width"),
                    "height": block.get("height"),
                    "ext": block.get("ext"),
                },
            )
        if block_type == 3:
            return PdfRegion(
                page_num=page_num,
                kind="vector",
                bbox=bbox,
                metadata={
                    "stroked": bool(block.get("stroked", False)),
                    "isrect": bool(block.get("isrect", False)),
                    "continues": bool(block.get("continues", False)),
                },
            )
        return None

    def _extract_line(self, line: dict[str, Any]) -> PdfLine:
        spans = tuple(self._extract_span(span) for span in line.get("spans", []))
        text = "".join(span.text for span in spans)
        return PdfLine(
            text=text,
            bbox=_bbox_tuple(line.get("bbox", _union_bbox([span.bbox for span in spans]))),
            writing_mode=int(line.get("wmode", 0)),
            direction=_direction_tuple(line.get("dir", (1.0, 0.0))),
            spans=spans,
        )

    def _extract_span(self, span: dict[str, Any]) -> PdfSpan:
        font = str(span.get("font", ""))
        size = float(span.get("size", 0.0) or 0.0)
        glyphs = tuple(_extract_glyph(char, font, size, span) for char in span.get("chars", []))
        text = "".join(glyph.text for glyph in glyphs)
        return PdfSpan(
            text=text,
            font=font,
            size=size,
            bbox=_bbox_tuple(span.get("bbox", _union_bbox([glyph.bbox for glyph in glyphs]))),
            glyphs=glyphs,
        )


class BornDigitalMathAuditor:
    """Find structure-backed math evidence without generating LaTeX."""

    def __init__(
        self,
        vector_margin: float = 2.0,
        segmentation: FormulaSegmentationConfig | None = None,
    ) -> None:
        self.vector_margin = vector_margin
        self.segmentation = segmentation or FormulaSegmentationConfig()

    def evidence_regions(self, page: BornDigitalPage) -> list[MathEvidenceRegion]:
        vectors = [region for region in page.regions if region.kind == "vector"]
        regions: list[MathEvidenceRegion] = []
        for region in page.regions:
            if region.kind != "text":
                continue
            regions.extend(self._line_evidence_regions(page.page_num, region, vectors))
        return regions

    def evidence_clusters(self, page: BornDigitalPage) -> list[MathEvidenceCluster]:
        regions = self.evidence_regions(page)
        if not regions:
            return []
        vectors = [region for region in page.regions if region.kind == "vector"]
        clusters: list[list[MathEvidenceRegion]] = []
        for region in sorted(regions, key=lambda item: (item.bbox[1], item.bbox[0])):
            target = _best_cluster(region, clusters)
            if target is None:
                clusters.append([region])
            else:
                target.append(region)
        return [_cluster_summary(page.page_num, cluster, vectors) for cluster in clusters]

    def _line_evidence_regions(
        self,
        page_num: int,
        region: PdfRegion,
        vectors: list[PdfRegion],
    ) -> list[MathEvidenceRegion]:
        regions: list[MathEvidenceRegion] = []
        for line in region.lines:
            for glyph_group in _math_glyph_groups(line):
                evidence = _glyph_group_evidence(glyph_group)
                bbox = _union_bbox([glyph.bbox for glyph in glyph_group])
                near_vectors = [
                    vector
                    for vector in vectors
                    if _bbox_intersects(_expand_bbox(bbox, self.vector_margin), vector.bbox)
                ]
                if near_vectors:
                    evidence.add("near_vector")
                if not evidence:
                    continue
                regions.append(
                    MathEvidenceRegion(
                        page_num=page_num,
                        bbox=bbox,
                        text="".join(glyph.text for glyph in glyph_group),
                        evidence=tuple(sorted(evidence)),
                        fonts=tuple(sorted({glyph.font for glyph in glyph_group if glyph.font})),
                        glyph_count=len(glyph_group),
                        unknown_glyph_count=sum(1 for glyph in glyph_group if glyph.is_unknown),
                        vector_count=len(near_vectors),
                    )
                )
        return regions

    def contextual_clusters(self, page: BornDigitalPage) -> list[MathEvidenceCluster]:
        clusters = self.evidence_clusters(page)
        if not clusters:
            return []
        text_lines = [line for region in page.regions if region.kind == "text" for line in region.lines]
        return [_expand_cluster_with_context(cluster, text_lines) for cluster in clusters]

    def display_formula_regions(self, page: BornDigitalPage) -> list[DisplayFormulaRegion]:
        """Segment display-style formula regions from born-digital PDF facts."""

        segmenter = DisplayFormulaSegmenter(self.segmentation)
        return segmenter.segment(page)

    def region_diagnostics(
        self,
        page: BornDigitalPage,
        regions: list[DisplayFormulaRegion] | None = None,
    ) -> list[FormulaRegionDiagnostics]:
        """Report quality signals for candidate display formula regions."""

        targets = regions if regions is not None else self.display_formula_regions(page)
        return [_region_diagnostics(page, region) for region in targets]


class DisplayFormulaSegmenter:
    """Group PDF lines and vector marks into display formula regions."""

    def __init__(self, config: FormulaSegmentationConfig | None = None) -> None:
        self.config = config or FormulaSegmentationConfig()

    def segment(self, page: BornDigitalPage) -> list[DisplayFormulaRegion]:
        vectors = [region for region in page.regions if region.kind == "vector"]
        line_infos = _page_line_infos(page, vectors, self.config)
        if not line_infos:
            return []
        layout = _estimate_page_layout(line_infos, page.page_size)
        line_infos = [_line_info_with_layout_evidence(info, layout, self.config) for info in line_infos]
        candidates = [info for info in line_infos if _is_display_seed(info, layout, self.config)]
        if not candidates:
            return []
        groups: list[list[_DisplayLineInfo]] = []
        for info in sorted(candidates, key=lambda item: (item.line.bbox[1], item.line.bbox[0])):
            target = _best_display_group(info, groups, self.config, layout)
            if target is None:
                groups.append([info])
            else:
                target.append(info)
        _attach_display_parts(groups, line_infos, self.config, layout)
        _attach_display_labels(groups, line_infos, self.config, layout)
        merged: list[DisplayFormulaRegion] = []
        for group in groups:
            region = _display_group_summary(page.page_num, group, vectors, self.config)
            if region.confidence >= self.config.min_confidence:
                merged.append(region)
        return _dedupe_display_regions(merged)


class PdfFormulaSemanticReconstructor:
    """Recover evidence-backed LaTeX fragments from born-digital formula regions."""

    def reconstruct(
        self,
        page: BornDigitalPage,
        region: DisplayFormulaRegion,
    ) -> FormulaSemanticResult:
        glyphs = self._region_glyphs(page, region)
        warnings: set[str] = set()
        evidence: set[str] = set(region.evidence)
        if any(glyph.is_unknown for glyph in glyphs):
            warnings.add("unknown_glyph")
        if not glyphs:
            return FormulaSemanticResult(
                latex="",
                confidence=0.0,
                evidence=tuple(sorted(evidence)),
                warnings=("empty_region",),
            )
        vectors = self._region_vectors(page, region)
        formula_like = _glyphs_look_like_formula_expression(glyphs)
        fraction = self._best_fraction_vector(glyphs, vectors) if formula_like else None
        if fraction is not None:
            latex = self._render_with_fraction(glyphs, fraction, vectors)
            evidence.add("fraction_vector")
            confidence = min(1.0, region.confidence + 0.06)
        else:
            latex = self._render_glyphs(glyphs, vectors)
            confidence = region.confidence
        if not formula_like:
            warnings.add("table_or_text_like_region")
        if any(glyph.text == "√" for glyph in glyphs):
            evidence.add("radical_glyph")
        if _has_script_size_pattern([glyph for glyph in glyphs if not glyph.text.isspace()]):
            evidence.add("script_layout")
        return FormulaSemanticResult(
            latex=_cleanup_latex(latex),
            confidence=round(confidence, 3),
            evidence=tuple(sorted(evidence)),
            warnings=tuple(sorted(warnings)),
        )

    def _region_glyphs(
        self,
        page: BornDigitalPage,
        region: DisplayFormulaRegion,
    ) -> list[PdfGlyph]:
        glyphs: list[PdfGlyph] = []
        expanded = _expand_bbox(region.bbox, 1.0)
        for pdf_region in page.regions:
            if pdf_region.kind != "text":
                continue
            for line in pdf_region.lines:
                if not _bbox_intersects(line.bbox, expanded):
                    continue
                line_glyphs = [
                    glyph
                    for span in line.spans
                    for glyph in span.glyphs
                    if glyph.text and not glyph.text.isspace() and _bbox_intersects(glyph.bbox, expanded)
                ]
                if not line_glyphs:
                    continue
                if _glyphs_look_like_equation_label(line_glyphs, page.page_size):
                    continue
                glyphs.extend(line_glyphs)
        return glyphs

    def _region_vectors(
        self,
        page: BornDigitalPage,
        region: DisplayFormulaRegion,
    ) -> list[PdfRegion]:
        expanded = _expand_bbox(region.bbox, 1.0)
        return [
            item
            for item in page.regions
            if item.kind == "vector" and _bbox_intersects(item.bbox, expanded)
        ]

    def _best_fraction_vector(
        self,
        glyphs: list[PdfGlyph],
        vectors: list[PdfRegion],
    ) -> PdfRegion | None:
        best: PdfRegion | None = None
        best_width = 0.0
        for vector in vectors:
            width = _bbox_width(vector.bbox)
            height = _bbox_height(vector.bbox)
            if width < 6.0 or height > max(1.5, width * 0.18):
                continue
            y = (vector.bbox[1] + vector.bbox[3]) / 2.0
            above = [glyph for glyph in glyphs if _glyph_overlaps_x(glyph, vector.bbox, 1.0) and _glyph_center(glyph)[1] < y - 0.8]
            below = [glyph for glyph in glyphs if _glyph_overlaps_x(glyph, vector.bbox, 1.0) and _glyph_center(glyph)[1] > y + 0.8]
            if not above or not below:
                continue
            if not _glyphs_look_like_local_fraction_part(above):
                continue
            if not _glyphs_look_like_local_fraction_part(below):
                continue
            if width > best_width:
                best = vector
                best_width = width
        return best

    def _render_with_fraction(
        self,
        glyphs: list[PdfGlyph],
        fraction: PdfRegion,
        vectors: list[PdfRegion],
    ) -> str:
        y = (fraction.bbox[1] + fraction.bbox[3]) / 2.0
        numerator = [
            glyph for glyph in glyphs
            if _glyph_overlaps_x(glyph, fraction.bbox, 1.0) and _glyph_center(glyph)[1] < y - 0.8
        ]
        denominator = [
            glyph for glyph in glyphs
            if (
                _glyph_overlaps_x(glyph, fraction.bbox, 2.0)
                and (_glyph_center(glyph)[1] > y + 0.4 or glyph.text == "√")
            )
        ]
        fraction_glyphs = set(numerator + denominator)
        before = [
            glyph for glyph in glyphs
            if glyph not in fraction_glyphs and _glyph_center(glyph)[0] < fraction.bbox[0] - 0.6
        ]
        after = [
            glyph for glyph in glyphs
            if glyph not in fraction_glyphs and _glyph_center(glyph)[0] > fraction.bbox[2] + 0.6
        ]
        parts = [
            self._render_glyphs(before, vectors),
            r"\frac{" + self._render_glyphs(numerator, vectors) + "}{" + self._render_glyphs(denominator, vectors) + "}",
            self._render_glyphs(after, vectors),
        ]
        return " ".join(part for part in parts if part.strip())

    def _render_glyphs(self, glyphs: list[PdfGlyph], vectors: list[PdfRegion]) -> str:
        glyphs = [glyph for glyph in glyphs if glyph.text and not glyph.text.isspace()]
        if not glyphs:
            return ""
        if glyphs[0].text == "√" and len(glyphs) > 1:
            return r"\sqrt{" + self._render_glyphs(glyphs[1:], vectors) + "}"
        lines = _cluster_glyph_lines(glyphs)
        rendered = [self._render_line(line, vectors) for line in lines]
        return r" \\ ".join(item for item in rendered if item)

    def _render_line(self, glyphs: list[PdfGlyph], vectors: list[PdfRegion]) -> str:
        ordered = sorted(glyphs, key=lambda glyph: (glyph.bbox[0], glyph.bbox[1]))
        tokens: list[_LatexToken] = []
        i = 0
        while i < len(ordered):
            glyph = ordered[i]
            if tokens:
                relation = _script_relation(tokens[-1].glyph, glyph)
                if relation:
                    script_glyphs, i = _take_script_glyphs(ordered, i, tokens[-1].glyph, relation)
                    script_text = self._render_line(script_glyphs, vectors)
                    tokens[-1] = replace(
                        tokens[-1],
                        text=f"{tokens[-1].text}{relation}{{{script_text}}}",
                    )
                    continue
            if glyph.text == "√":
                radicand, next_i = _take_radical_glyphs(ordered, i, vectors)
                if radicand:
                    tokens.append(_LatexToken(r"\sqrt{" + self._render_line(radicand, vectors) + "}", radicand[-1]))
                    i = next_i
                    continue
            word, next_i = _take_roman_word(ordered, i)
            if word:
                text = "".join(glyph.text for glyph in word)
                token = rf"\mathrm{{{text}}}" if len(text) > 1 else _glyph_to_latex(word[0])
                tokens.append(_LatexToken(token, word[-1]))
                i = next_i
                continue
            tokens.append(_LatexToken(_glyph_to_latex(glyph), glyph))
            i += 1
        return _join_latex_tokens([token.text for token in tokens])


@dataclass(frozen=True)
class _DisplayLineInfo:
    order: int
    region: PdfRegion
    line: PdfLine
    glyph_count: int
    math_glyph_count: int
    math_density: float
    near_vectors: tuple[PdfRegion, ...]
    evidence: tuple[DisplayFormulaEvidence, ...]
    equation_label: bool

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self.line.bbox


@dataclass(frozen=True)
class _PageLayout:
    page_size: tuple[float, float]
    body_left: float
    body_right: float

    @property
    def body_width(self) -> float:
        return max(self.body_right - self.body_left, 1.0)

    @property
    def body_center(self) -> float:
        return (self.body_left + self.body_right) / 2.0


@dataclass(frozen=True)
class _LatexToken:
    text: str
    glyph: PdfGlyph


def _page_lines(page: BornDigitalPage) -> list[tuple[int, PdfRegion, PdfLine]]:
    lines: list[tuple[int, PdfRegion, PdfLine]] = []
    order = 0
    for region in page.regions:
        if region.kind != "text":
            continue
        for line in region.lines:
            if not line.text.strip():
                continue
            lines.append((order, region, line))
            order += 1
    return lines


def _page_line_infos(
    page: BornDigitalPage,
    vectors: list[PdfRegion],
    config: FormulaSegmentationConfig,
) -> list[_DisplayLineInfo]:
    return [
        _display_line_info(order, region, line, page.page_size, vectors, config)
        for order, region, line in _page_lines(page)
    ]


def _display_line_info(
    order: int,
    region: PdfRegion,
    line: PdfLine,
    page_size: tuple[float, float],
    vectors: list[PdfRegion],
    config: FormulaSegmentationConfig,
) -> _DisplayLineInfo:
    glyphs = [
        glyph
        for span in line.spans
        for glyph in span.glyphs
        if glyph.text and not glyph.text.isspace()
    ]
    glyph_count = len(glyphs)
    math_glyph_count = sum(1 for glyph in glyphs if _glyph_has_math_evidence(glyph))
    math_density = math_glyph_count / glyph_count if glyph_count else 0.0
    near_vectors = tuple(
        vector
        for vector in vectors
        if _bbox_intersects(_expand_bbox(line.bbox, config.vector_margin), vector.bbox)
    )
    evidence: set[DisplayFormulaEvidence] = set()
    glyph_evidence = _glyph_group_evidence(glyphs)
    evidence.update(glyph_evidence)
    if math_density >= config.min_math_density:
        evidence.add("math_density")
    if near_vectors:
        evidence.add("near_vector")
    equation_label = _line_looks_like_equation_label(line, page_size)
    if equation_label:
        evidence.add("equation_label")
    return _DisplayLineInfo(
        order=order,
        region=region,
        line=line,
        glyph_count=glyph_count,
        math_glyph_count=math_glyph_count,
        math_density=math_density,
        near_vectors=near_vectors,
        evidence=tuple(sorted(evidence)),
        equation_label=equation_label,
    )


def _line_info_with_layout_evidence(
    info: _DisplayLineInfo,
    layout: _PageLayout,
    config: FormulaSegmentationConfig,
) -> _DisplayLineInfo:
    evidence = set(info.evidence)
    if _line_is_centered(info.bbox, layout, config):
        evidence.add("centered_line")
    else:
        evidence.discard("centered_line")
    if _line_is_short(info.bbox, layout, config):
        evidence.add("short_line")
    else:
        evidence.discard("short_line")
    return replace(info, evidence=tuple(sorted(evidence)))


def _is_display_seed(
    info: _DisplayLineInfo,
    layout: _PageLayout,
    config: FormulaSegmentationConfig,
) -> bool:
    if info.glyph_count == 0 or info.equation_label:
        return False
    if _line_looks_like_footnote_marker(info):
        return False
    evidence = set(info.evidence)
    width = _bbox_width(info.bbox)
    page_width = max(layout.page_size[0], 1.0)
    if width > page_width * config.max_full_width_ratio:
        return False
    structure_evidence = evidence.intersection({"math_font", "math_symbol", "script_size", "unknown_glyph", "near_vector"})
    if not structure_evidence:
        return False
    display_position = _line_has_display_position(info.bbox, layout, config)
    if info.math_density >= config.strong_formula_density and display_position:
        if _bbox_width(info.bbox) < layout.body_width * 0.08 and not _line_is_centered(info.bbox, layout, config):
            return False
        return width <= layout.body_width * config.max_seed_body_width_ratio or bool(info.near_vectors)
    if "near_vector" in evidence and info.math_density >= config.min_math_density:
        return display_position and width >= layout.body_width * 0.08
    if {"centered_line", "short_line"} <= evidence and info.math_density >= config.min_math_density:
        return (
            display_position
            and width >= layout.body_width * 0.10
            and width <= layout.body_width * config.max_seed_body_width_ratio
        )
    return False


def _best_display_group(
    info: _DisplayLineInfo,
    groups: list[list[_DisplayLineInfo]],
    config: FormulaSegmentationConfig,
    layout: _PageLayout,
) -> list[_DisplayLineInfo] | None:
    best: list[_DisplayLineInfo] | None = None
    best_gap = float("inf")
    for group in groups:
        if not _same_display_formula_group(info, group, config, layout):
            continue
        gap = _bbox_gap(info.bbox, _union_bbox([item.bbox for item in group]))
        if gap < best_gap:
            best_gap = gap
            best = group
    return best


def _same_display_formula_group(
    info: _DisplayLineInfo,
    group: list[_DisplayLineInfo],
    config: FormulaSegmentationConfig,
    layout: _PageLayout,
) -> bool:
    group_box = _union_bbox([item.bbox for item in group])
    line_height = _median_positive([_bbox_height(item.bbox) for item in group] + [_bbox_height(info.bbox)], 10.0)
    vertical_gap = max(0.0, max(info.bbox[1], group_box[1]) - min(info.bbox[3], group_box[3]))
    if vertical_gap > line_height * config.max_merge_vertical_gap_ratio:
        return False
    horizontal_gap = max(0.0, max(info.bbox[0], group_box[0]) - min(info.bbox[2], group_box[2]))
    if horizontal_gap > layout.page_size[0] * config.max_merge_horizontal_gap_ratio:
        return False
    if _bbox_intersects(_expand_bbox(info.bbox, config.vector_margin), _expand_bbox(group_box, config.vector_margin)):
        return True
    if any(vector == other for vector in info.near_vectors for item in group for other in item.near_vectors):
        return True
    if _horizontal_centers_close(info.bbox, group_box, layout, config):
        return True
    horizontal_overlap = min(info.bbox[2], group_box[2]) - max(info.bbox[0], group_box[0])
    return horizontal_overlap >= -layout.page_size[0] * 0.04


def _attach_display_parts(
    groups: list[list[_DisplayLineInfo]],
    line_infos: list[_DisplayLineInfo],
    config: FormulaSegmentationConfig,
    layout: _PageLayout,
) -> None:
    grouped_orders = {item.order for group in groups for item in group}
    candidates = [
        info
        for info in line_infos
        if info.order not in grouped_orders
        and not info.equation_label
        and not _line_looks_like_footnote_marker(info)
        and _line_can_attach_as_formula_part(info)
    ]
    for group in groups:
        changed = True
        while changed:
            changed = False
            group_box = _union_bbox([item.bbox for item in group])
            for info in candidates:
                if info in group:
                    continue
                if not _formula_part_belongs_to_group(info, group_box, config, layout):
                    continue
                group.append(info)
                changed = True


def _line_can_attach_as_formula_part(info: _DisplayLineInfo) -> bool:
    evidence = set(info.evidence)
    if info.math_density >= 0.80:
        return True
    return bool(evidence.intersection({"near_vector", "script_size", "math_symbol", "unknown_glyph"})) and info.math_density >= 0.20


def _formula_part_belongs_to_group(
    info: _DisplayLineInfo,
    group_box: tuple[float, float, float, float],
    config: FormulaSegmentationConfig,
    layout: _PageLayout,
) -> bool:
    line_height = max(_bbox_height(info.bbox), 1.0)
    vertical_overlap = min(info.bbox[3], group_box[3]) - max(info.bbox[1], group_box[1])
    vertical_gap = max(0.0, max(info.bbox[1], group_box[1]) - min(info.bbox[3], group_box[3]))
    horizontal_gap = max(0.0, max(info.bbox[0], group_box[0]) - min(info.bbox[2], group_box[2]))
    if vertical_overlap < -line_height * config.max_merge_vertical_gap_ratio and vertical_gap > line_height:
        return False
    if horizontal_gap > layout.body_width * 0.12 and not _bbox_intersects(
        _expand_bbox(info.bbox, config.vector_margin),
        _expand_bbox(group_box, config.vector_margin),
    ):
        return False
    return True


def _attach_display_labels(
    groups: list[list[_DisplayLineInfo]],
    line_infos: list[_DisplayLineInfo],
    config: FormulaSegmentationConfig,
    layout: _PageLayout,
) -> None:
    labels = [info for info in line_infos if info.equation_label]
    if not labels:
        return
    for group in groups:
        group_box = _union_bbox([item.bbox for item in group])
        for label in labels:
            if label in group:
                continue
            if not _label_belongs_to_group(label, group_box, config, layout):
                continue
            group.append(label)


def _label_belongs_to_group(
    label: _DisplayLineInfo,
    group_box: tuple[float, float, float, float],
    config: FormulaSegmentationConfig,
    layout: _PageLayout,
) -> bool:
    if label.bbox[0] < layout.body_right - layout.body_width * 0.18:
        return False
    vertical_overlap = min(label.bbox[3], group_box[3]) - max(label.bbox[1], group_box[1])
    line_height = max(_bbox_height(label.bbox), 1.0)
    group_height = max(_bbox_height(group_box), 1.0)
    vertical_gap = max(0.0, max(label.bbox[1], group_box[1]) - min(label.bbox[3], group_box[3]))
    if vertical_overlap >= -max(line_height, group_height) * 0.35:
        return True
    return vertical_gap <= line_height * config.max_merge_vertical_gap_ratio


def _display_group_summary(
    page_num: int,
    group: list[_DisplayLineInfo],
    vectors: list[PdfRegion],
    config: FormulaSegmentationConfig,
) -> DisplayFormulaRegion:
    ordered = sorted(group, key=lambda item: (item.line.bbox[1], item.line.bbox[0], item.order))
    bbox = _union_bbox([item.bbox for item in ordered])
    nearby_vectors = [
        vector
        for vector in vectors
        if _bbox_intersects(_expand_bbox(bbox, config.vector_margin), vector.bbox)
    ]
    evidence: set[DisplayFormulaEvidence] = set()
    for item in ordered:
        evidence.update(item.evidence)
    text = " ".join(item.line.text.strip() for item in ordered if item.line.text.strip())
    return DisplayFormulaRegion(
        page_num=page_num,
        bbox=bbox,
        text=text,
        line_count=len(ordered),
        vector_count=len(nearby_vectors),
        evidence=tuple(sorted(evidence)),
        confidence=_display_confidence(ordered, nearby_vectors),
    )


def _display_confidence(lines: list[_DisplayLineInfo], vectors: list[PdfRegion]) -> float:
    evidence: set[DisplayFormulaEvidence] = set()
    for line in lines:
        evidence.update(line.evidence)
    score = 0.0
    if "math_font" in evidence:
        score += 0.24
    if "math_symbol" in evidence:
        score += 0.16
    if "script_size" in evidence:
        score += 0.12
    if "near_vector" in evidence or vectors:
        score += 0.18
    if "math_density" in evidence:
        score += 0.16
    if "centered_line" in evidence:
        score += 0.08
    if "short_line" in evidence:
        score += 0.05
    if any(line.equation_label for line in lines):
        score += 0.03
    if len(lines) > 1:
        score += 0.06
    return round(min(score, 1.0), 3)


def _dedupe_display_regions(regions: list[DisplayFormulaRegion]) -> list[DisplayFormulaRegion]:
    result: list[DisplayFormulaRegion] = []
    for region in sorted(regions, key=lambda item: (-item.confidence, item.bbox[1], item.bbox[0])):
        if any(_bbox_overlap_ratio(region.bbox, kept.bbox) > 0.82 for kept in result):
            continue
        result.append(region)
    return sorted(result, key=lambda item: (item.bbox[1], item.bbox[0]))


def _region_diagnostics(
    page: BornDigitalPage,
    region: DisplayFormulaRegion,
) -> FormulaRegionDiagnostics:
    expanded = _expand_bbox(region.bbox, 1.0)
    lines: list[PdfLine] = []
    glyphs: list[PdfGlyph] = []
    for pdf_region in page.regions:
        if pdf_region.kind != "text":
            continue
        for line in pdf_region.lines:
            if not _bbox_intersects(line.bbox, expanded):
                continue
            line_glyphs = [
                glyph
                for span in line.spans
                for glyph in span.glyphs
                if glyph.text and not glyph.text.isspace() and _bbox_intersects(glyph.bbox, expanded)
            ]
            if not line_glyphs:
                continue
            lines.append(line)
            glyphs.extend(line_glyphs)
    vectors = [
        item for item in page.regions
        if item.kind == "vector" and _bbox_intersects(item.bbox, expanded)
    ]
    glyph_count = len(glyphs)
    math_glyph_count = sum(1 for glyph in glyphs if _glyph_has_math_evidence(glyph))
    roman_letter_count = sum(1 for glyph in glyphs if glyph.text.isalpha() and _is_latin_text_font(glyph.font))
    operator_count = sum(1 for glyph in glyphs if _is_math_symbol(glyph.text) or glyph.text in "=+-*/^_()[]{}|<>√∈∑∫")
    digit_count = sum(1 for glyph in glyphs if glyph.text.isdigit())
    math_density = math_glyph_count / glyph_count if glyph_count else 0.0
    line_alignment_spread = _line_alignment_spread(lines)

    risks: set[str] = set()
    evidence: set[str] = set(region.evidence)
    if not glyphs:
        risks.add("empty_region")
    if any(glyph.is_unknown for glyph in glyphs):
        risks.add("unknown_glyph")
    if not _glyphs_look_like_formula_expression(glyphs):
        risks.add("table_or_text_like_region")
    if _looks_like_prose_region(glyphs, math_density):
        risks.add("prose_like_region")
    if _looks_like_tabular_region(lines, line_alignment_spread):
        risks.add("tabular_alignment")
    if region.vector_count or vectors:
        evidence.add("vector_structure")
    if operator_count:
        evidence.add("operator_glyph")
    if digit_count:
        evidence.add("numeric_glyph")
    classification = _diagnostic_classification(
        risks=risks,
        math_density=math_density,
        operator_count=operator_count,
        vectors=vectors,
        line_count=len(lines),
    )
    return FormulaRegionDiagnostics(
        page_num=region.page_num,
        bbox=region.bbox,
        text=region.text,
        glyph_count=glyph_count,
        math_glyph_count=math_glyph_count,
        math_density=round(math_density, 3),
        roman_letter_count=roman_letter_count,
        operator_count=operator_count,
        digit_count=digit_count,
        vector_count=len(vectors),
        line_count=len(lines),
        line_alignment_spread=round(line_alignment_spread, 3),
        classification=classification,
        risks=tuple(sorted(risks)),
        evidence=tuple(sorted(evidence)),
    )


def _line_alignment_spread(lines: list[PdfLine]) -> float:
    if len(lines) <= 1:
        return 0.0
    lefts = [line.bbox[0] for line in lines]
    centers = [(line.bbox[0] + line.bbox[2]) / 2.0 for line in lines]
    return min(max(lefts) - min(lefts), max(centers) - min(centers))


def _looks_like_prose_region(glyphs: list[PdfGlyph], math_density: float) -> bool:
    if not glyphs:
        return False
    text = "".join(glyph.text for glyph in sorted(glyphs, key=lambda item: (item.bbox[1], item.bbox[0])))
    letters = [glyph for glyph in glyphs if glyph.text.isalpha()]
    roman_letters = [glyph for glyph in letters if _is_latin_text_font(glyph.font)]
    spaces = text.count(" ")
    punctuation = sum(1 for ch in text if ch in ",.;:")
    if len(roman_letters) >= 18 and math_density < 0.45 and spaces >= 2:
        return True
    return len(roman_letters) >= 24 and punctuation >= 2 and math_density < 0.65


def _looks_like_tabular_region(lines: list[PdfLine], line_alignment_spread: float) -> bool:
    if len(lines) < 3:
        return False
    widths = [_bbox_width(line.bbox) for line in lines]
    median_width = _median_positive(widths, 0.0)
    if median_width <= 0:
        return False
    short_rows = sum(1 for width in widths if width <= median_width * 1.25)
    return short_rows >= 3 and line_alignment_spread <= median_width * 0.20


def _diagnostic_classification(
    risks: set[str],
    math_density: float,
    operator_count: int,
    vectors: list[PdfRegion],
    line_count: int,
) -> str:
    if "empty_region" in risks:
        return "invalid"
    if "prose_like_region" in risks or "tabular_alignment" in risks:
        return "review"
    if "table_or_text_like_region" in risks and math_density < 0.55 and not vectors:
        return "review"
    if math_density >= 0.55 or vectors or operator_count >= 2 or line_count > 1:
        return "formula_candidate"
    return "review"


def _is_latin_text_font(font: str) -> bool:
    normalized = font.lower()
    math_markers = ("math", "cmmi", "cmsy", "cmex", "stix", "xits", "euler")
    if any(marker in normalized for marker in math_markers):
        return False
    return True


def _estimate_page_layout(
    line_infos: list[_DisplayLineInfo],
    page_size: tuple[float, float],
) -> _PageLayout:
    body_candidates = [
        info.bbox
        for info in line_infos
        if info.glyph_count >= 20
        and not info.equation_label
        and _bbox_width(info.bbox) >= page_size[0] * 0.35
    ]
    if not body_candidates:
        return _PageLayout(page_size=page_size, body_left=page_size[0] * 0.08, body_right=page_size[0] * 0.92)
    lefts = sorted(box[0] for box in body_candidates)
    rights = sorted(box[2] for box in body_candidates)
    body_left = lefts[len(lefts) // 2]
    body_right = rights[len(rights) // 2]
    if body_right <= body_left:
        return _PageLayout(page_size=page_size, body_left=page_size[0] * 0.08, body_right=page_size[0] * 0.92)
    return _PageLayout(page_size=page_size, body_left=body_left, body_right=body_right)


def _line_is_centered(
    bbox: tuple[float, float, float, float],
    layout: _PageLayout,
    config: FormulaSegmentationConfig,
) -> bool:
    center = (bbox[0] + bbox[2]) / 2.0
    return abs(center - layout.body_center) <= layout.body_width * config.centered_tolerance_ratio


def _line_has_display_position(
    bbox: tuple[float, float, float, float],
    layout: _PageLayout,
    config: FormulaSegmentationConfig,
) -> bool:
    if _line_is_centered(bbox, layout, config):
        return True
    left_margin = max(bbox[0] - layout.body_left, 0.0)
    right_margin = max(layout.body_right - bbox[2], 0.0)
    return (
        _bbox_width(bbox) <= layout.body_width * config.max_seed_body_width_ratio
        and left_margin >= layout.body_width * config.min_indent_ratio
        and right_margin >= layout.body_width * config.min_indent_ratio
    )


def _line_is_short(
    bbox: tuple[float, float, float, float],
    layout: _PageLayout,
    config: FormulaSegmentationConfig,
) -> bool:
    width = _bbox_width(bbox)
    if width <= layout.body_width * config.max_seed_body_width_ratio:
        return True
    return (
        bbox[0] >= layout.body_left + layout.body_width * config.min_indent_ratio
        and bbox[2] <= layout.body_right - layout.body_width * config.min_indent_ratio
    )


def _line_looks_like_equation_label(line: PdfLine, page_size: tuple[float, float]) -> bool:
    text = "".join(glyph.text for span in line.spans for glyph in span.glyphs if glyph.text and not glyph.text.isspace())
    if len(text) < 3 or len(text) > 8:
        return False
    if not (text.startswith("(") and text.endswith(")")):
        return False
    inner = text[1:-1]
    if not inner or not all(ch.isdigit() or ch in {".", "-"} for ch in inner):
        return False
    return line.bbox[0] >= page_size[0] * 0.68


def _line_looks_like_footnote_marker(info: _DisplayLineInfo) -> bool:
    text = "".join(glyph.text for span in info.line.spans for glyph in span.glyphs if glyph.text and not glyph.text.isspace())
    if len(text) == 1 and text in {"*", "∗", "†", "‡", "§"}:
        return True
    if 2 <= len(text) <= 3 and all(ch in {"*", "∗", "†", "‡", "§"} for ch in text):
        return True
    math_glyphs = [
        glyph
        for span in info.line.spans
        for glyph in span.glyphs
        if glyph.text and not glyph.text.isspace() and _glyph_has_math_evidence(glyph)
    ]
    if math_glyphs and all(glyph.text in {"*", "∗", "†", "‡", "§"} for glyph in math_glyphs):
        alpha_runs = [part for part in text.replace(".", " ").replace(",", " ").split() if any(ch.isalpha() for ch in part)]
        if any(len(part) >= 2 for part in alpha_runs):
            return True
    return False


def _horizontal_centers_close(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
    layout: _PageLayout,
    config: FormulaSegmentationConfig,
) -> bool:
    left_center = (left[0] + left[2]) / 2.0
    right_center = (right[0] + right[2]) / 2.0
    return abs(left_center - right_center) <= layout.body_width * config.centered_tolerance_ratio


def _bbox_width(bbox: tuple[float, float, float, float]) -> float:
    return max(bbox[2] - bbox[0], 0.0)


def _bbox_height(bbox: tuple[float, float, float, float]) -> float:
    return max(bbox[3] - bbox[1], 0.0)


def _median_positive(values: list[float], default: float) -> float:
    positives = sorted(value for value in values if value > 0)
    if not positives:
        return default
    return positives[len(positives) // 2]


def _bbox_overlap_ratio(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    ix0 = max(left[0], right[0])
    iy0 = max(left[1], right[1])
    ix1 = min(left[2], right[2])
    iy1 = min(left[3], right[3])
    intersection = max(ix1 - ix0, 0.0) * max(iy1 - iy0, 0.0)
    if intersection <= 0:
        return 0.0
    left_area = max(_bbox_width(left) * _bbox_height(left), 1.0)
    right_area = max(_bbox_width(right) * _bbox_height(right), 1.0)
    return intersection / min(left_area, right_area)


def _rawdict_flags() -> int:
    flags = int(getattr(fitz, "TEXTFLAGS_RAWDICT", 0) or 0)
    for name in (
        "TEXT_ACCURATE_BBOXES",
        "TEXT_COLLECT_VECTORS",
        "TEXT_USE_CID_FOR_UNKNOWN_UNICODE",
    ):
        flags |= int(getattr(fitz, name, 0) or 0)
    return flags


def _font_resource(item: Any) -> PdfFontResource:
    values = tuple(item)
    padded = values + ("",) * max(0, 7 - len(values))
    return PdfFontResource(
        xref=int(padded[0] or 0),
        extension=str(padded[1] or ""),
        font_type=str(padded[2] or ""),
        name=str(padded[3] or ""),
        resource_name=str(padded[4] or ""),
        encoding=str(padded[5] or ""),
        embedded=int(padded[6] or 0),
    )


def _page_fonts(page: Any) -> tuple[PdfFontResource, ...]:
    get_fonts = getattr(page, "get_fonts", None)
    if get_fonts is None:
        return ()
    try:
        fonts = get_fonts(full=True)
    except (TypeError, ValueError, RuntimeError):
        return ()
    return tuple(_font_resource(item) for item in fonts)


def _extract_glyph(char: dict[str, Any], font: str, size: float, span: dict[str, Any]) -> PdfGlyph:
    raw_text = char.get("c", "")
    text = _glyph_text(raw_text)
    return PdfGlyph(
        text=text,
        cid=_glyph_cid(raw_text),
        font=font,
        size=size,
        bbox=_bbox_tuple(char.get("bbox", span.get("bbox", (0, 0, 0, 0)))),
        origin=_origin_tuple(char.get("origin")),
        synthetic=bool(char.get("synthetic", False)),
        glyph_name=_glyph_name(char),
    )


def _glyph_text(value: Any) -> str:
    if isinstance(value, int):
        return f"cid:{value}"
    return str(value or "")


def _glyph_cid(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


def _glyph_name(char: dict[str, Any]) -> str:
    for key in ("glyph_name", "glyph", "name"):
        value = char.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _bbox_tuple(value: Any) -> tuple[float, float, float, float]:
    try:
        values = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0, 0.0)
    if len(values) != 4:
        return (0.0, 0.0, 0.0, 0.0)
    return values


def _origin_tuple(value: Any) -> tuple[float, float] | None:
    if value is None:
        return None
    try:
        values = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return None
    if len(values) != 2:
        return None
    return values


def _direction_tuple(value: Any) -> tuple[float, float]:
    try:
        values = tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return (1.0, 0.0)
    if len(values) != 2:
        return (1.0, 0.0)
    return values


def _union_bbox(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    boxes = [box for box in boxes if box != (0.0, 0.0, 0.0, 0.0)]
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _is_math_font_name(font: str) -> bool:
    font_name = font.split("+")[-1].lower()
    families = (
        "cmmi",
        "cmsy",
        "cmex",
        "msam",
        "msbm",
        "stix",
        "xits",
        "latinmodernmath",
        "cambria math",
        "symbol",
    )
    return any(family in font_name for family in families)


def _is_math_symbol(text: str) -> bool:
    if not text:
        return False
    codepoint = ord(text[0])
    return (
        0x2200 <= codepoint <= 0x22FF
        or 0x27C0 <= codepoint <= 0x27EF
        or 0x2980 <= codepoint <= 0x29FF
        or 0x2A00 <= codepoint <= 0x2AFF
        or 0x1D400 <= codepoint <= 0x1D7FF
    )


def _has_script_size_pattern(glyphs: list[PdfGlyph]) -> bool:
    sizes = [glyph.size for glyph in glyphs if glyph.size > 0 and not glyph.text.isspace()]
    if len(sizes) < 2:
        return False
    max_size = max(sizes)
    min_size = min(sizes)
    if min_size >= max_size * 0.86:
        return False
    base_centers = [
        (glyph.bbox[1] + glyph.bbox[3]) / 2.0
        for glyph in glyphs
        if glyph.size >= max_size * 0.86 and not glyph.text.isspace()
    ]
    script_centers = [
        (glyph.bbox[1] + glyph.bbox[3]) / 2.0
        for glyph in glyphs
        if glyph.size < max_size * 0.86 and not glyph.text.isspace()
    ]
    if not base_centers or not script_centers:
        return False
    base_center = sum(base_centers) / len(base_centers)
    return any(abs(center - base_center) >= max(1.2, max_size * 0.12) for center in script_centers)


def _glyph_group_evidence(glyphs: list[PdfGlyph]) -> set[MathEvidence]:
    evidence: set[MathEvidence] = set()
    if any(_is_math_font_name(glyph.font) for glyph in glyphs):
        evidence.add("math_font")
    if any(_is_math_symbol(glyph.text) for glyph in glyphs):
        evidence.add("math_symbol")
    if _has_script_size_pattern(glyphs):
        evidence.add("script_size")
    if any(glyph.is_unknown for glyph in glyphs):
        evidence.add("unknown_glyph")
    return evidence


def _math_glyph_groups(line: PdfLine) -> list[list[PdfGlyph]]:
    groups: list[list[PdfGlyph]] = []
    current: list[PdfGlyph] = []
    for span in line.spans:
        for glyph in span.glyphs:
            if glyph.text.isspace():
                _flush_group(groups, current)
                continue
            if _glyph_has_math_evidence(glyph):
                current.append(glyph)
                continue
            if current and _is_short_math_connector(glyph.text):
                current.append(glyph)
                continue
            _flush_group(groups, current)
    _flush_group(groups, current)
    return groups


def _flush_group(groups: list[list[PdfGlyph]], current: list[PdfGlyph]) -> None:
    if not current:
        return
    if _glyph_group_evidence(current):
        groups.append(list(current))
    current.clear()


def _glyph_has_math_evidence(glyph: PdfGlyph) -> bool:
    return _is_math_font_name(glyph.font) or _is_math_symbol(glyph.text) or glyph.is_unknown


def _glyphs_look_like_formula_expression(glyphs: list[PdfGlyph]) -> bool:
    text = "".join(glyph.text for glyph in glyphs if glyph.text and not glyph.text.isspace())
    if not text:
        return False
    letters = sum(1 for ch in text if ch.isalpha())
    digits = sum(1 for ch in text if ch.isdigit())
    operators = sum(1 for ch in text if ch in "=+-−*/·×^_()[]{}|<>√∈∑∫")
    math_fonts = sum(1 for glyph in glyphs if _glyph_has_math_evidence(glyph))
    roman_letters = sum(
        1
        for glyph in glyphs
        if glyph.text.isalpha() and _is_roman_math_word_glyph(glyph)
    )
    if operators >= 2 and math_fonts >= 1:
        return True
    if operators >= 3 and digits + math_fonts >= 2:
        return True
    if letters >= 10 and roman_letters / max(letters, 1) > 0.65 and operators < 3:
        return False
    if digits >= 12 and operators >= 4 and math_fonts <= 2:
        return False
    return math_fonts >= 2 and operators >= 1


def _glyphs_look_like_local_fraction_part(glyphs: list[PdfGlyph]) -> bool:
    clean = [glyph for glyph in glyphs if glyph.text and not glyph.text.isspace()]
    if not clean or len(clean) > 18:
        return False
    lines = _cluster_glyph_lines(clean)
    if len(lines) > 2:
        return False
    if sum(1 for glyph in clean if _glyph_has_math_evidence(glyph)) == 0:
        return False
    height = _bbox_height(_union_bbox([glyph.bbox for glyph in clean]))
    median_height = _median_positive([_bbox_height(glyph.bbox) for glyph in clean], 10.0)
    return height <= median_height * 2.9


def _cluster_glyph_lines(glyphs: list[PdfGlyph]) -> list[list[PdfGlyph]]:
    lines: list[list[PdfGlyph]] = []
    for glyph in sorted(glyphs, key=lambda item: (_glyph_center(item)[1], item.bbox[0])):
        center_y = _glyph_center(glyph)[1]
        target: list[PdfGlyph] | None = None
        for line in lines:
            line_center = sum(_glyph_center(item)[1] for item in line) / len(line)
            line_height = _median_positive([_bbox_height(item.bbox) for item in line] + [_bbox_height(glyph.bbox)], 10.0)
            if abs(center_y - line_center) <= line_height * 0.42:
                target = line
                break
        if target is None:
            lines.append([glyph])
        else:
            target.append(glyph)
    return [sorted(line, key=lambda item: item.bbox[0]) for line in lines]


def _script_relation(base: PdfGlyph, script: PdfGlyph) -> str | None:
    base_height = max(_bbox_height(base.bbox), 1.0)
    script_height = _bbox_height(script.bbox)
    if script_height <= 0 or script_height >= base_height * 0.90:
        return None
    base_center_y = _glyph_center(base)[1]
    script_center_y = _glyph_center(script)[1]
    horizontal_gap = script.bbox[0] - base.bbox[2]
    if horizontal_gap < -base_height * 0.15 or horizontal_gap > base_height * 0.95:
        return None
    if script_center_y < base_center_y - base_height * 0.10:
        return "^"
    if script_center_y > base_center_y + base_height * 0.10:
        return "_"
    return None


def _take_script_glyphs(
    ordered: list[PdfGlyph],
    start: int,
    base: PdfGlyph,
    relation: str,
) -> tuple[list[PdfGlyph], int]:
    result: list[PdfGlyph] = []
    i = start
    base_height = max(_bbox_height(base.bbox), 1.0)
    last_x = base.bbox[2]
    while i < len(ordered):
        glyph = ordered[i]
        if _script_relation(base, glyph) != relation:
            break
        if glyph.bbox[0] - last_x > base_height * 1.2:
            break
        result.append(glyph)
        last_x = glyph.bbox[2]
        i += 1
    return result, i


def _take_radical_glyphs(
    ordered: list[PdfGlyph],
    start: int,
    vectors: list[PdfRegion],
) -> tuple[list[PdfGlyph], int]:
    radical = ordered[start]
    bar = _radical_bar(radical, vectors)
    if bar is None:
        if start + 1 >= len(ordered):
            return [], start + 1
        return [ordered[start + 1]], start + 2
    result: list[PdfGlyph] = []
    i = start + 1
    while i < len(ordered):
        glyph = ordered[i]
        if glyph.bbox[0] > bar.bbox[2] + 1.0:
            break
        if _glyph_overlaps_x(glyph, bar.bbox, 1.0) and glyph.bbox[1] >= bar.bbox[1] - 1.5:
            result.append(glyph)
        i += 1
    return result, i


def _radical_bar(radical: PdfGlyph, vectors: list[PdfRegion]) -> PdfRegion | None:
    candidates = [
        vector for vector in vectors
        if _bbox_width(vector.bbox) >= max(_bbox_width(radical.bbox), 4.0)
        and abs(vector.bbox[1] - radical.bbox[1]) <= max(_bbox_height(radical.bbox), 1.0)
        and vector.bbox[0] >= radical.bbox[0] - 1.0
        and vector.bbox[0] <= radical.bbox[2] + 3.0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: _bbox_width(item.bbox))


def _take_roman_word(ordered: list[PdfGlyph], start: int) -> tuple[list[PdfGlyph], int]:
    first = ordered[start]
    if not _is_roman_math_word_glyph(first):
        return [], start
    result = [first]
    i = start + 1
    while i < len(ordered):
        glyph = ordered[i]
        if not _is_roman_math_word_glyph(glyph):
            break
        gap = glyph.bbox[0] - result[-1].bbox[2]
        if gap > max(_bbox_height(first.bbox) * 0.45, 3.0):
            break
        result.append(glyph)
        i += 1
    if len(result) < 2:
        return [], start
    return result, i


def _is_roman_math_word_glyph(glyph: PdfGlyph) -> bool:
    text = glyph.text
    if len(text) != 1 or not text.isalpha():
        return False
    font = glyph.font.lower()
    if _is_math_font_name(glyph.font):
        return False
    return "cmr" in font or "roman" in font or "times" in font or "nimbus" in font


def _glyph_to_latex(glyph: PdfGlyph) -> str:
    text = glyph.text
    if glyph.is_unknown:
        return r"\unknown"
    replacements = {
        "−": "-",
        "·": r"\cdot",
        "×": r"\times",
        "∈": r"\in",
        "∼": r"\sim",
        "≅": r"\cong",
        "≤": r"\leq",
        "≥": r"\geq",
        "√": r"\sqrt{}",
    }
    if text in replacements:
        return replacements[text]
    return _escape_latex_char(text)


def _escape_latex_char(text: str) -> str:
    if text in {"_", "^"}:
        return "\\" + text
    return text


def _join_latex_tokens(tokens: list[str]) -> str:
    text = " ".join(token for token in tokens if token)
    text = re.sub(r"\s+([,.;:)\\}\\]])", r"\1", text)
    text = re.sub(r"([({\\[])\s+", r"\1", text)
    text = re.sub(r"\s+([_^])", r"\1", text)
    text = re.sub(r"([_^])\s+", r"\1", text)
    text = re.sub(r"\\mathrm\{([A-Za-z]+)\}\s*\(", r"\\mathrm{\1}(", text)
    return text.strip()


def _cleanup_latex(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" ?\\\\ ?", r" \\\\ ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _glyph_center(glyph: PdfGlyph) -> tuple[float, float]:
    return ((glyph.bbox[0] + glyph.bbox[2]) / 2.0, (glyph.bbox[1] + glyph.bbox[3]) / 2.0)


def _glyph_overlaps_x(
    glyph: PdfGlyph,
    bbox: tuple[float, float, float, float],
    margin: float,
) -> bool:
    return glyph.bbox[2] >= bbox[0] - margin and glyph.bbox[0] <= bbox[2] + margin


def _glyphs_look_like_equation_label(glyphs: list[PdfGlyph], page_size: tuple[float, float]) -> bool:
    text = "".join(glyph.text for glyph in glyphs if glyph.text and not glyph.text.isspace())
    if len(text) < 3 or len(text) > 8:
        return False
    if not (text.startswith("(") and text.endswith(")")):
        return False
    if not all(ch.isdigit() or ch in {".", "-"} for ch in text[1:-1]):
        return False
    bbox = _union_bbox([glyph.bbox for glyph in glyphs])
    return bbox[0] >= page_size[0] * 0.68


def _is_short_math_connector(text: str) -> bool:
    return text in {"=", "+", "-", "−", "*", "/", "·", "×", "(", ")", "[", "]", "{", "}", ",", "."}


def _best_cluster(
    region: MathEvidenceRegion,
    clusters: list[list[MathEvidenceRegion]],
) -> list[MathEvidenceRegion] | None:
    best: list[MathEvidenceRegion] | None = None
    best_gap = float("inf")
    for cluster in clusters:
        cluster_box = _union_bbox([item.bbox for item in cluster])
        if not _same_formula_neighborhood(region.bbox, cluster_box):
            continue
        gap = _bbox_gap(region.bbox, cluster_box)
        if gap < best_gap:
            best_gap = gap
            best = cluster
    return best


def _same_formula_neighborhood(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    left_height = max(left[3] - left[1], 1.0)
    right_height = max(right[3] - right[1], 1.0)
    vertical_overlap = min(left[3], right[3]) - max(left[1], right[1])
    if vertical_overlap >= -max(left_height, right_height) * 1.25:
        horizontal_gap = max(0.0, max(left[0], right[0]) - min(left[2], right[2]))
        if horizontal_gap <= 36.0:
            return True
    horizontal_overlap = min(left[2], right[2]) - max(left[0], right[0])
    vertical_gap = max(0.0, max(left[1], right[1]) - min(left[3], right[3]))
    return horizontal_overlap >= -18.0 and vertical_gap <= 16.0


def _bbox_gap(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> float:
    horizontal = max(0.0, max(left[0], right[0]) - min(left[2], right[2]))
    vertical = max(0.0, max(left[1], right[1]) - min(left[3], right[3]))
    return horizontal + vertical


def _cluster_summary(
    page_num: int,
    cluster: list[MathEvidenceRegion],
    vectors: list[PdfRegion],
) -> MathEvidenceCluster:
    bbox = _union_bbox([region.bbox for region in cluster])
    nearby_vectors = [
        vector
        for vector in vectors
        if _bbox_intersects(_expand_bbox(bbox, 2.0), vector.bbox)
    ]
    evidence: set[MathEvidence] = set()
    for region in cluster:
        evidence.update(region.evidence)
    text = " ".join(region.text.strip() for region in sorted(cluster, key=lambda item: (item.bbox[1], item.bbox[0])) if region.text.strip())
    return MathEvidenceCluster(
        page_num=page_num,
        bbox=bbox,
        text=text,
        region_count=len(cluster),
        vector_count=len(nearby_vectors),
        evidence=tuple(sorted(evidence)),
    )


def _expand_cluster_with_context(
    cluster: MathEvidenceCluster,
    lines: list[PdfLine],
) -> MathEvidenceCluster:
    context_glyphs: list[PdfGlyph] = []
    expanded_box = _expand_bbox(cluster.bbox, 2.0)
    for line in lines:
        if not _line_can_contextualize_cluster(line.bbox, expanded_box):
            continue
        for span in line.spans:
            for glyph in span.glyphs:
                if glyph.text.isspace():
                    continue
                if _bbox_intersects(_expand_bbox(glyph.bbox, 8.0), expanded_box):
                    context_glyphs.append(glyph)
    if not context_glyphs:
        return cluster
    bbox = _union_bbox([cluster.bbox] + [glyph.bbox for glyph in context_glyphs])
    text = _context_text(lines, bbox)
    return MathEvidenceCluster(
        page_num=cluster.page_num,
        bbox=bbox,
        text=text or cluster.text,
        region_count=cluster.region_count,
        vector_count=cluster.vector_count,
        evidence=cluster.evidence,
        source="pdf_structure_context_cluster",
    )


def _line_can_contextualize_cluster(
    line_box: tuple[float, float, float, float],
    cluster_box: tuple[float, float, float, float],
) -> bool:
    vertical_overlap = min(line_box[3], cluster_box[3]) - max(line_box[1], cluster_box[1])
    line_height = max(line_box[3] - line_box[1], 1.0)
    cluster_height = max(cluster_box[3] - cluster_box[1], 1.0)
    return vertical_overlap >= -max(line_height, cluster_height) * 0.25


def _context_text(lines: list[PdfLine], bbox: tuple[float, float, float, float]) -> str:
    parts: list[str] = []
    for line in sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0])):
        glyphs = [
            glyph
            for span in line.spans
            for glyph in span.glyphs
            if not glyph.text.isspace() and _bbox_intersects(_expand_bbox(glyph.bbox, 1.0), bbox)
        ]
        if not glyphs:
            continue
        parts.append("".join(glyph.text for glyph in sorted(glyphs, key=lambda item: item.bbox[0])))
    return " ".join(parts)


def _expand_bbox(
    bbox: tuple[float, float, float, float],
    margin: float,
) -> tuple[float, float, float, float]:
    return (bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin)


def _bbox_intersects(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return left[0] <= right[2] and left[2] >= right[0] and left[1] <= right[3] and left[3] >= right[1]

"""Born-digital PDF structure extraction for math-aware indexing.

This module only records facts exposed by the PDF engine: glyph text/CID,
fonts, bounding boxes, spans, lines, vector blocks, and images. It deliberately
does not guess LaTeX from prose with regular expressions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import fitz


RegionKind = Literal["text", "image", "vector"]
WarningCode = Literal["unknown_glyph", "empty_text_block"]
MathEvidence = Literal["math_font", "math_symbol", "script_size", "near_vector", "unknown_glyph"]


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

    def __init__(self, vector_margin: float = 2.0) -> None:
        self.vector_margin = vector_margin

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
    )


def _glyph_text(value: Any) -> str:
    if isinstance(value, int):
        return f"cid:{value}"
    return str(value or "")


def _glyph_cid(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    return None


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

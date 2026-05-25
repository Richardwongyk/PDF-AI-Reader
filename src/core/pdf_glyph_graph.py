"""Raw born-digital PDF glyph graph extraction.

This module standardizes PDF structure facts for later symbol identity repair
and TinyBDMath parsing.  It records glyphs, vectors, images, fonts, reading
order edges, health diagnostics, and a stable input hash.  It deliberately
does not run OCR/MFR or infer LaTeX.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Literal

import fitz

from src.core.born_digital_math import (
    BornDigitalPage,
    MuPDFBornDigitalExtractor,
    PdfFontResource,
    PdfGlyph,
    PdfRegion,
)


RAW_GLYPH_GRAPH_SCHEMA_VERSION = "raw_glyph_graph_v1"

GlyphNodeKind = Literal["glyph"]
RegionNodeKind = Literal["vector", "image"]
GlyphGraphEdgeKind = Literal["next_in_span", "next_in_line"]


@dataclass(frozen=True)
class RawGraphFont:
    """One font resource visible on the page."""

    xref: int
    name: str
    normalized_name: str
    resource_name: str
    font_type: str
    encoding: str
    extension: str
    embedded: bool


@dataclass(frozen=True)
class RawGlyphNode:
    """One character-level node from the PDF text layer."""

    node_id: str
    kind: GlyphNodeKind
    page_num: int
    text: str
    font: str
    normalized_font: str
    size: float
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float] | None
    cid: int | None
    synthetic: bool
    is_unknown: bool
    region_id: str
    line_id: str
    span_id: str
    char_index: int
    glyph_name: str = ""


@dataclass(frozen=True)
class RawRegionNode:
    """One non-text region used as structure evidence."""

    node_id: str
    kind: RegionNodeKind
    page_num: int
    bbox: tuple[float, float, float, float]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawLineRecord:
    line_id: str
    region_id: str
    page_num: int
    bbox: tuple[float, float, float, float]
    text: str
    writing_mode: int
    direction: tuple[float, float]
    span_ids: tuple[str, ...]


@dataclass(frozen=True)
class RawSpanRecord:
    span_id: str
    line_id: str
    region_id: str
    page_num: int
    bbox: tuple[float, float, float, float]
    text: str
    font: str
    normalized_font: str
    size: float
    glyph_ids: tuple[str, ...]


@dataclass(frozen=True)
class GlyphGraphEdge:
    edge_id: str
    source: str
    target: str
    kind: GlyphGraphEdgeKind
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawGlyphGraphHealth:
    """Page-level diagnostics used for routing and quality gates."""

    glyph_count: int
    unknown_glyph_count: int
    unknown_glyph_rate: float
    vector_count: int
    image_count: int
    text_region_count: int
    font_count: int
    embedded_font_count: int
    type3_font_count: int
    cid_font_count: int
    image_only_page: bool
    needs_symbol_repair: bool
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RawGlyphGraph:
    """Stable page graph for born-digital formula parsing."""

    schema_version: str
    extractor: str
    page_num: int
    page_size: tuple[float, float]
    fonts: tuple[RawGraphFont, ...]
    glyphs: tuple[RawGlyphNode, ...]
    vectors: tuple[RawRegionNode, ...]
    images: tuple[RawRegionNode, ...]
    lines: tuple[RawLineRecord, ...]
    spans: tuple[RawSpanRecord, ...]
    edges: tuple[GlyphGraphEdge, ...]
    health: RawGlyphGraphHealth

    @property
    def input_hash(self) -> str:
        payload = self.to_json(include_input_hash=False)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="ignore")
        return hashlib.sha256(encoded).hexdigest()

    def to_json(self, *, include_input_hash: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if include_input_hash:
            payload["input_hash"] = self.input_hash
        return payload


class RawGlyphGraphExtractor:
    """Build raw glyph graphs from PyMuPDF pages or existing page facts."""

    def __init__(self, page_extractor: MuPDFBornDigitalExtractor | None = None) -> None:
        self._page_extractor = page_extractor or MuPDFBornDigitalExtractor()

    def extract_page(self, page: fitz.Page, page_num: int) -> RawGlyphGraph:
        page_facts = self._page_extractor.extract_page(page, page_num)
        return self.from_page_facts(page_facts)

    def extract_document(
        self,
        doc: fitz.Document,
        start_page: int = 0,
        max_pages: int | None = None,
    ) -> list[RawGlyphGraph]:
        end_page = doc.page_count if max_pages is None else min(doc.page_count, start_page + max_pages)
        return [self.extract_page(doc[page_num], page_num) for page_num in range(start_page, end_page)]

    def from_page_facts(self, page_facts: BornDigitalPage) -> RawGlyphGraph:
        fonts = tuple(_font_to_graph_font(font) for font in page_facts.fonts)
        glyphs: list[RawGlyphNode] = []
        vectors: list[RawRegionNode] = []
        images: list[RawRegionNode] = []
        lines: list[RawLineRecord] = []
        spans: list[RawSpanRecord] = []
        edges: list[GlyphGraphEdge] = []

        glyph_index = 0
        line_index = 0
        span_index = 0
        vector_index = 0
        image_index = 0

        for region_index, region in enumerate(page_facts.regions):
            region_id = f"p{page_facts.page_num}_r{region_index}"
            if region.kind == "vector":
                vectors.append(_region_node(region, region_id, "vector", vector_index))
                vector_index += 1
                continue
            if region.kind == "image":
                images.append(_region_node(region, region_id, "image", image_index))
                image_index += 1
                continue
            if region.kind != "text":
                continue
            for line in region.lines:
                line_id = f"p{page_facts.page_num}_l{line_index}"
                line_index += 1
                line_span_ids: list[str] = []
                line_glyph_ids: list[str] = []
                for span in line.spans:
                    span_id = f"p{page_facts.page_num}_s{span_index}"
                    span_index += 1
                    line_span_ids.append(span_id)
                    span_glyph_ids: list[str] = []
                    for char_index, glyph in enumerate(span.glyphs):
                        node_id = f"p{page_facts.page_num}_g{glyph_index}"
                        glyph_index += 1
                        span_glyph_ids.append(node_id)
                        line_glyph_ids.append(node_id)
                        glyphs.append(
                            _glyph_node(
                                glyph,
                                node_id=node_id,
                                page_num=page_facts.page_num,
                                region_id=region_id,
                                line_id=line_id,
                                span_id=span_id,
                                char_index=char_index,
                            )
                        )
                    spans.append(
                        RawSpanRecord(
                            span_id=span_id,
                            line_id=line_id,
                            region_id=region_id,
                            page_num=page_facts.page_num,
                            bbox=span.bbox,
                            text=span.text,
                            font=span.font,
                            normalized_font=normalize_pdf_font_name(span.font),
                            size=round(float(span.size), 6),
                            glyph_ids=tuple(span_glyph_ids),
                        )
                    )
                    edges.extend(
                        _reading_edges(
                            span_glyph_ids,
                            kind="next_in_span",
                            edge_prefix=f"{span_id}_next",
                        )
                    )
                lines.append(
                    RawLineRecord(
                        line_id=line_id,
                        region_id=region_id,
                        page_num=page_facts.page_num,
                        bbox=line.bbox,
                        text=line.text,
                        writing_mode=line.writing_mode,
                        direction=line.direction,
                        span_ids=tuple(line_span_ids),
                    )
                )
                edges.extend(
                    _reading_edges(
                        line_glyph_ids,
                        kind="next_in_line",
                        edge_prefix=f"{line_id}_next",
                    )
                )

        health = _graph_health(page_facts, glyphs, vectors, images, fonts)
        return RawGlyphGraph(
            schema_version=RAW_GLYPH_GRAPH_SCHEMA_VERSION,
            extractor=page_facts.extractor,
            page_num=page_facts.page_num,
            page_size=page_facts.page_size,
            fonts=fonts,
            glyphs=tuple(glyphs),
            vectors=tuple(vectors),
            images=tuple(images),
            lines=tuple(lines),
            spans=tuple(spans),
            edges=tuple(edges),
            health=health,
        )


def normalize_pdf_font_name(name: str) -> str:
    """Remove common PDF subset prefixes while preserving the base font name."""

    text = str(name or "")
    if "+" not in text:
        return text
    prefix, suffix = text.split("+", 1)
    if len(prefix) == 6 and prefix.isalpha() and prefix.upper() == prefix:
        return suffix
    return text


def _font_to_graph_font(font: PdfFontResource) -> RawGraphFont:
    return RawGraphFont(
        xref=int(font.xref),
        name=font.name,
        normalized_name=normalize_pdf_font_name(font.name),
        resource_name=font.resource_name,
        font_type=font.font_type,
        encoding=font.encoding,
        extension=font.extension,
        embedded=bool(font.embedded),
    )


def _glyph_node(
    glyph: PdfGlyph,
    *,
    node_id: str,
    page_num: int,
    region_id: str,
    line_id: str,
    span_id: str,
    char_index: int,
) -> RawGlyphNode:
    return RawGlyphNode(
        node_id=node_id,
        kind="glyph",
        page_num=page_num,
        text=glyph.text,
        font=glyph.font,
        normalized_font=normalize_pdf_font_name(glyph.font),
        size=round(float(glyph.size), 6),
        bbox=_round_bbox(glyph.bbox),
        origin=_round_origin(glyph.origin),
        cid=glyph.cid,
        synthetic=bool(glyph.synthetic),
        is_unknown=bool(glyph.is_unknown),
        region_id=region_id,
        line_id=line_id,
        span_id=span_id,
        char_index=int(char_index),
        glyph_name=str(getattr(glyph, "glyph_name", "") or ""),
    )


def _region_node(
    region: PdfRegion,
    region_id: str,
    kind: RegionNodeKind,
    index: int,
) -> RawRegionNode:
    return RawRegionNode(
        node_id=f"{region_id}_{kind}_{index}",
        kind=kind,
        page_num=region.page_num,
        bbox=_round_bbox(region.bbox),
        metadata=dict(region.metadata),
    )


def _reading_edges(
    node_ids: list[str],
    *,
    kind: GlyphGraphEdgeKind,
    edge_prefix: str,
) -> list[GlyphGraphEdge]:
    edges: list[GlyphGraphEdge] = []
    for index, (source, target) in enumerate(zip(node_ids, node_ids[1:], strict=False)):
        edges.append(
            GlyphGraphEdge(
                edge_id=f"{edge_prefix}_{index}",
                source=source,
                target=target,
                kind=kind,
            )
        )
    return edges


def _graph_health(
    page_facts: BornDigitalPage,
    glyphs: list[RawGlyphNode],
    vectors: list[RawRegionNode],
    images: list[RawRegionNode],
    fonts: tuple[RawGraphFont, ...],
) -> RawGlyphGraphHealth:
    glyph_count = len(glyphs)
    unknown_count = sum(1 for glyph in glyphs if glyph.is_unknown)
    font_types = [font.font_type.lower() for font in fonts]
    type3_count = sum(1 for value in font_types if "type3" in value or "type 3" in value)
    cid_count = sum(1 for value in font_types if "cid" in value)
    text_region_count = sum(1 for region in page_facts.regions if region.kind == "text")
    image_only = glyph_count == 0 and bool(images)
    return RawGlyphGraphHealth(
        glyph_count=glyph_count,
        unknown_glyph_count=unknown_count,
        unknown_glyph_rate=round(unknown_count / glyph_count, 6) if glyph_count else 0.0,
        vector_count=len(vectors),
        image_count=len(images),
        text_region_count=text_region_count,
        font_count=len(fonts),
        embedded_font_count=sum(1 for font in fonts if font.embedded),
        type3_font_count=type3_count,
        cid_font_count=cid_count,
        image_only_page=image_only,
        needs_symbol_repair=unknown_count > 0 or type3_count > 0,
        warnings=tuple(sorted(str(item) for item in page_facts.warnings)),
    )


def _round_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return tuple(round(float(value), 6) for value in bbox)  # type: ignore[return-value]


def _round_origin(origin: tuple[float, float] | None) -> tuple[float, float] | None:
    if origin is None:
        return None
    return (round(float(origin[0]), 6), round(float(origin[1]), 6))

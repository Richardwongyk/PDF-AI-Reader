"""Conservative r0.5 symbol identity repair for born-digital PDF glyph graphs.

The repairer enriches raw PDF glyph facts with auditable identity candidates.
It uses only non-image evidence: existing PDF text, standard glyph names, and
same-font/same-CID anchors inside the graph.  It does not infer formula
structure and does not call visual recognizers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any

from src.core.pdf_glyph_graph import RawGlyphGraph, RawGlyphNode


ENRICHED_GLYPH_GRAPH_SCHEMA_VERSION = "enriched_glyph_graph_v1"
SYMBOL_IDENTITY_REPAIR_VERSION = "symbol_identity_repair_v1"


@dataclass(frozen=True)
class SymbolIdentityCandidate:
    """One possible identity for a raw glyph."""

    unicode: str
    latex: str
    source: str
    confidence: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedSymbolIdentity:
    """A high-confidence repaired identity."""

    unicode: str
    latex: str
    source: str
    confidence: float


@dataclass(frozen=True)
class EnrichedGlyphNode:
    """A raw glyph plus r0.5 identity evidence."""

    node_id: str
    raw: RawGlyphNode
    identity_candidates: tuple[SymbolIdentityCandidate, ...]
    resolved_identity: ResolvedSymbolIdentity | None
    repair_trace: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SymbolIdentityRepairSummary:
    """Graph-level repair diagnostics."""

    raw_input_hash: str
    repair_version: str
    glyph_count: int
    unknown_before: int
    unknown_after: int
    repaired_count: int
    conflict_count: int
    sources: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class EnrichedGlyphGraph:
    """Raw Glyph Graph with r0.5 symbol identity repair evidence."""

    schema_version: str
    repair_version: str
    raw_graph: RawGlyphGraph
    glyphs: tuple[EnrichedGlyphNode, ...]
    summary: SymbolIdentityRepairSummary

    @property
    def input_hash(self) -> str:
        payload = self.to_json(include_input_hash=False, include_raw_graph=False)
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="ignore")
        return hashlib.sha256(encoded).hexdigest()

    def to_json(
        self,
        *,
        include_input_hash: bool = True,
        include_raw_graph: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "repair_version": self.repair_version,
            "raw_input_hash": self.raw_graph.input_hash,
            "glyphs": [glyph.to_json() for glyph in self.glyphs],
            "summary": asdict(self.summary),
        }
        if include_raw_graph:
            payload["raw_graph"] = self.raw_graph.to_json()
        if include_input_hash:
            payload["input_hash"] = self.input_hash
        return payload


class SymbolIdentityRepairer:
    """Build an Enriched Glyph Graph without image-based recognition."""

    def __init__(self, auto_resolve_threshold: float = 0.95) -> None:
        self._auto_resolve_threshold = float(auto_resolve_threshold)

    def repair_graph(self, graph: RawGlyphGraph) -> EnrichedGlyphGraph:
        anchors, font_cid_conflicts = _font_cid_anchors(graph.glyphs)
        enriched: list[EnrichedGlyphNode] = []
        candidate_conflicts = 0

        for glyph in graph.glyphs:
            node, has_candidate_conflict = self._repair_glyph(
                glyph,
                anchors=anchors,
                font_cid_conflicts=font_cid_conflicts,
            )
            enriched.append(node)
            if has_candidate_conflict:
                candidate_conflicts += 1

        unknown_before = sum(1 for glyph in graph.glyphs if glyph.is_unknown)
        unknown_after = sum(
            1
            for node in enriched
            if node.raw.is_unknown and node.resolved_identity is None
        )
        repaired_count = sum(
            1
            for node in enriched
            if node.raw.is_unknown and node.resolved_identity is not None
        )
        sources = sorted(
            {
                node.resolved_identity.source
                for node in enriched
                if node.resolved_identity is not None
            }
        )
        warnings = sorted({warning for node in enriched for warning in node.warnings})
        summary = SymbolIdentityRepairSummary(
            raw_input_hash=graph.input_hash,
            repair_version=SYMBOL_IDENTITY_REPAIR_VERSION,
            glyph_count=len(graph.glyphs),
            unknown_before=unknown_before,
            unknown_after=unknown_after,
            repaired_count=repaired_count,
            conflict_count=len(font_cid_conflicts) + candidate_conflicts,
            sources=tuple(sources),
            warnings=tuple(warnings),
        )
        return EnrichedGlyphGraph(
            schema_version=ENRICHED_GLYPH_GRAPH_SCHEMA_VERSION,
            repair_version=SYMBOL_IDENTITY_REPAIR_VERSION,
            raw_graph=graph,
            glyphs=tuple(enriched),
            summary=summary,
        )

    def _repair_glyph(
        self,
        glyph: RawGlyphNode,
        *,
        anchors: dict[tuple[str, int], str],
        font_cid_conflicts: set[tuple[str, int]],
    ) -> tuple[EnrichedGlyphNode, bool]:
        trace: list[str] = []
        warnings: list[str] = []
        candidates: list[SymbolIdentityCandidate] = []

        if _has_known_pdf_identity(glyph):
            trace.append("pdf_text_identity")
            candidate = _candidate_from_pdf_text(glyph)
            return (
                EnrichedGlyphNode(
                    node_id=glyph.node_id,
                    raw=glyph,
                    identity_candidates=(candidate,),
                    resolved_identity=ResolvedSymbolIdentity(
                        unicode=candidate.unicode,
                        latex=candidate.latex,
                        source=candidate.source,
                        confidence=candidate.confidence,
                    ),
                    repair_trace=tuple(trace),
                    warnings=(),
                ),
                False,
            )

        static_candidate = _candidate_from_glyph_name(glyph)
        if static_candidate is not None:
            trace.append("glyph_name_lookup")
            candidates.append(static_candidate)

        key = _font_cid_key(glyph)
        if key is not None:
            if key in font_cid_conflicts:
                warnings.append("font_cid_identity_conflict")
            elif key in anchors:
                trace.append("same_font_cid_anchor")
                value = anchors[key]
                candidates.append(
                    SymbolIdentityCandidate(
                        unicode=value,
                        latex=_latex_for_unicode(value),
                        source="same_font_cid_anchor",
                        confidence=0.97,
                        evidence=(
                            f"normalized_font={key[0]}",
                            f"cid={key[1]}",
                        ),
                    )
                )

        resolved, has_candidate_conflict = self._resolve_candidates(candidates, warnings)
        if has_candidate_conflict:
            warnings.append("identity_candidate_conflict")
        if resolved is not None:
            trace.append(f"resolved_by={resolved.source}")
        elif not candidates:
            warnings.append("unrecovered_identity")

        return (
            EnrichedGlyphNode(
                node_id=glyph.node_id,
                raw=glyph,
                identity_candidates=tuple(sorted(candidates, key=_candidate_sort_key)),
                resolved_identity=resolved,
                repair_trace=tuple(trace),
                warnings=tuple(sorted(set(warnings))),
            ),
            has_candidate_conflict,
        )

    def _resolve_candidates(
        self,
        candidates: list[SymbolIdentityCandidate],
        warnings: list[str],
    ) -> tuple[ResolvedSymbolIdentity | None, bool]:
        if not candidates:
            return None, False
        unicode_values = {candidate.unicode for candidate in candidates if candidate.unicode}
        if len(unicode_values) != 1:
            return None, True
        best = max(candidates, key=lambda item: item.confidence)
        if best.confidence < self._auto_resolve_threshold:
            warnings.append("low_identity_confidence")
            return None, False
        return (
            ResolvedSymbolIdentity(
                unicode=best.unicode,
                latex=best.latex,
                source=best.source,
                confidence=best.confidence,
            ),
            False,
        )


def _font_cid_anchors(
    glyphs: tuple[RawGlyphNode, ...],
) -> tuple[dict[tuple[str, int], str], set[tuple[str, int]]]:
    values: dict[tuple[str, int], set[str]] = {}
    for glyph in glyphs:
        key = _font_cid_key(glyph)
        if key is None or not _has_known_pdf_identity(glyph):
            continue
        values.setdefault(key, set()).add(glyph.text)
    anchors: dict[tuple[str, int], str] = {}
    conflicts: set[tuple[str, int]] = set()
    for key, identities in values.items():
        if len(identities) == 1:
            anchors[key] = next(iter(identities))
        else:
            conflicts.add(key)
    return anchors, conflicts


def _font_cid_key(glyph: RawGlyphNode) -> tuple[str, int] | None:
    if glyph.cid is None:
        return None
    font_name = glyph.normalized_font or glyph.font
    if not font_name:
        return None
    return (font_name, int(glyph.cid))


def _has_known_pdf_identity(glyph: RawGlyphNode) -> bool:
    return bool(glyph.text) and not glyph.is_unknown


def _candidate_from_pdf_text(glyph: RawGlyphNode) -> SymbolIdentityCandidate:
    return SymbolIdentityCandidate(
        unicode=glyph.text,
        latex=_latex_for_unicode(glyph.text),
        source="pdf_text",
        confidence=1.0,
        evidence=(
            f"font={glyph.normalized_font or glyph.font}",
            "pdf_text_layer",
        ),
    )


def _candidate_from_glyph_name(glyph: RawGlyphNode) -> SymbolIdentityCandidate | None:
    name = str(getattr(glyph, "glyph_name", "") or "").strip().lstrip("/")
    if not name:
        return None
    unicode_value = _unicode_from_glyph_name(name)
    if not unicode_value:
        return None
    return SymbolIdentityCandidate(
        unicode=unicode_value,
        latex=_latex_for_unicode(unicode_value),
        source="static_glyph_name_map",
        confidence=0.99,
        evidence=(
            f"glyph_name={name}",
            f"font={glyph.normalized_font or glyph.font}",
        ),
    )


def _unicode_from_glyph_name(name: str) -> str:
    base = name.split(".", 1)[0]
    if base in _GLYPH_NAME_TO_UNICODE:
        return _GLYPH_NAME_TO_UNICODE[base]
    encoded = _unicode_from_encoded_glyph_name(base)
    if encoded:
        return encoded
    normalized = _normalize_glyph_name(base)
    return _NORMALIZED_GLYPH_NAME_TO_UNICODE.get(normalized, "")


def _unicode_from_encoded_glyph_name(name: str) -> str:
    uni_match = re.fullmatch(r"uni([0-9A-Fa-f]{4})+", name)
    if uni_match is not None:
        hex_digits = name[3:]
        try:
            return "".join(chr(int(hex_digits[index : index + 4], 16)) for index in range(0, len(hex_digits), 4))
        except ValueError:
            return ""
    u_match = re.fullmatch(r"u([0-9A-Fa-f]{4,6})", name)
    if u_match is not None:
        try:
            return chr(int(u_match.group(1), 16))
        except (OverflowError, ValueError):
            return ""
    return ""


def _normalize_glyph_name(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", name.lower())


def _candidate_sort_key(candidate: SymbolIdentityCandidate) -> tuple[float, str, str]:
    return (-candidate.confidence, candidate.source, candidate.unicode)


def _latex_for_unicode(text: str) -> str:
    if text in _UNICODE_TO_LATEX:
        return _UNICODE_TO_LATEX[text]
    if len(text) == 1:
        return _escape_latex_char(text)
    return "".join(_latex_for_unicode(char) for char in text)


def _escape_latex_char(text: str) -> str:
    return _LATEX_ESCAPES.get(text, text)


_LATEX_ESCAPES = {
    "\\": r"\backslash{}",
    "{": r"\{",
    "}": r"\}",
    "_": r"\_",
    "^": r"\^{}",
    "#": r"\#",
    "$": r"\$",
    "%": r"\%",
    "&": r"\&",
}


_UNICODE_TO_LATEX = {
    "−": "-",
    "·": r"\cdot",
    "×": r"\times",
    "÷": r"\div",
    "±": r"\pm",
    "∓": r"\mp",
    "∗": r"\ast",
    "√": r"\sqrt",
    "∑": r"\sum",
    "∏": r"\prod",
    "∫": r"\int",
    "∮": r"\oint",
    "∞": r"\infty",
    "∂": r"\partial",
    "∇": r"\nabla",
    "∀": r"\forall",
    "∃": r"\exists",
    "∅": r"\emptyset",
    "∈": r"\in",
    "∉": r"\notin",
    "∋": r"\ni",
    "⊂": r"\subset",
    "⊃": r"\supset",
    "⊆": r"\subseteq",
    "⊇": r"\supseteq",
    "∪": r"\cup",
    "∩": r"\cap",
    "∧": r"\wedge",
    "∨": r"\vee",
    "¬": r"\neg",
    "→": r"\to",
    "←": r"\leftarrow",
    "↔": r"\leftrightarrow",
    "⇒": r"\Rightarrow",
    "⇐": r"\Leftarrow",
    "⇔": r"\Leftrightarrow",
    "≤": r"\leq",
    "≥": r"\geq",
    "≠": r"\neq",
    "≈": r"\approx",
    "≃": r"\simeq",
    "≅": r"\cong",
    "≡": r"\equiv",
    "∼": r"\sim",
    "∝": r"\propto",
    "⊕": r"\oplus",
    "⊗": r"\otimes",
    "⊥": r"\perp",
    "∥": r"\parallel",
    "ℓ": r"\ell",
    "ℏ": r"\hbar",
    "ı": r"\imath",
    "ȷ": r"\jmath",
    "α": r"\alpha",
    "β": r"\beta",
    "γ": r"\gamma",
    "δ": r"\delta",
    "ϵ": r"\epsilon",
    "ε": r"\varepsilon",
    "ζ": r"\zeta",
    "η": r"\eta",
    "θ": r"\theta",
    "ϑ": r"\vartheta",
    "ι": r"\iota",
    "κ": r"\kappa",
    "λ": r"\lambda",
    "μ": r"\mu",
    "ν": r"\nu",
    "ξ": r"\xi",
    "π": r"\pi",
    "ϖ": r"\varpi",
    "ρ": r"\rho",
    "ϱ": r"\varrho",
    "σ": r"\sigma",
    "ς": r"\varsigma",
    "τ": r"\tau",
    "υ": r"\upsilon",
    "φ": r"\phi",
    "ϕ": r"\varphi",
    "χ": r"\chi",
    "ψ": r"\psi",
    "ω": r"\omega",
    "Γ": r"\Gamma",
    "Δ": r"\Delta",
    "Θ": r"\Theta",
    "Λ": r"\Lambda",
    "Ξ": r"\Xi",
    "Π": r"\Pi",
    "Σ": r"\Sigma",
    "Υ": r"\Upsilon",
    "Φ": r"\Phi",
    "Ψ": r"\Psi",
    "Ω": r"\Omega",
}


_GLYPH_NAME_TO_UNICODE = {
    "Alpha": "Α",
    "Beta": "Β",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Epsilon": "Ε",
    "Zeta": "Ζ",
    "Eta": "Η",
    "Theta": "Θ",
    "Iota": "Ι",
    "Kappa": "Κ",
    "Lambda": "Λ",
    "Mu": "Μ",
    "Nu": "Ν",
    "Xi": "Ξ",
    "Omicron": "Ο",
    "Pi": "Π",
    "Rho": "Ρ",
    "Sigma": "Σ",
    "Tau": "Τ",
    "Upsilon": "Υ",
    "Phi": "Φ",
    "Chi": "Χ",
    "Psi": "Ψ",
    "Omega": "Ω",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ϵ",
    "epsilon1": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "theta1": "ϑ",
    "vartheta": "ϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "omicron": "ο",
    "pi": "π",
    "omega1": "ϖ",
    "varpi": "ϖ",
    "rho": "ρ",
    "rho1": "ϱ",
    "varrho": "ϱ",
    "sigma": "σ",
    "sigma1": "ς",
    "varsigma": "ς",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "phi1": "ϕ",
    "varphi": "ϕ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "summation": "∑",
    "sum": "∑",
    "product": "∏",
    "prod": "∏",
    "integral": "∫",
    "integraldisplay": "∫",
    "contourintegral": "∮",
    "radical": "√",
    "sqrt": "√",
    "minus": "−",
    "plusminus": "±",
    "minusplus": "∓",
    "multiply": "×",
    "times": "×",
    "divide": "÷",
    "cdot": "·",
    "asteriskmath": "∗",
    "partialdiff": "∂",
    "partial": "∂",
    "nabla": "∇",
    "infinity": "∞",
    "infty": "∞",
    "forall": "∀",
    "exists": "∃",
    "emptyset": "∅",
    "element": "∈",
    "in": "∈",
    "notelement": "∉",
    "notin": "∉",
    "owner": "∋",
    "contains": "∋",
    "subset": "⊂",
    "superset": "⊃",
    "subsetequal": "⊆",
    "supersetequal": "⊇",
    "union": "∪",
    "intersection": "∩",
    "logicaland": "∧",
    "logicalor": "∨",
    "not": "¬",
    "arrowright": "→",
    "rightarrow": "→",
    "arrowleft": "←",
    "leftarrow": "←",
    "arrowboth": "↔",
    "leftrightarrow": "↔",
    "arrowdblright": "⇒",
    "Rightarrow": "⇒",
    "arrowdblleft": "⇐",
    "Leftarrow": "⇐",
    "arrowdblboth": "⇔",
    "Leftrightarrow": "⇔",
    "lessequal": "≤",
    "leq": "≤",
    "greaterequal": "≥",
    "geq": "≥",
    "notequal": "≠",
    "neq": "≠",
    "approxequal": "≈",
    "approx": "≈",
    "similar": "∼",
    "sim": "∼",
    "simeq": "≃",
    "congruent": "≅",
    "cong": "≅",
    "equivalence": "≡",
    "equiv": "≡",
    "propersubset": "⊂",
    "propersuperset": "⊃",
    "proportional": "∝",
    "propto": "∝",
    "circleplus": "⊕",
    "oplus": "⊕",
    "circlemultiply": "⊗",
    "otimes": "⊗",
    "perpendicular": "⊥",
    "parallel": "∥",
    "ell": "ℓ",
    "hbar": "ℏ",
    "dotlessi": "ı",
    "dotlessj": "ȷ",
}


_NORMALIZED_GLYPH_NAME_TO_UNICODE = {
    _normalize_glyph_name(name): value
    for name, value in _GLYPH_NAME_TO_UNICODE.items()
    if name[:1].islower() or name not in {"Alpha"}
}

"""Canonical Symbol Layout Tree schema for TinyBDMath.

CSLT is the training/audit target for born-digital formula recovery.  It is a
tree-shaped semantic layout representation, not an author-source LaTeX style.
Production inference may emit CSLT candidates, but source LaTeX is only used to
build target CSLT rows during training and audits.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any, Literal


CSLT_SCHEMA_VERSION = "tinybdmath_cslt_v1"

CSLTNodeType = Literal[
    "symbol",
    "text_run",
    "group",
    "script",
    "fraction",
    "radical",
    "accent",
    "under_over",
    "fence",
    "matrix",
    "equation_number",
    "artifact",
]

CSLTEdgeRelation = Literal[
    "next",
    "child",
    "base",
    "sup",
    "sub",
    "over",
    "under",
    "numerator",
    "denominator",
    "fraction_bar",
    "radical_body",
    "radical_index",
    "radical_rule",
    "accent_base",
    "accent_mark",
    "fence_open",
    "fence_body",
    "fence_close",
    "matrix_row",
    "matrix_cell",
    "cell_content",
    "text_char",
    "artifact_of",
]


@dataclass(frozen=True)
class CSLTNode:
    node_id: str
    node_type: CSLTNodeType
    value: str = ""
    latex: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload["value"]:
            payload.pop("value")
        if not payload["latex"]:
            payload.pop("latex")
        if not payload["attrs"]:
            payload.pop("attrs")
        return payload


@dataclass(frozen=True)
class CSLTEdge:
    source: str
    target: str
    relation: CSLTEdgeRelation
    order: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        payload = asdict(self)
        if not payload["attrs"]:
            payload.pop("attrs")
        return payload


@dataclass(frozen=True)
class CSLTTree:
    root_id: str
    nodes: tuple[CSLTNode, ...]
    edges: tuple[CSLTEdge, ...] = ()
    schema_version: str = CSLT_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def node_map(self) -> dict[str, CSLTNode]:
        return {node.node_id: node for node in self.nodes}

    def children(self, node_id: str, relation: str | None = None) -> list[CSLTNode]:
        node_by_id = self.node_map()
        edges = [
            edge
            for edge in self.edges
            if edge.source == node_id and (relation is None or edge.relation == relation)
        ]
        return [node_by_id[edge.target] for edge in sorted(edges, key=_edge_sort_key) if edge.target in node_by_id]

    def child_ids(self, node_id: str, relation: str | None = None) -> list[str]:
        return [node.node_id for node in self.children(node_id, relation)]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "root_id": self.root_id,
            "nodes": [node.to_json() for node in sorted(self.nodes, key=lambda item: item.node_id)],
            "edges": [edge.to_json() for edge in sorted(self.edges, key=_edge_sort_key)],
            "metadata": _canonical_value(self.metadata),
            "warnings": list(self.warnings),
            "stable_hash": self.stable_hash(),
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "CSLTTree":
        nodes = tuple(
            CSLTNode(
                node_id=str(item.get("node_id", "") or ""),
                node_type=str(item.get("node_type", "artifact") or "artifact"),  # type: ignore[arg-type]
                value=str(item.get("value", "") or ""),
                latex=str(item.get("latex", "") or ""),
                attrs=dict(item.get("attrs", {}) or {}),
            )
            for item in payload.get("nodes", [])
            if isinstance(item, dict)
        )
        edges = tuple(
            CSLTEdge(
                source=str(item.get("source", "") or ""),
                target=str(item.get("target", "") or ""),
                relation=str(item.get("relation", "child") or "child"),  # type: ignore[arg-type]
                order=int(item.get("order", 0) or 0),
                attrs=dict(item.get("attrs", {}) or {}),
            )
            for item in payload.get("edges", [])
            if isinstance(item, dict)
        )
        return cls(
            root_id=str(payload.get("root_id", "") or ""),
            nodes=nodes,
            edges=edges,
            schema_version=str(payload.get("schema_version", "") or CSLT_SCHEMA_VERSION),
            metadata=dict(payload.get("metadata", {}) or {}),
            warnings=tuple(str(item) for item in payload.get("warnings", []) if item),
        )

    def canonical_payload(self) -> dict[str, Any]:
        payload = self.to_json()
        payload.pop("stable_hash", None)
        return _canonical_value(payload)

    def stable_hash(self) -> str:
        encoded = json.dumps(
            _canonical_value(
                {
                    "schema_version": self.schema_version,
                    "root_id": self.root_id,
                    "nodes": [node.to_json() for node in sorted(self.nodes, key=lambda item: item.node_id)],
                    "edges": [edge.to_json() for edge in sorted(self.edges, key=_edge_sort_key)],
                }
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="ignore")
        return hashlib.sha256(encoded).hexdigest()

    def to_latex(self) -> str:
        return _serialize_node(self.root_id, self.node_map(), self.edges, set())


class CSLTBuilder:
    """Small deterministic builder used by target-tree and tests."""

    def __init__(self) -> None:
        self._nodes: list[CSLTNode] = []
        self._edges: list[CSLTEdge] = []
        self._next_id = 0

    def add_node(
        self,
        node_type: CSLTNodeType,
        *,
        value: str = "",
        latex: str = "",
        attrs: dict[str, Any] | None = None,
    ) -> str:
        node_id = f"n{self._next_id:05d}"
        self._next_id += 1
        self._nodes.append(
            CSLTNode(
                node_id=node_id,
                node_type=node_type,
                value=str(value or ""),
                latex=str(latex or ""),
                attrs=dict(attrs or {}),
            )
        )
        return node_id

    def add_edge(
        self,
        source: str,
        target: str,
        relation: CSLTEdgeRelation = "child",
        *,
        order: int = 0,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        self._edges.append(
            CSLTEdge(
                source=str(source),
                target=str(target),
                relation=relation,
                order=int(order),
                attrs=dict(attrs or {}),
            )
        )

    def build(
        self,
        root_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        warnings: list[str] | tuple[str, ...] = (),
    ) -> CSLTTree:
        return CSLTTree(
            root_id=root_id,
            nodes=tuple(self._nodes),
            edges=tuple(self._edges),
            metadata=dict(metadata or {}),
            warnings=tuple(sorted(set(str(item) for item in warnings if item))),
        )


def cslt_from_json(payload: dict[str, Any]) -> CSLTTree:
    return CSLTTree.from_json(payload)


def _serialize_node(
    node_id: str,
    node_by_id: dict[str, CSLTNode],
    edges: tuple[CSLTEdge, ...],
    visited: set[str],
) -> str:
    if node_id in visited:
        return ""
    visited = set(visited)
    visited.add(node_id)
    node = node_by_id.get(node_id)
    if node is None:
        return ""
    outgoing = [edge for edge in edges if edge.source == node_id]

    if node.node_type == "symbol":
        return node.latex or node.value
    if node.node_type == "text_run":
        return r"\text{" + _escape_text(node.value) + "}"
    if node.node_type == "artifact":
        return ""
    if node.node_type == "group":
        return _serialize_sequence(node_id, node_by_id, edges, visited, relations=("child", "next", "cell_content"))
    if node.node_type == "script":
        base = _serialize_first(outgoing, "base", node_by_id, edges, visited)
        sub = _serialize_first(outgoing, "sub", node_by_id, edges, visited)
        sup = _serialize_first(outgoing, "sup", node_by_id, edges, visited)
        result = base
        if sub:
            result += "_{" + sub + "}"
        if sup:
            result += "^{" + sup + "}"
        return result
    if node.node_type == "fraction":
        numer = _serialize_first(outgoing, "numerator", node_by_id, edges, visited)
        denom = _serialize_first(outgoing, "denominator", node_by_id, edges, visited)
        return r"\frac{" + numer + "}{" + denom + "}"
    if node.node_type == "radical":
        body = _serialize_first(outgoing, "radical_body", node_by_id, edges, visited)
        index = _serialize_first(outgoing, "radical_index", node_by_id, edges, visited)
        if index:
            return r"\sqrt[" + index + "]{" + body + "}"
        return r"\sqrt{" + body + "}"
    if node.node_type == "accent":
        base = _serialize_first(outgoing, "accent_base", node_by_id, edges, visited)
        mark = node.latex or node.value or r"\hat"
        return mark + "{" + base + "}"
    if node.node_type == "under_over":
        base = _serialize_first(outgoing, "base", node_by_id, edges, visited)
        under = _serialize_first(outgoing, "under", node_by_id, edges, visited)
        over = _serialize_first(outgoing, "over", node_by_id, edges, visited)
        result = base
        if under:
            result += "_{" + under + "}"
        if over:
            result += "^{" + over + "}"
        return result
    if node.node_type == "fence":
        open_text = _serialize_first(outgoing, "fence_open", node_by_id, edges, visited)
        body = _serialize_first(outgoing, "fence_body", node_by_id, edges, visited)
        close_text = _serialize_first(outgoing, "fence_close", node_by_id, edges, visited)
        return open_text + body + close_text
    if node.node_type == "matrix":
        rows = _ordered_targets(outgoing, "matrix_row")
        row_texts: list[str] = []
        for row_id in rows:
            row_edges = [edge for edge in edges if edge.source == row_id]
            cells = _ordered_targets(row_edges, "matrix_cell")
            row_texts.append(
                "&".join(_serialize_node(cell_id, node_by_id, edges, visited) for cell_id in cells)
            )
        env = str(node.attrs.get("environment", "") or "matrix")
        return rf"\begin{{{env}}}" + r"\\".join(row_texts) + rf"\end{{{env}}}"
    return _serialize_sequence(node_id, node_by_id, edges, visited, relations=("child", "next"))


def _serialize_sequence(
    node_id: str,
    node_by_id: dict[str, CSLTNode],
    edges: tuple[CSLTEdge, ...],
    visited: set[str],
    *,
    relations: tuple[str, ...],
) -> str:
    children = [
        edge
        for edge in edges
        if edge.source == node_id and edge.relation in relations
    ]
    return "".join(
        _serialize_node(edge.target, node_by_id, edges, visited)
        for edge in sorted(children, key=_edge_sort_key)
    )


def _serialize_first(
    outgoing: list[CSLTEdge],
    relation: str,
    node_by_id: dict[str, CSLTNode],
    edges: tuple[CSLTEdge, ...],
    visited: set[str],
) -> str:
    targets = _ordered_targets(outgoing, relation)
    if not targets:
        return ""
    return _serialize_node(targets[0], node_by_id, edges, visited)


def _ordered_targets(edges: list[CSLTEdge], relation: str) -> list[str]:
    return [
        edge.target
        for edge in sorted((edge for edge in edges if edge.relation == relation), key=_edge_sort_key)
    ]


def _edge_sort_key(edge: CSLTEdge) -> tuple[str, int, str, str]:
    return (edge.source, int(edge.order), edge.relation, edge.target)


def _escape_text(text: str) -> str:
    return str(text).replace("\\", r"\textbackslash{}").replace("{", r"\{").replace("}", r"\}")


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value

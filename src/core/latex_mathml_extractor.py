"""KaTeX-backed LaTeX to MathML/parse-tree extraction for audits.

This module is for training and evaluation data preparation.  It delegates the
LaTeX grammar to the local KaTeX bundle already shipped with the app, avoiding
sample-specific parsing rules in Python.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


LATEX_MATHML_SCHEMA_VERSION = "latex_mathml_katex_v1"


@dataclass(frozen=True)
class LatexMathMLExtraction:
    schema_version: str
    parser: str
    parser_version: str
    input_hash: str
    latex: str
    display_mode: bool
    mathml: str
    parse_tree: Any
    node_counts: dict[str, int]
    relation_hints: dict[str, int]
    warnings: tuple[str, ...]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


class KaTeXMathMLExtractor:
    def __init__(
        self,
        *,
        node_path: str | None = None,
        katex_js: Path | None = None,
    ) -> None:
        self.node_path = node_path or shutil.which("node") or ""
        self.katex_js = katex_js or _default_katex_path()

    @property
    def available(self) -> bool:
        return bool(self.node_path) and self.katex_js.exists()

    def extract(self, latex: str, *, display_mode: bool = False) -> LatexMathMLExtraction:
        warnings: list[str] = []
        text = str(latex or "")
        input_hash = _hash({"latex": text, "display_mode": bool(display_mode), "schema": LATEX_MATHML_SCHEMA_VERSION})
        if not self.available:
            return LatexMathMLExtraction(
                schema_version=LATEX_MATHML_SCHEMA_VERSION,
                parser="katex",
                parser_version="unavailable",
                input_hash=input_hash,
                latex=text,
                display_mode=bool(display_mode),
                mathml="",
                parse_tree=[],
                node_counts={},
                relation_hints={},
                warnings=("katex_node_runtime_unavailable",),
            )
        payload = self._run_node(text, display_mode=display_mode)
        warnings.extend(str(item) for item in payload.get("warnings", []) if item)
        parse_tree = payload.get("parse_tree", [])
        return LatexMathMLExtraction(
            schema_version=LATEX_MATHML_SCHEMA_VERSION,
            parser="katex",
            parser_version=str(payload.get("parser_version", "")),
            input_hash=input_hash,
            latex=text,
            display_mode=bool(display_mode),
            mathml=str(payload.get("mathml", "") or ""),
            parse_tree=parse_tree,
            node_counts=_node_counts(parse_tree),
            relation_hints=_relation_hints(parse_tree),
            warnings=tuple(sorted(set(warnings))),
        )

    def extract_batch(self, items: list[dict[str, Any]]) -> list[LatexMathMLExtraction]:
        normalized = [
            {"latex": str(item.get("latex", "") or ""), "display_mode": bool(item.get("display_mode"))}
            for item in items
        ]
        if not normalized:
            return []
        if not self.available:
            return [self.extract(item["latex"], display_mode=bool(item["display_mode"])) for item in normalized]
        payload = self._run_node_batch(normalized)
        rows = payload.get("rows", [])
        if not isinstance(rows, list) or len(rows) != len(normalized):
            return [self.extract(item["latex"], display_mode=bool(item["display_mode"])) for item in normalized]
        result: list[LatexMathMLExtraction] = []
        for item, row in zip(normalized, rows):
            if not isinstance(row, dict):
                row = {"warnings": ["katex_batch_row_non_object"]}
            text = item["latex"]
            display_mode = bool(item["display_mode"])
            input_hash = _hash({"latex": text, "display_mode": display_mode, "schema": LATEX_MATHML_SCHEMA_VERSION})
            parse_tree = row.get("parse_tree", [])
            result.append(
                LatexMathMLExtraction(
                    schema_version=LATEX_MATHML_SCHEMA_VERSION,
                    parser="katex",
                    parser_version=str(payload.get("parser_version", "")),
                    input_hash=input_hash,
                    latex=text,
                    display_mode=display_mode,
                    mathml=str(row.get("mathml", "") or ""),
                    parse_tree=parse_tree,
                    node_counts=_node_counts(parse_tree),
                    relation_hints=_relation_hints(parse_tree),
                    warnings=tuple(sorted({str(w) for w in row.get("warnings", []) if w})),
                )
            )
        return result

    def _run_node(self, latex: str, *, display_mode: bool) -> dict[str, Any]:
        script = _node_script(self.katex_js)
        payload = {"latex": latex, "display_mode": bool(display_mode)}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False)
            input_path = Path(handle.name)
        try:
            completed = subprocess.run(
                [self.node_path, "-e", script, str(input_path)],
                cwd=str(_project_root()),
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
        finally:
            try:
                input_path.unlink()
            except OSError:
                pass
        if completed.returncode != 0:
            return {
                "parser_version": "",
                "mathml": "",
                "parse_tree": [],
                "warnings": [f"katex_node_failed:{completed.stderr.strip()[:200]}"],
            }
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return {
                "parser_version": "",
                "mathml": "",
                "parse_tree": [],
                "warnings": ["katex_node_invalid_json"],
            }
        return value if isinstance(value, dict) else {"warnings": ["katex_node_non_object_json"]}

    def _run_node_batch(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        script = _node_batch_script(self.katex_js)
        payload = {"items": items}
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
            json.dump(payload, handle, ensure_ascii=False)
            input_path = Path(handle.name)
        try:
            completed = subprocess.run(
                [self.node_path, "-e", script, str(input_path)],
                cwd=str(_project_root()),
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=max(20, min(300, len(items) // 10 + 20)),
                check=False,
            )
        finally:
            try:
                input_path.unlink()
            except OSError:
                pass
        if completed.returncode != 0:
            return {"rows": [], "parser_version": "", "warnings": [f"katex_batch_failed:{completed.stderr.strip()[:200]}"]}
        try:
            value = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return {"rows": [], "parser_version": "", "warnings": ["katex_batch_invalid_json"]}
        return value if isinstance(value, dict) else {"rows": [], "warnings": ["katex_batch_non_object_json"]}


def extract_many(
    rows: list[dict[str, Any]],
    *,
    latex_key: str = "label_latex",
    limit: int = 0,
    batch_size: int = 512,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    extractor = KaTeXMathMLExtractor()
    output: list[dict[str, Any]] = []
    warnings: dict[str, int] = {}
    selected = rows[: max(0, int(limit))] if limit > 0 else rows
    chunk_size = max(1, int(batch_size or 512))
    for offset in range(0, len(selected), chunk_size):
        chunk = selected[offset : offset + chunk_size]
        batch = [
            {"latex": str(row.get(latex_key, "") or ""), "display_mode": str(row.get("kind", "")) == "display"}
            for row in chunk
        ]
        extracted_rows = extractor.extract_batch(batch)
        for row, extracted_obj in zip(chunk, extracted_rows):
            extracted = extracted_obj.to_json()
            for warning in extracted.get("warnings", []):
                warnings[str(warning)] = warnings.get(str(warning), 0) + 1
            output.append(
                {
                    "row_id": row.get("row_id", ""),
                    "case": row.get("case", ""),
                    "kind": row.get("kind", ""),
                    "page_num": row.get("page_num"),
                    "input_hash": row.get("input_hash", ""),
                    "latex_key": latex_key,
                    "mathml_extraction": extracted,
                }
            )
    manifest = {
        "schema_version": "latex_mathml_katex_manifest_v1",
        "rows": len(output),
        "extractor_available": extractor.available,
        "batch_size": chunk_size,
        "warnings": dict(sorted(warnings.items())),
        "notes": [
            "KaTeX extraction is for training/audit labels only.",
            "Production born-digital parsing must not read source LaTeX.",
        ],
    }
    return output, manifest


def read_jsonl(path: Path, *, limit: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                rows.append(value)
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def write_extractions(rows: list[dict[str, Any]], manifest: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "latex_mathml_rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    (output_dir / "latex_mathml_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _node_script(katex_js: Path) -> str:
    js_path = str(katex_js).replace("\\", "\\\\")
    return f"""
const fs = require('fs');
const katex = require('{js_path}');
const payload = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
const warnings = [];
function cleanAst(value, seen) {{
  if (value === null || value === undefined) {{
    return value;
  }}
  if (typeof value !== 'object') {{
    return value;
  }}
  if (!seen) {{
    seen = new WeakSet();
  }}
  if (seen.has(value)) {{
    return undefined;
  }}
  seen.add(value);
  if (Array.isArray(value)) {{
    return value.map((item) => cleanAst(item, seen)).filter((item) => item !== undefined);
  }}
  const output = {{}};
  for (const [key, item] of Object.entries(value)) {{
    if (key === 'loc' || key === 'lexer' || key === 'settings' || key === 'tokenRegex' || key === 'catcodes') {{
      continue;
    }}
    if (typeof item === 'function') {{
      continue;
    }}
    const cleaned = cleanAst(item, seen);
    if (cleaned !== undefined) {{
      output[key] = cleaned;
    }}
  }}
  return output;
}}
let mathml = '';
let parseTree = [];
try {{
  mathml = katex.renderToString(payload.latex || '', {{
    output: 'mathml',
    displayMode: !!payload.display_mode,
    throwOnError: false,
    strict: 'ignore'
  }});
}} catch (err) {{
  warnings.push('katex_render_error:' + String(err && err.message || err).slice(0, 160));
}}
try {{
  parseTree = katex.__parse(payload.latex || '', {{
    displayMode: !!payload.display_mode,
    throwOnError: false,
    strict: 'ignore'
  }});
  parseTree = cleanAst(parseTree);
}} catch (err) {{
  warnings.push('katex_parse_error:' + String(err && err.message || err).slice(0, 160));
}}
console.log(JSON.stringify({{
  parser_version: katex.version || '',
  mathml,
  parse_tree: parseTree,
  warnings
}}));
"""


def _node_batch_script(katex_js: Path) -> str:
    js_path = str(katex_js).replace("\\", "\\\\")
    return f"""
const fs = require('fs');
const katex = require('{js_path}');
const payload = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));
function cleanAst(value, seen) {{
  if (value === null || value === undefined) {{
    return value;
  }}
  if (typeof value !== 'object') {{
    return value;
  }}
  if (!seen) {{
    seen = new WeakSet();
  }}
  if (seen.has(value)) {{
    return undefined;
  }}
  seen.add(value);
  if (Array.isArray(value)) {{
    return value.map((item) => cleanAst(item, seen)).filter((item) => item !== undefined);
  }}
  const output = {{}};
  for (const [key, item] of Object.entries(value)) {{
    if (key === 'loc' || key === 'lexer' || key === 'settings' || key === 'tokenRegex' || key === 'catcodes') {{
      continue;
    }}
    if (typeof item === 'function') {{
      continue;
    }}
    const cleaned = cleanAst(item, seen);
    if (cleaned !== undefined) {{
      output[key] = cleaned;
    }}
  }}
  return output;
}}
function extract(item) {{
  const warnings = [];
  let mathml = '';
  let parseTree = [];
  try {{
    mathml = katex.renderToString(item.latex || '', {{
      output: 'mathml',
      displayMode: !!item.display_mode,
      throwOnError: false,
      strict: 'ignore'
    }});
  }} catch (err) {{
    warnings.push('katex_render_error:' + String(err && err.message || err).slice(0, 160));
  }}
  try {{
    parseTree = katex.__parse(item.latex || '', {{
      displayMode: !!item.display_mode,
      throwOnError: false,
      strict: 'ignore'
    }});
    parseTree = cleanAst(parseTree);
  }} catch (err) {{
    warnings.push('katex_parse_error:' + String(err && err.message || err).slice(0, 160));
  }}
  return {{ mathml, parse_tree: parseTree, warnings }};
}}
const items = Array.isArray(payload.items) ? payload.items : [];
console.log(JSON.stringify({{
  parser_version: katex.version || '',
  rows: items.map(extract)
}}));
"""


def _node_counts(value: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in _walk_nodes(value):
        node_type = str(node.get("type", "") or "")
        if node_type:
            counts[node_type] = counts.get(node_type, 0) + 1
    return dict(sorted(counts.items()))


def _relation_hints(value: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in _walk_nodes(value):
        node_type = str(node.get("type", "") or "")
        if node_type == "supsub":
            if node.get("sup") is not None:
                counts["SUP"] = counts.get("SUP", 0) + 1
            if node.get("sub") is not None:
                counts["SUB"] = counts.get("SUB", 0) + 1
        elif node_type == "genfrac":
            counts["FRACTION_BAR"] = counts.get("FRACTION_BAR", 0) + 1
        elif node_type == "sqrt":
            counts["RADICAL_BODY"] = counts.get("RADICAL_BODY", 0) + 1
        elif node_type in {"array", "aligned"}:
            counts["ALIGNMENT"] = counts.get("ALIGNMENT", 0) + 1
        elif node_type == "accent":
            counts["OVERLINE"] = counts.get("OVERLINE", 0) + 1
    return dict(sorted(counts.items()))


def _walk_nodes(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            found.append(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def _default_katex_path() -> Path:
    return _project_root() / "src" / "ui" / "katex.min.js"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

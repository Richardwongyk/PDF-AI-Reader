"""JSON worker for process-isolated formula tools.

The main app calls this script from dedicated tool environments.  It keeps
heavy OCR/MFR imports out of the reader process and returns candidate-only
results for persistence.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


def _extract_latex(item: object) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("rec_formula", "text", "latex", "formula", "rec_text"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("res", "result", "data", "output", "prediction"):
            latex = _extract_latex(item.get(key))
            if latex:
                return latex
        return ""
    for attr in ("json", "to_json"):
        value = getattr(item, attr, None)
        if value is None:
            continue
        if callable(value):
            try:
                value = value()
            except TypeError:
                continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        latex = _extract_latex(value)
        if latex:
            return latex
    for attr in ("rec_formula", "text", "latex", "formula", "rec_text"):
        value = getattr(item, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _extract_score(item: object) -> float | None:
    for key in ("score", "confidence", "prob", "probability"):
        value = item.get(key) if isinstance(item, dict) else getattr(item, key, None)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int | float):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _run_paddle(items: list[dict[str, str]], model: str) -> list[dict[str, Any]]:
    from paddleocr import FormulaRecognition

    model_name = model or "PP-FormulaNet_plus-S"
    started = time.perf_counter()
    recognizer = FormulaRecognition(model_name=model_name, device="cpu")
    init_ms = int((time.perf_counter() - started) * 1000)
    paths = [item["image_path"] for item in items]
    started = time.perf_counter()
    outputs = list(recognizer.predict(input=paths, batch_size=max(1, min(4, len(paths)))))
    predict_ms = int((time.perf_counter() - started) * 1000)
    results: list[dict[str, Any]] = []
    per_item_ms = int(predict_ms / max(1, len(paths)))
    for item, output in zip(items, outputs, strict=False):
        results.append(
            {
                "candidate_id": item["candidate_id"],
                "latex": _extract_latex(output),
                "score": _extract_score(output),
                "model": "paddle_formula",
                "model_version": model_name,
                "preprocess_version": "png-v1",
                "duration_ms": per_item_ms,
                "warnings": [f"model_init_ms:{init_ms}"],
                "raw": str(output),
            }
        )
    return results


def _run_pix2text(items: list[dict[str, str]]) -> list[dict[str, Any]]:
    from pix2text import Pix2Text

    started = time.perf_counter()
    recognizer = Pix2Text(enable_formula=True, enable_table=False, device="cpu")
    init_ms = int((time.perf_counter() - started) * 1000)
    results: list[dict[str, Any]] = []
    for item in items:
        started = time.perf_counter()
        output = recognizer.recognize_formula(item["image_path"], return_text=False)
        duration_ms = int((time.perf_counter() - started) * 1000)
        results.append(
            {
                "candidate_id": item["candidate_id"],
                "latex": _extract_latex(output),
                "score": _extract_score(output),
                "model": "pix2text_formula",
                "model_version": "pix2text",
                "preprocess_version": "png-v1",
                "duration_ms": duration_ms,
                "warnings": [f"model_init_ms:{init_ms}"],
                "raw": output,
            }
        )
    return results


def _run_mineru_pdf_page(
    items: list[dict[str, str]],
    model: str,
    output_root: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    groups: dict[tuple[str, int], list[dict[str, str]]] = {}
    for item in items:
        pdf_path = str(item.get("pdf_path", "") or "")
        try:
            page_num = int(item.get("page_num", -1))
        except (TypeError, ValueError):
            page_num = -1
        if not pdf_path or page_num < 0:
            results.append(
                _failed_item(
                    item,
                    "mineru_missing_pdf_page_context",
                    model="mineru_hybrid_formula",
                    model_version=model or "hybrid-auto-engine",
                    preprocess_version="pdf-page-txt-v1",
                )
            )
            continue
        groups.setdefault((pdf_path, page_num), []).append(item)

    for (pdf_path, page_num), group_items in groups.items():
        page_out = output_root / f"mineru_p{page_num}"
        page_out.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        cmd = [
            sys.executable,
            "-m",
            "mineru.cli.client",
            "-p",
            pdf_path,
            "-o",
            str(page_out),
            "-b",
            model or "hybrid-auto-engine",
            "-m",
            "txt",
            "-s",
            str(page_num),
            "-e",
            str(page_num),
            "-f",
            "true",
            "-t",
            "false",
            "--image-analysis",
            "false",
        ]
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=900,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        if proc.returncode != 0:
            warning = (proc.stderr or proc.stdout or "mineru_failed")[:240]
            results.extend(
                _failed_item(
                    item,
                    f"mineru_failed:{warning}",
                    model="mineru_hybrid_formula",
                    model_version=model or "hybrid-auto-engine",
                    preprocess_version="pdf-page-txt-v1",
                )
                for item in group_items
            )
            continue
        candidates = _extract_mineru_formulas(page_out)
        for index, item in enumerate(group_items):
            latex = candidates[index] if index < len(candidates) else (candidates[0] if candidates else "")
            results.append(
                {
                    "candidate_id": item["candidate_id"],
                    "latex": latex,
                    "score": None,
                    "model": "mineru_hybrid_formula",
                    "model_version": model or "hybrid-auto-engine",
                    "preprocess_version": "pdf-page-txt-v1",
                    "duration_ms": duration_ms,
                    "warnings": [] if latex else ["mineru_no_formula_candidate"],
                    "raw": {
                        "output_dir": str(page_out),
                        "candidate_count": len(candidates),
                    },
                }
            )
    return results


def _run_pek_unimernet(items: list[dict[str, str]]) -> list[dict[str, Any]]:
    try:
        import unimernet  # type: ignore  # noqa: F401
    except Exception as exc:
        return [
            _failed_item(
                item,
                f"pek_unimernet_unavailable:{str(exc)[:180]}",
                model="pek_unimernet",
                model_version="pdf-extract-kit",
            )
            for item in items
        ]
    return [
        _failed_item(
            item,
            "pek_unimernet_worker_not_implemented",
            model="pek_unimernet",
            model_version="pdf-extract-kit",
        )
        for item in items
    ]


def _extract_mineru_formulas(output_dir: Path) -> list[str]:
    formulas: list[str] = []
    for path in sorted(output_dir.rglob("*")):
        if path.suffix.lower() not in {".md", ".json"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        formulas.extend(_formula_snippets_from_text(text))
    unique: dict[str, str] = {}
    for formula in formulas:
        compact = " ".join(str(formula or "").split())
        if compact:
            unique.setdefault(compact, compact)
    return list(unique.values())


def _formula_snippets_from_text(text: str) -> list[str]:
    snippets: list[str] = []
    patterns = [
        r"\$\$(.+?)\$\$",
        r"\\\[(.+?)\\\]",
        r"\\\((.+?)\\\)",
        r"<formula[^>]*>(.+?)</formula>",
    ]
    for pattern in patterns:
        snippets.extend(
            match.strip()
            for match in re.findall(pattern, text, flags=re.DOTALL)
            if match.strip()
        )
    return snippets


def _failed_item(
    item: dict[str, str],
    warning: str,
    *,
    model: str = "external_formula_tool",
    model_version: str = "",
    preprocess_version: str = "png-v1",
) -> dict[str, Any]:
    return {
        "candidate_id": item.get("candidate_id", ""),
        "latex": "",
        "score": None,
        "model": model,
        "model_version": model_version,
        "preprocess_version": preprocess_version,
        "duration_ms": 0,
        "warnings": [f"tool_failed:{warning}"],
        "raw": {},
    }


def main_with_args_for_test(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
    raw_items = payload.get("items", [])
    items = []
    for item in raw_items:
        if not isinstance(item, dict) or not item.get("candidate_id") or not item.get("image_path"):
            continue
        normalized: dict[str, Any] = {
            "candidate_id": str(item.get("candidate_id", "")),
            "image_path": str(item.get("image_path", "")),
        }
        for key in ("pdf_path", "page_num", "bbox"):
            if key in item:
                normalized[key] = item[key]
        items.append(normalized)
    if args.backend == "paddle_formula":
        results = _run_paddle(items, args.model)
    elif args.backend == "pix2text_formula":
        results = _run_pix2text(items)
    elif args.backend == "mineru_pdf_page":
        results = _run_mineru_pdf_page(items, args.model, Path(args.output).parent)
    elif args.backend == "pek_unimernet":
        results = _run_pek_unimernet(items)
    else:
        raise ValueError(f"unsupported backend: {args.backend}")
    Path(args.output).write_text(
        json.dumps({"backend": args.backend, "results": results}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return 0


def main() -> int:
    return main_with_args_for_test()


if __name__ == "__main__":
    raise SystemExit(main())

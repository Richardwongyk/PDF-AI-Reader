"""JSON worker for process-isolated formula tools.

The main app calls this script from dedicated tool environments.  It keeps
heavy OCR/MFR imports out of the reader process and returns candidate-only
results for persistence.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    raw_items = payload.get("items", [])
    items = [
        {
            "candidate_id": str(item.get("candidate_id", "")),
            "image_path": str(item.get("image_path", "")),
        }
        for item in raw_items
        if isinstance(item, dict) and item.get("candidate_id") and item.get("image_path")
    ]
    if args.backend == "paddle_formula":
        results = _run_paddle(items, args.model)
    elif args.backend == "pix2text_formula":
        results = _run_pix2text(items)
    else:
        raise ValueError(f"unsupported backend: {args.backend}")
    Path(args.output).write_text(
        json.dumps({"backend": args.backend, "results": results}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

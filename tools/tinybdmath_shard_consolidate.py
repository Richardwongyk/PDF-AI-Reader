"""Consolidate TinyBDMath page shards without rerunning PDF extraction."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tinybdmath_dataset_audit import audit_dataset
from tools.tinybdmath_gold_audit import audit_gold
from tools.tinybdmath_sharded_dataset import SHARD_PREPROCESS_VERSION
from tools.tinybdmath_training_data import build_training_rows, _write_jsonl


def consolidate_shards(output_dir: Path, *, min_age_sec: float = 5.0) -> dict[str, Any]:
    started = time.perf_counter()
    dataset_dir = output_dir / "dataset"
    training_dir = output_dir / "training"
    manifest_dir = output_dir / "manifests"
    shard_dir = output_dir / "page_shards"

    source_rows = _source_rows(manifest_dir)
    candidate_rows: list[dict[str, Any]] = []
    feature_rows: list[dict[str, Any]] = []
    shard_counts: dict[str, int] = {}
    failed_shards: list[dict[str, Any]] = []
    skipped_active_shards: list[str] = []
    now = time.time()
    for path in sorted(shard_dir.glob("*/page_*.json")):
        try:
            age_sec = now - path.stat().st_mtime
        except OSError:
            continue
        if age_sec < min_age_sec:
            skipped_active_shards.append(str(path))
            continue
        payload = _read_json(path)
        case = str(payload.get("case", path.parent.name))
        shard_counts[case] = shard_counts.get(case, 0) + 1
        if payload.get("status") != "done":
            failed_shards.append({"path": str(path), "status": payload.get("status", ""), "error": payload.get("error", "")})
            continue
        if payload.get("preprocess_version") != SHARD_PREPROCESS_VERSION:
            failed_shards.append({"path": str(path), "status": "stale_preprocess_version", "error": str(payload.get("preprocess_version", ""))})
            continue
        candidate_rows.extend(row for row in payload.get("candidate_rows", []) if isinstance(row, dict))
        feature_rows.extend(row for row in payload.get("feature_rows", []) if isinstance(row, dict))

    dataset_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(dataset_dir / "source_formulas.jsonl", source_rows)
    _write_jsonl(dataset_dir / "pdf_candidates.jsonl", candidate_rows)
    _write_jsonl(dataset_dir / "feature_graphs.jsonl", feature_rows)
    rows, training_report = build_training_rows(dataset_dir)
    _write_jsonl(training_dir / "tinybdmath_rows.jsonl", [asdict(row) for row in rows])
    _write_json(training_dir / "tinybdmath_report.json", asdict(training_report))
    dataset_audit = audit_dataset(dataset_dir, training_dir)
    gold_audit = audit_gold(dataset_dir)
    _write_json(output_dir / "dataset_audit.json", dataset_audit)
    _write_json(output_dir / "gold_audit.json", gold_audit)
    summary = {
        "schema_version": "tinybdmath_shard_consolidate_v1",
        "output_dir": str(output_dir),
        "preprocess_version": SHARD_PREPROCESS_VERSION,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "shards": shard_counts,
        "failed_shards": failed_shards[:100],
        "skipped_active_shards": skipped_active_shards[:100],
        "min_shard_age_sec": min_age_sec,
        "source_formulas": len(source_rows),
        "pdf_candidates": len(candidate_rows),
        "feature_graphs": len(feature_rows),
        "training_rows": len(rows),
        "training": asdict(training_report),
        "dataset_audit": dataset_audit,
        "gold_audit": gold_audit,
        "note": "This consolidation reuses existing page shards and does not parse PDFs.",
    }
    _write_json(output_dir / "summary.json", summary)
    return summary


def _source_rows(manifest_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(manifest_dir.glob("*_source_formulas.jsonl")):
        rows.extend(_read_jsonl(path))
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            value = json.loads(text)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--min-age-sec",
        type=float,
        default=5.0,
        help="Skip page shard files modified more recently than this many seconds.",
    )
    args = parser.parse_args()

    payload = consolidate_shards(args.output_dir, min_age_sec=max(0.0, args.min_age_sec))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

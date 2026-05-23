"""Audit and optionally clear test logs.

The desktop E2E runner clears app logs before each run, but this tool gives a
small standalone entry point for checking the latest logs without opening large
files by hand.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
APP_LOG = LOG_DIR / "app.log"


def _read_tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]


def _series(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "max": 0.0, "avg": 0.0}
    return {
        "count": len(values),
        "max": round(max(values), 1),
        "avg": round(sum(values) / len(values), 1),
    }


def audit_logs(tail_lines: int = 4000) -> dict[str, Any]:
    lines = _read_tail(APP_LOG, tail_lines)
    levels = {"CRITICAL": 0, "ERROR": 0, "WARNING": 0}
    for line in lines:
        for level in levels:
            if f"[{level}]" in line:
                levels[level] += 1

    zoom_ms: list[float] = []
    render_ms: list[float] = []
    visible_update_ms: list[float] = []
    for line in lines:
        if match := re.search(r"缩放完成 \(([\d.]+)ms", line):
            zoom_ms.append(float(match.group(1)))
        if match := re.search(r"切片\+绘制 \(([\d.]+)ms\)", line):
            render_ms.append(float(match.group(1)))
        if match := re.search(r"_update_visible_pages 耗时 ([\d.]+)ms", line):
            visible_update_ms.append(float(match.group(1)))

    patterns = {
        "document_loaded": "文档加载完成",
        "kb_build": "知识库构建完成",
        "split_opened": "裂缝已打开",
        "qa_finished": "问答完成",
        "followups": "追问建议",
        "formula_scan": "MFD",
    }
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app_log": str(APP_LOG.relative_to(ROOT)),
        "exists": APP_LOG.exists(),
        "size_bytes": APP_LOG.stat().st_size if APP_LOG.exists() else 0,
        "tail_lines": len(lines),
        "levels": levels,
        "counts": {
            name: sum(1 for line in lines if marker in line)
            for name, marker in patterns.items()
        },
        "performance": {
            "zoom_complete_ms": _series(zoom_ms),
            "render_ms": _series(render_ms),
            "visible_update_ms": _series(visible_update_ms),
        },
        "problems": [
            line for line in lines
            if any(f"[{level}]" in line for level in levels)
        ][-80:],
        "tail": lines[-80:],
    }


def clear_logs(keep_awake: bool = True) -> list[str]:
    LOG_DIR.mkdir(exist_ok=True)
    removed: list[str] = []
    for path in LOG_DIR.glob("*.log"):
        if keep_awake and path.name.startswith("keep_awake"):
            continue
        try:
            path.unlink()
            removed.append(str(path.relative_to(ROOT)))
        except PermissionError:
            removed.append(f"locked:{path.relative_to(ROOT)}")
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="Clear log files before reporting.")
    parser.add_argument(
        "--include-keep-awake",
        action="store_true",
        help="Also clear keep-awake logs when --clear is used.",
    )
    parser.add_argument("--tail-lines", type=int, default=4000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload: dict[str, Any] = {}
    if args.clear:
        payload["removed"] = clear_logs(keep_awake=not args.include_keep_awake)
    payload["audit"] = audit_logs(tail_lines=max(1, args.tail_lines))

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    levels = payload["audit"]["levels"]
    return 1 if any(levels.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())

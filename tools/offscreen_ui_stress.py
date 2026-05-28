"""Offscreen UI stress test for scroll/zoom/navigation hot paths.

This tool avoids pyautogui and does not take over the user's desktop.  It
drives PdfViewer directly under Qt's offscreen platform and writes a JSON
report that is suitable for long background runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "--visible" in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "windows"
else:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QApplication

from src.core.models import BlockType, DocumentBlock, ParseResult, SplitMode
from src.core.pdf_engine import DocumentEngine
from src.infra.page_cache import PageCache
from src.ui.pdf_viewer import PdfViewer
from src.ui.split_widget import WebViewPool


@dataclass
class StressReport:
    case: str
    pdf: str
    pages: int
    open_first_page_sec: float
    layout_sec: float
    actions: list[dict[str, Any]]
    metrics: dict[str, Any]
    ok: bool
    violations: list[str]


class _Config:
    pass


def _cases() -> dict[str, Path]:
    test_dir = ROOT / "测试资料"
    return {
        "attention": test_dir / "Attention is all you need.pdf",
        "napkin": test_dir / "Napkin.pdf",
    }


def _process_events(ms: int = 0) -> None:
    app = QApplication.instance()
    if app is None:
        return
    if ms <= 0:
        app.processEvents()
        return
    deadline = time.perf_counter() + ms / 1000.0
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.005)


def _wait_for(condition: Any, timeout: float) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        _process_events()
        if condition():
            return True
        time.sleep(0.01)
    return bool(condition())


def _series(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "max": 0.0, "p95": 0.0, "avg": 0.0}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return {
        "count": len(values),
        "max": round(max(values), 3),
        "p95": round(p95, 3),
        "avg": round(sum(values) / len(values), 3),
    }


def _action(actions: list[dict[str, Any]], name: str, start: float, **extra: Any) -> float:
    elapsed = (time.perf_counter() - start) * 1000
    actions.append({"name": name, "ms": round(elapsed, 3), **extra})
    return elapsed


def _sample_blocks(page_count: int) -> list[DocumentBlock]:
    pages = sorted({0, min(10, page_count - 1), min(50, page_count - 1), min(250, page_count - 1)})
    blocks: list[DocumentBlock] = []
    for page in pages:
        blocks.append(
            DocumentBlock(
                id=f"p{page}_b0",
                page_num=page,
                block_type=BlockType.PARAGRAPH,
                content=(
                    "This is a synthetic interaction block used for offscreen "
                    "scroll and translation split stress testing."
                ),
                bbox=(72.0, 96.0, 420.0, 150.0),
            )
        )
    return blocks


def _open_engine(pdf: Path, timeout: float = 45) -> tuple[DocumentEngine, ParseResult, float]:
    engine = DocumentEngine(PageCache(max_cache_size_mb=256))
    captured: list[ParseResult] = []
    errors: list[str] = []
    engine.parse_finished.connect(lambda result: captured.append(result))
    engine.parse_error.connect(lambda message: errors.append(message))
    start = time.perf_counter()
    engine.open_document(str(pdf))
    if not _wait_for(lambda: bool(captured or errors), timeout):
        raise TimeoutError(f"first-page parse timed out: {pdf}")
    if errors:
        raise RuntimeError(errors[-1])
    return engine, captured[-1], time.perf_counter() - start


def _fast_scroll_phase(
    viewer: PdfViewer,
    actions: list[dict[str, Any]],
    *,
    phase: str,
    multiplier: int,
    pace_ms: int = 0,
) -> list[float]:
    durations: list[float] = []
    bar = viewer.verticalScrollBar()
    max_value = max(1, bar.maximum())
    large_step = max(2400, max_value // 8)
    repeats = max(80, int(multiplier) * 4)
    total_units = 0
    for index in range(repeats):
        direction = 1 if index % 40 < 28 else -1
        delta = large_step * direction
        target = max(0, min(max_value, bar.value() + delta))
        start = time.perf_counter()
        bar.setValue(target)
        viewer._update_visible_pages()
        durations.append(_action(actions, "offscreen_fast_scroll", start, phase=phase, index=index))
        if pace_ms > 0:
            _process_events(pace_ms)
        total_units += abs(delta)
    actions.append(
        {
            "name": "offscreen_scroll_phase_total",
            "phase": phase,
            "events": repeats,
            "total_scroll_units": int(total_units),
        }
    )
    return durations


def _jump_phase(
    viewer: PdfViewer,
    actions: list[dict[str, Any]],
    pages: list[int],
    phase: str,
    pace_ms: int = 0,
) -> list[float]:
    durations: list[float] = []
    for index, page in enumerate(pages):
        start = time.perf_counter()
        viewer.scroll_to_page(page)
        durations.append(_action(actions, "offscreen_jump_page", start, phase=phase, index=index, page=page + 1))
        _process_events(max(1, pace_ms))
    return durations


def _zoom_phase(
    viewer: PdfViewer,
    actions: list[dict[str, Any]],
    steps: int,
    direction: str,
    phase: str,
    pace_ms: int = 0,
) -> list[float]:
    durations: list[float] = []
    for index in range(steps):
        start = time.perf_counter()
        if direction == "in":
            viewer.zoom_in()
        else:
            viewer.zoom_out()
        durations.append(_action(actions, f"offscreen_zoom_{direction}", start, phase=phase, index=index))
        _process_events(max(1, pace_ms))
    return durations


def _open_translation_split(viewer: PdfViewer, engine: DocumentEngine, result: ParseResult, actions: list[dict[str, Any]]) -> None:
    if not result.blocks:
        return
    block = result.blocks[0]
    viewer.scroll_to_page(block.page_num)
    viewer._update_visible_pages()
    if block.page_num not in viewer._rendered_pages:
        viewer._render_page(block.page_num)
        _wait_for(lambda: block.page_num in viewer._rendered_pages, 5)
    start = time.perf_counter()
    split = viewer.open_split_widget(block.id, SplitMode.TRANSLATION)
    _action(actions, "offscreen_open_translation_split", start, block_id=block.id, opened=split is not None)


def run_case(
    case: str,
    multiplier: int,
    *,
    visible: bool = False,
    pace_ms: int = 0,
    hold_open_sec: int = 0,
) -> StressReport:
    pdf = _cases()[case]
    app = QApplication.instance() or QApplication([])
    WebViewPool.prewarm()
    _wait_for(lambda: bool(getattr(WebViewPool._standby, "_engine_ready", False)), 5)
    engine, first_result, open_sec = _open_engine(pdf)
    page_count = first_result.page_count
    blocks = first_result.blocks or _sample_blocks(page_count)
    if not first_result.blocks:
        first_result = ParseResult(
            filepath=first_result.filepath,
            title=first_result.title,
            author=first_result.author,
            page_count=first_result.page_count,
            toc=first_result.toc,
            blocks=blocks,
            parsed_pages=sorted({block.page_num for block in blocks}),
        )
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(1280, 900)
    if visible:
        viewer.setWindowTitle(f"PDF AI Reader UI stress - {case} {multiplier}x")
        viewer.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    layout_start = time.perf_counter()
    viewer.load_document(first_result)
    viewer.show()
    if visible:
        viewer.showMaximized()
        viewer.raise_()
        viewer.activateWindow()
    _process_events(100)
    layout_sec = time.perf_counter() - layout_start

    jump_pages = [0, min(10, page_count - 1), min(50, page_count - 1), min(250, page_count - 1), page_count - 1]
    actions: list[dict[str, Any]] = []
    scroll_ms: list[float] = []
    jump_ms: list[float] = []
    zoom_ms: list[float] = []

    scroll_ms.extend(_fast_scroll_phase(viewer, actions, phase="baseline", multiplier=multiplier, pace_ms=pace_ms))
    jump_ms.extend(_jump_phase(viewer, actions, jump_pages + list(reversed(jump_pages)), "baseline", pace_ms=pace_ms))
    zoom_ms.extend(_zoom_phase(viewer, actions, 10, "in", "extreme_zoom", pace_ms=pace_ms))
    scroll_ms.extend(_fast_scroll_phase(viewer, actions, phase="extreme_zoom", multiplier=multiplier, pace_ms=pace_ms))
    jump_ms.extend(_jump_phase(viewer, actions, jump_pages + list(reversed(jump_pages)), "extreme_zoom", pace_ms=pace_ms))
    _open_translation_split(viewer, engine, first_result, actions)
    _process_events(max(1, pace_ms))
    scroll_ms.extend(_fast_scroll_phase(viewer, actions, phase="translation_split_open", multiplier=multiplier, pace_ms=pace_ms))
    zoom_ms.extend(_zoom_phase(viewer, actions, 6, "in", "translation_split_extreme_zoom", pace_ms=pace_ms))
    scroll_ms.extend(_fast_scroll_phase(viewer, actions, phase="translation_split_extreme_zoom", multiplier=multiplier, pace_ms=pace_ms))
    zoom_ms.extend(_zoom_phase(viewer, actions, 16, "out", "restore", pace_ms=pace_ms))
    if visible and hold_open_sec > 0:
        _process_events(hold_open_sec * 1000)

    metrics = {
        "scroll_ms": _series(scroll_ms),
        "jump_ms": _series(jump_ms),
        "zoom_ms": _series(zoom_ms),
        "total_scroll_units": sum(
            int(item.get("total_scroll_units", 0))
            for item in actions
            if item.get("name") == "offscreen_scroll_phase_total"
        ),
        "actions": {
            name: sum(1 for item in actions if item.get("name") == name)
            for name in {str(item.get("name")) for item in actions}
        },
    }
    violations: list[str] = []
    if open_sec > (8.0 if case == "napkin" else 4.0):
        violations.append(f"first-page open too slow: {open_sec:.2f}s")
    if layout_sec > 1.5:
        violations.append(f"viewer layout too slow: {layout_sec:.2f}s")
    if metrics["scroll_ms"]["p95"] > 80.0:  # type: ignore[index,operator]
        violations.append(f"scroll p95 too slow: {metrics['scroll_ms']['p95']}ms")
    if metrics["jump_ms"]["p95"] > 180.0:  # type: ignore[index,operator]
        violations.append(f"jump p95 too slow: {metrics['jump_ms']['p95']}ms")
    if metrics["zoom_ms"]["p95"] > 300.0:  # type: ignore[index,operator]
        violations.append(f"zoom p95 too slow: {metrics['zoom_ms']['p95']}ms")
    if metrics["total_scroll_units"] < 120 * max(1, multiplier) * 80:  # type: ignore[operator]
        violations.append("scroll stress floor not reached")

    engine.close_document()
    viewer.close()
    app.processEvents()
    return StressReport(
        case=case,
        pdf=str(pdf),
        pages=page_count,
        open_first_page_sec=round(open_sec, 4),
        layout_sec=round(layout_sec, 4),
        actions=actions,
        metrics=metrics,
        ok=not violations,
        violations=violations,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run offscreen UI scroll/zoom stress.")
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="napkin")
    parser.add_argument("--stress-multiplier", type=int, default=400)
    parser.add_argument("--output", default="test_artifacts/offscreen_ui_stress/report.json")
    parser.add_argument("--visible", action="store_true", help="Show the viewer on the Windows desktop.")
    parser.add_argument("--pace-ms", type=int, default=0, help="Delay between actions for visible inspection.")
    parser.add_argument("--hold-open-sec", type=int, default=0, help="Keep the viewer open after a visible run.")
    args = parser.parse_args()

    selected = list(_cases()) if args.case == "all" else [args.case]
    reports = [
        run_case(
            case,
            max(1, args.stress_multiplier),
            visible=args.visible,
            pace_ms=max(0, args.pace_ms),
            hold_open_sec=max(0, args.hold_open_sec),
        )
        for case in selected
    ]
    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stress_multiplier": max(1, args.stress_multiplier),
        "reports": [asdict(report) for report in reports],
    }
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(report.ok for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())

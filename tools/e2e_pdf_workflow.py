"""End-to-end desktop workflow test for PDF AI Reader.

This script intentionally drives the real Windows desktop window. It uses:
- pywinauto: process/window lifecycle
- pyautogui: real mouse, scroll, hotkeys, screenshots

It is not a pytest test because the long Napkin workflow is a product/perf gate,
not a quick unit-test gate.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from itertools import count
from pathlib import Path
from typing import Any

import pyautogui
from pywinauto import Application


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.born_digital_math_audit import audit_pdf as audit_born_digital_pdf
from tools.formula_latex_audit import _audit_case as audit_formula_latex_case

PYTHON = Path(r"C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe")
APP_LOG = ROOT / "logs" / "app.log"
ARTIFACT_DIR = ROOT / "test_artifacts" / "e2e"
COMMAND_FILE = ARTIFACT_DIR / "commands.jsonl"
EVENT_FILE = ARTIFACT_DIR / "events.jsonl"
FORMULA_ARTIFACT_DIR = ROOT / "test_artifacts" / "formula_audit"
_COMMAND_COUNTER = count(1)

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.03


@dataclass
class PdfCase:
    name: str
    pdf: Path
    latex_root: Path
    expected_min_pages: int
    scroll_steps: int
    jump_pages: list[int]
    performance_budget: dict[str, float]


@dataclass
class CaseResult:
    name: str
    pdf: str
    latex_root: str
    stress_multiplier: int
    launched_sec: float
    opened_sec: float
    window_title: str
    screenshots: list[str]
    actions: list[dict[str, Any]]
    log_summary: dict[str, Any]
    performance: dict[str, Any]
    image_metrics: dict[str, Any]
    formula_audit: dict[str, Any]
    ok: bool
    error: str = ""


def _cases() -> list[PdfCase]:
    test_dir = ROOT / "测试资料"
    return [
        PdfCase(
            name="attention",
            pdf=test_dir / "Attention is all you need.pdf",
            latex_root=test_dir / "Attention is all you need LaTeX源代码和资料，用于与PDF版是否扫描正确进行对照",
            expected_min_pages=10,
            scroll_steps=10,
            jump_pages=[0, 2, 8, 14],
            performance_budget={
                "max_zoom_complete_ms": 250.0,
                "max_render_ms": 150.0,
                "max_visible_update_ms": 350.0,
                "max_spike_factor": 2.0,
                "min_zoom_complete_count": 4.0,
            },
        ),
        PdfCase(
            name="napkin",
            pdf=test_dir / "Napkin.pdf",
            latex_root=test_dir / "Napkin LaTeX源代码，用于和原版PDF对照",
            expected_min_pages=100,
            scroll_steps=28,
            jump_pages=[0, 10, 50, 120, 250, 420, 620, 820, 1000, 1049],
            performance_budget={
                "max_zoom_complete_ms": 450.0,
                "max_render_ms": 250.0,
                "max_visible_update_ms": 600.0,
                "max_spike_factor": 2.0,
                "min_zoom_complete_count": 4.0,
            },
        ),
    ]


def _bounded_stress_count(multiplier: int, *, minimum: int, cap: int) -> int:
    multiplier = max(1, int(multiplier))
    minimum = max(0, int(minimum))
    cap = max(minimum, int(cap))
    if multiplier <= 1:
        return minimum
    return min(cap, max(minimum, minimum + int(multiplier ** 0.5)))


def _interleave_extremes(pages: Sequence[int]) -> list[int]:
    ordered = sorted(dict.fromkeys(max(0, int(p)) for p in pages))
    result: list[int] = []
    left = 0
    right = len(ordered) - 1
    while left <= right:
        result.append(ordered[left])
        if left != right:
            result.append(ordered[right])
        left += 1
        right -= 1
    return result


def _stress_pages(pages: Sequence[int], multiplier: int) -> list[int]:
    base = [max(0, int(p)) for p in pages]
    if not base:
        return []
    multiplier = max(1, int(multiplier))
    if multiplier <= 1:
        return base

    max_page = max(base)
    target = _bounded_stress_count(
        multiplier,
        minimum=max(len(set(base)), min(max_page + 1, 12)),
        cap=min(max_page + 1, 56),
    )
    if target <= 1 or max_page <= 0:
        samples = [0]
    else:
        samples = [round(i * max_page / (target - 1)) for i in range(target)]

    anchors = set(base)
    anchors.update(samples)
    anchors.update({0, max_page, max_page // 2})
    return _interleave_extremes(sorted(anchors))


def _zoom_cycle_count(multiplier: int) -> int:
    return _bounded_stress_count(multiplier, minimum=4, cap=12)


def _translation_toggle_pairs(multiplier: int) -> int:
    return _bounded_stress_count(multiplier, minimum=1, cap=8)


def _translation_request_count(multiplier: int) -> int:
    if int(multiplier) <= 1:
        return 0
    return _bounded_stress_count(multiplier, minimum=2, cap=12)


def _formula_scan_iterations(multiplier: int) -> int:
    return _bounded_stress_count(multiplier, minimum=1, cap=8)


def _stress_case(case: PdfCase, multiplier: int) -> PdfCase:
    multiplier = max(1, int(multiplier))
    if multiplier <= 1:
        return case
    budget = dict(case.performance_budget)
    budget["min_zoom_complete_count"] = float(
        max(int(budget.get("min_zoom_complete_count", 0) or 0), _zoom_cycle_count(multiplier) * 2)
    )
    return PdfCase(
        name=case.name,
        pdf=case.pdf,
        latex_root=case.latex_root,
        expected_min_pages=case.expected_min_pages,
        scroll_steps=_bounded_stress_count(multiplier, minimum=case.scroll_steps, cap=80),
        jump_pages=_stress_pages(case.jump_pages, multiplier),
        performance_budget=budget,
    )


def _reset_logs() -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    if APP_LOG.exists():
        try:
            APP_LOG.unlink()
        except PermissionError:
            _terminate_existing_reader_processes()
            for _ in range(20):
                try:
                    APP_LOG.unlink()
                    break
                except PermissionError:
                    time.sleep(0.25)
            else:
                raise


def _clear_history_logs() -> None:
    """Clear stale app logs before a closed-loop test run."""
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)
    for path in logs_dir.glob("*.log"):
        if path.name.startswith("keep_awake"):
            continue
        try:
            path.unlink()
        except PermissionError:
            _terminate_existing_reader_processes()
            try:
                path.unlink()
            except PermissionError:
                pass


def _terminate_existing_reader_processes() -> None:
    """Close stale app instances that keep app.log locked between E2E runs."""
    if os.name != "nt":
        return
    script = r"""
$selfPid = $PID
Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $selfPid -and
    @('python.exe','pythonw.exe','conda.exe','cmd.exe') -contains $_.Name -and
    $_.CommandLine -match 'src[\\/]+main\.py'
} | Select-Object -ExpandProperty ProcessId
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return
    pids: list[int] = []
    for raw in result.stdout.splitlines():
        raw = raw.strip()
        if not raw.isdigit():
            continue
        pid = int(raw)
        if pid and pid not in pids:
            pids.append(pid)
    for pid in pids:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    if pids:
        time.sleep(1.0)


def _reset_bridge_files() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    for path in (COMMAND_FILE, EVENT_FILE):
        if path.exists():
            path.unlink()


def _send_command(command: dict[str, Any]) -> str:
    COMMAND_FILE.parent.mkdir(parents=True, exist_ok=True)
    command = dict(command)
    command_id = str(command.get("command_id") or f"{int(time.time() * 1000)}-{next(_COMMAND_COUNTER)}")
    command["command_id"] = command_id
    with COMMAND_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(command, ensure_ascii=False) + "\n")
    return command_id


def _read_events() -> list[dict[str, Any]]:
    if not EVENT_FILE.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in EVENT_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _wait_for_event(
    event: str,
    start_index: int = 0,
    timeout: float = 15,
    command_id: str | None = None,
    predicate: Any | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = _read_events()
        for item in events[start_index:]:
            if item.get("event") != event:
                continue
            if command_id is not None and item.get("command_id") != command_id:
                continue
            if predicate is not None and not predicate(item):
                continue
            return item
        time.sleep(0.2)
    suffix = f" command_id={command_id}" if command_id else ""
    raise TimeoutError(f"event not found within {timeout:.1f}s: {event}{suffix}")


def _send_and_wait(
    command: dict[str, Any],
    event: str,
    timeout: float = 15,
    predicate: Any | None = None,
) -> dict[str, Any]:
    start_index = len(_read_events())
    command_id = _send_command(command)
    return _wait_for_event(
        event,
        start_index,
        timeout=timeout,
        command_id=command_id,
        predicate=predicate,
    )


def _scroll_to_page(page: int, timeout: float = 20) -> dict[str, Any]:
    page = max(0, int(page))
    return _send_and_wait(
        {"cmd": "scroll_to_page", "page": page},
        "scrolled_to_page",
        timeout=timeout,
        predicate=lambda item: int(item.get("page", -1)) == page,
    )


def _tail_log(lines: int = 120) -> list[str]:
    if not APP_LOG.exists():
        return []
    data = APP_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    return data[-lines:]


def _summarize_log() -> dict[str, Any]:
    lines = _tail_log(4000)
    levels = {"ERROR": 0, "CRITICAL": 0, "WARNING": 0}
    for line in lines:
        for level in levels:
            if f"[{level}]" in line:
                levels[level] += 1
    patterns = {
        "document_loaded": "文档加载完成",
        "parse_complete": "全量解析完成",
        "kb_build": "知识库构建完成",
        "qa_retrieval": "AskQuestionFlow: 检索到",
        "answer_finished": "问答完成",
        "split_opened": "裂缝已打开",
        "zoom": "PdfViewer: 缩放",
        "rendered": "全页 pixmap 就绪",
        "formula_scan": "MFD",
    }
    counts = {
        name: sum(1 for line in lines if pattern in line)
        for name, pattern in patterns.items()
    }
    return {
        "path": str(APP_LOG.relative_to(ROOT)),
        "size_bytes": APP_LOG.stat().st_size if APP_LOG.exists() else 0,
        "line_count_tail": len(lines),
        "levels": levels,
        "counts": counts,
        "tail": _tail_log(80),
    }


def _percentile(values: Sequence[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * ratio
    lo = int(index)
    hi = min(lo + 1, len(ordered) - 1)
    weight = index - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _series_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "max": 0.0, "p95": 0.0, "avg": 0.0}
    return {
        "count": len(values),
        "max": round(max(values), 1),
        "p95": round(_percentile(values, 0.95), 1),
        "avg": round(sum(values) / len(values), 1),
    }


def _summarize_action_coverage(actions: Sequence[dict[str, Any]], stress_multiplier: int) -> dict[str, Any]:
    counts: dict[str, int] = {}
    phases: dict[str, int] = {}
    total_wheel_units = 0
    for item in actions:
        name = str(item.get("name") or "")
        counts[name] = counts.get(name, 0) + 1
        phase = str(item.get("phase") or "")
        if phase:
            phases[phase] = phases.get(phase, 0) + 1
        total_wheel_units += abs(int(item.get("total_wheel_units") or 0))

    multiplier = max(1, int(stress_multiplier))
    required = {
        "large_wheel_burst": 3,
        "continuous_fast_scroll": 3,
        "reverse_fast_scroll": 3,
        "extreme_zoom_in": 9,
        "extreme_zoom_out": 9,
        "extreme_zoom_jump_page": 4,
        "double_click_toggle": _translation_toggle_pairs(multiplier) * 2,
        "translation_request_stress": _translation_request_count(multiplier),
        "formula_scan_stress": _formula_scan_iterations(multiplier),
    }
    required_phases = {
        "baseline",
        "extreme_zoom",
        "translation_split_open",
        "translation_split_extreme_zoom",
    }
    violations = [
        f"{name}: {counts.get(name, 0)} < {minimum}"
        for name, minimum in required.items()
        if counts.get(name, 0) < minimum
    ]
    for phase in sorted(required_phases):
        if phases.get(phase, 0) <= 0:
            violations.append(f"phase missing: {phase}")
    if total_wheel_units < 120 * multiplier * 4:
        violations.append(f"wheel_units: {total_wheel_units} below stress floor")
    return {
        "counts": counts,
        "phases": phases,
        "total_wheel_units": total_wheel_units,
        "violations": violations,
        "within_budget": not violations,
    }


def _summarize_performance(case: PdfCase) -> dict[str, Any]:
    lines = _tail_log(4000)
    zoom_complete_ms: list[float] = []
    render_ms: list[float] = []
    visible_update_ms: list[float] = []
    stale_render_drops = 0

    for line in lines:
        if "丢弃过期" in line:
            stale_render_drops += 1
        if match := re.search(r"缩放完成 \(([\d.]+)ms", line):
            zoom_complete_ms.append(float(match.group(1)))
        if match := re.search(r"切片\+绘制 \(([\d.]+)ms\)", line):
            render_ms.append(float(match.group(1)))
        if match := re.search(r"_update_visible_pages 耗时 ([\d.]+)ms", line):
            visible_update_ms.append(float(match.group(1)))

    budget = case.performance_budget
    violations: list[str] = []

    def check_latency(name: str, values: Sequence[float], budget_key: str) -> None:
        if not values:
            violations.append(f"{name}: no samples")
            return
        limit = budget.get(budget_key)
        if limit is not None:
            p95 = _percentile(values, 0.95)
            max_allowed = limit * float(budget.get("max_spike_factor", 2.0))
            if p95 > limit:
                violations.append(f"{name}: p95 {p95:.1f}ms > {limit:.1f}ms")
            if max(values) > max_allowed:
                violations.append(f"{name}: max {max(values):.1f}ms > {max_allowed:.1f}ms")

    check_latency("zoom_complete_ms", zoom_complete_ms, "max_zoom_complete_ms")
    check_latency("render_ms", render_ms, "max_render_ms")
    check_latency("visible_update_ms", visible_update_ms, "max_visible_update_ms")
    min_zoom = int(budget.get("min_zoom_complete_count", 0))
    if len(zoom_complete_ms) < min_zoom:
        violations.append(f"zoom_complete_count: {len(zoom_complete_ms)} < {min_zoom}")
    if len(render_ms) < max(1, len(zoom_complete_ms)):
        violations.append(f"render_count: {len(render_ms)} < zoom_complete_count {len(zoom_complete_ms)}")

    return {
        "budget": budget,
        "zoom_complete_ms": _series_summary(zoom_complete_ms),
        "render_ms": _series_summary(render_ms),
        "visible_update_ms": _series_summary(visible_update_ms),
        "stale_render_drops": stale_render_drops,
        "violations": violations,
        "within_budget": not violations,
    }


def _summarize_image_metrics(screenshots: list[str]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    violations: list[str] = []
    if not screenshots:
        return {"screenshots": metrics, "violations": ["no screenshots"], "within_budget": False}

    try:
        from PIL import Image, ImageFilter, ImageStat
    except Exception as exc:
        return {
            "screenshots": metrics,
            "violations": [f"Pillow unavailable: {exc}"],
            "within_budget": False,
        }

    for relative in screenshots:
        path = ROOT / relative
        try:
            with Image.open(path) as img:
                gray = img.convert("L")
                gray.thumbnail((640, 640))
                if hasattr(gray, "get_flattened_data"):
                    pixels = list(gray.get_flattened_data())
                else:
                    pixels = list(gray.getdata())
                total = max(1, len(pixels))
                nonwhite_ratio = sum(1 for pixel in pixels if pixel < 245) / total
                stat = ImageStat.Stat(gray)
                edges = gray.filter(ImageFilter.FIND_EDGES)
                edge_mean = ImageStat.Stat(edges).mean[0]
                item = {
                    "width": img.width,
                    "height": img.height,
                    "nonwhite_ratio": round(nonwhite_ratio, 4),
                    "edge_mean": round(edge_mean, 2),
                    "variance": round(stat.var[0], 1),
                }
        except Exception as exc:
            item = {"error": repr(exc)}
            violations.append(f"{relative}: unreadable screenshot")

        metrics[relative] = item
        if "error" in item:
            continue
        if item["nonwhite_ratio"] < 0.05:
            violations.append(f"{relative}: likely blank")
        if item["edge_mean"] < 2.0:
            violations.append(f"{relative}: low edge detail")
        if item["variance"] < 40.0:
            violations.append(f"{relative}: low luminance variance")

    return {
        "screenshots": metrics,
        "violations": violations,
        "within_budget": not violations,
    }


def _latest_log_match(pattern: str) -> str:
    regex = re.compile(pattern)
    for line in reversed(_tail_log(4000)):
        if regex.search(line):
            return line
    return ""


def _wait_for_log(pattern: str, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = _latest_log_match(pattern)
        if match:
            return match
        time.sleep(0.25)
    raise TimeoutError(f"log pattern not found within {timeout:.1f}s: {pattern}")


def _wait_for_event_or_log(
    event: str,
    start_index: int,
    log_pattern: str,
    timeout: float,
    command_id: str | None = None,
) -> str:
    deadline = time.monotonic() + timeout
    regex = re.compile(log_pattern)
    while time.monotonic() < deadline:
        events = _read_events()
        for item in events[start_index:]:
            if item.get("event") == event and (
                command_id is None or item.get("command_id") == command_id
            ):
                return "event"
        for line in reversed(_tail_log(4000)):
            if regex.search(line):
                return "log"
        time.sleep(0.2)
    raise TimeoutError(f"neither event nor log found within {timeout:.1f}s: {event} / {log_pattern}")


def _launch(case: PdfCase) -> tuple[subprocess.Popen[str], Any, float]:
    t0 = time.perf_counter()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    if os.name == "nt" and env.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        env.pop("QT_QPA_PLATFORM", None)
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu-sandbox")
    proc = subprocess.Popen(
        [
            str(PYTHON),
            "src/main.py",
            "--test-mode",
            "--open",
            str(case.pdf),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        app = Application(backend="uia").connect(process=proc.pid, timeout=30)
        window = _wait_for_main_window(app, timeout=60)
        window.set_focus()
        return proc, window, time.perf_counter() - t0
    except Exception:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        raise


def _wait_for_main_window(app: Application, timeout: float = 60) -> Any:
    deadline = time.monotonic() + timeout
    last_titles: list[str] = []
    title_match = None
    fallback = None
    while time.monotonic() < deadline:
        try:
            candidates = app.windows()
        except Exception:
            candidates = []
        last_titles = []
        for window in candidates:
            try:
                title = window.window_text()
                last_titles.append(title)
                rect = window.rectangle()
                has_rect = rect.width() > 0 and rect.height() > 0
                if has_rect and fallback is None:
                    fallback = window
                if "PDF AI Reader" in title:
                    title_match = window
                    if has_rect:
                        try:
                            window.wait("exists enabled", timeout=5)
                        except Exception:
                            pass
                        return window
            except Exception:
                continue
        if title_match is not None:
            return title_match
        if fallback is not None and time.monotonic() > deadline - min(timeout, 5):
            return fallback
        time.sleep(0.25)
    raise TimeoutError(f"main window not visible within {timeout:.1f}s; titles={last_titles!r}")


def _window_center(window: Any) -> tuple[int, int]:
    rect = window.rectangle()
    return (int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2))


def _content_point(window: Any, x_ratio: float = 0.48, y_ratio: float = 0.48) -> tuple[int, int]:
    rect = window.rectangle()
    x = int(rect.left + rect.width() * x_ratio)
    y = int(rect.top + rect.height() * y_ratio)
    return x, y


def _safe_move_to_window(window: Any) -> tuple[int, int]:
    x, y = _content_point(window, 0.50, 0.50)
    try:
        screen_width, screen_height = pyautogui.size()
        x = max(8, min(int(screen_width) - 8, x))
        y = max(8, min(int(screen_height) - 8, y))
    except Exception:
        pass
    pyautogui.moveTo(x, y, duration=0.05)
    return x, y


def _first_block_point(window: Any) -> tuple[int, int] | None:
    try:
        blocks = window.descendants(control_type="Pane")
    except Exception:
        return None
    candidates = []
    for block in blocks:
        try:
            name = block.element_info.name or ""
            if not name.startswith("block:"):
                continue
            rect = block.rectangle()
            if rect.width() < 30 or rect.height() < 10:
                continue
            candidates.append((rect.top, rect.left, rect))
        except Exception:
            continue
    if not candidates:
        return None
    _, _, rect = sorted(candidates)[0]
    return (int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2))


def _pick_visible_block_point(page: int = 0) -> tuple[str, tuple[int, int] | None, dict[str, Any]]:
    event = _send_and_wait({"cmd": "pick_block", "page": page}, "block_picked", timeout=10)
    block_id = str(event.get("block_id") or "")
    center = event.get("center")
    scale = _screen_scale_from_event(event)
    if isinstance(center, list) and len(center) == 2:
        return block_id, (int(center[0] * scale), int(center[1] * scale)), event
    return block_id, None, event


def _screen_scale_from_event(event: dict[str, Any]) -> float:
    screen = event.get("screen") or {}
    width = screen.get("width")
    height = screen.get("height")
    if not width or not height:
        return 1.0
    try:
        screenshot = pyautogui.screenshot()
        sx = screenshot.width / float(width)
        sy = screenshot.height / float(height)
        if 0.5 <= sx <= 4.0 and 0.5 <= sy <= 4.0:
            return (sx + sy) / 2.0
    except Exception:
        return 1.0
    return 1.0


def _point_in_window(window: Any, point: tuple[int, int]) -> bool:
    try:
        rect = window.rectangle()
        x, y = point
        return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom
    except Exception:
        return False


def _snapshot_state(timeout: float = 10) -> dict[str, Any]:
    return _send_and_wait({"cmd": "snapshot_state"}, "state", timeout=timeout)


def _wait_for_split_count_at_least(count: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_state: dict[str, Any] = {}
    while time.monotonic() < deadline:
        state = _snapshot_state(timeout=min(3.0, max(0.5, deadline - time.monotonic())))
        last_state = state
        if int(state.get("split_count", 0)) >= count:
            return state
        time.sleep(0.3)
    raise TimeoutError(f"split_count did not reach {count}: {last_state}")


def _first_split_id(state: dict[str, Any]) -> str:
    splits = state.get("splits") or []
    return str(splits[0]) if splits else ""


def _split_collapsed(state: dict[str, Any], block_id: str) -> bool | None:
    collapsed = state.get("split_collapsed") or {}
    if block_id not in collapsed:
        return None
    return bool(collapsed[block_id])


def _set_split_collapsed(block_id: str, expected_collapsed: bool, timeout: float = 20) -> bool:
    event = _send_and_wait(
        {
            "cmd": "set_split_collapsed",
            "block_id": block_id,
            "collapsed": expected_collapsed,
        },
        "split_state_set",
        timeout=timeout,
    )
    return event.get("collapsed") is expected_collapsed


def _double_click_translation_cycle(
    window: Any,
    actions: list[dict[str, Any]],
    toggle_pairs: int = 1,
) -> tuple[str, str]:
    _scroll_to_page(0, timeout=20)
    time.sleep(0.8)
    point = _first_block_point(window)
    picked_block_id = ""
    method = "mouse"
    if point is None:
        try:
            picked_block_id, point, pick_event = _pick_visible_block_point(page=0)
            if point is not None and not _point_in_window(window, point):
                point = None
                method = "bridge_fallback_offscreen_geometry"
            elif point is not None:
                method = "mouse_bridge_geometry"
        except Exception:
            pick_event = {}
            point = None
    if point is None:
        method = "bridge_fallback"
        t = time.perf_counter()
        command = {"cmd": "open_translation", "page": 0}
        if picked_block_id:
            command["block_id"] = picked_block_id
        event = _send_and_wait(command, "translation_requested", timeout=20)
        block_id = str(event.get("block_id") or "")
        _action(actions, "double_click_open_fallback", t, block_id=block_id)
    else:
        t = time.perf_counter()
        pyautogui.doubleClick(*point, interval=0.08)
        try:
            state = _wait_for_split_count_at_least(1, timeout=20)
            block_id = _first_split_id(state)
        except TimeoutError:
            if picked_block_id:
                event = _send_and_wait(
                    {"cmd": "open_translation", "block_id": picked_block_id},
                    "translation_requested",
                    timeout=20,
                )
                block_id = str(event.get("block_id") or "")
                method = f"{method}_bridge_recovery"
            else:
                raise
        _action(
            actions,
            "double_click_open",
            t,
            block_id=block_id,
            point=point,
            method=method,
            picked_block_id=picked_block_id,
            pick_event=_json_safe(pick_event),
        )
    if not block_id:
        raise RuntimeError("translation split did not open")

    states = [state for _ in range(max(1, int(toggle_pairs))) for state in (True, False)]
    for index, expected_collapsed in enumerate(states):
        t = time.perf_counter()
        used_bridge_fallback = False
        if point is None:
            _send_and_wait(
                {"cmd": "toggle_split", "block_id": block_id},
                "split_toggled",
                timeout=20,
            )
        else:
            pyautogui.doubleClick(*point, interval=0.08)
        deadline = time.monotonic() + 8
        last_state: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last_state = _snapshot_state(timeout=3)
            collapsed = _split_collapsed(last_state, block_id)
            if collapsed is expected_collapsed:
                break
            time.sleep(0.3)
        else:
            used_bridge_fallback = True
            if not _set_split_collapsed(block_id, expected_collapsed):
                last_state = _snapshot_state(timeout=3)
                raise TimeoutError(
                    f"split collapse state did not become {expected_collapsed}: {last_state}"
                )
        _action(
            actions,
            "double_click_toggle",
            t,
            index=index,
            block_id=block_id,
            collapsed=expected_collapsed,
            point=point,
            bridge_fallback=used_bridge_fallback,
        )
    return block_id, method


def _stress_translation_requests(
    block_id: str,
    case: PdfCase,
    multiplier: int,
    actions: list[dict[str, Any]],
) -> None:
    extra_requests = _translation_request_count(multiplier)
    pages = _stress_pages([0, *case.jump_pages[:3]], max(1, multiplier))
    for index in range(extra_requests):
        t = time.perf_counter()
        page = pages[index % len(pages)] if pages else 0
        command: dict[str, Any] = {"cmd": "open_translation", "page": max(0, int(page))}
        if index == 0 and block_id:
            command["block_id"] = block_id
        event = _send_and_wait(command, "translation_requested", timeout=20)
        _wait_for_split_count_at_least(1, timeout=20)
        _action(
            actions,
            "translation_request_stress",
            t,
            index=index,
            page=page + 1,
            block_id=str(event.get("block_id") or ""),
        )


def _run_formula_scan_stress(case: PdfCase, multiplier: int, actions: list[dict[str, Any]]) -> None:
    iterations = _formula_scan_iterations(multiplier)
    pages = _stress_pages([0, *case.jump_pages[:4]], iterations)
    for index in range(iterations):
        page = pages[index % len(pages)] if pages else 0
        t = time.perf_counter()
        _scroll_to_page(max(0, int(page)), timeout=20)
        time.sleep(0.2)
        page_event = _send_and_wait(
            {"cmd": "run_formula_page_scan_batch"},
            "formula_page_scan_requested",
            timeout=20,
        )
        scan_index = len(_read_events())
        scan_command_id = _send_command({"cmd": "high_precision_formula_scan"})
        scan_event = _wait_for_event(
            "formula_scan_requested",
            scan_index,
            timeout=20,
            command_id=scan_command_id,
        )
        deadline = time.monotonic() + 12
        finished_event: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            for item in _read_events()[scan_index:]:
                if (
                    item.get("event") == "formula_scan_finished"
                    and item.get("command_id") == scan_command_id
                ):
                    finished_event = item
            if finished_event is not None:
                break
            state = _snapshot_state(timeout=3)
            formula = state.get("formula") or {}
            if not formula.get("formula_running"):
                break
            time.sleep(0.5)
        final_state = _snapshot_state(timeout=5)
        _action(
            actions,
            "formula_scan_stress",
            t,
            index=index,
            page=page + 1,
            page_scan_started=bool(page_event.get("started")),
            formula_before=_json_safe(scan_event.get("before")),
            formula_after_request=_json_safe(scan_event.get("after")),
            formula_finished=_json_safe(finished_event),
            formula_final=_json_safe(final_state.get("formula")),
        )


def _json_safe(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _formula_audit(case: PdfCase) -> dict[str, Any]:
    start = time.perf_counter()
    FORMULA_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    born_pages = 6 if case.name == "attention" else 20
    born_start = 0 if case.name == "attention" else 60
    latex_max_pages = 0 if case.name == "attention" else 120
    try:
        born_report = audit_born_digital_pdf(
            pdf_path=case.pdf,
            start_page=born_start,
            max_pages=born_pages,
            sample_limit=6,
            latex_root=case.latex_root,
        )
        latex_report = audit_formula_latex_case(
            case,
            run_mfd=False,
            mfd_pages=None,
            max_pages=latex_max_pages,
            max_match_candidates=60,
            min_command_recall=0.35,
            min_weak_match_rate=0.35,
            max_low_similarity_pdf_rate=0.60,
            born_digital_math=True,
            born_digital_semantics=True,
            legacy_formula_heuristic=False,
            match_scope="display",
        )
        latex_payload = asdict(latex_report)
        gate_passed = bool(latex_report.quality_gate["passed"])
        payload = {
            "ok": bool(
                born_report.get("unknown_glyph_count", 0) == 0
                and born_report.get("pages", 0) > 0
                and gate_passed
            ),
            "elapsed_sec": round(time.perf_counter() - start, 3),
            "born_digital": born_report,
            "latex_alignment": latex_payload,
        }
        if not gate_passed:
            payload["expected_quality_gate_failure"] = True
            payload["reason"] = "born-digital display formula path is below LaTeX alignment gate"
        out_path = FORMULA_ARTIFACT_DIR / f"{case.name}_closed_loop_formula.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["artifact"] = str(out_path.relative_to(ROOT))
        return payload
    except Exception as exc:
        return {
            "ok": False,
            "elapsed_sec": round(time.perf_counter() - start, 3),
            "error": repr(exc),
            "traceback": traceback.format_exc(limit=12),
        }


def _screenshot(case_dir: Path, label: str) -> str:
    path = case_dir / f"{label}.png"
    img = pyautogui.screenshot()
    img.save(path)
    return str(path.relative_to(ROOT))


def _action(actions: list[dict[str, Any]], name: str, start: float, **extra: Any) -> None:
    actions.append({"name": name, "sec": round(time.perf_counter() - start, 3), **extra})


def _wheel_burst(
    actions: list[dict[str, Any]],
    name: str,
    x: int,
    y: int,
    *,
    amount: int,
    repeats: int,
    pause: float,
    phase: str,
) -> None:
    t = time.perf_counter()
    for index in range(max(1, int(repeats))):
        pyautogui.scroll(int(amount), x=x, y=y)
        if pause > 0:
            time.sleep(pause)
    _action(
        actions,
        name,
        t,
        phase=phase,
        amount=amount,
        repeats=repeats,
        pause=pause,
        total_wheel_units=amount * repeats,
    )


def _human_scroll_stress(
    actions: list[dict[str, Any]],
    x: int,
    y: int,
    *,
    multiplier: int,
    phase: str,
) -> None:
    multiplier = max(1, int(multiplier))
    _wheel_burst(
        actions,
        "large_wheel_burst",
        x,
        y,
        amount=-120,
        repeats=max(1, multiplier // 2),
        pause=0.03,
        phase=phase,
    )
    _wheel_burst(
        actions,
        "continuous_fast_scroll",
        x,
        y,
        amount=-90,
        repeats=max(20, multiplier + 10),
        pause=0.0,
        phase=phase,
    )
    _wheel_burst(
        actions,
        "reverse_fast_scroll",
        x,
        y,
        amount=90,
        repeats=max(10, multiplier // 2),
        pause=0.0,
        phase=phase,
    )


def _zoom_to_extreme(actions: list[dict[str, Any]], *, target_steps: int = 9) -> None:
    for index in range(max(1, int(target_steps))):
        t = time.perf_counter()
        pyautogui.hotkey("ctrl", "=")
        time.sleep(0.35)
        _action(actions, "extreme_zoom_in", t, index=index)


def _restore_zoom_from_extreme(actions: list[dict[str, Any]], *, target_steps: int = 9) -> None:
    for index in range(max(1, int(target_steps))):
        t = time.perf_counter()
        pyautogui.hotkey("ctrl", "-")
        time.sleep(0.25)
        _action(actions, "extreme_zoom_out", t, index=index)


def _drive_case(case: PdfCase, stress_multiplier: int = 1) -> CaseResult:
    stress_multiplier = max(1, int(stress_multiplier))
    if not case.pdf.exists():
        raise FileNotFoundError(case.pdf)
    if not case.latex_root.exists():
        raise FileNotFoundError(case.latex_root)

    case_dir = ARTIFACT_DIR / case.name
    if case_dir.exists():
        shutil.rmtree(case_dir)
    case_dir.mkdir(parents=True, exist_ok=True)

    _reset_logs()
    _reset_bridge_files()
    proc: subprocess.Popen[str] | None = None
    screenshots: list[str] = []
    actions: list[dict[str, Any]] = []
    try:
        proc, window, launched_sec = _launch(case)
        window.maximize()
        time.sleep(1.0)
        _safe_move_to_window(window)
        screenshots.append(_screenshot(case_dir, "00_launched"))

        t_open = time.perf_counter()
        _wait_for_log(r"文档加载完成", timeout=90)
        opened_sec = time.perf_counter() - t_open
        screenshots.append(_screenshot(case_dir, "01_document_loaded"))

        x, y = _safe_move_to_window(window)

        _human_scroll_stress(actions, x, y, multiplier=stress_multiplier, phase="baseline")
        for i in range(min(case.scroll_steps, max(10, stress_multiplier))):
            t = time.perf_counter()
            pyautogui.scroll(-40, x=x, y=y)
            time.sleep(0.03)
            _action(actions, "scroll_down", t, index=i)
        screenshots.append(_screenshot(case_dir, "02_scrolled_down"))

        for i, page in enumerate(case.jump_pages):
            t = time.perf_counter()
            _scroll_to_page(page, timeout=20)
            time.sleep(0.15)
            _action(actions, "jump_page", t, page=page + 1, index=i)
        screenshots.append(_screenshot(case_dir, "03_jump_attempts"))

        zoom_action_index = 0
        zoomed_jump_index = 0
        zoom_cycles = _zoom_cycle_count(stress_multiplier)
        pages_per_zoom_cycle = max(1, len(case.jump_pages) // max(1, zoom_cycles))
        for cycle in range(zoom_cycles):
            for step in range(2):
                t = time.perf_counter()
                pyautogui.hotkey("ctrl", "=")
                time.sleep(0.35)
                _action(actions, "zoom_in", t, index=zoom_action_index, cycle=cycle, step=step)
                zoom_action_index += 1
            page_slice = case.jump_pages[
                cycle * pages_per_zoom_cycle:(cycle + 1) * pages_per_zoom_cycle
            ]
            for page in page_slice or case.jump_pages[:1]:
                t = time.perf_counter()
                _scroll_to_page(page, timeout=20)
                time.sleep(0.12)
                _action(
                    actions,
                    "zoomed_jump_page",
                    t,
                    page=page + 1,
                    index=zoomed_jump_index,
                    cycle=cycle,
                )
                zoomed_jump_index += 1
            for step in range(2):
                t = time.perf_counter()
                pyautogui.hotkey("ctrl", "-")
                time.sleep(0.3)
                _action(actions, "zoom_out", t, index=zoom_action_index, cycle=cycle, step=step)
                zoom_action_index += 1
        screenshots.append(_screenshot(case_dir, "04_zoom_cycle_stress"))

        _zoom_to_extreme(actions, target_steps=9)
        _human_scroll_stress(actions, x, y, multiplier=stress_multiplier, phase="extreme_zoom")
        for i, page in enumerate(_stress_pages(case.jump_pages, 2)):
            t = time.perf_counter()
            _scroll_to_page(page, timeout=20)
            time.sleep(0.08)
            _action(actions, "extreme_zoom_jump_page", t, page=page + 1, index=i)
        screenshots.append(_screenshot(case_dir, "04b_extreme_zoom_scroll"))
        _restore_zoom_from_extreme(actions, target_steps=9)
        screenshots.append(_screenshot(case_dir, "05_zoom_out"))

        _scroll_to_page(0, timeout=20)
        time.sleep(1.0)

        _run_formula_scan_stress(case, stress_multiplier, actions)
        screenshots.append(_screenshot(case_dir, "05b_formula_scan_stress"))

        t = time.perf_counter()
        event_index = len(_read_events())
        kb_command_id = _send_command({"cmd": "rebuild_kb"})
        kb_wait_source = _wait_for_event_or_log(
            "kb_rebuilt",
            event_index,
            r"知识库构建完成",
            timeout=90 if case.name == "attention" else 240,
            command_id=kb_command_id,
        )
        _action(actions, "rebuild_kb", t, wait_source=kb_wait_source)

        block_id, translation_method = _double_click_translation_cycle(
            window,
            actions,
            toggle_pairs=_translation_toggle_pairs(stress_multiplier),
        )
        _human_scroll_stress(actions, x, y, multiplier=stress_multiplier, phase="translation_split_open")
        _zoom_to_extreme(actions, target_steps=5)
        _human_scroll_stress(actions, x, y, multiplier=stress_multiplier, phase="translation_split_extreme_zoom")
        _restore_zoom_from_extreme(actions, target_steps=5)
        _stress_translation_requests(block_id, case, stress_multiplier, actions)
        screenshots.append(_screenshot(case_dir, "06_double_click_cycle"))

        t = time.perf_counter()
        _send_and_wait(
            {
                "cmd": "ask_question",
                "block_id": block_id,
                "question": "Summarize the main technical idea using evidence from the full document.",
            },
            "qa_requested",
            timeout=20,
        )
        _wait_for_log(r"(问答完成|知识库中没有检索到可引用片段|知识库还在构建中)", timeout=60)
        _wait_for_log(r"追问建议 split=", timeout=60)
        split_state = _snapshot_state(timeout=10)
        _action(
            actions,
            "ask_question",
            t,
            block_id=block_id,
            followup_count=split_state.get("split_followups", {}).get(block_id, 0),
        )
        screenshots.append(_screenshot(case_dir, "07_qa"))

        t = time.perf_counter()
        _send_and_wait(
            {
                "cmd": "ask_dock_question",
                "question": "Across the full document, what evidence supports the main technical idea?",
            },
            "dock_qa_requested",
            timeout=20,
        )
        _wait_for_log(r"问答完成 split=__dock_qa__", timeout=60)
        _wait_for_log(r"追问建议 split=__dock_qa__", timeout=60)
        state = _snapshot_state(timeout=10)
        _action(
            actions,
            "ask_dock_question",
            t,
            dock_evidence_count=state.get("dock", {}).get("dock_evidence_count", 0),
            dock_answer_chars=state.get("dock", {}).get("dock_answer_chars", 0),
            dock_followup_count=state.get("dock", {}).get("dock_followup_count", 0),
        )
        screenshots.append(_screenshot(case_dir, "08_dock_qa"))

        pyautogui.scroll(-8, x=x, y=y)
        time.sleep(0.8)
        screenshots.append(_screenshot(case_dir, "09_final_scroll"))

        log_summary = _summarize_log()
        performance = _summarize_performance(case)
        image_metrics = _summarize_image_metrics(screenshots)
        action_coverage = _summarize_action_coverage(actions, stress_multiplier)
        formula_audit = _formula_audit(case)
        dock_action = next(
            (item for item in actions if item.get("name") == "ask_dock_question"),
            {},
        )
        ok = (
            log_summary["levels"]["CRITICAL"] == 0
            and log_summary["levels"]["ERROR"] == 0
            and log_summary["levels"]["WARNING"] == 0
            and log_summary["counts"]["document_loaded"] >= 1
            and len(screenshots) >= 6
            and int(dock_action.get("dock_evidence_count", 0)) > 0
            and int(dock_action.get("dock_answer_chars", 0)) > 0
            and int(dock_action.get("dock_followup_count", 0)) > 0
            and performance["within_budget"]
            and image_metrics["within_budget"]
            and action_coverage["within_budget"]
            and formula_audit["ok"]
        )
        if translation_method:
            ok = ok and any(item.get("name") == "double_click_open" for item in actions)
        if case.expected_min_pages > 0:
            title = window.window_text()
            ok = ok and bool(title)
        return CaseResult(
            name=case.name,
            pdf=str(case.pdf),
            latex_root=str(case.latex_root),
            stress_multiplier=stress_multiplier,
            launched_sec=round(launched_sec, 3),
            opened_sec=round(opened_sec, 3),
            window_title=window.window_text(),
            screenshots=screenshots,
            actions=actions,
            log_summary={**log_summary, "events": _read_events()[-80:]},
            performance={**performance, "action_coverage": action_coverage},
            image_metrics=image_metrics,
            formula_audit=_json_safe(formula_audit),
            ok=ok,
        )
    except Exception as exc:
        if proc and proc.poll() is None:
            try:
                screenshots.append(_screenshot(case_dir, "error"))
            except Exception:
                pass
        return CaseResult(
            name=case.name,
            pdf=str(case.pdf),
            latex_root=str(case.latex_root),
            stress_multiplier=stress_multiplier,
            launched_sec=0.0,
            opened_sec=0.0,
            window_title="",
            screenshots=screenshots,
            actions=actions,
            log_summary={**_summarize_log(), "events": _read_events()[-80:]},
            performance={
                **_summarize_performance(case),
                "action_coverage": _summarize_action_coverage(actions, stress_multiplier),
            },
            image_metrics=_summarize_image_metrics(screenshots),
            formula_audit={"ok": False, "error": "workflow failed before formula audit"},
            ok=False,
            error=repr(exc),
        )
    finally:
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PDF AI Reader desktop E2E workflow.")
    parser.add_argument(
        "--case",
        choices=["attention", "napkin", "all"],
        default="all",
        help="Which PDF workflow to run.",
    )
    parser.add_argument(
        "--stress-multiplier",
        type=int,
        default=1,
        help="Multiply scroll, page-turn, zoom, translation, and formula-scan interactions.",
    )
    args = parser.parse_args()
    stress_multiplier = max(1, int(args.stress_multiplier))

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    _clear_history_logs()
    selected = [
        _stress_case(case, stress_multiplier)
        for case in _cases()
        if args.case in ("all", case.name)
    ]
    results = [_drive_case(case, stress_multiplier=stress_multiplier) for case in selected]

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stress_multiplier": stress_multiplier,
        "results": [asdict(result) for result in results],
    }
    report_path = ARTIFACT_DIR / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

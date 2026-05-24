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
                "min_zoom_complete_count": 4.0,
            },
        ),
        PdfCase(
            name="napkin",
            pdf=test_dir / "Napkin.pdf",
            latex_root=test_dir / "Napkin LaTeX源代码，用于和原版PDF对照",
            expected_min_pages=100,
            scroll_steps=28,
            jump_pages=[0, 10, 50, 120, 250],
            performance_budget={
                "max_zoom_complete_ms": 450.0,
                "max_render_ms": 250.0,
                "max_visible_update_ms": 600.0,
                "min_zoom_complete_count": 4.0,
            },
        ),
    ]


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


def _send_command(command: dict[str, Any]) -> None:
    COMMAND_FILE.parent.mkdir(parents=True, exist_ok=True)
    with COMMAND_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(command, ensure_ascii=False) + "\n")


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


def _wait_for_event(event: str, start_index: int = 0, timeout: float = 15) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        events = _read_events()
        for item in events[start_index:]:
            if item.get("event") == event:
                return item
        time.sleep(0.2)
    raise TimeoutError(f"event not found within {timeout:.1f}s: {event}")


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

    def check_max(name: str, values: Sequence[float], budget_key: str) -> None:
        if not values:
            violations.append(f"{name}: no samples")
            return
        limit = budget.get(budget_key)
        if limit is not None and max(values) > limit:
            violations.append(f"{name}: max {max(values):.1f}ms > {limit:.1f}ms")

    check_max("zoom_complete_ms", zoom_complete_ms, "max_zoom_complete_ms")
    check_max("render_ms", render_ms, "max_render_ms")
    check_max("visible_update_ms", visible_update_ms, "max_visible_update_ms")
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
) -> str:
    deadline = time.monotonic() + timeout
    regex = re.compile(log_pattern)
    while time.monotonic() < deadline:
        events = _read_events()
        for item in events[start_index:]:
            if item.get("event") == event:
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
    app = Application(backend="uia").connect(process=proc.pid, timeout=30)
    window = app.window(title_re=".*PDF AI Reader.*")
    window.wait("visible enabled ready", timeout=45)
    window.set_focus()
    return proc, window, time.perf_counter() - t0


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
    event_index = len(_read_events())
    _send_command({"cmd": "pick_block", "page": page})
    event = _wait_for_event("block_picked", event_index, timeout=10)
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


def _snapshot_state(timeout: float = 10) -> dict[str, Any]:
    event_index = len(_read_events())
    _send_command({"cmd": "snapshot_state"})
    return _wait_for_event("state", event_index, timeout=timeout)


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
    event_index = len(_read_events())
    _send_command(
        {
            "cmd": "set_split_collapsed",
            "block_id": block_id,
            "collapsed": expected_collapsed,
        }
    )
    event = _wait_for_event("split_state_set", event_index, timeout=timeout)
    return event.get("collapsed") is expected_collapsed


def _double_click_translation_cycle(window: Any, actions: list[dict[str, Any]]) -> tuple[str, str]:
    point = _first_block_point(window)
    picked_block_id = ""
    method = "mouse"
    if point is None:
        try:
            picked_block_id, point, pick_event = _pick_visible_block_point(page=0)
            if point is not None:
                method = "mouse_bridge_geometry"
        except Exception:
            pick_event = {}
            point = None
    if point is None:
        method = "bridge_fallback"
        t = time.perf_counter()
        event_index = len(_read_events())
        command = {"cmd": "open_translation", "page": 0}
        if picked_block_id:
            command["block_id"] = picked_block_id
        _send_command(command)
        event = _wait_for_event("translation_requested", event_index, timeout=20)
        block_id = str(event.get("block_id") or "")
        _action(actions, "double_click_open_fallback", t, block_id=block_id)
    else:
        t = time.perf_counter()
        pyautogui.doubleClick(*point, interval=0.08)
        state = _wait_for_split_count_at_least(1, timeout=20)
        block_id = _first_split_id(state)
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

    for index, expected_collapsed in enumerate((True, False)):
        t = time.perf_counter()
        used_bridge_fallback = False
        if point is None:
            event_index = len(_read_events())
            _send_command({"cmd": "toggle_split", "block_id": block_id})
            _wait_for_event("split_toggled", event_index, timeout=20)
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


def _drive_case(case: PdfCase) -> CaseResult:
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

        for i in range(case.scroll_steps):
            t = time.perf_counter()
            pyautogui.scroll(-5, x=x, y=y)
            time.sleep(0.2)
            _action(actions, "scroll_down", t, index=i)
        screenshots.append(_screenshot(case_dir, "02_scrolled_down"))

        for i, page in enumerate(case.jump_pages):
            t = time.perf_counter()
            event_index = len(_read_events())
            _send_command({"cmd": "scroll_to_page", "page": page})
            _wait_for_event("scrolled_to_page", event_index, timeout=10)
            time.sleep(0.4)
            _action(actions, "jump_page", t, page=page + 1, index=i)
        screenshots.append(_screenshot(case_dir, "03_jump_attempts"))

        for i in range(2):
            t = time.perf_counter()
            pyautogui.hotkey("ctrl", "=")
            time.sleep(0.8)
            _action(actions, "zoom_in", t, index=i)
        screenshots.append(_screenshot(case_dir, "04_zoom_in"))

        for i in range(2):
            t = time.perf_counter()
            pyautogui.hotkey("ctrl", "-")
            time.sleep(0.8)
            _action(actions, "zoom_out", t, index=i)
        screenshots.append(_screenshot(case_dir, "05_zoom_out"))

        event_index = len(_read_events())
        _send_command({"cmd": "scroll_to_page", "page": 0})
        _wait_for_event("scrolled_to_page", event_index, timeout=10)
        time.sleep(1.0)

        t = time.perf_counter()
        event_index = len(_read_events())
        _send_command({"cmd": "rebuild_kb"})
        kb_wait_source = _wait_for_event_or_log(
            "kb_rebuilt",
            event_index,
            r"知识库构建完成",
            timeout=90 if case.name == "attention" else 240,
        )
        _action(actions, "rebuild_kb", t, wait_source=kb_wait_source)

        block_id, translation_method = _double_click_translation_cycle(window, actions)
        screenshots.append(_screenshot(case_dir, "06_double_click_cycle"))

        t = time.perf_counter()
        event_index = len(_read_events())
        _send_command({
            "cmd": "ask_question",
            "block_id": block_id,
            "question": "Summarize the main technical idea using evidence from the full document.",
        })
        _wait_for_event("qa_requested", event_index, timeout=20)
        _wait_for_log(r"(问答完成|知识库中没有检索到可引用片段|知识库还在构建中)", timeout=60)
        _wait_for_log(r"追问建议 split=", timeout=60)
        event_index = len(_read_events())
        _send_command({"cmd": "snapshot_state"})
        split_state = _wait_for_event("state", event_index, timeout=10)
        _action(
            actions,
            "ask_question",
            t,
            block_id=block_id,
            followup_count=split_state.get("split_followups", {}).get(block_id, 0),
        )
        screenshots.append(_screenshot(case_dir, "07_qa"))

        t = time.perf_counter()
        event_index = len(_read_events())
        _send_command({
            "cmd": "ask_dock_question",
            "question": "Across the full document, what evidence supports the main technical idea?",
        })
        _wait_for_event("dock_qa_requested", event_index, timeout=20)
        _wait_for_log(r"问答完成 split=__dock_qa__", timeout=60)
        _wait_for_log(r"追问建议 split=__dock_qa__", timeout=60)
        event_index = len(_read_events())
        _send_command({"cmd": "snapshot_state"})
        state = _wait_for_event("state", event_index, timeout=10)
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
            launched_sec=round(launched_sec, 3),
            opened_sec=round(opened_sec, 3),
            window_title=window.window_text(),
            screenshots=screenshots,
            actions=actions,
            log_summary={**log_summary, "events": _read_events()[-80:]},
            performance=performance,
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
            launched_sec=0.0,
            opened_sec=0.0,
            window_title="",
            screenshots=screenshots,
            actions=actions,
            log_summary={**_summarize_log(), "events": _read_events()[-80:]},
            performance=_summarize_performance(case),
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
    args = parser.parse_args()

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    _clear_history_logs()
    selected = [case for case in _cases() if args.case in ("all", case.name)]
    results = [_drive_case(case) for case in selected]

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "results": [asdict(result) for result in results],
    }
    report_path = ARTIFACT_DIR / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

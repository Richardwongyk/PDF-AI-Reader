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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pyautogui
from pywinauto import Application
from pywinauto.findwindows import ElementNotFoundError


ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(r"C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe")
APP_LOG = ROOT / "logs" / "app.log"
ARTIFACT_DIR = ROOT / "test_artifacts" / "e2e"
COMMAND_FILE = ARTIFACT_DIR / "commands.jsonl"
EVENT_FILE = ARTIFACT_DIR / "events.jsonl"


@dataclass
class PdfCase:
    name: str
    pdf: Path
    latex_root: Path
    expected_min_pages: int
    scroll_steps: int
    jump_pages: list[int]


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
        ),
        PdfCase(
            name="napkin",
            pdf=test_dir / "Napkin.pdf",
            latex_root=test_dir / "Napkin LaTeX源代码，用于和原版PDF对照",
            expected_min_pages=100,
            scroll_steps=28,
            jump_pages=[0, 10, 50, 120, 250],
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
        "line_count_tail": len(lines),
        "levels": levels,
        "counts": counts,
        "tail": _tail_log(80),
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
        screenshots.append(_screenshot(case_dir, "00_launched"))

        t_open = time.perf_counter()
        _wait_for_log(r"文档加载完成", timeout=90)
        opened_sec = time.perf_counter() - t_open
        screenshots.append(_screenshot(case_dir, "01_document_loaded"))

        x, y = _content_point(window)
        pyautogui.moveTo(x, y, duration=0.1)

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

        block_id = ""
        for i, command in enumerate((
            {"cmd": "open_translation", "page": 0},
            {"cmd": "toggle_split"},
            {"cmd": "toggle_split"},
        )):
            t = time.perf_counter()
            if block_id and "block_id" not in command:
                command["block_id"] = block_id
            event_index = len(_read_events())
            _send_command(command)
            event = _wait_for_event(
                "translation_requested" if i == 0 else "split_toggled",
                event_index,
                timeout=20,
            )
            block_id = str(event.get("block_id") or block_id)
            time.sleep(1.0)
            _action(actions, "translation_cycle", t, index=i, block_id=block_id)
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
        )
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

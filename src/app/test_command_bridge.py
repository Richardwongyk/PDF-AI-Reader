"""Small file-based command bridge for desktop E2E tests.

Enabled only from ``src/main.py --test-mode``. It gives the test runner a stable
way to trigger app-level actions while still using real mouse/scroll/zoom for
the visual workflow.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer

_logger = logging.getLogger(__name__)


class TestCommandBridge(QObject):
    def __init__(self, window: object, command_file: Path, event_file: Path) -> None:
        super().__init__(window)
        self._window = window
        self._command_file = command_file
        self._event_file = event_file
        self._offset = 0
        self._pending_fragment = ""
        self._active_command_id = ""
        self._kb_rebuild_command_id = ""
        self._formula_scan_command_id = ""
        self._kb_rebuild_pending = False
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._window._knowledge_engine.build_finished.connect(self._on_kb_finished)
        self._window._knowledge_engine.build_error.connect(self._on_kb_error)
        formula_flow = getattr(self._window, "_formula_index_flow", None)
        if formula_flow is not None:
            formula_flow.scan_finished.connect(self._on_formula_scan_finished)
        self._emit("ready", {})

    def _poll(self) -> None:
        if not self._command_file.exists():
            return
        try:
            with self._command_file.open("r", encoding="utf-8") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
        except OSError as exc:
            self._emit("error", {"error": repr(exc)})
            return
        if not chunk:
            return
        text = self._pending_fragment + chunk
        if text.endswith("\n"):
            lines = text.splitlines()
            self._pending_fragment = ""
        else:
            parts = text.splitlines()
            if not parts:
                self._pending_fragment = text
                return
            lines = parts[:-1]
            self._pending_fragment = parts[-1]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                command = json.loads(line)
                self._execute(command)
            except Exception as exc:
                self._emit("error", {"command": line, "error": repr(exc)})

    def _execute(self, command: dict[str, Any]) -> None:
        self._active_command_id = str(command.get("command_id") or "")
        cmd = command.get("cmd")
        try:
            if cmd == "scroll_to_page":
                page = max(0, int(command.get("page", 0)))
                self._window._pdf_viewer.scroll_to_page(page)
                self._emit("scrolled_to_page", {"page": page})
                return
            if cmd == "open_translation":
                block_id = self._pick_block(command)
                if not block_id:
                    raise RuntimeError("no block available for open_translation")
                self._window._on_block_double_clicked(block_id)
                self._emit("translation_requested", {"block_id": block_id})
                return
            if cmd == "pick_block":
                block_id = self._pick_block(command)
                if not block_id:
                    raise RuntimeError("no block available for pick_block")
                self._emit("block_picked", {"block_id": block_id, **self._block_geometry(block_id)})
                return
            if cmd == "toggle_split":
                block_id = str(command.get("block_id") or "")
                if not block_id:
                    block_id = self._pick_block(command)
                if not block_id:
                    raise RuntimeError("no block available for toggle_split")
                self._window._on_block_double_clicked(block_id)
                split = self._window._pdf_viewer.find_split_widget(block_id)
                self._emit(
                    "split_toggled",
                    {
                        "block_id": block_id,
                        "collapsed": bool(getattr(split, "collapsed", False)) if split else None,
                    },
                )
                return
            if cmd == "set_split_collapsed":
                block_id = str(command.get("block_id") or "")
                if not block_id:
                    block_id = self._pick_block(command)
                if not block_id:
                    raise RuntimeError("no block available for set_split_collapsed")
                target = bool(command.get("collapsed", False))
                split = self._window._pdf_viewer.find_split_widget(block_id)
                for _ in range(2):
                    if split is not None and bool(getattr(split, "collapsed", False)) is target:
                        break
                    self._window._on_block_double_clicked(block_id)
                    split = self._window._pdf_viewer.find_split_widget(block_id)
                self._emit(
                    "split_state_set",
                    {
                        "block_id": block_id,
                        "collapsed": bool(getattr(split, "collapsed", False)) if split else None,
                        "target": target,
                    },
                )
                return
            if cmd == "rebuild_kb":
                self._kb_rebuild_pending = True
                self._kb_rebuild_command_id = self._active_command_id
                self._window._on_build_knowledge_base()
                self._emit("kb_rebuild_requested", self._state())
                return
            if cmd == "ask_question":
                block_id = self._pick_block(command)
                if not block_id:
                    raise RuntimeError("no block available for ask_question")
                question = str(command.get("question") or "What is this document about?")
                self._window._on_block_question(block_id)
                self._window._on_split_ask(question, block_id)
                self._emit("qa_requested", {"block_id": block_id, "question": question})
                return
            if cmd == "ask_dock_question":
                question = str(command.get("question") or "What is this document about?")
                self._window._ai_question_input.setText(question)
                self._window._on_dock_question_submitted()
                self._emit("dock_qa_requested", {"question": question, **self._dock_state()})
                return
            if cmd == "run_formula_page_scan_batch":
                started = bool(self._window._start_import_page_scan_batch())
                self._emit(
                    "formula_page_scan_requested",
                    {"started": started, **self._formula_state()},
                )
                return
            if cmd == "high_precision_formula_scan":
                before = self._formula_state()
                self._formula_scan_command_id = self._active_command_id
                self._window._on_high_precision_formula_scan()
                self._emit(
                    "formula_scan_requested",
                    {
                        "mode": "high_precision",
                        "before": before,
                        "after": self._formula_state(),
                    },
                )
                return
            if cmd == "snapshot_state":
                self._emit("state", self._state())
                return
            raise ValueError(f"unknown test command: {cmd}")
        finally:
            self._active_command_id = ""

    def _on_kb_finished(self, doc_hash: str) -> None:
        if not self._kb_rebuild_pending:
            return
        self._kb_rebuild_pending = False
        payload = {
            "doc_hash": doc_hash,
            "blocks": len(getattr(self._window, "_current_blocks", [])),
        }
        if self._kb_rebuild_command_id:
            payload["command_id"] = self._kb_rebuild_command_id
            self._kb_rebuild_command_id = ""
        self._emit("kb_rebuilt", payload)

    def _on_kb_error(self, message: str) -> None:
        if not self._kb_rebuild_pending:
            return
        self._kb_rebuild_pending = False
        payload = {"message": message}
        if self._kb_rebuild_command_id:
            payload["command_id"] = self._kb_rebuild_command_id
            self._kb_rebuild_command_id = ""
        self._emit("kb_error", payload)

    def _on_formula_scan_finished(self, recognized: int, pending: int) -> None:
        payload = {
            "recognized": int(recognized),
            "pending": int(pending),
            **self._formula_state(),
        }
        if self._formula_scan_command_id:
            payload["command_id"] = self._formula_scan_command_id
            self._formula_scan_command_id = ""
        self._emit("formula_scan_finished", payload)

    def _pick_block(self, command: dict[str, Any]) -> str:
        block_id = command.get("block_id")
        if block_id:
            return str(block_id)
        page = command.get("page")
        blocks = list(getattr(self._window, "_current_blocks", []))
        if page is not None:
            page = int(page)
            blocks = [b for b in blocks if b.page_num == page]
        for block in blocks:
            if getattr(block, "block_type", "").value in ("paragraph", "heading"):
                content = getattr(block, "content", "") or ""
                if len(content.strip()) > 40:
                    return block.id
        return blocks[0].id if blocks else ""

    def _block_geometry(self, block_id: str) -> dict[str, Any]:
        viewer = self._window._pdf_viewer
        overlay = getattr(viewer, "_overlays", {}).get(block_id)
        if overlay is None:
            return {"visible": False, "rect": None, "center": None}
        try:
            top_left = overlay.mapToGlobal(overlay.rect().topLeft())
            rect = overlay.geometry()
            center = overlay.mapToGlobal(overlay.rect().center())
            return {
                "visible": bool(overlay.isVisible()),
                "rect": {
                    "x": int(top_left.x()),
                    "y": int(top_left.y()),
                    "width": int(rect.width()),
                    "height": int(rect.height()),
                },
                "center": [int(center.x()), int(center.y())],
                "screen": self._screen_geometry(),
            }
        except RuntimeError:
            return {"visible": False, "rect": None, "center": None}

    def _screen_geometry(self) -> dict[str, Any]:
        try:
            screen = self._window.screen()
            if screen is None:
                return {}
            geo = screen.geometry()
            return {
                "x": int(geo.x()),
                "y": int(geo.y()),
                "width": int(geo.width()),
                "height": int(geo.height()),
                "device_pixel_ratio": float(screen.devicePixelRatio()),
            }
        except RuntimeError:
            return {}

    def _state(self) -> dict[str, Any]:
        viewer = self._window._pdf_viewer
        splits = getattr(viewer, "_splits", {})
        return {
            "doc_hash": getattr(self._window, "_current_doc_hash", ""),
            "blocks": len(getattr(self._window, "_current_blocks", [])),
            "splits": list(splits.keys()),
            "split_count": len(splits),
            "split_collapsed": {
                split_id: bool(getattr(split, "collapsed", False))
                for split_id, split in splits.items()
            },
            "pages": self._window._doc_engine.page_count,
            "dock": self._dock_state(),
            "split_followups": self._split_followup_state(splits),
            "formula": self._formula_state(),
        }

    def _formula_state(self) -> dict[str, Any]:
        doc_hash = str(getattr(self._window, "_current_doc_hash", "") or "")
        flow = getattr(self._window, "_formula_index_flow", None)
        if not doc_hash or flow is None:
            return {
                "formula_running": bool(getattr(flow, "is_running", False)) if flow else False,
                "formula_work_pending": 0,
                "formula_block_pending": 0,
                "formula_index_pending": 0,
                "formula_page_pending": 0,
                "formula_round_pending": 0,
            }
        return {
            "formula_running": bool(getattr(flow, "is_running", False)),
            "formula_work_pending": int(self._window._pending_formula_work_count()),
            "formula_block_pending": int(self._window._pending_formula_block_count()),
            "formula_index_pending": int(flow.pending_count(doc_hash)),
            "formula_page_pending": int(flow.page_pending_count(doc_hash)),
            "formula_round_pending": int(flow.round_pending_count(doc_hash)),
        }

    def _dock_state(self) -> dict[str, Any]:
        evidence = getattr(self._window, "_ai_evidence_tree", None)
        answer = getattr(self._window, "_ai_answer_view", None)
        status = getattr(self._window, "_ai_doc_status", None)
        answer_text = str(getattr(self._window, "_dock_answer_text", "") or "")
        if not answer_text and answer is not None and hasattr(answer, "toPlainText"):
            answer_text = str(answer.toPlainText() or "")
        return {
            "dock_evidence_count": evidence.topLevelItemCount() if evidence else 0,
            "dock_answer_chars": len(answer_text),
            "dock_status": status.text() if status else "",
            "dock_followup_count": len(getattr(self._window, "_dock_followup_questions", [])),
        }

    def _split_followup_state(self, splits: dict[str, Any]) -> dict[str, int]:
        state: dict[str, int] = {}
        for split_id, split in splits.items():
            widget = getattr(split, "_followup_widget", None)
            layout = getattr(split, "_followup_layout", None)
            if widget is None or layout is None:
                state[split_id] = 0
                continue
            state[split_id] = layout.count() if widget.isVisible() else 0
        return state

    def _read_events(self) -> list[dict[str, Any]]:
        if not self._event_file.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self._event_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._active_command_id and "command_id" not in payload:
            payload = {**payload, "command_id": self._active_command_id}
        record = {"ts": time.time(), "event": event, **payload}
        try:
            self._event_file.parent.mkdir(parents=True, exist_ok=True)
            with self._event_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            _logger.warning("写入测试事件失败", exc_info=True)

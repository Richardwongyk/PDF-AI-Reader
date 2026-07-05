import os
import logging
import subprocess
import sys
import textwrap
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
SAMPLE_PDF = next(
    (
        path
        for path in (
            ROOT / "Attention is all you need.pdf",
            ROOT / "测试资料" / "Attention is all you need.pdf",
        )
        if path.exists()
    ),
    ROOT / "Attention is all you need.pdf",
)


def _run_python(source: str, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu-sandbox")
    return subprocess.run(
        [PYTHON, "-c", textwrap.dedent(source)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=True,
    )


def test_build_services_smoke() -> None:
    result = _run_python(
        """
        from src.main import setup_logging, build_services

        setup_logging()
        services = build_services()
        names = set(services.registered_services)
        required = {
            "config_manager", "glossary_manager", "navigator", "page_cache",
            "ai_cache", "chroma_repo", "embed_client", "embedding_service",
            "knowledge_engine", "ai_engine", "graph_index_flow",
            "formula_semantic_review", "document_engine",
        }
        missing = required - names
        assert not missing, missing
        services.shutdown()
        print("service smoke ok")
        """
    )
    assert "service smoke ok" in result.stdout


def test_setup_logging_prunes_old_app_logs(tmp_path, monkeypatch) -> None:
    from src import main

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    stale = log_dir / "old-app.log"
    stale.write_text("old", encoding="utf-8")
    fresh = log_dir / "fresh-app.log"
    fresh.write_text("fresh", encoding="utf-8")
    keep_awake = log_dir / "keep_awake_watchdog.log"
    keep_awake.write_text("keep", encoding="utf-8")
    old_time = time.time() - main._LOG_RETENTION_SEC - 60
    os.utime(stale, (old_time, old_time))
    os.utime(keep_awake, (old_time, old_time))

    main._prune_old_logs(log_dir)

    assert not stale.exists()
    assert fresh.exists()
    assert keep_awake.exists()


def test_setup_logging_keeps_console_quiet_by_default(tmp_path, monkeypatch) -> None:
    from src import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(main._CONSOLE_LOG_ENV, raising=False)

    main.setup_logging()

    handlers = logging.getLogger().handlers
    assert any(isinstance(handler, RotatingFileHandler) for handler in handlers)
    assert not any(getattr(handler, "stream", None) is sys.stdout for handler in handlers)


def test_setup_logging_allows_explicit_console_log(tmp_path, monkeypatch) -> None:
    from src import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(main._CONSOLE_LOG_ENV, "1")

    main.setup_logging()

    assert any(getattr(handler, "stream", None) is sys.stdout for handler in logging.getLogger().handlers)


def test_runtime_dir_helpers_prune_only_stale_process_dirs(tmp_path) -> None:
    from src import main

    runtime_root = tmp_path / "runtime"
    stale = runtime_root / "process-100"
    fresh = runtime_root / "process-200"
    unrelated = runtime_root / "cache"
    for path in (stale, fresh, unrelated):
        path.mkdir(parents=True)
        (path / "marker.txt").write_text(path.name, encoding="utf-8")
    old_time = time.time() - main._RUNTIME_RETENTION_SEC - 60
    os.utime(stale, (old_time, old_time))
    os.utime(unrelated, (old_time, old_time))

    main._prune_old_runtime_dirs(runtime_root)

    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()
    assert main._process_runtime_dir(runtime_root).name == f"process-{os.getpid()}"


def test_primary_instance_lock_marks_second_instance_secondary(tmp_path) -> None:
    from src import main

    runtime_root = tmp_path / "runtime"
    first_lock, first_secondary = main._acquire_primary_instance_lock(runtime_root)
    assert first_lock is not None
    assert first_secondary is False
    try:
        second_lock, second_secondary = main._acquire_primary_instance_lock(runtime_root)
        assert second_lock is None
        assert second_secondary is True
    finally:
        first_lock.unlock()


def test_secondary_instance_uses_fts_without_chroma_repo() -> None:
    result = _run_python(
        """
        from src.main import setup_logging, build_services

        setup_logging()
        services = build_services(test_mode=True, secondary_instance=True)
        engine = services.get("knowledge_engine")
        assert engine.backend_name == "sqlite_fts"
        assert "chroma_repo" not in services._singletons
        services.shutdown()
        print("secondary fts fallback smoke ok")
        """
    )
    assert "secondary fts fallback smoke ok" in result.stdout


def test_ollama_reachability_negative_result_is_fast_and_cached() -> None:
    result = _run_python(
        """
        import time
        import src.main as main

        main._OLLAMA_REACHABILITY_CACHE.clear()
        host = "http://127.0.0.1:9"

        start = time.perf_counter()
        first = main._ollama_host_reachable(host)
        first_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        second = main._ollama_host_reachable(host)
        second_elapsed = time.perf_counter() - start

        assert first is False
        assert second is False
        assert first_elapsed < 1.0
        assert second_elapsed < 0.05
        print("ollama reachability cache ok")
        """
    )
    assert "ollama reachability cache ok" in result.stdout


def test_build_services_uses_fts_for_hash_embedding() -> None:
    result = _run_python(
        """
        from src.main import setup_logging, build_services

        setup_logging()
        services = build_services(test_mode=True)
        engine = services.get("knowledge_engine")
        assert engine.backend_name == "sqlite_fts"
        services.shutdown()
        print("fts fallback smoke ok")
        """
    )
    assert "fts fallback smoke ok" in result.stdout


def test_fts_fallback_does_not_initialize_chroma_repo() -> None:
    result = _run_python(
        """
        from src.main import setup_logging, build_services

        setup_logging()
        services = build_services(test_mode=True)
        engine = services.get("knowledge_engine")
        assert engine.backend_name == "sqlite_fts"
        assert "chroma_repo" not in services._singletons
        services.shutdown()
        print("fts avoids chroma smoke ok")
        """
    )
    assert "fts avoids chroma smoke ok" in result.stdout


def test_formula_semantic_review_service_is_lazy_registered() -> None:
    result = _run_python(
        """
        from src.main import setup_logging, build_services

        setup_logging()
        services = build_services(test_mode=True)
        assert "formula_semantic_review" in services.registered_services
        assert "formula_semantic_review" not in services._singletons
        assert "ai_engine" not in services._singletons
        services.shutdown()
        print("formula semantic review lazy registration ok")
        """
    )
    assert "formula semantic review lazy registration ok" in result.stdout


def test_sample_pdf_parse_smoke() -> None:
    if not SAMPLE_PDF.exists():
        pytest.skip("sample PDF is not available in this checkout")
    result = _run_python(
        f"""
        import sys
        from pathlib import Path
        from PySide6.QtCore import QCoreApplication, QTimer
        from src.core.models import AppConfig
        from src.core.pdf_engine import DocumentEngine

        app = QCoreApplication(sys.argv)
        engine = DocumentEngine(AppConfig())
        state = {{"result": None, "error": None}}

        def finish():
            engine.shutdown()
            app.quit()

        def on_finished(result):
            state["result"] = result
            QTimer.singleShot(0, finish)

        def on_error(message):
            state["error"] = message
            QTimer.singleShot(0, finish)

        engine.parse_finished.connect(on_finished)
        engine.parse_error.connect(on_error)
        QTimer.singleShot(30000, lambda: (state.__setitem__("error", "timeout"), finish()))
        engine.open_document(str(Path({str(SAMPLE_PDF)!r})))
        app.exec()

        if state["error"]:
            raise RuntimeError(state["error"])
        result = state["result"]
        assert result is not None
        assert result.page_count == 15
        assert len(result.blocks) > 0
        assert result.parsed_pages
        print("pdf parse smoke ok")
        """
    )
    assert "pdf parse smoke ok" in result.stdout


def test_long_pdf_parse_finished_before_full_background_parse() -> None:
    napkin_candidates = list(ROOT.rglob("Napkin.pdf"))
    if not napkin_candidates:
        pytest.skip("Napkin PDF is not available in this checkout")
    result = _run_python(
        f"""
        import sys, time
        from pathlib import Path
        from PySide6.QtCore import QCoreApplication, QTimer
        from src.core.models import AppConfig
        from src.core.pdf_engine import DocumentEngine

        app = QCoreApplication(sys.argv)
        engine = DocumentEngine(AppConfig())
        state = {{"first": None, "completed": None, "error": None, "start": 0.0}}

        def finish():
            engine.shutdown()
            app.quit()

        def on_finished(result):
            state["first"] = time.perf_counter() - state["start"]
            print(f"first={{state['first']:.3f}} blocks={{len(result.blocks)}} pages={{len(result.parsed_pages)}}")
            QTimer.singleShot(1200, finish)

        def on_completed(result):
            state["completed"] = time.perf_counter() - state["start"]
            print(f"completed={{state['completed']:.3f}} blocks={{len(result.blocks)}}")

        def on_error(message):
            state["error"] = message
            QTimer.singleShot(0, finish)

        engine.parse_finished.connect(on_finished)
        engine.parse_completed.connect(on_completed)
        engine.parse_error.connect(on_error)
        state["start"] = time.perf_counter()
        engine.open_document(str(Path({str(napkin_candidates[0])!r})))
        QTimer.singleShot(10000, lambda: (state.__setitem__("error", "timeout"), finish()))
        app.exec()

        if state["error"]:
            raise RuntimeError(state["error"])
        assert state["first"] is not None
        assert state["first"] < 5.0
        print("long pdf first parse smoke ok")
        """
    )
    assert "long pdf first parse smoke ok" in result.stdout


def test_main_window_smoke() -> None:
    result = _run_python(
        """
        import sys
        from PySide6.QtCore import Qt, QTimer, QPoint
        from PySide6.QtGui import QAction
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QDockWidget, QPushButton, QScrollArea, QToolBar, QToolButton, QWidget
        from PySide6.QtWebEngineCore import QWebEngineProfile

        from src.main import setup_logging, build_services
        from src.ui import main_window as main_window_module

        main_window_module.MainWindow._check_first_launch = lambda self: None
        main_window_module.MainWindow._prewarm_webview_pool = lambda self: None

        setup_logging()
        app = QApplication(sys.argv)
        profile = QWebEngineProfile.defaultProfile()
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        services = build_services()
        window = main_window_module.MainWindow(services)
        window.show()
        app.processEvents()
        app_style = app.styleSheet()
        assert "PDF_AI_READER_TOOLTIP_STYLE" in app_style
        assert "color: #ffffff" in app_style
        assert "#4a3f6b" not in app_style
        assert window.findChild(QWidget, "ai_question_input") is not None
        assert window.findChild(QWidget, "ai_evidence_tree") is not None
        assert window.findChild(QWidget, "ai_answer_view") is not None
        left_toggle = window.findChild(QToolButton, "left_panel_toggle_button")
        right_toggle = window.findChild(QToolButton, "right_panel_toggle_button")
        assert left_toggle is not None
        assert right_toggle is not None
        toolbar = window.findChild(QToolBar, "main_toolbar")
        assert toolbar is not None
        toolbar_spacer = window.findChild(QWidget, "toolbar_spacer")
        restore_handle = window.findChild(QWidget, "toolbar_restore_handle")
        assert toolbar_spacer is not None
        assert restore_handle is not None
        left_corner = window.menuBar().cornerWidget(Qt.Corner.TopLeftCorner)
        right_corner = window.menuBar().cornerWidget(Qt.Corner.TopRightCorner)
        assert left_corner is not None
        assert right_corner is not None
        assert left_corner.findChild(QToolButton, "left_panel_toggle_button") is left_toggle
        assert right_corner.findChild(QToolButton, "right_panel_toggle_button") is right_toggle
        assert right_corner.width() >= right_toggle.width() + 8
        assert all(toolbar.widgetForAction(action) is not left_toggle for action in toolbar.actions())
        assert all(toolbar.widgetForAction(action) is not right_toggle for action in toolbar.actions())
        assert toolbar_spacer.width() >= 80
        assert not window._reader_chrome_collapsed
        assert not restore_handle.isVisible()
        def close_active_popup():
            popup = QApplication.activePopupWidget()
            if popup is not None:
                popup.close()
                app.processEvents()
            return popup
        menu_pos = QPoint(64, max(1, window.menuBar().height() // 2))
        QTest.mouseClick(window.menuBar(), Qt.MouseButton.LeftButton, pos=menu_pos)
        app.processEvents()
        assert not window._reader_chrome_collapsed
        assert toolbar.isVisible()
        assert window.statusBar().isVisible()
        assert close_active_popup() is not None
        QTest.mouseDClick(window.menuBar(), Qt.MouseButton.LeftButton, pos=menu_pos)
        app.processEvents()
        assert window._reader_chrome_collapsed
        assert not toolbar.isVisible()
        assert not window.statusBar().isVisible()
        assert left_toggle.isVisible()
        assert right_toggle.isVisible()
        QTest.mouseClick(window.menuBar(), Qt.MouseButton.LeftButton, pos=menu_pos)
        app.processEvents()
        assert window._reader_chrome_collapsed
        assert not toolbar.isVisible()
        assert not window.statusBar().isVisible()
        assert close_active_popup() is not None
        QTest.mouseDClick(window.menuBar(), Qt.MouseButton.LeftButton, pos=menu_pos)
        app.processEvents()
        assert not window._reader_chrome_collapsed
        assert toolbar.isVisible()
        assert window.statusBar().isVisible()
        QTest.mouseDClick(toolbar_spacer, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert window._reader_chrome_collapsed
        assert not toolbar.isVisible()
        assert not window.statusBar().isVisible()
        assert restore_handle.isVisible()
        QTest.mouseDClick(restore_handle, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert not window._reader_chrome_collapsed
        assert toolbar.isVisible()
        assert window.statusBar().isVisible()
        assert not restore_handle.isVisible()
        assert left_toggle.width() <= 34
        assert right_toggle.width() <= 34
        assert "#111827" in left_toggle.styleSheet()
        assert "#3b82f6" in left_toggle.styleSheet()
        assert "#f8fafc" in left_toggle.styleSheet()
        assert "QToolTip" in left_toggle.styleSheet()
        assert "color: #ffffff" in left_toggle.styleSheet()
        assert left_toggle.styleSheet() == right_toggle.styleSheet()
        assert left_toggle.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert right_toggle.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert not left_toggle.icon().isNull()
        assert not right_toggle.icon().isNull()
        assert not window._left_panel_collapsed
        assert left_toggle.text() == ""
        assert left_toggle.toolTip() == "隐藏左侧导航栏"
        center_calls = []
        window._pdf_viewer.center_horizontally = lambda: center_calls.append("center")
        center_count = len(center_calls)
        left_toggle.click()
        app.processEvents()
        QTest.qWait(120)
        app.processEvents()
        assert len(center_calls) > center_count
        assert window._left_panel_collapsed
        assert left_toggle.text() == ""
        assert not left_toggle.icon().isNull()
        assert left_toggle.toolTip() == "显示左侧导航栏"
        assert not window._left_dock.isVisible()
        left_toggle.click()
        assert not window._left_panel_collapsed
        assert left_toggle.text() == ""
        assert left_toggle.toolTip() == "隐藏左侧导航栏"
        assert window._left_dock.isVisible()
        assert not (window._left_dock.features() & QDockWidget.DockWidgetFeature.DockWidgetClosable)
        left_float_button = window.findChild(QToolButton, "left_dock_float_button")
        assert left_float_button is not None
        assert left_float_button.isVisible()
        assert left_float_button.toolButtonStyle() == Qt.ToolButtonStyle.ToolButtonIconOnly
        assert left_float_button.text() == ""
        assert not left_float_button.icon().isNull()
        assert not window._left_dock.isFloating()
        left_float_button.click()
        app.processEvents()
        assert window._left_dock.isFloating()
        assert left_float_button.isVisible()
        assert left_float_button.toolTip() == "归位到左侧导航栏"
        left_float_button.click()
        app.processEvents()
        assert not window._left_dock.isFloating()
        assert window.dockWidgetArea(window._left_dock) == Qt.DockWidgetArea.LeftDockWidgetArea
        assert right_toggle.text() == ""
        assert right_toggle.toolTip() == "隐藏右侧 AI 工具集"
        assert window._right_panel_body is not None
        assert not window._right_panel_collapsed
        center_count = len(center_calls)
        right_toggle.click()
        app.processEvents()
        QTest.qWait(120)
        app.processEvents()
        assert len(center_calls) > center_count
        assert window._right_panel_collapsed
        assert right_toggle.text() == ""
        assert not right_toggle.icon().isNull()
        assert right_toggle.toolTip() == "显示右侧 AI 工具集"
        assert not window._right_dock.isVisible()
        right_toggle.click()
        assert not window._right_panel_collapsed
        assert right_toggle.text() == ""
        assert right_toggle.toolTip() == "隐藏右侧 AI 工具集"
        assert window._right_dock.isVisible()
        assert not (window._right_dock.features() & QDockWidget.DockWidgetFeature.DockWidgetClosable)
        float_button = window.findChild(QToolButton, "right_dock_float_button")
        assert float_button is not None
        assert float_button.isVisible()
        assert not window._right_dock.isFloating()
        long_followup = "这是一个很长的追问问题，用来验证右侧面板默认显示最左侧开头文字，并且可以横向滚动查看后半段内容？"
        window._on_followup_ready([long_followup, "第二个补充问题？", "第三个补充问题？"], window._dock_answer_split_id)
        app.processEvents()
        QTest.qWait(50)
        app.processEvents()
        assert isinstance(window._ai_followup_widget, QScrollArea)
        assert window._ai_followup_widget.isVisible()
        assert window._ai_followup_widget.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert window._ai_followup_widget.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        followup_buttons = [
            button for button in window.findChildren(QPushButton)
            if button.property("followup_question")
        ]
        assert [button.property("followup_question") for button in followup_buttons] == [
            long_followup, "第二个补充问题？", "第三个补充问题？"
        ]
        assert followup_buttons[0].text() == long_followup
        assert "text-align: left" in followup_buttons[0].styleSheet()
        assert window._ai_followup_container.minimumWidth() >= followup_buttons[0].fontMetrics().horizontalAdvance(long_followup)
        assert window._ai_followup_widget.horizontalScrollBar().value() == 0
        assert window._ai_followup_widget.verticalScrollBar().value() == 0
        docked_right_width = window._right_dock.width()
        screen = window.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry()
        expected_float_width = min(
            max(760, int(window.width() * 0.55)),
            max(window._right_panel_min_width, available.width() - 64),
        )
        expected_float_height = min(
            max(680, int(window.height() * 0.78)),
            max(480, available.height() - 64),
        )
        window._dock_answer_render_text = "cached dock answer"
        window._dock_answer_render_finished = True
        refresh_calls = []
        window._refresh_dock_answer_view = lambda: refresh_calls.append(window._dock_answer_render_text)
        float_button.click()
        app.processEvents()
        QTest.qWait(200)
        app.processEvents()
        assert window._right_dock.isFloating()
        assert float_button.isVisible()
        assert "cached dock answer" in refresh_calls
        float_geom = window._right_dock.geometry()
        assert float_geom.width() >= min(720, expected_float_width)
        assert float_geom.height() >= min(640, expected_float_height)
        assert float_geom.width() > docked_right_width
        float_button.click()
        app.processEvents()
        assert not window._right_dock.isFloating()
        assert window.dockWidgetArea(window._right_dock) == Qt.DockWidgetArea.RightDockWidgetArea
        assert abs(window._right_dock.width() - docked_right_width) <= 24
        assert window.findChild(QAction, "high_precision_formula_action") is not None
        assert window.findChild(QAction, "high_precision_formula_toolbar_action") is not None
        assert window._formula_idle_timer.interval() == 5000
        QTimer.singleShot(500, window.close)
        QTimer.singleShot(1500, app.quit)
        code = app.exec()
        services.shutdown()
        assert code == 0
        print("main window smoke ok")
        """,
        timeout=60,
    )
    assert "main window smoke ok" in result.stdout


def test_split_widget_followup_buttons() -> None:
    result = _run_python(
        """
        import sys
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea, QVBoxLayout

        from src.core.models import BlockType, DocumentBlock, SplitMode
        from src.ui.split_widget import SplitWidget, WebViewPool

        app = QApplication(sys.argv)
        block = DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.PARAGRAPH,
            content="The attention mechanism maps queries and keys.",
            bbox=(0, 0, 100, 20),
        )
        widget = SplitWidget(block, mode=SplitMode.QUESTION)
        idx = widget._body_layout.indexOf(widget._result_view)
        assert idx >= 0
        assert widget._body_layout.stretch(idx) == 1
        view = widget._result_view
        try:
            view.loadFinished.disconnect(widget._on_page_loaded)
        except Exception:
            pass
        widget._body_layout.removeWidget(view)
        view.setParent(None)
        WebViewPool.release(view)
        widget._result_view = None
        widget._page_ready = False
        widget._thaw_webview()
        idx = widget._body_layout.indexOf(widget._result_view)
        assert idx >= 0
        assert widget._body_layout.stretch(idx) == 1
        long_question = "这是一个很长的追问问题，用来验证左侧文字优先可见，并且需要横向滚动条查看完整内容？"
        widget.show_followup_questions([long_question, "问题二？", "问题三？"])
        assert isinstance(widget._followup_widget, QScrollArea)
        assert isinstance(widget._followup_layout, QVBoxLayout)
        assert widget._followup_widget.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        assert widget._followup_widget.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
        buttons = widget.findChildren(QPushButton)
        followups = [button.property("followup_question") for button in buttons if button.property("followup_question")]
        assert followups == [long_question, "问题二？", "问题三？"]
        followup_buttons = [button for button in buttons if button.property("followup_question")]
        assert followup_buttons[0].text() == long_question
        assert "text-align: left" in followup_buttons[0].styleSheet()
        assert widget._followup_container.minimumWidth() >= followup_buttons[0].fontMetrics().horizontalAdvance(long_question)
        assert all(button.minimumHeight() >= 32 for button in followup_buttons)
        widget.set_content_padding(12, -4)
        assert widget._content_padding == (12, 0)
        widget._pending_padding_js = None
        widget._queue_content_padding()
        assert "paddingLeft='12px'" in widget._pending_padding_js
        assert "paddingRight='0px'" in widget._pending_padding_js
        emitted_heights = []
        widget.height_changed.connect(emitted_heights.append)
        widget._compute_chrome_height = lambda: 40
        widget._saved_height = 220
        widget.setFixedHeight(220)
        widget.setMaximumHeight(16777215)
        widget._adjust_height(180)
        assert widget._saved_height == 224
        assert widget.minimumHeight() == 224
        assert widget.maximumHeight() == 224
        assert emitted_heights[-1] == 224
        widget.setMaximumHeight(16777215)
        widget._adjust_height(180)
        assert widget.minimumHeight() == 224
        assert widget.maximumHeight() == 224
        widget._mode = SplitMode.TRANSLATION
        widget._update_mode_ui()
        widget.apply_theme("light")
        assert "#f0f5ff" in widget.styleSheet()
        assert "qlineargradient" not in widget.styleSheet()
        widget.close()
        app.quit()
        print("split followup smoke ok")
        """,
        timeout=60,
    )
    assert "split followup smoke ok" in result.stdout


def test_webview_pool_prewarm_starts_engine() -> None:
    result = _run_python(
        """
        import sys
        import time
        from PySide6.QtWidgets import QApplication

        from src.ui.split_widget import WebViewPool

        app = QApplication(sys.argv)
        WebViewPool.prewarm()
        deadline = time.time() + 8
        while time.time() < deadline and not getattr(WebViewPool._standby, "_engine_ready", False):
            app.processEvents()
            time.sleep(0.02)
        assert WebViewPool._standby is not None
        assert getattr(WebViewPool._standby, "_engine_ready", False) is True
        view = WebViewPool.acquire()
        assert getattr(view, "_engine_ready", False) is True
        WebViewPool.clear()
        view.close()
        view.deleteLater()
        for _ in range(20):
            app.processEvents()
            time.sleep(0.01)
        app.quit()
        print("webview prewarm ok")
        """,
        timeout=30,
    )
    assert "webview prewarm ok" in result.stdout

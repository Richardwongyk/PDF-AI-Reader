import os
import subprocess
import sys
import textwrap
import time
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
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QApplication, QDockWidget, QToolBar, QToolButton, QWidget
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
        assert window.findChild(QWidget, "ai_question_input") is not None
        assert window.findChild(QWidget, "ai_evidence_tree") is not None
        assert window.findChild(QWidget, "ai_answer_view") is not None
        toggle = window.findChild(QToolButton, "right_panel_toggle_button")
        assert toggle is not None
        toolbar = window.findChild(QToolBar, "main_toolbar")
        assert toolbar is not None
        assert any(toolbar.widgetForAction(action) is toggle for action in toolbar.actions())
        assert toggle.text() == "隐藏 AI"
        assert window._right_panel_body is not None
        assert not window._right_panel_collapsed
        toggle.click()
        assert window._right_panel_collapsed
        assert toggle.text() == "显示 AI"
        assert not window._right_dock.isVisible()
        toggle.click()
        assert not window._right_panel_collapsed
        assert toggle.text() == "隐藏 AI"
        assert window._right_dock.isVisible()
        assert not (window._right_dock.features() & QDockWidget.DockWidgetFeature.DockWidgetClosable)
        float_button = window.findChild(QToolButton, "right_dock_float_button")
        assert float_button is not None
        assert float_button.isVisible()
        assert not window._right_dock.isFloating()
        float_button.click()
        app.processEvents()
        assert window._right_dock.isFloating()
        assert float_button.isVisible()
        float_button.click()
        app.processEvents()
        assert not window._right_dock.isFloating()
        assert window.dockWidgetArea(window._right_dock) == Qt.DockWidgetArea.RightDockWidgetArea
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
        from PySide6.QtWidgets import QApplication, QPushButton

        from src.core.models import BlockType, DocumentBlock, SplitMode
        from src.ui.split_widget import SplitWidget

        app = QApplication(sys.argv)
        block = DocumentBlock(
            id="p0_b0",
            page_num=0,
            block_type=BlockType.PARAGRAPH,
            content="The attention mechanism maps queries and keys.",
            bbox=(0, 0, 100, 20),
        )
        widget = SplitWidget(block, mode=SplitMode.QUESTION)
        widget.show_followup_questions(["问题一？", "问题二？", "问题三？"])
        buttons = widget.findChildren(QPushButton)
        followups = [button.property("followup_question") for button in buttons if button.property("followup_question")]
        assert followups == ["问题一？", "问题二？", "问题三？"]
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

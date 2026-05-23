import os
import subprocess
import sys
import textwrap
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
            "knowledge_engine", "ai_engine", "document_engine",
        }
        missing = required - names
        assert not missing, missing
        services.shutdown()
        print("service smoke ok")
        """
    )
    assert "service smoke ok" in result.stdout


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


def test_main_window_smoke() -> None:
    result = _run_python(
        """
        import sys
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QApplication, QWidget
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
        assert window.findChild(QAction, "high_precision_formula_action") is not None
        assert window.findChild(QAction, "high_precision_formula_toolbar_action") is not None
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

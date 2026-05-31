"""
主窗口 —— PDF AI Reader 应用的主界面。

MainWindow: 管理全局布局（菜单栏/工具栏/状态栏/中央阅读区/侧边栏），
负责连接 UI 信号到 Core 服务，协调所有子组件。
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QStatusBar,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.models import (
    AppConfig,
    BlockType,
    DocumentBlock,
    SplitMode,
    TaskType,
)
from src.core.pdf_engine import DocumentEngine
from src.core.ai_engine import AIEngine
from src.core.knowledge_engine import KnowledgeEngine
from src.core.glossary_manager import GlossaryManager
from src.core.navigator import Navigator
from src.core.service_container import ServiceContainer
from src.app.formula_acceptance_review import FormulaAcceptanceReviewService
from src.ui.pdf_viewer import PdfViewer
from src.ui.split_widget import SplitWidget, WebViewPool
from src.ui.theme import apply_theme
from src.app.formula_index_scheduler import FormulaScanTrigger


class _DockAnswerBridge(QObject):
    """QWebChannel bridge for the dock answer WebView."""

    @Slot(int)
    def onHeightChanged(self, h: int) -> None:
        pass


class MainWindow(QMainWindow):
    """PDF AI Reader 主窗口。

    布局:
    ┌─────────────────────────────────────────────┐
    │  MenuBar (文件/编辑/视图/工具/帮助)          │
    ├─────────────────────────────────────────────┤
    │  ToolBar (打开/缩放/搜索/设置)               │
    ├────┬──────────────────────────┬─────────────┤
    │ 左 │     中央 PDF 阅读区      │  右         │
    │ 侧 │     (PdfViewer)          │  侧         │
    │ 边 │                          │  边         │
    │ 栏 │                          │  栏         │
    ├────┴──────────────────────────┴─────────────┤
    │  StatusBar (页码/模型状态/任务进度)          │
    └─────────────────────────────────────────────┘
    """

    def __init__(self, services: ServiceContainer) -> None:
        """初始化主窗口。

        Args:
            services: 核心服务定位器。
        """
        super().__init__()
        self._services = services
        self._config: AppConfig = services.get("config_manager").get()
        self._doc_engine: DocumentEngine = services.get("document_engine")
        self._ai_engine: AIEngine = services.get("ai_engine")
        self._knowledge_engine: KnowledgeEngine = services.get("knowledge_engine")
        self._glossary_manager: GlossaryManager = services.get("glossary_manager")
        self._navigator: Navigator = services.get("navigator")
        self._ai_cache = services.get("ai_cache")  # SQLite AI 结果缓存

        # 翻译流程协调器（借鉴 Mad Professor AIManager 模式）
        from src.app.translate_flow import TranslationFlow
        self._translate_flow = TranslationFlow(self._ai_engine, self._ai_cache)

        # 文档生命周期协调器（借鉴 Mad Professor DataManager 模式）
        from src.app.document_flow import DocumentFlow
        self._document_flow = DocumentFlow(
            self._doc_engine, self._knowledge_engine,
            self._ai_engine, self._glossary_manager,
            services.get("graph_index_flow"),
        )

        # 概念解释流程协调器（借鉴 Mad Professor AIManager 模式）
        from src.app.explain_flow import ExplainFlow
        self._explain_flow = ExplainFlow(self._ai_engine, self._doc_engine, self._ai_cache)

        # 问答流程协调器（借鉴 Mad Professor AIManager 模式）
        from src.app.ask_flow import AskQuestionFlow
        self._ask_flow = AskQuestionFlow(self._ai_engine, self._knowledge_engine)

        # 公式索引流程：异步补扫图片/扫描公式，并增量写回知识库
        from src.app.formula_index_flow import FormulaIndexFlow
        from src.app.formula_index_scheduler import FormulaIndexScheduler
        from src.app.formula_knowledge_graph import FormulaKnowledgeGraphService
        from src.app.formula_knowledge_update import FormulaKnowledgeUpdateService
        from src.app.formula_semantic_review import FormulaSemanticReviewFlow
        self._formula_index_flow = FormulaIndexFlow(self)
        self._formula_index_scheduler = FormulaIndexScheduler()
        self._formula_semantic_review_flow = FormulaSemanticReviewFlow(
            lambda: services.get("formula_semantic_review"),
            self,
        )
        graph_flow = services.get("graph_index_flow")
        graph_store = getattr(graph_flow, "_store", None)
        self._formula_knowledge_update_service = FormulaKnowledgeUpdateService(
            self._formula_index_flow.store,
            self._knowledge_engine,
            graph_store,
        )
        self._formula_knowledge_graph_service = FormulaKnowledgeGraphService(
            self._formula_index_flow.store,
            graph_store,
        )

        # 当前文档状态
        self._current_doc_hash: str = ""
        self._current_blocks: list[DocumentBlock] = []
        self._blocks_by_id: dict[str, DocumentBlock] = {}
        self._loaded_block_pages: set[int] = set()
        self._pending_page_blocks: dict[int, list[DocumentBlock]] = {}
        self._viewer_document_loaded: bool = False
        self._has_native_toc: bool = False
        self._active_translation_blocks: set[str] = set()
        self._last_user_translation_at: float = 0.0
        self._pymupdf4llm_pending_result: object | None = None
        self._dock_answer_text: str = ""
        self._dock_answer_split_id: str = "__dock_qa__"
        self._dock_last_question: str = ""
        self._dock_followup_questions: list[str] = []
        self._dock_answer_page_ready: bool = False
        self._dock_answer_pending_js: str | None = None
        self._dock_answer_pending_theme: str | None = None
        self._dock_answer_bridge: _DockAnswerBridge | None = None
        self._pending_kb_upserts: dict[str, DocumentBlock] = {}
        self._last_formula_viewport_pages: set[int] = set()
        self._formula_import_thread: _FormulaImportPlanThread | None = None
        self._formula_viewport_timer = QTimer(self)
        self._formula_viewport_timer.setSingleShot(True)
        self._formula_viewport_timer.setInterval(250)
        self._formula_viewport_timer.timeout.connect(self._schedule_viewport_formula_scan)
        self._formula_idle_timer = QTimer(self)
        self._formula_idle_timer.setSingleShot(True)
        self._formula_idle_timer.setInterval(5000)
        self._formula_idle_timer.timeout.connect(self._schedule_idle_formula_scan)

        self._init_ui()
        self._connect_signals()
        self._apply_theme()

        self.setWindowTitle("PDF AI Reader")
        self.resize(1400, 900)

    # =========================================================================
    # UI 初始化
    # =========================================================================

    def _init_ui(self) -> None:
        """构建所有 UI 组件。"""
        self._create_menu_bar()
        self._create_tool_bar()
        self._create_status_bar()
        self._create_central_widget()
        self._create_side_panels()

    def _create_menu_bar(self) -> None:
        """创建菜单栏。"""
        menubar: QMenuBar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        open_action = QAction("打开 PDF...(&O)", self)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(self._on_open_pdf)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        close_action = QAction("关闭文档(&C)", self)
        close_action.triggered.connect(lambda: self._document_flow.close_document())
        file_menu.addAction(close_action)
        file_menu.addSeparator()
        exit_action = QAction("退出(&X)", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 视图菜单
        view_menu = menubar.addMenu("视图(&V)")
        zoom_in_action = QAction("放大(&I)", self)
        zoom_in_action.triggered.connect(lambda: self._pdf_viewer.zoom_in())
        view_menu.addAction(zoom_in_action)
        zoom_out_action = QAction("缩小(&O)", self)
        zoom_out_action.triggered.connect(lambda: self._pdf_viewer.zoom_out())
        view_menu.addAction(zoom_out_action)
        # QShortcut 直接注册到窗口，绕过菜单系统的快捷键限制
        QShortcut(QKeySequence("Ctrl+="), self, lambda: self._pdf_viewer.zoom_in())
        QShortcut(QKeySequence("Ctrl+-"), self, lambda: self._pdf_viewer.zoom_out())

        # 工具菜单
        tools_menu = menubar.addMenu("工具(&T)")
        build_kb_action = QAction("构建/重建知识库(&B)", self)
        build_kb_action.triggered.connect(self._on_build_knowledge_base)
        tools_menu.addAction(build_kb_action)
        high_precision_formula_action = QAction("高精度扫描当前公式(&F)", self)
        high_precision_formula_action.setObjectName("high_precision_formula_action")
        high_precision_formula_action.triggered.connect(self._on_high_precision_formula_scan)
        tools_menu.addAction(high_precision_formula_action)
        formula_review_action = QAction("公式候选审核(&R)", self)
        formula_review_action.setObjectName("formula_acceptance_review_action")
        formula_review_action.triggered.connect(self._on_open_formula_acceptance_review)
        tools_menu.addAction(formula_review_action)
        tools_menu.addSeparator()
        glossary_action = QAction("术语表管理器(&G)", self)
        glossary_action.triggered.connect(self._on_open_glossary_editor)
        tools_menu.addAction(glossary_action)
        tools_menu.addSeparator()
        settings_action = QAction("设置(&S)...", self)
        settings_action.triggered.connect(self._on_open_settings)
        tools_menu.addAction(settings_action)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")
        about_action = QAction("关于(&A)", self)
        about_action.triggered.connect(self._on_about)
        help_menu.addAction(about_action)

    def _create_tool_bar(self) -> None:
        """创建工具栏。"""
        toolbar: QToolBar = self.addToolBar("主工具栏")
        toolbar.setMovable(False)

        open_action = QAction("📂 打开", self)
        open_action.triggered.connect(self._on_open_pdf)
        toolbar.addAction(open_action)

        toolbar.addSeparator()

        # 搜索框（简化版，仅占位）
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("搜索文档...")
        self._search_box.setMaximumWidth(200)
        toolbar.addWidget(self._search_box)

        toolbar.addSeparator()
        self._page_jump_box = QLineEdit()
        self._page_jump_box.setObjectName("page_jump_box")
        self._page_jump_box.setAccessibleName("page_jump_box")
        self._page_jump_box.setPlaceholderText("页码")
        self._page_jump_box.setMaximumWidth(80)
        self._page_jump_box.returnPressed.connect(self._on_page_jump_requested)
        toolbar.addWidget(self._page_jump_box)

        toolbar.addSeparator()
        formula_scan_action = QAction("公式精扫", self)
        formula_scan_action.setObjectName("high_precision_formula_toolbar_action")
        formula_scan_action.setToolTip("高精度扫描当前视口公式")
        formula_scan_action.triggered.connect(self._on_high_precision_formula_scan)
        toolbar.addAction(formula_scan_action)

    def _create_status_bar(self) -> None:
        """创建状态栏。"""
        status: QStatusBar = self.statusBar()

        self._status_page_label = QLabel("就绪")
        status.addWidget(self._status_page_label)

        self._status_model_label = QLabel("")
        status.addPermanentWidget(self._status_model_label)

        self._status_progress = QProgressBar()
        self._status_progress.setMaximumWidth(150)
        self._status_progress.setVisible(False)
        status.addPermanentWidget(self._status_progress)

    def _create_central_widget(self) -> None:
        """创建中央 PDF 阅读区。"""
        self._pdf_viewer = PdfViewer(self._doc_engine, self._config.ui)
        self.setCentralWidget(self._pdf_viewer)

    def _create_side_panels(self) -> None:
        """创建侧边栏面板。"""
        # 左侧：目录 + 书签
        self._left_dock = QDockWidget("导航", self)
        self._left_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )

        self._toc_tree = QTreeWidget()
        self._toc_tree.setHeaderLabel("目录")
        self._toc_tree.itemClicked.connect(self._on_toc_item_clicked)
        self._left_dock.setWidget(self._toc_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._left_dock)

        # 右侧：全文问答与证据面板
        self._right_dock = QDockWidget("AI 工具集", self)
        self._right_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 10, 10, 10)
        right_layout.setSpacing(8)

        self._ai_doc_status = QLabel("未打开文档")
        self._ai_doc_status.setObjectName("ai_doc_status")
        self._ai_doc_status.setWordWrap(True)
        self._ai_doc_status.setStyleSheet("color: #444; font-weight: 600;")
        right_layout.addWidget(self._ai_doc_status)

        action_row = QHBoxLayout()
        self._ai_question_input = QLineEdit()
        self._ai_question_input.setObjectName("ai_question_input")
        self._ai_question_input.setPlaceholderText("基于全文提问...")
        self._ai_question_input.returnPressed.connect(self._on_dock_question_submitted)
        action_row.addWidget(self._ai_question_input, 1)
        self._ai_ask_button = QPushButton("提问")
        self._ai_ask_button.setObjectName("ai_ask_button")
        self._ai_ask_button.clicked.connect(self._on_dock_question_submitted)
        action_row.addWidget(self._ai_ask_button)
        right_layout.addLayout(action_row)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        right_layout.addWidget(separator)

        evidence_label = QLabel("检索依据")
        evidence_label.setStyleSheet("font-weight: 600;")
        right_layout.addWidget(evidence_label)
        self._ai_evidence_tree = QTreeWidget()
        self._ai_evidence_tree.setObjectName("ai_evidence_tree")
        self._ai_evidence_tree.setHeaderLabels(["证据", "相关度", "片段"])
        self._ai_evidence_tree.setRootIsDecorated(False)
        self._ai_evidence_tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._ai_evidence_tree.itemDoubleClicked.connect(self._on_evidence_item_activated)
        self._ai_evidence_tree.setMinimumHeight(170)
        self._ai_evidence_tree.setColumnWidth(0, 112)
        self._ai_evidence_tree.setColumnWidth(1, 72)
        right_layout.addWidget(self._ai_evidence_tree, 1)

        answer_label = QLabel("回答")
        answer_label.setStyleSheet("font-weight: 600;")
        right_layout.addWidget(answer_label)
        self._ai_answer_view = WebViewPool.acquire()
        self._ai_answer_view.setObjectName("ai_answer_view")
        self._ai_answer_view.setMinimumHeight(220)
        self._dock_answer_page_ready = False
        self._dock_answer_pending_js = None
        self._dock_answer_pending_theme = None
        self._dock_answer_bridge = _DockAnswerBridge(self)
        WebViewPool.swap_bridge(self._ai_answer_view, self._dock_answer_bridge)
        self._ai_answer_view.loadFinished.connect(self._on_dock_answer_page_loaded)
        WebViewPool.load_template(self._ai_answer_view)
        right_layout.addWidget(self._ai_answer_view, 2)

        self._ai_followup_label = QLabel("追问")
        self._ai_followup_label.setStyleSheet("font-weight: 600;")
        self._ai_followup_label.setVisible(False)
        right_layout.addWidget(self._ai_followup_label)
        self._ai_followup_layout = QVBoxLayout()
        self._ai_followup_layout.setSpacing(4)
        right_layout.addLayout(self._ai_followup_layout)

        self._right_dock.setWidget(right_widget)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._right_dock)

    def _apply_theme(self) -> None:
        """应用主题。light=系统原生，dark/sepia=QPalette。广播到所有裂缝。"""
        theme = self._config.ui.theme
        if theme != "light":
            apply_theme(theme)
        self.setStyleSheet("")
        if self._pdf_viewer:
            self._pdf_viewer.apply_theme_to_splits(theme)
        self._set_dock_answer_theme(theme)

    def _set_dock_answer_theme(self, theme: str) -> None:
        """同步右侧问答 WebView 主题。"""
        safe_theme = json.dumps(theme)
        js_code = f"setTheme({safe_theme});"
        if self._dock_answer_page_ready:
            self._ai_answer_view.page().runJavaScript(js_code)
        else:
            self._dock_answer_pending_theme = js_code

    def _on_dock_answer_page_loaded(self, ok: bool) -> None:
        """右侧问答 WebView 模板加载完成。"""
        self._dock_answer_page_ready = bool(ok)
        if not ok:
            self.logger.warning("右侧问答 WebView 页面加载失败")
            return
        self._ai_answer_view.page().runJavaScript("window.pageReady = true;")
        if self._dock_answer_pending_theme:
            self._ai_answer_view.page().runJavaScript(self._dock_answer_pending_theme)
            self._dock_answer_pending_theme = None
        if self._dock_answer_pending_js:
            self._ai_answer_view.page().runJavaScript(self._dock_answer_pending_js)
            self._dock_answer_pending_js = None

    def _render_dock_answer(self, text: str, finished: bool = False) -> None:
        """把右侧全文问答内容交给现有 Markdown/KaTeX 模板渲染。"""
        safe_text = json.dumps(text)
        js_bool = "true" if finished else "false"
        js_code = f"updateContent({safe_text}, {js_bool});"
        if self._dock_answer_page_ready:
            self._ai_answer_view.page().runJavaScript(js_code)
        else:
            self._dock_answer_pending_js = js_code

    def _set_dock_answer_text(self, text: str, finished: bool = False) -> None:
        """更新右侧问答文本缓存并渲染。"""
        self._dock_answer_text = text
        self._render_dock_answer(text, finished=finished)

    # =========================================================================
    # 信号连接
    # =========================================================================

    def _connect_signals(self) -> None:
        """连接 UI 信号到 Core 层服务和内部处理。"""
        # DocumentFlow 协调器（借鉴 Mad Professor DataManager 模式）
        self._document_flow.document_opened.connect(self._on_document_opened)
        self._document_flow.document_closed.connect(self._on_document_closed)
        self._document_flow.parse_progress.connect(self._on_parse_progress)
        self._document_flow.parse_error.connect(self._on_parse_error)

        # DocumentEngine 信号（DocumentFlow 未覆盖的）
        self._doc_engine.formula_blocks_updated.connect(self._on_formula_blocks_updated)
        self._doc_engine.page_blocks_ready.connect(self._on_page_blocks_ready)

        # KnowledgeEngine 信号
        self._knowledge_engine.build_progress.connect(self._on_kb_progress)
        self._knowledge_engine.build_finished.connect(self._on_kb_finished)
        self._knowledge_engine.build_error.connect(self._on_kb_error)
        self._knowledge_engine.blocks_upserted.connect(self._on_kb_blocks_upserted)
        self._formula_index_flow.formulas_updated.connect(self._on_background_formula_blocks_updated)
        self._formula_index_flow.formula_blocks_detected.connect(self._on_formula_blocks_updated)
        self._formula_index_flow.scan_finished.connect(self._on_formula_index_scan_finished)
        self._formula_semantic_review_flow.review_finished.connect(
            self._on_formula_semantic_review_finished
        )

        # PdfViewer → 内部处理
        self._pdf_viewer.block_double_clicked.connect(self._on_block_double_clicked)
        self._pdf_viewer.block_translate_requested.connect(self._on_block_translate)
        self._pdf_viewer.block_question_requested.connect(self._on_block_question)
        self._pdf_viewer.block_explain_requested.connect(self._on_block_explain)
        self._pdf_viewer.viewport_changed.connect(self._on_viewport_changed)
        self._pdf_viewer.split_close_requested.connect(self._on_split_closed)

        # AIEngine 流式信号（由 PdfViewer 内部的 SplitWidget 消费）
        self._ai_engine.translation_token.connect(self._on_translation_token)
        # 翻译流程（经过 TranslationFlow 协调器：AICache → AIEngine → 缓存）
        self._translate_flow.translation_ready.connect(self._on_translation_ready)
        self._translate_flow.translation_error.connect(self._on_translation_error)

        # 解释流程（经过 ExplainFlow 协调器：OCR → 问题构建）
        self._explain_flow.question_ready.connect(
            lambda q, bid: self._on_split_ask(q, bid)
        )
        self._ask_flow.answer_unavailable.connect(self._on_answer_unavailable)
        self._ask_flow.retrieval_ready.connect(self._on_retrieval_ready)
        self._ai_engine.answer_token.connect(self._on_answer_token)
        self._ai_engine.answer_finished.connect(self._on_answer_finished)
        self._ai_engine.followup_ready.connect(self._on_followup_ready)
        self._ai_engine.answer_error.connect(self._on_answer_error)

        # Navigator
        self._navigator.toc_ready.connect(self._on_toc_ready)
        self._navigator.bookmarks_changed.connect(lambda _: None)  # 后续实现

        # ConfigManager
        self._services.get("config_manager").config_changed.connect(self._on_config_changed)

        # 首次启动检查
        QTimer.singleShot(500, self._check_first_launch)
        # WebView 热备池预热：后台加载模板 HTML，消除首次打开裂缝的延迟
        QTimer.singleShot(800, self._prewarm_webview_pool)

    def _prewarm_webview_pool(self) -> None:
        """后台预热 WebViewPool 热备实例。"""
        from src.ui.split_widget import WebViewPool
        WebViewPool.prewarm()

    # =========================================================================
    # 文件操作
    # =========================================================================

    def _on_open_pdf(self) -> None:
        """打开 PDF 文件对话框。"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "打开 PDF 文件", "",
            "PDF 文件 (*.pdf);;所有文件 (*.*)",
        )
        if filepath:
            self._open_pdf_file(filepath)

    def _open_pdf_file(self, filepath: str) -> None:
        """打开 PDF 文件（委托 DocumentFlow 协调器）。"""
        self.logger.info("_open_pdf_file: %s", filepath)
        self._status_page_label.setText("正在解析 PDF...")
        self._status_progress.setVisible(True)
        self._document_flow.open_document(filepath)

    def _on_document_closed(self) -> None:
        """DocumentFlow 通知文档已关闭 → 清理 UI。"""
        self._pdf_viewer.clear()
        self._current_doc_hash = ""
        self._current_blocks.clear()
        self._blocks_by_id.clear()
        self._loaded_block_pages.clear()
        self._pending_page_blocks.clear()
        self._active_translation_blocks.clear()
        self._last_user_translation_at = 0.0
        self._pymupdf4llm_pending_result = None
        self._pending_kb_upserts.clear()
        self._formula_index_flow.stop()
        self._formula_semantic_review_flow.stop()
        self._stop_formula_import_thread()
        self._formula_viewport_timer.stop()
        self._formula_idle_timer.stop()
        self._last_formula_viewport_pages.clear()
        self._viewer_document_loaded = False
        self._has_native_toc = False
        self._status_page_label.setText("就绪")
        self.setWindowTitle("PDF AI Reader")

    # =========================================================================
    # DocumentEngine 回调
    # =========================================================================

    def _on_document_opened(self, result) -> None:
        """DocumentFlow 通知文档解析完成 → 加载 UI。"""
        import time
        start = time.time()
        self.logger.info("_on_document_opened: %s (%d pages, %d blocks)",
                         result.filepath, result.page_count, len(result.blocks))

        self._current_blocks = list(result.blocks)
        self._blocks_by_id = {b.id: b for b in self._current_blocks}
        self._loaded_block_pages = set(getattr(result, "parsed_pages", []))
        if not self._loaded_block_pages:
            self._loaded_block_pages = {b.page_num for b in self._current_blocks}
        self._has_native_toc = bool(result.toc)
        self._current_doc_hash = self._document_flow.current_hash
        self._ask_flow.set_doc_hash(self._current_doc_hash)
        self._status_progress.setVisible(False)
        self._status_page_label.setText(
            f"{result.title or Path(result.filepath).name} — {result.page_count} 页"
        )
        self._ai_doc_status.setText(
            f"{result.title or Path(result.filepath).name}\n{result.page_count} 页，正在检查知识库"
        )
        self._set_dock_answer_text("", finished=True)
        self._ai_evidence_tree.clear()
        self._clear_dock_followups()
        self.setWindowTitle(f"{result.title or Path(result.filepath).name} — PDF AI Reader")

        # 加载到 PdfViewer。不要在这里 processEvents；长文档后台页块信号可能插队拖慢首屏。
        self._pdf_viewer.load_document(result)
        for page_num, page_blocks in sorted(self._pending_page_blocks.items()):
            if page_num not in self._loaded_block_pages:
                self._loaded_block_pages.add(page_num)
                self._current_blocks = [
                    block for block in self._current_blocks
                    if block.page_num != page_num
                ]
                self._current_blocks.extend(page_blocks)
                for block in page_blocks:
                    self._blocks_by_id[block.id] = block
            self._pdf_viewer.update_page_blocks(page_num, page_blocks)
        self._pending_page_blocks.clear()
        self._viewer_document_loaded = True
        self._last_formula_viewport_pages.clear()
        self._formula_idle_timer.stop()
        QTimer.singleShot(250, lambda fp=result.filepath: self._start_import_formula_plan_thread(fp))

        # 加载目录
        if result.toc:
            self._navigator.load_toc(result.toc)
        elif result.blocks:
            self._navigator.generate_toc_from_blocks(result.blocks)

        render_engine = "PyMuPDF"
        model_state = self._model_status_text()
        self._status_model_label.setText(f"检查知识库... | {model_state} | {render_engine}")
        QTimer.singleShot(0, self._refresh_knowledge_status)

        elapsed = time.time() - start
        self.logger.info("文档加载完成，耗时 %.2fs", elapsed)

        self._pymupdf4llm_pending_result = result
        QTimer.singleShot(15000, self._maybe_run_pymupdf4llm_enhance)
        self._formula_viewport_timer.start(1200)
        self._formula_idle_timer.start(5000)

    def _refresh_knowledge_status(self) -> None:
        """延后检查知识库状态，避免卡住长文档首屏加载。"""
        if not self._current_doc_hash:
            return
        render_engine = "PyMuPDF"
        model_state = self._model_status_text()
        if self._knowledge_engine.check_exists(self._current_doc_hash):
            status = self._knowledge_engine.get_status(self._current_doc_hash)
            count = status.embedded_blocks or status.total_blocks
            suffix = f"{count} 块" if count else "就绪"
            self._status_model_label.setText(f"知识库就绪({suffix}) | {model_state} | {render_engine}")
            self._ai_doc_status.setText(f"知识库就绪\n{suffix}")
        else:
            self._status_model_label.setText(f"知识库构建中... | {model_state} | {render_engine}")
            self._ai_doc_status.setText("知识库构建中")

    def _on_page_blocks_ready(self, page_num: int, blocks: list[DocumentBlock]) -> None:
        """长文档后台补齐某页 blocks。"""
        page_blocks = list(blocks)
        if page_num in self._loaded_block_pages:
            return
        self._loaded_block_pages.add(page_num)
        self._current_blocks = [
            block for block in self._current_blocks
            if block.page_num != page_num
        ]
        self._current_blocks.extend(page_blocks)
        for block in page_blocks:
            self._blocks_by_id[block.id] = block
        if self._viewer_document_loaded:
            self._pdf_viewer.update_page_blocks(page_num, page_blocks)
        else:
            self._pending_page_blocks[page_num] = page_blocks
        if not self._has_native_toc and any(b.block_type == BlockType.HEADING for b in page_blocks):
            self._navigator.generate_toc_from_blocks(self._current_blocks)

    def _on_parse_progress(self, current: int, total: int) -> None:
        """解析进度更新。"""
        self._status_progress.setMaximum(total)
        self._status_progress.setValue(current)

    def _on_parse_error(self, message: str) -> None:
        """解析出错。"""
        self._status_progress.setVisible(False)
        self._status_page_label.setText("解析失败")
        QMessageBox.warning(self, "打开失败", message)

    def _on_page_jump_requested(self) -> None:
        """工具栏页码跳转。输入使用 1-based 页码。"""
        raw = self._page_jump_box.text().strip()
        if not raw:
            return
        try:
            page = int(raw)
        except ValueError:
            self._status_page_label.setText("页码格式错误")
            return
        max_page = self._doc_engine.page_count
        if max_page <= 0:
            self._status_page_label.setText("请先打开 PDF")
            return
        page = max(1, min(max_page, page))
        self._page_jump_box.setText(str(page))
        self._pdf_viewer.scroll_to_page(page - 1)
        self._status_page_label.setText(f"第 {page}/{max_page} 页")

    def _on_formula_blocks_updated(self, updated: list[dict[str, Any]]) -> None:
        """MFD/MFR 精扫完成：更新块类型、LaTeX 内容并刷新 overlay。"""
        update_map = {u["id"]: u for u in updated}
        touched_pages: set[int] = set()
        changed_blocks: list[DocumentBlock] = []
        for block in self._current_blocks:
            if block.id in update_map:
                info = update_map[block.id]
                block.block_type = BlockType(info["block_type"])
                block.metadata.update(info.get("metadata", {}))
                if "bbox" in info:
                    block.bbox = tuple(float(value) for value in info["bbox"])
                touched_pages.add(block.page_num)
                # MFR 阶段：用识别到的 LaTeX 替换公式块内容
                if "content" in info:
                    block.content = info["content"]
                changed_blocks.append(block)
        for info in updated:
            if not info.get("is_new") or info["id"] in self._blocks_by_id:
                continue
            block = DocumentBlock(
                id=info["id"],
                page_num=int(info["page_num"]),
                block_type=BlockType(info["block_type"]),
                content=info.get("content", ""),
                bbox=tuple(info["bbox"]),
                metadata=info.get("metadata", {}),
            )
            self._current_blocks.append(block)
            self._blocks_by_id[block.id] = block
            touched_pages.add(block.page_num)
            changed_blocks.append(block)
        for page_num in touched_pages:
            page_blocks = [b for b in self._current_blocks if b.page_num == page_num]
            self._pdf_viewer.update_page_blocks(page_num, page_blocks)
        # 刷新已渲染的 overlay
        for block_id in update_map:
            ov = self._pdf_viewer._overlays.get(block_id)
            if ov is None:
                # 可能在 _LazyPageWidget 中
                for container in self._pdf_viewer._page_containers.values():
                    ov = container.overlay(block_id)
                    if ov:
                        break
            if ov:
                ov.update()
        self._status_model_label.setText(
            f"📚 知识库就绪 | ✅ 公式精扫完成 | 🖥️ {'QtPdf' if self._doc_engine.using_qtpdf else 'PyMuPDF'}"
        )
        if changed_blocks:
            self._queue_knowledge_upsert(changed_blocks)
        pending = [
            block for block in changed_blocks
            if block.block_type == BlockType.FORMULA and block.metadata.get("needs_ocr")
        ]
        if pending:
            filepath = getattr(self._doc_engine, "_filepath", "")
            priority_pages = {block.page_num for block in pending[:4]}
            self._schedule_formula_scan(
                pages=priority_pages,
                trigger=FormulaScanTrigger.USER_ACTION,
                filepath=filepath,
                blocks=pending,
            )

    def _on_background_formula_blocks_updated(self, updated_blocks: list[DocumentBlock]) -> None:
        """后台公式索引返回 LaTeX：刷新页面并增量更新知识库。"""
        if not updated_blocks:
            return
        touched_pages: set[int] = set()
        for updated in updated_blocks:
            existing = self._blocks_by_id.get(updated.id)
            if existing is None:
                self._current_blocks.append(updated)
                self._blocks_by_id[updated.id] = updated
                existing = updated
            else:
                existing.content = updated.content
                existing.block_type = updated.block_type
                existing.metadata.update(updated.metadata)
            touched_pages.add(existing.page_num)
        for page_num in touched_pages:
            page_blocks = [b for b in self._current_blocks if b.page_num == page_num]
            self._pdf_viewer.update_page_blocks(page_num, page_blocks)
        blocks = [self._blocks_by_id[b.id] for b in updated_blocks if b.id in self._blocks_by_id]
        self._queue_knowledge_upsert(blocks)
        self.logger.info("后台公式索引更新 %d 个公式块", len(updated_blocks))

    def _on_formula_index_scan_finished(self, recognized: int, pending: int) -> None:
        """后台公式索引一批完成。"""
        if recognized or pending:
            self._ai_doc_status.setText(
                f"知识库就绪\n公式索引: 本批识别 {recognized}，待补扫 {pending}"
            )

    def _on_formula_semantic_review_finished(self, result: dict[str, object]) -> None:
        """r3 semantic review finished one bounded batch."""
        if str(result.get("doc_hash", "")) != self._current_doc_hash:
            return
        done = int(result.get("done", 0) or 0)
        failed = int(result.get("failed", 0) or 0)
        skipped = int(result.get("skipped", 0) or 0)
        pending = int(result.get("pending", 0) or 0)
        error = str(result.get("error", "") or "")
        if error:
            self.logger.warning("公式语义复核失败: %s", error)
        if done or failed or skipped or pending:
            self._ai_doc_status.setText(
                f"知识库就绪\n公式语义复核: 完成 {done}，失败 {failed}，跳过 {skipped}，待复核 {pending}"
            )
        if pending > 0 and self._viewer_document_loaded:
            self._formula_idle_timer.start(8000)

    def _on_viewport_changed(self, value: int, maximum: int) -> None:
        """Schedule a tiny cache-only formula scan for pages near the viewport."""
        if not self._viewer_document_loaded or not self._current_doc_hash:
            return
        self._formula_viewport_timer.start()

    def _schedule_viewport_formula_scan(self) -> None:
        pages = self._pdf_viewer.visible_pages(margin_pages=False)
        if not pages or pages == self._last_formula_viewport_pages:
            return
        self._last_formula_viewport_pages = set(pages)
        self._schedule_formula_scan(pages=pages, trigger=FormulaScanTrigger.VIEWPORT)

    def _schedule_idle_formula_scan(self) -> None:
        """Run one low-priority background formula/index batch."""
        if not self._viewer_document_loaded or not self._current_doc_hash:
            return
        if self._formula_index_flow.is_running or self._formula_semantic_review_flow.is_running:
            self._formula_idle_timer.start(5000)
            return
        before_pending = self._pending_formula_work_count()
        if before_pending <= 0:
            return
        if (
            self._pending_formula_block_count() > 0
            or self._formula_index_flow.pending_count(self._current_doc_hash) > 0
        ):
            self._schedule_formula_scan(pages=set(), trigger=FormulaScanTrigger.BACKGROUND)
        elif not self._start_formula_semantic_review_batch():
            if not self._run_formula_knowledge_graph_batch():
                if not self._run_formula_knowledge_update_batch():
                    self._start_import_page_scan_batch()
        if self._pending_formula_work_count() > 0:
            self._formula_idle_timer.start(8000)

    def _on_high_precision_formula_scan(self) -> None:
        """User-triggered high-precision formula scan for current viewport first."""
        if not self._viewer_document_loaded or not self._current_doc_hash:
            QMessageBox.information(self, "公式精扫", "请先打开一个 PDF 文件。")
            return
        pages = self._pdf_viewer.visible_pages(margin_pages=True)
        plan = self._formula_index_scheduler.plan_for_pages(
            self._current_blocks,
            pages,
            FormulaScanTrigger.HIGH_PRECISION,
            self._doc_engine.page_count,
        )
        if not plan.blocks:
            self._ai_doc_status.setText("公式索引\n当前没有待精扫公式")
            return
        filepath = getattr(self._doc_engine, "_filepath", "")
        self._enqueue_formula_plan(filepath, plan, "high_precision")
        self._ai_doc_status.setText(
            f"公式精扫已启动\n本批 {min(len(plan.blocks), plan.batch_budget)} / 待扫 {len(plan.blocks)}"
        )

    def _on_open_formula_acceptance_review(self) -> None:
        """Open the audited formula acceptance review dialog."""
        if not self._viewer_document_loaded or not self._current_doc_hash:
            QMessageBox.information(self, "公式审核", "请先打开一个 PDF 文件。")
            return
        from src.ui.formula_acceptance_dialog import FormulaAcceptanceDialog

        filepath = getattr(self._doc_engine, "_filepath", "")
        service = FormulaAcceptanceReviewService(self._formula_index_flow.store)
        dlg = FormulaAcceptanceDialog(service, self._current_doc_hash, filepath, self)
        dlg.evidence_location_requested.connect(self._on_formula_evidence_location_requested)
        dlg.exec()
        if self._formula_knowledge_update_service.pending_count(self._current_doc_hash) > 0:
            self._formula_idle_timer.start(1000)

    def _on_formula_evidence_location_requested(self, page_num: int, bbox: object) -> None:
        try:
            box = tuple(float(value) for value in bbox)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if len(box) != 4:
            return
        self._pdf_viewer.scroll_to_bbox(int(page_num), box)

    def _schedule_evidence_formula_scan(self, evidence: list[dict[str, Any]]) -> None:
        if not evidence or not self._current_doc_hash:
            return
        filepath = getattr(self._doc_engine, "_filepath", "")
        plan = self._formula_index_scheduler.plan_for_evidence(
            self._current_blocks,
            evidence,
            self._doc_engine.page_count,
        )
        self._enqueue_formula_plan(filepath, plan, "evidence")

    def _schedule_formula_scan(
        self,
        pages: set[int],
        trigger: FormulaScanTrigger,
        filepath: str = "",
        blocks: list[DocumentBlock] | None = None,
    ) -> None:
        if not self._current_doc_hash:
            return
        filepath = filepath or getattr(self._doc_engine, "_filepath", "")
        source_blocks = blocks if blocks is not None else self._current_blocks
        plan = self._formula_index_scheduler.plan_for_pages(
            source_blocks,
            pages,
            trigger,
            self._doc_engine.page_count,
        )
        self._enqueue_formula_plan(filepath, plan, trigger.value)

    def _enqueue_formula_plan(self, filepath: str, plan: Any, reason: str) -> None:
        if not filepath or not plan.blocks:
            return
        self.logger.info(
            "公式扫描调度: reason=%s blocks=%d budget=%d cache_only=%s pages=%s",
            reason,
            len(plan.blocks),
            plan.batch_budget,
            plan.cache_only,
            sorted(plan.priority_pages)[:8],
        )
        self._formula_index_flow.enqueue_plan(filepath, self._current_doc_hash, plan)

    def _start_import_formula_plan_thread(self, filepath: str) -> None:
        """Persist full-document formula work off the UI thread."""
        if not filepath or not self._current_doc_hash or self._doc_engine.page_count <= 0:
            return
        if self._formula_import_thread is not None and self._formula_import_thread.isRunning():
            return
        plan = self._formula_index_scheduler.plan_for_pages(
            self._current_blocks,
            pages=set(),
            trigger=FormulaScanTrigger.BACKGROUND,
            page_count=self._doc_engine.page_count,
        )
        self._formula_import_thread = _FormulaImportPlanThread(
            store=self._formula_index_flow.store,
            filepath=filepath,
            doc_hash=self._current_doc_hash,
            page_count=self._doc_engine.page_count,
            plan_blocks=plan.blocks,
            plan_priority_pages=plan.priority_pages,
            plan_scan_round=plan.scan_round,
            formula_blocks=[
                block for block in self._current_blocks
                if block.block_type == BlockType.FORMULA
            ],
        )
        self._formula_import_thread.finished_signal.connect(self._on_import_formula_plan_persisted)
        self._formula_import_thread.finished.connect(self._formula_import_thread.deleteLater)
        self._formula_import_thread.finished.connect(self._on_formula_import_thread_done)
        self._formula_import_thread.start()

    def _on_import_formula_plan_persisted(self, result: dict[str, int | str]) -> None:
        if result.get("doc_hash") != self._current_doc_hash:
            return
        queued_blocks = int(result.get("queued_blocks", 0) or 0)
        queued_pages = int(result.get("queued_pages", 0) or 0)
        queued_reviews = int(result.get("queued_reviews", 0) or 0)
        queued_graph = int(result.get("queued_graph", 0) or 0)
        if queued_blocks or queued_pages or queued_reviews or queued_graph:
            self.logger.info(
                "导入后全篇公式任务入队: block_ocr=%d page_mfd=%d semantic_review=%d knowledge_graph=%d",
                queued_blocks,
                queued_pages,
                queued_reviews,
                queued_graph,
            )
            self._ai_doc_status.setText(
                f"知识库构建中\n公式任务已入队: 页面 {queued_pages}，公式 {queued_blocks}，复核 {queued_reviews}，图谱 {queued_graph}"
            )
        if self._pending_formula_work_count() > 0:
            self._formula_idle_timer.start(8000)

    def _on_formula_import_thread_done(self) -> None:
        self._formula_import_thread = None

    def _stop_formula_import_thread(self) -> None:
        thread = self._formula_import_thread
        if thread and thread.isRunning():
            thread.requestInterruption()
            thread.quit()
            thread.wait(1500)
        self._formula_import_thread = None

    def _start_import_page_scan_batch(self) -> bool:
        filepath = getattr(self._doc_engine, "_filepath", "")
        if not filepath or not self._current_doc_hash:
            return False
        if self._formula_index_flow.page_pending_count(self._current_doc_hash) <= 0:
            return False
        pages = self._pdf_viewer.visible_pages(margin_pages=True)
        started = self._formula_index_flow.start_page_scan_batch(
            filepath=filepath,
            doc_hash=self._current_doc_hash,
            blocks=self._current_blocks,
            allowed_pages=pages,
            priority_pages=pages,
            batch_budget=2,
        )
        if started:
            self.logger.info("页面级公式检测启动: pages=%s batch=%d", sorted(pages), started)
            return True
        started = self._formula_index_flow.start_page_scan_batch(
            filepath=filepath,
            doc_hash=self._current_doc_hash,
            blocks=self._current_blocks,
            batch_budget=2,
        )
        if started:
            self.logger.info("页面级公式检测启动: batch=%d", started)
        return bool(started)

    def _start_formula_semantic_review_batch(self) -> bool:
        if not self._current_doc_hash:
            return False
        if self._formula_semantic_review_flow.pending_count(self._current_doc_hash) <= 0:
            return False
        started = self._formula_semantic_review_flow.start_batch(
            self._current_doc_hash,
            self._current_blocks,
            limit=4,
        )
        if started:
            self.logger.info("公式语义复核启动: batch=4")
        return started

    def _run_formula_knowledge_update_batch(self) -> bool:
        if not self._current_doc_hash:
            return False
        service = self._formula_knowledge_update_service
        if service.pending_count(self._current_doc_hash) <= 0:
            return False
        result = service.run_batch(
            self._current_doc_hash,
            self._current_blocks,
            limit=8,
        )
        if result.deferred:
            self.logger.info("公式 r5 增量知识更新等待知识库就绪: %d", result.deferred)
            return False
        if result.done or result.failed or result.skipped:
            self.logger.info(
                "公式 r5 增量知识更新: done=%d failed=%d skipped=%d graph_synced=%d graph_failed=%d pending=%d",
                result.done,
                result.failed,
                result.skipped,
                result.graph_synced,
                result.graph_failed,
                result.pending,
            )
            return True
        return False

    def _run_formula_knowledge_graph_batch(self) -> bool:
        if not self._current_doc_hash:
            return False
        service = self._formula_knowledge_graph_service
        if service.pending_count(self._current_doc_hash) <= 0:
            return False
        filepath = getattr(self._doc_engine, "_filepath", "")
        result = service.run_batch(
            self._current_doc_hash,
            filepath,
            self._current_blocks,
            limit=8,
        )
        if result.done or result.failed or result.skipped:
            self.logger.info(
                "公式 r4 图谱增强: done=%d failed=%d skipped=%d pending=%d",
                result.done,
                result.failed,
                result.skipped,
                result.pending,
            )
            self._ai_doc_status.setText(
                f"知识库就绪\n公式图谱: 完成 {result.done}，失败 {result.failed}，跳过 {result.skipped}，待处理 {result.pending}"
            )
            return True
        return False

    def _pending_formula_block_count(self) -> int:
        return sum(
            1 for block in self._current_blocks
            if block.block_type == BlockType.FORMULA
            and block.metadata.get("needs_ocr")
            and not block.metadata.get("mfr_recognized")
        )

    def _pending_formula_work_count(self) -> int:
        if not self._current_doc_hash:
            return self._pending_formula_block_count()
        return (
            self._pending_formula_block_count()
            + self._formula_index_flow.pending_count(self._current_doc_hash)
            + self._formula_index_flow.page_pending_count(self._current_doc_hash)
            + self._formula_index_flow.round_pending_count(self._current_doc_hash)
            + self._formula_semantic_review_flow.pending_count(self._current_doc_hash)
            + self._formula_knowledge_graph_service.pending_count(self._current_doc_hash)
            + self._formula_knowledge_update_service.pending_count(self._current_doc_hash)
        )

    # =========================================================================
    # KnowledgeEngine 回调
    # =========================================================================

    def _on_kb_progress(self, current: int, total: int) -> None:
        """知识库构建进度。"""
        self._status_progress.setVisible(True)
        self._status_progress.setMaximum(total)
        self._status_progress.setValue(current)
        self._ai_doc_status.setText(f"知识库构建中\n{current}/{total} 块")

    def _on_kb_finished(self, doc_hash: str) -> None:
        """知识库构建完成。"""
        self._status_progress.setVisible(False)
        render_engine = "QtPdf" if self._doc_engine.using_qtpdf else "PyMuPDF"
        self._status_model_label.setText(f"📚 知识库就绪 | {self._model_status_text()} | 🖥️ {render_engine}")
        try:
            status = self._knowledge_engine.get_status(doc_hash)
            count = status.embedded_blocks or status.total_blocks
        except Exception:
            count = 0
        self._ai_doc_status.setText(f"知识库就绪\n{count or '未知'} 块")
        self._flush_pending_knowledge_upserts(doc_hash)

    def _on_kb_error(self, message: str) -> None:
        """知识库构建失败。"""
        self._status_progress.setVisible(False)
        self._status_model_label.setText("⚠️ 知识库构建失败")
        self._ai_doc_status.setText("知识库构建失败")
        self.logger.warning("知识库构建失败: %s", message)

    def _on_kb_blocks_upserted(self, doc_hash: str, count: int) -> None:
        """知识库增量更新完成。"""
        if doc_hash != self._current_doc_hash:
            return
        self.logger.info("知识库增量更新完成: %d 个块", count)

    def _queue_knowledge_upsert(self, blocks: list[DocumentBlock]) -> None:
        """Queue or run incremental index updates for formula-enriched blocks."""
        if not blocks or not self._current_doc_hash:
            return
        unique_blocks = {block.id: block for block in blocks if block.id}
        if not unique_blocks:
            return
        if self._knowledge_engine.check_exists(self._current_doc_hash):
            self._knowledge_engine.upsert_blocks(list(unique_blocks.values()), self._current_doc_hash)
            return
        self._pending_kb_upserts.update(unique_blocks)
        self.logger.info("知识库尚未就绪，暂存 %d 个增量公式块", len(unique_blocks))

    def _flush_pending_knowledge_upserts(self, doc_hash: str) -> None:
        """Flush formula OCR updates collected while the base index was building."""
        if doc_hash != self._current_doc_hash or not self._pending_kb_upserts:
            return
        blocks = list(self._pending_kb_upserts.values())
        self._pending_kb_upserts.clear()
        self._knowledge_engine.upsert_blocks(blocks, doc_hash)
        self.logger.info("知识库就绪后写入暂存公式块: %d", len(blocks))

    def _on_build_knowledge_base(self) -> None:
        """手动触发知识库构建/重建。"""
        if not self._current_blocks:
            QMessageBox.information(self, "提示", "请先打开一个 PDF 文件。")
            return
        self._status_model_label.setText("🔨 正在检查知识库...")
        self._knowledge_engine.build_knowledge_base(
            self._current_blocks, self._current_doc_hash, force_rebuild=False
        )

    # =========================================================================
    # 段落交互
    # =========================================================================

    def _on_block_double_clicked(self, block_id: str) -> None:
        """双击段落：首次翻译，再次折叠/展开切换。"""
        self.logger.info("双击段落: %s", block_id)
        split = self._pdf_viewer.find_split_widget(block_id)
        if split:
            if split.collapsed:
                split.expand()
            else:
                split.collapse()
        else:
            self._on_block_translate(block_id)

    def _on_block_translate(self, block_id: str) -> None:
        """右键 → 翻译段落（委托 TranslationFlow 协调器）。"""
        self.logger.info("翻译请求: %s", block_id)
        self._last_user_translation_at = time.monotonic()
        block = self._find_block(block_id)
        split = self._pdf_viewer.open_split_widget(block_id, SplitMode.TRANSLATION)

        if not split or not block:
            return
        if split.collapsed:
            split.expand()
            return
        if split._current_answer:
            return

        # 委托 TranslationFlow（内部处理 AICache 检查 + AIEngine 调用）
        hit = self._translate_flow.request_translation(block, self._current_doc_hash)
        if not hit:
            self._active_translation_blocks.add(block_id)
            split.set_busy(True)

    def _on_block_question(self, block_id: str) -> None:
        """右键 → 提问。"""
        split = self._pdf_viewer.open_split_widget(block_id, SplitMode.QUESTION)
        if split:
            split.question_submitted.connect(
                lambda q, bid=block_id: self._on_split_ask(q, bid)
            )

    def _on_block_explain(self, block_id: str) -> None:
        """右键 → 解释概念（委托 ExplainFlow 协调器）。"""
        block = self._find_block(block_id)
        split = self._pdf_viewer.open_split_widget(block_id, SplitMode.EXPLANATION)
        if not split or not block:
            return
        split.set_busy(True)
        self._explain_flow.request_explanation(block, split)

    def _on_split_ask(self, question: str, block_id: str) -> None:
        """裂缝中提交问题 → 委托 AskQuestionFlow 协调器。"""
        block = self._find_block(block_id)
        split = self._pdf_viewer.find_split_widget(block_id)
        chat_history = split.chat_history if split else None
        self._ask_flow.request_answer(
            question=question,
            block=block,
            block_id=block_id,
            chat_history=chat_history,
            find_block_cb=self._find_block,
            all_blocks=self._current_blocks,
        )

    def _on_dock_question_submitted(self) -> None:
        """右侧全文问答提交。"""
        question = self._ai_question_input.text().strip()
        if not question:
            return
        if not self._current_doc_hash:
            self._set_dock_answer_text("请先打开 PDF。", finished=True)
            return
        self._dock_last_question = question
        self._dock_followup_questions = []
        self._set_dock_answer_text("正在检索全文知识库...", finished=False)
        self._ai_evidence_tree.clear()
        self._clear_dock_followups()
        self._ask_flow.request_answer(
            question=question,
            block=None,
            block_id=self._dock_answer_split_id,
            chat_history=None,
            find_block_cb=self._find_block,
            all_blocks=self._current_blocks,
        )

    def _on_retrieval_ready(self, block_id: str, evidence: list[dict[str, Any]]) -> None:
        """展示问答检索到的全文依据。"""
        if block_id != self._dock_answer_split_id:
            return
        self._ai_evidence_tree.clear()
        for item in evidence:
            page = int(item.get("page", 0))
            content = str(item.get("content", "")).strip().replace("\n", " ")
            relevance = float(item.get("retrieval_score", 0.0))
            lexical = float(item.get("lexical_score", 0.0))
            vector = float(item.get("vector_score", 0.0))
            source_id = str(item.get("source_id") or "")
            block_type = str(item.get("type") or "")
            section = str(item.get("section") or "")
            tree_item = QTreeWidgetItem(self._ai_evidence_tree)
            tree_item.setText(0, f"[{source_id}] 第{page}页")
            tree_item.setText(1, f"{relevance:.2f}")
            tree_item.setText(2, content[:180])
            detail = (
                f"{source_id} · 第{page}页 · {block_type}"
                f"{' · ' + section if section else ''}\n"
                f"相关度 {relevance:.2f} · 词面 {lexical:.2f} · 向量 {vector:.2f}\n\n"
                f"{content}"
            )
            tree_item.setToolTip(0, detail)
            tree_item.setToolTip(1, detail)
            tree_item.setToolTip(2, detail)
            tree_item.setData(0, Qt.ItemDataRole.UserRole, page - 1)
            tree_item.setData(2, Qt.ItemDataRole.UserRole, item.get("id", ""))
        self._ai_doc_status.setText(f"检索到 {len(evidence)} 条依据")
        self._schedule_evidence_formula_scan(evidence)

    def _on_evidence_item_activated(self, item: QTreeWidgetItem, column: int) -> None:
        """双击右侧证据片段跳转到来源页。"""
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page is not None:
            self._pdf_viewer.scroll_to_page(int(page))

    def _clear_dock_followups(self) -> None:
        """清空右侧全文问答追问建议。"""
        if not hasattr(self, "_ai_followup_layout"):
            return
        while self._ai_followup_layout.count():
            item = self._ai_followup_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self._dock_followup_questions = []
        self._ai_followup_label.setVisible(False)

    def _on_dock_followup_clicked(self, question: str) -> None:
        """右侧追问按钮点击后继续基于全文提问。"""
        self._ai_question_input.setText(question)
        self._on_dock_question_submitted()

    # =========================================================================
    # AI 翻译回调
    # =========================================================================

    def _on_translation_token(self, token: str, block_id: str) -> None:
        """翻译流式 token。"""
        split = self._pdf_viewer.find_split_widget(block_id)
        if split:
            split.display_answer_stream(token)
        else:
            self.logger.debug("翻译token SplitWidget已关闭: %s", block_id)

    def _on_translation_ready(self, full_text: str, block_id: str) -> None:
        """翻译就绪（来自 TranslationFlow，缓存或 AI）——渲染 Markdown/LaTeX。"""
        self._active_translation_blocks.discard(block_id)
        split = self._pdf_viewer.find_split_widget(block_id)
        if split:
            text = full_text if full_text else split._current_answer
            has_dollar = '$$' in text or '$' in text
            has_formula = '【FORMULA' in text
            self.logger.info("翻译就绪 block=%s len=%d has$$=%s hasF=%s preview=%s",
                             block_id, len(text), has_dollar, has_formula, text[:120])
            split.display_full_answer(full_text)

    def _on_answer_finished(self, full_answer: str, split_id: str) -> None:
        """问答完成——渲染 Markdown/LaTeX。"""
        self.logger.info("问答完成 split=%s", split_id)
        if split_id == self._dock_answer_split_id:
            self._set_dock_answer_text(full_answer or self._dock_answer_text, finished=True)
            self._ai_doc_status.setText("全文问答完成")
            return
        split = self._pdf_viewer.find_split_widget(split_id)
        if split:
            split.display_full_answer(full_answer)

    def _on_followup_ready(self, questions: list[str], split_id: str) -> None:
        """问答完成后的追问建议。"""
        clean_questions = [q.strip() for q in questions if isinstance(q, str) and q.strip()]
        if split_id == self._dock_answer_split_id:
            self._clear_dock_followups()
            self._dock_followup_questions = clean_questions[:3]
            for question in self._dock_followup_questions:
                btn = QPushButton(question[:56] + ("..." if len(question) > 56 else ""))
                btn.setObjectName("ai_followup_button")
                btn.setToolTip(question)
                btn.setProperty("followup_question", question)
                btn.clicked.connect(lambda checked=False, text=question: self._on_dock_followup_clicked(text))
                self._ai_followup_layout.addWidget(btn)
            self._ai_followup_label.setVisible(bool(self._dock_followup_questions))
            if self._dock_followup_questions:
                self._ai_doc_status.setText("全文问答完成，可继续追问")
            self.logger.info("追问建议 split=%s count=%d", split_id, len(self._dock_followup_questions))
            return
        split = self._pdf_viewer.find_split_widget(split_id)
        if split:
            split.show_followup_questions(clean_questions)
        self.logger.info("追问建议 split=%s count=%d", split_id, len(clean_questions))

    def _on_answer_unavailable(self, message: str, split_id: str) -> None:
        """知识库未就绪或无上下文时，直接在裂缝中给出边界提示。"""
        split = self._pdf_viewer.find_split_widget(split_id)
        if split:
            split.display_full_answer(message)
        if split_id == self._dock_answer_split_id:
            self._set_dock_answer_text(message, finished=True)
            self._ai_doc_status.setText("全文问答不可用")

    def _on_translation_error(self, message: str, block_id: str) -> None:
        """翻译出错。"""
        self._active_translation_blocks.discard(block_id)
        self.logger.error("翻译失败 block=%s: %s", block_id, message)
        split = self._pdf_viewer.find_split_widget(block_id)
        if split:
            split.show_error(f"翻译失败: {message}")

    # =========================================================================
    # AI 问答回调
    # =========================================================================

    def _on_answer_token(self, token: str, split_id: str) -> None:
        """问答流式 token。"""
        if split_id == self._dock_answer_split_id:
            self._dock_answer_text += token
            self._render_dock_answer(self._dock_answer_text, finished=False)
            return
        split = self._pdf_viewer.find_split_widget(split_id)
        if split:
            split.display_answer_stream(token)

    def _on_answer_error(self, message: str, split_id: str) -> None:
        """问答出错。"""
        self.logger.error("问答失败 split=%s: %s", split_id, message)
        if split_id == self._dock_answer_split_id:
            self._set_dock_answer_text(f"回答失败: {message}", finished=True)
            self._ai_doc_status.setText("全文问答失败")
            return
        split = self._pdf_viewer.find_split_widget(split_id)
        if split:
            split.show_error(f"回答失败: {message}")

    def _on_split_closed(self, block_id: str) -> None:
        """裂缝关闭后的清理。"""
        pass  # 目前保留裂缝实例以便重新打开时恢复缓存

    # =========================================================================
    # 导航
    # =========================================================================

    def _on_toc_ready(self, toc: list[dict[str, Any]]) -> None:
        """目录数据就绪，更新左侧目录树。"""
        self._toc_tree.clear()
        for item in toc:
            tree_item = QTreeWidgetItem(self._toc_tree)
            tree_item.setText(0, item.get("title", ""))
            tree_item.setData(0, Qt.ItemDataRole.UserRole, item.get("page", 0))

    def _on_toc_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """目录项点击 → 跳转到对应页。"""
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if page is not None:
            self._pdf_viewer.scroll_to_page(int(page))

    # =========================================================================
    # 设置与主题
    # =========================================================================

    def _on_open_settings(self) -> None:
        """打开设置对话框 — 配置云端 API。"""
        from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QComboBox

        dlg = QDialog(self)
        dlg.setWindowTitle("设置 — 云端 API 配置")
        layout = QFormLayout(dlg)

        provider_box = QComboBox()
        providers = ["deepseek/deepseek-chat", "deepseek/deepseek-v4-pro", "openai/gpt-4o", "qwen/qwen-plus",
                      "glm-4", "moonshot-v1-8k"]
        provider_box.addItems(providers)
        # 选中当前配置
        current_cloud = self._config.model.cloud_translation or self._config.model.cloud
        if current_cloud in providers:
            provider_box.setCurrentText(current_cloud)
        layout.addRow("模型:", provider_box)

        key_edit = QLineEdit()
        key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        key_edit.setPlaceholderText("输入 API Key...")
        existing = self._services.get("config_manager").get_api_key(current_cloud) or ""
        key_edit.setText(existing)
        layout.addRow("API Key:", key_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            provider = provider_box.currentText()
            api_key = key_edit.text().strip()
            if api_key:
                # 保存到配置
                cm = self._services.get("config_manager")
                current_cfg = cm.get()
                current_cfg.api_keys[provider] = api_key
                cm.update({
                    "model": {
                        "cloud": provider,
                        "cloud_translation": provider,
                    },
                    "api_keys": {provider: api_key},
                })
                self._status_model_label.setText(f"✅ 云端: {provider}")
                QMessageBox.information(self, "已保存",
                    f"云端模型 {provider} 已配置。\n请重启应用以初始化云端客户端。")
            else:
                QMessageBox.warning(self, "未保存", "API Key 不能为空。")

    def _on_open_glossary_editor(self) -> None:
        """打开术语表管理器。"""
        QMessageBox.information(self, "术语表管理器", "术语表编辑器将在后续版本实现。")

    def _on_config_changed(self, config: AppConfig) -> None:
        """配置变更：重新应用主题。"""
        self._config = config
        self._apply_theme()

    def _model_status_text(self) -> str:
        """状态栏模型说明，明确云端/本地/测试模式边界。"""
        strategy = self._config.routing.translation
        cloud = self._config.model.cloud_translation or self._config.model.cloud
        reasoning = self._config.model.cloud_reasoning
        key = self._services.get("config_manager").get_api_key(cloud)
        has_key = bool(key and key.strip() and "在此填入" not in key)
        if strategy == "cloud_only":
            return f"翻译: {cloud} · 全文: {reasoning}" if has_key else "测试模式: 未配置云端 API"
        if strategy == "local_only":
            return f"本地生成: {self._config.model.local}"
        return f"混合路由: 本地优先→{cloud if has_key else 'Mock'}"

    def _on_about(self) -> None:
        """关于对话框。"""
        QMessageBox.about(
            self, "关于 PDF AI Reader",
            "PDF AI Reader v0.5 (原型版)\n\n"
            "面向专业数理论文的 AI 辅助阅读与翻译工具。\n\n"
            "技术栈: Python 3.14.4 + PySide6 + PyMuPDF + Ollama\n"
            "本地模型: Qwen3.5:4b\n"
            "嵌入模型: BGE-M3",
        )

    def _maybe_run_pymupdf4llm_enhance(self) -> None:
        """只在阅读空闲时启动增强解析，避免打断打开/滚动/翻译。"""
        result = self._pymupdf4llm_pending_result
        if result is None or not self._current_doc_hash:
            return
        now = time.monotonic()
        if self._active_translation_blocks or now - self._last_user_translation_at < 20.0:
            QTimer.singleShot(10000, self._maybe_run_pymupdf4llm_enhance)
            return
        self._pymupdf4llm_pending_result = None
        self._run_pymupdf4llm_enhance(result)

    def _run_pymupdf4llm_enhance(self, result) -> None:
        """启动 QThread 运行 PyMuPDF4LLM 增强解析，完成后通过 Signal 回到主线程。"""
        if result is None:
            return
        if result.filepath != getattr(self._doc_engine, "_filepath", ""):
            return
        if getattr(result, "page_count", 0) > 200:
            self.logger.info(
                "PyMuPDF4LLM 增强跳过: 长文档 %d 页，优先保证打开/滚动/翻译速度",
                result.page_count,
            )
            return
        # 仅拷贝非图片块，避免跨线程读写 UI 持有的原对象
        text_blocks = [b for b in result.blocks if b.block_type != BlockType.IMAGE]
        if not text_blocks:
            return
        thread = _PyMuPDF4LLMThread(result.filepath, text_blocks)
        thread.finished_signal.connect(self._on_pymupdf4llm_finished)
        thread.finished.connect(lambda t=thread: self._ai_engine._active_threads.remove(t) if t in self._ai_engine._active_threads else None)
        self._ai_engine._active_threads.append(thread)
        thread.start()

    def _on_pymupdf4llm_finished(self, enhanced_data: list[tuple[str, str]]) -> None:
        """主线程槽函数：安全更新 block.content。"""
        if self._active_translation_blocks:
            self.logger.info(
                "PyMuPDF4LLM 增强结果暂不应用: %d 个翻译进行中",
                len(self._active_translation_blocks),
            )
            QTimer.singleShot(
                10000,
                lambda data=list(enhanced_data): self._on_pymupdf4llm_finished(data),
            )
            return
        update_map = dict(enhanced_data)
        for block in self._current_blocks:
            if block.id in update_map:
                if block.id in self._active_translation_blocks:
                    continue
                block.content = update_map[block.id]
                block.metadata["enhanced_by"] = "pymupdf4llm"
        self.logger.info("PyMuPDF4LLM 增强完成: %d 个块", len(enhanced_data))


    def _check_first_launch(self) -> None:
        """首次启动检查：默认检查云端配置，本地模式才检查 Ollama。"""
        if self._config.routing.translation == "cloud_only" or self._config.routing.qa == "cloud_only":
            cloud = self._config.model.cloud_translation or self._config.model.cloud
            key = self._services.get("config_manager").get_api_key(cloud)
            if key and key.strip() and "在此填入" not in key:
                self._status_model_label.setText(
                    f"✅ 翻译: {cloud} · 全文: {self._config.model.cloud_reasoning}"
                )
                return
            self._status_model_label.setText("⚠️ 未配置云端 API，当前为测试模式")
            QMessageBox.information(
                self,
                "模型配置",
                "当前默认使用云端模型生成翻译和回答，但尚未配置 API Key。\n\n"
                "在配置前，应用会使用模拟回答，知识库检索和翻译质量不能代表真实效果。\n"
                "配置云端模型后，翻译/问答内容会发送到对应 API 服务；PDF 解析和本地索引仍保存在本机。",
            )
            return

        model_status = self._ai_engine.check_local_model_status()

        if not model_status["ollama_available"]:
            self._status_model_label.setText("⚠️ Ollama 服务未连接")

            reply = QMessageBox.question(
                self, "首次启动",
                "未检测到 Ollama 服务。\n\n"
                "只有在启用本地生成模式时才需要 Ollama。\n"
                "请确保已安装并启动 Ollama 桌面应用。\n\n"
                "是否前往 Ollama 官网下载？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                import webbrowser
                webbrowser.open("https://ollama.com/download")
        elif not model_status["qwen_available"]:
            self._status_model_label.setText("⚠️ 模型未下载")

            reply = QMessageBox.question(
                self, "首次启动",
                "未检测到本地 AI 模型 (Qwen3.5:4b)。\n\n"
                "请在终端执行以下命令下载模型：\n"
                "  ollama pull qwen3.5:4b\n\n"
                "下载完成后重新启动应用即可。",
                QMessageBox.StandardButton.Ok,
            )
        else:
            self._status_model_label.setText("✅ 本地模型就绪")

    # =========================================================================
    # 工具方法
    # =========================================================================

    def _find_block(self, block_id: str) -> DocumentBlock | None:
        """在 _current_blocks 中查找指定 ID 的块。

        Args:
            block_id: 块 ID。

        Returns:
            DocumentBlock 或 None。
        """
        block = self._blocks_by_id.get(block_id)
        if block is not None:
            return block
        for block in self._current_blocks:
            if block.id == block_id:
                self._blocks_by_id[block_id] = block
                return block
        return None

    @property
    def logger(self) -> logging.Logger:
        """获取日志记录器。"""
        return logging.getLogger("MainWindow")

    def closeEvent(self, event) -> None:
        """窗口关闭事件：按顺序清理资源。

        清理顺序：文档引擎 → 知识库引擎 → 术语表 → 配置。
        确保 ChromaDB WAL 文件正确刷入磁盘。
        """
        # 1. 关闭文档（停止解析线程，关闭 PDF 文件）
        self._formula_index_flow.stop()
        self._stop_formula_import_thread()
        self._doc_engine.close_document()
        # 2. 关闭知识库引擎（等待构建任务完成，关闭数据库连接）
        self._knowledge_engine.close()
        # 3. 保存术语表变更
        self._glossary_manager.save()
        event.accept()


class _PyMuPDF4LLMThread(QThread):
    """后台运行 PyMuPDF4LLM 增强，完成后通过 Signal 将结果发回主线程。"""
    finished_signal = Signal(list)  # list[tuple[str, str]]

    def __init__(self, filepath: str, blocks: list[DocumentBlock]) -> None:
        super().__init__()
        self._filepath = filepath
        self._blocks_copy = [b.model_copy() for b in blocks]

    def run(self) -> None:
        try:
            from src.core.pdf_engine import PyMuPDF4LLMChunker
            import fitz

            enhancer = PyMuPDF4LLMChunker()
            if not enhancer.is_available:
                return

            doc = fitz.open(self._filepath)
            enhancer.enhance_blocks(doc, self._blocks_copy)
            doc.close()

            enhanced = [
                (b.id, b.content) for b in self._blocks_copy
                if b.metadata.get("enhanced_by") == "pymupdf4llm"
            ]
            if enhanced:
                self.finished_signal.emit(enhanced)
        except Exception:
            pass


class _FormulaImportPlanThread(QThread):
    """Persist import-time formula jobs without blocking first-page interaction."""

    finished_signal = Signal(dict)

    def __init__(
        self,
        *,
        store: object,
        filepath: str,
        doc_hash: str,
        page_count: int,
        plan_blocks: list[DocumentBlock],
        plan_priority_pages: set[int],
        plan_scan_round: str,
        formula_blocks: list[DocumentBlock],
    ) -> None:
        super().__init__()
        self._store = store
        self._filepath = filepath
        self._doc_hash = doc_hash
        self._page_count = int(page_count)
        self._plan_blocks = [block.model_copy(deep=True) for block in plan_blocks]
        self._plan_priority_pages = set(plan_priority_pages)
        self._plan_scan_round = str(plan_scan_round)
        self._formula_blocks = [block.model_copy(deep=True) for block in formula_blocks]

    def run(self) -> None:
        from src.app.formula_index_store import FormulaScanRound

        result: dict[str, int | str] = {
            "doc_hash": self._doc_hash,
            "queued_blocks": 0,
            "queued_pages": 0,
            "queued_reviews": 0,
            "queued_graph": 0,
        }
        if self.isInterruptionRequested():
            self.finished_signal.emit(result)
            return
        try:
            result["queued_blocks"] = self._store.enqueue_blocks(
                self._doc_hash,
                self._filepath,
                self._plan_blocks,
                self._plan_priority_pages,
                scan_round=self._plan_scan_round,
            )
            if self.isInterruptionRequested():
                self.finished_signal.emit(result)
                return
            result["queued_pages"] = self._store.enqueue_pages(
                self._doc_hash,
                self._filepath,
                range(max(0, self._page_count)),
                scan_round=FormulaScanRound.PDF_STRUCTURE,
            )
            if self.isInterruptionRequested():
                self.finished_signal.emit(result)
                return
            semantic_payloads = {
                block.id: {
                    "stage": FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value,
                    "input_hash": self._store.content_hash(block),
                    "content_hash": self._store.content_hash(block),
                    "model": "pending_semantic_review",
                    "model_version": "pending_semantic_review",
                }
                for block in self._formula_blocks
            }
            result["queued_reviews"] = self._store.enqueue_round_records(
                self._doc_hash,
                self._filepath,
                FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
                "block",
                self._formula_blocks,
                result_json_by_target=semantic_payloads,
            )
            graph_payloads = {
                block.id: {
                    "stage": FormulaScanRound.KNOWLEDGE_GRAPH.value,
                    "input_hash": self._store.content_hash(block),
                    "content_hash": self._store.content_hash(block),
                    "extractor": "structural_graph_v1",
                    "model_version": "structural_graph_v1",
                }
                for block in self._formula_blocks
                if str(block.content or "").strip()
            }
            graph_blocks = [
                block for block in self._formula_blocks
                if str(block.content or "").strip()
            ]
            result["queued_graph"] = self._store.enqueue_round_records(
                self._doc_hash,
                self._filepath,
                FormulaScanRound.KNOWLEDGE_GRAPH,
                "block",
                graph_blocks,
                result_json_by_target=graph_payloads,
            )
        except Exception as exc:
            result["error"] = repr(exc)
        self.finished_signal.emit(result)



"""
主窗口 —— PDF AI Reader 应用的主界面。

MainWindow: 管理全局布局（菜单栏/工具栏/状态栏/中央阅读区/侧边栏），
负责连接 UI 信号到 Core 服务，协调所有子组件。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QProgressBar,
    QStatusBar,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
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
from src.ui.pdf_viewer import PdfViewer
from src.ui.split_widget import SplitWidget
from src.ui.theme import apply_theme


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
        )

        # 概念解释流程协调器（借鉴 Mad Professor AIManager 模式）
        from src.app.explain_flow import ExplainFlow
        self._explain_flow = ExplainFlow(self._ai_engine, self._doc_engine, self._ai_cache)

        # 当前文档状态
        self._current_doc_hash: str = ""
        self._current_blocks: list[DocumentBlock] = []

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
        from PySide6.QtWidgets import QLineEdit
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("搜索文档...")
        self._search_box.setMaximumWidth(200)
        toolbar.addWidget(self._search_box)

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

        # 右侧：AI 工具集（占位，后续完整实现）
        self._right_dock = QDockWidget("AI 工具集", self)
        self._right_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        right_widget = QWidget()
        right_label = QLabel("AI 工具集\n\n打开文档后：\n- 显示章节摘要\n- 专业术语列表\n- AI 推荐问题")
        right_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_label.setStyleSheet("color: #888; padding: 16px;")
        from PySide6.QtWidgets import QVBoxLayout
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(right_label)
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

        # KnowledgeEngine 信号
        self._knowledge_engine.build_progress.connect(self._on_kb_progress)
        self._knowledge_engine.build_finished.connect(self._on_kb_finished)
        self._knowledge_engine.build_error.connect(self._on_kb_error)

        # PdfViewer → 内部处理
        self._pdf_viewer.block_double_clicked.connect(self._on_block_double_clicked)
        self._pdf_viewer.block_translate_requested.connect(self._on_block_translate)
        self._pdf_viewer.block_question_requested.connect(self._on_block_question)
        self._pdf_viewer.block_explain_requested.connect(self._on_block_explain)
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
        self._ai_engine.answer_token.connect(self._on_answer_token)
        self._ai_engine.answer_finished.connect(self._on_answer_finished)
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

        self._current_blocks = result.blocks
        self._current_doc_hash = self._document_flow.current_hash
        self._status_progress.setVisible(False)
        self._status_page_label.setText(
            f"{result.title or Path(result.filepath).name} — {result.page_count} 页"
        )
        self.setWindowTitle(f"{result.title or Path(result.filepath).name} — PDF AI Reader")

        # 加载到 PdfViewer
        QApplication.processEvents()
        self._pdf_viewer.load_document(result)
        QApplication.processEvents()

        # 加载目录
        if result.toc:
            self._navigator.load_toc(result.toc)
        else:
            self._navigator.generate_toc_from_blocks(result.blocks)

        # 状态栏
        render_engine = "PyMuPDF"
        if self._knowledge_engine.check_exists(self._current_doc_hash):
            self._status_model_label.setText(f"📚 知识库就绪 | 🖥️ {render_engine}")
        else:
            self._status_model_label.setText(f"🔨 构建中... | 🖥️ {render_engine}")

        elapsed = time.time() - start
        self.logger.info("文档加载完成，耗时 %.2fs", elapsed)

        # PyMuPDF4LLM 异步增强
        QTimer.singleShot(2000, lambda: self._run_pymupdf4llm_enhance(result))

    def _on_parse_progress(self, current: int, total: int) -> None:
        """解析进度更新。"""
        self._status_progress.setMaximum(total)
        self._status_progress.setValue(current)

    def _on_parse_error(self, message: str) -> None:
        """解析出错。"""
        self._status_progress.setVisible(False)
        self._status_page_label.setText("解析失败")
        QMessageBox.warning(self, "打开失败", message)

    def _on_formula_blocks_updated(self, updated: list[dict]) -> None:
        """MFD/MFR 精扫完成：更新块类型、LaTeX 内容并刷新 overlay。"""
        update_map = {u["id"]: u for u in updated}
        for block in self._current_blocks:
            if block.id in update_map:
                info = update_map[block.id]
                block.block_type = BlockType(info["block_type"])
                block.metadata.update(info.get("metadata", {}))
                # MFR 阶段：用识别到的 LaTeX 替换公式块内容
                if "content" in info:
                    block.content = info["content"]
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

    # =========================================================================
    # KnowledgeEngine 回调
    # =========================================================================

    def _on_kb_progress(self, current: int, total: int) -> None:
        """知识库构建进度。"""
        self._status_progress.setVisible(True)
        self._status_progress.setMaximum(total)
        self._status_progress.setValue(current)

    def _on_kb_finished(self, doc_hash: str) -> None:
        """知识库构建完成。"""
        self._status_progress.setVisible(False)
        render_engine = "QtPdf" if self._doc_engine.using_qtpdf else "PyMuPDF"
        self._status_model_label.setText(f"📚 知识库就绪 | 🖥️ {render_engine}")

    def _on_kb_error(self, message: str) -> None:
        """知识库构建失败。"""
        self._status_progress.setVisible(False)
        self._status_model_label.setText("⚠️ 知识库构建失败")
        self.logger.warning("知识库构建失败: %s", message)

    def _on_build_knowledge_base(self) -> None:
        """手动触发知识库构建/重建。"""
        if not self._current_blocks:
            QMessageBox.information(self, "提示", "请先打开一个 PDF 文件。")
            return
        self._status_model_label.setText("🔨 正在重建知识库...")
        self._knowledge_engine.build_knowledge_base(
            self._current_blocks, self._current_doc_hash, force_rebuild=True
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
        """裂缝中提交问题 → 调用 AI 问答。"""
        block = self._find_block(block_id)
        retrieved = []
        if self._current_doc_hash and self._knowledge_engine.check_exists(self._current_doc_hash):
            try:
                retrieved_raw = self._knowledge_engine.retrieve(
                    question, self._current_doc_hash, top_k=3,
                    exclude_ids=[block_id] if block else None,
                )
                retrieved = [
                    self._find_block(r["id"]) for r in retrieved_raw
                    if self._find_block(r["id"])
                ]
            except Exception:
                pass

        split = self._pdf_viewer.find_split_widget(block_id)
        chat_history = split.chat_history if split else None

        self._ai_engine.request_answer(
            question=question,
            current_block=block,
            retrieved_blocks=retrieved,
            chat_history=chat_history,
            split_id=block_id,
        )

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
        split = self._pdf_viewer.find_split_widget(split_id)
        if split:
            split.display_full_answer(full_answer)

    def _on_translation_error(self, message: str, block_id: str) -> None:
        """翻译出错。"""
        self.logger.error("翻译失败 block=%s: %s", block_id, message)
        split = self._pdf_viewer.find_split_widget(block_id)
        if split:
            split.show_error(f"翻译失败: {message}")

    # =========================================================================
    # AI 问答回调
    # =========================================================================

    def _on_answer_token(self, token: str, split_id: str) -> None:
        """问答流式 token。"""
        split = self._pdf_viewer.find_split_widget(split_id)
        if split:
            split.display_answer_stream(token)

    def _on_answer_error(self, message: str, split_id: str) -> None:
        """问答出错。"""
        self.logger.error("问答失败 split=%s: %s", split_id, message)
        split = self._pdf_viewer.find_split_widget(split_id)
        if split:
            split.show_error(f"回答失败: {message}")

    def _on_split_closed(self, block_id: str) -> None:
        """裂缝关闭后的清理。"""
        pass  # 目前保留裂缝实例以便重新打开时恢复缓存

    # =========================================================================
    # 导航
    # =========================================================================

    def _on_toc_ready(self, toc: list[dict]) -> None:
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
        providers = ["deepseek/deepseek-chat", "openai/gpt-4o", "qwen/qwen-plus",
                      "glm-4", "moonshot-v1-8k", "claude-sonnet-4-20250514"]
        provider_box.addItems(providers)
        # 选中当前配置
        current_cloud = self._config.model.cloud
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
                cm.update({"model": {"cloud": provider}, "api_keys": {provider: api_key}})
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

    def _on_about(self) -> None:
        """关于对话框。"""
        QMessageBox.about(
            self, "关于 PDF AI Reader",
            "PDF AI Reader v0.5 (原型版)\n\n"
            "面向专业数理论文的 AI 辅助阅读与翻译工具。\n\n"
            "技术栈: Python 3.13 + PySide6 + PyMuPDF + Ollama\n"
            "本地模型: Qwen3.5:4b\n"
            "嵌入模型: BGE-M3",
        )

    def _run_pymupdf4llm_enhance(self, result) -> None:
        """启动 QThread 运行 PyMuPDF4LLM 增强解析，完成后通过 Signal 回到主线程。"""
        # 仅拷贝非图片块，避免跨线程读写 UI 持有的原对象
        text_blocks = [b for b in result.blocks if b.block_type != BlockType.IMAGE]
        thread = _PyMuPDF4LLMThread(result.filepath, text_blocks)
        thread.finished_signal.connect(self._on_pymupdf4llm_finished)
        thread.finished.connect(lambda t=thread: self._ai_engine._active_threads.remove(t) if t in self._ai_engine._active_threads else None)
        self._ai_engine._active_threads.append(thread)
        thread.start()

    def _on_pymupdf4llm_finished(self, enhanced_data: list[tuple[str, str]]) -> None:
        """主线程槽函数：安全更新 block.content。"""
        update_map = dict(enhanced_data)
        for block in self._current_blocks:
            if block.id in update_map:
                block.content = update_map[block.id]
                block.metadata["enhanced_by"] = "pymupdf4llm"
        self.logger.info("PyMuPDF4LLM 增强完成: %d 个块", len(enhanced_data))


    def _check_first_launch(self) -> None:
        """首次启动检查：验证本地模型状态。"""
        model_status = self._ai_engine.check_local_model_status()

        if not model_status["ollama_available"]:
            self._status_model_label.setText("⚠️ Ollama 服务未连接")

            reply = QMessageBox.question(
                self, "首次启动",
                "未检测到 Ollama 服务。\n\n"
                "Ollama 是运行本地 AI 模型所必需的后台服务。\n"
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
        for block in self._current_blocks:
            if block.id == block_id:
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



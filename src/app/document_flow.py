"""
文档生命周期协调器 — 借鉴 Mad Professor DataManager 模式。

职责：
  1. 打开/关闭文档（委托 DocumentEngine）
  2. 解析完成 → 构建知识库 + 更新术语表
  3. 通过 Qt 信号通知 UI 状态变化
  4. 关闭时停止所有活跃 AI 线程

Usage:
    flow = DocumentFlow(doc_engine, knowledge_engine, ai_engine, glossary_manager)
    flow.document_opened.connect(ui.on_document_opened)
    flow.open_document("/path/to/file.pdf")
"""

import logging

from PySide6.QtCore import QObject, Signal

_logger = logging.getLogger(__name__)


class DocumentFlow(QObject):
    """文档生命周期协调器。

    借鉴 Mad Professor 的 DataManager：
    - 管理后台线程生命周期（解析线程、AI 线程）
    - 信号驱动 UI 更新
    - 状态自管理（当前文档哈希、知识库检查）
    """

    # Signals
    document_opened = Signal(object)   # ParseResult
    document_closing = Signal()        # emitted before the PDF engine releases the document
    document_closed = Signal()
    parse_progress = Signal(int, int)   # (current, total)
    parse_error = Signal(str)

    def __init__(
        self,
        doc_engine: object,         # DocumentEngine
        knowledge_engine: object,   # KnowledgeEngine
        ai_engine: object,          # AIEngine
        glossary_manager: object,   # GlossaryManager
        graph_index_flow: object | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._doc_engine = doc_engine
        self._knowledge_engine = knowledge_engine
        self._ai_engine = ai_engine
        self._glossary_manager = glossary_manager
        self._graph_index_flow = graph_index_flow
        self._current_hash: str = ""

        # 连接 DocumentEngine 信号（借鉴 DataManager 连接 Pipeline 信号模式）
        self._doc_engine.parse_finished.connect(self._on_parse_finished)
        self._doc_engine.parse_completed.connect(self._on_parse_completed)
        self._doc_engine.parse_progress.connect(self.parse_progress)
        self._doc_engine.parse_error.connect(self.parse_error)

        _logger.info("DocumentFlow: 初始化完成")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_hash(self) -> str:
        return self._current_hash

    def open_document(self, filepath: str) -> None:
        """打开 PDF 文件。如有旧文档先关闭。"""
        _logger.info("DocumentFlow: open_document(%s)", filepath)
        if self._current_hash:
            self.close_document()
        self._doc_engine.open_document(filepath)

    def close_document(self) -> None:
        """关闭当前文档，停止所有活跃线程。"""
        _logger.info("DocumentFlow: close_document START")
        self.document_closing.emit()

        # 停止所有 AI 线程（借鉴 Mad Professor 的线程生命周期管理）
        for t in list(self._ai_engine._active_threads):
            if t.isRunning():
                t.requestInterruption()
                t.quit()
                t.wait(1000)
        self._ai_engine._active_threads.clear()

        self._doc_engine.close_document()
        self._current_hash = ""
        self.document_closed.emit()
        _logger.info("DocumentFlow: close_document END")

    # ------------------------------------------------------------------
    # DocumentEngine 回调
    # ------------------------------------------------------------------

    def _on_parse_finished(self, result: object) -> None:
        """首批页面解析完成 → 立即通知 UI。"""
        _logger.info("DocumentFlow: 首批解析完成 %s (%d pages, %d blocks)",
                     result.filepath, result.page_count, len(result.blocks))

        from src.infra.file_hash import compute_sha256
        self._current_hash = compute_sha256(result.filepath)[:16]

        # 更新术语表
        self._ai_engine.translation_service.update_glossary(
            self._glossary_manager.get_entries(["math", "cs_ml", "physics"])
        )

        self.document_opened.emit(result)

    def _on_parse_completed(self, result: object) -> None:
        """全量解析完成 → 构建知识库。"""
        _logger.info("DocumentFlow: 全量解析完成 %s (%d blocks)",
                     result.filepath, len(result.blocks))
        if not self._current_hash:
            return

        # 知识库构建（借鉴 DataManager._add_paper_vector_store 模式）
        if not self._knowledge_engine.check_exists(self._current_hash):
            _logger.info("DocumentFlow: 构建知识库...")
            self._knowledge_engine.build_knowledge_base(
                result.blocks, self._current_hash
            )

        graph_flow = self._graph_index_flow
        if graph_flow is not None and getattr(graph_flow, "enabled", False):
            started = graph_flow.enqueue_document(
                result.filepath,
                self._current_hash,
                result.blocks,
            )
            if started:
                _logger.info("DocumentFlow: 图谱索引后台任务已启动")

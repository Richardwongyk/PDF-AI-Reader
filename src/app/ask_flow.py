"""
问答流程协调器 — 借鉴 Mad Professor AIManager 模式。

职责：
  1. 知识库语义检索（查 ChromaDB）
  2. 委托 AIEngine 生成答案

Usage:
    flow = AskQuestionFlow(ai_engine, knowledge_engine)
    flow.set_doc_hash(doc_hash)
    flow.request_answer(question, block, block_id, chat_history, find_block_cb)
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject

_logger = logging.getLogger(__name__)


class AskQuestionFlow(QObject):
    """问答流程协调器。

    借鉴 Mad Professor AIManager + DataManager 模式：
    - 从 KnowledgeEngine 检索相关块
    - 委托 AIEngine 生成答案（信号已由 MainWindow 直连）
    """

    def __init__(
        self,
        ai_engine: object,
        knowledge_engine: object,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ai_engine = ai_engine
        self._knowledge_engine = knowledge_engine
        self._current_doc_hash: str = ""
        _logger.info("AskQuestionFlow: 初始化完成")

    def set_doc_hash(self, doc_hash: str) -> None:
        """更新当前文档哈希（打开新文档时调用）。"""
        self._current_doc_hash = doc_hash

    def request_answer(
        self,
        question: str,
        block: object | None,
        block_id: str,
        chat_history: list[dict[str, str]] | None,
        find_block_cb,
    ) -> None:
        """执行知识库检索 + 委托 AIEngine 生成答案。

        Args:
            question: 用户问题。
            block: 当前段落的 DocumentBlock。
            block_id: 段落 ID。
            chat_history: 多轮对话历史。
            find_block_cb: 根据 block_id 查找 DocumentBlock 的回调。
        """
        retrieved: list = []
        if self._current_doc_hash and self._knowledge_engine.check_exists(self._current_doc_hash):
            try:
                retrieved_raw = self._knowledge_engine.retrieve(
                    question, self._current_doc_hash, top_k=3,
                    exclude_ids=[block_id] if block else None,
                )
                retrieved = [
                    find_block_cb(r["id"]) for r in retrieved_raw
                    if find_block_cb(r["id"])
                ]
                _logger.info("AskQuestionFlow: 检索到 %d 个相关块", len(retrieved))
            except Exception:
                _logger.warning("AskQuestionFlow: 检索失败", exc_info=True)

        self._ai_engine.request_answer(
            question=question,
            current_block=block,
            retrieved_blocks=retrieved,
            chat_history=chat_history,
            split_id=block_id,
        )

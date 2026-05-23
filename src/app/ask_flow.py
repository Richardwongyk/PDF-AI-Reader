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

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from src.core.models import DocumentBlock

_logger = logging.getLogger(__name__)


class AskQuestionFlow(QObject):
    """问答流程协调器。

    借鉴 Mad Professor AIManager + DataManager 模式：
    - 从 KnowledgeEngine 检索相关块
    - 委托 AIEngine 生成答案（信号已由 MainWindow 直连）
    """

    answer_unavailable = Signal(str, str)  # (message, block_id)
    retrieval_ready = Signal(str, list)  # (block_id, evidence list[dict])

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
        find_block_cb: Callable[[str], DocumentBlock | None],
    ) -> None:
        """执行知识库检索 + 委托 AIEngine 生成答案。

        Args:
            question: 用户问题。
            block: 当前段落的 DocumentBlock。
            block_id: 段落 ID。
            chat_history: 多轮对话历史。
            find_block_cb: 根据 block_id 查找 DocumentBlock 的回调。
        """
        retrieved: list[DocumentBlock] = []
        if not self._current_doc_hash:
            self.answer_unavailable.emit("当前文档尚未建立知识库上下文，请等待解析完成后再提问。", block_id)
            return

        if not self._knowledge_engine.check_exists(self._current_doc_hash):
            self.answer_unavailable.emit("知识库还在构建中，稍后再问才能基于全文回答。", block_id)
            return

        if self._current_doc_hash:
            try:
                top_k = 8 if block is None else 3
                retrieved_raw = self._knowledge_engine.retrieve(
                    question, self._current_doc_hash, top_k=top_k,
                    exclude_ids=[block_id] if block else None,
                )
                retrieved = []
                evidence = []
                for result in retrieved_raw:
                    found = find_block_cb(result["id"])
                    if not found:
                        continue
                    retrieved.append(found)
                    evidence.append({
                        "id": found.id,
                        "page": found.page_num + 1,
                        "type": found.block_type.value,
                        "distance": float(result.get("distance", 0.0)),
                        "retrieval_score": float(result.get("retrieval_score", 0.0)),
                        "lexical_score": float(result.get("lexical_score", 0.0)),
                        "vector_score": float(result.get("vector_score", 0.0)),
                        "content": found.content,
                    })
                self.retrieval_ready.emit(block_id, evidence)
                _logger.info("AskQuestionFlow: 检索到 %d 个相关块", len(retrieved))
            except Exception:
                _logger.warning("AskQuestionFlow: 检索失败", exc_info=True)

        if block is None and not retrieved:
            self.answer_unavailable.emit("知识库中没有检索到可引用片段，无法按文档依据回答这个问题。", block_id)
            return

        self._ai_engine.request_answer(
            question=question,
            current_block=block,
            retrieved_blocks=retrieved,
            chat_history=chat_history,
            split_id=block_id,
        )

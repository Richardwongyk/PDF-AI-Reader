"""
翻译流程协调器 — 借鉴 Mad Professor AIManager 模式。

职责：
  1. 接收 UI 翻译请求
  2. 查 AICache（命中直接返回，跳过 LLM 调用）
  3. 委托 AIEngine 执行翻译（未命中）
  4. 翻译完成自动存入 AICache
  5. 通过 Qt 信号通知 UI 结果

Usage:
    flow = TranslationFlow(ai_engine, ai_cache)
    flow.translation_ready.connect(ui.on_translation_ready)
    flow.request_translation(block, doc_hash)
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

_logger = logging.getLogger(__name__)


class TranslationFlow(QObject):
    """翻译流程协调器。

    借鉴 Mad Professor 的 AIManager + AIResponseThread 模式：
    - AIManager 持有 AI 聊天实例，管理线程生命周期
    - TranslationFlow 持有 AIEngine + AICache，管理缓存 + 委托
    """

    translation_ready = Signal(str, str)   # (full_text, block_id)
    translation_error = Signal(str, str)   # (error_msg, block_id)

    def __init__(
        self,
        ai_engine: object,      # AIEngine
        ai_cache: object,       # AICache
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ai_engine = ai_engine
        self._ai_cache = ai_cache
        self._pending: dict[str, str] = {}  # block_id → doc_hash

        # 连接 AIEngine 信号
        self._ai_engine.translation_finished.connect(self._on_translation_finished)
        self._ai_engine.translation_error.connect(self._on_translation_error)
        _logger.info("TranslationFlow: 初始化完成")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_translation(self, block: object, doc_hash: str = "") -> bool:
        """请求翻译指定段落。

        Returns:
            True  = AICache 命中，结果已通过 translation_ready 信号发送
            False = 已委托 AIEngine，等待异步回调
        """
        block_id = block.id

        # 1. 查 AICache（借鉴 Mad Professor 的 RAG 缓存模式）
        if doc_hash:
            cached = self._ai_cache.get(block_id, doc_hash, "translation")
            if cached:
                _logger.info("TranslationFlow: AICache HIT %s → 直接返回 (%d chars)",
                             block_id, len(cached))
                self.translation_ready.emit(cached, block_id)
                return True

        # 2. 记录待处理请求（用于回调时关联 doc_hash）
        if doc_hash:
            self._pending[block_id] = doc_hash

        # 3. 委托 AIEngine（借鉴 Mad Professor AIResponseThread.start()）
        _logger.info("TranslationFlow: 委托 AIEngine 翻译 %s", block_id)
        self._ai_engine.request_translation(block)
        return False

    # ------------------------------------------------------------------
    # AIEngine 信号回调（借鉴 Mad Professor _on_ai_sentence_ready 模式）
    # ------------------------------------------------------------------

    def _on_translation_finished(self, full_text: str, block_id: str) -> None:
        """AI 翻译完成 → 存入 AICache → 通知 UI。"""
        # 存入缓存
        doc_hash = self._pending.pop(block_id, "")
        if doc_hash and full_text:
            self._ai_cache.put(block_id, doc_hash, "translation", full_text, model="cloud")

        # 通知 UI
        self.translation_ready.emit(full_text, block_id)

    def _on_translation_error(self, error_msg: str, block_id: str) -> None:
        """AI 翻译出错 → 清理 pending → 通知 UI。"""
        self._pending.pop(block_id, None)
        self.translation_error.emit(error_msg, block_id)

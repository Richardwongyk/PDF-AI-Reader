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

import logging
import re

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
        self._pending: dict[str, tuple[str, str]] = {}  # block_id → (doc_hash, content_hash)
        self._pending_blocks: dict[str, object] = {}

        # 连接 AIEngine 信号
        self._ai_engine.translation_finished.connect(self._on_translation_finished)
        self._ai_engine.translation_error.connect(self._on_translation_error)
        _logger.info("TranslationFlow: 初始化完成")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_translation(
        self,
        block: object,
        doc_hash: str = "",
        *,
        force_refresh: bool = False,
    ) -> bool:
        """请求翻译指定段落。

        Returns:
            True  = AICache 命中，结果已通过 translation_ready 信号发送
            False = 已委托 AIEngine，等待异步回调
        """
        block_id = block.id
        content = getattr(block, "content", "") or ""
        if self._is_passthrough_content(content):
            self.translation_ready.emit(content, block_id)
            return True

        content_hash = self._ai_cache.hash_text(content)

        # 1. 查 AICache（借鉴 Mad Professor 的 RAG 缓存模式）
        if doc_hash and not force_refresh:
            cached = self._ai_cache.get(
                block_id, doc_hash, "translation", content_hash
            )
            if cached:
                _logger.info("TranslationFlow: AICache HIT %s → 直接返回 (%d chars)",
                             block_id, len(cached))
                self.translation_ready.emit(cached, block_id)
                return True
        elif force_refresh:
            _logger.info("TranslationFlow: force-refresh %s → 跳过 AICache 读取", block_id)

        # 2. 防止重复请求（借鉴 Mad Professor 去重检查）
        if block_id in self._pending:
            _logger.info("TranslationFlow: block=%s 已有进行中请求，跳过", block_id)
            return False

        # 3. 记录待处理请求（用于回调时关联 doc_hash）
        if doc_hash:
            self._pending[block_id] = (doc_hash, content_hash)
            self._pending_blocks[block_id] = block

        # 4. 委托 AIEngine（借鉴 Mad Professor AIResponseThread.start()）
        _logger.info("TranslationFlow: 委托 AIEngine 翻译 %s", block_id)
        self._ai_engine.request_translation(block)
        return False

    # ------------------------------------------------------------------
    # AIEngine 信号回调（借鉴 Mad Professor _on_ai_sentence_ready 模式）
    # ------------------------------------------------------------------

    def _on_translation_finished(self, full_text: str, block_id: str) -> None:
        """AI 翻译完成 → 存入 AICache → 通知 UI。"""
        # 存入缓存
        doc_hash, content_hash = self._pending.pop(block_id, ("", ""))
        block = self._pending_blocks.pop(block_id, None)
        if block is not None and content_hash:
            current_hash = self._ai_cache.hash_text(
                getattr(block, "content", "") or ""
            )
            if current_hash != content_hash:
                _logger.info(
                    "TranslationFlow: 内容已更新，但保留本次翻译结果 %s（%s -> %s）",
                    block_id, content_hash[:12], current_hash[:12],
                )
        if doc_hash and full_text:
            self._ai_cache.put(
                block_id, doc_hash, "translation", full_text,
                model="cloud", content_hash=content_hash,
            )

        # 通知 UI
        self.translation_ready.emit(full_text, block_id)

    def _on_translation_error(self, error_msg: str, block_id: str) -> None:
        """AI 翻译出错 → 清理 pending → 通知 UI。"""
        self._pending.pop(block_id, None)
        self._pending_blocks.pop(block_id, None)
        self.translation_error.emit(error_msg, block_id)

    @staticmethod
    def _is_passthrough_content(text: str) -> bool:
        """纯公式、页眉页脚、编号类短文本不调用云端，直接显示原文。"""
        stripped = text.strip()
        if not stripped:
            return True
        if len(stripped) <= 3:
            return True
        alpha = len(re.findall(r"[A-Za-z]", stripped))
        cjk = len(re.findall(r"[\u4e00-\u9fff]", stripped))
        if cjk > 0:
            return True
        mathish = len(re.findall(r"[=+\-*/^_{}\\()[\]<>∑∫√≤≥≈∞]", stripped))
        if len(stripped) <= 80 and alpha <= 8 and mathish >= max(1, len(stripped) // 5):
            return True
        return False

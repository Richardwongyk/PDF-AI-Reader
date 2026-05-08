"""
概念解释流程协调器 — 借鉴 Mad Professor AIManager 模式。

职责：
  1. 公式块 → OCR 提取 LaTeX → 构建解释问题
  2. 文字块 → 直接构建解释问题
  3. OCR 线程管理（_OnDemandOcrThread 生命周期）
  4. AICache 缓存检查（解释结果复用）

Usage:
    flow = ExplainFlow(ai_engine, doc_engine, ai_cache)
    flow.explanation_ready.connect(ui.on_explanation_ready)
    flow.request_explanation(block, doc_hash)
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal

_logger = logging.getLogger(__name__)


class _OnDemandOcrThread(QThread):
    """VIP 公式 OCR 线程（从 MainWindow 迁移至此）。"""
    finished_signal = Signal(str, str)  # (latex, error)

    def __init__(self, filepath: str, block: object) -> None:
        super().__init__()
        self._filepath = filepath
        self._block = block

    def run(self) -> None:
        try:
            from src.core.math_ocr import MathOCR
            import fitz

            ocr = MathOCR()
            if not ocr.is_available:
                self.finished_signal.emit("", "Pix2Text(MathOCR) 未安装")
                return

            doc = fitz.open(self._filepath)
            page = doc[self._block.page_num]
            bbox = self._block.bbox
            zoom = max(min(768 / (bbox[2] - bbox[0]), 96 / (bbox[3] - bbox[1])), 1.5)
            zoom = min(zoom, 4.0)
            mat = fitz.Matrix(zoom, zoom)

            pad = 8
            clip = fitz.Rect(
                max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                bbox[2] + pad, bbox[3] + pad,
            )
            pix = page.get_pixmap(matrix=mat, clip=clip, colorspace=fitz.csRGB)
            img_bytes = pix.tobytes("png")

            latex = ocr.recognize(img_bytes)
            doc.close()
            if latex:
                self.finished_signal.emit(latex, "")
            else:
                self.finished_signal.emit("", "OCR 未能识别公式")
        except Exception as e:
            self.finished_signal.emit("", str(e))


class ExplainFlow(QObject):
    """概念解释流程协调器。

    借鉴 Mad Professor AIManager：
    - 管理 OCR 线程生命周期
    - 公式拦截 → OCR → 构建问题
    - AICache 集成
    """

    question_ready = Signal(str, str)   # (question, block_id)
    error_occurred = Signal(str, str)   # (error, block_id)

    def __init__(
        self,
        ai_engine: object,
        doc_engine: object,
        ai_cache: object | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._ai_engine = ai_engine
        self._doc_engine = doc_engine
        self._ai_cache = ai_cache
        _logger.info("ExplainFlow: 初始化完成")

    def request_explanation(self, block: object, split: object) -> None:
        """请求解释概念。

        公式块 → OCR 提取 LaTeX → 构建解释问题
        文字块 → 直接构建解释问题
        """
        from src.core.models import BlockType, TaskType

        if block.block_type == BlockType.FORMULA and not block.metadata.get("mfr_recognized"):
            split.display_answer_stream("⏳ 正在启动视觉 AI 提取高精度公式，请稍候 1~2 秒...\n\n")
            doc = self._doc_engine.document
            if doc is None:
                split.show_error("文档未打开")
                return
            thread = _OnDemandOcrThread(doc.name, block)
            thread.finished_signal.connect(
                lambda latex, err, b=block, s=split:
                    self._on_ocr_finished(latex, err, b, s)
            )
            thread.finished.connect(
                lambda t=thread:
                    self._ai_engine._active_threads.remove(t)
                    if t in self._ai_engine._active_threads else None
            )
            self._ai_engine._active_threads.append(thread)
            thread.start()
            return

        question = f"请解释这个概念的含义：{block.content[:100]}"
        self.question_ready.emit(question, block.id)

    def _on_ocr_finished(self, latex: str, err: str, block: object, split: object) -> None:
        """OCR 完成 → 更新 block 内容 → 构建问题。"""
        if latex:
            block.content = f"$$\n{latex}\n$$"
            block.metadata["latex"] = latex
            block.metadata["mfr_recognized"] = True
            split.clear()
            question = (
                f"请详细解释这个公式的物理/数学含义，并说明各个符号代表什么：\n"
                f"$$\n{latex}\n$$"
            )
            self.question_ready.emit(question, block.id)
        else:
            split.show_error(f"公式提取失败: {err}")
            self.error_occurred.emit(err, block.id)

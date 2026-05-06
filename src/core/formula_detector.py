"""
公式检测器 —— 可插拔接口，支持多种公式识别方案。

当前实现：改进的启发式检测（基于字体 + LaTeX 模式 + Unicode 数学符号）
未来可接入：Pix2Text / Texo / UniRec 等 ML 模型
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import fitz

from src.core.models import BlockType, DocumentBlock


class FormulaDetector(ABC):
    """公式检测器抽象接口。

    所有公式检测方案（启发式、Pix2Text、Texo 等）实现此接口。
    """

    @abstractmethod
    def detect(self, doc: fitz.Document) -> list[dict[str, Any]]:
        """检测文档中所有公式。

        Args:
            doc: PyMuPDF 文档对象。

        Returns:
            公式列表，每项 {"page": int, "bbox": (x0,y0,x1,y1), "latex": str|None, "score": float}
        """
        ...

    @abstractmethod
    def name(self) -> str:
        """检测器名称。"""
        ...


class Pix2TextMFDDetector(FormulaDetector):
    """Pix2Text MFD（纯公式检测，ONNX Runtime，无需 PyTorch）。

    使用 Pix2Text 内置的 MathFormulaDetector。
    只做 bbox 检测，不做 LaTeX 识别（速度快，~1.5s/页）。
    """

    def __init__(self, dpi: int = 200) -> None:
        self._dpi = dpi
        self._mfd = None

    def name(self) -> str:
        return "pix2text-mfd"

    def _get_mfd(self):
        if self._mfd is None:
            from pix2text.formula_detector import MathFormulaDetector
            self._mfd = MathFormulaDetector()
        return self._mfd

    def detect(self, doc: fitz.Document) -> list[dict[str, Any]]:
        """检测文档中所有页面的公式（完整接口实现）。"""
        return self.detect_specific_pages(doc, list(range(doc.page_count)))

    def detect_specific_pages(
        self, doc: fitz.Document, page_nums: list[int]
    ) -> list[dict[str, Any]]:
        """只对指定页面执行 MFD 检测。

        Args:
            doc: PyMuPDF 文档对象。
            page_nums: 需要检测的页码列表。

        Returns:
            公式列表。
        """
        import io, logging
        from PySide6.QtCore import QThread
        logger = logging.getLogger("Pix2TextMFD")
        mfd = self._get_mfd()
        formulas: list[dict[str, Any]] = []
        for page_num in page_nums:
            if QThread.currentThread().isInterruptionRequested():
                break
            page = doc[page_num]
            pix = page.get_pixmap(dpi=self._dpi)
            # 使用内存字节流，避免磁盘 I/O
            img_bytes = pix.tobytes("png")
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(img_bytes))
                iw, ih = img.size
                results = mfd.detect(img)
                page_w = page.rect.width
                page_h = page.rect.height
                scale_x = page_w / iw
                scale_y = page_h / ih
                for r in results:
                    box = r.get('box', [])
                    if hasattr(box, 'shape') and len(getattr(box, 'shape', [])) == 2:
                        pts = [[float(v) for v in row] for row in box]
                        xs = [p[0] for p in pts]
                        ys = [p[1] for p in pts]
                        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                    else:
                        flat = [float(x) for x in box]
                        x0, y0, x1, y1 = flat[0], flat[1], flat[2], flat[3]
                    formulas.append({
                        "page": page_num,
                        "bbox": (x0 * scale_x, y0 * scale_y,
                                 x1 * scale_x, y1 * scale_y),
                        "latex": None,
                        "score": float(r.get('score', 0)),
                    })
                logger.info("Page %d/%d: %d formulas", page_num + 1, doc.page_count, len(results))
            except Exception as e:
                logger.warning("Page %d failed: %s", page_num, e)
        return formulas

    def _page_has_formulas(self, blocks: list[DocumentBlock], page_num: int) -> bool:
        """判断页面是否可能包含公式（LaTeX 命令、数学 Unicode、低英文比例）。"""
        page_blocks = [b for b in blocks
                       if b.page_num == page_num
                       and b.block_type in (BlockType.PARAGRAPH, BlockType.FORMULA)]
        if not page_blocks:
            return False
        for b in page_blocks:
            text = b.content
            # LaTeX 命令
            for pat in (r'\frac', r'\sum', r'\int', r'\sqrt', r'\alpha', r'\beta',
                        r'\theta', r'\mathbf', r'\mathcal', r'\begin', r'\end',
                        r'\\(', r'\\[', r'\langle', r'\rangle'):
                if pat in text:
                    return True
            # 多单字符 token + 少英文 = 数学表达式
            tokens = text.split()
            english = sum(1 for w in tokens if w.isalpha() and len(w) > 2)
            single = sum(1 for w in tokens if len(w) == 1)
            if single >= 3 and english <= 3 and len(text) > 15:
                return True
            # 高比例数学 Unicode
            math = sum(1 for c in text if ord(c) > 0x2000 and ord(c) < 0x2B00)
            if len(text) > 0 and math / len(text) > 0.1:
                return True
        return False

    def apply_to_blocks(self, blocks: list[DocumentBlock], doc: fitz.Document) -> list[DocumentBlock]:
        # 对所有页面跑 MFD 视觉检测（Pix2Text YOLO11 模型，~1.5s/页）
        import logging
        logger = logging.getLogger("Pix2TextMFD")
        candidate_pages = list(range(doc.page_count))
        logger.info("MFD 视觉检测: %d 页", len(candidate_pages))
        formulas = self.detect_specific_pages(doc, candidate_pages)
        matched = 0
        for block in blocks:
            if block.block_type != BlockType.PARAGRAPH:
                continue
            for f in formulas:
                if f["page"] != block.page_num:
                    continue
                fb = f["bbox"]
                ox, oy = max(block.bbox[0], fb[0]), max(block.bbox[1], fb[1])
                ox2, oy2 = min(block.bbox[2], fb[2]), min(block.bbox[3], fb[3])
                if ox < ox2 and oy < oy2:
                    overlap = (ox2 - ox) * (oy2 - oy)
                    block_area = (block.bbox[2] - block.bbox[0]) * (block.bbox[3] - block.bbox[1])
                    if block_area > 0 and overlap / block_area > 0.3:
                        block.block_type = BlockType.FORMULA
                        block.metadata["formula_detector"] = "pix2text-mfd"
                        block.metadata["formula_score"] = f["score"]
                        matched += 1
                        break
        logger.info("共标注 %d 个公式块", matched)
        return blocks

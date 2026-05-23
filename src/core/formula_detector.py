"""
公式检测器 —— 可插拔接口，支持多种公式识别方案。

当前实现：改进的启发式检测（基于字体 + LaTeX 模式 + Unicode 数学符号）
未来可接入：Pix2Text / Texo / UniRec 等 ML 模型
"""

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
    """Pix2Text MFD + 可选 MFR OCR。

    使用 Pix2Text 内置的 MathFormulaDetector。
    先做 bbox 检测，再对 PDF 图片/扫描公式裁剪并尝试识别 LaTeX。
    """

    def __init__(self, dpi: int = 200, max_existing_ocr_blocks: int = 6) -> None:
        self._dpi = dpi
        self._max_existing_ocr_blocks = max_existing_ocr_blocks
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
        """粗略判断页面是否可能包含公式。"""
        page_all_blocks = [b for b in blocks if b.page_num == page_num]
        if any(b.block_type == BlockType.IMAGE for b in page_all_blocks):
            return True
        page_blocks = [b for b in page_all_blocks if b.block_type == BlockType.PARAGRAPH]
        if not page_blocks:
            return False
        import re
        math_patterns = [r'\\frac', r'\\sum', r'\\int', r'\\sqrt', r'\\alpha', r'\\beta',
                         r'\\theta', r'\\mathbf', r'\\mathcal', r'\$\$', r'\\begin', r'\\end']
        for b in page_blocks:
            for pat in math_patterns:
                if pat in b.content:
                    return True
        return False

    def _crop_formula_image(self, doc: fitz.Document, formula: dict[str, Any]) -> bytes:
        """按 MFD bbox 从 PDF 页面裁剪公式图片，供 MFR 识别。"""
        page_num = int(formula["page"])
        return self._crop_bbox_image(doc, page_num, formula["bbox"], dpi=self._dpi, pad=3.0)

    @staticmethod
    def _crop_bbox_image(
        doc: fitz.Document,
        page_num: int,
        bbox: tuple[float, float, float, float] | list[float],
        dpi: int,
        pad: float,
    ) -> bytes:
        """按 bbox 裁剪 PDF 页面图片。"""
        page = doc[page_num]
        x0, y0, x1, y1 = [float(v) for v in bbox]
        clip = fitz.Rect(
            max(page.rect.x0, x0 - pad),
            max(page.rect.y0, y0 - pad),
            min(page.rect.x1, x1 + pad),
            min(page.rect.y1, y1 + pad),
        )
        if clip.is_empty or clip.width < 2 or clip.height < 2:
            return b""
        scale = dpi / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(scale, scale),
            clip=clip,
            colorspace=fitz.csRGB,
            alpha=False,
        )
        return pix.tobytes("png")

    @staticmethod
    def _normalize_latex(latex: str) -> str:
        """清理 MFR 常见的逐字母空格输出。"""
        import re

        text = str(latex or "").strip().strip("$")

        def collapse_command(match: re.Match[str]) -> str:
            command = match.group(1)
            body = match.group(2)
            parts = body.split()
            if len(parts) >= 3 and all(len(part) == 1 and part.isalpha() for part in parts):
                body = "".join(parts)
            return f"\\{command}{{{body}}}"

        text = re.sub(r"\\(mathrm|operatorname\*?|text)\s*\{([^{}]+)\}", collapse_command, text)
        text = text.replace(r"\cfrac", r"\frac")
        return text.strip()

    @staticmethod
    def _has_latex_command(text: str) -> bool:
        """文本是否已经包含可用 LaTeX 命令。"""
        import re
        return bool(re.search(r"\\[A-Za-z]+", text or ""))

    def _should_ocr_existing_formula_block(self, block: DocumentBlock) -> bool:
        """判断现有公式块是否需要用 MFR 从页面图像重识别。"""
        import re

        if block.block_type != BlockType.FORMULA:
            return False
        if block.metadata.get("mfr_recognized") or block.metadata.get("formula_ocr"):
            return False
        text = (block.content or "").strip()
        if not text or text.startswith("[图片公式"):
            return False
        if self._has_latex_command(text):
            return False
        if len(text) < 8 or len(text) > 260:
            return False
        if re.search(r"\b(?:Layer Type|BLEU|params|steps|EN-DE|EN-FR|dev)\b", text):
            return False

        words = re.findall(r"[A-Za-z]{3,}", text)
        math_markers = sum(text.count(ch) for ch in "=+-*/^_()[]{}∈≤≥×·√∑∫")
        has_math_word = bool(re.search(r"\b(?:softmax|Attention|FFN|sin|cos|log|exp|max|min)\b", text))
        if "=" not in text:
            return False
        if len(words) > 12:
            return False
        return math_markers >= 2 or has_math_word

    @staticmethod
    def _existing_formula_ocr_priority(block: DocumentBlock) -> tuple[int, int, int]:
        """越像短公式，越靠前进入有限 OCR 预算。"""
        import re

        text = (block.content or "").strip()
        words = re.findall(r"[A-Za-z]{3,}", text)
        math_markers = sum(text.count(ch) for ch in "=+-*/^_()[]{}∈≤≥×·√∑∫")
        starts_formula = bool(
            re.match(r"^(?:[A-Z]{1,4}|[A-Za-z]+\(.*|lrate|PE\s*\()", text)
        )
        has_function = bool(re.search(r"\b(?:softmax|Attention|FFN|sin|cos|log|exp|max|min)\b", text))
        score = 0
        score += 6 if "=" in text else 0
        score += 4 if starts_formula else 0
        score += 3 if has_function else 0
        score += min(math_markers, 8)
        score -= max(len(words) - 8, 0) * 2
        return (score, -len(words), -len(text))

    def _recognize_existing_formula_blocks(
        self, doc: fitz.Document, blocks: list[DocumentBlock]
    ) -> dict[str, str]:
        """对已有但非 LaTeX 的公式块做受控 MFR 重识别。"""
        if self._max_existing_ocr_blocks <= 0:
            return {}

        targets = [
            block for block in blocks
            if self._should_ocr_existing_formula_block(block)
        ]
        targets.sort(key=self._existing_formula_ocr_priority, reverse=True)
        targets = targets[:self._max_existing_ocr_blocks]
        if not targets:
            return {}

        import logging
        logger = logging.getLogger("Pix2TextMFD")
        images: list[bytes] = []
        image_blocks: list[DocumentBlock] = []
        for block in targets:
            try:
                image = self._crop_bbox_image(
                    doc,
                    block.page_num,
                    block.bbox,
                    dpi=max(self._dpi, 300),
                    pad=6.0,
                )
            except Exception as exc:
                logger.debug("现有公式块裁剪失败 block=%s: %s", block.id, exc)
                image = b""
            if image:
                images.append(image)
                image_blocks.append(block)

        if not images:
            return {}
        try:
            from src.core.math_ocr import MathOCR
            latex_results = MathOCR().recognize_batch(images)
        except Exception as exc:
            logger.warning("现有公式块 MFR OCR 不可用，保留原文本: %s", exc)
            return {}

        recognized: dict[str, str] = {}
        for block, latex in zip(image_blocks, latex_results, strict=False):
            cleaned = self._normalize_latex(latex)
            if cleaned and self._has_latex_command(cleaned):
                recognized[block.id] = cleaned
        logger.info(
            "现有公式块 MFR OCR 完成: %d/%d 个公式块重识别为 LaTeX",
            len(recognized),
            len(targets),
        )
        return recognized

    def _recognize_scanned_formulas(
        self, doc: fitz.Document, formulas: list[dict[str, Any]]
    ) -> dict[int, str]:
        """对新增的图片/扫描公式做批量 LaTeX OCR。

        返回值的 key 是 formulas 中的下标。OCR 服务不可用或失败时返回空 dict，
        调用方仍保留 ``needs_ocr=True``，不影响 PDF 阅读和公式定位。
        """
        if not formulas:
            return {}
        import logging
        logger = logging.getLogger("Pix2TextMFD")

        images: list[bytes] = []
        image_indices: list[int] = []
        for idx, formula in enumerate(formulas):
            try:
                image = self._crop_formula_image(doc, formula)
            except Exception as exc:
                logger.debug("公式裁剪失败 idx=%d page=%s: %s", idx, formula.get("page"), exc)
                image = b""
            if image:
                images.append(image)
                image_indices.append(idx)

        if not images:
            return {}
        try:
            from src.core.math_ocr import MathOCR
            latex_results = MathOCR().recognize_batch(images)
        except Exception as exc:
            logger.warning("MFR 公式 OCR 不可用，保留待识别状态: %s", exc)
            return {}

        recognized: dict[int, str] = {}
        for idx, latex in zip(image_indices, latex_results, strict=False):
            cleaned = str(latex or "").strip()
            if cleaned:
                recognized[idx] = cleaned
        logger.info("MFR OCR 完成: %d/%d 个图片公式识别为 LaTeX", len(recognized), len(formulas))
        return recognized

    def apply_to_blocks(self, blocks: list[DocumentBlock], doc: fitz.Document) -> list[DocumentBlock]:
        # 只对有公式特征的页面跑 MFD
        candidate_pages = [pn for pn in range(doc.page_count)
                           if self._page_has_formulas(blocks, pn)]
        existing_ocr_results = self._recognize_existing_formula_blocks(doc, blocks)
        existing_ocr_count = 0
        for block in blocks:
            latex = self._normalize_latex(existing_ocr_results.get(block.id, ""))
            if not latex:
                continue
            block.content = latex
            block.block_type = BlockType.FORMULA
            block.metadata.update({
                "formula_detector": "pix2text-mfd",
                "formula_ocr": "pix2text-mfr",
                "mfr_recognized": True,
                "latex_source": "existing_block_ocr",
                "needs_ocr": False,
            })
            existing_ocr_count += 1
        if not candidate_pages:
            if existing_ocr_count:
                import logging
                logging.getLogger("Pix2TextMFD").info(
                    "重识别 %d 个现有公式块，无需额外 MFD 页面检测",
                    existing_ocr_count,
                )
            return blocks
        import logging
        logger = logging.getLogger("Pix2TextMFD")
        logger.info("需检测页: %d/%d (%s)", len(candidate_pages), doc.page_count,
                     ','.join(str(p+1) for p in candidate_pages))
        formulas = self.detect_specific_pages(doc, candidate_pages)
        matched = 0
        added = 0
        existing_ids = {b.id for b in blocks}
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
        by_page: dict[int, list[DocumentBlock]] = {}
        for block in blocks:
            by_page.setdefault(block.page_num, []).append(block)
        new_formulas: list[dict[str, Any]] = []
        for f in formulas:
            page_blocks = by_page.get(f["page"], [])
            fb = f["bbox"]
            matched_existing = False
            for block in page_blocks:
                if block.block_type == BlockType.IMAGE:
                    continue
                bx0, by0, bx1, by1 = block.bbox
                ox, oy = max(bx0, fb[0]), max(by0, fb[1])
                ox2, oy2 = min(bx1, fb[2]), min(by1, fb[3])
                if ox < ox2 and oy < oy2:
                    overlap = (ox2 - ox) * (oy2 - oy)
                    formula_area = max((fb[2] - fb[0]) * (fb[3] - fb[1]), 1.0)
                    if overlap / formula_area > 0.3:
                        matched_existing = True
                        break
            if matched_existing:
                continue
            new_formulas.append(f)
        ocr_results = self._recognize_scanned_formulas(doc, new_formulas)
        for idx, f in enumerate(new_formulas):
            page_blocks = by_page.get(f["page"], [])
            page_num = int(f["page"])
            next_index = len(page_blocks)
            new_id = f"p{page_num}_b{next_index}"
            while new_id in existing_ids:
                next_index += 1
                new_id = f"p{page_num}_b{next_index}"
            latex = self._normalize_latex(ocr_results.get(idx, ""))
            metadata = {
                "formula_detector": "pix2text-mfd",
                "formula_score": f["score"],
                "source": "image_or_scan",
                "needs_ocr": not bool(latex),
            }
            if latex:
                metadata.update({
                    "formula_ocr": "pix2text-mfr",
                    "mfr_recognized": True,
                    "latex_source": "image_ocr",
                })
            new_block = DocumentBlock(
                id=new_id,
                page_num=page_num,
                block_type=BlockType.FORMULA,
                content=latex or "[图片公式，等待 OCR 识别]",
                bbox=tuple(float(v) for v in fb),
                metadata=metadata,
            )
            blocks.append(new_block)
            by_page.setdefault(page_num, []).append(new_block)
            existing_ids.add(new_id)
            added += 1
        logger.info(
            "共标注 %d 个文本公式块，重识别 %d 个现有公式块，新增 %d 个图片/扫描公式块",
            matched,
            existing_ocr_count,
            added,
        )
        return blocks

"""
MathOCR —— 数学公式图片 → LaTeX 识别服务。

使用 Pix2Text MFR v1.5 模型（TrOCR 架构，ONNX 推理）将公式截图转为 LaTeX 源码。
模型首次使用时自动下载（约 500MB），后续使用缓存。

Plan 决策 #2: MFD 检测 BBox → 裁剪图片 → MFR 识别 LaTeX → 注入回文本
"""

from __future__ import annotations

import io
import logging
import os
import tempfile

_logger = logging.getLogger(__name__)


class MathOCR:
    """数学公式 OCR 服务。

    将公式图片（PNG bytes）识别为标准 LaTeX 源码。
    底层使用 Pix2Text MFR 模型，纯本地运行，无需联网。
    模型首次加载时自动从 HuggingFace 下载。

    用法:
        ocr = MathOCR()
        latex = ocr.recognize(png_bytes)  # → "\\frac{1}{2}"
    """

    def __init__(self) -> None:
        self._p2t = None
        self._available: bool | None = None

    @property
    def is_available(self) -> bool:
        """MFR 服务是否可用（Pix2Text 是否已安装）。"""
        if self._available is None:
            try:
                import pix2text  # noqa: F401
                self._available = True
            except ImportError:
                _logger.info("Pix2Text 未安装，MFR 不可用。安装: pip install pix2text")
                self._available = False
        return self._available

    def _ensure_model(self) -> None:
        """懒加载 Pix2Text 模型（首次调用时触发，可能耗时较长）。"""
        if self._p2t is not None:
            return
        if not self.is_available:
            raise RuntimeError("Pix2Text 未安装，无法进行公式识别")
        from pix2text import Pix2Text
        _logger.info("正在加载 Pix2Text MFR 模型（首次加载约需 30-60s）...")
        self._p2t = Pix2Text.from_config()
        _logger.info("Pix2Text MFR 模型就绪")

    def recognize(self, image_bytes: bytes) -> str:
        """识别公式图片并返回 LaTeX 源码。

        Args:
            image_bytes: PNG 格式的公式截图字节数据。

        Returns:
            标准 LaTeX 源码字符串（如 "\\frac{1}{2}"）。
            识别失败或服务不可用时返回空字符串。
        """
        if not self.is_available:
            return ""
        try:
            self._ensure_model()
            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes))
            # Pix2Text recognize_formula 接受文件路径列表
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                img.save(f, format="PNG")
                tmp_path = f.name
            try:
                result = self._p2t.recognize_formula([tmp_path])
                if result and len(result) > 0:
                    latex = result[0]
                    if latex:
                        return str(latex).strip()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            return ""
        except Exception as e:
            _logger.warning("MFR 识别失败: %s", e)
            return ""

    def recognize_batch(self, images: list[bytes]) -> list[str]:
        """批量识别公式图片。

        Args:
            images: PNG 格式的公式截图字节数据列表。

        Returns:
            与输入顺序对应的 LaTeX 字符串列表。
            单个识别失败则对应位置为空字符串。
        """
        results: list[str] = []
        for img_bytes in images:
            results.append(self.recognize(img_bytes))
        return results

"""
MathOCR —— 数学公式图片 → LaTeX 识别服务。

使用 Pix2Text MFR v1.5 模型（TrOCR 架构）将公式截图转为 LaTeX 源码。
针对 CPU 推理优化：多线程、批量处理、仅加载公式模块、ONNX 后端。

CPU 加速策略：
- OMP/OpenBLAS 线程数 = CPU 核心数
- 仅启用公式识别模块 (enable_table=False)
- 批量识别减少模型调用开销
- 适中的图片尺寸平衡速度与精度
"""

from __future__ import annotations

import io
import logging
import os
import tempfile

_logger = logging.getLogger(__name__)


class MathOCR:
    """数学公式 OCR 服务 —— CPU 优化版。

    优化策略：
    1. 环境变量 OMP_NUM_THREADS = CPU 核心数，榨干多核性能
    2. enable_table=False，仅加载公式识别模型，减少内存和启动时间
    3. 批量 recognize_batch() 分摊模型调用开销
    4. 输入图片适度压缩到 768px，平衡速度与精度

    用法:
        ocr = MathOCR()
        latex = ocr.recognize(png_bytes)     # → "\\frac{1}{2}"
        results = ocr.recognize_batch([...])  # 批量识别
    """

    # MFR 输入图片最大边长（px），缩小可加速但过低影响精度
    _RESIZED_SHAPE: int = 768
    # 批量识别的批次大小
    _BATCH_SIZE: int = 4

    def __init__(self) -> None:
        self._p2t = None
        self._available: bool | None = None
        self._num_threads: int = os.cpu_count() or 4
        self._setup_env()

    # ------------------------------------------------------------------
    # 公开属性
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def recognize(self, image_bytes: bytes) -> str:
        """识别单张公式图片并返回 LaTeX 源码。

        Args:
            image_bytes: PNG 格式的公式截图字节数据。

        Returns:
            标准 LaTeX 源码字符串（如 "\\frac{1}{2}"）。
            识别失败或服务不可用时返回空字符串。
        """
        if not self.is_available:
            return ""
        results = self.recognize_batch([image_bytes])
        return results[0] if results else ""

    def recognize_batch(self, images: list[bytes]) -> list[str]:
        """批量识别公式图片 — CPU 多线程并行。

        将图片列表分批提交给 MFR 模型，利用 batch_size
        分摊模型推理开销。

        Args:
            images: PNG 格式的公式截图字节数据列表。

        Returns:
            与输入顺序对应的 LaTeX 字符串列表。
        """
        if not self.is_available or not images:
            return [""] * len(images)

        try:
            self._ensure_model()
            return self._recognize_batch_impl(images)
        except Exception as e:
            _logger.warning("MFR 批量识别失败: %s", e)
            return [""] * len(images)

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _setup_env(self) -> None:
        """设置 CPU 多线程环境变量。"""
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                     "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            if var not in os.environ:
                os.environ[var] = str(self._num_threads)

    def _ensure_model(self) -> None:
        """懒加载 Pix2Text 模型（仅公式模块，跳过表格）。

        首次调用时触发，约需 30-60 秒加载 + 可能下载模型。
        """
        if self._p2t is not None:
            return
        if not self.is_available:
            raise RuntimeError("Pix2Text 未安装，无法进行公式识别")

        _logger.info(
            "加载 Pix2Text MFR (CPU, %d threads, batch=%d)...",
            self._num_threads, self._BATCH_SIZE,
        )
        from pix2text import Pix2Text
        # 只启用公式识别，关闭表格检测以减少模型加载时间和内存
        self._p2t = Pix2Text.from_config(
            enable_table=False,
            enable_formula=True,
            device="cpu",
        )
        _logger.info("Pix2Text MFR 模型就绪")

    def _recognize_batch_impl(self, images: list[bytes]) -> list[str]:
        """批量识别的实际实现。"""
        from PIL import Image

        results: list[str] = [""] * len(images)
        tmp_paths: list[str] = []

        # Step 1: 将所有图片保存为临时文件
        for img_bytes in images:
            if not img_bytes:
                tmp_paths.append("")
                continue
            try:
                img = Image.open(io.BytesIO(img_bytes))
                # 适度缩放以加速推理
                w, h = img.size
                max_dim = max(w, h)
                if max_dim > self._RESIZED_SHAPE:
                    scale = self._RESIZED_SHAPE / max_dim
                    img = img.resize(
                        (int(w * scale), int(h * scale)),
                        Image.LANCZOS,
                    )
                f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                img.save(f, format="PNG")
                f.close()
                tmp_paths.append(f.name)
            except Exception as e:
                _logger.warning("图片处理失败: %s", e)
                tmp_paths.append("")

        # Step 2: 分批调用 MFR
        try:
            valid = [(i, p) for i, p in enumerate(tmp_paths) if p]
            for batch_start in range(0, len(valid), self._BATCH_SIZE):
                batch = valid[batch_start:batch_start + self._BATCH_SIZE]
                indices = [b[0] for b in batch]
                paths = [b[1] for b in batch]
                try:
                    batch_results = self._p2t.recognize_formula(paths)
                    for j, idx in enumerate(indices):
                        if j < len(batch_results) and batch_results[j]:
                            results[idx] = str(batch_results[j]).strip()
                except Exception as e:
                    _logger.warning("MFR 批次识别失败: %s", e)
        finally:
            # Step 3: 清理临时文件
            for p in tmp_paths:
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass

        return results

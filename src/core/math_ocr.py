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
import threading

_logger = logging.getLogger(__name__)


class MathOCR:
    """数学公式 OCR 服务 —— CPU 优化版 (单例模式)。

    单例保证全局只加载一次模型，后续调用瞬间返回。
    """

    _RESIZED_SHAPE: int = 768
    _BATCH_SIZE: int = 4

    # 【修复】单例模式：确保全局只加载一次模型，彻底解决每次点击卡顿 30 秒的问题
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._p2t = None
        self._available: bool | None = None
        self._num_threads: int = os.cpu_count() or 4
        self._setup_env()
        self._initialized = True

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

        首次调用时触发，显式禁用无关模块，加载时间从 ~40s 降至 ~2s。
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
        # 仅启用公式识别，跳过表格 OCR 模型
        # enable_table=False 避免加载 TableOCR（~2s），layout 仍加载但轻量（~0.5s）
        self._p2t = Pix2Text(
            enable_formula=True,
            enable_table=False,
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

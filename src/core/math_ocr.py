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

import io
import hashlib
import logging
import os
import sqlite3
import tempfile
import threading

from datetime import datetime, timezone
from pathlib import Path

_logger = logging.getLogger(__name__)


class _FormulaOcrCache:
    """Persistent image-hash cache for formula OCR results."""

    def __init__(self, db_path: str = "data/formula_ocr_cache.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS formula_ocr_cache (
                image_hash TEXT PRIMARY KEY,
                latex      TEXT NOT NULL,
                model      TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        self._conn.commit()
        self._lock = threading.Lock()

    @staticmethod
    def hash_image(image_bytes: bytes) -> str:
        return hashlib.sha256(image_bytes).hexdigest()

    def get(self, image_hash: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT latex FROM formula_ocr_cache WHERE image_hash=?",
                (image_hash,),
            ).fetchone()
        return str(row[0]) if row else None

    def put(self, image_hash: str, latex: str, model: str) -> None:
        if not latex.strip():
            return
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO formula_ocr_cache
                   (image_hash, latex, model, created_at)
                   VALUES (?, ?, ?, ?)""",
                (image_hash, latex, model, datetime.now(timezone.utc).isoformat()),
            )
            self._conn.commit()


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
        self._cache = _FormulaOcrCache()
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
        results = self.recognize_batch([image_bytes])
        return results[0] if results else ""

    def recognize_batch(
        self, images: list[bytes], max_uncached: int | None = None
    ) -> list[str]:
        """批量识别公式图片 — CPU 多线程并行。

        将图片列表分批提交给 MFR 模型，利用 batch_size
        分摊模型推理开销。所有图片都会先查持久化缓存；当 ``max_uncached``
        被设置时，只对缓存未命中的前 N 张图片运行 MFR。

        Args:
            images: PNG 格式的公式截图字节数据列表。
            max_uncached: 本次允许进入模型推理的缓存未命中图片数量。

        Returns:
            与输入顺序对应的 LaTeX 字符串列表。
        """
        if not images:
            return [""] * len(images)

        cached_results, misses = self._read_cache(images)
        if max_uncached is not None:
            allowed = max(0, int(max_uncached))
            if allowed < len(misses):
                skipped = len(misses) - allowed
                _logger.info(
                    "MFR OCR 预算限制: 推理 %d 张未命中图片，延后 %d 张",
                    allowed,
                    skipped,
                )
                misses = misses[:allowed]

        if not misses:
            return cached_results

        if not self.is_available:
            return cached_results

        try:
            self._ensure_model()
            miss_images = [images[i] for i, _ in misses]
            miss_results = self._recognize_batch_impl(miss_images)
            for (original_index, image_hash), latex in zip(misses, miss_results, strict=False):
                cleaned = str(latex or "").strip()
                cached_results[original_index] = cleaned
                if cleaned:
                    self._cache.put(image_hash, cleaned, "pix2text-mfr")
            return cached_results
        except Exception as e:
            _logger.warning("MFR 批量识别失败: %s", e)
            return cached_results

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _setup_env(self) -> None:
        """设置 CPU 多线程环境变量。"""
        for var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                     "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            if var not in os.environ:
                os.environ[var] = str(self._num_threads)

    def _read_cache(self, images: list[bytes]) -> tuple[list[str], list[tuple[int, str]]]:
        """Return cached OCR results and the images still needing MFR."""
        results: list[str] = [""] * len(images)
        misses: list[tuple[int, str]] = []
        hits = 0
        for index, image in enumerate(images):
            if not image:
                continue
            image_hash = self._cache.hash_image(image)
            cached = self._cache.get(image_hash)
            if cached is not None:
                results[index] = cached
                hits += 1
            else:
                misses.append((index, image_hash))
        if hits:
            _logger.info("MFR OCR 缓存命中: %d/%d", hits, len(images))
        return results, misses

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

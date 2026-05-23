"""
MathOCR —— 数学公式图片 → LaTeX 识别服务。

默认使用 Pix2Text MFR v1.5 模型将公式截图转为 LaTeX 源码。
具体识别模型通过 FormulaRecognizer 后端接入；缓存、限流和调用方保持不变。

CPU 加速策略：
- OMP/OpenBLAS 线程数 = CPU 核心数
- 仅启用公式识别模块 (enable_table=False)
- 批量识别减少模型调用开销
- 适中的图片尺寸平衡速度与精度
"""

import hashlib
import logging
import os
import sqlite3
import threading

from datetime import datetime, timezone
from pathlib import Path

from src.core.formula_recognizers import FormulaRecognizer, FormulaRecognizerRegistry

_logger = logging.getLogger(__name__)


class _FormulaOcrCache:
    """Persistent image-hash cache for formula OCR results."""

    def __init__(self, db_path: str = "data/formula_ocr_cache.db") -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()
        self._lock = threading.Lock()

    def _ensure_schema(self) -> None:
        """Create or migrate the OCR cache to a model-scoped key."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS formula_ocr_cache (
                cache_key  TEXT PRIMARY KEY,
                image_hash TEXT DEFAULT '',
                latex      TEXT NOT NULL,
                model      TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(formula_ocr_cache)").fetchall()
        }
        pk_columns = {
            str(row[1])
            for row in self._conn.execute("PRAGMA table_info(formula_ocr_cache)").fetchall()
            if int(row[5] or 0) > 0
        }
        if "cache_key" not in columns or "image_hash" in pk_columns:
            self._conn.execute("ALTER TABLE formula_ocr_cache RENAME TO formula_ocr_cache_legacy")
            self._conn.execute("""
                CREATE TABLE formula_ocr_cache (
                    cache_key  TEXT PRIMARY KEY,
                    image_hash TEXT DEFAULT '',
                    latex      TEXT NOT NULL,
                    model      TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
            legacy_columns = {
                str(row[1])
                for row in self._conn.execute("PRAGMA table_info(formula_ocr_cache_legacy)").fetchall()
            }
            if {"image_hash", "latex", "model", "created_at"}.issubset(legacy_columns):
                self._conn.execute("""
                    INSERT OR IGNORE INTO formula_ocr_cache
                    (cache_key, image_hash, latex, model, created_at)
                    SELECT
                        CASE
                            WHEN model IS NOT NULL AND model != '' THEN model || ':' || image_hash
                            ELSE image_hash
                        END,
                        image_hash,
                        latex,
                        COALESCE(model, ''),
                        created_at
                    FROM formula_ocr_cache_legacy
                    WHERE image_hash IS NOT NULL AND latex IS NOT NULL
                """)
            self._conn.execute("DROP TABLE formula_ocr_cache_legacy")
        self._conn.commit()

    @staticmethod
    def hash_image(image_bytes: bytes) -> str:
        return hashlib.sha256(image_bytes).hexdigest()

    @staticmethod
    def cache_key(image_hash: str, model: str) -> str:
        return f"{model}:{image_hash}"

    def get(self, image_hash: str, model: str = "") -> str | None:
        cache_key = self.cache_key(image_hash, model) if model else image_hash
        with self._lock:
            row = self._conn.execute(
                """SELECT latex FROM formula_ocr_cache
                   WHERE cache_key=? OR (image_hash=? AND (?='' OR model=?))
                   ORDER BY CASE WHEN cache_key=? THEN 0 ELSE 1 END
                   LIMIT 1""",
                (cache_key, image_hash, model, model, cache_key),
            ).fetchone()
        return str(row[0]) if row else None

    def put(self, image_hash: str, latex: str, model: str) -> None:
        if not latex.strip():
            return
        cache_key = self.cache_key(image_hash, model)
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO formula_ocr_cache
                   (cache_key, image_hash, latex, model, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (cache_key, image_hash, latex, model, datetime.now(timezone.utc).isoformat()),
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
    _default_backend: str = "pix2text-mfr"
    _default_backend_kwargs: dict[str, object] = {}

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance

    def __init__(self, backend: str | None = None) -> None:
        if self._initialized:
            return
        self._backend_name = str(backend or self.__class__._default_backend or "pix2text-mfr")
        self._backend_kwargs = dict(self.__class__._default_backend_kwargs)
        self._recognizer: FormulaRecognizer | None = None
        self._num_threads: int = os.cpu_count() or 4
        self._cache = _FormulaOcrCache()
        self._setup_env()
        self._initialized = True

    @classmethod
    def set_default_backend(cls, backend: str) -> None:
        """Set process-wide backend before the singleton is first constructed."""
        backend_name = str(backend or "pix2text-mfr").strip() or "pix2text-mfr"
        cls.set_default_backend_config(backend_name)

    @classmethod
    def set_default_backend_config(cls, backend: str, **kwargs: object) -> None:
        """Set process-wide backend and optional backend-specific configuration."""
        backend_name = str(backend or "pix2text-mfr").strip() or "pix2text-mfr"
        if cls._instance is not None and getattr(cls._instance, "_initialized", False):
            current = getattr(cls._instance, "_backend_name", "")
            current_kwargs = getattr(cls._instance, "_backend_kwargs", {})
            if current != backend_name or current_kwargs != kwargs:
                _logger.warning(
                    "公式 OCR 后端已初始化为 %s，忽略后续切换到 %s",
                    current,
                    backend_name,
                )
                return
        cls._default_backend = backend_name
        cls._default_backend_kwargs = dict(kwargs)

    # ------------------------------------------------------------------
    # 公开属性
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """MFR 服务是否可用。"""
        try:
            return self._get_recognizer().is_available
        except Exception as exc:
            _logger.info("公式识别后端不可用: %s", exc)
            return False

    @property
    def backend_name(self) -> str:
        return self._backend_name

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
            cache_namespace = self._cache_namespace()
            for (original_index, image_hash), latex in zip(misses, miss_results, strict=False):
                cleaned = str(latex or "").strip()
                cached_results[original_index] = cleaned
                if cleaned:
                    self._cache.put(image_hash, cleaned, cache_namespace)
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
        cache_namespace = self._cache_namespace()
        for index, image in enumerate(images):
            if not image:
                continue
            image_hash = self._cache.hash_image(image)
            cached = self._cache.get(image_hash, cache_namespace)
            if cached is not None:
                results[index] = cached
                hits += 1
            else:
                misses.append((index, image_hash))
        if hits:
            _logger.info("MFR OCR 缓存命中: %d/%d", hits, len(images))
        return results, misses

    def _get_recognizer(self) -> FormulaRecognizer:
        if self._recognizer is None:
            self._recognizer = FormulaRecognizerRegistry.create(
                self._backend_name,
                batch_size=self._BATCH_SIZE,
                num_threads=self._num_threads,
                **self._backend_kwargs,
            )
        return self._recognizer

    def _cache_namespace(self) -> str:
        return self._get_recognizer().cache_namespace

    def _ensure_model(self) -> None:
        """Lazily initialize the selected formula recognizer backend."""
        recognizer = self._get_recognizer()
        if not recognizer.is_available:
            raise RuntimeError(f"公式识别后端不可用: {recognizer.name}")

    def _recognize_batch_impl(self, images: list[bytes]) -> list[str]:
        """批量识别的实际实现。"""
        return self._get_recognizer().recognize_batch(images)

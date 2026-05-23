"""Pluggable formula image recognizers.

The UI, background queue, cache, and knowledge-base writeback should not know
which MFR model is active.  This module keeps that boundary explicit so higher
accuracy backends can be added without changing the hot UI path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class FormulaRecognizer(ABC):
    """Image-to-LaTeX recognizer backend."""

    name: str

    @property
    @abstractmethod
    def is_available(self) -> bool:
        """Whether the backend can run in the current environment."""
        ...

    @abstractmethod
    def recognize_batch(self, images: list[bytes]) -> list[str]:
        """Recognize a batch of cropped formula PNG bytes."""
        ...


class Pix2TextFormulaRecognizer(FormulaRecognizer):
    """Pix2Text MFR backend."""

    name = "pix2text-mfr"

    def __init__(self, batch_size: int = 4, num_threads: int = 4) -> None:
        self._batch_size = max(1, int(batch_size))
        self._num_threads = max(1, int(num_threads))
        self._p2t = None
        self._available: bool | None = None

    @property
    def is_available(self) -> bool:
        if self._available is None:
            try:
                import pix2text  # noqa: F401
                self._available = True
            except ImportError:
                self._available = False
        return self._available

    def recognize_batch(self, images: list[bytes]) -> list[str]:
        import io
        import logging
        import os
        import tempfile

        from PIL import Image

        logger = logging.getLogger(__name__)
        self._ensure_model()
        results: list[str] = [""] * len(images)
        tmp_paths: list[str] = []

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
            except Exception as exc:
                logger.warning("图片处理失败: %s", exc)
                tmp_paths.append("")

        try:
            valid = [(i, p) for i, p in enumerate(tmp_paths) if p]
            for batch_start in range(0, len(valid), self._batch_size):
                batch = valid[batch_start:batch_start + self._batch_size]
                indices = [item[0] for item in batch]
                paths = [item[1] for item in batch]
                try:
                    batch_results = self._p2t.recognize_formula(paths)
                    for offset, idx in enumerate(indices):
                        if offset < len(batch_results) and batch_results[offset]:
                            results[idx] = str(batch_results[offset]).strip()
                except Exception as exc:
                    logger.warning("MFR 批次识别失败: %s", exc)
        finally:
            for path in tmp_paths:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

        return results

    def _ensure_model(self) -> None:
        import logging

        if self._p2t is not None:
            return
        if not self.is_available:
            raise RuntimeError("Pix2Text 未安装，无法进行公式识别")

        logger = logging.getLogger(__name__)
        logger.info(
            "加载 Pix2Text MFR (CPU, %d threads, batch=%d)...",
            self._num_threads,
            self._batch_size,
        )
        from pix2text import Pix2Text

        self._p2t = Pix2Text(
            enable_formula=True,
            enable_table=False,
            device="cpu",
        )
        logger.info("Pix2Text MFR 模型就绪")


class FormulaRecognizerRegistry:
    """Factory registry for OCR backends."""

    _constructors = {
        "pix2text-mfr": Pix2TextFormulaRecognizer,
        "pix2text": Pix2TextFormulaRecognizer,
    }

    @classmethod
    def create(cls, name: str, **kwargs: object) -> FormulaRecognizer:
        key = str(name or "pix2text-mfr").strip().lower()
        constructor = cls._constructors.get(key)
        if constructor is None:
            raise ValueError(f"Unknown formula recognizer backend: {name}")
        return constructor(**kwargs)

    @classmethod
    def available_names(cls) -> list[str]:
        return sorted(cls._constructors)

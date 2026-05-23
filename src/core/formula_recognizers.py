"""Pluggable formula image recognizers.

The UI, background queue, cache, and knowledge-base writeback should not know
which MFR model is active.  This module keeps that boundary explicit so higher
accuracy backends can be added without changing the hot UI path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
import json


class FormulaRecognizer(ABC):
    """Image-to-LaTeX recognizer backend."""

    name: str

    @property
    def cache_namespace(self) -> str:
        """Stable namespace for OCR cache entries produced by this backend."""
        return self.name

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

    def __init__(self, batch_size: int = 4, num_threads: int = 4, **_: object) -> None:
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


class PaddleFormulaRecognizer(FormulaRecognizer):
    """PaddleOCR 3.x formula recognition backend."""

    name = "paddle_formula"
    _PREPROCESS_VERSION = "png-v1"

    _LATEX_KEYS = (
        "rec_formula",
        "rec_text",
        "latex",
        "formula",
        "text",
    )

    def __init__(
        self,
        batch_size: int = 4,
        num_threads: int = 4,
        model_name: str = "PP-FormulaNet_plus-S",
        device: str | None = None,
        enable_hpi: bool = False,
    ) -> None:
        self._batch_size = max(1, int(batch_size))
        self._num_threads = max(1, int(num_threads))
        self._model_name = str(model_name or "PP-FormulaNet_plus-S")
        self._device = device
        self._enable_hpi = bool(enable_hpi)
        self._model = None
        self._available: bool | None = None

    @property
    def cache_namespace(self) -> str:
        return f"{self.name}:{self._model_name}:{self._PREPROCESS_VERSION}"

    @property
    def is_available(self) -> bool:
        if self._available is None:
            try:
                self._formula_cls()
                self._available = True
            except Exception:
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
                logger.warning("Paddle 公式 OCR 图片处理失败: %s", exc)
                tmp_paths.append("")

        try:
            valid = [(i, p) for i, p in enumerate(tmp_paths) if p]
            for batch_start in range(0, len(valid), self._batch_size):
                batch = valid[batch_start:batch_start + self._batch_size]
                indices = [item[0] for item in batch]
                paths = [item[1] for item in batch]
                try:
                    batch_outputs = self._predict_paths(paths)
                    latex_values = self._normalize_outputs(batch_outputs, len(paths))
                    for offset, idx in enumerate(indices):
                        if offset < len(latex_values) and latex_values[offset]:
                            results[idx] = latex_values[offset]
                except Exception as exc:
                    logger.warning("Paddle 公式 OCR 批次识别失败: %s", exc)
        finally:
            for path in tmp_paths:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        pass

        return results

    @classmethod
    def _extract_latex(cls, item: object) -> str:
        if item is None:
            return ""
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, dict):
            for key in cls._LATEX_KEYS:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            for key in ("res", "result", "data", "output", "prediction"):
                value = item.get(key)
                latex = cls._extract_latex(value)
                if latex:
                    return latex
            return ""

        for attr in ("json", "to_json"):
            value = getattr(item, attr, None)
            if value is None:
                continue
            if callable(value):
                try:
                    value = value()
                except TypeError:
                    continue
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except json.JSONDecodeError:
                    pass
            latex = cls._extract_latex(value)
            if latex:
                return latex

        for attr in cls._LATEX_KEYS:
            value = getattr(item, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @classmethod
    def _normalize_outputs(cls, outputs: object, expected: int) -> list[str]:
        if expected <= 0:
            return []
        if isinstance(outputs, dict) or isinstance(outputs, str):
            items: list[object] = [outputs]
        elif isinstance(outputs, Iterable):
            items = list(outputs)
        else:
            items = [outputs]

        if len(items) == 1 and expected > 1 and isinstance(items[0], list):
            items = list(items[0])

        latex_values = [cls._extract_latex(item) for item in items[:expected]]
        if len(latex_values) < expected:
            latex_values.extend([""] * (expected - len(latex_values)))
        return latex_values

    @staticmethod
    def _formula_cls() -> type:
        from paddleocr import FormulaRecognition

        return FormulaRecognition

    def _ensure_model(self) -> None:
        import logging

        if self._model is not None:
            return
        if not self.is_available:
            raise RuntimeError("PaddleOCR FormulaRecognition 未安装，无法进行公式识别")

        logger = logging.getLogger(__name__)
        logger.info(
            "加载 PaddleOCR FormulaRecognition (%s, batch=%d, threads=%d)...",
            self._model_name,
            self._batch_size,
            self._num_threads,
        )
        formula_cls = self._formula_cls()
        kwargs: dict[str, object] = {
            "model_name": self._model_name,
            "enable_hpi": self._enable_hpi,
            "cpu_threads": self._num_threads,
        }
        if self._device:
            kwargs["device"] = self._device
        try:
            self._model = formula_cls(**kwargs)
        except TypeError:
            kwargs.pop("enable_hpi", None)
            kwargs.pop("cpu_threads", None)
            self._model = formula_cls(**kwargs)
        logger.info("PaddleOCR FormulaRecognition 模型就绪")

    def _predict_paths(self, paths: list[str]) -> object:
        predict = getattr(self._model, "predict")
        try:
            return predict(input=paths, batch_size=self._batch_size)
        except TypeError:
            try:
                return predict(paths, batch_size=self._batch_size)
            except TypeError:
                return predict(paths)


class FormulaRecognizerRegistry:
    """Factory registry for OCR backends."""

    _constructors = {
        "paddle": PaddleFormulaRecognizer,
        "paddle_formula": PaddleFormulaRecognizer,
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

"""Pluggable formula image recognizers.

The UI, background queue, cache, and knowledge-base writeback should not know
which MFR model is active.  This module keeps that boundary explicit so higher
accuracy backends can be added without changing the hot UI path.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class FormulaRecognitionResult:
    """Formula recognizer output with optional confidence metadata."""

    latex: str
    score: float | None = None
    raw: object | None = None
    warnings: tuple[str, ...] = ()


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

    def recognize_batch_with_metadata(
        self, images: list[bytes]
    ) -> list[FormulaRecognitionResult]:
        """Recognize formulas and preserve backend confidence when available."""
        return [
            FormulaRecognitionResult(latex=str(latex or "").strip())
            for latex in self.recognize_batch(images)
        ]


class Pix2TextFormulaRecognizer(FormulaRecognizer):
    """Pix2Text MFR backend."""

    name = "pix2text-mfr"
    _LATEX_KEYS = ("text", "latex", "formula", "rec_formula", "rec_text")
    _SCORE_KEYS = ("score", "confidence", "prob", "probability")

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
        return [result.latex for result in self.recognize_batch_with_metadata(images)]

    def recognize_batch_with_metadata(
        self, images: list[bytes]
    ) -> list[FormulaRecognitionResult]:
        import io
        import logging
        import os
        import tempfile

        from PIL import Image

        logger = logging.getLogger(__name__)
        self._ensure_model()
        results: list[FormulaRecognitionResult] = [
            FormulaRecognitionResult(latex="") for _ in images
        ]
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
                    batch_outputs = self._recognize_paths(paths)
                    batch_results = self._normalize_outputs(batch_outputs, len(paths))
                    for offset, idx in enumerate(indices):
                        if offset < len(batch_results):
                            results[idx] = batch_results[offset]
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

    def _recognize_paths(self, paths: list[str]) -> object:
        """Call Pix2Text while preserving score-capable output shapes."""
        recognize_formula = getattr(self._p2t, "recognize_formula")
        try:
            return recognize_formula(
                paths,
                batch_size=self._batch_size,
                return_text=False,
            )
        except TypeError:
            try:
                return recognize_formula(paths, batch_size=self._batch_size)
            except TypeError:
                return recognize_formula(paths)

    @classmethod
    def _normalize_outputs(
        cls, outputs: object, expected: int
    ) -> list[FormulaRecognitionResult]:
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
        results = [cls._extract_result(item) for item in items[:expected]]
        if len(results) < expected:
            results.extend(
                FormulaRecognitionResult(latex="") for _ in range(expected - len(results))
            )
        return results

    @classmethod
    def _extract_result(cls, item: object) -> FormulaRecognitionResult:
        if item is None:
            return FormulaRecognitionResult(latex="")
        if isinstance(item, str):
            return FormulaRecognitionResult(latex=item.strip(), raw=item)
        if isinstance(item, dict):
            latex = ""
            for key in cls._LATEX_KEYS:
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    latex = value.strip()
                    break
            score = cls._extract_score(item)
            for key in ("res", "result", "data", "output", "prediction"):
                if latex:
                    break
                nested = item.get(key)
                nested_result = cls._extract_result(nested)
                latex = nested_result.latex
                if score is None:
                    score = nested_result.score
            return FormulaRecognitionResult(latex=latex, score=score, raw=item)

        for attr in cls._LATEX_KEYS:
            value = getattr(item, attr, None)
            if isinstance(value, str) and value.strip():
                return FormulaRecognitionResult(
                    latex=value.strip(),
                    score=cls._extract_score(item),
                    raw=item,
                )
        return FormulaRecognitionResult(
            latex="",
            score=cls._extract_score(item),
            raw=item,
        )

    @classmethod
    def _extract_score(cls, item: object) -> float | None:
        for key in cls._SCORE_KEYS:
            if isinstance(item, dict):
                value = item.get(key)
            else:
                value = getattr(item, key, None)
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    continue
        return None

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

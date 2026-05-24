"""Process-isolated formula recognition tools.

Heavy MFR/OCR packages live in dedicated conda environments.  The reader's
main Python process only launches small worker subprocesses and records their
candidate outputs for later review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
WORKER_SCRIPT = ROOT / "tools" / "formula_tool_worker.py"


@dataclass(frozen=True)
class ExternalFormulaToolSpec:
    """One external formula tool invocation spec."""

    name: str
    backend: str
    python: str
    model: str = ""
    model_version: str = ""
    preprocess_version: str = "png-v1"
    timeout_sec: int = 240
    env: dict[str, str] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.name and self.backend and self.python)


@dataclass(frozen=True)
class ExternalFormulaCandidate:
    """One candidate result from an external tool."""

    candidate_id: str
    latex: str
    model: str
    model_version: str
    preprocess_version: str
    score: float | None = None
    duration_ms: int = 0
    warnings: tuple[str, ...] = ()
    raw: Any = None


class ExternalFormulaToolRunner:
    """Run external formula tools through a stable JSON subprocess contract."""

    @classmethod
    def default_specs(cls) -> list[ExternalFormulaToolSpec]:
        """Load tool specs from environment variables.

        Supported forms:
        - ``PDF_AI_READER_FORMULA_TOOL_SPECS``: JSON list or path to a JSON file.
        - ``PDF_AI_READER_PADDLE_PYTHON`` / ``PDF_AI_READER_PIX2TEXT_PYTHON``:
          compact shortcuts for local smoke deployments.
        """
        specs: list[ExternalFormulaToolSpec] = []
        raw = os.getenv("PDF_AI_READER_FORMULA_TOOL_SPECS", "").strip()
        if raw:
            try:
                payload: object
                maybe_path = Path(raw)
                if maybe_path.exists():
                    payload = json.loads(maybe_path.read_text(encoding="utf-8"))
                else:
                    payload = json.loads(raw)
                specs.extend(cls._parse_specs(payload))
            except Exception:
                specs = []

        paddle_python = os.getenv("PDF_AI_READER_PADDLE_PYTHON", "").strip()
        if paddle_python:
            specs.append(
                ExternalFormulaToolSpec(
                    name="paddle_formula",
                    backend="paddle_formula",
                    python=paddle_python,
                    model=os.getenv("PDF_AI_READER_PADDLE_MODEL", "PP-FormulaNet_plus-S"),
                    model_version=os.getenv("PDF_AI_READER_PADDLE_VERSION", ""),
                    env={
                        key: value
                        for key, value in {
                            "PADDLE_PDX_CACHE_HOME": os.getenv("PADDLE_PDX_CACHE_HOME", ""),
                            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": os.getenv(
                                "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True"
                            ),
                        }.items()
                        if value
                    },
                )
            )

        pix2text_python = os.getenv("PDF_AI_READER_PIX2TEXT_PYTHON", "").strip()
        if pix2text_python:
            specs.append(
                ExternalFormulaToolSpec(
                    name="pix2text_formula",
                    backend="pix2text_formula",
                    python=pix2text_python,
                    model="pix2text",
                    model_version=os.getenv("PDF_AI_READER_PIX2TEXT_VERSION", ""),
                )
            )

        unique: dict[tuple[str, str, str], ExternalFormulaToolSpec] = {}
        for spec in specs:
            if spec.enabled:
                unique[(spec.name, spec.backend, spec.python)] = spec
        return list(unique.values())

    @staticmethod
    def _parse_specs(payload: object) -> list[ExternalFormulaToolSpec]:
        if isinstance(payload, dict):
            payload = payload.get("tools", [])
        if not isinstance(payload, list):
            return []
        specs: list[ExternalFormulaToolSpec] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            env = item.get("env", {})
            specs.append(
                ExternalFormulaToolSpec(
                    name=str(item.get("name", "") or ""),
                    backend=str(item.get("backend", item.get("name", "")) or ""),
                    python=str(item.get("python", "") or ""),
                    model=str(item.get("model", "") or ""),
                    model_version=str(item.get("model_version", "") or ""),
                    preprocess_version=str(item.get("preprocess_version", "png-v1") or "png-v1"),
                    timeout_sec=int(item.get("timeout_sec", 240) or 240),
                    env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
                )
            )
        return specs

    def recognize_images(
        self,
        images: list[tuple[str, bytes]],
        specs: list[ExternalFormulaToolSpec] | None = None,
    ) -> list[ExternalFormulaCandidate]:
        if not images:
            return []
        active_specs = specs if specs is not None else self.default_specs()
        if not active_specs:
            return []

        candidates: list[ExternalFormulaCandidate] = []
        with tempfile.TemporaryDirectory(prefix="pdf_ai_formula_tools_") as tmp:
            tmp_dir = Path(tmp)
            items = []
            for index, (candidate_id, image_bytes) in enumerate(images):
                if not image_bytes:
                    continue
                path = tmp_dir / f"{index:04d}.png"
                path.write_bytes(image_bytes)
                items.append({"candidate_id": candidate_id, "image_path": str(path)})
            if not items:
                return []

            input_path = tmp_dir / "input.json"
            input_path.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
            for spec in active_specs:
                output_path = tmp_dir / f"{spec.name}_output.json"
                started = time.perf_counter()
                env = os.environ.copy()
                env.update(spec.env)
                cmd = [
                    spec.python,
                    str(WORKER_SCRIPT),
                    "--backend",
                    spec.backend,
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ]
                if spec.model:
                    cmd.extend(["--model", spec.model])
                try:
                    proc = subprocess.run(
                        cmd,
                        cwd=ROOT,
                        env=env,
                        text=True,
                        capture_output=True,
                        timeout=max(1, int(spec.timeout_sec)),
                        check=False,
                    )
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    if proc.returncode != 0:
                        candidates.extend(
                            self._failed_candidates(items, spec, elapsed_ms, proc.stderr or proc.stdout)
                        )
                        continue
                    payload = json.loads(output_path.read_text(encoding="utf-8"))
                    candidates.extend(self._candidates_from_payload(payload, spec, elapsed_ms))
                except Exception as exc:
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    candidates.extend(self._failed_candidates(items, spec, elapsed_ms, str(exc)))
        return candidates

    @staticmethod
    def _failed_candidates(
        items: list[dict[str, object]],
        spec: ExternalFormulaToolSpec,
        elapsed_ms: int,
        error: str,
    ) -> list[ExternalFormulaCandidate]:
        warning = f"tool_failed:{error[:240]}"
        return [
            ExternalFormulaCandidate(
                candidate_id=str(item.get("candidate_id", "")),
                latex="",
                model=spec.name,
                model_version=spec.model_version or spec.model,
                preprocess_version=spec.preprocess_version,
                duration_ms=elapsed_ms,
                warnings=(warning,),
            )
            for item in items
        ]

    @staticmethod
    def _candidates_from_payload(
        payload: object,
        spec: ExternalFormulaToolSpec,
        fallback_elapsed_ms: int,
    ) -> list[ExternalFormulaCandidate]:
        if not isinstance(payload, dict):
            return []
        results = payload.get("results", [])
        if not isinstance(results, list):
            return []
        candidates: list[ExternalFormulaCandidate] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            warnings = item.get("warnings", [])
            if not isinstance(warnings, list):
                warnings = []
            candidates.append(
                ExternalFormulaCandidate(
                    candidate_id=str(item.get("candidate_id", "")),
                    latex=str(item.get("latex", "") or "").strip(),
                    model=str(item.get("model", spec.name) or spec.name),
                    model_version=str(
                        item.get("model_version", spec.model_version or spec.model) or ""
                    ),
                    preprocess_version=str(
                        item.get("preprocess_version", spec.preprocess_version) or spec.preprocess_version
                    ),
                    score=_optional_float(item.get("score")),
                    duration_ms=int(item.get("duration_ms", fallback_elapsed_ms) or fallback_elapsed_ms),
                    warnings=tuple(str(value) for value in warnings if str(value)),
                    raw=item.get("raw"),
                )
            )
        return candidates


def _optional_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None

r"""Smoke test external formula/document parsing tools in the dedicated env.

Run this with the external Python environment, e.g.

    C:\Users\WYK\.conda\envs\pdf_formula_tools_310\python.exe tools\external_formula_tools_smoke.py

It deliberately avoids importing the main app package so the heavy tool
environment can stay process-isolated from the Python 3.14 reader.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "测试资料" / "Attention is all you need.pdf"
DEFAULT_FORMULA_IMAGE = ROOT / "test_artifacts" / "pix2text_smoke" / "attention_formula_p3_b6.png"


@dataclass
class ToolSmokeResult:
    name: str
    available: bool
    status: str
    elapsed_sec: float
    output: str = ""
    error: str = ""


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _paddle_formula_smoke() -> ToolSmokeResult:
    started = time.perf_counter()
    try:
        from paddleocr import FormulaRecognition

        cls_name = f"{FormulaRecognition.__module__}.{FormulaRecognition.__name__}"
        return ToolSmokeResult(
            name="paddle_formula",
            available=True,
            status="import_ok",
            elapsed_sec=round(time.perf_counter() - started, 3),
            output=cls_name,
        )
    except Exception as exc:
        return ToolSmokeResult(
            name="paddle_formula",
            available=False,
            status="import_failed",
            elapsed_sec=round(time.perf_counter() - started, 3),
            error=str(exc),
        )


def _paddle_formula_image_smoke(image: Path) -> ToolSmokeResult:
    started = time.perf_counter()
    try:
        from paddleocr import FormulaRecognition

        model = FormulaRecognition(model_name="PP-FormulaNet_plus-S", device="cpu")
        outputs = list(model.predict(input=str(image), batch_size=1))
        return ToolSmokeResult(
            name="paddle_formula_image",
            available=True,
            status="ok",
            elapsed_sec=round(time.perf_counter() - started, 3),
            output=json.dumps([str(item) for item in outputs], ensure_ascii=False),
        )
    except Exception as exc:
        return ToolSmokeResult(
            name="paddle_formula_image",
            available=False,
            status="failed",
            elapsed_sec=round(time.perf_counter() - started, 3),
            error=str(exc),
        )


def _pix2text_formula_image_smoke(image: Path) -> ToolSmokeResult:
    started = time.perf_counter()
    try:
        from pix2text import Pix2Text

        p2t = Pix2Text(enable_formula=True, enable_table=False, device="cpu")
        output = p2t.recognize_formula(str(image), return_text=False)
        return ToolSmokeResult(
            name="pix2text_formula_image",
            available=True,
            status="ok",
            elapsed_sec=round(time.perf_counter() - started, 3),
            output=str(output),
        )
    except Exception as exc:
        return ToolSmokeResult(
            name="pix2text_formula_image",
            available=False,
            status="failed",
            elapsed_sec=round(time.perf_counter() - started, 3),
            error=str(exc),
        )


def _unimernet_smoke() -> ToolSmokeResult:
    started = time.perf_counter()
    try:
        import unimernet
        import pkgutil

        modules = [m.name for m in pkgutil.iter_modules(unimernet.__path__)][:20]
        return ToolSmokeResult(
            name="unimernet",
            available=True,
            status="import_ok",
            elapsed_sec=round(time.perf_counter() - started, 3),
            output=json.dumps(modules, ensure_ascii=False),
        )
    except Exception as exc:
        return ToolSmokeResult(
            name="unimernet",
            available=False,
            status="import_failed",
            elapsed_sec=round(time.perf_counter() - started, 3),
            error=str(exc),
        )


def _magic_pdf_smoke(pdf: Path, output_dir: Path, run_parse: bool) -> ToolSmokeResult:
    started = time.perf_counter()
    if not _module_available("magic_pdf"):
        return ToolSmokeResult(
            name="magic_pdf",
            available=False,
            status="missing",
            elapsed_sec=0.0,
            error="magic_pdf module not found",
        )
    if not run_parse:
        return ToolSmokeResult(
            name="magic_pdf",
            available=True,
            status="import_ok",
            elapsed_sec=round(time.perf_counter() - started, 3),
        )
    exe = Path(sys.executable).parent / "Scripts" / "magic-pdf.exe"
    if not exe.exists():
        return ToolSmokeResult(
            name="magic_pdf",
            available=True,
            status="cli_missing",
            elapsed_sec=round(time.perf_counter() - started, 3),
            error=str(exe),
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(exe),
        "-p",
        str(pdf),
        "-o",
        str(output_dir),
        "-m",
        "txt",
        "-s",
        "0",
        "-e",
        "1",
    ]
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        status = "ok" if proc.returncode == 0 else "failed"
        return ToolSmokeResult(
            name="magic_pdf",
            available=True,
            status=status,
            elapsed_sec=round(time.perf_counter() - started, 3),
            output=(proc.stdout or "")[-1000:],
            error=(proc.stderr or "")[-1000:],
        )
    except Exception as exc:
        return ToolSmokeResult(
            name="magic_pdf",
            available=True,
            status="exception",
            elapsed_sec=round(time.perf_counter() - started, 3),
            error=str(exc),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", default=str(DEFAULT_PDF))
    parser.add_argument("--formula-image", default=str(DEFAULT_FORMULA_IMAGE))
    parser.add_argument("--output", default="test_artifacts/external_formula_tools_smoke/report.json")
    parser.add_argument(
        "--only",
        action="append",
        choices=["paddle", "pix2text", "unimernet", "magic_pdf"],
        help="Limit checks to one or more tools for isolated env validation.",
    )
    parser.add_argument("--run-formula-image", action="store_true")
    parser.add_argument("--run-magic-pdf", action="store_true")
    args = parser.parse_args()

    pdf = Path(args.pdf)
    formula_image = Path(args.formula_image)
    output = ROOT / args.output
    magic_output = output.parent / "magic_pdf"
    selected = set(args.only or ["paddle", "pix2text", "unimernet", "magic_pdf"])
    results = []
    if "paddle" in selected:
        results.append(_paddle_formula_smoke())
    if "unimernet" in selected:
        results.append(_unimernet_smoke())
    if "magic_pdf" in selected:
        results.append(_magic_pdf_smoke(pdf, magic_output, run_parse=args.run_magic_pdf))
    if args.run_formula_image:
        if formula_image.exists():
            if "paddle" in selected:
                results.append(_paddle_formula_image_smoke(formula_image))
            if "pix2text" in selected:
                results.append(_pix2text_formula_image_smoke(formula_image))
        else:
            results.append(
                ToolSmokeResult(
                    name="formula_image",
                    available=False,
                    status="missing",
                    elapsed_sec=0.0,
                    error=str(formula_image),
                )
            )
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "python": sys.executable,
        "pdf": str(pdf),
        "formula_image": str(formula_image),
        "results": [asdict(result) for result in results],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all(result.available for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

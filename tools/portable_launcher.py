"""Small Windows launcher for the portable release bundle.

The real application runs from the bundled Python runtime under ``runtime``.
Keeping this launcher stdlib-only avoids the PyInstaller/PySide6 DLL loading
issue that affected the direct one-folder build.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path


def _message_box(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "PDF AI Reader", 0x10)
    except Exception:
        pass


def _append_path(env: dict[str, str], *paths: Path) -> None:
    existing = env.get("PATH", "")
    entries = [str(path) for path in paths if path.exists()]
    env["PATH"] = os.pathsep.join(entries + ([existing] if existing else []))


def main() -> int:
    bundle_dir = Path(sys.executable).resolve().parent
    runtime_dir = bundle_dir / "r"
    app_dir = bundle_dir / "a"
    if not runtime_dir.exists():
        runtime_dir = bundle_dir / "runtime"
    if not app_dir.exists():
        app_dir = bundle_dir / "app"
    main_py = app_dir / "src" / "main.py"
    package_dir = runtime_dir / "p"
    if not package_dir.exists():
        package_dir = runtime_dir / "Lib" / "site-packages"

    pythonw = runtime_dir / "pythonw.exe"
    python = runtime_dir / "python.exe"
    if os.environ.get("PDF_AI_READER_LAUNCHER_CONSOLE") == "1":
        interpreter = python
    else:
        interpreter = pythonw if pythonw.exists() else python

    if not interpreter.exists() or not main_py.exists():
        _message_box(
            "PDF AI Reader release package is incomplete.\n\n"
            "Please keep PDF-AI-Reader.exe together with the bundled app and runtime folders."
        )
        return 1

    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("ANONYMIZED_TELEMETRY", "False")
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu-sandbox")
    env["PYTHONPATH"] = os.pathsep.join([str(app_dir), str(package_dir)])
    env["CONDA_PREFIX"] = str(runtime_dir)
    env["CONDA_DEFAULT_ENV"] = "pdf_ai_reader_314"
    env["QT_PLUGIN_PATH"] = str(package_dir / "PySide6" / "plugins")
    env["QML2_IMPORT_PATH"] = str(package_dir / "PySide6" / "qml")
    _append_path(
        env,
        runtime_dir,
        runtime_dir / "Scripts",
        runtime_dir / "DLLs",
        runtime_dir / "Library" / "bin",
        runtime_dir / "b",
        runtime_dir / "Library" / "usr" / "bin",
        runtime_dir / "Library" / "mingw-w64" / "bin",
        package_dir / "PySide6",
        package_dir / "shiboken6",
    )

    command = [str(interpreter), str(main_py), *sys.argv[1:]]
    try:
        process = subprocess.Popen(command, cwd=str(app_dir), env=env)
    except OSError as exc:
        _message_box(f"PDF AI Reader failed to start:\n\n{exc}")
        return 1

    if os.environ.get("PDF_AI_READER_LAUNCHER_WAIT") == "1":
        return int(process.wait())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

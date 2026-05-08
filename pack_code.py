"""
代码打包工具 —— 收集项目 Python 源码和必要资源文件，输出到单一 .txt 文件。

包含: *.py, *.yaml, *.json, *.html, *.css, *.js (KaTeX), *.md
排除: 隐藏文件/目录, data/ 二进制, logs/, 开源借鉴/, API key 配置

用法: python pack_code.py [输出文件名]
      默认输出: 代码打包.txt
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def should_include(filepath: Path, root: Path, output_name: str) -> bool:
    """判断文件是否应被打包。"""
    # 排除自身和输出文件
    if filepath.name in {output_name, Path(__file__).name}:
        return False

    # 排除敏感文件
    if filepath.name in {"config.yaml", "极简设计报告.md", "pdf_viewer_old.py"}:
        return False

    # 相对路径
    try:
        rel = filepath.relative_to(root)
    except ValueError:
        return False

    parts = rel.parts

    # 排除特定顶级目录
    skip_top = {"logs", "开源借鉴", "__pycache__", ".git", ".claude", ".vscode",
                "node_modules", ".pytest_cache", ".mypy_cache"}
    if parts[0] in skip_top:
        return False

    # 排除所有隐藏文件/目录（. 开头）
    for p in parts:
        if p.startswith("."):
            return False

    # data/ 目录仅允许 .py, .json, .yaml, .md（排除 .db, .db-shm, .db-wal 等）
    if len(parts) > 1 and parts[0] == "data":
        if filepath.suffix.lower() not in {".py", ".json", ".yaml", ".yml", ".md"}:
            return False

    # 允许的扩展名
    allowed = {".py", ".yaml", ".yml", ".json", ".md", ".html", ".css", ".js", ".bat"}
    if filepath.suffix.lower() in allowed:
        return True

    # 无扩展名的特殊文件
    if filepath.name in {"requirements.txt", "TODO.md"}:
        return True

    return False


def collect_files(root: Path, output_name: str) -> list[Path]:
    files: list[Path] = []
    for filepath in sorted(root.rglob("*")):
        if filepath.is_file() and should_include(filepath, root, output_name):
            files.append(filepath)
    return files


def read_file_safe(filepath: Path) -> str | None:
    try:
        return filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return filepath.read_text(encoding="gbk")
        except Exception:
            return None


def pack(root: Path, output_name: str) -> tuple[int, int]:
    files = collect_files(root, output_name)
    if not files:
        print("未找到任何可打包的文件。")
        return 0, 0

    output_path = root / output_name
    success, failed, total_size = 0, 0, 0

    with output_path.open("w", encoding="utf-8") as out:
        out.write("=" * 80 + "\n")
        out.write(f"代码打包 — {_timestamp()}\n")
        out.write(f"根目录: {root}\n文件总数: {len(files)}\n")
        out.write("=" * 80 + "\n\n## 文件索引\n\n")

        for i, f in enumerate(files, 1):
            try:
                rel = f.relative_to(root)
            except ValueError:
                rel = f
            out.write(f"{i:4d}. {rel}\n")
        out.write("\n" + "=" * 80 + "\n\n")

        for i, filepath in enumerate(files, 1):
            try:
                rel = filepath.relative_to(root)
            except ValueError:
                rel = filepath

            content = read_file_safe(filepath)
            if content is None:
                failed += 1
                print(f"[跳过-编码失败] {rel}")
                continue

            size_kb = len(content.encode("utf-8")) / 1024
            total_size += size_kb
            out.write(f"\n{'─' * 80}\n")
            out.write(f"## [{i}/{len(files)}] {rel}\n")
            out.write(f"## {size_kb:.1f} KB | {content.count(chr(10)) + 1} 行\n")
            out.write(f"{'─' * 80}\n\n")
            out.write(content)
            if not content.endswith("\n"):
                out.write("\n")
            out.write("\n")
            success += 1
            print(f"[{i:4d}/{len(files)}] {rel}")

        out.write("\n" + "=" * 80 + "\n")
        out.write(f"打包完成: {success} 个文件, {total_size:.1f} KB, 失败 {failed}\n")
        out.write("=" * 80 + "\n")

    print(f"\n打包完成: {success} 个文件 → {output_path} ({total_size:.1f} KB)")
    if failed:
        print(f"失败: {failed} 个文件")
    return success, failed


def _timestamp() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


if __name__ == "__main__":
    root = Path.cwd()
    output_name = sys.argv[1] if len(sys.argv) > 1 else "代码打包.txt"
    pack(root, output_name)

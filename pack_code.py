"""
代码打包工具 —— 递归收集项目中所有 Python 脚本和文档文件，输出到单一 .txt 文件。

包含:
  - *.py (所有 Python 源码)
  - *.yaml, *.yml (配置文件)
  - *.txt, *.md (文档、需求、设计等)
  - *.bat (启动脚本)
  - *.json (如有配置)
排除:
  - __pycache__/, .git/, .claude/, .vscode/, node_modules/
  - data/ 目录下的二进制/数据库文件
  - logs/ 目录
  - *.pdf, *.png, *.jpg 等二进制文件
  - 打包输出文件自身

用法: python pack_code.py [输出文件名]
      默认输出: 代码打包.txt
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def should_include(filepath: Path, root: Path, output_name: str) -> bool:
    """判断文件是否应被打包。"""
    # 排除输出文件自身
    if filepath.name == output_name or filepath.name == Path(__file__).name:
        return False

    # 排除敏感/不必要文件
    if filepath.name in {"config.yaml", "极简设计报告.md", "pdf_viewer_old.py"}:
        return False

    # 获取相对于根目录的路径
    try:
        rel = filepath.relative_to(root)
    except ValueError:
        return False

    parts = rel.parts

    # 排除开源借鉴目录（第三方仓库克隆，体积大且非本项目代码）
    if "开源借鉴" in parts:
        return False

    # 排除特定目录
    exclude_dirs = {
        "__pycache__", ".git", ".claude", ".vscode", "node_modules",
        ".pytest_cache", ".mypy_cache", ".ruff_cache",
    }
    for part in parts[:-1]:  # 检查路径中的所有目录部分
        if part in exclude_dirs or part.startswith("."):
            return False

    # data/ 目录只包含 .py, .json, .yaml, .md, .txt
    if "data" in parts[:-1]:
        if filepath.suffix not in {".py", ".json", ".yaml", ".yml", ".md", ".txt"}:
            return False

    # logs/ 目录完全排除
    if "logs" in parts[:-1]:
        return False

    # 检查扩展名
    allowed = {
        ".py", ".yaml", ".yml", ".txt", ".md", ".bat", ".json",
        ".html", ".css", ".js", ".cfg", ".ini", ".toml",
    }
    if filepath.suffix.lower() in allowed:
        return True

    # 无扩展名的特殊文件
    special_files = {"requirements.txt", ".gitignore", "TODO.md"}
    if filepath.name in special_files or filepath.name == "requirements.txt":
        return True

    return False


def collect_files(root: Path, output_name: str) -> list[Path]:
    """递归收集所有需要打包的文件。"""
    files: list[Path] = []
    for filepath in sorted(root.rglob("*")):
        if filepath.is_file() and should_include(filepath, root, output_name):
            files.append(filepath)
    return files


def read_file_safe(filepath: Path) -> str | None:
    """安全读取文件内容，编码失败时返回 None。"""
    try:
        return filepath.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return filepath.read_text(encoding="gbk")
        except Exception:
            return None


def pack(root: Path, output_name: str) -> tuple[int, int]:
    """执行打包。

    Returns:
        (成功文件数, 失败文件数)
    """
    files = collect_files(root, output_name)
    if not files:
        print("未找到任何可打包的文件。")
        return 0, 0

    output_path = root / output_name
    success = 0
    failed = 0
    total_size = 0

    with output_path.open("w", encoding="utf-8") as out:
        out.write("=" * 80 + "\n")
        out.write(f"代码打包 — 生成时间: {_timestamp()}\n")
        out.write(f"根目录: {root}\n")
        out.write(f"文件总数: {len(files)}\n")
        out.write("=" * 80 + "\n\n")

        out.write("## 文件索引\n\n")
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

            # 文件头
            size_kb = len(content.encode("utf-8")) / 1024
            total_size += size_kb
            out.write(f"\n{'─' * 80}\n")
            out.write(f"## [{i}/{len(files)}] 文件: {rel}\n")
            out.write(f"## 大小: {size_kb:.1f} KB | 行数: {content.count(chr(10)) + 1}\n")
            out.write(f"{'─' * 80}\n\n")
            out.write(content)
            if not content.endswith("\n"):
                out.write("\n")
            out.write("\n")

            success += 1
            print(f"[{i:4d}/{len(files)}] {rel}")

        # 文件尾
        out.write("\n" + "=" * 80 + "\n")
        out.write(f"打包完成: {success} 个文件, {total_size:.1f} KB\n")
        out.write(f"失败: {failed} 个文件\n")
        out.write("=" * 80 + "\n")

    print(f"\n打包完成: {success} 个文件 → {output_path}")
    print(f"总大小: {total_size:.1f} KB")
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

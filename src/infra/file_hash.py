"""统一文件哈希工具。"""
import hashlib
from pathlib import Path


def compute_sha256(filepath: str, chunk_size: int = 65536) -> str:
    """计算文件的 SHA-256 哈希（用于缓存键/文档去重）。"""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()

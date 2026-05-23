"""Benchmark knowledge backends on bundled test PDFs."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.ai_engine import HashingEmbeddingClient
from src.core.knowledge_backends import create_knowledge_backend
from src.core.knowledge_engine import EmbeddingService
from src.core.pdf_engine import DocumentChunker
from src.data.chroma_repo import ChromaRepo


@dataclass
class BenchmarkCase:
    name: str
    pdf: Path


def _cases() -> dict[str, BenchmarkCase]:
    test_dir = ROOT / "测试资料"
    return {
        "attention": BenchmarkCase("attention", test_dir / "Attention is all you need.pdf"),
        "napkin": BenchmarkCase("napkin", test_dir / "Napkin.pdf"),
    }


def _parse_blocks(case: BenchmarkCase, max_pages: int) -> list[Any]:
    with fitz.open(str(case.pdf)) as doc:
        chunker = DocumentChunker()
        pages = range(doc.page_count if max_pages <= 0 else min(doc.page_count, max_pages))
        blocks = []
        for page_num in pages:
            blocks.extend(chunker.chunk_page(doc, page_num))
        return blocks


def _clean_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _benchmark_backend(
    backend_name: str,
    case: BenchmarkCase,
    blocks: list[Any],
    query: str,
) -> dict[str, Any]:
    base_dir = ROOT / "test_artifacts" / "knowledge_benchmark" / backend_name / case.name
    _clean_dir(base_dir)
    embed = EmbeddingService(HashingEmbeddingClient())
    repo = ChromaRepo(str(base_dir / "chroma"))
    backend = create_knowledge_backend(
        backend_name,
        repo,
        embed.embed,
        sqlite_dir=base_dir / "fts",
    )
    progress: list[tuple[int, int]] = []
    doc_hash = f"{case.name}_benchmark"
    t0 = time.perf_counter()
    backend.build(blocks, doc_hash, True, lambda c, t: progress.append((c, t)))
    build_sec = time.perf_counter() - t0
    query_vector = embed.embed_single(query)
    t1 = time.perf_counter()
    results = backend.retrieve(query, query_vector, doc_hash, top_k=8)
    retrieve_sec = time.perf_counter() - t1
    status = backend.status(doc_hash)
    backend.close()
    return {
        "backend": backend_name,
        "case": case.name,
        "blocks": len(blocks),
        "build_sec": round(build_sec, 3),
        "retrieve_sec": round(retrieve_sec, 4),
        "progress_last": progress[-1] if progress else None,
        "status": status.model_dump(),
        "top_ids": [item.get("id") for item in results[:5]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["attention", "napkin"], default="attention")
    parser.add_argument("--backend", choices=["legacy_chroma", "sqlite_fts", "all"], default="all")
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--query", default="What evidence supports the main technical idea?")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "test_artifacts" / "knowledge_benchmark" / "report.json",
    )
    args = parser.parse_args()

    case = _cases()[args.case]
    blocks = _parse_blocks(case, max(0, args.max_pages))
    backends = ["legacy_chroma", "sqlite_fts"] if args.backend == "all" else [args.backend]
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "case": {
            "name": case.name,
            "pdf": str(case.pdf),
        },
        "max_pages": max(0, args.max_pages),
        "results": [
            _benchmark_backend(backend_name, case, blocks, args.query)
            for backend_name in backends
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

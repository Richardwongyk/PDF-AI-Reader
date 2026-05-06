"""
ChromaDB 向量存储仓库。

封装 ChromaDB PersistentClient 的增删改查操作。
每个 PDF 文档对应一个独立的 Collection。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

import chromadb
from chromadb.api.types import Embedding


class ChromaRepo:
    """ChromaDB 向量存储仓库。

    负责：
    - 创建/获取文档对应的 Collection
    - 批量写入块及其向量
    - 语义检索（余弦相似度查询）
    - Collection 生命周期管理（存在检查、删除）

    所有方法均为同步调用。调用方负责将其放在工作线程中执行。
    """

    # Collection 名称前缀
    COLLECTION_PREFIX: str = "pdf_"

    def __init__(self, persist_dir: str) -> None:
        """初始化 ChromaDB 连接。

        Args:
            persist_dir: 持久化数据目录路径（如 "./data/knowledge_bases"）。
        """
        os.makedirs(persist_dir, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_dir)

    def create_or_get_collection(self, doc_hash: str) -> chromadb.Collection:
        """为文档创建或获取 Collection。

        Collection 名称格式: pdf_{doc_hash}
        使用余弦相似度（hnsw:space = cosine）。

        Args:
            doc_hash: 文档 SHA256 哈希前 16 位。

        Returns:
            ChromaDB Collection 对象。
        """
        name = f"{self.COLLECTION_PREFIX}{doc_hash}"
        return self._client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def get_collection(self, doc_hash: str) -> chromadb.Collection:
        """获取已有 Collection。

        Args:
            doc_hash: 文档哈希。

        Returns:
            ChromaDB Collection 对象。

        Raises:
            ValueError: Collection 不存在时。
        """
        name = f"{self.COLLECTION_PREFIX}{doc_hash}"
        try:
            return self._client.get_collection(name)
        except Exception:
            raise ValueError(f"知识库 '{name}' 不存在，请先构建知识库。")

    def upsert_blocks(
        self,
        doc_hash: str,
        block_ids: list[str],
        documents: list[str],
        vectors: list[list[float]],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        """批量写入或更新块及其向量。

        Args:
            doc_hash: 文档哈希。
            block_ids: 块 ID 列表（与 documents/vectors 顺序对应）。
            documents: 块文本内容列表。
            vectors: 块向量列表（每个为 1024 维 float 列表）。
            metadatas: 可选的元数据列表（每个为 dict）。
        """
        collection = self.create_or_get_collection(doc_hash)
        collection.upsert(
            ids=block_ids,
            documents=documents,
            embeddings=vectors,  # type: ignore[arg-type]
            metadatas=metadatas,  # type: ignore[arg-type]
        )

    def query_relevant(
        self,
        doc_hash: str,
        query_vector: list[float],
        top_k: int = 3,
        exclude_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """查询最相关的块。

        使用余弦相似度在指定文档的 Collection 中检索。

        Args:
            doc_hash: 文档哈希。
            query_vector: 查询向量（由 EmbeddingService 生成）。
            top_k: 返回的最大块数。
            exclude_ids: 需要排除的块 ID 列表（避免返回当前段落自身）。

        Returns:
            按相似度降序的结果列表，每个结果含:
            - "id": 块 ID
            - "document": 块文本内容
            - "metadata": 元数据字典
            - "distance": 余弦距离（越小越相似）
        """
        collection = self.get_collection(doc_hash)
        where_filter: dict[str, Any] | None = None
        if exclude_ids:
            # ChromaDB 目前不直接支持 NOT IN 过滤，
            # 采用后处理方式：多取一些结果，再排除。
            fetch_count = top_k + len(exclude_ids)
        else:
            fetch_count = top_k

        results: dict = collection.query(
            query_embeddings=[query_vector],
            n_results=fetch_count,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        # 整理结果格式
        entries: list[dict[str, Any]] = []
        if results.get("ids") and results["ids"][0]:
            for i, block_id in enumerate(results["ids"][0]):
                if exclude_ids and block_id in exclude_ids:
                    continue
                entries.append({
                    "id": block_id,
                    "document": results["documents"][0][i] if results.get("documents") else "",
                    "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                    "distance": results["distances"][0][i] if results.get("distances") else 0.0,
                })
                if len(entries) >= top_k:
                    break

        return entries

    def get_block_by_id(self, doc_hash: str, block_id: str) -> dict[str, Any] | None:
        """获取指定块的数据。

        Args:
            doc_hash: 文档哈希。
            block_id: 块 ID。

        Returns:
            块数据字典或 None。
        """
        collection = self.get_collection(doc_hash)
        result: dict = collection.get(
            ids=[block_id],
            include=["documents", "metadatas"],
        )
        if result.get("ids") and result["ids"]:
            return {
                "id": result["ids"][0],
                "document": result["documents"][0] if result.get("documents") else "",
                "metadata": result["metadatas"][0] if result.get("metadatas") else {},
            }
        return None

    def delete_collection(self, doc_hash: str) -> None:
        """删除指定文档的知识库 Collection。

        Args:
            doc_hash: 文档哈希。
        """
        name = f"{self.COLLECTION_PREFIX}{doc_hash}"
        try:
            self._client.delete_collection(name)
        except Exception:
            pass  # Collection 不存在时不报错

    def collection_exists(self, doc_hash: str) -> bool:
        """检查指定文档的知识库是否已构建。

        Args:
            doc_hash: 文档哈希。

        Returns:
            True 表示 Collection 存在。
        """
        name = f"{self.COLLECTION_PREFIX}{doc_hash}"
        try:
            self._client.get_collection(name)
            return True
        except Exception:
            return False

    def list_collections(self) -> list[str]:
        """列出所有已构建的知识库 Collection 名称。

        Returns:
            Collection 名称列表。
        """
        return [c.name for c in self._client.list_collections()]

    @staticmethod
    def compute_doc_hash(filepath: str) -> str:
        """计算文件的 SHA256 哈希并返回前 16 位。

        用于生成 Collection 名称和判断知识库是否已构建。

        Args:
            filepath: 文件路径。

        Returns:
            16 位十六进制字符串。
        """
        sha = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()[:16]

"""
知识库引擎 —— 嵌入服务、摘要生成、知识库构建与检索。

EmbeddingService: 文本向量化（通过 Ollama BGE-M3）
KnowledgeEngine: 知识库构建与语义检索的协调者
"""

import math
import re
from collections import Counter

from PySide6.QtCore import QObject, QReadLocker, QReadWriteLock, QThreadPool, QWriteLocker, Signal

from src.core.base_service import BaseService
from src.core.models import (
    AppConfig,
    DocumentBlock,
    KnowledgeStatus,
)
from src.data.chroma_repo import ChromaRepo

# 嵌入客户端接口（避免循环依赖，仅用于类型注解）
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from src.core.ai_engine import BaseLLMClient


# =============================================================================
# EmbeddingService —— 文本向量化
# =============================================================================

class EmbeddingService:
    """文本向量化服务。

    支持本地 Ollama（BGE-M3）和云端嵌入 API。
    通过 BaseLLMClient 抽象接口，自动本地优先、云端回退。
    """

    _BATCH_SIZE: int = 20
    _MAX_CACHE_SIZE: int = 5000

    def __init__(self, primary: BaseLLMClient, fallback: BaseLLMClient | None = None) -> None:
        """初始化嵌入服务。

        Args:
            primary: 主嵌入客户端（通常为 OllamaClient）。
            fallback: 回退客户端（云端），可为 None。
        """
        self._primary = primary
        self._fallback = fallback
        self._cache: dict[str, list[float]] = {}
        self._available: bool | None = None  # 延迟检测

    @property
    def is_available(self) -> bool:
        """嵌入服务当前是否可用。"""
        if self._available is None:
            self._available = self._primary.check_availability()
            if not self._available and self._fallback:
                self._available = self._fallback.check_availability()
        return self._available

    def _get_client(self) -> BaseLLMClient:
        """获取当前可用的嵌入客户端（本地优先）。"""
        if self._primary.check_availability():
            return self._primary
        if self._fallback and self._fallback.check_availability():
            return self._fallback
        raise RuntimeError("无可用的嵌入模型。请启动 Ollama 或配置云端 API Key。")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """对一批文本生成向量。"""
        vectors: list[list[float]] = []
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, text in enumerate(texts):
            if text in self._cache:
                vectors.append(self._cache[text])
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)
                vectors.append([])

        if not uncached_texts:
            return vectors

        client = self._get_client()
        for batch_start in range(0, len(uncached_texts), self._BATCH_SIZE):
            batch_texts = uncached_texts[batch_start:batch_start + self._BATCH_SIZE]
            batch_vectors = client.embed_batch(batch_texts)

            for j, vec in enumerate(batch_vectors):
                original_idx = uncached_indices[batch_start + j]
                original_text = texts[original_idx]
                vectors[original_idx] = vec
                if len(self._cache) < self._MAX_CACHE_SIZE:
                    self._cache[original_text] = vec

        return vectors

    def embed_single(self, text: str) -> list[float]:
        """对单条文本生成向量。"""
        return self.embed([text])[0]

    def clear_cache(self) -> None:
        """清空向量缓存。"""
        self._cache.clear()

    def check_availability(self) -> bool:
        """检查嵌入服务是否可用。"""
        return self.is_available


# =============================================================================
# KnowledgeEngine —— 知识库引擎
# =============================================================================

class KnowledgeEngine(BaseService):
    """知识库引擎。

    负责：
    - 知识库构建（接收块列表，协调嵌入和存储）
    - 语义检索（接收查询文本，返回最相关块）
    - 知识库生命周期管理（创建、查询、删除）
    """

    _MAX_RETRIEVAL_CANDIDATES = 48
    _EN_STOPWORDS = {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
        "for", "from", "how", "in", "into", "is", "it", "its", "of", "on",
        "or", "that", "the", "their", "this", "to", "was", "what", "when",
        "where", "which", "why", "with",
    }

    # === 信号 ===
    build_progress = Signal(int, int)        # (已完成块数, 总块数)
    build_finished = Signal(str)             # (文档哈希)
    build_error = Signal(str)                # (错误信息)

    def __init__(
        self,
        embed_service: EmbeddingService,
        chroma_repo: ChromaRepo,
        parent: QObject | None = None,
    ) -> None:
        """初始化知识库引擎。

        Args:
            embed_service: 嵌入服务实例。
            chroma_repo: ChromaDB 仓库实例。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._embed = embed_service
        self._repo = chroma_repo
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(2)  # 最多 2 个并行嵌入线程
        self._db_lock = QReadWriteLock()  # 防止 ChromaDB SQLite 并发读写锁死

    @property
    def embedding_service(self) -> EmbeddingService:
        """获取嵌入服务实例。"""
        return self._embed

    @property
    def chroma_repo(self) -> ChromaRepo:
        """获取 ChromaDB 仓库实例。"""
        return self._repo

    def build_knowledge_base(
        self, blocks: list[DocumentBlock], doc_hash: str, force_rebuild: bool = False
    ) -> None:
        """构建或重建知识库。在工作线程池中异步执行。

        Args:
            blocks: 文档块列表。
            doc_hash: 文件 SHA256 哈希（前 16 位）。
            force_rebuild: True 时即使已存在也重新构建。
        """
        if not force_rebuild and self._repo.collection_exists(doc_hash):
            self.build_finished.emit(doc_hash)
            return

        if force_rebuild:
            self._repo.delete_collection(doc_hash)

        # 使用 QThreadPool 异步执行
        worker = _BuildWorker(self._embed, self._repo, blocks, doc_hash, self._db_lock)
        worker.progress.connect(self.build_progress.emit)
        worker.finished.connect(self.build_finished.emit)
        worker.error.connect(self.build_error.emit)
        self._pool.start(worker)

    def retrieve(
        self,
        query: str,
        doc_hash: str,
        top_k: int = 3,
        exclude_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """从知识库中检索与查询最相关的块。

        工作流程：
        1. 调用 EmbeddingService.embed_single(query) 生成查询向量。
        2. 从 ChromaRepo 多取候选块。
        3. 结合向量距离、问题词覆盖率、章节/摘要命中进行重排。

        Args:
            query: 自然语言查询文本。
            doc_hash: 文档哈希。
            top_k: 返回最大块数。
            exclude_ids: 排除的块 ID 列表。

        Returns:
            按相似度降序的结果列表。
        """
        if top_k <= 0:
            return []

        query_vector = self._embed.embed_single(query)
        fetch_k = self._retrieval_candidate_count(top_k)
        with QReadLocker(self._db_lock):
            candidates = self._repo.query_relevant(
                doc_hash, query_vector, top_k=fetch_k, exclude_ids=exclude_ids
            )
        return self._rerank_retrieval_results(query, candidates, top_k=top_k)

    @classmethod
    def _retrieval_candidate_count(cls, top_k: int) -> int:
        """为最终 top_k 计算重排候选池大小。"""
        if top_k <= 0:
            return 0
        return min(cls._MAX_RETRIEVAL_CANDIDATES, max(top_k * 4, top_k + 8))

    @classmethod
    def _rerank_retrieval_results(
        cls,
        query: str,
        results: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """用轻量混合分数重排向量检索候选。"""
        if top_k <= 0:
            return []

        query_tokens = cls._tokenize_for_retrieval(query)
        query_terms = Counter(query_tokens)
        if not query_terms:
            return results[:top_k]

        scored: list[tuple[float, float, float, int, dict[str, Any]]] = []
        for index, result in enumerate(results):
            metadata = result.get("metadata") or {}
            document = str(result.get("document") or result.get("content") or "")
            context_text = cls._candidate_text(document, metadata)
            lexical_score = cls._lexical_score(query_tokens, query_terms, context_text)
            distance = cls._safe_distance(result.get("distance"))
            vector_score = 1.0 / (1.0 + max(distance, 0.0))
            combined_score = (0.55 * vector_score) + (0.45 * lexical_score)

            ranked = dict(result)
            ranked["vector_score"] = vector_score
            ranked["lexical_score"] = lexical_score
            ranked["retrieval_score"] = combined_score
            scored.append((combined_score, lexical_score, -distance, -index, ranked))

        scored.sort(reverse=True)
        return [item[-1] for item in scored[:top_k]]

    @classmethod
    def _candidate_text(cls, document: str, metadata: dict[str, Any]) -> str:
        """拼出用于关键词重排的候选文本。"""
        fields = [
            document,
            str(metadata.get("section") or ""),
            str(metadata.get("summary") or ""),
            str(metadata.get("keywords") or ""),
        ]
        return "\n".join(field for field in fields if field)

    @classmethod
    def _lexical_score(
        cls,
        query_tokens: list[str],
        query_terms: Counter[str],
        candidate_text: str,
    ) -> float:
        """计算问题词在候选块中的覆盖程度。"""
        candidate_tokens = cls._tokenize_for_retrieval(candidate_text)
        candidate_terms = Counter(candidate_tokens)
        if not candidate_terms:
            return 0.0

        total = sum(query_terms.values())
        matched = sum(min(count, candidate_terms.get(term, 0)) for term, count in query_terms.items())
        coverage = matched / total if total else 0.0

        unique_total = len(query_terms)
        unique_matched = sum(1 for term in query_terms if candidate_terms.get(term, 0) > 0)
        unique_coverage = unique_matched / unique_total if unique_total else 0.0

        query_bigrams = set(cls._bigrams(query_tokens))
        candidate_bigrams = set(cls._bigrams(candidate_tokens))
        bigram_score = (
            len(query_bigrams & candidate_bigrams) / len(query_bigrams)
            if query_bigrams else 0.0
        )

        score = (0.55 * coverage) + (0.35 * unique_coverage) + (0.10 * bigram_score)
        return min(1.0, score)

    @classmethod
    def _tokenize_for_retrieval(cls, text: str) -> list[str]:
        """中英文混合检索分词，保留公式变量和术语形态。"""
        lowered = text.lower()
        raw_tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", lowered)
        tokens: list[str] = []
        for token in raw_tokens:
            if token in cls._EN_STOPWORDS:
                continue
            tokens.append(token)
            if re.match(r"^[a-z0-9_]+$", token):
                tokens.extend(cls._simple_variants(token))
        return tokens

    @staticmethod
    def _simple_variants(token: str) -> list[str]:
        """加入少量英文词形变体，提升问句和论文正文的直接命中率。"""
        variants: list[str] = []
        if len(token) > 4 and token.endswith("ies"):
            variants.append(f"{token[:-3]}y")
        elif len(token) > 4 and token.endswith("s"):
            variants.append(token[:-1])
        if len(token) > 5 and token.endswith("ing"):
            base = token[:-3]
            variants.append(base)
            variants.append(f"{base}e")
        if len(token) > 4 and token.endswith("ed"):
            variants.append(token[:-2])
        return variants

    @staticmethod
    def _bigrams(tokens: Any) -> list[str]:
        token_list = list(tokens)
        return [
            f"{token_list[i]}_{token_list[i + 1]}"
            for i in range(len(token_list) - 1)
        ]

    @staticmethod
    def _safe_distance(value: Any) -> float:
        try:
            distance = float(value)
        except (TypeError, ValueError):
            return math.inf
        if math.isnan(distance):
            return math.inf
        return distance

    def delete_knowledge_base(self, doc_hash: str) -> None:
        """删除指定文档的知识库。

        Args:
            doc_hash: 文档哈希。
        """
        self._repo.delete_collection(doc_hash)

    def check_exists(self, doc_hash: str) -> bool:
        """检查指定文档的知识库是否已构建。

        Args:
            doc_hash: 文档哈希。

        Returns:
            True 表示知识库已存在。
        """
        return self._repo.collection_exists(doc_hash)

    def get_status(self, doc_hash: str) -> KnowledgeStatus:
        """获取知识库状态。

        Args:
            doc_hash: 文档哈希。

        Returns:
            KnowledgeStatus 对象。
        """
        exists = self._repo.collection_exists(doc_hash)
        total_blocks = 0
        if exists:
            try:
                collection = self._repo.get_collection(doc_hash)
                total_blocks = collection.count()
            except Exception:
                total_blocks = 0
        return KnowledgeStatus(
            doc_hash=doc_hash,
            collection_name=f"pdf_{doc_hash}",
            is_ready=exists,
            total_blocks=total_blocks,
            embedded_blocks=total_blocks,
        )

    def close(self) -> None:
        """关闭知识库引擎，释放线程池和数据库连接。"""
        # 1. 等待所有构建任务完成
        if self._pool.activeThreadCount() > 0:
            self._pool.waitForDone(5000)
        # 2. 关闭 ChromaDB 连接
        self._repo.close()
        self.logger.info("知识库引擎已关闭")


# =============================================================================
# _BuildWorker —— 知识库构建 Worker
# =============================================================================

from PySide6.QtCore import QRunnable  # noqa: E402


class _BuildWorker(QRunnable):
    """在 QThreadPool 中执行知识库构建的 Worker。"""

    def __init__(
        self,
        embed_service: EmbeddingService,
        chroma_repo: ChromaRepo,
        blocks: list[DocumentBlock],
        doc_hash: str,
        db_lock: QReadWriteLock | None = None,
    ) -> None:
        """初始化构建 Worker。

        Args:
            embed_service: 嵌入服务实例。
            chroma_repo: ChromaDB 仓库实例。
            blocks: 文档块列表。
            doc_hash: 文档哈希。
            db_lock: 数据库写锁（防止 SQLite 并发锁死）。
        """
        super().__init__()
        self._embed = embed_service
        self._repo = chroma_repo
        self._blocks = blocks
        self._doc_hash = doc_hash
        self._db_lock = db_lock

        from PySide6.QtCore import Signal, QObject

        class _Signals(QObject):
            progress = Signal(int, int)
            finished = Signal(str)
            error = Signal(str)

        self._signals = _Signals()

    @property
    def progress(self) -> Signal:
        """进度信号 (current, total)。"""
        return self._signals.progress

    @property
    def finished(self) -> Signal:
        """完成信号 (doc_hash)。"""
        return self._signals.finished

    @property
    def error(self) -> Signal:
        """错误信号 (error_message)。"""
        return self._signals.error

    def run(self) -> None:
        """在 QThreadPool 工作线程中执行构建。

        步骤：
        1. 创建 ChromaDB Collection
        2. 分批生成嵌入向量
        3. 批量写入 ChromaDB
        """
        import logging
        _logger = logging.getLogger("KnowledgeEngine")
        try:
            import time
            start_time = time.time()

            total = len(self._blocks)
            self._signals.progress.emit(0, total)

            text_contents = [b.content for b in self._blocks]
            vectors = self._embed.embed(text_contents)

            # 准备写入数据
            block_ids = [b.id for b in self._blocks]
            metadatas = [
                {
                    "page": b.page_num,
                    "type": b.block_type.value,
                    "section": b.section_title,
                    "summary": b.metadata.get("summary", ""),
                }
                for b in self._blocks
            ]

            # 分批写入（加锁防 SQLite 并发锁死）
            _BATCH_SIZE = 50
            for batch_start in range(0, total, _BATCH_SIZE):
                batch_end = min(batch_start + _BATCH_SIZE, total)
                if self._db_lock:
                    with QWriteLocker(self._db_lock):
                        self._repo.upsert_blocks(
                            self._doc_hash,
                            block_ids=block_ids[batch_start:batch_end],
                            documents=text_contents[batch_start:batch_end],
                            vectors=vectors[batch_start:batch_end],
                            metadatas=metadatas[batch_start:batch_end],
                        )
                else:
                    self._repo.upsert_blocks(
                        self._doc_hash,
                        block_ids=block_ids[batch_start:batch_end],
                        documents=text_contents[batch_start:batch_end],
                        vectors=vectors[batch_start:batch_end],
                        metadatas=metadatas[batch_start:batch_end],
                    )
                self._signals.progress.emit(batch_end, total)

            elapsed = time.time() - start_time
            _logger.info(
                "知识库构建完成: doc_hash=%s, blocks=%d, time=%.1fs",
                self._doc_hash, total, elapsed,
            )
            self._signals.finished.emit(self._doc_hash)

        except Exception as e:
            _logger.error("知识库构建失败: %s", e)
            self._signals.error.emit(str(e))

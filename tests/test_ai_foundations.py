import pytest

from src.core.ai_engine import HashingEmbeddingClient, HybridModelRouter, MockLLMClient, QAService
from src.core.knowledge_engine import KnowledgeEngine
from src.core.models import AppConfig, DocumentBlock, BlockType, TaskType


class _UnavailableClient(MockLLMClient):
    def check_availability(self) -> bool:
        return False


class _NamedClient(MockLLMClient):
    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    @property
    def model_name(self) -> str:
        return self._name


def test_hashing_embedding_is_deterministic_and_query_sensitive() -> None:
    client = HashingEmbeddingClient(dimensions=128)
    a1, a2, b = client.embed_batch([
        "attention mechanism and transformer",
        "attention mechanism and transformer",
        "riemannian manifold curvature tensor",
    ])

    assert a1 == a2
    assert a1 != b
    assert pytest.approx(sum(v * v for v in a1), rel=1e-6) == 1.0


def test_knowledge_retrieval_reranks_candidates_by_query_evidence() -> None:
    results = [
        {
            "id": "near-vector",
            "document": "This paragraph discusses residual layers and optimization.",
            "metadata": {"section": "Model"},
            "distance": 0.02,
        },
        {
            "id": "lexical-match",
            "document": "Scaled dot-product attention maps queries, keys, and values.",
            "metadata": {"section": "Attention"},
            "distance": 0.85,
        },
    ]

    ranked = KnowledgeEngine._rerank_retrieval_results(
        "How does attention use queries and keys?",
        results,
        top_k=1,
    )

    assert ranked[0]["id"] == "lexical-match"
    assert ranked[0]["lexical_score"] > ranked[0]["vector_score"]


def test_knowledge_retrieval_candidate_pool_expands_for_full_document_qa() -> None:
    assert KnowledgeEngine._retrieval_candidate_count(8) == 32
    assert KnowledgeEngine._retrieval_candidate_count(100) == 48


def test_router_keeps_local_and_cloud_roles_separate() -> None:
    cfg = AppConfig()
    cfg.routing.translation = "cloud_only"
    local = _NamedClient("local")
    cloud = _NamedClient("cloud")
    fallback = _NamedClient("fallback")

    router = HybridModelRouter(local, cloud, fallback, cfg)

    assert router.route(TaskType.TRANSLATION).model_name == "cloud"
    assert router.local_available is True
    assert router.cloud_available is True


def test_router_uses_fallback_only_when_cloud_missing_in_cloud_only_mode() -> None:
    cfg = AppConfig()
    cfg.routing.translation = "cloud_only"
    router = HybridModelRouter(_UnavailableClient(), None, _NamedClient("fallback"), cfg)

    assert router.route(TaskType.TRANSLATION).model_name == "fallback"


def test_qa_without_context_does_not_invite_free_answering() -> None:
    cfg = AppConfig()
    router = HybridModelRouter(None, None, MockLLMClient(), cfg)
    service = QAService(router)

    messages = service._build_qa_messages("what is the theorem?", None, [], None)

    assert "无法基于本文档给出可靠答案" in messages[-1]["content"]
    assert "请根据你的知识回答" not in messages[-1]["content"]


def test_qa_with_context_includes_page_reference() -> None:
    cfg = AppConfig()
    router = HybridModelRouter(None, None, MockLLMClient(), cfg)
    service = QAService(router)
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="The attention mechanism maps queries and keys.",
        bbox=(0, 0, 100, 20),
    )

    messages = service._build_qa_messages("attention?", block, [], None)

    assert "[当前段落 — 第1页]" in messages[-1]["content"]
    assert block.content in messages[-1]["content"]


def test_mock_qa_generates_followup_questions() -> None:
    cfg = AppConfig()
    router = HybridModelRouter(None, None, MockLLMClient(), cfg)
    service = QAService(router)

    questions = service.generate_followup_questions("what is attention?", "It uses context.")

    assert len(questions) == 3
    assert all(question.endswith("？") for question in questions)

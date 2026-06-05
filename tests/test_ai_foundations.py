import pytest

from src.core.ai_engine import (
    HashingEmbeddingClient,
    HybridModelRouter,
    MockLLMClient,
    QAService,
    _QAThread,
    _user_facing_error,
)
from src.core.knowledge_engine import KnowledgeEngine
from src.core.model_providers import normalize_litellm_model
from src.core.models import AppConfig, DocumentBlock, BlockType, TaskType
from src.data.config_manager import ConfigManager


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


class _RouterClient(MockLLMClient):
    def __init__(self, name: str, available: bool = True) -> None:
        super().__init__()
        self._name = name
        self._available = available

    @property
    def model_name(self) -> str:
        return self._name

    def check_availability(self) -> bool:
        return self._available


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


def test_app_config_exposes_rag_and_reasoning_models() -> None:
    cfg = AppConfig()

    assert cfg.model.cloud_translation == "deepseek/deepseek-chat"
    assert cfg.model.cloud_reasoning == "deepseek/deepseek-v4-pro"
    assert cfg.model.formula_ocr_backend == "pix2text-mfr"
    assert cfg.model.formula_ocr_model == "PP-FormulaNet_plus-S"
    assert cfg.rag.backend == "legacy_chroma"
    assert cfg.rag.candidate_pool == 48
    assert cfg.routing.translation == "cloud_only"
    assert cfg.routing.qa == "cloud_only"
    assert cfg.routing.summarization == "cloud_only"


def test_deepseek_reasoning_model_name_is_litellm_compatible() -> None:
    assert normalize_litellm_model("deepseek-v4-pro") == "deepseek/deepseek-v4-pro"
    assert normalize_litellm_model("deepseek/deepseek-v4-flash") == "deepseek/deepseek-v4-flash"


def test_config_api_key_reuses_same_deepseek_provider_family(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model:
  cloud: deepseek/deepseek-v4-flash
api_keys:
  deepseek/deepseek-v4-flash: sk-test
""",
        encoding="utf-8",
    )
    manager = ConfigManager(str(config_path))

    assert manager.get_api_key("deepseek/deepseek-v4-pro") == "sk-test"
    assert manager.get_api_key("deepseek-v4-pro") == "sk-test"


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


def test_router_uses_reasoning_client_for_full_document_tasks() -> None:
    cfg = AppConfig()
    cfg.routing.qa = "cloud_only"
    router = HybridModelRouter(
        None,
        _RouterClient("translation"),
        _RouterClient("fallback"),
        cfg,
        reasoning_client=_RouterClient("reasoning"),
    )

    assert router.route(TaskType.QA).model_name == "reasoning"
    assert router.route(TaskType.TRANSLATION).model_name == "translation"


def test_router_keeps_followups_on_lightweight_cloud_model() -> None:
    cfg = AppConfig()
    cfg.routing.qa = "cloud_only"
    router = HybridModelRouter(
        None,
        _RouterClient("translation"),
        _RouterClient("fallback"),
        cfg,
        reasoning_client=_RouterClient("reasoning"),
    )

    assert router.route(TaskType.FOLLOWUP_QUESTIONS).model_name == "translation"


def test_translation_preprocessor_restores_inline_math_delimiters() -> None:
    from src.core.pdf_engine import TextPreprocessor

    preprocessor = TextPreprocessor()
    protected = preprocessor.protect_formulas(r"The model \(M\) contains \(G\).")

    assert protected == "The model 【FORMULA_0】 contains 【FORMULA_1】."
    assert preprocessor.restore_formulas(protected) == r"The model \(M\) contains \(G\)."


def test_translation_preprocessor_does_not_guess_split_bare_math() -> None:
    from src.core.pdf_engine import TextPreprocessor

    preprocessor = TextPreprocessor()
    protected = preprocessor.protect_formulas(r"generic subset \(G ⊆\) P, where \(G\) is new")

    assert protected == "generic subset 【FORMULA_0】 P, where 【FORMULA_1】 is new"
    restored = preprocessor.restore_formulas(protected)
    assert restored == r"generic subset \(G ⊆\) P, where \(G\) is new"


def test_translation_preprocessor_does_not_protect_bare_math_without_evidence() -> None:
    from src.core.pdf_engine import TextPreprocessor

    preprocessor = TextPreprocessor()
    protected = preprocessor.protect_formulas("the statement Π 1 is absolute")

    assert protected == "the statement Π 1 is absolute"
    assert preprocessor.restore_formulas(protected) == "the statement Π 1 is absolute"


def test_qa_without_context_allows_labeled_background_supplement() -> None:
    cfg = AppConfig()
    router = HybridModelRouter(None, None, MockLLMClient(), cfg)
    service = QAService(router)

    messages = service._build_qa_messages("what is the theorem?", None, [], None)

    assert "未检索到足够的本文档证据" in messages[-1]["content"]
    assert "背景补充" in messages[0]["content"]
    assert "不得把背景知识说成论文原文内容" in messages[0]["content"]


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

    assert "[S0] 当前段落 · 第1页" in messages[-1]["content"]
    assert "[S1]" not in messages[-1]["content"]
    assert block.content in messages[-1]["content"]
    assert "证据编号形如 [S1]" in messages[0]["content"]


def test_qa_with_retrieved_blocks_uses_source_ids() -> None:
    cfg = AppConfig()
    router = HybridModelRouter(None, None, MockLLMClient(), cfg)
    service = QAService(router)
    block = DocumentBlock(
        id="p2_b0",
        page_num=2,
        block_type=BlockType.FORMULA,
        content=r"$$\nA=\frac{QK^T}{\sqrt{d_k}}\n$$",
        bbox=(0, 0, 100, 20),
        section_title="Attention",
    )

    messages = service._build_qa_messages("attention?", None, [block], None)

    assert "[S1] 相关片段 · 第3页 · formula · Attention" in messages[-1]["content"]
    assert block.content in messages[-1]["content"]


def test_mock_qa_generates_followup_questions() -> None:
    cfg = AppConfig()
    router = HybridModelRouter(None, None, MockLLMClient(), cfg)
    service = QAService(router)

    questions = service.generate_followup_questions("what is attention?", "It uses context.")

    assert len(questions) == 3
    assert all(question.endswith("？") for question in questions)


def test_followup_parser_tolerates_numbered_model_output() -> None:
    questions = QAService._parse_followup_questions(
        "1. 注意力机制为何能并行？\n2. 位置编码起什么作用？\n3. 多头注意力的优势是什么？"
    )

    assert questions == [
        "注意力机制为何能并行？",
        "位置编码起什么作用？",
        "多头注意力的优势是什么？",
    ]


class _StreamFailQAService:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def answer(
        self,
        question: str,
        current_block: DocumentBlock | None,
        retrieved_blocks: list[DocumentBlock],
        chat_history: list[dict[str, str]] | None,
        stream: bool = True,
    ) -> object:
        self.calls.append(stream)
        if stream:
            raise RuntimeError("stream failed")
        return "fallback answer"

    def generate_followup_questions(self, question: str, answer: str) -> list[str]:
        return []


def test_qa_thread_falls_back_to_non_streaming_answer() -> None:
    service = _StreamFailQAService()
    thread = _QAThread(  # type: ignore[arg-type]
        service,
        "question",
        current_block=None,
        retrieved_blocks=[],
        chat_history=None,
    )
    tokens: list[str] = []
    finished: list[str] = []
    errors: list[str] = []
    thread.token_generated.connect(tokens.append)
    thread.finished_signal.connect(finished.append)
    thread.error_signal.connect(errors.append)

    thread.run()

    assert service.calls == [True, False]
    assert tokens == ["fallback answer"]
    assert finished == ["fallback answer"]
    assert errors == []


class _FailingClient(MockLLMClient):
    @property
    def model_name(self) -> str:
        return "failing"

    def generate(self, messages: list[dict[str, str]], **kwargs: object) -> str:
        raise RuntimeError("cloud ssl failed")

    def generate_stream(self, messages: list[dict[str, str]], **kwargs: object):
        raise RuntimeError("cloud ssl failed")


def test_qa_service_falls_back_when_selected_client_fails() -> None:
    cfg = AppConfig()
    cfg.routing.qa = "cloud_only"
    router = HybridModelRouter(
        local_client=None,
        cloud_client=None,
        fallback_client=MockLLMClient(),
        config=cfg,
        reasoning_client=_FailingClient(),
    )
    service = QAService(router)

    answer = service.answer("what is attention?", None, [], None, stream=False)

    assert isinstance(answer, str)
    assert answer.strip()


def test_user_facing_error_summarizes_network_traceback() -> None:
    message = _user_facing_error(RuntimeError("SSL: UNEXPECTED_EOF_WHILE_READING\nTraceback detail"))

    assert message.startswith("云端模型连接失败")
    assert "\n" not in message
    assert len(message) < 300

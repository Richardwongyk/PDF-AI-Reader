from __future__ import annotations

from src.app.ask_flow import AskQuestionFlow
from src.core.models import BlockType, DocumentBlock


class _KnowledgeEngine:
    def __init__(self) -> None:
        self.top_k: int | None = None

    def check_exists(self, doc_hash: str) -> bool:
        return doc_hash == "doc-1"

    def retrieve(
        self,
        question: str,
        doc_hash: str,
        top_k: int,
        exclude_ids: list[str] | None = None,
    ) -> list[dict[str, object]]:
        self.top_k = top_k
        assert question == "what is attention?"
        assert doc_hash == "doc-1"
        assert exclude_ids is None
        return [{
            "id": "p0_b0",
            "distance": 0.125,
            "retrieval_score": 0.82,
            "lexical_score": 0.75,
            "vector_score": 0.88,
        }]


class _AIEngine:
    def __init__(self) -> None:
        self.request: dict[str, object] | None = None

    def request_answer(self, **kwargs: object) -> None:
        self.request = kwargs


def test_full_document_question_emits_retrieval_evidence() -> None:
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="Attention links queries, keys, and values across a sequence.",
        bbox=(0, 0, 100, 20),
    )
    knowledge = _KnowledgeEngine()
    ai_engine = _AIEngine()
    flow = AskQuestionFlow(ai_engine, knowledge)
    flow.set_doc_hash("doc-1")

    emitted: list[tuple[str, list[dict[str, object]]]] = []
    flow.retrieval_ready.connect(lambda split_id, evidence: emitted.append((split_id, evidence)))

    flow.request_answer(
        question="what is attention?",
        block=None,
        block_id="__dock_qa__",
        chat_history=None,
        find_block_cb=lambda block_id: block if block_id == block.id else None,
    )

    assert knowledge.top_k == 8
    assert emitted == [
        (
            "__dock_qa__",
            [
                {
                    "id": "p0_b0",
                    "page": 1,
                    "type": "paragraph",
                    "distance": 0.125,
                    "retrieval_score": 0.82,
                    "lexical_score": 0.75,
                    "vector_score": 0.88,
                    "content": block.content,
                },
            ],
        )
    ]
    assert ai_engine.request is not None
    assert ai_engine.request["retrieved_blocks"] == [block]
    assert ai_engine.request["current_block"] is None

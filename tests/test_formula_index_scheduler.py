from src.app.formula_index_scheduler import (
    FormulaIndexScheduler,
    FormulaScanPolicy,
    FormulaScanTrigger,
)
from src.core.models import BlockType, DocumentBlock


def _formula(block_id: str, page_num: int, score: float = 0.5) -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        page_num=page_num,
        block_type=BlockType.FORMULA,
        content="[image formula]",
        bbox=(0, 0, 100, 30),
        metadata={"needs_ocr": True, "formula_score": score},
    )


def test_viewport_plan_is_cache_only_and_scoped_to_visible_pages() -> None:
    scheduler = FormulaIndexScheduler()
    plan = scheduler.plan_for_pages(
        [_formula("p0_b1", 0), _formula("p3_b1", 3)],
        pages={0},
        trigger=FormulaScanTrigger.VIEWPORT,
        page_count=10,
    )

    assert [block.id for block in plan.blocks] == ["p0_b1"]
    assert plan.batch_budget == 2
    assert plan.cache_only is True
    assert plan.drain_queue is False


def test_evidence_plan_prioritizes_evidence_pages() -> None:
    scheduler = FormulaIndexScheduler(FormulaScanPolicy(evidence_budget=3))
    plan = scheduler.plan_for_evidence(
        [_formula("p1_low", 1, 0.1), _formula("p4_high", 4, 0.9), _formula("p8_other", 8, 1.0)],
        evidence=[{"page": 5}, {"page": "2"}],
        page_count=20,
    )

    assert [block.id for block in plan.blocks] == ["p4_high", "p1_low"]
    assert plan.priority_pages == {1, 4}
    assert plan.batch_budget == 3
    assert plan.cache_only is True


def test_high_precision_plan_can_use_model_and_drain_queue() -> None:
    scheduler = FormulaIndexScheduler(FormulaScanPolicy(high_precision_budget=12))
    plan = scheduler.plan_for_pages(
        [_formula("p0", 0), _formula("p20", 20)],
        pages={0},
        trigger=FormulaScanTrigger.HIGH_PRECISION,
        page_count=100,
    )

    assert [block.id for block in plan.blocks] == ["p0", "p20"]
    assert plan.batch_budget == 12
    assert plan.cache_only is False
    assert plan.drain_queue is True


def test_background_budget_is_reduced_for_large_documents() -> None:
    scheduler = FormulaIndexScheduler(FormulaScanPolicy(background_budget=8, large_doc_pages=300))
    plan = scheduler.plan_for_pages(
        [_formula("p0", 0), _formula("p400", 400)],
        pages=set(),
        trigger=FormulaScanTrigger.BACKGROUND,
        page_count=1000,
    )

    assert plan.batch_budget == 4
    assert plan.cache_only is True
    assert plan.drain_queue is True

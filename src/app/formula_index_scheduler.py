"""Formula scan scheduling policy.

The scheduler turns UI context such as viewport pages, question evidence, and
explicit user actions into a small OCR batch plan. The plan is deliberately
conservative by default: interactive reading only performs cache-first scans,
while future high-precision modes can opt into model-backed OCR.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.app.formula_index_store import FormulaScanRound
from src.core.models import BlockType, DocumentBlock


class FormulaScanTrigger(str, Enum):
    """Where a formula scan request came from."""

    VIEWPORT = "viewport"
    EVIDENCE = "evidence"
    USER_ACTION = "user_action"
    BACKGROUND = "background"
    HIGH_PRECISION = "high_precision"


@dataclass(frozen=True)
class FormulaScanPlan:
    """Budget and ordering decision for one scheduling request."""

    blocks: list[DocumentBlock]
    priority_pages: set[int]
    batch_budget: int
    drain_queue: bool
    cache_only: bool
    scan_round: str = FormulaScanRound.CACHED_RECOGNITION.value


class FormulaScanPolicy:
    """Choose formula OCR budgets without hurting reading performance."""

    def __init__(
        self,
        viewport_budget: int = 2,
        evidence_budget: int = 4,
        user_action_budget: int = 6,
        background_budget: int = 8,
        high_precision_budget: int = 16,
        large_doc_pages: int = 300,
    ) -> None:
        self.viewport_budget = viewport_budget
        self.evidence_budget = evidence_budget
        self.user_action_budget = user_action_budget
        self.background_budget = background_budget
        self.high_precision_budget = high_precision_budget
        self.large_doc_pages = large_doc_pages

    def make_plan(
        self,
        blocks: list[DocumentBlock],
        trigger: FormulaScanTrigger,
        priority_pages: set[int],
        page_count: int,
    ) -> FormulaScanPlan:
        pending = self._pending_formula_blocks(blocks)
        pending.sort(
            key=lambda block: self.priority_key(block, priority_pages),
            reverse=True,
        )
        batch_budget = self._batch_budget(trigger, page_count)
        return FormulaScanPlan(
            blocks=pending,
            priority_pages=priority_pages,
            batch_budget=batch_budget,
            drain_queue=trigger is FormulaScanTrigger.HIGH_PRECISION,
            cache_only=trigger is not FormulaScanTrigger.HIGH_PRECISION,
            scan_round=self._scan_round(trigger),
        )

    def _batch_budget(self, trigger: FormulaScanTrigger, page_count: int) -> int:
        if trigger is FormulaScanTrigger.VIEWPORT:
            return self.viewport_budget
        if trigger is FormulaScanTrigger.EVIDENCE:
            return self.evidence_budget
        if trigger is FormulaScanTrigger.USER_ACTION:
            return self.user_action_budget
        if trigger is FormulaScanTrigger.HIGH_PRECISION:
            return self.high_precision_budget
        budget = self.background_budget
        if page_count >= self.large_doc_pages:
            budget = max(1, budget // 2)
        return budget

    @staticmethod
    def _scan_round(trigger: FormulaScanTrigger) -> str:
        if trigger is FormulaScanTrigger.HIGH_PRECISION:
            return FormulaScanRound.LOCAL_HIGH_PRECISION.value
        return FormulaScanRound.CACHED_RECOGNITION.value

    @staticmethod
    def _pending_formula_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
        return [
            block.model_copy(deep=True)
            for block in blocks
            if block.block_type == BlockType.FORMULA
            and block.metadata.get("needs_ocr")
            and not block.metadata.get("mfr_recognized")
        ]

    @staticmethod
    def priority_key(block: DocumentBlock, priority_pages: set[int]) -> tuple[int, float, float, int]:
        page_boost = 1 if block.page_num in priority_pages else 0
        formula_score = float(block.metadata.get("formula_score", 0.0) or 0.0)
        area = max((block.bbox[2] - block.bbox[0]) * (block.bbox[3] - block.bbox[1]), 0.0)
        return (page_boost, formula_score, area, -block.page_num)


class FormulaIndexScheduler:
    """Build scan plans from document blocks and UI context."""

    def __init__(self, policy: FormulaScanPolicy | None = None) -> None:
        self._policy = policy or FormulaScanPolicy()

    def plan_for_pages(
        self,
        blocks: list[DocumentBlock],
        pages: set[int],
        trigger: FormulaScanTrigger,
        page_count: int,
    ) -> FormulaScanPlan:
        if pages:
            scoped = [
                block for block in blocks
                if block.page_num in pages
            ]
        else:
            scoped = list(blocks)
        return self._policy.make_plan(scoped, trigger, pages, page_count)

    def plan_for_evidence(
        self,
        blocks: list[DocumentBlock],
        evidence: list[dict[str, object]],
        page_count: int,
    ) -> FormulaScanPlan:
        pages: set[int] = set()
        for item in evidence:
            page = item.get("page")
            try:
                pages.add(max(0, int(page) - 1))
            except (TypeError, ValueError):
                continue
        return self.plan_for_pages(blocks, pages, FormulaScanTrigger.EVIDENCE, page_count)

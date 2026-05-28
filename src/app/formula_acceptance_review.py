"""Service API for audited formula acceptance review."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from src.app.formula_index_store import FormulaIndexStore


class FormulaAcceptanceReviewService:
    """Small facade shared by CLI and UI review entry points."""

    def __init__(self, store: FormulaIndexStore) -> None:
        self._store = store

    def list_results(
        self,
        doc_hash: str,
        *,
        candidate_id: str = "",
        stage: str = "",
        accepted: bool | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        records = self._store.list_recognition_results(
            doc_hash,
            candidate_id=candidate_id or None,
            stage=stage or None,
            accepted=accepted,
            limit=limit,
        )
        return {
            "doc_hash": doc_hash,
            "count": len(records),
            "results": [_record_json(record) for record in records],
        }

    def list_ready_fusion(
        self,
        doc_hash: str,
        *,
        candidate_id: str = "",
        decision: str = "ready_for_manual_accept",
        limit: int = 50,
    ) -> dict[str, Any]:
        records = self._store.list_fusion_records(
            doc_hash,
            candidate_id=candidate_id or None,
            decision=decision or None,
            limit=limit,
        )
        return {
            "doc_hash": doc_hash,
            "count": len(records),
            "fusion_records": [_record_json(record) for record in records],
        }

    def accept_result(
        self,
        doc_hash: str,
        *,
        result_id: str,
        filepath: str = "",
        source: str = "manual",
        decider: str = "",
        reason: str = "",
        payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        decision = self._store.accept_recognition_result(
            doc_hash=doc_hash,
            result_id=result_id,
            filepath=filepath,
            decision_source=source,
            decider=decider,
            reason=reason,
            payload=payload or {},
        )
        return {"decision": _record_json(decision)}

    def reject_result(
        self,
        doc_hash: str,
        *,
        result_id: str,
        source: str = "manual",
        decider: str = "",
        reason: str = "",
        payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        decision = self._store.reject_recognition_result(
            doc_hash=doc_hash,
            result_id=result_id,
            decision_source=source,
            decider=decider,
            reason=reason,
            payload=payload or {},
        )
        return {"decision": _record_json(decision)}

    def accept_fusion(
        self,
        doc_hash: str,
        *,
        fusion_id: str,
        filepath: str = "",
        source: str = "manual_fusion_review",
        decider: str = "",
        reason: str = "",
        allow_not_ready: bool = False,
    ) -> dict[str, Any]:
        decision = self._store.accept_fusion_record(
            doc_hash=doc_hash,
            fusion_id=fusion_id,
            filepath=filepath,
            decision_source=source,
            decider=decider,
            reason=reason,
            allow_not_ready=allow_not_ready,
        )
        return {"decision": _record_json(decision)}

    def list_decisions(
        self,
        doc_hash: str,
        *,
        candidate_id: str = "",
        result_id: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        decisions = self._store.list_acceptance_decisions(
            doc_hash,
            candidate_id=candidate_id or None,
            result_id=result_id or None,
            limit=limit,
        )
        return {
            "doc_hash": doc_hash,
            "count": len(decisions),
            "decisions": [_record_json(decision) for decision in decisions],
        }


def _record_json(record: Any) -> dict[str, Any]:
    return asdict(record)

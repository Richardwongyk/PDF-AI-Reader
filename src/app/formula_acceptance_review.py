"""Service API for audited formula acceptance review."""

from __future__ import annotations

import hashlib
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

    def revise_result(
        self,
        doc_hash: str,
        *,
        result_id: str,
        revised_latex: str,
        filepath: str = "",
        source: str = "manual_revision",
        decider: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        source_record = self._store.get_recognition_result_by_id(result_id)
        if source_record is None or source_record.doc_hash != doc_hash:
            raise ValueError(f"formula recognition result not found: {result_id}")
        latex = str(revised_latex or "").strip()
        if not latex:
            raise ValueError("revised_latex is required")
        revision_input_hash = _revision_input_hash(
            doc_hash=doc_hash,
            candidate_id=source_record.candidate_id,
            source_id=result_id,
            revised_latex=latex,
        )
        revision_result_id = self._store.put_recognition_result(
            doc_hash=doc_hash,
            candidate_id=source_record.candidate_id,
            stage="manual_revision",
            model="human_review",
            model_version="manual_revision_v1",
            preprocess_version=str(source_record.result_id),
            input_hash=revision_input_hash,
            latex=latex,
            normalized_latex=latex,
            score=1.0,
            warnings=[],
            evidence={
                **source_record.evidence,
                "source": "manual_revision",
                "source_result_id": source_record.result_id,
                "source_stage": source_record.stage,
                "source_model": source_record.model,
            },
            accepted=False,
        )
        decision = self._store.accept_recognition_result(
            doc_hash=doc_hash,
            result_id=revision_result_id,
            filepath=filepath,
            decision_source=source,
            decider=decider,
            reason=reason,
            payload={
                "manual_revision": True,
                "source_result_id": source_record.result_id,
                "source_stage": source_record.stage,
            },
        )
        return {
            "revision_result_id": revision_result_id,
            "decision": _record_json(decision),
        }

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

    def revise_fusion(
        self,
        doc_hash: str,
        *,
        fusion_id: str,
        revised_latex: str,
        filepath: str = "",
        source: str = "manual_revision_fusion",
        decider: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        fusion = self._store.get_fusion_record_by_id(fusion_id)
        if fusion is None or fusion.doc_hash != doc_hash:
            raise ValueError(f"formula fusion record not found: {fusion_id}")
        latex = str(revised_latex or "").strip()
        if not latex:
            raise ValueError("revised_latex is required")
        revision_input_hash = _revision_input_hash(
            doc_hash=doc_hash,
            candidate_id=fusion.candidate_id,
            source_id=fusion.fusion_id,
            revised_latex=latex,
        )
        evidence = _fusion_revision_evidence(fusion.result_json)
        revision_result_id = self._store.put_recognition_result(
            doc_hash=doc_hash,
            candidate_id=fusion.candidate_id,
            stage="manual_revision",
            model="human_review",
            model_version="manual_revision_v1",
            preprocess_version=fusion.fusion_id,
            input_hash=revision_input_hash,
            latex=latex,
            normalized_latex=latex,
            score=1.0,
            warnings=list(fusion.risk_flags),
            evidence={
                **evidence,
                "source": "manual_revision_fusion",
                "fusion_id": fusion.fusion_id,
                "fusion_version": fusion.fusion_version,
                "fusion_decision": fusion.decision,
                "fusion_best_result_id": fusion.best_result_id,
            },
            accepted=False,
        )
        decision = self._store.accept_recognition_result(
            doc_hash=doc_hash,
            result_id=revision_result_id,
            filepath=filepath,
            decision_source=source,
            decider=decider,
            reason=reason,
            payload={
                "manual_revision": True,
                "fusion_id": fusion.fusion_id,
                "fusion_version": fusion.fusion_version,
                "fusion_decision": fusion.decision,
            },
        )
        return {
            "revision_result_id": revision_result_id,
            "decision": _record_json(decision),
        }

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


def _revision_input_hash(
    *,
    doc_hash: str,
    candidate_id: str,
    source_id: str,
    revised_latex: str,
) -> str:
    digest = hashlib.sha256()
    for value in (
        "formula_manual_revision_v1",
        doc_hash,
        candidate_id,
        source_id,
        revised_latex,
    ):
        digest.update(str(value).encode("utf-8", errors="ignore"))
        digest.update(b"\0")
    return digest.hexdigest()


def _fusion_revision_evidence(payload: dict[str, object]) -> dict[str, object]:
    ranked = payload.get("ranked_candidates", [])
    best = ranked[0] if isinstance(ranked, list) and ranked else {}
    if not isinstance(best, dict):
        return {}
    evidence = best.get("evidence", {})
    return dict(evidence) if isinstance(evidence, dict) else {}

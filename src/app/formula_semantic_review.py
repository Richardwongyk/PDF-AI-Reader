"""Cloud semantic review for formula candidates.

This module consumes the persisted ``r3_cloud_semantic_review`` queue and writes
review candidates back to ``FormulaIndexStore``. It deliberately does not
replace ``DocumentBlock.content``; accepted writeback needs a separate evidence
gate.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from src.app.formula_index_store import FormulaIndexStore, FormulaRoundRecord, FormulaScanRound
from src.core.ai_engine import BaseLLMClient
from src.core.models import BlockType, DocumentBlock, is_math_wrapped, wrap_math_text


@dataclass(frozen=True)
class FormulaSemanticReviewResult:
    """Structured cloud review candidate."""

    target_id: str
    suggested_latex: str
    should_replace: bool
    confidence: float
    reason: str
    risks: list[str]
    raw_response: str
    input_hash: str = ""
    model: str = ""
    model_version: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "suggested_latex": self.suggested_latex,
            "should_replace": self.should_replace,
            "confidence": self.confidence,
            "reason": self.reason,
            "risks": self.risks,
            "raw_response": self.raw_response,
            "input_hash": self.input_hash,
            "model": self.model,
            "model_version": self.model_version,
            "stage": FormulaScanRound.CLOUD_SEMANTIC_REVIEW.value,
        }


class FormulaSemanticReviewService:
    """Review queued formula blocks with a reasoning LLM."""

    def __init__(
        self,
        store: FormulaIndexStore,
        client: BaseLLMClient,
        batch_size: int = 4,
        timeout_sec: int = 90,
    ) -> None:
        self._store = store
        self._client = client
        self._batch_size = max(1, int(batch_size))
        self._timeout_sec = max(5, int(timeout_sec))

    def pending_count(self, doc_hash: str) -> int:
        return self._store.round_pending_count(
            doc_hash,
            scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        )

    def run_batch(
        self,
        doc_hash: str,
        blocks: list[DocumentBlock],
        limit: int | None = None,
    ) -> dict[str, int]:
        """Run one bounded semantic-review batch.

        Returns status counts for this call. The caller decides when to schedule
        the next batch, so this method never drains the whole document by
        default.
        """
        block_map = {block.id: block for block in blocks}
        batch_limit = self._batch_size if limit is None else max(0, int(limit))
        if not doc_hash or batch_limit <= 0:
            return {"done": 0, "failed": 0, "skipped": 0}
        records = self._store.list_round_records(
            doc_hash,
            statuses={"queued"},
            scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
            limit=batch_limit,
        )
        if not records:
            return {"done": 0, "failed": 0, "skipped": 0}
        self._store.mark_round_running(
            doc_hash,
            FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
            "block",
            [record.target_id for record in records],
        )
        counts = {"done": 0, "failed": 0, "skipped": 0}
        for record in records:
            block = block_map.get(record.target_id) or _block_from_record_payload(record)
            started = time.perf_counter()
            if block is None or block.block_type != BlockType.FORMULA:
                self._store.mark_round_failed(
                    doc_hash,
                    FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
                    "block",
                    record.target_id,
                    "missing_formula_block",
                    status="skipped",
                )
                counts["skipped"] += 1
                continue
            try:
                result = self.review_block(record, block)
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._store.mark_round_done(
                    doc_hash,
                    FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
                    "block",
                    record.target_id,
                    _merge_review_payload(record.result_json, result.to_json()),
                    elapsed_ms=elapsed_ms,
                )
                counts["done"] += 1
            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                self._store.mark_round_failed(
                    doc_hash,
                    FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
                    "block",
                    record.target_id,
                    _review_error_message(exc),
                    elapsed_ms=elapsed_ms,
                )
                counts["failed"] += 1
        return counts

    def review_block(
        self,
        record: FormulaRoundRecord,
        block: DocumentBlock,
    ) -> FormulaSemanticReviewResult:
        candidates = self._candidate_records_for_block(record.doc_hash, record.target_id)
        fusion_records = self._fusion_records_for_block(record.doc_hash, record.target_id)
        messages = self._build_messages(
            block,
            candidates=candidates,
            fusion_records=fusion_records,
        )
        raw = self._client.generate(
            messages,
            temperature=0,
            max_tokens=700,
            timeout=self._timeout_sec,
        )
        parsed = _parse_review_json(raw)
        suggested = _normalize_suggested_latex(
            parsed.get("suggested_latex", ""),
            display=not _is_inline_review_target(block),
        )
        return FormulaSemanticReviewResult(
            target_id=record.target_id,
            suggested_latex=suggested,
            should_replace=bool(parsed.get("should_replace", False)),
            confidence=_bounded_float(parsed.get("confidence", 0.0)),
            reason=str(parsed.get("reason", "") or "").strip(),
            risks=_normalize_risks(parsed.get("risks", [])),
            raw_response=raw,
            input_hash=_review_input_hash(block, candidates, fusion_records),
            model=self._client.model_name,
            model_version=self._client.model_name,
        )

    def _candidate_records_for_block(self, doc_hash: str, candidate_id: str) -> list[dict[str, object]]:
        records = self._store.list_recognition_results(
            doc_hash,
            candidate_id=candidate_id,
            limit=20,
        )
        return [
            {
                "result_id": record.result_id,
                "stage": record.stage,
                "model": record.model,
                "model_version": record.model_version,
                "preprocess_version": record.preprocess_version,
                "input_hash": record.input_hash,
                "latex": record.latex,
                "score": record.score,
                "warnings": list(record.warnings),
                "accepted": record.accepted,
                "evidence": record.evidence,
            }
            for record in records
        ]

    def _fusion_records_for_block(self, doc_hash: str, candidate_id: str) -> list[dict[str, object]]:
        records = self._store.list_fusion_records(
            doc_hash,
            candidate_id=candidate_id,
            limit=5,
        )
        return [
            {
                "fusion_id": record.fusion_id,
                "fusion_version": record.fusion_version,
                "input_hash": record.input_hash,
                "best_result_id": record.best_result_id,
                "ranked_result_ids": list(record.ranked_result_ids),
                "coverage": record.coverage,
                "agreement_score": record.agreement_score,
                "source_similarity": record.source_similarity,
                "syntax_valid": record.syntax_valid,
                "risk_flags": list(record.risk_flags),
                "accepted_gate": record.accepted_gate,
                "decision": record.decision,
                "result_json": record.result_json,
            }
            for record in records
        ]

    def _build_messages(
        self,
        block: DocumentBlock,
        *,
        candidates: list[dict[str, object]] | None = None,
        fusion_records: list[dict[str, object]] | None = None,
    ) -> list[dict[str, str]]:
        context = {
            "block_id": block.id,
            "page": block.page_num + 1,
            "content": block.content,
            "bbox": list(block.bbox),
            "section_title": block.section_title,
            "metadata": _semantic_metadata_summary(block.metadata),
            "recognition_candidates": _semantic_candidate_summaries(candidates or []),
            "fusion_records": _semantic_fusion_summaries(fusion_records or []),
            "instructions": {
                "goal": "produce a faithful LaTeX candidate from the provided PDF/fusion/tool evidence",
                "output_delimiter": "display formulas must use $$...$$; inline formulas must use \\(...\\)",
                "candidate_only": True,
                "no_source_assumption": "do not assume access to original TeX source; use only this evidence",
                "insufficient_evidence": "return suggested_latex='' and should_replace=false when evidence is not enough",
            },
        }
        return [
            {
                "role": "system",
                "content": (
                    "你是数学公式 LaTeX 语义复核器。你只根据给定 PDF glyph/bbox/font、"
                    "本地工具候选和 fusion 证据提出候选 LaTeX，不访问也不假设源 TeX。"
                    "你的输出只是候选 JSON，不会自动覆盖正文。只输出一个 JSON 对象，"
                    "不要输出 Markdown、解释文本或代码块。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "请复核下面公式块并尽量给出规范 LaTeX 候选。"
                    "必须保留数学语义、上下标、分式、根号、矩阵/多行结构和数学字体信息；"
                    "不要把普通正文补成公式。若证据不足，请 suggested_latex 为空且 should_replace=false。\n"
                    "JSON 字段必须包含 suggested_latex, should_replace, confidence, reason, risks。"
                    "suggested_latex 必须带数学定界符：行间 $$...$$，行内 \\(...\\)。\n"
                    f"公式块证据: {json.dumps(context, ensure_ascii=False)}"
                ),
            },
        ]


class FormulaSemanticReviewFlow(QObject):
    """Run bounded r3 formula semantic review batches off the UI thread."""

    review_finished = Signal(dict)

    def __init__(
        self,
        service_factory: Callable[[], FormulaSemanticReviewService],
        parent: QObject | None = None,
        store: FormulaIndexStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._service_factory = service_factory
        self._store = store or FormulaIndexStore()
        self._thread: _FormulaSemanticReviewWorker | None = None

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.isRunning())

    def pending_count(self, doc_hash: str) -> int:
        if not doc_hash:
            return 0
        return self._store.round_pending_count(
            doc_hash,
            scan_round=FormulaScanRound.CLOUD_SEMANTIC_REVIEW,
        )

    def start_batch(
        self,
        doc_hash: str,
        blocks: list[DocumentBlock],
        limit: int | None = None,
    ) -> bool:
        if not doc_hash or not blocks or self.is_running:
            return False
        if self.pending_count(doc_hash) <= 0:
            return False
        self._thread = _FormulaSemanticReviewWorker(
            service_factory=self._service_factory,
            doc_hash=doc_hash,
            blocks=blocks,
            limit=limit,
        )
        self._thread.finished_signal.connect(self._on_worker_finished)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._on_worker_thread_done)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._thread and self._thread.isRunning():
            self._thread.requestInterruption()
            self._thread.quit()
            self._thread.wait(1500)
        self._thread = None

    def _on_worker_finished(self, result: dict[str, object]) -> None:
        self.review_finished.emit(result)

    def _on_worker_thread_done(self) -> None:
        self._thread = None


class _FormulaSemanticReviewWorker(QThread):
    """Consume one persisted r3 queue batch in a background thread."""

    finished_signal = Signal(dict)

    def __init__(
        self,
        service_factory: Callable[[], FormulaSemanticReviewService],
        doc_hash: str,
        blocks: list[DocumentBlock],
        limit: int | None = None,
    ) -> None:
        super().__init__()
        self._service_factory = service_factory
        self._doc_hash = doc_hash
        self._blocks = [block.model_copy(deep=True) for block in blocks]
        self._limit = limit

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                self.finished_signal.emit({
                    "doc_hash": self._doc_hash,
                    "done": 0,
                    "failed": 0,
                    "skipped": 0,
                    "pending": 0,
                })
                return
            service = self._service_factory()
            counts = service.run_batch(self._doc_hash, self._blocks, limit=self._limit)
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                **counts,
                "pending": service.pending_count(self._doc_hash),
            })
        except Exception as exc:
            self.finished_signal.emit({
                "doc_hash": self._doc_hash,
                "done": 0,
                "failed": 1,
                "skipped": 0,
                "pending": 0,
                "error": str(exc),
            })


def _parse_review_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise FormulaSemanticReviewParseError(raw)
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise FormulaSemanticReviewParseError(raw) from exc
    if not isinstance(value, dict):
        raise ValueError("semantic review response must be a JSON object")
    return value


class FormulaSemanticReviewParseError(ValueError):
    def __init__(self, raw: str) -> None:
        self.raw = str(raw or "")
        super().__init__("semantic review response is not JSON")


def _review_error_message(exc: Exception) -> str:
    if isinstance(exc, FormulaSemanticReviewParseError):
        raw = " ".join(exc.raw.split())
        return f"{exc}; raw_response_excerpt={raw[:360]}"
    return str(exc)


def _merge_review_payload(existing: dict[str, object], result: dict[str, object]) -> dict[str, object]:
    merged = dict(result)
    for key in (
        "fusion_version",
        "best_result_id",
        "decision",
        "candidate_count",
        "review_priority",
        "review_priority_reason",
        "review_candidate",
    ):
        if key in existing and key not in merged:
            merged[key] = existing[key]
    pending_hash = str(existing.get("input_hash", "") or "")
    result_hash = str(result.get("input_hash", "") or "")
    if pending_hash:
        merged["queued_input_hash"] = pending_hash
    if result_hash:
        merged["review_input_hash"] = result_hash
    return merged


def _review_input_hash(
    block: DocumentBlock,
    candidates: list[dict[str, object]],
    fusion_records: list[dict[str, object]],
) -> str:
    payload = {
        "block_id": block.id,
        "page_num": block.page_num,
        "bbox": [round(float(value), 3) for value in block.bbox],
        "content": block.content,
        "metadata": block.metadata,
        "recognition_candidates": candidates,
        "fusion_records": fusion_records,
    }
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8", errors="ignore")).hexdigest()


def _block_from_record_payload(record: FormulaRoundRecord) -> DocumentBlock | None:
    payload = record.result_json
    candidate = payload.get("review_candidate")
    if not isinstance(candidate, dict):
        return None
    latex = str(candidate.get("latex", "") or "").strip()
    if not latex:
        return None
    bbox_value = candidate.get("bbox", (0, 0, 0, 0))
    if not isinstance(bbox_value, (list, tuple)) or len(bbox_value) != 4:
        bbox = (0.0, 0.0, 0.0, 0.0)
    else:
        try:
            bbox = tuple(float(value) for value in bbox_value)
        except (TypeError, ValueError):
            bbox = (0.0, 0.0, 0.0, 0.0)
    page_value = candidate.get("page_num", record.page_num)
    try:
        page_num = int(page_value)
    except (TypeError, ValueError):
        page_num = record.page_num
    return DocumentBlock(
        id=record.target_id,
        page_num=page_num,
        block_type=BlockType.FORMULA,
        content=latex,
        bbox=bbox,  # type: ignore[arg-type]
        metadata={
            "source": str(candidate.get("source", "formula_round_payload") or "formula_round_payload"),
            "fusion_input_hash": str(payload.get("input_hash", "") or ""),
            "fusion_decision": str(payload.get("decision", "") or ""),
            "source_block_id": str(candidate.get("source_block_id", "") or ""),
            "source_context": str(candidate.get("source_context", "") or ""),
            "inline_pdf_evidence": candidate.get("inline_pdf_evidence", {})
            if isinstance(candidate.get("inline_pdf_evidence"), dict) else {},
            "candidate_only": True,
        },
    )


def _semantic_metadata_summary(metadata: dict[str, object]) -> dict[str, object]:
    allowed = {
        "source",
        "needs_ocr",
        "confidence",
        "evidence",
        "warnings",
        "line_count",
        "vector_count",
        "formula_score",
        "candidate_only",
        "fusion_decision",
        "fusion_input_hash",
        "source_block_id",
        "source_context",
        "inline_pdf_evidence",
        "born_digital_diagnostics",
    }
    summary: dict[str, object] = {}
    for key in allowed:
        if key not in metadata:
            continue
        value = metadata[key]
        if key == "born_digital_diagnostics" and isinstance(value, dict):
            summary[key] = {
                name: value.get(name)
                for name in (
                    "classification",
                    "risks",
                    "evidence",
                    "math_density",
                    "math_glyph_count",
                    "operator_count",
                    "digit_count",
                    "line_count",
                    "vector_count",
                )
                if name in value
            }
        else:
            summary[key] = value
    return summary


def _semantic_candidate_summaries(candidates: list[dict[str, object]]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for item in candidates[:12]:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence")
        evidence_summary: dict[str, object] = {}
        if isinstance(evidence, dict):
            evidence_summary = {
                key: evidence.get(key)
                for key in (
                    "page_num",
                    "bbox",
                    "source",
                    "text",
                    "diagnostics",
                    "block_id",
                    "source_context",
                    "inline_pdf_evidence",
                )
                if key in evidence
            }
            details = evidence.get("details")
            if isinstance(details, dict) and "diagnostics" in details:
                evidence_summary["diagnostics"] = details.get("diagnostics")
        summaries.append({
            "result_id": item.get("result_id", ""),
            "stage": item.get("stage", ""),
            "model": item.get("model", ""),
            "model_version": item.get("model_version", ""),
            "preprocess_version": item.get("preprocess_version", ""),
            "input_hash": item.get("input_hash", ""),
            "latex": item.get("latex", ""),
            "score": item.get("score"),
            "warnings": item.get("warnings", []),
            "accepted": bool(item.get("accepted", False)),
            "evidence": evidence_summary,
        })
    return summaries


def _semantic_fusion_summaries(fusion_records: list[dict[str, object]]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for item in fusion_records[:5]:
        if not isinstance(item, dict):
            continue
        result = item.get("result_json")
        result_json = result if isinstance(result, dict) else {}
        ranked = result_json.get("ranked_candidates", [])
        ranked_summary: list[dict[str, object]] = []
        if isinstance(ranked, list):
            for candidate in ranked[:6]:
                if not isinstance(candidate, dict):
                    continue
                ranked_summary.append({
                    "stage": candidate.get("stage", ""),
                    "model": candidate.get("model", ""),
                    "latex": candidate.get("latex", ""),
                    "source_similarity": candidate.get("source_similarity"),
                    "score": candidate.get("score"),
                    "warnings": candidate.get("warnings", []),
                    "evidence": _semantic_inline_evidence_summary(candidate.get("evidence")),
                })
        summaries.append({
            "fusion_version": item.get("fusion_version", ""),
            "input_hash": item.get("input_hash", ""),
            "decision": item.get("decision", ""),
            "coverage": item.get("coverage"),
            "agreement_score": item.get("agreement_score"),
            "source_similarity": item.get("source_similarity"),
            "syntax_valid": item.get("syntax_valid"),
            "risk_flags": item.get("risk_flags", []),
            "accepted_gate": item.get("accepted_gate", {}),
            "best_latex": result_json.get("best_latex", ""),
            "best_stage": result_json.get("best_stage", ""),
            "best_model": result_json.get("best_model", ""),
            "stage_quality": result_json.get("stage_quality", {}),
            "ranked_candidates": ranked_summary,
        })
    return summaries


def _semantic_inline_evidence_summary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    inline = value.get("inline_pdf_evidence")
    if not isinstance(inline, dict):
        return {}
    return {
        key: inline.get(key)
        for key in (
            "source",
            "fonts",
            "span_count",
            "has_script_size",
            "font_size_min",
            "font_size_max",
            "bbox",
            "spans",
        )
        if key in inline
    }


def _normalize_suggested_latex(value: object, *, display: bool) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if is_math_wrapped(text):
        return text
    return wrap_math_text(text, display=display)


def _is_inline_review_target(block: DocumentBlock) -> bool:
    source = str(block.metadata.get("source", "") or "")
    if "inline" in source:
        return True
    if str(block.id).lower().find("inline") >= 0:
        return True
    text = str(block.content or "").strip()
    return bool(text and "\n" not in text and len(text) <= 80 and not text.startswith("$$"))


def _bounded_float(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _normalize_risks(value: object) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []

"""Review and accept/reject persisted formula recognition candidates.

This CLI is deliberately small: it only changes accepted flags through
FormulaIndexStore, records an audit event, and optionally queues r5 knowledge
incremental update work for accepted results.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.app.formula_index_store import FormulaIndexStore


def _record_json(record: Any) -> dict[str, Any]:
    return asdict(record)


def _write_json(payload: dict[str, Any], output: str = "") -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(text + "\n", encoding="utf-8")
        return
    print(text)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit formula recognition candidates and gate accepted r5 updates.",
    )
    parser.add_argument(
        "--db",
        default="data/formula_index_jobs.db",
        help="Formula index SQLite database.",
    )
    parser.add_argument(
        "--doc-hash",
        required=True,
        help="Document hash whose formula candidates should be reviewed.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON output path. Defaults to stdout.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List recognition results.")
    list_parser.add_argument("--candidate-id", default="", help="Optional candidate/block id.")
    list_parser.add_argument("--stage", default="", help="Optional recognition stage.")
    list_parser.add_argument("--accepted-only", action="store_true", help="Only show accepted results.")
    list_parser.add_argument("--unaccepted-only", action="store_true", help="Only show unaccepted results.")
    list_parser.add_argument("--limit", type=int, default=50)

    ready_parser = subparsers.add_parser("ready", help="List fusion records ready for manual acceptance.")
    ready_parser.add_argument("--candidate-id", default="", help="Optional candidate/block id.")
    ready_parser.add_argument("--decision", default="ready_for_manual_accept", help="Fusion decision to list.")
    ready_parser.add_argument("--limit", type=int, default=50)

    accept_parser = subparsers.add_parser("accept", help="Accept one result and queue r5 when filepath is known.")
    accept_parser.add_argument("--result-id", required=True)
    accept_parser.add_argument("--filepath", default="", help="Source PDF path used for the r5 round record.")
    accept_parser.add_argument("--source", default="manual_cli", help="Decision source label.")
    accept_parser.add_argument("--decider", default="", help="Person or process making the decision.")
    accept_parser.add_argument("--reason", default="", help="Short audit reason.")

    reject_parser = subparsers.add_parser("reject", help="Reject one result and clear its accepted flag.")
    reject_parser.add_argument("--result-id", required=True)
    reject_parser.add_argument("--source", default="manual_cli", help="Decision source label.")
    reject_parser.add_argument("--decider", default="", help="Person or process making the decision.")
    reject_parser.add_argument("--reason", default="", help="Short audit reason.")

    accept_fusion_parser = subparsers.add_parser(
        "accept-fusion",
        help="Accept one persisted fusion record and queue r5 when filepath is known.",
    )
    accept_fusion_parser.add_argument("--fusion-id", required=True)
    accept_fusion_parser.add_argument("--filepath", default="", help="Source PDF path used for the r5 round record.")
    accept_fusion_parser.add_argument("--source", default="manual_cli_fusion", help="Decision source label.")
    accept_fusion_parser.add_argument("--decider", default="", help="Person or process making the decision.")
    accept_fusion_parser.add_argument("--reason", default="", help="Short audit reason.")
    accept_fusion_parser.add_argument(
        "--allow-not-ready",
        action="store_true",
        help="Allow accepting fusion records whose decision is not ready_for_manual_accept.",
    )

    decisions_parser = subparsers.add_parser("decisions", help="List acceptance audit events.")
    decisions_parser.add_argument("--candidate-id", default="", help="Optional candidate/block id.")
    decisions_parser.add_argument("--result-id", default="", help="Optional result id.")
    decisions_parser.add_argument("--limit", type=int, default=50)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = FormulaIndexStore(str(args.db))
    try:
        if args.command == "list":
            accepted: bool | None = None
            if args.accepted_only and args.unaccepted_only:
                raise SystemExit("--accepted-only and --unaccepted-only are mutually exclusive")
            if args.accepted_only:
                accepted = True
            elif args.unaccepted_only:
                accepted = False
            records = store.list_recognition_results(
                args.doc_hash,
                candidate_id=args.candidate_id or None,
                stage=args.stage or None,
                accepted=accepted,
                limit=args.limit,
            )
            _write_json(
                {
                    "doc_hash": args.doc_hash,
                    "count": len(records),
                    "results": [_record_json(record) for record in records],
                },
                args.output,
            )
            return 0

        if args.command == "ready":
            records = store.list_fusion_records(
                args.doc_hash,
                candidate_id=args.candidate_id or None,
                decision=args.decision or None,
                limit=args.limit,
            )
            _write_json(
                {
                    "doc_hash": args.doc_hash,
                    "count": len(records),
                    "fusion_records": [_record_json(record) for record in records],
                },
                args.output,
            )
            return 0

        if args.command == "accept":
            decision = store.accept_recognition_result(
                doc_hash=args.doc_hash,
                result_id=args.result_id,
                filepath=args.filepath,
                decision_source=args.source,
                decider=args.decider,
                reason=args.reason,
                payload={"cli": "tools/formula_acceptance_review.py"},
            )
            _write_json({"decision": _record_json(decision)}, args.output)
            return 0

        if args.command == "accept-fusion":
            decision = store.accept_fusion_record(
                doc_hash=args.doc_hash,
                fusion_id=args.fusion_id,
                filepath=args.filepath,
                decision_source=args.source,
                decider=args.decider,
                reason=args.reason,
                allow_not_ready=args.allow_not_ready,
            )
            _write_json({"decision": _record_json(decision)}, args.output)
            return 0

        if args.command == "reject":
            decision = store.reject_recognition_result(
                doc_hash=args.doc_hash,
                result_id=args.result_id,
                decision_source=args.source,
                decider=args.decider,
                reason=args.reason,
                payload={"cli": "tools/formula_acceptance_review.py"},
            )
            _write_json({"decision": _record_json(decision)}, args.output)
            return 0

        if args.command == "decisions":
            decisions = store.list_acceptance_decisions(
                args.doc_hash,
                candidate_id=args.candidate_id or None,
                result_id=args.result_id or None,
                limit=args.limit,
            )
            _write_json(
                {
                    "doc_hash": args.doc_hash,
                    "count": len(decisions),
                    "decisions": [_record_json(decision) for decision in decisions],
                },
                args.output,
            )
            return 0
    finally:
        store.close()
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

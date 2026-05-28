"""Review and accept/reject persisted formula recognition candidates.

This CLI is deliberately small: it only changes accepted flags through
FormulaIndexStore, records an audit event, and optionally queues r5 knowledge
incremental update work for accepted results.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.app.formula_acceptance_review import FormulaAcceptanceReviewService
from src.app.formula_index_store import FormulaIndexStore


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

    revise_parser = subparsers.add_parser("revise", help="Accept a manual LaTeX revision for one result.")
    revise_parser.add_argument("--result-id", required=True)
    revise_parser.add_argument("--latex", required=True, help="Reviewer supplied LaTeX.")
    revise_parser.add_argument("--filepath", default="", help="Source PDF path used for the r5 round record.")
    revise_parser.add_argument("--source", default="manual_cli_revision", help="Decision source label.")
    revise_parser.add_argument("--decider", default="", help="Person or process making the decision.")
    revise_parser.add_argument("--reason", default="", help="Short audit reason.")

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

    revise_fusion_parser = subparsers.add_parser(
        "revise-fusion",
        help="Accept a manual LaTeX revision for one fusion record.",
    )
    revise_fusion_parser.add_argument("--fusion-id", required=True)
    revise_fusion_parser.add_argument("--latex", required=True, help="Reviewer supplied LaTeX.")
    revise_fusion_parser.add_argument("--filepath", default="", help="Source PDF path used for the r5 round record.")
    revise_fusion_parser.add_argument("--source", default="manual_cli_revision_fusion", help="Decision source label.")
    revise_fusion_parser.add_argument("--decider", default="", help="Person or process making the decision.")
    revise_fusion_parser.add_argument("--reason", default="", help="Short audit reason.")

    decisions_parser = subparsers.add_parser("decisions", help="List acceptance audit events.")
    decisions_parser.add_argument("--candidate-id", default="", help="Optional candidate/block id.")
    decisions_parser.add_argument("--result-id", default="", help="Optional result id.")
    decisions_parser.add_argument("--limit", type=int, default=50)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    store = FormulaIndexStore(str(args.db))
    service = FormulaAcceptanceReviewService(store)
    try:
        if args.command == "list":
            accepted: bool | None = None
            if args.accepted_only and args.unaccepted_only:
                raise SystemExit("--accepted-only and --unaccepted-only are mutually exclusive")
            if args.accepted_only:
                accepted = True
            elif args.unaccepted_only:
                accepted = False
            _write_json(
                service.list_results(
                    args.doc_hash,
                    candidate_id=args.candidate_id,
                    stage=args.stage,
                    accepted=accepted,
                    limit=args.limit,
                ),
                args.output,
            )
            return 0

        if args.command == "ready":
            _write_json(
                service.list_ready_fusion(
                    args.doc_hash,
                    candidate_id=args.candidate_id,
                    decision=args.decision,
                    limit=args.limit,
                ),
                args.output,
            )
            return 0

        if args.command == "accept":
            _write_json(
                service.accept_result(
                    args.doc_hash,
                    result_id=args.result_id,
                    filepath=args.filepath,
                    source=args.source,
                    decider=args.decider,
                    reason=args.reason,
                    payload={"cli": "tools/formula_acceptance_review.py"},
                ),
                args.output,
            )
            return 0

        if args.command == "accept-fusion":
            _write_json(
                service.accept_fusion(
                    args.doc_hash,
                    fusion_id=args.fusion_id,
                    filepath=args.filepath,
                    source=args.source,
                    decider=args.decider,
                    reason=args.reason,
                    allow_not_ready=args.allow_not_ready,
                ),
                args.output,
            )
            return 0

        if args.command == "revise":
            _write_json(
                service.revise_result(
                    args.doc_hash,
                    result_id=args.result_id,
                    revised_latex=args.latex,
                    filepath=args.filepath,
                    source=args.source,
                    decider=args.decider,
                    reason=args.reason,
                ),
                args.output,
            )
            return 0

        if args.command == "revise-fusion":
            _write_json(
                service.revise_fusion(
                    args.doc_hash,
                    fusion_id=args.fusion_id,
                    revised_latex=args.latex,
                    filepath=args.filepath,
                    source=args.source,
                    decider=args.decider,
                    reason=args.reason,
                ),
                args.output,
            )
            return 0

        if args.command == "reject":
            _write_json(
                service.reject_result(
                    args.doc_hash,
                    result_id=args.result_id,
                    source=args.source,
                    decider=args.decider,
                    reason=args.reason,
                    payload={"cli": "tools/formula_acceptance_review.py"},
                ),
                args.output,
            )
            return 0

        if args.command == "decisions":
            _write_json(
                service.list_decisions(
                    args.doc_hash,
                    candidate_id=args.candidate_id,
                    result_id=args.result_id,
                    limit=args.limit,
                ),
                args.output,
            )
            return 0
    finally:
        store.close()
    raise SystemExit(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

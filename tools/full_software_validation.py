"""Run broad validation gates for the PDF AI Reader project.

This entry point is intentionally a coordinator.  It does not implement PDF
parsing, formula recognition, RAG, or UI behavior itself; it runs the existing
focused tools and records one auditable report for a whole-software pass.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAIN_PYTHON = Path(r"C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe")
DEFAULT_TINYBDMATH_GRAPH_PARSER_MODEL = (
    ROOT
    / "test_artifacts"
    / "tinybdmath_graph_parser_m1"
    / "tinybdmath_graph_parser_model.json"
)


CORE_TESTS = [
    "tests/test_environment.py",
    "tests/test_smoke.py",
    "tests/test_ai_foundations.py",
    "tests/test_ask_flow.py",
    "tests/test_knowledge_backends.py",
    "tests/test_graph_index_store.py",
    "tests/test_graph_index_flow.py",
    "tests/test_latex_math_source_parser.py",
]

FORMULA_TESTS = [
    "tests/test_born_digital_math.py",
    "tests/test_external_formula_tools.py",
    "tests/test_formula_detector.py",
    "tests/test_formula_index_flow.py",
    "tests/test_formula_index_performance.py",
    "tests/test_formula_index_scheduler.py",
    "tests/test_formula_knowledge_graph.py",
    "tests/test_formula_knowledge_update.py",
    "tests/test_formula_multiround_pipeline.py",
    "tests/test_formula_semantic_review.py",
    "tests/test_formula_tool_comparison.py",
    "tests/test_latex_mathml_extractor.py",
    "tests/test_pdf_glyph_graph.py",
    "tests/test_symbol_identity_repair.py",
]

QUICK_TESTS = [
    "tests/test_environment.py",
    "tests/test_smoke.py",
    "tests/test_ask_flow.py",
    "tests/test_graph_index_flow.py",
    "tests/test_formula_index_flow.py",
    "tests/test_formula_multiround_pipeline.py",
    "tests/test_formula_semantic_review.py",
    "tests/test_tinybdmath_alignment.py",
    "tests/test_tinybdmath_alignment_audit.py",
    "tests/test_tinybdmath_candidate_service.py",
    "tests/test_tinybdmath_cslt_schema.py",
    "tests/test_tinybdmath_graph_parser.py",
    "tests/test_tinybdmath_symbol_equivalence.py",
    "tests/test_tinybdmath_target_tree.py",
]


@dataclass(frozen=True)
class ValidationStep:
    name: str
    category: str
    command: tuple[str, ...]
    timeout_sec: int
    required: bool = True
    output_files: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationOptions:
    profile: str
    case: str
    output_dir: Path
    python: Path
    max_pages: int = 0
    dry_run: bool = False
    fail_fast: bool = False
    include_desktop_e2e: bool = False
    include_cloud: bool = False
    include_local_tools: bool = False
    strict_logs: bool = False
    tinybdmath_graph_parser_model: Path | None = None
    stress_multiplier: int = 1


@dataclass
class StepResult:
    name: str
    category: str
    command: list[str]
    timeout_sec: int
    required: bool
    status: str
    exit_code: int | None
    elapsed_sec: float
    stdout_log: str | None = None
    stderr_log: str | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    output_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    artifact_checks: list[str] = field(default_factory=list)


def _existing(paths: Iterable[str]) -> list[str]:
    return [path for path in paths if (ROOT / path).exists()]


def _tinybdmath_tests() -> list[str]:
    return [str(path.relative_to(ROOT)) for path in sorted((ROOT / "tests").glob("test_tinybdmath_*.py"))]


def _python_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    if DEFAULT_MAIN_PYTHON.exists():
        return DEFAULT_MAIN_PYTHON
    return Path(sys.executable)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _cmd(python: Path, *args: object) -> tuple[str, ...]:
    return (str(python), *(str(arg) for arg in args))


def _pytest_step(
    name: str,
    python: Path,
    targets: Sequence[str],
    timeout_sec: int,
) -> ValidationStep:
    return ValidationStep(
        name=name,
        category="pytest",
        command=_cmd(python, "-m", "pytest", *targets, "-q"),
        timeout_sec=timeout_sec,
        notes=("repository unit/integration tests",),
    )


def _pytest_steps(profile: str, python: Path) -> list[ValidationStep]:
    if profile == "quick":
        return [_pytest_step("pytest_quick_core_formula", python, _existing(QUICK_TESTS), 180)]
    if profile in {"standard", "nightly"}:
        return [
            _pytest_step("pytest_core_rag_graph", python, _existing(CORE_TESTS), 240),
            _pytest_step("pytest_formula_pipeline", python, _existing(FORMULA_TESTS), 360),
            _pytest_step("pytest_tinybdmath_model_path", python, _tinybdmath_tests(), 420),
            _pytest_step("pytest_cloud_config_contracts", python, _existing(["tests/test_cloud_models.py"]), 120),
        ]
    return [
        ValidationStep(
            name="pytest_full_repository",
            category="pytest",
            command=_cmd(python, "-m", "pytest", "-q"),
            timeout_sec=900,
            notes=("all pytest tests under tests/",),
        )
    ]


def _page_budget(profile: str, requested: int, case_name: str, dense_slice: bool = False) -> int:
    if requested > 0:
        return requested
    if profile == "quick":
        return 2 if case_name == "attention" else 3
    if profile == "standard":
        return 4 if case_name == "attention" else (8 if dense_slice else 6)
    if profile == "nightly":
        return 12 if case_name == "attention" else (64 if dense_slice else 24)
    return 0


def _scale_positive(value: int, multiplier: int) -> int:
    if value <= 0:
        return value
    return max(1, int(value) * max(1, int(multiplier)))


def _selected_case_slices(
    case: str,
    profile: str,
    max_pages: int,
    stress_multiplier: int = 1,
) -> list[tuple[str, str, int, int]]:
    names = ["attention", "napkin"] if case == "all" else [case]
    slices: list[tuple[str, str, int, int]] = []
    for name in names:
        if name == "napkin" and profile in {"standard", "full", "nightly"}:
            slices.append((
                "napkin_front",
                "napkin",
                0,
                _scale_positive(
                    _page_budget(profile, max_pages, "napkin", dense_slice=False),
                    stress_multiplier,
                ),
            ))
            slices.append((
                "napkin_formula_dense",
                "napkin",
                8,
                _scale_positive(
                    _page_budget(profile, max_pages, "napkin", dense_slice=True),
                    stress_multiplier,
                ),
            ))
            continue
        slices.append((
            name,
            name,
            0,
            _scale_positive(_page_budget(profile, max_pages, name), stress_multiplier),
        ))
    return slices


def _case_arg(case: str) -> str:
    return "all" if case == "all" else case


def _formula_index_steps(options: ValidationOptions) -> list[ValidationStep]:
    steps: list[ValidationStep] = []
    for slice_name, case_name, start_page, pages in _selected_case_slices(
        options.case,
        options.profile,
        options.max_pages,
        options.stress_multiplier,
    ):
        out = options.output_dir / f"formula_index_{slice_name}.json"
        db = options.output_dir / f"formula_index_{slice_name}.db"
        command = _cmd(
            options.python,
            "tools/formula_index_performance.py",
            "--case",
            case_name,
            "--start-page",
            start_page,
            "--max-pages",
            max(1, pages) if pages > 0 else 0,
            "--db",
            _rel(db),
            "--output",
            _rel(out),
        )
        steps.append(ValidationStep(
            name=f"formula_index_performance_{slice_name}",
            category="formula_index",
            command=command,
            timeout_sec=300 if options.profile != "full" else 900,
            output_files=(_rel(out), _rel(db)),
            notes=("born-digital import path, no OCR/MFR",),
        ))
    return steps


def _formula_audit_steps(options: ValidationOptions) -> list[ValidationStep]:
    pages = options.max_pages
    if pages <= 0 and options.profile == "quick":
        pages = 3
    if pages <= 0 and options.profile == "standard":
        pages = 12
    pages = _scale_positive(pages, options.stress_multiplier)
    command: list[object] = [
        "tools/formula_latex_audit.py",
        "--case",
        _case_arg(options.case),
        "--output",
        _rel(options.output_dir / "formula_latex_audit.json"),
        "--born-digital-math",
        "--born-digital-semantics",
        "--no-legacy-formula-heuristic",
        "--match-scope",
        "all",
    ]
    if pages > 0:
        command.extend(["--max-pages", pages])
    if options.profile in {"full", "nightly"}:
        command.append("--quality-gate")
    return [
        ValidationStep(
            name="formula_latex_source_audit",
            category="formula_audit",
            command=_cmd(options.python, *command),
            timeout_sec=900 if options.profile in {"full", "nightly"} else 360,
            output_files=(_rel(options.output_dir / "formula_latex_audit.json"),),
            notes=("LaTeX source is used only as validation evidence",),
        )
    ]


def _multiround_steps(options: ValidationOptions) -> list[ValidationStep]:
    steps: list[ValidationStep] = []
    graph_parser_model = options.tinybdmath_graph_parser_model or DEFAULT_TINYBDMATH_GRAPH_PARSER_MODEL
    has_graph_parser_model = graph_parser_model.exists()
    for slice_name, case_name, start_page, pages in _selected_case_slices(
        options.case,
        options.profile,
        options.max_pages,
        options.stress_multiplier,
    ):
        formula_db = options.output_dir / f"multiround_{slice_name}.db"
        graph_db = options.output_dir / f"graph_{slice_name}.db"
        report = options.output_dir / f"multiround_{slice_name}.json"
        r2_limit = 0
        r2_sample_limit = 0
        r3_limit = 0
        r4_limit = 64 if options.profile in {"full", "nightly"} else 16
        r5_limit = 16 if options.profile in {"full", "nightly"} else 4
        if options.include_local_tools:
            r2_limit = _scale_positive(8, options.stress_multiplier)
            r2_sample_limit = r2_limit
        if options.include_cloud:
            r3_limit = _scale_positive(4, options.stress_multiplier)
        r4_limit = _scale_positive(r4_limit, options.stress_multiplier)
        r5_limit = _scale_positive(r5_limit, options.stress_multiplier)
        command: list[object] = [
            "tools/formula_multiround_pipeline.py",
            "--case",
            case_name,
            "--start-page",
            start_page,
            "--max-pages",
            max(1, pages) if pages > 0 else 0,
            "--r1-limit",
            0,
            "--r2-limit",
            r2_limit,
            "--r2-sample-formulas",
            r2_sample_limit,
            "--r3-limit",
            r3_limit,
            "--r4-limit",
            r4_limit,
            "--r5-limit",
            r5_limit,
            "--formula-db",
            _rel(formula_db),
            "--graph-db",
            _rel(graph_db),
            "--output",
            _rel(report),
            "--run-tinybdmath",
        ]
        notes = ["born-digital-first multiround pipeline", "TinyBDMath r2a uses Graph Parser as the main path"]
        if has_graph_parser_model:
            command.extend(["--tinybdmath-graph-parser-model", _rel(graph_parser_model)])
            notes.append("TinyBDMath Graph Parser model is loaded")
        else:
            notes.append("TinyBDMath r2a will abstain because Graph Parser artifact is absent")
        if options.include_local_tools:
            command.append("--auto-local-tools")
            notes.append("explicit local OCR/MFR tool comparison enabled")
        if options.include_cloud:
            command.append("--run-cloud-review")
            notes.append("explicit cloud semantic review enabled")
        steps.append(ValidationStep(
            name=f"formula_multiround_{slice_name}",
            category="formula_multiround",
            command=_cmd(options.python, *command),
            timeout_sec=900 if options.profile in {"full", "nightly"} else 420,
            output_files=(_rel(report), _rel(formula_db), _rel(graph_db)),
            notes=tuple(notes),
        ))
        if options.profile in {"standard", "full", "nightly"}:
            reuse = options.output_dir / f"multiround_{slice_name}_reuse.json"
            reuse_command = [*command]
            output_index = reuse_command.index("--output") + 1
            reuse_command[output_index] = _rel(reuse)
            reuse_command.append("--reuse-db")
            steps.append(ValidationStep(
                name=f"formula_multiround_reopen_skip_{slice_name}",
                category="formula_multiround",
                command=_cmd(options.python, *reuse_command),
                timeout_sec=900 if options.profile in {"full", "nightly"} else 420,
                output_files=(_rel(reuse), _rel(formula_db), _rel(graph_db)),
                notes=("second-open skip/reuse gate",),
            ))
    return steps


def _desktop_e2e_step(options: ValidationOptions) -> list[ValidationStep]:
    if not options.include_desktop_e2e and options.profile != "nightly":
        return []
    return [
        ValidationStep(
            name="desktop_e2e_workflow",
            category="desktop_e2e",
            command=_cmd(
                options.python,
                "tools/e2e_pdf_workflow.py",
                "--case",
                _case_arg(options.case),
                "--stress-multiplier",
                max(1, options.stress_multiplier),
            ),
            timeout_sec=1800,
            output_files=("test_artifacts/e2e/report.json",),
            notes=("opens the GUI and drives scroll/jump/zoom/translation/QA/log audit",),
        )
    ]


def _log_audit_step(options: ValidationOptions) -> list[ValidationStep]:
    out = options.output_dir / "log_audit.json"
    return [
        ValidationStep(
            name="log_audit",
            category="logs",
            command=_cmd(
                options.python,
                "tools/test_log_audit.py",
                "--tail-lines",
                6000,
                "--output",
                _rel(out),
            ),
            timeout_sec=60,
            required=options.strict_logs,
            output_files=(_rel(out),),
            notes=("required only with --strict-logs; existing logs can contain older warnings",),
        )
    ]


def build_plan(options: ValidationOptions) -> list[ValidationStep]:
    steps: list[ValidationStep] = []
    steps.extend(_pytest_steps(options.profile, options.python))
    steps.extend(_formula_index_steps(options))
    steps.extend(_formula_audit_steps(options))
    steps.extend(_multiround_steps(options))
    steps.extend(_desktop_e2e_step(options))
    steps.extend(_log_audit_step(options))
    return steps


def _tail(text: str, max_chars: int = 5000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _run_step(step: ValidationStep, output_dir: Path) -> StepResult:
    started = time.perf_counter()
    stdout_log = output_dir / "logs" / f"{step.name}.stdout.log"
    stderr_log = output_dir / "logs" / f"{step.name}.stderr.log"
    env = os.environ.copy()
    env.update(step.env)
    try:
        proc = subprocess.run(
            list(step.command),
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=step.timeout_sec,
            check=False,
        )
        elapsed = time.perf_counter() - started
        _write_text(stdout_log, proc.stdout)
        _write_text(stderr_log, proc.stderr)
        status = "passed" if proc.returncode == 0 else ("failed" if step.required else "warning")
        result = StepResult(
            name=step.name,
            category=step.category,
            command=list(step.command),
            timeout_sec=step.timeout_sec,
            required=step.required,
            status=status,
            exit_code=proc.returncode,
            elapsed_sec=round(elapsed, 3),
            stdout_log=_rel(stdout_log),
            stderr_log=_rel(stderr_log),
            stdout_tail=_tail(proc.stdout),
            stderr_tail=_tail(proc.stderr),
            output_files=list(step.output_files),
            notes=list(step.notes),
        )
        _apply_artifact_checks(step, result)
        return result
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - started
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        _write_text(stdout_log, str(stdout))
        _write_text(stderr_log, str(stderr))
        return StepResult(
            name=step.name,
            category=step.category,
            command=list(step.command),
            timeout_sec=step.timeout_sec,
            required=step.required,
            status="timeout" if step.required else "warning",
            exit_code=None,
            elapsed_sec=round(elapsed, 3),
            stdout_log=_rel(stdout_log),
            stderr_log=_rel(stderr_log),
            stdout_tail=_tail(str(stdout)),
            stderr_tail=_tail(str(stderr)),
            output_files=list(step.output_files),
            notes=[*step.notes, "process timed out"],
        )


def _planned_result(step: ValidationStep) -> StepResult:
    return StepResult(
        name=step.name,
        category=step.category,
        command=list(step.command),
        timeout_sec=step.timeout_sec,
        required=step.required,
        status="planned",
        exit_code=None,
        elapsed_sec=0.0,
        output_files=list(step.output_files),
        notes=list(step.notes),
    )


def _load_json_if_small(path: str) -> Any:
    p = ROOT / path
    if not p.exists() or p.stat().st_size > 25_000_000:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _first_json_output(step: ValidationStep) -> tuple[str, dict[str, Any] | None]:
    for output in step.output_files:
        if not output.endswith(".json"):
            continue
        payload = _load_json_if_small(output)
        if isinstance(payload, dict):
            return output, payload
    return "", None


def _reports(payload: dict[str, Any]) -> list[dict[str, Any]]:
    reports = payload.get("reports", [])
    return [item for item in reports if isinstance(item, dict)] if isinstance(reports, list) else []


def _count_prefix(values: dict[str, Any], prefix: str) -> int:
    total = 0
    for key, value in values.items():
        if str(key).startswith(prefix):
            try:
                total += int(value)
            except Exception:
                continue
    return total


def _has_round(report: dict[str, Any], round_name: str, status: str | None = None) -> bool:
    counts = report.get("formula_round_jobs", {})
    if isinstance(counts, dict):
        prefix = f"{round_name}:"
        if any(str(key).startswith(prefix) for key in counts):
            if status is None or int(counts.get(f"{round_name}:{status}", 0) or 0) > 0:
                return True
    for item in report.get("rounds", []) or []:
        if not isinstance(item, dict):
            continue
        if item.get("round") != round_name:
            continue
        if status is None or item.get("status") == status:
            return True
    return False


def _check_formula_index_artifact(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    checks: list[str] = []
    problems: list[str] = []
    reports = _reports(payload)
    if not reports:
        return checks, ["formula index report has no case reports"]
    for report in reports:
        case = str(report.get("case", "unknown"))
        if int(report.get("pages_scanned", 0) or 0) <= 0:
            problems.append(f"{case}: pages_scanned is zero")
        round_jobs = report.get("round_jobs", {})
        if not isinstance(round_jobs, dict) or _count_prefix(round_jobs, "r0_pdf_structure:") <= 0:
            problems.append(f"{case}: r0_pdf_structure queue count missing")
        checks.append(f"{case}:formula_index_pages={report.get('pages_scanned', 0)}")
    return checks, problems


def _check_formula_audit_artifact(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    checks: list[str] = []
    problems: list[str] = []
    reports = _reports(payload)
    if not reports:
        return checks, ["formula audit report has no case reports"]
    if not payload.get("born_digital_math_enabled"):
        problems.append("formula audit did not enable born-digital math")
    if payload.get("legacy_formula_heuristic_enabled"):
        problems.append("formula audit unexpectedly enabled legacy formula heuristic")
    if payload.get("mfd_enabled"):
        problems.append("formula audit unexpectedly enabled MFD/OCR in default validation")
    total_source = sum(int(report.get("source_formula_snippets", 0) or 0) for report in reports)
    if total_source <= 0:
        problems.append("formula audit did not find any source formulas")
    for report in reports:
        case = str(report.get("name", "unknown"))
        source_count = int(report.get("source_formula_snippets", 0) or 0)
        pdf_candidate_count = int(report.get("pdf_formula_candidate_snippets", 0) or 0)
        if source_count <= 0 and pdf_candidate_count > 0:
            problems.append(f"{case}: PDF formula candidates exist but source_formula_snippets is zero")
        if int(report.get("pages", 0) or 0) <= 0:
            problems.append(f"{case}: audited pages is zero")
        checks.append(
            f"{case}:source={source_count} "
            f"pdf_candidates={pdf_candidate_count}"
        )
    return checks, problems


def _round_detail_value(report: dict[str, Any], round_name: str, key: str) -> int:
    total = 0
    for item in report.get("rounds", []) or []:
        if not isinstance(item, dict) or item.get("round") != round_name:
            continue
        details = item.get("details", {})
        if isinstance(details, dict):
            try:
                total += int(details.get(key, 0) or 0)
            except Exception:
                continue
    return total


def _tinybdmath_processed(report: dict[str, Any]) -> int:
    for item in report.get("rounds", []) or []:
        if not isinstance(item, dict) or item.get("round") != "r2a_tinybdmath_structural":
            continue
        details = item.get("details", {})
        if isinstance(details, dict):
            try:
                return int(details.get("processed", 0) or 0)
            except Exception:
                return 0
    counts = report.get("formula_round_jobs", {})
    return int(counts.get("r2a_tinybdmath_structural:done", 0) or 0) if isinstance(counts, dict) else 0


def _reuse_evidence(report: dict[str, Any]) -> int:
    evidence = _round_detail_value(report, "r0_pdf_structure", "skipped_completed_pages")
    for snapshot in report.get("formula_fusion_snapshots", []) or []:
        if not isinstance(snapshot, dict):
            continue
        persisted = snapshot.get("persisted", {})
        if isinstance(persisted, dict):
            try:
                evidence += int(persisted.get("already_done_same_input", 0) or 0)
            except Exception:
                pass
    for item in report.get("rounds", []) or []:
        if not isinstance(item, dict) or item.get("round") != "r2a_tinybdmath_structural":
            continue
        details = item.get("details", {})
        if not isinstance(details, dict):
            continue
        for part in ("structure", "inline"):
            sub = details.get(part, {})
            if isinstance(sub, dict):
                try:
                    evidence += int(sub.get("skipped_cached", 0) or 0)
                except Exception:
                    pass
    return evidence


def _check_multiround_artifact(step: ValidationStep, payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    checks: list[str] = []
    problems: list[str] = []
    reports = _reports(payload)
    if not reports:
        return checks, ["multiround report has no case reports"]
    include_cloud = "--run-cloud-review" in step.command
    include_local_tools = "--auto-local-tools" in step.command
    expect_reuse = "reopen_skip" in step.name
    for report in reports:
        case = str(report.get("case", "unknown"))
        pages = int(report.get("pages_scanned", 0) or 0)
        if pages <= 0:
            problems.append(f"{case}: pages_scanned is zero")
        if not _has_round(report, "r0_pdf_structure", "done"):
            problems.append(f"{case}: r0_pdf_structure done round missing")
        if int(report.get("formula_blocks", 0) or 0) > 0 and not _has_round(report, "r0_5_symbol_identity_repair"):
            problems.append(f"{case}: r0.5 symbol identity round missing")
        processed = _tinybdmath_processed(report)
        recognition = report.get("recognition_results", {})
        tiny_count = _count_prefix(recognition, "tinybdmath_structural:") if isinstance(recognition, dict) else 0
        if processed > 0 and tiny_count <= 0:
            problems.append(f"{case}: TinyBDMath processed {processed} rows but recognition result is missing")
        if not include_local_tools and isinstance(recognition, dict) and _count_prefix(recognition, "local_precise:") > 0:
            problems.append(f"{case}: local OCR/MFR results appeared without --include-local-tools")
        for item in report.get("rounds", []) or []:
            if not isinstance(item, dict) or item.get("round") != "r3_cloud_semantic_review":
                continue
            details = item.get("details", {})
            if isinstance(details, dict) and bool(details.get("cloud")) and not include_cloud:
                problems.append(f"{case}: cloud review ran without --include-cloud")
        if expect_reuse and _reuse_evidence(report) <= 0:
            problems.append(f"{case}: reopen/reuse step did not report skip/cache evidence")
        checks.append(
            f"{case}:pages={pages} tinybdmath={processed} "
            f"reuse_evidence={_reuse_evidence(report)}"
        )
    return checks, problems


def _check_log_artifact(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    audit = payload.get("audit", {})
    if not isinstance(audit, dict):
        return [], ["log audit payload missing audit object"]
    levels = audit.get("levels", {})
    checks = [f"log_levels={levels}"]
    return checks, []


def _validate_artifact_semantics(step: ValidationStep) -> tuple[list[str], list[str]]:
    _path, payload = _first_json_output(step)
    if payload is None:
        return [], []
    if step.category == "formula_index":
        return _check_formula_index_artifact(payload)
    if step.category == "formula_audit":
        return _check_formula_audit_artifact(payload)
    if step.category == "formula_multiround":
        return _check_multiround_artifact(step, payload)
    if step.category == "logs":
        return _check_log_artifact(payload)
    return [], []


def _apply_artifact_checks(step: ValidationStep, result: StepResult) -> None:
    if result.status not in {"passed", "warning"}:
        return
    checks, problems = _validate_artifact_semantics(step)
    result.artifact_checks.extend(f"PASS {item}" for item in checks)
    result.artifact_checks.extend(f"FAIL {item}" for item in problems)
    if problems:
        result.status = "failed" if step.required else "warning"


def _artifact_summary(results: Sequence[StepResult]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for result in results:
        loaded: dict[str, Any] = {}
        for output in result.output_files:
            if output.endswith(".json"):
                payload = _load_json_if_small(output)
                if isinstance(payload, dict):
                    loaded[output] = {
                        key: payload.get(key)
                        for key in ("status", "generated_at", "created_at", "elapsed_sec", "pages_scanned")
                        if key in payload
                    }
                    if "round_counts" in payload:
                        loaded[output]["round_counts"] = payload.get("round_counts")
                    if "reports" in payload and isinstance(payload["reports"], list):
                        loaded[output]["report_count"] = len(payload["reports"])
        if loaded:
            summary[result.name] = loaded
    return summary


def run_validation(options: ValidationOptions) -> int:
    options.output_dir.mkdir(parents=True, exist_ok=True)
    steps = build_plan(options)
    results: list[StepResult] = []
    started = time.perf_counter()
    for step in steps:
        print(f"[full-validation] {step.name} ({step.category})", flush=True)
        result = _planned_result(step) if options.dry_run else _run_step(step, options.output_dir)
        results.append(result)
        print(f"[full-validation] {step.name}: {result.status} ({result.elapsed_sec:.3f}s)", flush=True)
        if options.fail_fast and result.required and result.status not in {"passed", "planned"}:
            break
    elapsed = time.perf_counter() - started
    required_failures = [
        result
        for result in results
        if result.required and result.status not in {"passed", "planned"}
    ]
    payload = {
        "schema_version": "full_software_validation_v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "repo": str(ROOT),
        "options": {
            **asdict(options),
            "output_dir": _rel(options.output_dir),
            "python": str(options.python),
            "tinybdmath_graph_parser_model": (
                str(options.tinybdmath_graph_parser_model)
                if options.tinybdmath_graph_parser_model is not None
                else str(DEFAULT_TINYBDMATH_GRAPH_PARSER_MODEL)
            ),
        },
        "status": "passed" if not required_failures else "failed",
        "elapsed_sec": round(elapsed, 3),
        "step_count": len(results),
        "required_failure_count": len(required_failures),
        "results": [asdict(result) for result in results],
        "artifact_summary": _artifact_summary(results),
    }
    report = options.output_dir / "full_software_validation_report.json"
    report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "report": _rel(report),
        "elapsed_sec": payload["elapsed_sec"],
        "step_count": payload["step_count"],
        "required_failure_count": payload["required_failure_count"],
    }, ensure_ascii=False, indent=2), flush=True)
    return 0 if not required_failures else 1


def _parse_args(argv: Sequence[str] | None = None) -> ValidationOptions:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=["quick", "standard", "full", "nightly"], default="standard")
    parser.add_argument("--case", choices=["attention", "napkin", "all"], default="all")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "test_artifacts" / "full_software_validation")
    parser.add_argument("--python", type=Path, default=_python_path())
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--include-desktop-e2e", action="store_true")
    parser.add_argument("--include-cloud", action="store_true")
    parser.add_argument("--include-local-tools", action="store_true")
    parser.add_argument("--strict-logs", action="store_true")
    parser.add_argument("--tinybdmath-graph-parser-model", type=Path)
    parser.add_argument(
        "--stress-multiplier",
        type=int,
        default=1,
        help="Multiply page budgets and desktop E2E interaction counts.",
    )
    args = parser.parse_args(argv)
    return ValidationOptions(
        profile=args.profile,
        case=args.case,
        output_dir=args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir,
        python=_python_path(args.python),
        max_pages=max(0, args.max_pages),
        dry_run=args.dry_run,
        fail_fast=args.fail_fast,
        include_desktop_e2e=args.include_desktop_e2e,
        include_cloud=args.include_cloud,
        include_local_tools=args.include_local_tools,
        strict_logs=args.strict_logs,
        tinybdmath_graph_parser_model=args.tinybdmath_graph_parser_model,
        stress_multiplier=max(1, args.stress_multiplier),
    )


def main(argv: Sequence[str] | None = None) -> int:
    return run_validation(_parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

import json
from pathlib import Path


def _options(tmp_path: Path, **overrides):
    from tools.full_software_validation import ValidationOptions

    values = {
        "profile": "standard",
        "case": "all",
        "output_dir": tmp_path / "validation",
        "python": Path("python"),
        "max_pages": 2,
        "dry_run": True,
        "fail_fast": False,
        "include_desktop_e2e": False,
        "include_cloud": False,
        "include_local_tools": False,
        "strict_logs": False,
        "tinybdmath_edge_model": None,
        "stress_multiplier": 1,
    }
    values.update(overrides)
    return ValidationOptions(**values)


def test_build_plan_covers_whole_software_gates(tmp_path) -> None:
    from tools.full_software_validation import build_plan

    steps = build_plan(_options(tmp_path))
    names = {step.name for step in steps}
    categories = {step.category for step in steps}

    assert "pytest_core_rag_graph" in names
    assert "pytest_formula_pipeline" in names
    assert "pytest_tinybdmath_model_path" in names
    assert "formula_latex_source_audit" in names
    assert "formula_multiround_attention" in names
    assert "formula_multiround_reopen_skip_attention" in names
    assert "formula_multiround_napkin_front" in names
    assert "formula_multiround_napkin_formula_dense" in names
    assert {"pytest", "formula_index", "formula_audit", "formula_multiround", "logs"}.issubset(categories)
    assert "desktop_e2e" not in categories


def test_build_plan_enables_explicit_e2e_cloud_and_local_tools(tmp_path) -> None:
    from tools.full_software_validation import build_plan

    steps = build_plan(
        _options(
            tmp_path,
            include_desktop_e2e=True,
            include_cloud=True,
            include_local_tools=True,
        )
    )
    by_name = {step.name: step for step in steps}

    assert "desktop_e2e_workflow" in by_name
    multiround = by_name["formula_multiround_attention"]
    command = " ".join(multiround.command)
    assert "--run-cloud-review" in command
    assert "--auto-local-tools" in command
    assert "--r2-sample-formulas" in command


def test_stress_multiplier_scales_pages_limits_and_desktop_e2e(tmp_path) -> None:
    from tools.full_software_validation import build_plan

    steps = build_plan(
        _options(
            tmp_path,
            include_desktop_e2e=True,
            include_cloud=True,
            include_local_tools=True,
            max_pages=2,
            stress_multiplier=5,
        )
    )
    by_name = {step.name: step for step in steps}

    index_cmd = list(by_name["formula_index_performance_attention"].command)
    multiround_cmd = list(by_name["formula_multiround_attention"].command)
    e2e_cmd = list(by_name["desktop_e2e_workflow"].command)

    assert index_cmd[index_cmd.index("--max-pages") + 1] == "10"
    assert multiround_cmd[multiround_cmd.index("--max-pages") + 1] == "10"
    assert multiround_cmd[multiround_cmd.index("--r2-limit") + 1] == "40"
    assert multiround_cmd[multiround_cmd.index("--r2-sample-formulas") + 1] == "40"
    assert multiround_cmd[multiround_cmd.index("--r3-limit") + 1] == "20"
    assert multiround_cmd[multiround_cmd.index("--r4-limit") + 1] == "80"
    assert multiround_cmd[multiround_cmd.index("--r5-limit") + 1] == "20"
    assert e2e_cmd[e2e_cmd.index("--stress-multiplier") + 1] == "5"


def test_dry_run_writes_auditable_report(tmp_path) -> None:
    from tools.full_software_validation import run_validation

    options = _options(tmp_path, profile="quick", case="attention")

    exit_code = run_validation(options)

    report_path = options.output_dir / "full_software_validation_report.json"
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["schema_version"] == "full_software_validation_v1"
    assert payload["status"] == "passed"
    assert payload["step_count"] >= 5
    assert all(result["status"] == "planned" for result in payload["results"])
    assert any(result["name"] == "formula_multiround_attention" for result in payload["results"])


def test_multiround_artifact_semantics_require_reuse_evidence(tmp_path) -> None:
    from tools.full_software_validation import ValidationStep, _check_multiround_artifact

    step = ValidationStep(
        name="formula_multiround_reopen_skip_attention",
        category="formula_multiround",
        command=("python", "tools/formula_multiround_pipeline.py", "--reuse-db"),
        timeout_sec=60,
    )
    payload = {
        "reports": [
            {
                "case": "attention",
                "pages_scanned": 2,
                "formula_blocks": 1,
                "rounds": [
                    {"round": "r0_pdf_structure", "status": "done", "details": {}},
                    {"round": "r0_5_symbol_identity_repair", "status": "done", "details": {}},
                    {
                        "round": "r2a_tinybdmath_structural",
                        "status": "done",
                        "details": {"processed": 1},
                    },
                ],
                "recognition_results": {"tinybdmath_structural:tinybdmath": 1},
                "formula_fusion_snapshots": [],
            }
        ]
    }

    checks, problems = _check_multiround_artifact(step, payload)

    assert checks
    assert any("reopen/reuse step did not report skip/cache evidence" in item for item in problems)


def test_multiround_artifact_semantics_accept_real_skip_evidence(tmp_path) -> None:
    from tools.full_software_validation import ValidationStep, _check_multiround_artifact

    step = ValidationStep(
        name="formula_multiround_reopen_skip_attention",
        category="formula_multiround",
        command=("python", "tools/formula_multiround_pipeline.py", "--reuse-db"),
        timeout_sec=60,
    )
    payload = {
        "reports": [
            {
                "case": "attention",
                "pages_scanned": 2,
                "formula_blocks": 1,
                "rounds": [
                    {
                        "round": "r0_pdf_structure",
                        "status": "done",
                        "details": {"skipped_completed_pages": 2},
                    },
                    {"round": "r0_5_symbol_identity_repair", "status": "done", "details": {}},
                    {
                        "round": "r2a_tinybdmath_structural",
                        "status": "done",
                        "details": {
                            "processed": 0,
                            "structure": {"skipped_cached": 1},
                            "inline": {"skipped_cached": 3},
                        },
                    },
                ],
                "recognition_results": {"tinybdmath_structural:tinybdmath": 4},
                "formula_fusion_snapshots": [
                    {"persisted": {"already_done_same_input": 4}},
                ],
            }
        ]
    }

    _checks, problems = _check_multiround_artifact(step, payload)

    assert problems == []


def test_formula_audit_semantics_reject_default_ocr_path() -> None:
    from tools.full_software_validation import _check_formula_audit_artifact

    payload = {
        "mfd_enabled": True,
        "born_digital_math_enabled": True,
        "legacy_formula_heuristic_enabled": False,
        "reports": [
            {"name": "attention", "pages": 2, "source_formula_snippets": 10},
        ],
    }

    _checks, problems = _check_formula_audit_artifact(payload)

    assert "formula audit unexpectedly enabled MFD/OCR in default validation" in problems


def test_formula_audit_semantics_allow_formula_free_page_slice() -> None:
    from tools.full_software_validation import _check_formula_audit_artifact

    payload = {
        "mfd_enabled": False,
        "born_digital_math_enabled": True,
        "legacy_formula_heuristic_enabled": False,
        "reports": [
            {"name": "attention", "pages": 2, "source_formula_snippets": 10, "pdf_formula_candidate_snippets": 3},
            {"name": "napkin", "pages": 2, "source_formula_snippets": 0, "pdf_formula_candidate_snippets": 0},
        ],
    }

    _checks, problems = _check_formula_audit_artifact(payload)

    assert problems == []

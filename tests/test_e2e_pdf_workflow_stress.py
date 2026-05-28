import json

from tools import e2e_pdf_workflow as workflow
from tools.e2e_pdf_workflow import (
    _cases,
    _formula_scan_iterations,
    _send_command,
    _summarize_action_coverage,
    _stress_case,
    _stress_pages,
    _translation_request_count,
    _translation_toggle_pairs,
    _zoom_cycle_count,
    _wait_for_event,
)


def test_stress_pages_cover_document_without_repeating_same_pages() -> None:
    pages = _stress_pages([0, 10, 50, 120, 250, 420, 620, 820, 1000, 1049], 400)

    assert pages[0] == 0
    assert pages[1] == 1049
    assert len(pages) <= 64
    assert len(pages) == len(set(pages))
    assert max(pages) == 1049
    assert any(500 <= page <= 550 for page in pages)


def test_stress_case_scales_interactions_and_zoom_budget() -> None:
    case = _cases()[0]

    stressed = _stress_case(case, 5)

    assert stressed.name == case.name
    assert stressed.scroll_steps > case.scroll_steps
    assert len(stressed.jump_pages) > len(case.jump_pages)
    assert len(stressed.jump_pages) <= 64
    assert (
        stressed.performance_budget["min_zoom_complete_count"]
        >= case.performance_budget["min_zoom_complete_count"]
    )
    assert stressed.performance_budget["max_render_ms"] == case.performance_budget["max_render_ms"]


def test_stress_case_keeps_zoomed_jump_pages_groupable_by_multiplier() -> None:
    case = _cases()[0]

    stressed = _stress_case(case, 5)

    assert stressed.jump_pages[0] == min(case.jump_pages)
    assert stressed.jump_pages[1] == max(case.jump_pages)
    assert len(stressed.jump_pages) == len(set(stressed.jump_pages))


def test_stress_counts_are_bounded_for_400x() -> None:
    assert _zoom_cycle_count(400) == 12
    assert _translation_toggle_pairs(400) == 8
    assert _translation_request_count(400) == 12
    assert _formula_scan_iterations(400) == 8


def test_send_command_adds_command_id(tmp_path, monkeypatch) -> None:
    command_file = tmp_path / "commands.jsonl"
    monkeypatch.setattr(workflow, "COMMAND_FILE", command_file)

    command_id = _send_command({"cmd": "snapshot_state"})

    payload = json.loads(command_file.read_text(encoding="utf-8"))
    assert payload["cmd"] == "snapshot_state"
    assert payload["command_id"] == command_id


def test_wait_for_event_filters_by_command_id(tmp_path, monkeypatch) -> None:
    event_file = tmp_path / "events.jsonl"
    monkeypatch.setattr(workflow, "EVENT_FILE", event_file)
    event_file.write_text(
        "\n".join(
            [
                json.dumps({"event": "scrolled_to_page", "page": 1, "command_id": "old"}),
                json.dumps({"event": "scrolled_to_page", "page": 8, "command_id": "new"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    event = _wait_for_event("scrolled_to_page", command_id="new", timeout=0.1)

    assert event["page"] == 8


def test_action_coverage_requires_real_stress_phases() -> None:
    actions = [
        {"name": "large_wheel_burst", "phase": "baseline", "total_wheel_units": 2400},
        {"name": "continuous_fast_scroll", "phase": "baseline", "total_wheel_units": 2700},
        {"name": "reverse_fast_scroll", "phase": "baseline", "total_wheel_units": 900},
        {"name": "large_wheel_burst", "phase": "extreme_zoom", "total_wheel_units": 2400},
        {"name": "continuous_fast_scroll", "phase": "extreme_zoom", "total_wheel_units": 2700},
        {"name": "reverse_fast_scroll", "phase": "extreme_zoom", "total_wheel_units": 900},
        {"name": "large_wheel_burst", "phase": "translation_split_open", "total_wheel_units": 2400},
        {"name": "continuous_fast_scroll", "phase": "translation_split_open", "total_wheel_units": 2700},
        {"name": "reverse_fast_scroll", "phase": "translation_split_open", "total_wheel_units": 900},
        {
            "name": "large_wheel_burst",
            "phase": "translation_split_extreme_zoom",
            "total_wheel_units": 2400,
        },
        {
            "name": "continuous_fast_scroll",
            "phase": "translation_split_extreme_zoom",
            "total_wheel_units": 2700,
        },
        {
            "name": "reverse_fast_scroll",
            "phase": "translation_split_extreme_zoom",
            "total_wheel_units": 900,
        },
        *({"name": "extreme_zoom_in"} for _ in range(9)),
        *({"name": "extreme_zoom_out"} for _ in range(9)),
        *({"name": "extreme_zoom_jump_page"} for _ in range(4)),
        *({"name": "double_click_toggle"} for _ in range(_translation_toggle_pairs(5) * 2)),
        *({"name": "translation_request_stress"} for _ in range(_translation_request_count(5))),
        *({"name": "formula_scan_stress"} for _ in range(_formula_scan_iterations(5))),
    ]

    coverage = _summarize_action_coverage(actions, 5)

    assert coverage["within_budget"] is True
    assert coverage["phases"]["translation_split_extreme_zoom"] == 3


def test_action_coverage_rejects_missing_translation_scroll_phase() -> None:
    coverage = _summarize_action_coverage(
        [{"name": "large_wheel_burst", "phase": "baseline", "total_wheel_units": 5000}],
        5,
    )

    assert coverage["within_budget"] is False
    assert any("translation_split_open" in item for item in coverage["violations"])

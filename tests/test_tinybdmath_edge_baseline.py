from src.core.tinybdmath_edge_baseline import train_edge_baseline


def test_edge_baseline_trains_on_relation_samples() -> None:
    samples = [
        _sample("e1", "right_neighbor", "HORIZONTAL"),
        _sample("e2", "subscript_zone", "SUB", dy=0.7),
        _sample("e3", "superscript_zone", "SUP", dy=-0.7),
        _sample("e4", "far_context", "NO_RELATION", dx=4.0),
        _sample("e5", "above_zone", "ABOVE", dy=-1.2),
        _sample("e6", "below_zone", "BELOW", dy=1.2),
        _sample("e7", "radical_body_candidate", "RADICAL_BODY", dx=0.6),
    ]

    model, report = train_edge_baseline(samples, epochs=3)

    assert report["samples"] == len(samples)
    assert model.predict(samples[0]) in model.labels
    assert model.predict_proba(samples[0])
    assert "RADICAL_BODY" in model.labels


def _sample(edge_id: str, hint: str, label: str, *, dx: float = 0.5, dy: float = 0.0) -> dict:
    return {
        "row_id": "r",
        "edge_id": edge_id,
        "hint": hint,
        "label": label,
        "features": {
            "dx_over_height": dx,
            "dy_over_height": dy,
            "x_overlap": 0.0,
            "y_overlap": 0.7,
            "size_ratio": 0.8,
            "same_font": 1,
        },
    }

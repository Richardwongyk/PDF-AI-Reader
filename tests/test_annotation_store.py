from src.data.annotation_store import read_annotation_store, save_annotation_patch


def test_annotation_patch_preserves_existing_notes_from_other_window(tmp_path) -> None:
    path = tmp_path / "annotations.json"

    save_annotation_patch(path, "doc-a", "p0_b1", "first note")
    save_annotation_patch(path, "doc-a", "p0_b2", "second note")
    save_annotation_patch(path, "doc-b", "p0_b1", "other doc")

    assert read_annotation_store(path) == {
        "doc-a": {
            "p0_b1": "first note",
            "p0_b2": "second note",
        },
        "doc-b": {
            "p0_b1": "other doc",
        },
    }


def test_annotation_patch_deletes_only_target_block(tmp_path) -> None:
    path = tmp_path / "annotations.json"

    save_annotation_patch(path, "doc-a", "p0_b1", "first note")
    save_annotation_patch(path, "doc-a", "p0_b2", "second note")
    save_annotation_patch(path, "doc-a", "p0_b1", "")

    assert read_annotation_store(path) == {
        "doc-a": {
            "p0_b2": "second note",
        },
    }

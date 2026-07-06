from src.data.annotation_store import read_annotation_store, save_annotation_patch


def test_annotation_store_ignores_malformed_and_blank_entries(tmp_path) -> None:
    path = tmp_path / "annotations.json"
    path.write_text(
        """
        {
          "doc-a": {
            "p0_b1": "keep",
            "p0_b2": "   "
          },
          "doc-b": ["not", "a", "mapping"],
          "doc-c": {
            "p0_b3": 123
          }
        }
        """,
        encoding="utf-8",
    )

    assert read_annotation_store(path) == {
        "doc-a": {"p0_b1": "keep"},
        "doc-c": {"p0_b3": "123"},
    }

    path.write_text("{broken json", encoding="utf-8")
    assert read_annotation_store(path) == {}


def test_annotation_patch_with_empty_doc_hash_is_noop(tmp_path) -> None:
    path = tmp_path / "annotations.json"

    assert save_annotation_patch(path, "", "p0_b1", "note") == {}
    assert not path.exists()


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

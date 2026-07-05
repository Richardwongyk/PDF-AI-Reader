"""Cross-process safe JSON storage for paragraph annotations."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from PySide6.QtCore import QLockFile


AnnotationStore = dict[str, dict[str, str]]


def read_annotation_store(path: str | Path) -> AnnotationStore:
    store_path = Path(path)
    try:
        if not store_path.exists():
            return {}
        raw = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _clean_store(raw)


def save_annotation_patch(
    path: str | Path,
    doc_hash: str,
    block_id: str,
    note: str,
    *,
    timeout_ms: int = 5000,
) -> AnnotationStore:
    store_path = Path(path)
    if not doc_hash:
        return {}
    store_path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(store_path.with_suffix(store_path.suffix + ".lock")))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(timeout_ms):
        raise OSError(f"Could not lock annotation store: {store_path}")
    try:
        store = read_annotation_store(store_path)
        notes = dict(store.get(doc_hash, {}))
        clean_note = str(note or "").strip()
        if clean_note:
            notes[str(block_id)] = clean_note
        else:
            notes.pop(str(block_id), None)
        if notes:
            store[doc_hash] = notes
        else:
            store.pop(doc_hash, None)
        _atomic_write_store(store_path, store)
        return store
    finally:
        lock.unlock()


def _atomic_write_store(path: Path, store: AnnotationStore) -> None:
    tmp_path = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    tmp_path.write_text(
        json.dumps(store, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _clean_store(raw: object) -> AnnotationStore:
    if not isinstance(raw, dict):
        return {}
    store: AnnotationStore = {}
    for doc_hash, notes in raw.items():
        if not isinstance(notes, dict):
            continue
        clean_notes = {
            str(block_id): str(note)
            for block_id, note in notes.items()
            if str(note).strip()
        }
        if clean_notes:
            store[str(doc_hash)] = clean_notes
    return store

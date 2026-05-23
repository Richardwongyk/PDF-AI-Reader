from src.app.graph_index_store import GraphIndexStore
from src.core.models import BlockType, DocumentBlock


def _block(
    block_id: str = "p0_b0",
    block_type: BlockType = BlockType.PARAGRAPH,
    content: str = "Transformer attention connects tokens.",
) -> DocumentBlock:
    return DocumentBlock(
        id=block_id,
        page_num=0,
        block_type=block_type,
        content=content,
        bbox=(0, 0, 100, 20),
    )


def test_graph_index_store_enqueues_and_lists_tasks(tmp_path) -> None:
    store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    blocks = [
        _block("p0_b0", BlockType.HEADING, "Attention"),
        _block("p0_b1", BlockType.PARAGRAPH, "Queries, keys, and values."),
    ]

    assert store.enqueue_blocks("doc-1", "paper.pdf", blocks) == 2

    tasks = store.list_tasks("doc-1", {"queued"})
    assert [task.block_id for task in tasks] == ["p0_b0", "p0_b1"]
    assert tasks[0].priority > tasks[1].priority
    assert store.pending_count("doc-1") == 2


def test_graph_index_store_keeps_done_task_when_content_unchanged(tmp_path) -> None:
    store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    block = _block()
    store.enqueue_blocks("doc-1", "paper.pdf", [block])
    store.mark_running("doc-1", [block.id])
    store.mark_done(
        "doc-1",
        block.id,
        extractor="test-extractor",
        nodes=[{"id": "concept:attention", "type": "concept"}],
        edges=[{"source": "p0_b0", "target": "concept:attention", "type": "mentions"}],
    )

    assert store.enqueue_blocks("doc-1", "paper.pdf", [block]) == 0
    assert store.counts("doc-1") == {"done": 1}
    assert store.artifacts("doc-1", block.id)[0]["extractor"] == "test-extractor"


def test_graph_index_store_requeues_done_task_when_content_changes(tmp_path) -> None:
    store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    block = _block(content="old")
    store.enqueue_blocks("doc-1", "paper.pdf", [block])
    store.mark_done("doc-1", block.id, extractor="test", nodes=[], edges=[])

    changed = _block(content="new")
    assert store.enqueue_blocks("doc-1", "paper.pdf", [changed]) == 1

    task = store.list_tasks("doc-1", {"queued"})[0]
    assert task.block_id == changed.id
    assert task.content_hash == store.content_hash(changed)


def test_graph_index_store_records_failures_and_skips(tmp_path) -> None:
    store = GraphIndexStore(str(tmp_path / "graph_jobs.db"))
    failed = _block("p0_b0")
    skipped = _block("p0_b1")
    store.enqueue_blocks("doc-1", "paper.pdf", [failed, skipped])

    store.mark_running("doc-1", [failed.id])
    store.mark_failed("doc-1", failed.id, "model timeout")
    store.mark_skipped("doc-1", skipped.id, "disabled")

    assert store.counts("doc-1") == {"failed": 1, "skipped": 1}
    failed_task = store.list_tasks("doc-1", {"failed"})[0]
    assert failed_task.attempts == 1
    assert failed_task.error == "model timeout"

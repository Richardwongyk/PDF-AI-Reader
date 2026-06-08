from types import SimpleNamespace

from PySide6.QtCore import QObject, Signal

from src.app.translate_flow import TranslationFlow
from src.infra.ai_cache import AICache


class _FakeAIEngine(QObject):
    translation_finished = Signal(str, str)
    translation_error = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[object] = []

    def request_translation(self, block: object) -> None:
        self.requests.append(block)


def test_translation_flow_force_refresh_skips_read_cache_and_overwrites(tmp_path) -> None:
    cache = AICache(str(tmp_path / "ai_cache.db"))
    engine = _FakeAIEngine()
    flow = TranslationFlow(engine, cache)
    ready: list[tuple[str, str]] = []
    flow.translation_ready.connect(lambda text, block_id: ready.append((text, block_id)))

    block = SimpleNamespace(
        id="p0_b0",
        content="The attention mechanism maps queries and keys to weighted values.",
    )
    doc_hash = "doc-hash"
    content_hash = cache.hash_text(block.content)
    cache.put(block.id, doc_hash, "translation", "旧译文", content_hash=content_hash)

    assert flow.request_translation(block, doc_hash) is True
    assert ready == [("旧译文", block.id)]
    assert engine.requests == []

    ready.clear()
    assert flow.request_translation(block, doc_hash, force_refresh=True) is False
    assert engine.requests == [block]

    engine.translation_finished.emit("新译文", block.id)

    assert ready == [("新译文", block.id)]
    assert cache.get(block.id, doc_hash, "translation", content_hash) == "新译文"
    cache.close()

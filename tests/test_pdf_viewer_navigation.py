from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.core.models import BlockType, DocumentBlock, ParseResult
from src.ui.paragraph_widget import BlockOverlay
from src.ui.pdf_viewer import PdfViewer, _VirtualPageLayout


class _Signal:
    def connect(self, _callback) -> None:  # noqa: ANN001
        return


class _DocEngine:
    def __init__(self) -> None:
        self.document = None
        self.page_rendered = _Signal()
        self.requested_pages: list[int] = []
        self.rendered_pages: list[int] = []
        self.pixmaps: dict[int, QPixmap] = {}

    def request_page_blocks_async(self, page_num: int) -> None:
        self.requested_pages.append(page_num)

    def request_page_render_async(self, _page_num: int, *, dpi: int) -> None:
        self.rendered_pages.append(_page_num)

    def get_page_pixmap(self, page_num: int, *, dpi: int) -> QPixmap | None:
        return self.pixmaps.get(page_num)


class _Config:
    pass


class _FakeSplitWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.padding_calls: list[tuple[int, int]] = []

    def set_content_padding(self, left_px: int, right_px: int) -> None:
        self.padding_calls.append((left_px, right_px))


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_block_overlay_context_menu_shows_translation_and_annotation_only() -> None:
    _app()
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="paragraph",
        bbox=(0.0, 0.0, 100.0, 40.0),
    )
    overlay = BlockOverlay(block)

    actions = [action.text() for action in overlay._build_context_menu().actions()]

    assert actions == ["📖 翻译段落", "📝 批注/备注"]


def test_pdf_viewer_scrolls_to_bbox_and_shows_highlight() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=3, blocks=[]))

    moved = viewer.scroll_to_bbox(1, [10, 120, 90, 150])

    assert moved is True
    assert viewer._evidence_highlight is not None
    assert viewer._evidence_highlight.objectName() == "formula_evidence_highlight"
    assert viewer.verticalScrollBar().value() > 0
    assert viewer.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded


def test_pdf_viewer_enables_horizontal_scroll_for_zoomed_pages() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(320, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[]))

    viewer._set_zoom(2.0)
    viewer._sync_scroll_range(max(viewer.viewport().height(), 1))

    assert viewer.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert viewer.horizontalScrollBar().maximum() > 0


def test_pdf_viewer_centers_page_widget_in_wide_viewport() -> None:
    app = _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(900, 480)
    viewer.show()
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[]))

    viewer.scroll_to_page(0)
    app.processEvents()

    container = viewer._page_containers[0]
    viewer._sync_content_size(max(viewer.viewport().height(), 1))
    viewer._layout.activate()
    expected_x = (viewer._content.width() - container.width()) // 2
    assert abs(container.x() - expected_x) <= 2
    assert viewer.horizontalScrollBar().maximum() == 0
    viewer.close()


def test_pdf_viewer_center_horizontally_keeps_wide_page_scrollable() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(320, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[]))
    viewer._set_zoom(2.0)

    viewer.center_horizontally()

    hbar = viewer.horizontalScrollBar()
    assert viewer.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert hbar.maximum() > 0
    assert hbar.value() == hbar.maximum() // 2


def test_pdf_viewer_recenters_horizontally_after_zooming_back_out() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(320, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[]))
    viewer.scroll_to_page(0)

    viewer._set_zoom(2.0)
    viewer.horizontalScrollBar().setValue(viewer.horizontalScrollBar().maximum())
    viewer._set_zoom(1.2)

    hbar = viewer.horizontalScrollBar()
    assert hbar.maximum() > 0
    assert hbar.value() == hbar.maximum() // 2


def test_pdf_viewer_scroll_to_bbox_moves_horizontally_for_wide_pages() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(320, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[]))
    viewer._set_zoom(2.0)
    viewer._sync_scroll_range(max(viewer.viewport().height(), 1))

    moved = viewer.scroll_to_bbox(0, [300, 120, 360, 150])

    assert moved is True
    assert viewer.horizontalScrollBar().value() > 0


def test_pdf_viewer_recomputes_split_text_padding_on_zoom() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="paragraph",
        bbox=(20.0, 120.0, 180.0, 160.0),
    )
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[block]))
    split = _FakeSplitWidget()
    viewer._splits[block.id] = split  # type: ignore[assignment]
    viewer._split_pages.add(0)
    viewer._page_segments[0] = [{"split_id": block.id}]

    viewer._set_zoom(1.2)

    page_pt_w = viewer._page_rects_pt[0][0]
    assert split.width() == viewer._page_metas[0]["width"]
    assert split.padding_calls[-1] == (
        int(block.bbox[0] * viewer._scale),
        int((page_pt_w - block.bbox[2]) * viewer._scale),
    )


def test_pdf_viewer_rejects_invalid_bbox() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[]))

    assert viewer.scroll_to_bbox(0, [10, 20, 5, 30]) is False
    assert viewer.scroll_to_bbox(5, [10, 20, 30, 40]) is False


def test_pdf_viewer_reuses_hidden_page_at_current_zoom() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=2, blocks=[]))
    container = viewer._ensure_page_widget(0)
    assert container is not None
    container._rendered = True
    container._zoom = viewer._scale

    viewer._hide_page_from_layout(0)
    viewer._zoom_multiplier = 1.2
    viewer._scale = viewer._base_scale * 1.2
    viewer._dpi = int(viewer._base_dpi * 1.2)
    viewer._page_metas[0]["width"] += 10

    reused = viewer._ensure_page_widget(0)

    assert reused is container
    assert reused.rendered is False
    assert reused.width() == viewer._page_metas[0]["width"]


def test_pdf_viewer_jump_page_renders_target_and_neighbors_immediately() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=200, blocks=[]))

    viewer.scroll_to_page(150)

    assert viewer.verticalScrollBar().value() == int(viewer._vlayout.page_y(150))
    assert 150 in engine.requested_pages
    assert 150 in engine.rendered_pages
    assert engine.rendered_pages


def test_pdf_viewer_reuses_segment_overlays_on_zoom() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="paragraph",
        bbox=(20.0, 150.0, 180.0, 190.0),
    )
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[block]))
    widget = QWidget()
    widget.setFixedSize(400, 300)
    viewer._page_segments[0] = [{"y0": 0, "y1": 300, "blocks": [block], "widget": widget}]

    viewer._refresh_segment_overlays(widget, [block], 0, 300)
    first = widget.findChild(BlockOverlay)
    assert first is not None

    viewer._zoom_multiplier = 1.2
    viewer._scale = viewer._base_scale * 1.2
    viewer._refresh_segment_overlays(widget, [block], 0, 360)

    assert widget.findChild(BlockOverlay) is first
    assert first.width() > 0


def test_pdf_viewer_split_segment_uses_cropped_label_pixmap() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="paragraph",
        bbox=(20.0, 150.0, 180.0, 190.0),
    )
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[block]))
    pixmap = QPixmap(400, 600)
    pixmap.fill()

    widget = viewer._build_segment_widget(pixmap, 120, 360, [block], page_num=0)

    label = widget.findChild(QLabel)
    assert label is not None
    assert label.pixmap() is not None
    assert int(label.pixmap().deviceIndependentSize().height()) == 240
    assert widget.findChild(BlockOverlay) is not None


def test_pdf_viewer_reuses_page_overlays_on_zoom_refresh() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="paragraph",
        bbox=(20.0, 30.0, 180.0, 70.0),
    )
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[block]))
    container = viewer._ensure_page_widget(0)
    assert container is not None

    viewer._refresh_container_overlays(container, [block])
    first = container.overlay(block.id)
    assert first is not None

    viewer._zoom_multiplier = 1.2
    viewer._scale = viewer._base_scale * 1.2
    viewer._refresh_container_overlays(container, [block])

    assert container.overlay(block.id) is first
    assert first.width() > 0


def test_pdf_viewer_hides_offscreen_split_page_without_dropping_state() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=12, blocks=[]))

    top = QWidget()
    split = QWidget()
    bottom = QWidget()
    for widget in (top, split, bottom):
        widget.setFixedSize(400, 80)
        widget.show()
        viewer._remember_widget_page(widget, 0)
    viewer._layout.insertWidget(1, top)
    viewer._layout.insertWidget(2, split)
    viewer._layout.insertWidget(3, bottom)
    viewer._page_segments[0] = [
        {"y0": 0, "y1": 100, "blocks": [], "widget": top},
        {"split_id": "p0_b0"},
        {"y0": 100, "y1": 200, "blocks": [], "widget": bottom},
    ]
    viewer._splits["p0_b0"] = split  # type: ignore[assignment]
    viewer._split_pages.add(0)
    viewer._active_pages.add(0)
    assert viewer._vlayout is not None
    viewer._vlayout.register_split(0, 80.0)

    viewer.scroll_to_page(8)

    assert "p0_b0" in viewer._splits
    assert 0 in viewer._split_pages
    assert 0 not in viewer._active_pages
    assert viewer._layout.indexOf(split) < 0
    assert split.isHidden()

    viewer.scroll_to_page(0)

    assert "p0_b0" in viewer._splits
    assert 0 in viewer._active_pages
    assert viewer._layout.indexOf(split) >= 0
    assert not split.isHidden()


def test_pdf_viewer_restores_offscreen_split_page_after_zoom_rerender() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=12, blocks=[]))

    top = QWidget()
    split = QWidget()
    bottom = QWidget()
    for widget in (top, split, bottom):
        widget.setFixedSize(400, 80)
        widget.show()
        viewer._remember_widget_page(widget, 0)
    viewer._layout.insertWidget(1, top)
    viewer._layout.insertWidget(2, split)
    viewer._layout.insertWidget(3, bottom)
    viewer._page_segments[0] = [
        {"y0": 0, "y1": 120, "blocks": [], "widget": top},
        {"split_id": "p0_b0"},
        {"y0": 120, "y1": 240, "blocks": [], "widget": bottom},
    ]
    viewer._splits["p0_b0"] = split  # type: ignore[assignment]
    viewer._split_pages.add(0)
    viewer._active_pages.add(0)
    assert viewer._vlayout is not None
    viewer._vlayout.register_split(0, 80.0)

    viewer._hide_split_page_from_layout(0)
    assert viewer._layout.indexOf(top) < 0

    new_pixmap = QPixmap(600, 900)
    new_pixmap.fill()
    engine.pixmaps[0] = new_pixmap
    viewer._scale = 2.0
    for seg in viewer._page_segments[0]:
        if "split_id" not in seg:
            seg["y0_pt"] = seg["y0"] / 1.0
            seg["y1_pt"] = seg["y1"] / 1.0
            seg["y0"] = int(seg["y0_pt"] * viewer._scale)
            seg["y1"] = int(seg["y1_pt"] * viewer._scale)
    viewer._pending_split_rerenders.add(0)

    viewer._show_split_page_in_layout(0)

    restored_top = viewer._page_segments[0][0]["widget"]
    restored_bottom = viewer._page_segments[0][2]["widget"]
    assert restored_top is not top
    assert restored_bottom is not bottom
    assert viewer._layout.indexOf(restored_top) >= 0
    assert viewer._layout.indexOf(split) >= 0
    assert viewer._layout.indexOf(restored_bottom) >= 0
    assert "p0_b0" in viewer._splits
    assert 0 not in viewer._pending_split_rerenders
    assert restored_top.height() == 240


def test_pdf_viewer_rebuilds_split_segments_at_current_page_width() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(900, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[]))
    page_w = viewer._page_metas[0]["width"]

    top = QWidget()
    split = QWidget()
    bottom = QWidget()
    for widget in (top, split, bottom):
        widget.setFixedSize(page_w, 80)
        viewer._remember_widget_page(widget, 0)
    viewer._insert_page_content_widget(1, top)
    viewer._insert_page_content_widget(2, split)
    viewer._insert_page_content_widget(3, bottom)
    viewer._page_segments[0] = [
        {"y0": 0, "y1": 120, "blocks": [], "widget": top},
        {"split_id": "p0_b0"},
        {"y0": 120, "y1": 240, "blocks": [], "widget": bottom},
    ]
    viewer._splits["p0_b0"] = split  # type: ignore[assignment]
    viewer._split_pages.add(0)
    viewer._active_pages.add(0)
    viewer._screen_dpr = 2.0

    physical_pixmap = QPixmap(page_w * 2, 800 * 2)
    physical_pixmap.fill()
    rebuilt = viewer._rebuild_split_segments_from_pixmap(
        0,
        physical_pixmap,
        only_if_in_layout=False,
    )

    rebuilt_top = viewer._page_segments[0][0]["widget"]
    rebuilt_bottom = viewer._page_segments[0][2]["widget"]
    assert rebuilt == 2
    assert rebuilt_top.width() == page_w
    assert rebuilt_bottom.width() == page_w
    assert physical_pixmap.devicePixelRatio() == 2.0


def test_pdf_viewer_zoom_prioritizes_visible_split_pages() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=8, blocks=[]))

    visible_top = QWidget()
    visible_split = QWidget()
    visible_bottom = QWidget()
    for widget in (visible_top, visible_split, visible_bottom):
        widget.setFixedSize(400, 80)
        widget.show()
        viewer._remember_widget_page(widget, 0)
    viewer._layout.insertWidget(1, visible_top)
    viewer._layout.insertWidget(2, visible_split)
    viewer._layout.insertWidget(3, visible_bottom)
    viewer._page_segments[0] = [
        {"y0": 0, "y1": 120, "blocks": [], "widget": visible_top},
        {"split_id": "p0_b0"},
        {"y0": 120, "y1": 240, "blocks": [], "widget": visible_bottom},
    ]
    viewer._splits["p0_b0"] = visible_split  # type: ignore[assignment]
    viewer._split_pages.add(0)
    viewer._active_pages.add(0)

    hidden_top = QWidget()
    hidden_split = QWidget()
    hidden_bottom = QWidget()
    for widget in (hidden_top, hidden_split, hidden_bottom):
        widget.setFixedSize(400, 80)
        widget.hide()
        viewer._remember_widget_page(widget, 5)
    viewer._page_segments[5] = [
        {"y0": 0, "y1": 120, "blocks": [], "widget": hidden_top},
        {"split_id": "p5_b0"},
        {"y0": 120, "y1": 240, "blocks": [], "widget": hidden_bottom},
    ]
    viewer._splits["p5_b0"] = hidden_split  # type: ignore[assignment]
    viewer._split_pages.add(5)
    assert viewer._vlayout is not None
    viewer._vlayout.register_split(0, 80.0)
    viewer._vlayout.register_split(5, 80.0)

    viewer._set_zoom(1.2)

    assert 0 in engine.rendered_pages
    assert 5 not in engine.rendered_pages
    assert {0, 5}.issubset(viewer._pending_split_rerenders)
    assert visible_top.width() == viewer._page_metas[0]["width"]
    assert visible_split.width() == viewer._page_metas[0]["width"]
    assert visible_bottom.width() == viewer._page_metas[0]["width"]
    assert visible_top.height() > 80


def test_pdf_viewer_restores_scroll_anchor_when_split_above_changes_height() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=4, blocks=[]))
    assert viewer._vlayout is not None

    viewer.scroll_to_page(3)
    anchor = viewer._capture_scroll_anchor()

    viewer._vlayout.register_split(0, 300.0)
    viewer._adjust_spacers({3})
    viewer._restore_scroll_anchor(anchor)

    assert viewer._capture_scroll_anchor() == (3, 0.0)
    assert viewer.verticalScrollBar().value() == int(viewer._vlayout.page_y(3))


def test_pdf_viewer_counts_collapsed_split_as_zero_virtual_height() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    split = QWidget()
    split._saved_height = 240
    split._collapsed = False
    split.hide()

    assert viewer._split_virtual_height(split) == 240.0

    split._collapsed = True

    assert viewer._split_virtual_height(split) == 0.0


def test_pdf_viewer_clear_last_split_restores_full_page_without_cached_pixmap() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="paragraph",
        bbox=(20.0, 120.0, 180.0, 160.0),
    )
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[block]))

    top = QWidget()
    split = QWidget()
    bottom = QWidget()
    for widget in (top, split, bottom):
        widget.setFixedSize(400, 80)
        widget.show()
        viewer._remember_widget_page(widget, 0)
    viewer._layout.insertWidget(1, top)
    viewer._layout.insertWidget(2, split)
    viewer._layout.insertWidget(3, bottom)
    viewer._page_segments[0] = [
        {"y0": 0, "y1": 120, "blocks": [block], "widget": top},
        {"split_id": block.id},
        {"y0": 120, "y1": 240, "blocks": [], "widget": bottom},
    ]
    split._saved_height = 80
    split._collapsed = False
    split._prev_split_h = 80
    viewer._splits[block.id] = split  # type: ignore[assignment]
    viewer._split_pages.add(0)
    viewer._active_pages.add(0)
    assert viewer._vlayout is not None
    viewer._vlayout.register_split(0, 80.0)

    viewer._on_clear_close(block.id)

    assert 0 not in viewer._split_pages
    assert len(viewer._page_segments[0]) == 1
    assert viewer._layout.indexOf(top) < 0
    assert viewer._layout.indexOf(bottom) < 0
    assert viewer._page_segments[0][0]["widget"] is not None
    assert 0 in engine.rendered_pages


def test_pdf_viewer_clear_one_of_multiple_splits_keeps_neighbor_segments_without_pixmap() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    block_a = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="paragraph a",
        bbox=(20.0, 80.0, 180.0, 120.0),
    )
    block_b = DocumentBlock(
        id="p0_b1",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="paragraph b",
        bbox=(20.0, 180.0, 180.0, 220.0),
    )
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[block_a, block_b]))

    top = QWidget()
    split_a = QWidget()
    middle = QWidget()
    split_b = QWidget()
    bottom = QWidget()
    for widget in (top, split_a, middle, split_b, bottom):
        widget.setFixedSize(400, 60)
        widget.show()
        viewer._remember_widget_page(widget, 0)
    for idx, widget in enumerate((top, split_a, middle, split_b, bottom), start=1):
        viewer._layout.insertWidget(idx, widget)
    viewer._page_segments[0] = [
        {"y0": 0, "y1": 80, "blocks": [block_a], "widget": top},
        {"split_id": block_a.id},
        {"y0": 80, "y1": 160, "blocks": [], "widget": middle},
        {"split_id": block_b.id},
        {"y0": 160, "y1": 240, "blocks": [block_b], "widget": bottom},
    ]
    for split in (split_a, split_b):
        split._saved_height = 80
        split._collapsed = False
        split._prev_split_h = 80
    viewer._splits[block_a.id] = split_a  # type: ignore[assignment]
    viewer._splits[block_b.id] = split_b  # type: ignore[assignment]
    viewer._split_pages.add(0)
    viewer._active_pages.add(0)
    assert viewer._vlayout is not None
    viewer._vlayout.register_split(0, 160.0)

    viewer._on_clear_close(block_a.id)

    assert 0 in viewer._split_pages
    assert block_a.id not in viewer._splits
    assert block_b.id in viewer._splits
    assert [seg.get("split_id") for seg in viewer._page_segments[0] if "split_id" in seg] == [block_b.id]
    assert viewer._layout.indexOf(top) >= 0
    assert viewer._layout.indexOf(middle) >= 0
    assert viewer._layout.indexOf(split_b) >= 0
    assert viewer._layout.indexOf(split_a) < 0
    assert 0 in viewer._pending_split_rerenders
    assert 0 in engine.rendered_pages


def test_pdf_viewer_clear_adjacent_annotation_split_keeps_translation_split() -> None:
    _app()
    engine = _DocEngine()
    viewer = PdfViewer(engine, _Config())  # type: ignore[arg-type]
    viewer.resize(640, 480)
    block = DocumentBlock(
        id="p0_b0",
        page_num=0,
        block_type=BlockType.PARAGRAPH,
        content="paragraph",
        bbox=(20.0, 80.0, 180.0, 120.0),
    )
    viewer.load_document(ParseResult(filepath="paper.pdf", page_count=1, blocks=[block]))

    top = QWidget()
    translation = QWidget()
    annotation = QWidget()
    bottom = QWidget()
    for widget in (top, translation, annotation, bottom):
        widget.setFixedSize(400, 60)
        widget.show()
        viewer._remember_widget_page(widget, 0)
    for idx, widget in enumerate((top, translation, annotation, bottom), start=1):
        viewer._layout.insertWidget(idx, widget)

    annotation_id = viewer.annotation_split_id(block.id)
    viewer._page_segments[0] = [
        {"y0": 0, "y1": 120, "blocks": [block], "widget": top},
        {"split_id": block.id},
        {"split_id": annotation_id},
        {"y0": 120, "y1": 240, "blocks": [], "widget": bottom},
    ]
    for split in (translation, annotation):
        split._saved_height = 60
        split._collapsed = False
        split._prev_split_h = 60
    viewer._splits[block.id] = translation  # type: ignore[assignment]
    viewer._splits[annotation_id] = annotation  # type: ignore[assignment]
    viewer._split_sources[block.id] = block.id
    viewer._split_sources[annotation_id] = block.id
    viewer._split_pages.add(0)
    viewer._active_pages.add(0)
    assert viewer._vlayout is not None
    viewer._vlayout.register_split(0, 120.0)

    viewer._on_clear_close(annotation_id)

    assert annotation_id not in viewer._splits
    assert block.id in viewer._splits
    assert [seg.get("split_id") for seg in viewer._page_segments[0] if "split_id" in seg] == [block.id]
    assert viewer._layout.indexOf(translation) >= 0
    assert viewer._layout.indexOf(annotation) < 0


def test_virtual_page_layout_uses_binary_search_semantics() -> None:
    layout = _VirtualPageLayout({0: 100.0, 1: 200.0, 2: 150.0})

    assert layout.page_at_y(0) == 0
    assert layout.page_at_y(120) == 1
    assert layout.page_at_y(310) == 2
    assert layout.page_range_for_viewport(90, 40, 0) == [0, 1]
    assert layout.page_range_for_viewport(305, 20, 0) == [2]

    layout.register_split(1, 50.0)

    assert layout.page_at_y(330) == 1
    assert layout.page_at_y(360) == 2
    assert layout.page_range_for_viewport(90, 270, 0) == [0, 1, 2]

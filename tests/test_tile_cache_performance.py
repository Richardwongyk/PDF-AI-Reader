from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from src.ui.pdf_viewer import _LazyPageWidget


class _Cache:
    def __init__(self) -> None:
        self.put_calls = 0
        self.get_calls = 0

    def put(self, *_args, **_kwargs) -> None:
        self.put_calls += 1

    def get(self, *_args, **_kwargs):
        self.get_calls += 1
        return None


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_small_page_render_does_not_preslice_tile_cache() -> None:
    _app()
    widget = _LazyPageWidget(page_num=0, width_px=986, height_px=1276)
    pixmap = QPixmap(986, 1276)
    pixmap.setDevicePixelRatio(1.5)
    cache = _Cache()

    widget.render(pixmap, [], 2.0, lambda _overlay: None, tile_cache=cache)

    assert cache.put_calls == 0


def test_large_page_render_still_preslices_tile_cache() -> None:
    _app()
    widget = _LazyPageWidget(page_num=0, width_px=3000, height_px=3000)
    pixmap = QPixmap(3000, 3000)
    pixmap.setDevicePixelRatio(1.5)
    cache = _Cache()

    widget.render(pixmap, [], 2.0, lambda _overlay: None, tile_cache=cache)

    assert cache.put_calls > 0


def test_low_res_fallback_on_large_page_still_uses_tile_path() -> None:
    _app()
    widget = _LazyPageWidget(page_num=0, width_px=5000, height_px=5000)
    fallback = QPixmap(1000, 1000)

    assert widget._should_paint_tiles(fallback)


def test_large_tile_paint_uses_whole_page_fallback_on_cache_miss() -> None:
    _app()
    widget = _LazyPageWidget(page_num=0, width_px=5000, height_px=5000)
    fallback = QPixmap(1000, 1000)
    fallback.setDevicePixelRatio(1.0)
    cache = _Cache()
    widget._rendered = True
    widget._full_pixmap = fallback
    widget._tile_cache = cache
    widget._zoom = 5.0
    drawn_full: list[bool] = []
    widget._draw_full_pixmap = lambda _painter, _pixmap: drawn_full.append(True)  # type: ignore[method-assign]
    canvas = QPixmap(5000, 5000)
    painter = QPainter(canvas)

    widget._paint_tiles(painter, 1280)
    painter.end()

    assert drawn_full == [True]
    assert cache.get_calls > 0
    assert cache.put_calls == 0

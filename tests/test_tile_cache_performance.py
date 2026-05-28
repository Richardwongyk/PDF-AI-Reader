from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from src.ui.pdf_viewer import _LazyPageWidget


class _Cache:
    def __init__(self) -> None:
        self.put_calls = 0

    def put(self, *_args, **_kwargs) -> None:
        self.put_calls += 1


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

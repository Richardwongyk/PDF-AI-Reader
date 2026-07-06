from PySide6.QtGui import QColor, QPalette

from src.core.models import AppConfig
from src.ui.theme import (
    _make_black_white_palette,
    _make_dark_palette,
    _make_light_gray_palette,
    _make_sepia_palette,
    normalize_theme_name,
)


def _luminance(color: QColor) -> float:
    values = []
    for value in (color.redF(), color.greenF(), color.blueF()):
        values.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * values[0] + 0.7152 * values[1] + 0.0722 * values[2]


def _contrast_ratio(a: QColor, b: QColor) -> float:
    lighter = max(_luminance(a), _luminance(b))
    darker = min(_luminance(a), _luminance(b))
    return (lighter + 0.05) / (darker + 0.05)


def test_default_ui_theme_is_dark() -> None:
    assert AppConfig().ui.theme == "dark"


def test_dark_theme_background_contract() -> None:
    palette = _make_dark_palette()
    assert palette.color(QPalette.ColorRole.Window).name() == "#1e1e2e"
    assert palette.color(QPalette.ColorRole.Base).name() == "#2a2a3c"


def test_old_light_theme_key_maps_to_light_gray() -> None:
    assert normalize_theme_name("light") == "light_gray"


def test_light_gray_theme_uses_gray_surfaces_and_dark_text() -> None:
    palette = _make_light_gray_palette()
    assert palette.color(QPalette.ColorRole.Window).name() == "#dcdcdc"
    assert palette.color(QPalette.ColorRole.Base).name() == "#e4e4e4"
    assert palette.color(QPalette.ColorRole.Text).name() == "#2a2a2a"


def test_sepia_theme_uses_remote_beige_palette() -> None:
    palette = _make_sepia_palette()
    assert palette.color(QPalette.ColorRole.Window).name() == "#faf4e3"
    assert palette.color(QPalette.ColorRole.Base).name() == "#fffbf0"
    assert palette.color(QPalette.ColorRole.Text).name() == "#191928"


def test_black_white_theme_uses_black_surfaces_and_white_text() -> None:
    palette = _make_black_white_palette()
    assert palette.color(QPalette.ColorRole.Window).name() == "#1a1a1a"
    assert palette.color(QPalette.ColorRole.Base).name() == "#242424"
    assert palette.color(QPalette.ColorRole.Text).name() == "#e8e8e8"


def test_main_window_theme_stylesheets_keep_sidebar_contrast() -> None:
    from src.ui.main_window import MainWindow

    light_gray_style = MainWindow._main_window_theme_stylesheet(object(), "light_gray")
    assert "QDockWidget, QDockWidget QWidget" in light_gray_style
    assert "background: #e4e4e4;" in light_gray_style
    assert "color: #2a2a2a;" in light_gray_style

    sepia_style = MainWindow._main_window_theme_stylesheet(object(), "sepia")
    assert "background: #faf4e3;" in sepia_style
    assert "background: #fffbf0;" in sepia_style
    assert "color: #191928;" in sepia_style

    black_white_style = MainWindow._main_window_theme_stylesheet(object(), "black_white")
    assert "background: #1a1a1a;" in black_white_style
    assert "background: #242424;" in black_white_style
    assert "color: #e8e8e8;" in black_white_style


def test_settings_dialog_stylesheets_keep_controls_readable() -> None:
    from src.ui.main_window import MainWindow

    light_gray_style = MainWindow._settings_dialog_stylesheet(object(), "light_gray")
    assert "QDialog#settings_dialog QLineEdit" in light_gray_style
    assert "background: #e4e4e4;" in light_gray_style
    assert "color: #2a2a2a;" in light_gray_style
    assert "QComboBox QAbstractItemView" in light_gray_style

    sepia_style = MainWindow._settings_dialog_stylesheet(object(), "sepia")
    assert "background: #faf4e3;" in sepia_style
    assert "background: #fffbf0;" in sepia_style
    assert "color: #191928;" in sepia_style
    assert "QComboBox QAbstractItemView" in sepia_style

    black_white_style = MainWindow._settings_dialog_stylesheet(object(), "black_white")
    assert "background: #242424;" in black_white_style
    assert "background: #1e1e1e;" in black_white_style
    assert "color: #e8e8e8;" in black_white_style
    assert "QComboBox QAbstractItemView" in black_white_style


def test_theme_core_contrast() -> None:
    for make_palette in (
        _make_dark_palette,
        _make_sepia_palette,
        _make_light_gray_palette,
        _make_black_white_palette,
    ):
        palette = make_palette()
        pairs = [
            (QPalette.ColorRole.Window, QPalette.ColorRole.WindowText),
            (QPalette.ColorRole.Base, QPalette.ColorRole.Text),
            (QPalette.ColorRole.Button, QPalette.ColorRole.ButtonText),
            (QPalette.ColorRole.Highlight, QPalette.ColorRole.HighlightedText),
        ]
        for bg_role, fg_role in pairs:
            assert _contrast_ratio(palette.color(bg_role), palette.color(fg_role)) >= 4.5

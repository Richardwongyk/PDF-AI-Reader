"""
主题模块 —— 使用 QPalette 控制整体配色，QSS 仅用于裂缝等定制组件。

设计原则：不通过 setStyleSheet 在顶层设置全局 QSS，避免级联破坏子控件。
全局配色通过 QApplication.setPalette() 实现，定制组件各自在本地设置 QSS。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QWidget


# =============================================================================
# 裂缝容器 (SplitWidget) 专用样式 — 仅用于 SplitWidget 自身 setStyleSheet
# =============================================================================

SPLIT_WIDGET_STYLE: str = """
QFrame#split_container {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #f0f4ff,
        stop: 1 #e8d5f5
    );
    border-radius: 12px;
    border: 1px solid #c0c0e0;
    margin: 2px 0px;
    padding: 4px 8px;
    color: #222;
}

QLabel#header_title {
    font-size: 14px;
    font-weight: bold;
    color: #4a3f6b;
    padding: 4px 0px;
}

QLabel#context_label {
    font-size: 11px;
    color: #8888aa;
    font-style: italic;
    padding: 2px 0px 8px 0px;
}

QTextEdit#input_area {
    border: 1px solid #d0d0e8;
    border-radius: 6px;
    padding: 8px;
    background: rgba(255, 255, 255, 0.75);
    color: #222;
    font-size: 13px;
    min-height: 80px;
    max-height: 300px;
}

QPushButton#send_button {
    background: #6c5ce7;
    color: white;
    border-radius: 6px;
    padding: 6px 18px;
    font-weight: bold;
    min-width: 60px;
}

QPushButton#send_button:hover {
    background: #5a4bd1;
}

QPushButton#send_button:pressed {
    background: #4a3db0;
}

QPushButton#close_button,
QPushButton#collapse_button {
    background: transparent;
    border: 1px solid #d0d0e8;
    border-radius: 6px;
    font-size: 12px;
    color: #8888aa;
    padding: 4px 10px;
    font-weight: bold;
}

QPushButton#close_button:hover,
QPushButton#collapse_button:hover {
    color: #4a3f6b;
    background: rgba(108, 92, 231, 0.08);
}

QPushButton#action_button {
    background: transparent;
    border: 1px solid #d0d0e8;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 12px;
    color: #444;
}

QPushButton#action_button:hover {
    background: rgba(108, 92, 231, 0.1);
    border-color: #6c5ce7;
}
"""

# 暗色主题 SplitWidget 样式
SPLIT_WIDGET_STYLE_DARK: str = """
QFrame#split_container {
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #1e1e2e,
        stop: 1 #2a1a3a
    );
    border-radius: 12px;
    border: 1px solid #3a3a5a;
    margin: 2px 0px;
    padding: 4px 8px;
    color: #cdd6f4;
}

QLabel#header_title {
    font-size: 14px;
    font-weight: bold;
    color: #cdd6f4;
    padding: 4px 0px;
}

QLabel#context_label {
    font-size: 11px;
    color: #a6adc8;
    font-style: italic;
    padding: 2px 0px 8px 0px;
}

QTextEdit#input_area {
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px;
    background: rgba(30, 30, 46, 0.75);
    color: #cdd6f4;
    font-size: 13px;
    min-height: 80px;
    max-height: 300px;
}

QPushButton#send_button {
    background: #6c5ce7;
    color: white;
    border-radius: 6px;
    padding: 6px 18px;
    font-weight: bold;
    min-width: 60px;
}

QPushButton#send_button:hover {
    background: #5a4bd1;
}

QPushButton#send_button:pressed {
    background: #4a3db0;
}

QPushButton#close_button,
QPushButton#collapse_button {
    background: transparent;
    border: 1px solid #45475a;
    border-radius: 6px;
    font-size: 12px;
    color: #a6adc8;
    padding: 4px 10px;
    font-weight: bold;
}

QPushButton#close_button:hover,
QPushButton#collapse_button:hover {
    color: #cdd6f4;
    background: rgba(108, 92, 231, 0.15);
}

QPushButton#action_button {
    background: transparent;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 12px;
    color: #cdd6f4;
}

QPushButton#action_button:hover {
    background: rgba(108, 92, 231, 0.15);
    border-color: #6c5ce7;
}
"""


def get_split_style(theme_name: str) -> str:
    """根据主题返回对应的 SplitWidget QSS。"""
    if theme_name == "dark":
        return SPLIT_WIDGET_STYLE_DARK
    return SPLIT_WIDGET_STYLE

# =============================================================================
# QPalette 主题
# =============================================================================


def normalize_theme_name(theme_name: str) -> str:
    if theme_name == "light":
        return "light_gray"
    if theme_name in {"dark", "sepia", "light_gray", "black_white"}:
        return theme_name
    return "dark"


def _make_light_gray_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.WindowText, QColor(42, 42, 42))
    p.setColor(QPalette.ColorRole.Base, QColor(228, 228, 228))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(216, 216, 216))
    p.setColor(QPalette.ColorRole.Text, QColor(42, 42, 42))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(42, 42, 42))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(228, 228, 228))
    p.setColor(QPalette.ColorRole.Button, QColor(212, 212, 212))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(42, 42, 42))
    p.setColor(QPalette.ColorRole.Light, QColor(228, 228, 228))
    p.setColor(QPalette.ColorRole.Midlight, QColor(216, 216, 216))
    p.setColor(QPalette.ColorRole.Dark, QColor(170, 170, 170))
    p.setColor(QPalette.ColorRole.Mid, QColor(185, 185, 185))
    p.setColor(QPalette.ColorRole.Shadow, QColor(150, 150, 150))
    p.setColor(QPalette.ColorRole.Highlight, QColor(91, 155, 213))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(42, 42, 42))
    p.setColor(QPalette.ColorRole.Link, QColor(91, 155, 213))
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(70, 130, 180))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(150, 150, 150))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(150, 150, 150))
    return p


def _make_black_white_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(26, 26, 26))
    p.setColor(QPalette.ColorRole.WindowText, QColor(232, 232, 232))
    p.setColor(QPalette.ColorRole.Base, QColor(36, 36, 36))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(30, 30, 30))
    p.setColor(QPalette.ColorRole.Text, QColor(232, 232, 232))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(232, 232, 232))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(26, 26, 26))
    p.setColor(QPalette.ColorRole.Button, QColor(44, 44, 44))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(232, 232, 232))
    p.setColor(QPalette.ColorRole.Light, QColor(50, 50, 50))
    p.setColor(QPalette.ColorRole.Midlight, QColor(40, 40, 40))
    p.setColor(QPalette.ColorRole.Dark, QColor(90, 90, 90))
    p.setColor(QPalette.ColorRole.Mid, QColor(70, 70, 70))
    p.setColor(QPalette.ColorRole.Shadow, QColor(15, 15, 15))
    p.setColor(QPalette.ColorRole.Highlight, QColor(108, 92, 231))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.Link, QColor(140, 180, 255))
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(180, 155, 255))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(120, 120, 120))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(120, 120, 120))
    return p


def _make_light_palette() -> QPalette:
    """构建素白（学术）主题 QPalette。"""
    p = QPalette()

    # 窗口/背景
    p.setColor(QPalette.ColorRole.Window, QColor(250, 250, 250))
    p.setColor(QPalette.ColorRole.WindowText, QColor(30, 30, 30))
    p.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(245, 245, 250))
    p.setColor(QPalette.ColorRole.Text, QColor(30, 30, 30))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(17, 24, 39))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))

    # 按钮
    p.setColor(QPalette.ColorRole.Button, QColor(245, 245, 250))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(30, 30, 30))
    p.setColor(QPalette.ColorRole.Light, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.Midlight, QColor(240, 240, 245))
    p.setColor(QPalette.ColorRole.Dark, QColor(180, 180, 190))
    p.setColor(QPalette.ColorRole.Mid, QColor(200, 200, 210))
    p.setColor(QPalette.ColorRole.Shadow, QColor(150, 150, 160))

    # 高亮（选中项）
    p.setColor(QPalette.ColorRole.Highlight, QColor(108, 92, 231))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

    # 链接
    p.setColor(QPalette.ColorRole.Link, QColor(108, 92, 231))
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(130, 80, 200))

    # 禁用状态
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(160, 160, 160))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(160, 160, 160))

    return p


def _make_dark_palette() -> QPalette:
    """构建暗夜主题 QPalette。"""
    p = QPalette()

    p.setColor(QPalette.ColorRole.Window, QColor(30, 30, 46))
    p.setColor(QPalette.ColorRole.WindowText, QColor(205, 214, 244))
    p.setColor(QPalette.ColorRole.Base, QColor(42, 42, 60))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(37, 37, 56))
    p.setColor(QPalette.ColorRole.Text, QColor(205, 214, 244))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(17, 24, 39))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))

    p.setColor(QPalette.ColorRole.Button, QColor(50, 50, 70))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(205, 214, 244))
    p.setColor(QPalette.ColorRole.Light, QColor(60, 60, 80))
    p.setColor(QPalette.ColorRole.Midlight, QColor(45, 45, 65))
    p.setColor(QPalette.ColorRole.Dark, QColor(100, 100, 120))
    p.setColor(QPalette.ColorRole.Mid, QColor(80, 80, 100))
    p.setColor(QPalette.ColorRole.Shadow, QColor(20, 20, 30))

    p.setColor(QPalette.ColorRole.Highlight, QColor(108, 92, 231))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

    p.setColor(QPalette.ColorRole.Link, QColor(137, 180, 250))
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(180, 160, 250))

    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(120, 120, 140))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(120, 120, 140))

    return p


def _make_sepia_palette() -> QPalette:
    """构建米黄主题 QPalette。"""
    p = QPalette()

    p.setColor(QPalette.ColorRole.Window, QColor(250, 244, 227))
    p.setColor(QPalette.ColorRole.WindowText, QColor(25, 25, 40))
    p.setColor(QPalette.ColorRole.Base, QColor(255, 251, 240))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(248, 240, 220))
    p.setColor(QPalette.ColorRole.Text, QColor(25, 25, 40))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(25, 25, 40))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 251, 240))

    p.setColor(QPalette.ColorRole.Button, QColor(244, 236, 218))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(25, 25, 40))
    p.setColor(QPalette.ColorRole.Light, QColor(255, 251, 240))
    p.setColor(QPalette.ColorRole.Midlight, QColor(248, 240, 220))
    p.setColor(QPalette.ColorRole.Dark, QColor(190, 178, 160))
    p.setColor(QPalette.ColorRole.Mid, QColor(210, 198, 180))
    p.setColor(QPalette.ColorRole.Shadow, QColor(165, 150, 130))

    p.setColor(QPalette.ColorRole.Highlight, QColor(212, 144, 42))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(25, 25, 40))

    p.setColor(QPalette.ColorRole.Link, QColor(212, 144, 42))
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(170, 110, 30))

    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(170, 165, 155))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(170, 165, 155))

    return p


def apply_theme(theme_name: str) -> None:
    """应用指定主题到整个应用程序。

    使用 QApplication.setPalette() 设置全局调色板，
    不通过 QSS 级联，确保所有原生控件正常渲染。

    Args:
        theme_name: "dark" / "sepia" / "light_gray" / "black_white"
    """
    themes: dict[str, QPalette] = {
        "dark": _make_dark_palette(),
        "sepia": _make_sepia_palette(),
        "light_gray": _make_light_gray_palette(),
        "black_white": _make_black_white_palette(),
    }
    palette = themes.get(normalize_theme_name(theme_name), _make_dark_palette())
    app = QApplication.instance()
    if app is not None:
        app.setPalette(palette)

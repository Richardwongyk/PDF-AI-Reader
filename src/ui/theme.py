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
    """构建护眼羊皮纸主题 QPalette。"""
    p = QPalette()

    p.setColor(QPalette.ColorRole.Window, QColor(245, 222, 179))
    p.setColor(QPalette.ColorRole.WindowText, QColor(60, 40, 20))
    p.setColor(QPalette.ColorRole.Base, QColor(252, 240, 210))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(245, 230, 195))
    p.setColor(QPalette.ColorRole.Text, QColor(60, 40, 20))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(17, 24, 39))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))

    p.setColor(QPalette.ColorRole.Button, QColor(240, 220, 180))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(60, 40, 20))
    p.setColor(QPalette.ColorRole.Light, QColor(255, 240, 210))
    p.setColor(QPalette.ColorRole.Midlight, QColor(245, 225, 190))
    p.setColor(QPalette.ColorRole.Dark, QColor(180, 160, 130))
    p.setColor(QPalette.ColorRole.Mid, QColor(200, 180, 150))
    p.setColor(QPalette.ColorRole.Shadow, QColor(150, 130, 100))

    p.setColor(QPalette.ColorRole.Highlight, QColor(180, 140, 80))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

    p.setColor(QPalette.ColorRole.Link, QColor(120, 80, 40))
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(150, 100, 60))

    return p


def apply_theme(theme_name: str) -> None:
    """应用指定主题到整个应用程序。

    使用 QApplication.setPalette() 设置全局调色板，
    不通过 QSS 级联，确保所有原生控件正常渲染。

    Args:
        theme_name: "light" / "dark" / "sepia"
    """
    themes: dict[str, QPalette] = {
        "light": _make_light_palette(),
        "dark": _make_dark_palette(),
        "sepia": _make_sepia_palette(),
    }
    palette = themes.get(theme_name, _make_light_palette())
    QApplication.instance().setPalette(palette)

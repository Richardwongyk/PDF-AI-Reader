"""
主题模块 —— 使用 QPalette 控制整体配色，QSS 仅用于定制组件局部样式。

三款主题
--------
- light → 米黄：暖米黄底 + 深灰蓝字 + 琥珀强调
- dark  → 浅灰：浅灰底 + 米白字 + 浅蓝强调
- sepia → 黑白：暖白底 + 黑色字 + 靛紫强调，翻译框浅灰

设计原则
--------
- 全局配色通过 QApplication.setPalette() 实现，不通过全局 setStyleSheet 级联。
- 定制组件（SplitWidget、面板切换按钮、工具栏手柄）在本地通过 setStyleSheet
  设置 QSS，并通过 get_xxx_style(theme) 函数返回对应主题的样式字符串。
- 任何 QSS 中使用的颜色值都应取自本模块的主题感知样式变量。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


# ═══════════════════════════════════════════════════════════════════════════════
# 设计系统色板
# ═══════════════════════════════════════════════════════════════════════════════

_DANGER = "#e74c3c"

# ── 米黄 (light) ──────────────────────────────────────────────────────────────
_ACCENT_MH       = "#d4902a"   # 琥珀
_ACCENT_MH_HOVER = "#b87a20"
_ACCENT_MH_SOFT  = "#fdf0de"
_SURFACE_MH_0    = "#faf4e3"   # 米黄底 (250,244,227)
_SURFACE_MH_1    = "#f0e8d4"
_SURFACE_MH_BORDER = "#d8ccb8"
_TEXT_MH_PRIMARY   = "#191928"   # 深灰蓝 (25,25,40)
_TEXT_MH_SECONDARY = "#6b6b78"

# ── 浅灰 (dark) ───────────────────────────────────────────────────────────────
_ACCENT_QH        = "#5b9bd5"   # 浅蓝
_ACCENT_QH_HOVER  = "#4a8ac4"
_ACCENT_QH_SOFT   = "#e8f1f8"
_SURFACE_QH_0     = "#dcdcdc"   # 浅灰底 (220,220,220)
_SURFACE_QH_1     = "#d4d4d4"
_SURFACE_QH_BORDER = "#bebebe"
_TEXT_QH_PRIMARY   = "#2a2a2a"   # 深灰字
_TEXT_QH_SECONDARY = "#6e6e6e"

# ── 黑白 (sepia) —— 黑底白字 ──────────────────────────────────────────────────
_ACCENT_HB        = "#6c5ce7"
_ACCENT_HB_HOVER  = "#5a4bd1"
_ACCENT_HB_SOFT   = "#2a2040"
_SURFACE_HB_0     = "#1a1a1a"     # 黑底
_SURFACE_HB_1     = "#242424"
_SURFACE_HB_BORDER = "#3a3a3a"
_TEXT_HB_PRIMARY   = "#e8e8e8"     # 白字
_TEXT_HB_SECONDARY = "#999999"


# ═══════════════════════════════════════════════════════════════════════════════
# SplitWidget 裂缝容器样式
# ═══════════════════════════════════════════════════════════════════════════════

SPLIT_WIDGET_STYLE: str = f"""
QFrame#split_container {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {_SURFACE_MH_1},
        stop: 0.5 #f5edd8,
        stop: 1 #ece0d0
    );
    border-radius: 14px;
    border: 1px solid {_SURFACE_MH_BORDER};
    margin: 4px 2px;
    padding: 6px 10px;
    color: {_TEXT_MH_PRIMARY};
}}

QLabel#header_title {{
    font-size: 14px; font-weight: 700; color: #6b5018;
    padding: 4px 0px;
}}
QLabel#context_label {{
    font-size: 12px; color: {_TEXT_MH_SECONDARY}; font-style: italic;
    padding: 2px 0px 10px 0px;
}}

QTextEdit#input_area {{
    border: 1px solid {_SURFACE_MH_BORDER}; border-radius: 8px;
    padding: 10px 12px; background: rgba(255,252,244,0.80);
    color: {_TEXT_MH_PRIMARY}; font-size: 13px;
    min-height: 84px; max-height: 300px;
}}
QTextEdit#input_area:focus {{
    border-color: {_ACCENT_MH};
    background: rgba(255,252,244,0.95);
}}

QPushButton#send_button {{
    background: {_ACCENT_MH}; color: #fff; border: none;
    border-radius: 8px; padding: 6px 22px; font-size: 13px;
    font-weight: 700; min-width: 64px;
}}
QPushButton#send_button:hover {{ background: {_ACCENT_MH_HOVER}; }}
QPushButton#send_button:pressed {{ background: #a06818; }}

QPushButton#close_button {{
    background: transparent; border: none; font-size: 20px;
    color: {_TEXT_MH_SECONDARY}; padding: 2px 8px; border-radius: 4px;
}}
QPushButton#close_button:hover {{
    color: {_DANGER}; background: rgba(231,76,60,0.08);
}}

QPushButton#action_button {{
    background: transparent; border: 1px solid {_SURFACE_MH_BORDER};
    border-radius: 6px; padding: 5px 12px; font-size: 12px;
    color: {_TEXT_MH_SECONDARY};
}}
QPushButton#action_button:hover {{
    background: rgba(212,144,42,0.08); border-color: {_ACCENT_MH};
    color: {_ACCENT_MH};
}}
"""

SPLIT_WIDGET_STYLE_DARK: str = f"""
QFrame#split_container {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {_SURFACE_QH_1},
        stop: 0.5 #eef3f7,
        stop: 1 #e0e8f0
    );
    border-radius: 14px; border: 1px solid {_SURFACE_QH_BORDER};
    margin: 4px 2px; padding: 6px 10px; color: {_TEXT_QH_PRIMARY};
}}

QLabel#header_title {{
    font-size: 14px; font-weight: 700; color: #3a6ea5;
    padding: 4px 0px;
}}
QLabel#context_label {{
    font-size: 12px; color: {_TEXT_QH_SECONDARY}; font-style: italic;
    padding: 2px 0px 10px 0px;
}}

QTextEdit#input_area {{
    border: 1px solid {_SURFACE_QH_BORDER}; border-radius: 8px;
    padding: 10px 12px; background: rgba(255,255,255,0.80);
    color: {_TEXT_QH_PRIMARY}; font-size: 13px;
    min-height: 84px; max-height: 300px;
}}
QTextEdit#input_area:focus {{
    border-color: {_ACCENT_QH};
    background: rgba(255,255,255,0.95);
}}

QPushButton#send_button {{
    background: {_ACCENT_QH}; color: #fff; border: none;
    border-radius: 8px; padding: 6px 22px; font-size: 13px;
    font-weight: 700; min-width: 64px;
}}
QPushButton#send_button:hover {{ background: {_ACCENT_QH_HOVER}; }}
QPushButton#send_button:pressed {{ background: #3a7ab4; }}

QPushButton#close_button {{
    background: transparent; border: none; font-size: 20px;
    color: {_TEXT_QH_SECONDARY}; padding: 2px 8px; border-radius: 4px;
}}
QPushButton#close_button:hover {{
    color: {_DANGER}; background: rgba(231,76,60,0.06);
}}

QPushButton#action_button {{
    background: transparent; border: 1px solid {_SURFACE_QH_BORDER};
    border-radius: 6px; padding: 5px 12px; font-size: 12px;
    color: {_TEXT_QH_SECONDARY};
}}
QPushButton#action_button:hover {{
    background: rgba(91,155,213,0.08); border-color: {_ACCENT_QH};
    color: {_ACCENT_QH};
}}
"""

SPLIT_WIDGET_STYLE_SEPIA: str = f"""
QFrame#split_container {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {_SURFACE_HB_1},
        stop: 0.5 #2a2040,
        stop: 1 #221a34
    );
    border-radius: 14px; border: 1px solid {_SURFACE_HB_BORDER};
    margin: 4px 2px; padding: 6px 10px; color: {_TEXT_HB_PRIMARY};
}}

QLabel#header_title {{
    font-size: 14px; font-weight: 700; color: #c8b8f0;
    padding: 4px 0px;
}}
QLabel#context_label {{
    font-size: 12px; color: {_TEXT_HB_SECONDARY}; font-style: italic;
    padding: 2px 0px 10px 0px;
}}

QTextEdit#input_area {{
    border: 1px solid {_SURFACE_HB_BORDER}; border-radius: 8px;
    padding: 10px 12px; background: rgba(255,255,255,0.80);
    color: {_TEXT_HB_PRIMARY}; font-size: 13px;
    min-height: 84px; max-height: 300px;
}}
QTextEdit#input_area:focus {{
    border-color: {_ACCENT_HB};
    background: rgba(255,255,255,0.95);
}}

QPushButton#send_button {{
    background: {_ACCENT_HB}; color: #fff; border: none;
    border-radius: 8px; padding: 6px 22px; font-size: 13px;
    font-weight: 700; min-width: 64px;
}}
QPushButton#send_button:hover {{ background: {_ACCENT_HB_HOVER}; }}
QPushButton#send_button:pressed {{ background: #4a3db0; }}

QPushButton#close_button {{
    background: transparent; border: none; font-size: 20px;
    color: {_TEXT_HB_SECONDARY}; padding: 2px 8px; border-radius: 4px;
}}
QPushButton#close_button:hover {{
    color: {_DANGER}; background: rgba(231,76,60,0.08);
}}

QPushButton#action_button {{
    background: transparent; border: 1px solid {_SURFACE_HB_BORDER};
    border-radius: 6px; padding: 5px 12px; font-size: 12px;
    color: {_TEXT_HB_SECONDARY};
}}
QPushButton#action_button:hover {{
    background: rgba(108,92,231,0.08); border-color: {_ACCENT_HB};
    color: {_ACCENT_HB};
}}
"""


def get_split_style(theme_name: str) -> str:
    """根据当前主题返回对应的 SplitWidget QSS。"""
    styles = {
        "dark": SPLIT_WIDGET_STYLE_DARK,
        "sepia": SPLIT_WIDGET_STYLE_SEPIA,
    }
    return styles.get(theme_name, SPLIT_WIDGET_STYLE)


# ═══════════════════════════════════════════════════════════════════════════════
# 面板切换按钮样式
# ═══════════════════════════════════════════════════════════════════════════════

_PANEL_TOGGLE_LIGHT: str = f"""
QToolButton#left_panel_toggle_button,
QToolButton#right_panel_toggle_button {{
    background: {_SURFACE_MH_1}; color: {_ACCENT_MH};
    border: 1px solid {_SURFACE_MH_BORDER}; border-radius: 7px;
    padding: 0; font-size: 18px; font-weight: 700;
}}
QToolButton#left_panel_toggle_button:hover,
QToolButton#right_panel_toggle_button:hover {{
    background: {_ACCENT_MH_SOFT}; color: {_ACCENT_MH_HOVER};
    border-color: {_ACCENT_MH};
}}
QToolButton#left_panel_toggle_button:pressed,
QToolButton#right_panel_toggle_button:pressed {{
    background: #f5e0c0; color: #a06818; border-color: {_ACCENT_MH_HOVER};
}}
QToolTip {{
    color: {_TEXT_MH_PRIMARY}; background-color: {_SURFACE_MH_1};
    border: 1px solid {_SURFACE_MH_BORDER}; padding: 5px 8px; border-radius: 4px;
}}
"""

_PANEL_TOGGLE_DARK: str = f"""
QToolButton#left_panel_toggle_button,
QToolButton#right_panel_toggle_button {{
    background: {_SURFACE_QH_1}; color: {_ACCENT_QH};
    border: 1px solid {_SURFACE_QH_BORDER}; border-radius: 7px;
    padding: 0; font-size: 18px; font-weight: 700;
}}
QToolButton#left_panel_toggle_button:hover,
QToolButton#right_panel_toggle_button:hover {{
    background: {_ACCENT_QH_SOFT}; color: #4a8ac4;
    border-color: {_ACCENT_QH};
}}
QToolButton#left_panel_toggle_button:pressed,
QToolButton#right_panel_toggle_button:pressed {{
    background: #d0e0f0; color: #3a7ab4; border-color: #4a8ac4;
}}
QToolTip {{
    color: {_TEXT_QH_PRIMARY}; background-color: {_SURFACE_QH_1};
    border: 1px solid {_SURFACE_QH_BORDER}; padding: 5px 8px; border-radius: 4px;
}}
"""

_PANEL_TOGGLE_SEPIA: str = f"""
QToolButton#left_panel_toggle_button,
QToolButton#right_panel_toggle_button {{
    background: {_SURFACE_HB_1}; color: {_ACCENT_HB};
    border: 1px solid {_SURFACE_HB_BORDER}; border-radius: 7px;
    padding: 0; font-size: 18px; font-weight: 700;
}}
QToolButton#left_panel_toggle_button:hover,
QToolButton#right_panel_toggle_button:hover {{
    background: {_ACCENT_HB_SOFT}; color: {_ACCENT_HB_HOVER};
    border-color: {_ACCENT_HB};
}}
QToolButton#left_panel_toggle_button:pressed,
QToolButton#right_panel_toggle_button:pressed {{
    background: #d8d0f0; color: #4a3db0; border-color: {_ACCENT_HB_HOVER};
}}
QToolTip {{
    color: {_TEXT_HB_PRIMARY}; background-color: {_SURFACE_HB_1};
    border: 1px solid {_SURFACE_HB_BORDER}; padding: 5px 8px; border-radius: 4px;
}}
"""


def get_panel_toggle_style(theme_name: str) -> str:
    styles = {"dark": _PANEL_TOGGLE_DARK, "sepia": _PANEL_TOGGLE_SEPIA}
    return styles.get(theme_name, _PANEL_TOGGLE_LIGHT)


# ═══════════════════════════════════════════════════════════════════════════════
# 工具栏恢复手柄样式
# ═══════════════════════════════════════════════════════════════════════════════

_TOOLBAR_HANDLE_LIGHT: str = f"""
QFrame#toolbar_restore_handle {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {_SURFACE_MH_0}, stop:1 {_SURFACE_MH_1});
    border-bottom: 1px solid {_ACCENT_MH};
}}
QToolTip {{ color:{_TEXT_MH_PRIMARY}; background:{_SURFACE_MH_1};
    border:1px solid {_SURFACE_MH_BORDER}; padding:5px 8px; border-radius:4px; }}
"""

_TOOLBAR_HANDLE_DARK: str = f"""
QFrame#toolbar_restore_handle {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {_SURFACE_QH_0}, stop:1 {_SURFACE_QH_1});
    border-bottom: 1px solid {_ACCENT_QH};
}}
QToolTip {{ color:{_TEXT_QH_PRIMARY}; background:{_SURFACE_QH_1};
    border:1px solid {_SURFACE_QH_BORDER}; padding:5px 8px; border-radius:4px; }}
"""

_TOOLBAR_HANDLE_SEPIA: str = f"""
QFrame#toolbar_restore_handle {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {_SURFACE_HB_0}, stop:1 {_SURFACE_HB_1});
    border-bottom: 1px solid {_ACCENT_HB};
}}
QToolTip {{ color:{_TEXT_HB_PRIMARY}; background:{_SURFACE_HB_1};
    border:1px solid {_SURFACE_HB_BORDER}; padding:5px 8px; border-radius:4px; }}
"""


def get_toolbar_handle_style(theme_name: str) -> str:
    styles = {"dark": _TOOLBAR_HANDLE_DARK, "sepia": _TOOLBAR_HANDLE_SEPIA}
    return styles.get(theme_name, _TOOLBAR_HANDLE_LIGHT)


# ═══════════════════════════════════════════════════════════════════════════════
# 全局组件样式（菜单栏/工具栏/状态栏/导航树/AI面板）
# ═══════════════════════════════════════════════════════════════════════════════

_GLOBAL_LIGHT = f"""
QMenuBar {{
    background: #faf4e3; color: #191928;
    border-bottom: 1px solid #d8ccb8;
}}
QMenuBar::item:selected {{ background: #f0e8d4; }}
QMenu {{
    background: #fffbf0; color: #191928;
    border: 1px solid #d8ccb8;
}}
QMenu::item:selected {{ background: #f0e8d4; }}
QToolBar {{
    background: #faf4e3; border-bottom: 1px solid #d8ccb8;
    color: #191928; spacing: 4px;
}}
QToolBar QToolButton {{ color: #191928; }}
QToolBar QLineEdit {{
    background: #fffbf0; color: #191928;
    border: 1px solid #d8ccb8; border-radius: 4px; padding: 2px 6px;
}}
QStatusBar {{
    background: #faf4e3; color: #6b6b78;
    border-top: 1px solid #d8ccb8;
}}
"""

_GLOBAL_DARK = f"""
QMenuBar {{
    background: #dcdcdc; color: #2a2a2a;
    border-bottom: 1px solid #bebebe;
}}
QMenuBar::item:selected {{ background: #d4d4d4; }}
QMenu {{
    background: #e4e4e4; color: #2a2a2a;
    border: 1px solid #bebebe;
}}
QMenu::item:selected {{ background: #d4d4d4; }}
QToolBar {{
    background: #dcdcdc; border-bottom: 1px solid #bebebe;
    color: #2a2a2a; spacing: 4px;
}}
QToolBar QToolButton {{ color: #2a2a2a; }}
QToolBar QLineEdit {{
    background: #e4e4e4; color: #2a2a2a;
    border: 1px solid #bebebe; border-radius: 4px; padding: 2px 6px;
}}
QStatusBar {{
    background: #dcdcdc; color: #6e6e6e;
    border-top: 1px solid #bebebe;
}}
"""

_GLOBAL_SEPIA = f"""
QMenuBar {{
    background: #1a1a1a; color: #e8e8e8;
    border-bottom: 1px solid #3a3a3a;
}}
QMenuBar::item:selected {{ background: #1e1e1e; }}
QMenu {{
    background: #242424; color: #e8e8e8;
    border: 1px solid #3a3a3a;
}}
QMenu::item:selected {{ background: #1e1e1e; }}
QToolBar {{
    background: #1a1a1a; border-bottom: 1px solid #3a3a3a;
    color: #e8e8e8; spacing: 4px;
}}
QToolBar QToolButton {{ color: #e8e8e8; }}
QToolBar QLineEdit {{
    background: #242424; color: #e8e8e8;
    border: 1px solid #3a3a3a; border-radius: 4px; padding: 2px 6px;
}}
QStatusBar {{
    background: #1a1a1a; color: #999999;
    border-top: 1px solid #3a3a3a;
}}
"""


def get_global_style(theme_name: str) -> str:
    """返回当前主题的全局组件 QSS。"""
    styles = {"dark": _GLOBAL_DARK, "sepia": _GLOBAL_SEPIA}
    return styles.get(theme_name, _GLOBAL_LIGHT)

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
    """米黄主题 —— 暖米黄底 + 深灰蓝字。"""
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
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.Link, QColor(212, 144, 42))
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(170, 110, 30))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
               QColor(170, 165, 155))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
               QColor(170, 165, 155))
    return p


def _make_dark_palette() -> QPalette:
    """浅灰主题 —— 中灰底 + 深灰字。"""
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
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.Link, QColor(91, 155, 213))
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(70, 130, 180))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,
               QColor(150, 150, 150))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,
               QColor(150, 150, 150))
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

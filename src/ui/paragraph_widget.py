"""
段落叠加组件 —— 渲染在原始 PDF 页面图片上方的透明交互热区。

BlockOverlay: 透明 QWidget，覆盖在 PDF 页面图片的对应坐标上。
鼠标悬停时高亮，双击/右键触发翻译/问答/解释。
不显示任何文本——完全保留 PDF 原始渲染的视觉效果。
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QAction
from PySide6.QtWidgets import (
    QMenu,
    QWidget,
)

from src.core.models import BlockType, DocumentBlock


class BlockOverlay(QWidget):
    """透明交互热区 —— 覆盖在 PDF 页面渲染图片上方。

    不显示任何内容，仅作为鼠标交互的接收区域。
    - 鼠标进入：半透明高亮（段落=浅紫，公式=浅灰）
    - 鼠标点击：加深高亮，发射信号
    - 右键菜单：翻译/提问/解释
    - 定理/证明环境：左侧橙色竖线标记
    """

    # === 信号 ===
    clicked = Signal(str)
    double_clicked = Signal(str)
    translate_requested = Signal(str)
    question_requested = Signal(str)
    explain_requested = Signal(str)

    def __init__(self, block: DocumentBlock) -> None:
        """初始化透明叠加层。

        Args:
            block: 关联的文档块（包含 bbox 坐标）。
        """
        super().__init__()
        self._block = block
        self._is_hovered = False
        self._is_selected = False
        self._has_translation: bool = False  # 该段落是否已有翻译缓存

        # 透明背景 + 接收鼠标事件
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

        # 位置和大小由父 PageWidget.add_overlay() 设置（含 DPI 缩放）

    @property
    def block(self) -> DocumentBlock:
        """获取关联的文档块。"""
        return self._block

    @property
    def block_id(self) -> str:
        """获取块 ID。"""
        return self._block.id

    def set_highlighted(self, on: bool) -> None:
        """设置悬停高亮状态。"""
        self._is_hovered = on
        self.update()

    def set_selected(self, on: bool) -> None:
        """设置选中状态。"""
        self._is_selected = on
        self.update()

    def set_has_translation(self, has_trans: bool) -> None:
        """设置该段落是否已有翻译缓存。"""
        self._has_translation = has_trans
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击：发射信号。"""
        self.double_clicked.emit(self._block.id)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """单击：发射信号。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._block.id)

    def enterEvent(self, event) -> None:
        self._is_hovered = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._is_hovered = False
        self.update()

    def paintEvent(self, event) -> None:
        """绘制半透明覆盖层（仅在悬停/选中时可见）。"""
        if not self._is_hovered and not self._is_selected:
            return  # 完全透明，不绘制任何东西

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 悬停=淡紫半透明，选中=深紫半透明
        if self._block.block_type == BlockType.FORMULA:
            c = QColor(240, 240, 250, 200)  # 公式=浅灰
        elif self._is_selected:
            c = QColor(108, 92, 231, 75)    # 选中=深紫
        else:
            c = QColor(108, 92, 231, 38)    # 悬停=淡紫

        painter.fillRect(self.rect(), c)

        # 定理/证明环境：左侧橙色标记
        if self._block.metadata.get("is_theorem"):
            painter.fillRect(0, 0, 3, self.height(), QColor(230, 126, 34))

        painter.end()

    def _on_context_menu(self, pos) -> None:
        """右键菜单。"""
        menu = QMenu(self)

        translate_action = QAction("📖 翻译段落", menu)
        translate_action.triggered.connect(
            lambda: self.translate_requested.emit(self._block.id)
        )
        menu.addAction(translate_action)

        question_action = QAction("🔍 在此处提问", menu)
        question_action.triggered.connect(
            lambda: self.question_requested.emit(self._block.id)
        )
        menu.addAction(question_action)

        label = "✏️ 解释此公式" if self._block.block_type == BlockType.FORMULA else "✏️ 解释此概念"
        explain_action = QAction(label, menu)
        explain_action.triggered.connect(
            lambda: self.explain_requested.emit(self._block.id)
        )
        menu.addAction(explain_action)

        menu.exec(self.mapToGlobal(pos))

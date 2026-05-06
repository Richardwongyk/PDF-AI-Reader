"""
裂缝式交互容器 —— 软件的核心 UI 组件。
展开时填满页面宽度显示翻译，折叠时缩为段落左侧 6px 蓝色细条。
双击段落或翻译框切换折叠/展开。
"""

from __future__ import annotations

import json
import logging
import os

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QTimer,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.models import DocumentBlock, SplitMode, SplitState

_logger = logging.getLogger(__name__)


class _ResizeHandle(QWidget):
    """底部拖拽手柄。"""
    dragged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(6)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self._dragging = False
        self._last_y = 0

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_y = event.globalPosition().y()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            delta = int(event.globalPosition().y() - self._last_y)
            self._last_y = event.globalPosition().y()
            self.dragged.emit(delta)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._dragging = False
            event.accept()


class _HeightBridge(QObject):
    """QWebChannel 桥接对象：接收 JS 推送的内容高度变化。"""

    height_changed = Signal(int)

    @Slot(int)
    def onHeightChanged(self, h: int) -> None:
        self.height_changed.emit(h)


class WebViewPool:
    """QWebEngineView 热备池。

    维护 1 个预加载模板的 WebView 热备实例。
    acquire() 即时返回已就绪的 WebView，避免冷启动延迟。
    release() 回收 WebView 作为下一轮热备。
    将同时活跃的 Chromium 进程限制在 2 个以内。
    """

    _standby: QWebEngineView | None = None
    _in_use: int = 0

    @classmethod
    def _template_url(cls) -> QUrl:
        template_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "markdown_template.html")
        )
        return QUrl.fromLocalFile(template_path)

    @classmethod
    def prewarm(cls) -> None:
        """后台预热：创建并加载模板 HTML 的隐藏 WebView。"""
        if cls._standby is not None:
            return
        view = QWebEngineView()
        view.setMinimumHeight(60)
        view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        view.setUrl(cls._template_url())
        view.setObjectName("result_area")
        cls._standby = view

    @classmethod
    def acquire(cls) -> QWebEngineView:
        """获取一个 WebView：优先从热备取，否则新建。"""
        if cls._standby is not None:
            view = cls._standby
            cls._standby = None
        else:
            view = QWebEngineView()
            view.setMinimumHeight(60)
            view.page().setBackgroundColor(Qt.GlobalColor.transparent)
            view.setUrl(cls._template_url())
            view.setObjectName("result_area")
        cls._in_use += 1
        # 后台补充热备
        QTimer.singleShot(50, cls.prewarm)
        return view

    @classmethod
    def release(cls, view: QWebEngineView) -> None:
        """回收 WebView 到热备池，超过限额则销毁。"""
        cls._in_use = max(0, cls._in_use - 1)
        if cls._standby is None and cls._in_use < 2:
            # 重新加载模板，留作热备
            view.setUrl(cls._template_url())
            cls._standby = view
        else:
            view.setParent(None)
            view.deleteLater()


class SplitWidget(QFrame):
    """裂缝式交互容器。

    展开态：全宽，显示翻译/问答内容，底部拖拽手柄
    折叠态：段落左侧 6px 蓝色细条（高度与原文段落一致）
    双击切换展开/折叠
    """

    question_submitted = Signal(str, str)
    translation_requested = Signal(str)
    close_requested = Signal(str)

    _MAX_HISTORY_ROUNDS: int = 6
    _MIN_HEIGHT: int = 80
    _COLLAPSED_WIDTH: int = 6

    # 蓝色系
    _BLUE = "#5b8def"
    _BLUE_DARK = "#3d6fcf"
    _BLUE_LIGHT = "#e8f0fe"
    _BLUE_BG = "#f0f5ff"

    def __init__(
        self,
        block: DocumentBlock,
        mode: SplitMode = SplitMode.QUESTION,
        position: str = "below",
        block_pixel_height: int = 200,
        page_width: int = 0,
    ) -> None:
        super().__init__()
        self._block = block
        self._mode = mode
        self._position = position
        self._state = SplitState.HIDDEN
        self._chat_history: list[dict[str, str]] = []
        self._cached_result: str = ""
        self._current_answer: str = ""
        self._collapsed: bool = False
        self._saved_height: int = max(
            self._MIN_HEIGHT,
            int(block_pixel_height * 0.7),
        )
        self._block_pixel_height: int = block_pixel_height
        self._page_width: int = page_width
        self._user_resized: bool = False

        from src.ui.theme import SPLIT_WIDGET_STYLE, get_split_style
        self._current_theme = "light"

        self.setObjectName("split_container")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(SPLIT_WIDGET_STYLE)
        self._init_ui()
        self.setVisible(False)

    # ── 属性 ──

    @property
    def block_id(self) -> str: return self._block.id
    @property
    def state(self) -> SplitState: return self._state
    @property
    def mode(self) -> SplitMode: return self._mode
    @property
    def chat_history(self) -> list[dict[str, str]]: return list(self._chat_history)
    @property
    def collapsed(self) -> bool: return self._collapsed

    # ── 公开方法 ──

    def open(self, mode: SplitMode | None = None) -> None:
        if mode is not None:
            self._mode = mode
            self._update_mode_ui()
        self._state = SplitState.READY
        self._collapsed = False
        self._user_resized = False
        self._animate_expand()
        if self._mode == SplitMode.QUESTION:
            self._input_area.setFocus()

    def close(self) -> None:
        if self._state == SplitState.HIDDEN:
            return
        self._cached_result = self._current_answer
        self._state = SplitState.HIDDEN
        self.setVisible(False)

    def collapse(self) -> None:
        """折叠裂缝：截图冻结 WebView 后动画收缩。

        截图冻结可让折叠后的细条保留内容预览，
        同时为后续 WebView 回收（释放 Chromium 进程）做好准备。
        """
        if self._collapsed:
            return
        self._collapsed = True
        self._page_width = max(self._page_width, self.width())
        self._freeze_webview()
        self._animate_collapse()

    def expand(self) -> None:
        """展开裂缝：还原 WebView 并动画展开。"""
        if not self._collapsed:
            return
        self._collapsed = False
        self._thaw_webview()
        self._animate_expand()
        self._update_mode_ui()

    def _animate_expand(self) -> None:
        """动画展开：从 0 到 _saved_height。"""
        self.setVisible(True)
        self.setMaximumHeight(0)
        target = self._saved_height
        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(250)
        anim.setStartValue(0)
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self._expand_anim = anim

    def _animate_collapse(self) -> None:
        """动画折叠：从当前高度到 0，完成后隐藏。"""
        current = self.height()
        anim = QPropertyAnimation(self, b"maximumHeight")
        anim.setDuration(200)
        anim.setStartValue(current)
        anim.setEndValue(0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(lambda: self.setVisible(False))
        anim.start()
        self._collapse_anim = anim

    def display_answer_stream(self, token: str) -> None:
        self._current_answer += token
        self._update_webview(is_finished=False)

    def display_full_answer(self, answer: str) -> None:
        text = answer if answer else self._current_answer
        if text:
            self._current_answer = text
            self._update_webview(is_finished=True)
        self.set_busy(False)

    def show_followup_questions(self, questions: list[str]) -> None:
        while self._followup_layout.count():
            item = self._followup_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for q in questions[:3]:
            btn = QPushButton(q[:50] + ("..." if len(q) > 50 else ""))
            btn.setObjectName("action_button")
            btn.setToolTip(q)
            btn.clicked.connect(lambda checked, text=q: self._on_followup_click(text))
            self._followup_layout.addWidget(btn)
        self._followup_widget.setVisible(len(questions) > 0)

    def show_error(self, message: str) -> None:
        self._current_answer = f"**❌ 发生错误**\n\n```text\n{message}\n```"
        self._update_webview(is_finished=True)
        self.set_busy(False)

    def clear(self) -> None:
        self._chat_history.clear()
        self._cached_result = ""
        self._current_answer = ""
        self._update_webview(is_finished=True)
        if self._mode == SplitMode.QUESTION:
            self._input_area.clear()

    # ── UI 构建 ──

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 正文区域 ──
        self._body_widget = QWidget()
        body_layout = QVBoxLayout(self._body_widget)
        body_layout.setContentsMargins(2, 0, 2, 0)
        body_layout.setSpacing(0)
        self._body_layout = body_layout  # 保存引用，供 WebView 动态替换

        # 标题栏
        header_widget = QWidget()
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 0)
        self._header_label = QLabel()
        self._header_label.setObjectName("header_title")
        header_layout.addWidget(self._header_label)
        header_layout.addStretch()
        collapse_btn = QPushButton("∧")
        collapse_btn.setObjectName("close_button")
        collapse_btn.setToolTip("折叠 (Esc)")
        collapse_btn.clicked.connect(self.collapse)
        header_layout.addWidget(collapse_btn)
        body_layout.addWidget(header_widget)

        # 上下文
        self._context_label = QLabel()
        self._context_label.setObjectName("context_label")
        preview = self._block.content[:30].replace("\n", " ")
        self._context_label.setText(f'原文: "{preview}..."')
        body_layout.addWidget(self._context_label)

        # 输入区域
        self._input_widget = QWidget()
        input_layout = QVBoxLayout(self._input_widget)
        input_layout.setContentsMargins(0, 0, 0, 0)
        input_layout.setSpacing(4)
        self._input_area = QTextEdit()
        self._input_area.setObjectName("input_area")
        self._input_area.setPlaceholderText("在此输入问题...(Ctrl+Enter 发送)")
        self._input_area.setMaximumHeight(200)
        input_layout.addWidget(self._input_area)
        send_layout = QHBoxLayout()
        send_layout.addStretch()
        send_btn = QPushButton("发送")
        send_btn.setObjectName("send_button")
        send_btn.clicked.connect(self._on_send)
        send_layout.addWidget(send_btn)
        input_layout.addLayout(send_layout)
        body_layout.addWidget(self._input_widget)

        # QWebEngineView — 从热备池获取，避免冷启动延迟
        self._result_view = WebViewPool.acquire()
        self._page_ready = False
        self._pending_js: str | None = None
        self._pending_theme: str | None = None

        # QWebChannel — JS 内容高度变化实时推送 → Python
        self._height_bridge = _HeightBridge(self)
        self._height_bridge.height_changed.connect(self._adjust_height)
        self._web_channel = QWebChannel(self)
        self._web_channel.registerObject("bridge", self._height_bridge)
        self._result_view.page().setWebChannel(self._web_channel)

        self._result_view.loadFinished.connect(self._on_page_loaded)
        template_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "markdown_template.html")
        )
        self._result_view.setUrl(QUrl.fromLocalFile(template_path))
        body_layout.addWidget(self._result_view, 1)

        # 冻结截图标签（折叠时替换 WebView 以预览内容）
        self._frozen_label = QLabel()
        self._frozen_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._frozen_label.setWordWrap(False)
        self._frozen_label.setVisible(False)
        self._frozen_label.setStyleSheet("background: transparent; border: none;")
        body_layout.addWidget(self._frozen_label, 1)

        # 追问
        self._followup_widget = QWidget()
        self._followup_layout = QHBoxLayout(self._followup_widget)
        self._followup_layout.setContentsMargins(0, 0, 0, 0)
        self._followup_layout.setSpacing(4)
        self._followup_widget.setVisible(False)
        body_layout.addWidget(self._followup_widget)

        # 动画引用（防 GC 回收）
        self._expand_anim: QPropertyAnimation | None = None
        self._collapse_anim: QPropertyAnimation | None = None
        self._action_widget = QWidget()
        action_layout = QHBoxLayout(self._action_widget)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(6)
        action_layout.addStretch()
        copy_btn = QPushButton("复制")
        copy_btn.setObjectName("action_button")
        copy_btn.clicked.connect(self._on_copy)
        action_layout.addWidget(copy_btn)
        self._regen_btn = QPushButton("⟳ 重新翻译")
        self._regen_btn.setObjectName("action_button")
        self._regen_btn.clicked.connect(self._on_regenerate)
        action_layout.addWidget(self._regen_btn)
        clear_btn = QPushButton("✕ 清除并关闭")
        clear_btn.setObjectName("action_button")
        clear_btn.clicked.connect(self._on_clear_close)
        action_layout.addWidget(clear_btn)
        body_layout.addWidget(self._action_widget)

        main_layout.addWidget(self._body_widget, 1)

        # 拖拽手柄
        self._resize_handle = _ResizeHandle(self)
        self._resize_handle.dragged.connect(self._on_resize_drag)
        main_layout.addWidget(self._resize_handle)

        self._update_mode_ui()

    # ── 模式样式 ──

    def _apply_translation_style(self) -> None:
        """应用翻译/解释模式的样式。"""
        bg = "#f0f5ff" if self._current_theme == "light" else "#1a1a2e"
        self.setStyleSheet(f"""
            QFrame#split_container {{
                background: {bg};
                border: none;
                margin: 0px;
                padding: 0px;
            }}
            QPushButton#action_button {{
                background: {self._BLUE};
                color: #fff;
                border: none;
                border-radius: 6px;
                padding: 6px 14px;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton#action_button:hover {{
                background: {self._BLUE_DARK};
            }}
        """)

    def _update_mode_ui(self) -> None:
        if self._mode == SplitMode.TRANSLATION:
            self._header_label.setVisible(False)
            self._context_label.setVisible(False)
            self._input_widget.setVisible(False)
            self._followup_widget.setVisible(False)
            self._action_widget.setVisible(True)
            self._regen_btn.setText("⟳ 重新翻译")
            self._apply_translation_style()
        elif self._mode == SplitMode.EXPLANATION:
            self._apply_translation_style()
            self._header_label.setVisible(True)
            self._header_label.setText("✏️ 解释")
            self._context_label.setVisible(True)
            self._input_widget.setVisible(True)
            self._action_widget.setVisible(True)
            self._followup_widget.setVisible(False)
            self._input_area.setPlaceholderText("请解释此概念的含义...")
        else:
            from src.ui.theme import SPLIT_WIDGET_STYLE
            self.setStyleSheet(SPLIT_WIDGET_STYLE)
            self._header_label.setVisible(True)
            self._header_label.setText("🔍 提问")
            self._context_label.setVisible(True)
            self._input_widget.setVisible(True)
            self._action_widget.setVisible(True)
            self._followup_widget.setVisible(True)
            self._input_area.setPlaceholderText("在此输入问题...(Ctrl+Enter 发送)")

    def set_busy(self, busy: bool) -> None:
        self._state = SplitState.BUSY if busy else SplitState.READY
        self._input_area.setEnabled(not busy)

    def apply_theme(self, theme: str) -> None:
        """将主题应用到 SplitWidget QSS 和 WebView HTML 内容。"""
        from src.ui.theme import get_split_style
        self._current_theme = theme
        self.setStyleSheet(get_split_style(theme))
        if self._page_ready and self._result_view is not None:
            self._result_view.page().runJavaScript(f"setTheme('{theme}');")
        else:
            self._pending_theme = theme

    # ── WebView 截图冻结 ──

    def _freeze_webview(self) -> None:
        """截图 WebView 内容，回收 WebView 到热备池，显示截图占位。"""
        if self._result_view is None or not self._page_ready:
            return
        # 保存当前内容以便展开时恢复
        self._cached_result = self._current_answer
        # 截图当前 WebView 内容
        pixmap = self._result_view.grab()
        if not pixmap.isNull():
            self._frozen_label.setPixmap(pixmap)
        # 断开信号，从布局移除，回收
        try:
            self._result_view.loadFinished.disconnect(self._on_page_loaded)
        except Exception:
            pass
        self._body_layout.removeWidget(self._result_view)
        WebViewPool.release(self._result_view)
        self._result_view = None
        self._page_ready = False
        self._frozen_label.setVisible(True)

    def _thaw_webview(self) -> None:
        """从热备池获取 WebView，设置 QWebChannel 并恢复显示。"""
        if self._result_view is not None:
            return
        view = WebViewPool.acquire()
        self._result_view = view
        # 插入布局（在 frozen_label 之前）
        idx = self._body_layout.indexOf(self._frozen_label)
        if idx >= 0:
            self._body_layout.insertWidget(idx, view, 1)

        # 重新注册 QWebChannel（池中 WebView 之前可能连接过其他 bridge）
        self._web_channel = QWebChannel(self)
        self._web_channel.registerObject("bridge", self._height_bridge)
        view.page().setWebChannel(self._web_channel)

        view.loadFinished.connect(self._on_page_loaded)
        # 页面加载完成后恢复缓存内容
        if self._cached_result:
            safe_text = json.dumps(self._cached_result)
            self._pending_js = f"updateContent({safe_text}, true);"
            self._current_answer = self._cached_result

        template_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "markdown_template.html")
        )
        view.setUrl(QUrl.fromLocalFile(template_path))
        view.setVisible(True)
        self._frozen_label.setVisible(False)

    # ── 拖拽 ──

    def _on_resize_drag(self, delta: int) -> None:
        if self._collapsed:
            return
        new_h = max(self._MIN_HEIGHT, self.height() + delta)
        self._saved_height = new_h
        self._user_resized = True  # 用户主动拖拽，锁定自动高度
        self.setFixedHeight(new_h)

    # ── WebView ──

    def _update_webview(self, is_finished: bool = False) -> None:
        safe_text = json.dumps(self._current_answer)
        js_bool = "true" if is_finished else "false"
        # 更新内容；高度变化由 ResizeObserver → QWebChannel 实时推送
        js_code = f"updateContent({safe_text}, {js_bool});"
        if self._page_ready:
            self._result_view.page().runJavaScript(js_code)
        else:
            self._pending_js = js_code

    def _adjust_height(self, content_height: int) -> None:
        """QWebChannel 推送的内容高度变化回调。"""
        if self._user_resized or self._collapsed:
            return
        if content_height and content_height > 0:
            chrome_h = self._action_widget.height() + 20
            needed = content_height + chrome_h
            if needed > self.height():
                new_h = min(needed, 600)
                self._saved_height = new_h
                self.setFixedHeight(new_h)

    def _on_page_loaded(self, ok: bool) -> None:
        if ok:
            self._page_ready = True
            self._result_view.page().runJavaScript("window.pageReady = true;")
            if self._pending_theme:
                self._result_view.page().runJavaScript(f"setTheme('{self._pending_theme}');")
                self._pending_theme = None
            if self._pending_js:
                self._result_view.page().runJavaScript(self._pending_js)
                self._pending_js = None
        else:
            _logger.warning("WebView 页面加载失败: block=%s", self._block.id)

    # ── 事件 ──

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Return and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._on_send()
        elif event.key() == Qt.Key.Key_Escape:
            self.collapse()
        else:
            super().keyPressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击翻译框本身 → 折叠/展开。"""
        if self._collapsed:
            self.expand()
        else:
            self.collapse()
        event.accept()

    # ── 槽 ──

    def _on_send(self) -> None:
        question = self._input_area.toPlainText().strip()
        if not question:
            return
        self.set_busy(True)
        self._chat_history.append({"role": "user", "content": question})
        if len(self._chat_history) > self._MAX_HISTORY_ROUNDS * 2:
            self._chat_history = self._chat_history[-(self._MAX_HISTORY_ROUNDS * 2):]
        self.question_submitted.emit(question, self._block.id)
        self._input_area.clear()

    def _on_followup_click(self, question: str) -> None:
        self._input_area.setText(question)
        self._on_send()

    def _on_copy(self) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self._current_answer)

    def _on_clear_close(self) -> None:
        """清除翻译并关闭裂缝，释放资源。"""
        self._cached_result = ""
        self._current_answer = ""
        self._chat_history.clear()
        # 仅在 WebView 存活时更新，冻结状态跳过（PdfViewer 即将销毁此 widget）
        if self._page_ready and self._result_view is not None:
            self._update_webview(is_finished=True)
        self.close_requested.emit(self._block.id)

    def _on_regenerate(self) -> None:
        if self._collapsed:
            self.expand()
        self._current_answer = ""
        self._update_webview(is_finished=True)
        self.set_busy(True)
        if self._mode == SplitMode.TRANSLATION:
            self.translation_requested.emit(self._block.id)
        elif self._chat_history:
            if self._chat_history[-1]["role"] == "assistant":
                self._chat_history.pop()
            self.question_submitted.emit(self._chat_history[-1]["content"], self._block.id)

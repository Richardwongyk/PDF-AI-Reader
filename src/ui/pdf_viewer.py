"""
PDF 阅读器 —— 整页渲染 + 点击段落时页面在该段落下边界裂开。
支持同页多段裂开（裂开后的上下半页各自仍可再裂开）。

性能优化：虚拟视口懒加载 —— 仅渲染可见页面的 pixmap，
非可见页面释放 pixmap 内存。对 100+ 页论文可节省 ~90% 内存。
"""

from __future__ import annotations

import logging

from shiboken6 import isValid as _isValid
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget,
)

from src.core.models import DocumentBlock, ParseResult, SplitMode, UIConfig
from src.core.pdf_engine import DocumentEngine
from src.ui.paragraph_widget import BlockOverlay
from src.ui.split_widget import SplitWidget

_logger = logging.getLogger(__name__)


class _LazyPageWidget(QWidget):
    """支持延迟加载的页面容器。

    未渲染时显示浅灰占位，渲染后显示 pixmap + BlockOverlay。
    调用 unrender() 可释放 pixmap 内存，保留占位。
    """

    def __init__(self, page_num: int, width_px: int, height_px: int) -> None:
        super().__init__()
        self.page_num = page_num
        self.setFixedSize(width_px, height_px)

        self._label = QLabel(self)
        self._label.setFixedSize(width_px, height_px)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._show_placeholder()

        self._rendered = False
        self._overlays: dict[str, BlockOverlay] = {}

    def _show_placeholder(self) -> None:
        self._label.clear()
        self._label.setStyleSheet(
            "background: #e8e8e8; color: #aaa; font-size: 18px;"
        )
        self._label.setText(f"…")

    def render(
        self,
        pixmap: QPixmap,
        blocks: list[DocumentBlock],
        scale: float,
        connect_cb: callable,
    ) -> None:
        """渲染页面：设置 pixmap 并创建 BlockOverlay。"""
        self._label.setPixmap(pixmap)
        self._label.setStyleSheet("background: transparent;")
        self._label.setText("")
        self._rendered = True

        for b in blocks:
            bx0, by0, bx1, by1 = b.bbox
            sx0 = int(bx0 * scale)
            sy0 = int(by0 * scale)
            sw = max(int((bx1 - bx0) * scale), 1)
            sh = max(int((by1 - by0) * scale), 1)
            if sy0 + sh <= 0 or sy0 >= self.height():
                continue
            ov = BlockOverlay(b)
            ov.setParent(self)
            ov.setGeometry(sx0, max(sy0, 0), sw,
                           max(min(sy0 + sh, self.height()) - max(sy0, 0), 1))
            ov.raise_()
            ov.show()
            connect_cb(ov)
            self._overlays[b.id] = ov

    def unrender(self) -> list[str]:
        """释放 pixmap 和 overlay，返回被清除的 block_id 列表。"""
        self._label.clear()
        self._show_placeholder()
        cleared = list(self._overlays.keys())
        for ov in self._overlays.values():
            ov.deleteLater()
        self._overlays.clear()
        self._rendered = False
        return cleared

    @property
    def rendered(self) -> bool:
        return self._rendered

    def overlay(self, block_id: str) -> BlockOverlay | None:
        return self._overlays.get(block_id)


class PdfViewer(QScrollArea):
    block_double_clicked = Signal(str)
    block_translate_requested = Signal(str)
    block_question_requested = Signal(str)
    block_explain_requested = Signal(str)
    viewport_changed = Signal(int, int)
    split_close_requested = Signal(str)

    def __init__(self, doc_engine: DocumentEngine, config: UIConfig) -> None:
        super().__init__()
        self._doc_engine = doc_engine
        self._config = config
        self._dpi, self._scale = 150, 150 / 72.0
        self._all_blocks: list[DocumentBlock] = []
        self._page_segments: dict[int, list[dict]] = {}
        self._splits: dict[str, SplitWidget] = {}
        self._block_to_page: dict[str, int] = {}
        self._overlays: dict[str, BlockOverlay] = {}
        self._trans_indicators: dict[str, QWidget] = {}

        # 懒加载相关
        self._page_containers: dict[int, _LazyPageWidget] = {}
        self._rendered_pages: set[int] = set()
        self._split_pages: set[int] = set()
        self._viewport_timer = QTimer(self)
        self._viewport_timer.setSingleShot(True)
        self._viewport_timer.setInterval(80)
        self._viewport_timer.timeout.connect(self._update_visible_pages)

        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(10, 6, 10, 6)
        self._layout.setSpacing(0)
        self.setWidget(self._content)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setBackgroundRole(QPalette.ColorRole.Base)

        # 异步渲染：连接 DocumentEngine 的完成信号
        self._doc_engine.page_rendered.connect(self._on_page_rendered_async)

        app = QApplication.instance()
        if app:
            app.setStyleSheet(app.styleSheet() + " QToolTip { color: #4a3f6b; }")

    # ── 页面段构建（始终渲染，用于裂开后的上下半页） ──

    def _build_segment_widget(
        self, pixmap: QPixmap, y0: int, y1: int, blocks: list[DocumentBlock]
    ) -> QWidget:
        """创建裁切图片 + 透明叠加层的段组件（始终渲染）。"""
        h = max(y1 - y0, 1)
        if not blocks:
            w = QWidget(); w.setFixedSize(pixmap.width(), h); return w
        cropped = pixmap.copy(0, y0, pixmap.width(), h)
        w = QWidget()
        w.setFixedSize(cropped.size())
        label = QLabel(w)
        label.setPixmap(cropped)
        label.move(0, 0)
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        for b in blocks:
            bx0, by0, bx1, by1 = b.bbox
            sx0 = int(bx0 * self._scale)
            sy0 = int(by0 * self._scale) - y0
            sw = max(int((bx1 - bx0) * self._scale), 1)
            sh = max(int((by1 - by0) * self._scale), 1)
            if sy0 + sh <= 0 or sy0 >= h:
                continue
            ov = BlockOverlay(b)
            ov.setParent(w)
            ov.setGeometry(sx0, max(sy0, 0), sw,
                           max(min(sy0 + sh, h) - max(sy0, 0), 1))
            ov.raise_()
            ov.show()
            self._connect_overlay(ov)
        return w

    # ── 文档加载 ──

    def load_document(self, result: ParseResult) -> None:
        self.clear()
        self._all_blocks = result.blocks
        if not self._all_blocks:
            return

        # 按页分组
        pages: dict[int, list[DocumentBlock]] = {}
        for b in self._all_blocks:
            pages.setdefault(b.page_num, []).append(b)
            self._block_to_page[b.id] = b.page_num

        doc = self._doc_engine.document
        for page_num in sorted(pages.keys()):
            # 用 page.rect 计算占位尺寸，无需渲染 pixmap
            if doc and page_num < doc.page_count:
                rect = doc[page_num].rect
                w_px = int(rect.width * self._scale)
                h_px = int(rect.height * self._scale)
            else:
                w_px, h_px = 600, 800  # 回退

            container = _LazyPageWidget(page_num, w_px, h_px)
            self._layout.addWidget(container)
            self._page_containers[page_num] = container
            self._page_segments[page_num] = [{
                "y0": 0, "y1": h_px,
                "blocks": pages[page_num], "widget": container,
            }]

        self._layout.addStretch()

        # 连接滚动信号（仅首次加载时，用标志位防重复连接）
        if not hasattr(self, '_scroll_connected'):
            self.verticalScrollBar().valueChanged.connect(self._on_scroll)
            self._scroll_connected = True

        # 首屏渲染
        QTimer.singleShot(50, self._update_visible_pages)

    # ── 懒加载核心 ──

    def _on_scroll(self, value: int) -> None:
        """滚动事件 → 防抖后更新可见页面。"""
        self._viewport_timer.start()
        self.viewport_changed.emit(value, self.verticalScrollBar().maximum())

    def _compute_page_y_offsets(self) -> dict[int, int]:
        """计算每个页面在当前 layout 中的 Y 偏移。"""
        offsets: dict[int, int] = {}
        y = self._layout.contentsMargins().top()
        for i in range(self._layout.count()):
            item = self._layout.itemAt(i)
            w = item.widget()
            if w is None:
                continue
            # 尝试找到该 widget 所属的 page_num
            found = False
            for page_num, segs in self._page_segments.items():
                for seg in segs:
                    if seg.get("widget") is w:
                        if page_num not in offsets:
                            offsets[page_num] = y
                        found = True
                        break
                if found:
                    break
            if not found and isinstance(w, SplitWidget):
                pass  # SplitWidget 不占页面位
            y += w.height()
        return offsets

    def _update_visible_pages(self) -> None:
        """根据滚动位置决定哪些页面需要渲染/释放。"""
        if not self._page_containers:
            return

        scroll_y = self.verticalScrollBar().value()
        viewport_h = self.viewport().height()
        if viewport_h <= 0:
            return

        # 清理已不在 _page_containers 中的页面（已被裂开合并为普通 widget）
        for page_num in list(self._rendered_pages):
            if page_num not in self._page_containers:
                self._rendered_pages.discard(page_num)

        offsets = self._compute_page_y_offsets()

        needed: set[int] = set()
        for page_num in self._page_containers:
            y = offsets.get(page_num, 0)
            page_h = self._get_page_height(page_num)
            page_bottom = y + page_h
            margin = viewport_h
            if page_bottom >= scroll_y - margin and y <= scroll_y + viewport_h + margin:
                needed.add(page_num)

        # 有活跃裂缝的页面始终保留渲染
        needed |= self._split_pages

        # 释放离开视口的页面（仅 _page_containers 中的）
        for page_num in (self._rendered_pages - needed):
            if page_num in self._page_containers:
                self._unrender_page(page_num)

        # 渲染新进入视口的页面
        for page_num in (needed - self._rendered_pages):
            if page_num in self._page_containers:
                self._render_page(page_num)

        self._rendered_pages = needed

    def _get_page_height(self, page_num: int) -> int:
        """获取某页在当前布局中的总高度。"""
        segs = self._page_segments.get(page_num, [])
        h = 0
        for seg in segs:
            w = seg.get("widget")
            if w:
                h += w.height()
        # 加上该页的 SplitWidget 高度
        for block_id, split in self._splits.items():
            if self._block_to_page.get(block_id) == page_num:
                if split.isVisible():
                    h += split.height()
        return h

    def _render_page(self, page_num: int) -> None:
        """渲染指定页：异步提交到 QThreadPool，不阻塞主线程。

        页面渲染完成后通过 page_rendered 信号回调 _on_page_rendered_async。
        """
        container = self._page_containers.get(page_num)
        if container is None or container.rendered:
            return

        segs = self._page_segments.get(page_num, [])
        if not segs:
            return

        # 只有单段（未裂开）的页面才用懒加载渲染
        if len(segs) == 1 and segs[0].get("widget") is container:
            self._doc_engine.request_page_render_async(page_num, dpi=self._dpi)

    def _on_page_rendered_async(self, page_num: int, qpixmap: object) -> None:
        """异步渲染完成回调：将 QPixmap 设置到容器并恢复翻译指示器。"""
        container = self._page_containers.get(page_num)
        if container is None or container.rendered:
            return
        # 防止文档切换后，旧异步任务回调访问已销毁的 C++ 对象
        if not _isValid(container):
            return

        segs = self._page_segments.get(page_num, [])
        if len(segs) != 1 or segs[0].get("widget") is not container:
            return

        blocks = segs[0]["blocks"]
        pixmap = qpixmap if isinstance(qpixmap, QPixmap) else None
        if pixmap is None or pixmap.isNull():
            return
        container.render(pixmap, blocks, self._scale, self._connect_overlay)
        for b in blocks:
            if b.id in self._trans_indicators:
                self._set_translation_marker(b.id, True)

    def _unrender_page(self, page_num: int) -> None:
        """释放页面 pixmap 和 overlay，保留占位。"""
        container = self._page_containers.get(page_num)
        if container is None or not container.rendered:
            return

        # 先清理翻译指示器
        for block_id, indicator in list(self._trans_indicators.items()):
            if self._block_to_page.get(block_id) == page_num:
                indicator.deleteLater()
                del self._trans_indicators[block_id]

        cleared = container.unrender()
        for block_id in cleared:
            self._overlays.pop(block_id, None)

    # ── 翻译指示器 ──

    def _set_translation_marker(self, block_id: str, has: bool) -> None:
        """在页面最左边界处创建/移除蓝色翻译指示器条。"""
        ov = self._get_overlay(block_id)
        if not ov:
            return

        # 清理旧指示器（父控件可能已被删除，导致 C++ 对象已释放）
        old = self._trans_indicators.pop(block_id, None)
        if old and _isValid(old):
            old.deleteLater()

        if has:
            parent_w = ov.parentWidget()
            if parent_w:
                y = ov.geometry().y()
                h = max(ov.geometry().height(), 20)
                indicator = QWidget(parent_w)
                indicator.setGeometry(0, y, 3, h)
                indicator.setStyleSheet("background: #a0c4f0;")
                indicator.setToolTip("已翻译 — 双击原文展开")
                indicator.show()
                self._trans_indicators[block_id] = indicator

    def _get_overlay(self, block_id: str) -> BlockOverlay | None:
        """获取 overlay（可能在 _LazyPageWidget 或普通 segment widget 中）。

        检测 C++ 对象是否仍存活，若已销毁则自动清理引用。
        """
        ov = self._overlays.get(block_id)
        if ov:
            if _isValid(ov):
                return ov
            self._overlays.pop(block_id, None)
        for container in self._page_containers.values():
            ov = container.overlay(block_id)
            if ov and _isValid(ov):
                return ov
        return None

    # ── 裂缝操作 ──

    def open_split_widget(
        self, block_id: str, mode: SplitMode = SplitMode.TRANSLATION
    ) -> SplitWidget | None:
        """在段落下边界裂开页面，插入裂缝。若已存在则展开。"""
        existing = self._splits.get(block_id)
        if existing is not None:
            if existing.collapsed:
                existing.expand()
            return existing

        page_num = self._block_to_page.get(block_id)
        if page_num is None:
            return None

        # 确保页面已渲染
        if page_num not in self._rendered_pages:
            self._render_page(page_num)
            self._rendered_pages.add(page_num)

        segs = self._page_segments.get(page_num, [])
        seg_idx = -1
        for i, seg in enumerate(segs):
            if "split_id" in seg:
                continue
            for b in seg["blocks"]:
                if b.id == block_id:
                    seg_idx = i
                    break
            if seg_idx >= 0:
                break
        if seg_idx < 0:
            return None

        seg = segs[seg_idx]
        old_widget = seg["widget"]
        all_blocks = seg["blocks"]
        pixmap = self._doc_engine.get_page_pixmap(page_num, dpi=self._dpi)
        if pixmap is None:
            return None

        block = self._find_block(block_id)
        if block is None:
            return None
        cut_y = int(block.bbox[3] * self._scale) + 2
        cut_y = max(cut_y, seg["y0"] + 10)
        cut_y = min(cut_y, seg["y1"] - 10)

        above_blocks = [b for b in all_blocks if b.bbox[3] * self._scale <= cut_y + 5]
        below_blocks = [b for b in all_blocks if b.bbox[1] * self._scale >= cut_y - 5]

        block_px_h = int((block.bbox[3] - block.bbox[1]) * self._scale)
        split = SplitWidget(
            block, mode=mode, position="below",
            block_pixel_height=max(block_px_h, 60),
            page_width=pixmap.width(),
        )
        split.setFixedWidth(pixmap.width())
        split.question_submitted.connect(lambda q, bid=block_id: self._on_split_q(q, bid))
        split.translation_requested.connect(self.block_translate_requested.emit)
        split.close_requested.connect(lambda bid=block_id: self._on_clear_close(bid))

        old_idx = self._layout.indexOf(old_widget)

        # 如果 old_widget 是 _LazyPageWidget，清理其 overlay 引用并从容器列表移除
        if isinstance(old_widget, _LazyPageWidget):
            for b_id in list(old_widget._overlays.keys()):
                self._overlays.pop(b_id, None)
            self._page_containers.pop(page_num, None)
            # 清理该页面上所有翻译指示器（父控件即将被删除，子控件也会被自动删除）
            for b_id in list(self._trans_indicators.keys()):
                if self._block_to_page.get(b_id) == page_num:
                    del self._trans_indicators[b_id]
        else:
            # 非 _LazyPageWidget（已裂开过的段）：递归清理 BlockOverlay 引用
            for child in old_widget.findChildren(BlockOverlay):
                self._overlays.pop(child.block_id, None)
        old_widget.hide()
        self._layout.removeWidget(old_widget)

        top_w = self._build_segment_widget(pixmap, seg["y0"], cut_y, above_blocks)
        self._layout.insertWidget(old_idx, top_w)

        self._layout.insertWidget(old_idx + 1, split)

        bot_w = self._build_segment_widget(pixmap, cut_y, seg["y1"], below_blocks)
        self._layout.insertWidget(old_idx + 2, bot_w)

        split.open(mode)
        self._splits[block_id] = split
        _logger.info("裂缝已打开: block=%s page=%d mode=%s", block_id, page_num, mode.value)
        self._split_pages.add(page_num)
        self._set_translation_marker(block_id, True)
        old_widget.deleteLater()

        new_segs = segs[:seg_idx] + [
            {"y0": seg["y0"], "y1": cut_y, "blocks": above_blocks, "widget": top_w},
            {"split_id": block_id},
            {"y0": cut_y, "y1": seg["y1"], "blocks": below_blocks, "widget": bot_w},
        ] + segs[seg_idx + 1:]
        self._page_segments[page_num] = new_segs

        QApplication.processEvents()
        self.ensureVisible(0, split.y(), 50, 80)
        return split

    def find_split_widget(self, block_id: str) -> SplitWidget | None:
        return self._splits.get(block_id)

    def scroll_to_page(self, page_num: int) -> None:
        """滚动到指定页面。"""
        offsets = self._compute_page_y_offsets()
        y = offsets.get(page_num)
        if y is not None:
            self.verticalScrollBar().setValue(y)

    # ── 主题 ──

    def apply_theme_to_splits(self, theme: str) -> None:
        """将主题广播到所有已打开的裂缝。"""
        for split in self._splits.values():
            split.apply_theme(theme)

    # ── 清理 ──

    def clear(self) -> None:
        _logger.info("PdfViewer.clear: 开始 (%d splits, %d pages)",
                     len(self._splits), len(self._page_containers))
        for block_id, s in list(self._splits.items()):
            s.close()
            s.deleteLater()
        self._splits.clear()
        self._page_segments.clear()
        self._block_to_page.clear()
        self._overlays.clear()
        for ind in list(self._trans_indicators.values()):
            if _isValid(ind):
                ind.deleteLater()
        self._trans_indicators.clear()
        self._all_blocks.clear()
        self._page_containers.clear()
        self._rendered_pages.clear()
        self._split_pages.clear()
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        _logger.info("PdfViewer.clear: 完成")

    # ── Overlay 连接 ──

    def _connect_overlay(self, ov: BlockOverlay) -> None:
        self._overlays[ov.block_id] = ov
        ov.double_clicked.connect(self.block_double_clicked.emit)
        ov.translate_requested.connect(self.block_translate_requested.emit)
        ov.question_requested.connect(self.block_question_requested.emit)
        ov.explain_requested.connect(self.block_explain_requested.emit)

    def _find_block(self, block_id: str) -> DocumentBlock | None:
        for b in self._all_blocks:
            if b.id == block_id:
                return b
        return None

    # ── 内部槽 ──

    def _on_split_q(self, question: str, block_id: str) -> None:
        pass

    def _on_clear_close(self, block_id: str) -> None:
        """清除翻译并彻底销毁裂缝，合并页面段。"""
        self._set_translation_marker(block_id, False)
        s = self._splits.pop(block_id, None)
        if s is None:
            return
        page_num = self._block_to_page.get(block_id)
        if page_num is not None:
            self._merge_segments(page_num, block_id)
            # 检查该页是否还有活跃裂缝
            has_other_splits = any(
                self._block_to_page.get(bid) == page_num
                for bid in self._splits
            )
            if not has_other_splits:
                self._split_pages.discard(page_num)
        s.close()
        s.deleteLater()
        self.split_close_requested.emit(block_id)

    def _merge_segments(self, page_num: int, split_id: str) -> None:
        """裂缝关闭时，合并被它分开的相邻段。"""
        segs = self._page_segments.get(page_num, [])
        for i, seg in enumerate(segs):
            if seg.get("split_id") == split_id:
                if i > 0 and i + 1 < len(segs):
                    prev_seg = segs[i - 1]
                    next_seg = segs[i + 1]
                    pixmap = self._doc_engine.get_page_pixmap(page_num, dpi=self._dpi)
                    if pixmap:
                        merged_blocks = prev_seg["blocks"] + next_seg["blocks"]
                        merged_w = self._build_segment_widget(
                            pixmap, prev_seg["y0"], next_seg["y1"], merged_blocks
                        )
                        pw = prev_seg["widget"]
                        nw = next_seg["widget"]
                        # 清理旧 Widget 中所有 BlockOverlay 的 Python 引用，
                        # 防止后续代码访问已销毁的 C++ 对象导致 RuntimeError
                        for child in pw.findChildren(BlockOverlay) + nw.findChildren(BlockOverlay):
                            self._overlays.pop(child.block_id, None)
                        idx_p = self._layout.indexOf(pw)
                        pw.hide(); self._layout.removeWidget(pw); pw.deleteLater()
                        nw.hide(); self._layout.removeWidget(nw); nw.deleteLater()
                        self._layout.insertWidget(idx_p, merged_w)
                        new_segs = segs[:i - 1] + [{
                            "y0": prev_seg["y0"], "y1": next_seg["y1"],
                            "blocks": merged_blocks, "widget": merged_w,
                        }] + segs[i + 2:]
                        self._page_segments[page_num] = new_segs

                        # 合并后页面使用普通 widget（始终渲染），移除懒加载容器
                        self._page_containers.pop(page_num, None)
                        self._rendered_pages.discard(page_num)
                break

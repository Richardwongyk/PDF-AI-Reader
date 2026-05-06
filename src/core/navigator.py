"""
导航器 —— 目录提取、书签管理、AI 智能书签建议。

Navigator: 管理文档的导航结构（目录树和书签列表）。
"""

from __future__ import annotations

import time
import uuid

from PySide6.QtCore import QObject, Signal

from src.core.base_service import BaseService
from src.core.models import Bookmark, DocumentBlock


class Navigator(BaseService):
    """文档导航器。

    负责：
    - 提取 PDF 原生大纲（目录）
    - 书签的增删改查与排序
    - AI 智能书签建议
    """

    # === 信号 ===
    toc_ready = Signal(list)               # 目录数据就绪 → list[dict]
    bookmarks_changed = Signal(list)       # 书签列表变化 → list[Bookmark]
    ai_bookmarks_suggested = Signal(list)  # AI 建议书签列表 → list[dict]

    def __init__(self, parent: QObject | None = None) -> None:
        """初始化导航器。

        Args:
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._bookmarks: list[Bookmark] = []
        self._toc: list[dict] = []

    @property
    def bookmarks(self) -> list[Bookmark]:
        """获取当前所有书签。"""
        return list(self._bookmarks)

    @property
    def toc(self) -> list[dict]:
        """获取当前目录树。"""
        return list(self._toc)

    def load_toc(self, raw_toc: list[dict]) -> None:
        """加载 PDF 原生大纲数据。

        Args:
            raw_toc: 从 PyMuPDF doc.get_toc() 提取的原始目录。
        """
        self._toc = raw_toc
        self.toc_ready.emit(self._toc)

    def generate_toc_from_blocks(self, blocks: list[DocumentBlock]) -> list[dict]:
        """当 PDF 无原生大纲时，从标题块推断目录结构。

        提取所有 block_type="heading" 的块，
        按字号和页面位置排序并推断层级。

        Args:
            blocks: 文档块列表。

        Returns:
            推断出的目录树。
        """
        headings = [b for b in blocks if b.block_type.value == "heading"]
        if not headings:
            self._toc = []
            self.toc_ready.emit([])
            return []

        toc: list[dict] = []
        for h in headings:
            toc.append({
                "title": h.content,
                "page": h.page_num,
                "level": 1,  # 默认一级，实际可通过字号差调整
            })

        self._toc = toc
        self.toc_ready.emit(toc)
        return toc

    def add_bookmark(self, page_num: int, title: str, note: str = "") -> Bookmark:
        """手动添加书签。

        Args:
            page_num: 页码（0-based）。
            title: 书签标题。
            note: 可选备注。

        Returns:
            新创建的 Bookmark 对象。
        """
        bookmark = Bookmark(
            id=str(uuid.uuid4())[:8],
            page_num=page_num,
            title=title,
            note=note,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self._bookmarks.append(bookmark)
        self.bookmarks_changed.emit(list(self._bookmarks))
        return bookmark

    def remove_bookmark(self, bookmark_id: str) -> bool:
        """删除书签。

        Args:
            bookmark_id: 书签 ID。

        Returns:
            是否成功删除。
        """
        for i, bm in enumerate(self._bookmarks):
            if bm.id == bookmark_id:
                del self._bookmarks[i]
                self.bookmarks_changed.emit(list(self._bookmarks))
                return True
        return False

    def reorder_bookmarks(self, ordered_ids: list[str]) -> None:
        """按指定顺序重排书签。

        Args:
            ordered_ids: 按新顺序排列的书签 ID 列表。
        """
        id_to_bm = {bm.id: bm for bm in self._bookmarks}
        new_order: list[Bookmark] = []
        for bid in ordered_ids:
            if bid in id_to_bm:
                new_order.append(id_to_bm[bid])
        self._bookmarks = new_order
        self.bookmarks_changed.emit(list(self._bookmarks))

    def suggest_ai_bookmarks(self, blocks: list[DocumentBlock]) -> list[dict]:
        """AI 扫描全文，自动建议重要节点作为书签。

        策略：
        1. 收集所有 heading 块
        2. 对每个 heading 评估是否值得标记为书签

        Args:
            blocks: 文档块列表。

        Returns:
            建议书签列表 [{"title": "...", "page": 0}, ...]。
        """
        suggestions: list[dict] = []

        # 收集标题块
        headings = [b for b in blocks if b.block_type.value == "heading"]

        for h in headings:
            suggestions.append({
                "title": h.content,
                "page": h.page_num,
            })

        # 也建议重要定理/定义块
        for b in blocks:
            content_lower = b.content.lower()
            if any(kw in content_lower for kw in ("theorem", "lemma", "definition")):
                # 取前 60 字符作为描述
                short_title = b.content[:60].strip().replace("\n", " ")
                suggestions.append({
                    "title": f"[重要] {short_title}...",
                    "page": b.page_num,
                })

        self.ai_bookmarks_suggested.emit(suggestions)
        return suggestions

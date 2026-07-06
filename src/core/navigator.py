"""
导航器 —— 目录提取、书签管理、AI 智能书签建议。

Navigator: 管理文档的导航结构（目录树和书签列表）。
"""

import re
import time
import unicodedata
import uuid
from typing import Any

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

    _MIN_GENERATED_TOC_ITEMS = 2
    _MAX_GENERATED_TOC_TITLE_LENGTH = 160
    _CID_MARKER_RE = re.compile(r"\(?cid\s*[:：]\s*\d+\)?", re.IGNORECASE)
    _CHINESE_NUMERAL_CLASS = "一二三四五六七八九十百零〇两"
    _STRUCTURAL_TOC_PREFIX_RE = re.compile(
        rf"(?=第[{_CHINESE_NUMERAL_CLASS}]+节\s*\S|"
        rf"[{_CHINESE_NUMERAL_CLASS}]+、\s*\S|"
        r"\d+、\s*\S)"
    )
    _STRUCTURAL_SECTION_RE = re.compile(rf"^第[{_CHINESE_NUMERAL_CLASS}]+节\s*\S")
    _STRUCTURAL_CHINESE_ITEM_RE = re.compile(rf"^[{_CHINESE_NUMERAL_CLASS}]+、\s*\S")
    _STRUCTURAL_DIGIT_ITEM_RE = re.compile(r"^\d+、\s*\S")
    _MATH_FRAGMENT_TOKENS = frozenset({
        "arccos",
        "arccot",
        "arcsin",
        "arctan",
        "cos",
        "cot",
        "csc",
        "curl",
        "det",
        "div",
        "grad",
        "lim",
        "ln",
        "log",
        "max",
        "min",
        "sec",
        "sin",
        "tan",
    })
    _MOJIBAKE_MARKERS = (
        "\ufffd",
        "Ã",
        "Â",
        "â€",
        "â€™",
        "â€œ",
        "â€�",
        "â€“",
        "â€”",
        "鈥",
        "锛",
        "鍙",
        "鐨",
        "绗",
        "鏂",
        "瀹",
        "瑙",
        "鐩",
    )

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
    def toc(self) -> list[dict[str, Any]]:
        """获取当前目录树。"""
        return list(self._toc)

    def load_toc(self, raw_toc: list[dict[str, Any]]) -> None:
        """加载 PDF 原生大纲数据。

        Args:
            raw_toc: 从 PyMuPDF doc.get_toc() 提取的原始目录。
        """
        self._toc = raw_toc
        self.toc_ready.emit(self._toc)

    def generate_toc_from_blocks(self, blocks: list[DocumentBlock]) -> list[dict[str, Any]]:
        """当 PDF 无原生大纲时，从标题块推断目录结构。

        优先从所有文本块中提取结构化标题（如“第X节”“一、”“1、”），
        再回退到 block_type="heading" 的块；不足以形成可靠目录时返回空列表。

        Args:
            blocks: 文档块列表。

        Returns:
            推断出的目录树。
        """
        structural_toc = self._generate_structural_toc_from_blocks(blocks)
        if len(structural_toc) >= self._MIN_GENERATED_TOC_ITEMS:
            self._toc = structural_toc
            self.toc_ready.emit(structural_toc)
            return structural_toc

        headings = sorted(
            (b for b in blocks if getattr(b.block_type, "value", b.block_type) == "heading"),
            key=self._block_sort_key,
        )
        if not headings:
            self._toc = []
            self.toc_ready.emit([])
            return []

        toc: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for h in headings:
            title = self._normalize_generated_toc_title(h.content)
            if not title:
                continue
            title_key = title.casefold()
            if title_key in seen_titles:
                continue
            seen_titles.add(title_key)
            toc.append({
                "title": title,
                "page": h.page_num,
                "level": 1,  # 默认一级，实际可通过字号差调整
            })

        if len(toc) < self._MIN_GENERATED_TOC_ITEMS:
            self._toc = []
            self.toc_ready.emit([])
            return []

        self._toc = toc
        self.toc_ready.emit(toc)
        return toc

    @classmethod
    def _generate_structural_toc_from_blocks(cls, blocks: list[DocumentBlock]) -> list[dict[str, Any]]:
        toc: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for block in sorted(blocks, key=cls._block_sort_key):
            for title in cls._split_structural_titles(block.content):
                normalized = cls._normalize_generated_toc_title(title)
                if not normalized:
                    continue
                title_key = normalized.casefold()
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)
                toc.append({
                    "title": normalized,
                    "page": block.page_num,
                    "level": cls._structural_title_level(normalized),
                })
        return toc

    @staticmethod
    def _block_sort_key(block: DocumentBlock) -> tuple[int, float, float]:
        bbox = block.bbox or ()
        y = bbox[1] if len(bbox) > 1 else 0.0
        x = bbox[0] if len(bbox) > 0 else 0.0
        return block.page_num, y, x

    @classmethod
    def _split_structural_titles(cls, text: str) -> list[str]:
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return []
        matches = list(cls._STRUCTURAL_TOC_PREFIX_RE.finditer(cleaned))
        if not matches or matches[0].start() != 0:
            return []
        titles: list[str] = []
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(cleaned)
            title = cleaned[start:end].strip(" \t\r\n;；,，.。")
            if cls._is_structural_title(title):
                titles.append(title)
        return titles

    @classmethod
    def _is_structural_title(cls, title: str) -> bool:
        return (
            bool(cls._STRUCTURAL_SECTION_RE.match(title))
            or bool(cls._STRUCTURAL_CHINESE_ITEM_RE.match(title))
            or bool(cls._STRUCTURAL_DIGIT_ITEM_RE.match(title))
        )

    @classmethod
    def _structural_title_level(cls, title: str) -> int:
        if cls._STRUCTURAL_SECTION_RE.match(title):
            return 1
        if cls._STRUCTURAL_CHINESE_ITEM_RE.match(title):
            return 2
        if cls._STRUCTURAL_DIGIT_ITEM_RE.match(title):
            return 3
        return 1

    @classmethod
    def _normalize_generated_toc_title(cls, text: str) -> str:
        title = " ".join(str(text or "").replace("\u00ad", "").split())
        title = title.strip(" \t\r\n-:\u2013\u2014\uff1a|\u2022\u00b7")
        if not title or len(title) > cls._MAX_GENERATED_TOC_TITLE_LENGTH:
            return ""
        if cls._CID_MARKER_RE.search(title):
            return ""
        if cls._looks_like_corrupt_title(title):
            return ""
        if cls._looks_like_formula_fragment_title(title):
            return ""
        if not cls._has_enough_readable_content(title):
            return ""
        return title

    @classmethod
    def _looks_like_corrupt_title(cls, title: str) -> bool:
        if any(marker in title for marker in cls._MOJIBAKE_MARKERS):
            return True
        for ch in title:
            category = unicodedata.category(ch)
            if category in {"Cc", "Cs", "Co", "Cn"}:
                return True
        return False

    @classmethod
    def _looks_like_formula_fragment_title(cls, title: str) -> bool:
        if any("\u4e00" <= ch <= "\u9fff" for ch in title):
            return False
        tokens = re.findall(r"[A-Za-z]+|\d+", title)
        if not tokens:
            return False
        math_like = 0
        for token in tokens:
            lowered = token.lower()
            if token.isdigit() or len(lowered) == 1 or lowered in cls._MATH_FRAGMENT_TOKENS:
                math_like += 1
        return math_like / len(tokens) >= 0.75

    @staticmethod
    def _has_enough_readable_content(title: str) -> bool:
        chars = [ch for ch in title if not ch.isspace()]
        if not chars:
            return False
        readable = [ch for ch in chars if ch.isalnum()]
        if len(readable) < 2:
            return False
        return len(readable) / len(chars) >= 0.35

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

    def suggest_ai_bookmarks(self, blocks: list[DocumentBlock]) -> list[dict[str, Any]]:
        """AI 扫描全文，自动建议重要节点作为书签。

        策略：
        1. 收集所有 heading 块
        2. 对每个 heading 评估是否值得标记为书签

        Args:
            blocks: 文档块列表。

        Returns:
            建议书签列表 [{"title": "...", "page": 0}, ...]。
        """
        suggestions: list[dict[str, Any]] = []

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

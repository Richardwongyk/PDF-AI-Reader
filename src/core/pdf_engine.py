"""
PDF 文档引擎 —— PDF 打开、段落分割、文本预处理。

DocumentEngine: 协调解析流水线的入口。
DocumentChunker: 智能段落分割与公式识别。
TextPreprocessor: 翻译前文本清洗与公式保护。
"""

from __future__ import annotations

import re
from typing import Any

import fitz  # PyMuPDF
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QPixmap

from src.core.base_service import BaseService
from src.core.models import (
    AppConfig,
    BlockType,
    DocumentBlock,
    ParseResult,
)


# =============================================================================
# TextPreprocessor —— 文本预处理器
# =============================================================================

class TextPreprocessor:
    """翻译文本预处理器。

    核心职责：
    - 提取 LaTeX 公式（$$...$$ 和 $...$）并替换为占位符
    - 合并 PDF 中常见的 mid-sentence 断行
    - 翻译完成后反向替换恢复公式
    """

    # 匹配行间公式 $$...$$
    _DISPLAY_FORMULA_RE: re.Pattern[str] = re.compile(
        r"\$\$(.+?)\$\$", re.DOTALL
    )
    # 匹配行内公式 $...$（不匹配 $$）
    _INLINE_FORMULA_RE: re.Pattern[str] = re.compile(
        r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)"
    )

    def __init__(self) -> None:
        """初始化预处理器。"""
        self._formula_store: dict[str, str] = {}  # 占位符 → 原始 LaTeX

    def protect_formulas(self, text: str) -> str:
        """将文本中的 LaTeX 公式替换为占位符。

        处理顺序：先匹配行间公式 $$...$$，再匹配行内公式 $...$。
        原始公式字符串存入 self._formula_store。

        Args:
            text: 原始文本。

        Returns:
            公式被替换为【FORMULA_0】、【FORMULA_1】... 的文本。
        """
        self._formula_store.clear()
        counter = 0

        def _replace_display(match: re.Match[str]) -> str:
            nonlocal counter
            placeholder = f"【FORMULA_{counter}】"
            self._formula_store[placeholder] = f"$${match.group(1)}$$"
            counter += 1
            return placeholder

        def _replace_inline(match: re.Match[str]) -> str:
            nonlocal counter
            placeholder = f"【FORMULA_{counter}】"
            self._formula_store[placeholder] = f"${match.group(1)}$"
            counter += 1
            return placeholder

        # 先保护行间公式（$$...$$）
        protected = self._DISPLAY_FORMULA_RE.sub(_replace_display, text)
        # 再保护行内公式（$...$）
        protected = self._INLINE_FORMULA_RE.sub(_replace_inline, protected)

        return protected

    def restore_formulas(self, translated_text: str) -> str:
        """将占位符反向替换回原始 LaTeX 公式。

        Args:
            translated_text: 包含占位符的译文。

        Returns:
            恢复公式后的最终译文。
        """
        result = translated_text
        for placeholder, formula in self._formula_store.items():
            result = result.replace(placeholder, formula)
        return result

    @staticmethod
    def clean_text(text: str) -> str:
        """基础文本清洗：合并断行、去除多余空白。

        - 将行尾连字符 "- "（后跟换行）合并为 ""
        - 将单个换行符替换为空格（段落内换行）
        - 保留连续两个换行符（段落分隔）

        Args:
            text: 原始文本。

        Returns:
            清洗后的文本。
        """
        # 修复断词连字符
        cleaned = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
        # 单个换行 → 空格
        cleaned = re.sub(r"(?<!\n)\n(?!\n)", " ", cleaned)
        # 多个空白 → 单个空格
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        return cleaned.strip()


# =============================================================================
# DocumentChunker —— 段落与公式分割
# =============================================================================

class DocumentChunker:
    """PDF 内容智能分块器。

    使用 PyMuPDF 提供的文本坐标、字体、字号信息，
    将页面中的文本行按排版特征聚合为逻辑块。
    同时识别独立公式并标记。
    """

    # 数学 Unicode 字符范围（用于公式检测）
    _MATH_UNICODE_RANGES: list[tuple[int, int]] = [
        (0x2200, 0x22FF),  # 数学运算符
        (0x27C0, 0x27EF),  # 杂项数学符号-A
        (0x2980, 0x29FF),  # 杂项数学符号-B
        (0x2A00, 0x2AFF),  # 补充数学运算符
        (0x1D400, 0x1D7FF),  # 数学字母数字符号
    ]

    # 常见 LaTeX 命令模式
    _LATEX_COMMAND_PATTERN: re.Pattern[str] = re.compile(
        r"\\(?:frac|sum|int|prod|lim|partial|sqrt|alpha|beta|gamma|delta|"
        r"epsilon|theta|lambda|mu|pi|sigma|phi|omega|infty|nabla|"
        r"mathbf|mathcal|mathbb|mathfrak|text|"
        r"begin|end|left|right|langle|rangle|"
        r"times|cdot|pm|leq|geq|neq|approx|equiv|sim|"
        r"rightarrow|leftarrow|Rightarrow|Leftarrow|longrightarrow)"
    )

    # 常见数学字体名称关键词
    _MATH_FONT_KEYWORDS: list[str] = [
        "CM", "Math", "Symbol", "Euler", "Cambria Math",
        "STIX", "XITS", "TeX", "Latin Modern Math",
    ]

    def __init__(self, median_font_size: float = 10.0, line_spacing_factor: float = 1.5) -> None:
        """初始化分块器。

        Args:
            median_font_size: 默认正文中位数字号（pt），用于标题检测。
            line_spacing_factor: 行间距阈值因子（相对于行高中位数）。
        """
        self._median_font_size = median_font_size
        self._line_spacing_factor = line_spacing_factor

    def chunk(self, doc: fitz.Document) -> list[DocumentBlock]:
        """分块：PyMuPDF 文本块 = 段落单位 + 标题检测 + 双栏处理。

        策略：信任 PyMuPDF 的文本块划分（PDF 内部结构），不做行合并/拆分。
        对排版规范的论文，PyMuPDF 的块边界已经足够精确。
        仅在此基础上做：标题检测（字号+加粗）、双栏交错排列。
        公式检测由后续 Pix2Text MFD 完成。
        """
        all_blocks: list[DocumentBlock] = []
        for page_num in range(doc.page_count):
            page_blocks = self._extract_page_blocks(doc[page_num], page_num)
            if not page_blocks:
                continue
            # 双栏检测与交错排列
            columns = self._detect_columns(page_blocks)
            if len(columns) > 1:
                page_blocks = self._interleave_columns(columns)
            # 重新编号
            for i, b in enumerate(page_blocks):
                b.id = f"p{page_num}_b{len(all_blocks) + i}"
            all_blocks.extend(page_blocks)
        return all_blocks

    def _extract_page_blocks(
        self, page: fitz.Page, page_num: int
    ) -> list[DocumentBlock]:
        """从单页提取 DocumentBlock 列表。

        PyMuPDF 的每个 text block 作为一个 DocumentBlock。
        用 _is_formula_from_spans 和 _is_heading 做类型判定。
        """
        text_dict: dict[str, Any] = page.get_text("dict")
        blocks: list[DocumentBlock] = []

        # 计算中位数字号（用于标题检测）
        all_sizes: list[float] = []
        for block in text_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    s = span.get("size", 0)
                    if s > 0:
                        all_sizes.append(s)
        median_size = sorted(all_sizes)[len(all_sizes) // 2] if all_sizes else 10.0

        for block in text_dict.get("blocks", []):
            b = block["bbox"]
            bbox = (b[0], b[1], b[2], b[3])

            # 图片块
            if block.get("type") != 0:
                blocks.append(DocumentBlock(
                    id=f"p{page_num}_b{len(blocks)}",
                    page_num=page_num, block_type=BlockType.IMAGE,
                    content="", bbox=bbox,
                ))
                continue

            # 收集 spans
            spans: list[dict] = []
            for line in block.get("lines", []):
                spans.extend(line.get("spans", []))
            if not spans:
                continue

            text = " ".join(s.get("text", "") for s in spans)
            if not text.strip():
                continue

            # 类型判定：公式 > 标题 > 段落
            if self._is_formula_from_spans(spans):
                block_type = BlockType.FORMULA
            elif self._is_heading(spans[0], median_size):
                block_type = BlockType.HEADING
            else:
                block_type = BlockType.PARAGRAPH

            blocks.append(DocumentBlock(
                id=f"p{page_num}_b{len(blocks)}",
                page_num=page_num,
                block_type=block_type,
                content=text.strip(),
                bbox=bbox,
            ))

        return blocks

    @staticmethod
    def _detect_columns(blocks: list[DocumentBlock]) -> list[list[DocumentBlock]]:
        """检测页面是否为双栏布局。返回按栏分组的块列表。"""
        if len(blocks) < 6:
            return [blocks]
        x0s = [b.bbox[0] for b in blocks]
        median_x = sorted(x0s)[len(x0s) // 2]
        left = [b for b in blocks if b.bbox[0] < median_x]
        right = [b for b in blocks if b.bbox[0] >= median_x]
        # 只有当两栏都有足够块，且 x 间距 > 50pt 时才分栏
        if len(left) >= 3 and len(right) >= 3:
            left_mid = sorted([b.bbox[2] for b in left])[len(left) // 2]
            right_mid = sorted([b.bbox[0] for b in right])[len(right) // 2]
            if right_mid - left_mid > 50:
                return [left, right]
        return [blocks]

    @staticmethod
    def _interleave_columns(cols: list[list[DocumentBlock]]) -> list[DocumentBlock]:
        """将多栏块按 y 交错排列（模拟阅读顺序）。"""
        result: list[DocumentBlock] = []
        indices = [0] * len(cols)
        while True:
            # 找到下一个 y 最小的块
            min_y = float('inf')
            min_col = -1
            for ci, col in enumerate(cols):
                if indices[ci] < len(col):
                    y = col[indices[ci]].bbox[1]
                    if y < min_y:
                        min_y = y
                        min_col = ci
            if min_col < 0:
                break
            result.append(cols[min_col][indices[min_col]])
            indices[min_col] += 1
        return result

    @staticmethod
    def _is_formula_from_spans(spans: list) -> bool:
        """基于 span 级别检测数学公式。"""
        full_text = "".join(s.get("text", "") for s in spans)
        fonts = [s.get("font", "") for s in spans]
        # 排除：太短、email、大量英文单词（说明是正文）
        if len(full_text) < 8:
            return False
        if "@" in full_text and len(full_text) < 60:
            return False
        # 英文单词>20个 → 不是公式
        english_words = sum(1 for w in full_text.split() if w.isalpha() and len(w) > 2)
        if english_words > 20:
            return False
        # 数学字体
        for f in fonts:
            for kw in ("CM", "Math", "Symbol", "Cambria", "STIX", "XITS", "TeX"):
                if kw.lower() in f.lower() and len(full_text) > 10:
                    return True
        # LaTeX 命令（至少匹配2个才认为是公式，减少误判）
        latex_count = sum(1 for pat in (
            r"\frac", r"\sum", r"\int", r"\prod", r"\sqrt",
            r"\alpha", r"\beta", r"\gamma", r"\delta", r"\theta",
            r"\lambda", r"\mu", r"\pi", r"\sigma", r"\omega",
            r"\mathbf", r"\mathcal", r"\mathbb",
            r"\begin", r"\end", r"\left", r"\right",
            r"\infty", r"\partial", r"\nabla",
        ) if pat in full_text)
        if latex_count >= 2:
            return True
        # 数学 Unicode 占比很高（排除 ∗ ◦ • 等常见标点）
        MATH_RANGES = [(0x2202, 0x2233), (0x2260, 0x22FF), (0x27C0, 0x27EF),
                       (0x2980, 0x29FF), (0x2A00, 0x2AFF)]
        math_count = sum(1 for c in full_text if any(lo <= ord(c) <= hi for lo, hi in MATH_RANGES))
        total = len(full_text)
        if total > 0 and math_count / total > 0.15 and total < 300:
            return True
        return False

    @staticmethod
    def _is_heading(line: dict[str, Any], median_font_size: float) -> bool:
        """判断一个文本行是否为标题样式。

        判断依据：字号 > 中位数字号 2pt 或字体加粗。

        Args:
            line: 文本行信息字典。
            median_font_size: 页面中位数字号。

        Returns:
            True 表示该行为标题。
        """
        size = line.get("size", median_font_size)
        flags = line.get("flags", 0)
        is_bold = bool(flags & 2**3)  # PDF 字体 flags bit 3 = bold
        return size > median_font_size + 1.5 or is_bold

    def rechunk_blocks(
        self, blocks: list[DocumentBlock], merge_indices: list[int]
    ) -> list[DocumentBlock]:
        """响应用户手动调整：合并指定的相邻块。

        Args:
            blocks: 原始块列表。
            merge_indices: 需要合并的起始索引列表（每个与下一个合并）。

        Returns:
            合并后的新块列表。
        """
        skip: set[int] = set()
        result: list[DocumentBlock] = []

        for i in range(len(blocks)):
            if i in skip:
                continue
            if i in merge_indices and i + 1 < len(blocks):
                # 合并块 i 和 i+1
                merged = DocumentBlock(
                    id=blocks[i].id,
                    page_num=blocks[i].page_num,
                    block_type=blocks[i].block_type,
                    content=blocks[i].content + " " + blocks[i + 1].content,
                    bbox=(
                        min(blocks[i].bbox[0], blocks[i + 1].bbox[0]),
                        min(blocks[i].bbox[1], blocks[i + 1].bbox[1]),
                        max(blocks[i].bbox[2], blocks[i + 1].bbox[2]),
                        max(blocks[i].bbox[3], blocks[i + 1].bbox[3]),
                    ),
                    section_title=blocks[i].section_title,
                )
                result.append(merged)
                skip.add(i + 1)
            else:
                result.append(blocks[i])

        return result

    def split_block(
        self, block: DocumentBlock, split_position: int
    ) -> tuple[DocumentBlock, DocumentBlock]:
        """响应用户手动调整：在指定字符位置拆分块。

        Args:
            block: 要拆分的块。
            split_position: 拆分位置（字符索引）。

        Returns:
            (前一半块, 后一半块)
        """
        content_before = block.content[:split_position].strip()
        content_after = block.content[split_position:].strip()
        return (
            DocumentBlock(
                id=block.id + "_a",
                page_num=block.page_num,
                block_type=block.block_type,
                content=content_before,
                bbox=block.bbox,
                section_title=block.section_title,
                metadata=block.metadata,
            ),
            DocumentBlock(
                id=block.id + "_b",
                page_num=block.page_num,
                block_type=block.block_type,
                content=content_after,
                bbox=block.bbox,
                section_title=block.section_title,
                metadata=block.metadata,
            ),
        )


# =============================================================================
# DocumentEngine —— 文档引擎
# =============================================================================

class DocumentEngine(BaseService):
    """PDF 文档处理引擎。

    负责：
    - 打开/关闭 PDF 文件
    - 提取元数据（标题、作者、页数、原生目录）
    - 协调段落分割流水线
    - 提供页面渲染的 Pixmap（主线程安全）
    """

    # === 信号 ===
    parse_finished = Signal(ParseResult)
    parse_progress = Signal(int, int)  # (当前页, 总页数)
    parse_error = Signal(str)
    formula_blocks_updated = Signal(list)  # list[dict] — MFD 精扫修正的块

    def __init__(self, config: AppConfig, parent: QObject | None = None) -> None:
        """初始化文档引擎。

        Args:
            config: 应用配置对象。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._config = config
        self._doc: fitz.Document | None = None
        self._chunker = DocumentChunker()
        self._preprocessor = TextPreprocessor()
        self._thread: QThread | None = None
        # 渲染缓存: {page_num: {dpi: QPixmap}}
        self._pixmap_cache: dict[int, dict[int, QPixmap]] = {}

    @property
    def is_open(self) -> bool:
        """当前是否有打开的文档。"""
        return self._doc is not None

    @property
    def page_count(self) -> int:
        """当前文档的总页数。未打开时返回 0。"""
        return self._doc.page_count if self._doc else 0

    @property
    def chunker(self) -> DocumentChunker:
        """获取文档分块器实例。"""
        return self._chunker

    @property
    def preprocessor(self) -> TextPreprocessor:
        """获取文本预处理器实例。"""
        return self._preprocessor

    @property
    def document(self) -> fitz.Document | None:
        """获取底层 PyMuPDF 文档对象。"""
        return self._doc

    def open_document(self, filepath: str) -> None:
        """打开 PDF 文件并在工作线程中执行解析流水线。

        Args:
            filepath: PDF 文件的绝对路径。
        """
        # 清理上一个线程
        if self._thread is not None:
            if self._thread.isRunning():
                self._thread.requestInterruption()
                self._thread.quit()
                if not self._thread.wait(3000):
                    self.logger.warning("上一解析线程未能在 3s 内退出，强制终止")
                    self._thread.terminate()
            self._thread.deleteLater()
            self._thread = None

        # 直接继承 QThread，信号直连最可靠
        self._thread = _ParseThread(filepath, self._chunker)
        self._thread.progress.connect(self.parse_progress.emit)
        self._thread.finished_parsing.connect(self._on_parse_finished)
        self._thread.parse_error.connect(self.parse_error.emit)
        self._thread.formula_blocks_updated.connect(self.formula_blocks_updated.emit)
        self._thread.start()

    def _on_parse_finished(self, result: ParseResult) -> None:
        """解析完成的内部处理。

        Args:
            result: ParseResult 对象。
        """
        # 重新打开文档（供渲染使用）
        try:
            self._doc = fitz.open(result.filepath)
        except Exception:
            self._doc = None
        self._pixmap_cache.clear()
        self.parse_finished.emit(result)

    def close_document(self) -> None:
        """关闭当前文档，释放资源。"""
        if self._doc:
            self._doc.close()
            self._doc = None
        self._pixmap_cache.clear()

    def get_page_pixmap(
        self, page_num: int, dpi: int = 150
    ) -> QPixmap | None:
        """获取指定页面的渲染图像（主线程安全）。

        Args:
            page_num: 页码（0-based）。
            dpi: 渲染分辨率。

        Returns:
            QPixmap 对象，若页面不存在则返回 None。
        """
        if not self._doc or page_num < 0 or page_num >= self._doc.page_count:
            return None

        # 检查缓存
        if page_num in self._pixmap_cache and dpi in self._pixmap_cache[page_num]:
            return self._pixmap_cache[page_num][dpi]

        try:
            page = self._doc[page_num]
            # 使用 dpi 计算缩放矩阵
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            # 转换为 QPixmap
            qpixmap = QPixmap()
            qpixmap.loadFromData(pix.tobytes("ppm"), "PPM")

            # 存入缓存
            if page_num not in self._pixmap_cache:
                self._pixmap_cache[page_num] = {}
            self._pixmap_cache[page_num][dpi] = qpixmap

            # LRU 清理：超过 20 个页面缓存时删除最旧的
            if len(self._pixmap_cache) > 20:
                oldest = min(self._pixmap_cache.keys())
                del self._pixmap_cache[oldest]

            return qpixmap
        except Exception:
            return None

    def preload_pages(self, page_nums: list[int], dpi: int = 150) -> None:
        """预加载指定页面到渲染缓存。

        Args:
            page_nums: 需要预加载的页码列表。
            dpi: 渲染分辨率。
        """
        for pn in page_nums:
            self.get_page_pixmap(pn, dpi)


# =============================================================================
# _ParseThread —— 解析工作线程
# =============================================================================

class _ParseThread(QThread):
    """PDF 解析线程 —— 两阶段异步加载。

    阶段一（极速呈现）：仅启发式分块，立刻发射 finished_parsing。
    阶段二（后台精扫）：Pix2Text MFD 深度学习模型补充公式检测，
    完成后发射 formula_blocks_updated。
    """

    progress = Signal(int, int)
    finished_parsing = Signal(ParseResult)
    parse_error = Signal(str)
    formula_blocks_updated = Signal(list)  # list[dict] — 被 MFD 修正的块信息

    def __init__(self, filepath: str, chunker: DocumentChunker) -> None:
        super().__init__()
        self._filepath = filepath
        self._chunker = chunker

    def run(self) -> None:
        """两阶段 PDF 解析。"""
        try:
            doc = fitz.open(self._filepath)
            if doc.needs_pass:
                self.parse_error.emit("PDF 文件已加密，暂不支持密码保护的文件。")
                doc.close()
                return

            metadata = doc.metadata or {}
            title = metadata.get("title", "")
            author = metadata.get("author", "")
            page_count = doc.page_count

            toc: list[dict] = []
            try:
                raw_toc = doc.get_toc()
                for item in raw_toc:
                    toc.append({"level": item[0], "title": item[1], "page": item[2] - 1})
            except Exception:
                pass

            # ── 阶段一：启发式分块（极速，<0.5s） ──
            self.progress.emit(0, page_count)
            blocks = self._chunker.chunk(doc)
            self.progress.emit(page_count, page_count)

            result = ParseResult(
                filepath=self._filepath,
                title=title,
                author=author,
                page_count=page_count,
                toc=toc,
                blocks=blocks,
            )
            self.finished_parsing.emit(result)

            # ── 阶段二：ML 公式精扫（后台，不阻塞阅读） ──
            if self.isInterruptionRequested():
                doc.close()
                return

            from src.core.formula_detector import Pix2TextMFDDetector
            import logging
            logger = logging.getLogger("ParseThread")
            try:
                refined = Pix2TextMFDDetector(dpi=200).apply_to_blocks(blocks, doc)
                # 收集被 ML 修正的块
                updated: list[dict] = []
                for b in refined:
                    if (b.block_type.value == "formula"
                            and b.metadata.get("formula_detector") == "pix2text-mfd"):
                        updated.append({
                            "id": b.id,
                            "block_type": b.block_type.value,
                            "metadata": b.metadata,
                        })
                if updated and not self.isInterruptionRequested():
                    self.formula_blocks_updated.emit(updated)
                logger.info(
                    "MFD 精扫完成: %d 个公式块被修正", len(updated),
                )
            except Exception as e:
                logger.warning("MFD 精扫失败（不影响阅读）: %s", e)

            doc.close()

        except FileNotFoundError:
            self.parse_error.emit(f"文件不存在: {self._filepath}")
        except Exception as e:
            import traceback
            self.parse_error.emit(f"PDF 解析失败: {e}\n{traceback.format_exc()}")

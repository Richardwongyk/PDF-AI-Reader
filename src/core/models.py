"""
数据模型 —— PDF AI Reader 全部数据结构的 Pydantic 模型定义。

每个模型都是 Pydantic BaseModel，提供自动验证、序列化/反序列化、
JSON Schema 导出等能力。所有类型注解使用 Python 3.14 原生语法。
"""

from enum import Enum
from typing import Any
from typing import Literal

from pydantic import BaseModel, Field


# =============================================================================
# 枚举类型
# =============================================================================

class BlockType(str, Enum):
    """文档块类型枚举。"""
    PARAGRAPH = "paragraph"   # 普通段落
    FORMULA = "formula"       # 数学公式（LaTeX）
    HEADING = "heading"       # 章节标题
    IMAGE = "image"           # 图片（预留）
    TABLE = "table"           # 表格（预留）


class SplitMode(str, Enum):
    """裂缝工作模式枚举。"""
    QUESTION = "question"          # 提问模式：显示输入框 + 结果区
    TRANSLATION = "translation"    # 翻译模式：仅显示结果区（译文）
    EXPLANATION = "explanation"    # 解释模式：自动发送解释请求


class SplitState(str, Enum):
    """裂缝状态机枚举。"""
    HIDDEN = "hidden"       # 完全隐藏
    OPENING = "opening"     # 正在播放展开动画
    READY = "ready"         # 已打开，等待输入或展示结果
    BUSY = "busy"           # 正在等待 AI 响应
    CLOSING = "closing"     # 正在播放关闭动画


class TaskType(str, Enum):
    """AI 任务类型枚举。"""
    TRANSLATION = "translation"
    QA = "qa"
    EMBEDDING = "embedding"
    SUMMARIZATION = "summarization"
    FOLLOWUP_QUESTIONS = "followup_questions"


class RoutingStrategy(str, Enum):
    """路由策略枚举。"""
    LOCAL_FIRST = "local_first"
    CLOUD_ONLY = "cloud_only"
    LOCAL_ONLY = "local_only"


# =============================================================================
# 核心数据模型
# =============================================================================

class DocumentBlock(BaseModel):
    """文档知识块 —— 知识库的最小单元。

    每个块代表一个段落、一个公式或一个标题，
    是 PDF 分割、向量嵌入、语义检索的基本单位。
    """
    id: str
    # 唯一标识，格式: "p{page_num}_b{block_index}"
    # 示例: "p5_b12" 表示第 5 页第 12 个块

    page_num: int
    # 所在页码（0-based）

    block_type: BlockType
    # 块类型：段落 / 公式 / 标题 / 图片 / 表格

    content: str
    # 块的文本内容。
    # - 类型为 paragraph 时：英文原文段落
    # - 类型为 formula 时：LaTeX 源码（如 "E = mc^2"）
    # - 类型为 heading 时：章节标题文本

    bbox: tuple[float, float, float, float]
    # 包围框坐标: (x0, y0, x1, y1)，相对于页面左上角，单位 pt
    # 用于点击定位和高亮绘制

    section_title: str = ""
    # 所属章节标题（由最近的 heading 块确定）

    metadata: dict[str, Any] = Field(default_factory=dict)
    # 扩展元数据，可包含:
    # - "summary": AI 生成的摘要（≤50字）
    # - "is_theorem": bool, 是否为定理/证明环境
    # - "domain": str, 推测的学科领域
    # - "keywords": list[str], 关键术语


def is_math_wrapped(text: str) -> bool:
    """Return True when text already has a LaTeX math delimiter."""
    stripped = str(text or "").strip()
    return (
        stripped.startswith("$$")
        or stripped.startswith(r"\[")
        or stripped.startswith(r"\(")
        or (stripped.startswith("$") and not stripped.startswith("$$"))
    )


def wrap_math_text(text: str, display: bool = True) -> str:
    """Wrap math text with display or inline LaTeX delimiters if needed."""
    stripped = str(text or "").strip()
    if not stripped or is_math_wrapped(stripped):
        return stripped
    if display:
        return f"$$\n{stripped}\n$$"
    return rf"\({stripped}\)"


def document_block_index_text(block: DocumentBlock) -> str:
    """Text form used by translation/RAG indexes for a document block."""
    if block.block_type == BlockType.FORMULA:
        return wrap_math_text(block.content, display=True)
    return str(block.content or "")


class GlossaryEntry(BaseModel):
    """术语表条目。"""
    en: str                              # 英文术语
    zh: str                              # 中文翻译
    domain: str                          # 学科领域（如 "math", "cs_ml", "physics"）
    force: bool = False                  # 是否强制使用此翻译
    aliases: list[str] = Field(default_factory=list)  # 英文别名
    notes: str = ""                      # 可选备注


class Bookmark(BaseModel):
    """书签。"""
    id: str                              # 唯一标识
    page_num: int                        # 页码（0-based）
    title: str                           # 书签标题
    note: str = ""                       # 可选备注
    is_ai_suggested: bool = False        # 是否由 AI 自动建议
    created_at: str = ""                 # ISO 时间戳


# =============================================================================
# 配置模型
# =============================================================================

class ModelConfig(BaseModel):
    """模型配置。"""
    local: str = "qwen3.5:4b"            # 可选本地生成模型（Ollama）
    cloud: str = "deepseek/deepseek-chat" # 云端默认模型
    cloud_translation: str = "deepseek/deepseek-chat" # 翻译与轻量任务模型
    cloud_reasoning: str = "deepseek/deepseek-v4-pro" # 全文问答、结构抽取与图谱模型
    embed_local: str = "bge-m3"          # 可选本地嵌入模型；不可用时使用轻量哈希嵌入
    formula_ocr_backend: str = "pix2text-mfr"  # pix2text-mfr / paddle_formula / unimernet
    formula_ocr_model: str = "PP-FormulaNet_plus-S"  # Paddle: PP-FormulaNet_plus-S/M/L or UniMERNet
    ollama_host: str = "http://localhost:11434"  # Ollama 服务地址


class RAGConfig(BaseModel):
    """全文理解与 RAG 配置。"""
    backend: str = "legacy_chroma"       # legacy_chroma / llamaindex_chroma / sqlite_fts / qdrant
    graph_backend: str = "disabled"      # disabled / llamaindex_neo4j
    candidate_pool: int = 48             # 向量检索后进入重排的最大候选数
    final_evidence: int = 8              # 全文问答默认证据数
    enable_hybrid_rerank: bool = True    # 向量 + 关键词混合重排
    enable_graph_index: bool = False     # 是否构建概念/公式/章节知识图谱


class RoutingConfig(BaseModel):
    """路由策略配置。"""
    translation: str = "cloud_only"      # cloud_only / local_first / local_only
    qa: str = "cloud_only"
    summarization: str = "cloud_only"
    embed: str = "local_only"            # Ollama 不可用时自动退到确定性哈希嵌入
    auto_upgrade_threshold: int = 2000   # 字符数超过此值自动升级到云端


class UIConfig(BaseModel):
    """UI 配置。"""
    language: str = "zh_CN"              # 界面语言
    theme: str = "light"                 # light / dark / sepia
    split_position: str = "below"        # below / right（裂缝默认位置）
    font_size: int = 12                  # 阅读区字体大小
    line_spacing: float = 1.5            # 行距
    show_word_translation: bool = False  # 是否开启取词翻译


class AppConfig(BaseModel):
    """应用配置总模型。"""
    model: ModelConfig = Field(default_factory=ModelConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    api_keys: dict[str, str] = Field(default_factory=dict)


# =============================================================================
# 运行时数据结构
# =============================================================================

class ParseResult(BaseModel):
    """PDF 解析结果（用于线程间传递）。"""
    filepath: str
    title: str = ""
    author: str = ""
    page_count: int = 0
    toc: list[dict[str, Any]] = Field(default_factory=list)      # 原生大纲/目录
    blocks: list[DocumentBlock] = Field(default_factory=list)
    parsed_pages: list[int] = Field(default_factory=list)  # 本次结果已完成块解析的页码


class KnowledgeStatus(BaseModel):
    """知识库状态。"""
    doc_hash: str
    collection_name: str
    is_ready: bool = False
    total_blocks: int = 0
    embedded_blocks: int = 0
    build_time_seconds: float = 0.0

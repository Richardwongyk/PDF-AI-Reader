# PDF AI 阅读器 · 技术设计文档 (TDD)

**版本:** V4.0（全面重写——匹配实际代码）
**日期:** 2026-05-03
**状态:** 详细设计——与代码库同步
**依赖文档:** 产品需求文档 (PRD) V4.0
**开发语言:** Python 3.14.4
**GUI 框架:** PySide6 ≥ 6.11.0
**本地默认模型:** Qwen3.5:4b
**嵌入模型:** BGE-M3 (通过 Ollama)
**环境管理:** Conda (环境名: `pdf_ai_reader_314`)

---

## 文档使用说明

本文档是 PDF AI Reader 的**权威技术规范**，与 `src/` 下的实际代码同步。文档覆盖：
- 完整的模块结构与类定义（与代码一致）
- 所有公开方法的签名与职责
- 信号与槽的连接规范
- 线程模型与并发安全约束
- 数据模型定义
- 错误处理与降级策略

---

## 1. 设计概述

### 1.1 系统目标

构建一款基于 PySide6 的桌面端 PDF 阅读与 AI 深度辅助软件。系统遵循 **"本地优先、隐私至上、模块化、可扩展"** 原则。

核心闭环：
> **PDF 解析与块分割 → 向量嵌入与知识库构建 → 混合模型路由 → 裂缝式交互呈现**

### 1.2 核心设计原则

1. **严格三層分离**：UI 层只通过 Qt 信号/槽与 Core 层通信。Core 层封装全部算法与 AI 调用。Data 层提供持久化接口。
2. **异步与并发**：所有耗时操作（PDF 解析、AI 推理、嵌入生成）在 QThread 工作线程中执行。UI 线程不阻塞超过 16ms。
3. **故障隔离与降级**：模块异常不导致崩溃。本地模型不可用时回退到 Mock 客户端。
4. **配置驱动**：所有可变参数外置到 `config.yaml`，由 `ConfigManager` 热加载。
5. **可测试性**：Core 层通过接口注入依赖，可脱离 UI 测试。

### 1.3 完整技术栈

| 组件 | 库/工具 | 使用版本 | 选型理由 |
|------|---------|----------|---------|
| 运行环境 | Python | 3.14.4 | 2026 LTS 分支；T-Strings (PEP 750)、延迟注解 (PEP 649/749)、无 GIL 构建 (PEP 779) |
| 包管理 | Conda | 最新 | 独立环境 `pdf_ai_reader_314`，隔离依赖 |
| 桌面框架 | PySide6 | ≥6.11.0 | LGPL；Qt 6.11 绑定；信号发射优化 (PYSIDE-3279)；QtCanvasPainter 新模块 |
| PDF 渲染与解析 | PyMuPDF (fitz) | ≥1.27.2 | C 语言级性能；`page.get_text("dict")` 提供结构化坐标文本 |
| 本地模型管理 | Ollama | ≥0.6.1 | REST API (`localhost:11434`)；GGUF 量化模型；CPU-only 推理 |
| 本地 LLM | Qwen3.5:4b | — | 4B 参数；16GB 内存流畅；中英双语；量化后 ~2.7GB |
| 嵌入模型 | BGE-M3 | — | 1024 维；多语言语义向量；通过 Ollama 统一管理 |
| 向量存储 | ChromaDB | ≥0.5.0 | 纯 Python 嵌入式；零配置持久化（SQLite + 自定义格式） |
| 云端模型网关 | LiteLLM | ≥1.77.3 | 统一 API 调用 100+ 模型；自动重试；支持 streaming |
| 数据校验 | Pydantic | ≥2.12.0 | 类型安全；自动验证；高性能序列化 |
| 配置解析 | PyYAML | ≥6.0.2 | 读写 `config.yaml`；嵌套结构支持 |
| 公式检测 | Pix2Text | 可选 | ONNX Runtime MFD 模型（纯检测，无需 PyTorch） |
| Markdown 渲染 | marked.js + KaTeX | 本地文件 | 在 QWebEngineView 中渲染富文本和 LaTeX 公式 |
| 测试 | pytest | ≥8.0.0 | 单元测试框架 |

### 1.4 Python 3.14 适配要点

| 特性 | PEP | 项目中的应用 |
|------|-----|-------------|
| 延迟注解求值 | PEP 649/749 | 默认行为；无需 `from __future__ import annotations`（代码仍保留以兼容 3.13） |
| T-Strings | PEP 750 | 可用于 Prompt 模板安全构建（`t"..."`），避免注入 |
| 无 GIL 构建 | PEP 779 | 后台嵌入/推理时可真正利用多核（需编译时启用 `--disable-gil`） |
| 多解释器 | PEP 734 | `concurrent.interpreters` 可用于隔离模型调用 |
| Zstd 压缩 | PEP 784 | `compression.zstd` 可用于知识库数据压缩 |
| 远程调试 | PEP 768 | `python -m pdb -p <PID>` 可附加到运行中的进程 |
| 增量 GC | — | 大文档知识库构建时 GC 暂停显著减少 |
| 尾调用解释器 | — | pyperformance 基准快 3%-5% |

### 1.5 PySide6 6.11 适配要点

| 特性 | 项目中的应用 |
|------|-------------|
| 信号发射优化 (PYSIDE-3279) | UI 响应更快，高频 token 流式显示更流畅 |
| QtWebView 模块 | 可选择替代 QWebEngineView 降低包体积（评估中） |
| pyproject.toml 工具配置 | 打包时可配置 pyside6-rcc 压缩参数 |
| 多继承修复 (PYSIDE-3282) | `_BaseServiceMeta` 元类组合更稳定 |
| 最低 Python 3.10 | Python 3.9 被正式丢弃 |

---

## 2. 系统架构

### 2.1 分层架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        UI 层 (src/ui/)                            │
│                                                                  │
│  MainWindow ──┬── PdfViewer (QScrollArea + 虚拟视口懒加载)       │
│               │      ├── _LazyPageWidget (按页占位/渲染)         │
│               │      │     └── BlockOverlay (透明交互热区)       │
│               │      └── SplitWidget (裂缝容器 — QWebEngineView) │
│               ├── QDockWidget (左: 目录导航 / 右: AI 工具集)     │
│               └── QStatusBar (页码/模型状态/进度)                 │
│                                                                  │
│  信号流: UI 信号 → Core 服务 → 信号回调 → UI 更新               │
└─────────────────────────┬────────────────────────────────────────┘
                          │  Qt 信号/槽 + QThread
┌─────────────────────────┼────────────────────────────────────────┐
│                         │       Core 层 (src/core/)               │
│                                                                  │
│  CoreServiceRegistry (服务定位器 — 依赖注入容器)                  │
│     ├── DocumentEngine (PDF 解析 + 块分割 + 渲染缓存)            │
│     ├── AIEngine (翻译/问答协调器 — 线程管理 + 信号转发)         │
│     │     ├── TranslationService (Prompt 构建 + 公式保护)        │
│     │     └── QAService (知识库检索 + 上下文组装)                │
│     ├── KnowledgeEngine (向量嵌入 + ChromaDB 构建/检索)          │
│     ├── GlossaryManager (术语表 CRUD + Prompt 注入)              │
│     └── Navigator (目录 + 书签管理)                              │
│                                                                  │
│  AI 客户端层:                                                    │
│     ├── BaseLLMClient (抽象接口)                                  │
│     ├── OllamaClient (本地 Ollama REST API)                      │
│     ├── LiteLLMClient (云端统一客户端 + 自动重试)                 │
│     ├── MockLLMClient (测试/降级用模拟客户端)                     │
│     └── HybridModelRouter (任务路由决策)                          │
│                                                                  │
│  辅助模块:                                                       │
│     ├── DocumentChunker (段落分割 + 公式检测 + 双栏处理)         │
│     ├── TextPreprocessor (公式占位符保护/恢复)                   │
│     ├── Pix2TextMFDDetector (ML 公式精扫 — 阶段二)               │
│     └── EmbeddingService (BGE-M3 向量化 + 缓存)                  │
│                                                                  │
└─────────────────────────┬────────────────────────────────────────┘
                          │
┌─────────────────────────┼────────────────────────────────────────┐
│                         │       Data 层 (src/data/)               │
│                                                                  │
│     ChromaRepo (ChromaDB CRUD + 余弦检索)                       │
│     GlossaryRepo (JSON 术语表读写 + CSV/JSON 导入)               │
│     ConfigManager (YAML 配置读写 + 热加载 + .env 集成)           │
│                                                                  │
│  存储后端:                                                       │
│     ChromaDB (data/knowledge_bases/) — 向量 + 元数据             │
│     JSON 文件 (data/glossary/) — 术语表                          │
│     YAML 文件 (config.yaml) — 用户配置                           │
│     .env — API Key 环境变量                                      │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 模块依赖方向

严格自下而上：**Data ← Core ← UI**。禁止反向依赖。

- `src/core/` 依赖 `src/data/` 的全部模块
- `src/ui/` 依赖 `src/core/` 和 `src/data/`
- UI 层通过 `CoreServiceRegistry` 获取 Core 服务实例
- 跨层通信仅通过 Qt 信号/槽和 `CoreServiceRegistry`

### 2.3 项目目录结构（实际）

```
D:\程设大作业\
├── config.yaml                    # 应用配置（模型、路由、UI、API Key）
├── requirements.txt               # 精确依赖清单
├── run_py314.bat                  # Conda 环境启动脚本
├── TODO.md                        # 开发 TODO
├── .gitignore
├── .vscode/settings.json          # VS Code 配置（conda 路径）
├── .claude/settings.local.json    # Claude 权限配置
├── data/
│   ├── glossary/                  # 内置学科术语包
│   │   ├── math.json              # 数学（73 条术语）
│   │   ├── cs_ml.json             # 计算机科学/ML（70 条术语）
│   │   └── physics.json           # 物理（45 条术语）
│   └── knowledge_bases/           # ChromaDB 持久化数据（运行时生成）
│       ├── chroma.sqlite3
│       └── <collection_uuid>/
├── logs/
│   └── app.log                    # 应用运行日志
├── src/
│   ├── __init__.py
│   ├── main.py                    # 程序入口：QApplication + 服务初始化
│   ├── core/
│   │   ├── __init__.py
│   │   ├── models.py              # 全部 Pydantic 数据模型
│   │   ├── base_service.py        # BaseService 抽象基类
│   │   ├── service_registry.py    # CoreServiceRegistry 服务定位器
│   │   ├── pdf_engine.py          # DocumentEngine + DocumentChunker + TextPreprocessor
│   │   ├── formula_detector.py    # FormulaDetector 接口 + Pix2TextMFDDetector
│   │   ├── ai_engine.py           # AIEngine + LLM 客户端 + 路由 + 翻译/问答服务
│   │   ├── knowledge_engine.py    # KnowledgeEngine + EmbeddingService
│   │   ├── glossary_manager.py    # GlossaryManager 术语表管理
│   │   └── navigator.py           # Navigator 目录/书签管理
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── main_window.py         # MainWindow 主窗口
│   │   ├── pdf_viewer.py          # PdfViewer + _LazyPageWidget 虚拟视口
│   │   ├── paragraph_widget.py    # BlockOverlay 透明交互热区
│   │   ├── split_widget.py        # SplitWidget 裂缝式交互容器
│   │   ├── theme.py               # QPalette 主题 + SplitWidget QSS
│   │   ├── markdown_template.html # marked.js + KaTeX HTML 模板
│   │   ├── marked.min.js          # Markdown → HTML (本地)
│   │   ├── katex.min.js           # LaTeX 渲染 (本地)
│   │   ├── katex.min.css          # KaTeX 样式 (本地)
│   │   └── auto-render.min.js     # KaTeX 自动渲染 (本地)
│   └── data/
│       ├── __init__.py
│       ├── chroma_repo.py         # ChromaRepo
│       ├── config_manager.py      # ConfigManager
│       └── glossary_repo.py       # GlossaryRepo
└── tests/
    └── __init__.py
```

---

## 3. 核心数据流

### 3.1 流程 A：打开 PDF → 解析 → 呈现（两阶段异步）

**阶段一（极速呈现，<0.5s）**：
1. 用户通过 `QFileDialog` 或菜单选择 PDF
2. `MainWindow._open_pdf_file()` 调用 `DocumentEngine.open_document(filepath)`
3. `_ParseThread` 在工作线程中执行：
   - `fitz.open(filepath)` 打开文档
   - 提取元数据（标题、作者、页数、原生目录）
   - 调用 `DocumentChunker.chunk(doc)` 启发式分块（基于字体/坐标/LaTeX 模式）
   - 发射 `progress` 信号更新进度条
   - 发射 `finished_parsing(ParseResult)` 信号
4. 主线程接收 `parse_finished`：
   - `PdfViewer.load_document(result)` 创建 `_LazyPageWidget` 占位（按 `page.rect` 计算尺寸）
   - `Navigator.load_toc()` 或 `Navigator.generate_toc_from_blocks()` 加载目录
   - 立即可以滚动浏览（首屏懒加载渲染）

**阶段二（后台精扫，不阻塞阅读）**：
5. `_ParseThread` 继续在后台执行（在 `finished_parsing` 发射后）：
   - 调用 `Pix2TextMFDDetector.apply_to_blocks()` 仅对候选页面（含 LaTeX 命令的页面）跑 ONNX 模型
   - 发射 `formula_blocks_updated(updated: list[dict])` 信号
6. 主线程接收 `formula_blocks_updated`：
   - 更新内存中 `DocumentBlock.block_type` 为 FORMULA
   - 刷新对应 `BlockOverlay` 样式
   - 状态栏更新"公式精扫完成"

**知识库构建（独立异步）**：
7. `MainWindow._on_document_loaded()` 中检查知识库：
   - 若已存在 → 状态栏显示"知识库已就绪"
   - 若不存在 → 调用 `KnowledgeEngine.build_knowledge_base()`，在 `QThreadPool` 中执行
8. 构建过程：分批生成 BGE-M3 嵌入 → 分批（50 条/批）写入 ChromaDB
9. 通过 `build_progress` / `build_finished` / `build_error` 信号反馈

### 3.2 流程 B：段落翻译（裂缝交互）

1. 用户右键 BlockOverlay → "翻译段落"
2. `BlockOverlay.translate_requested.emit(block_id)`
3. → `PdfViewer.block_translate_requested.emit(block_id)`
4. → `MainWindow._on_block_translate(block_id)`:
   - 调用 `PdfViewer.open_split_widget(block_id, SplitMode.TRANSLATION)` 裂开页面
   - 调用 `AIEngine.request_translation(block)` 启工作线程
5. `_TranslationThread` 在线程中执行 `TranslationService.translate_block()`：
   - `TextPreprocessor.protect_formulas()` 替换公式为占位符
   - `_build_messages()` 注入系统 Prompt + 术语表 + Few-shot 示例
   - `HybridModelRouter.route(TaskType.TRANSLATION)` 选择客户端
   - 流式调用 LLM，每个 token 通过 `token_generated` 信号发送
6. `AIEngine.translation_token` → `MainWindow._on_translation_token` → `SplitWidget.display_answer_stream(token)`
7. `SplitWidget` 将 token 追加到内部缓冲区，通过 `QWebEngineView.runJavaScript()` 调用 `updateContent()` 实时渲染 Markdown + LaTeX
8. 翻译完成：`SplitWidget.display_full_answer()` 做最终渲染

### 3.3 流程 C：裂缝问答（含知识库检索）

1. 用户右键 BlockOverlay → "在此处提问"
2. `SplitWidget` 以 `SplitMode.QUESTION` 模式打开，显示输入框
3. 用户输入问题后点"发送"或 Ctrl+Enter
4. `MainWindow._on_split_ask()`:
   - 调用 `KnowledgeEngine.retrieve(question, doc_hash, top_k=3)` 检索相关块
   - 调用 `AIEngine.request_answer(question, current_block, retrieved_blocks, chat_history, split_id)`
5. `_QAThread` 在线程中执行 `QAService.answer()`：
   - `_build_qa_messages()` 组装上下文（当前块 + 检索结果 + 对话历史）
   - 流式调用 LLM
6. 回答流式显示在 `SplitWidget` 的 QWebEngineView 中

---

## 4. 数据模型 (`src/core/models.py`)

所有结构使用 Pydantic 定义。以下列出实际存在的模型：

### 4.1 块类型

```python
class BlockType(str, Enum):
    PARAGRAPH = "paragraph"   # 普通段落
    FORMULA = "formula"       # 数学公式 (LaTeX)
    HEADING = "heading"       # 章节标题
    IMAGE = "image"           # 图片（预留）
    TABLE = "table"           # 表格（预留）
```

### 4.2 DocumentBlock

```python
class DocumentBlock(BaseModel):
    id: str                                          # 唯一标识 "p{page}_b{index}"
    page_num: int                                    # 页码 (0-based)
    block_type: BlockType                            # 块类型
    content: str                                     # 文本内容/LaTeX 源码
    bbox: tuple[float, float, float, float]          # 包围框 (x0,y0,x1,y1) pt
    section_title: str = ""                          # 所属章节标题
    metadata: dict = Field(default_factory=dict)     # 扩展元数据 (summary, is_theorem, domain, formula_detector 等)
```

### 4.3 GlossaryEntry

```python
class GlossaryEntry(BaseModel):
    en: str                              # 英文术语
    zh: str                              # 中文翻译
    domain: str                          # 学科领域
    force: bool = False                  # 是否强制使用此翻译
    aliases: list[str] = Field(default_factory=list)
    notes: str = ""
```

### 4.4 配置模型

```python
class ModelConfig(BaseModel):
    local: str = "qwen3.5:4b"
    cloud: str = "deepseek/deepseek-chat"
    embed_local: str = "bge-m3"
    ollama_host: str = "http://localhost:11434"

class RoutingConfig(BaseModel):
    translation: str = "local_first"
    qa: str = "local_first"
    summarization: str = "local_first"
    embed: str = "local_only"
    auto_upgrade_threshold: int = 2000

class UIConfig(BaseModel):
    language: str = "zh_CN"
    theme: str = "light"                    # light / dark / sepia
    split_position: str = "below"
    font_size: int = 12
    line_spacing: float = 1.5
    show_word_translation: bool = False

class AppConfig(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    api_keys: dict[str, str] = Field(default_factory=dict)
```

### 4.5 运行时数据结构

```python
class ParseResult(BaseModel):
    filepath: str
    title: str = ""
    author: str = ""
    page_count: int = 0
    toc: list[dict] = Field(default_factory=list)
    blocks: list[DocumentBlock] = Field(default_factory=list)

class KnowledgeStatus(BaseModel):
    doc_hash: str
    collection_name: str
    is_ready: bool = False
    total_blocks: int = 0
    embedded_blocks: int = 0
    build_time_seconds: float = 0.0

class Bookmark(BaseModel):
    id: str
    page_num: int
    title: str
    note: str = ""
    is_ai_suggested: bool = False
    created_at: str = ""
```

### 4.6 裂缝状态枚举

```python
class SplitMode(str, Enum):
    QUESTION = "question"          # 提问模式
    TRANSLATION = "translation"    # 翻译模式
    EXPLANATION = "explanation"    # 解释模式

class SplitState(str, Enum):
    HIDDEN = "hidden"
    OPENING = "opening"
    READY = "ready"
    BUSY = "busy"
    CLOSING = "closing"
```

### 4.7 AI 任务类型

```python
class TaskType(str, Enum):
    TRANSLATION = "translation"
    QA = "qa"
    EMBEDDING = "embedding"
    SUMMARIZATION = "summarization"
    FOLLOWUP_QUESTIONS = "followup_questions"

class RoutingStrategy(str, Enum):
    LOCAL_FIRST = "local_first"
    CLOUD_ONLY = "cloud_only"
    LOCAL_ONLY = "local_only"
```

---

## 5. Core 层详细设计

### 5.1 服务基类 (`src/core/base_service.py`)

```python
class _BaseServiceMeta(type(QObject), ABCMeta):
    """组合 QObject 和 ABC 的元类——解决 PySide6 多继承元类冲突。"""
    pass

class BaseService(QObject, metaclass=_BaseServiceMeta):
    """所有 Core 服务的抽象基类。提供:
    - logger: 服务专属日志记录器
    - QObject 的 moveToThread 支持
    """
    def __init__(self, parent: QObject | None = None): ...
    @property
    def logger(self) -> logging.Logger: ...
```

### 5.2 服务注册中心 (`src/core/service_registry.py`)

```python
class CoreServiceRegistry:
    """IoC 容器——应用启动时注册所有服务实例，UI 层按名获取。"""
    def register(self, name: str, service: Any) -> None: ...
    def get(self, name: str) -> Any: ...        # KeyError 若未注册
    def unregister(self, name: str) -> None: ...
    @property
    def registered_services(self) -> list[str]: ...
```

注册表（启动时由 `main.py` 建立）：
| 名称 | 类型 |
|------|------|
| `config_manager` | ConfigManager |
| `document_engine` | DocumentEngine |
| `chroma_repo` | ChromaRepo |
| `knowledge_engine` | KnowledgeEngine |
| `glossary_manager` | GlossaryManager |
| `ai_engine` | AIEngine |
| `navigator` | Navigator |

### 5.3 文档引擎 (`src/core/pdf_engine.py`)

#### TextPreprocessor

```python
class TextPreprocessor:
    """翻译文本预处理器。核心职责：
    - protect_formulas(text) → 将 LaTeX 公式 $$...$$ / $...$ 替换为占位符 【FORMULA_N】
    - restore_formulas(text) → 将占位符反向替换回原始 LaTeX
    - clean_text(text) → 合并 PDF 断行、修复断词连字符
    """
    def protect_formulas(self, text: str) -> str: ...
    def restore_formulas(self, translated_text: str) -> str: ...
    @staticmethod
    def clean_text(text: str) -> str: ...
```

#### DocumentChunker

```python
class DocumentChunker:
    """PDF 智能分块器。策略：
    - 信任 PyMuPDF 的 text block 划分（PDF 内部结构已准确）
    - 在 block 级别做类型判定（标题/公式/段落）
    - 双栏检测与交错排列（y 坐标排序实现阅读顺序）
    - 公式检测：字体名 + LaTeX 命令 + 数学 Unicode 占比

    关键方法:
    - chunk(doc: fitz.Document) → list[DocumentBlock]     主入口
    - _extract_page_blocks(page, page_num) → list[...]    单页提取
    - _detect_columns(blocks) → list[list[...]]           双栏检测
    - _interleave_columns(cols) → list[...]               双栏交错
    - _is_formula_from_spans(spans) → bool                公式判定（多规则融合）
    - _is_heading(span, median_font_size) → bool          标题判定
    - rechunk_blocks(blocks, merge_indices) → list[...]   手动合并块
    - split_block(block, split_position) → tuple[...]     手动拆分块
    """
```

公式检测规则（`_is_formula_from_spans`）：
1. 排除太短 (<8 字符)、email 格式
2. 英文单词 >20 个 → 不是公式
3. 字体名含 "CM"/"Math"/"Symbol"/"Cambria"/"STIX"/"XITS"/"TeX" → 是公式
4. LaTeX 命令 ≥2 个（`\frac`, `\sum`, `\int` 等）→ 是公式
5. 数学 Unicode 占比 >15% 且总长 <300 → 是公式

#### DocumentEngine

```python
class DocumentEngine(BaseService):
    """PDF 文档处理引擎。协调解析流水线，管理渲染缓存。

    信号:
    - parse_finished(ParseResult)     文档解析完成
    - parse_progress(int, int)        解析进度 (当前页, 总页数)
    - parse_error(str)                解析错误
    - formula_blocks_updated(list)    阶段二 MFD 公式精扫结果

    属性:
    - is_open: bool                   是否有打开的文档
    - page_count: int                 总页数
    - chunker: DocumentChunker       分块器实例
    - preprocessor: TextPreprocessor 预处理器实例
    - document: fitz.Document | None 底层 PyMuPDF 文档

    方法:
    - open_document(filepath) → None   异步打开 PDF（创建 _ParseThread）
    - close_document() → None          关闭文档，释放资源
    - get_page_pixmap(page_num, dpi=150) → QPixmap | None  页面渲染（带缓存+LRU）
    - preload_pages(page_nums, dpi=150) → None              预加载到缓存
    """
```

渲染缓存策略：
- 双层字典 `{page_num: {dpi: QPixmap}}`
- LRU 淘汰：缓存超过 20 个页面时删除最旧条目
- PPM 格式零拷贝转换（`pix.tobytes("ppm")` → `QPixmap.loadFromData`）

#### _ParseThread

```python
class _ParseThread(QThread):
    """两阶段异步 PDF 解析线程。

    阶段一：
    - fitz.open() 打开文档
    - 提取 metadata、TOC
    - chunker.chunk(doc) 启发式分块
    - 发射 finished_parsing(ParseResult)

    阶段二（在 finished_parsing 之后继续执行）：
    - Pix2TextMFDDetector.apply_to_blocks() ML 精扫
    - 仅对有 LaTeX 特征的候选页面运行 ONNX 模型
    - 发射 formula_blocks_updated(updated)

    信号:
    - progress(int, int)
    - finished_parsing(ParseResult)
    - parse_error(str)
    - formula_blocks_updated(list)
    """
```

### 5.4 公式检测器 (`src/core/formula_detector.py`)

```python
class FormulaDetector(ABC):
    """公式检测器抽象接口。"""
    @abstractmethod
    def detect(self, doc: fitz.Document) -> list[dict[str, Any]]: ...
    @abstractmethod
    def name(self) -> str: ...

class Pix2TextMFDDetector(FormulaDetector):
    """Pix2Text MFD 纯公式检测。

    特点：
    - ONNX Runtime 推理，无需 PyTorch
    - 只检测 bbox，不做 LaTeX 识别
    - ~1.5s/页，仅对候选页面运行

    方法:
    - detect(doc) → list[{page, bbox, latex, score}]
    - apply_to_blocks(blocks, doc) → 原地修改 block_type 为 FORMULA
    - _page_has_formulas(blocks, page_num) → 粗略判断是否包含公式
    """
```

### 5.5 AI 引擎 (`src/core/ai_engine.py`)

#### LLM 客户端抽象

```python
class BaseLLMClient(ABC):
    """LLM 客户端抽象基类。"""
    @abstractmethod
    def generate(self, messages: list[dict[str, str]], **kwargs) -> str: ...
    @abstractmethod
    def generate_stream(self, messages: list[dict[str, str]], **kwargs) -> Generator[str, None, None]: ...
    @property
    @abstractmethod
    def model_name(self) -> str: ...
    @abstractmethod
    def check_availability(self) -> bool: ...
```

#### 客户端实现

```python
class OllamaClient(BaseLLMClient):
    """本地 Ollama 客户端。延迟导入 ollama 避免启动失败。
    通过 ollama.Client(host).chat(model, messages, stream, options) 调用。"""

class LiteLLMClient(BaseLLMClient):
    """云端客户端。内置 3 次自动重试（指数退避 1s/1.5s/2.25s）。
    generate_stream 失败时也支持全量重试。"""

class MockLLMClient(BaseLLMClient):
    """模拟客户端——不调用任何 API。检测 System Prompt 中的"翻译"/"将以下英文"
    来自动判断是翻译还是问答任务，生成对应的模拟回复。
    当用户未配置云端 API Key 时作为最终回退使用。"""
```

#### HybridModelRouter

```python
class HybridModelRouter:
    """混合模型路由器。

    构造: HybridModelRouter(local_client, cloud_client, config)
    - local_client: OllamaClient（实际未使用——仅检测可用性）
    - cloud_client: LiteLLMClient 或 MockLLMClient
    - 当前实现：EMBEDDING 固定本地，其余任务优先检测 local→cloud→fallback

    决策逻辑:
    - EMBEDDING → 固定本地（BGE-M3 必须可用）
    - 其他任务 → 根据 config.routing 的策略：
      | local_only  → 仅本地，失败抛异常
      | cloud_only  → 仅云端，失败抛异常
      | local_first → 本地优先，不可用时回退云端
    """
    def route(self, task: TaskType) -> BaseLLMClient: ...
    @property
    def local_available(self) -> bool: ...
    @property
    def cloud_available(self) -> bool: ...
```

#### TranslationService

```python
class TranslationService:
    """专业论文翻译服务。

    System Prompt 控制点:
    1. 角色设定: "精通{domain}的科技翻译专家"
    2. 公式保护: "所有【FORMULA_x】占位符必须原样保留"
    3. 语态转换: "化被动为主动，英文长句拆分为中文短句"
    4. 术语强制: 注入 {glossary_terms} 映射表
    5. 定理保护: "条件与结论精确对应，不改变逻辑连接词"
    6. LaTeX 格式: "行内 \(...\)，行间 \[...\]"
    7. Few-shot 示例: 2 组英中对照范例

    方法:
    - translate_block(block, domain, stream=True) → Generator | str
    - translate_sentences(sentences, domain) → list[str]
    - translate_word(word) → str
    - update_glossary(entries: list[GlossaryEntry]) → None
    - _build_messages(text, domain, block_type) → list[dict]  构建完整消息列表
    - _format_glossary() → str                                 "- en -> zh [强制]"
    - _post_process(translated) → str                         公式恢复
    """
```

#### QAService

```python
class QAService:
    """文档问答服务。

    System Prompt 控制点:
    1. "严格依据提供的文档片段回答问题"
    2. "如果答案不在提供的片段中，明确告知无法确定，绝不编造"
    3. LaTeX 格式展示数学公式
    4. 引用具体章节/定理编号/页码

    上下文组装策略:
    - [当前段落 — 第N页] 放在最前面
    - [相关片段1/2/3 — 第N页] 按检索相似度排列
    - 多轮对话历史保留最近 6 轮（12 条消息）

    方法:
    - answer(question, current_block, retrieved_blocks, chat_history, stream=True) → Generator | str
    - generate_followup_questions(question, answer) → list[str]  生成 3 个追问
    - _build_qa_messages(...) → list[dict]
    """
```

#### AIEngine（顶层协调器）

```python
class AIEngine(BaseService):
    """AI 引擎协调器——UI 层的唯一 AI 入口。

    信号:
    - translation_token(str, str)       (token, block_id)
    - translation_finished(str, str)    (完整译文, block_id)
    - translation_error(str, str)       (错误信息, block_id)
    - answer_token(str, str)            (token, split_id)
    - answer_finished(str, str)         (完整回答, split_id)
    - answer_error(str, str)            (错误信息, split_id)

    方法:
    - request_translation(block, domain="math") → None    启翻译线程
    - request_answer(question, current_block, retrieved_blocks, chat_history, split_id) → None
    - check_local_model_status() → dict[str, bool]        {ollama/qwen/bge}_available
    """
```

#### 工作线程

```python
class _TranslationThread(QThread):
    """翻译线程——直接继承 QThread 保证信号可靠。"""
    token_generated = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)
    def run(self) → None:  遍历 translate_block() 的流式 token 并发射信号

class _QAThread(QThread):
    """问答线程——结构与 _TranslationThread 类似。"""
    token_generated = Signal(str)
    finished_signal = Signal(str)
    error_signal = Signal(str)
```

### 5.6 知识库引擎 (`src/core/knowledge_engine.py`)

#### EmbeddingService

```python
class EmbeddingService:
    """文本向量化服务。通过 Ollama BGE-M3 生成 1024 维语义向量。

    特性:
    - 每批最多 20 条文本
    - 内存 LRU 缓存（最多 5000 条，~20MB）
    - 逐条调用 ollama.Client.embeddings()（非 batch API）

    方法:
    - embed(texts: list[str]) → list[list[float]]     批量向量化（先查缓存）
    - embed_single(text: str) → list[float]            单条向量化
    - clear_cache() → None
    - check_availability() → bool                      试嵌 "test" 检测可用性
    """
```

#### KnowledgeEngine

```python
class KnowledgeEngine(BaseService):
    """知识库引擎。

    信号:
    - build_progress(int, int)        (已完成, 总数)
    - build_finished(str)             (doc_hash)
    - build_error(str)

    方法:
    - build_knowledge_base(blocks, doc_hash, force_rebuild=False) → None
      QThreadPool 异步构建：创建 Collection → 分批嵌入 → QMutex 保护写入
    - retrieve(query, doc_hash, top_k=3, exclude_ids=None) → list[dict]
      查询向量化 → ChromaRepo.query_relevant() 余弦搜索
    - delete_knowledge_base(doc_hash) → None
    - check_exists(doc_hash) → bool
    - get_status(doc_hash) → KnowledgeStatus
    """
```

#### _BuildWorker (QRunnable)

```python
class _BuildWorker(QRunnable):
    """QThreadPool 构建 Worker。
    - 分批生成嵌入向量（EmbeddingService.embed() 自动分批 20 条）
    - 分批写入 ChromaDB（每批 50 条，QMutex 防 SQLite 并发锁死）
    - 通过内部 Signal QObject 发射 progress/finished/error
    """
```

### 5.7 术语表管理器 (`src/core/glossary_manager.py`)

```python
class GlossaryManager:
    """术语表管理器。在内存中维护 {domain: [GlossaryEntry, ...]} 字典。

    方法:
    - reload() → None                                   重载 JSON 文件
    - add_term(en, zh, domain, force) → GlossaryEntry   添加术语
    - remove_term(en, domain) → bool                    删除术语
    - get_translation_mapping(domains) → dict[str,str]  {英文: 中文}
    - format_for_prompt(domains) → str                  "- en → zh"
    - search_terms(keyword) → list[GlossaryEntry]       模糊搜索
    - resolve_conflict(en, preferred_domain) → str      多义消歧
    - get_entries(domains=None) → list[GlossaryEntry]   获取全部/指定领域术语
    - save() → None                                     写回 JSON 文件
    - import_user_glossary(filepath) → int              导入外部文件
    - domains: list[str]                                已加载领域列表
    """
```

### 5.8 导航器 (`src/core/navigator.py`)

```python
class Navigator(BaseService):
    """文档导航器——管理目录树和书签列表。

    信号:
    - toc_ready(list)              目录数据就绪
    - bookmarks_changed(list)      书签变更
    - ai_bookmarks_suggested(list) AI 建议书签

    方法:
    - load_toc(raw_toc: list[dict]) → None              加载原生大纲
    - generate_toc_from_blocks(blocks) → list[dict]    从 heading 块推断目录
    - add_bookmark(page_num, title, note) → Bookmark    手动添加书签
    - remove_bookmark(bookmark_id) → bool               删除书签
    - reorder_bookmarks(ordered_ids) → None             拖拽排序
    - suggest_ai_bookmarks(blocks) → list[dict]         自动建议（heading + theorem 块）
    """
```

---

## 6. Data 层设计

### 6.1 ChromaRepo (`src/data/chroma_repo.py`)

```python
class ChromaRepo:
    """ChromaDB 向量存储仓库。
    - 每个 PDF 对应独立 Collection (命名: pdf_{doc_hash})
    - 余弦相似度检索 (hnsw:space = cosine)
    - 所有方法为同步调用，由调用方负责放到工作线程

    方法:
    - create_or_get_collection(doc_hash) → Collection
    - get_collection(doc_hash) → Collection
    - upsert_blocks(doc_hash, block_ids, documents, vectors, metadatas) → None
    - query_relevant(doc_hash, query_vector, top_k, exclude_ids) → list[dict]
    - get_block_by_id(doc_hash, block_id) → dict | None
    - delete_collection(doc_hash) → None
    - collection_exists(doc_hash) → bool
    - list_collections() → list[str]
    @staticmethod compute_doc_hash(filepath) → str       SHA256[:16]
    """
```

### 6.2 GlossaryRepo (`src/data/glossary_repo.py`)

```python
class GlossaryRepo:
    """术语表持久化仓库。支持 JSON (.json) 和 CSV (.csv) 格式。

    方法:
    - load_all() → dict[str, list[GlossaryEntry]]     加载所有 JSON
    - load_domain(domain) → list[GlossaryEntry]        加载指定领域
    - save_domain(domain, entries) → None              保存领域
    - import_from_file(filepath) → list[GlossaryEntry] 导入外部文件
    """
```

### 6.3 ConfigManager (`src/data/config_manager.py`)

```python
class ConfigManager(QObject):
    """YAML 配置管理器。

    信号: config_changed(AppConfig)

    方法:
    - load() → AppConfig               启动时加载/创建默认配置
    - save() → None                    写回 YAML 文件
    - get() → AppConfig                深拷贝副本（修改不影响内部状态）
    - update(partial: dict) → None     部分更新（深度合并）+ 自动 save + 发射信号
    - get_api_key(provider) → str|None 查 config → 查环境变量

    加载策略: 深度合并 YAML ↔ Pydantic 默认值
    .env 支持: 使用 python-dotenv (可选依赖)
    """
```

---

## 7. UI 层设计

### 7.1 MainWindow (`src/ui/main_window.py`)

```python
class MainWindow(QMainWindow):
    """主窗口。
    布局: 菜单栏 → 工具栏 → [左Dock | PdfViewer | 右Dock] → 状态栏

    核心职责:
    - UI 信号 ↔ Core 服务信号的桥接
    - 文档加载/关闭的完整流程协调
    - 翻译/问答的触发与流式回调处理
    - 首次启动 Ollama/模型检测
    - 设置对话框（云端 API 配置）

    关键槽:
    - _on_document_loaded(ParseResult)       → PdfViewer 加载 + 目录 + 知识库构建
    - _on_block_translate(block_id)          → 裂开页面 + AIEngine 翻译
    - _on_split_ask(question, block_id)      → 知识库检索 + AIEngine 问答
    - _on_translation_token/...              → SplitWidget 流式显示
    - _on_formula_blocks_updated(updated)    → 刷新 BlockOverlay

    状态管理:
    - _current_blocks: list[DocumentBlock]   当前文档全量块
    - _current_doc_hash: str                 文档哈希
    """
```

### 7.2 PdfViewer (`src/ui/pdf_viewer.py`)

```python
class PdfViewer(QScrollArea):
    """PDF 滚动阅读区——虚拟视口懒加载。

    信号:
    - block_double_clicked(str)
    - block_translate_requested(str)
    - block_question_requested(str)
    - block_explain_requested(str)
    - viewport_changed(int, int)
    - split_close_requested(str)

    核心优化:
    - 虚拟视口：仅渲染可见 ±1 视口高度的页面
    - 离屏页面释放 pixmap 内存 → 灰色占位
    - 80ms 防抖滚动监听

    核心方法:
    - load_document(ParseResult) → None              创建全量 _LazyPageWidget 占位
    - open_split_widget(block_id, mode) → SplitWidget|None  在段落下边界裂开页面
    - find_split_widget(block_id) → SplitWidget|None
    - scroll_to_page(page_num) → None
    - clear() → None                                  清空全部资源
    """

class _LazyPageWidget(QWidget):
    """支持延迟加载的页面容器。
    - 未渲染: 浅灰背景 + "…" 占位
    - 已渲染: pixmap QLabel + BlockOverlay 叠加层
    - unrender(): 释放 pixmap，释放 overlay，恢复占位
    """
```

裂开算法（`open_split_widget`）：
1. 确定 block_id 所在的页面和段（segment）
2. 计算切割线 y = block.bbox[3] * scale + 2 (段落下边界)
3. 将原段 widget 替换为：上半段 widget + SplitWidget + 下半段 widget
4. 上半段/下半段各包含裁切后的 pixmap 和对应 BlockOverlay
5. 滚动到 SplitWidget 可见位置

合并算法（`_merge_segments`）：
1. 找到裂缝对应的前一段和后一段
2. 合并 block 列表和 pixmap 区域
3. 创建新的完整段 widget 替换两个半段 + 裂缝

### 7.3 BlockOverlay (`src/ui/paragraph_widget.py`)

```python
class BlockOverlay(QWidget):
    """透明交互热区——覆盖在 PDF 页面图片上方。

    信号:
    - clicked(str)               单击 → block_id
    - double_clicked(str)        双击 → block_id
    - translate_requested(str)   右键"翻译段落"
    - question_requested(str)    右键"在此处提问"
    - explain_requested(str)     右键"解释此概念/公式"

    视觉效果:
    - 默认完全透明（不遮挡 PDF）
    - 悬停: 淡紫半透明 (108,92,231, 38α)
    - 选中: 深紫半透明 (108,92,231, 75α)
    - 公式: 浅灰 (240,240,250, 200α)
    - 定理环境: 左侧 3px 橙色竖线

    右键菜单:
    - 📖 翻译段落
    - 🔍 在此处提问
    - ✏️ 解释此公式/概念
    """
```

### 7.4 SplitWidget (`src/ui/split_widget.py`)

```python
class SplitWidget(QFrame):
    """裂缝式交互容器——软件的核心 UI 组件。

    两种形态:
    1. 展开态: 全宽视图，包含标题栏/上下文/输入区/QWebEngineView 结果区/操作栏
    2. 折叠态: setVisible(False) 完全隐藏——页面上下段无缝合并

    特性:
    - QWebEngineView 渲染 Markdown + LaTeX（加载本地 HTML 模板）
    - JavaScript 桥接: updateContent(text, isFinished) 注入内容
    - 底部可拖拽手柄调整高度（_ResizeHandle）
    - 高度自适应: 内容超出当前高度时自动撑高（最大 600px），用户拖拽后锁定
    - 折叠/展开: 双击 SplitWidget 自身或点击 ∧ 按钮
    - 多轮对话: 保留最近 6 轮 chat_history
    - Esc 键折叠

    模式:
    - TRANSLATION: 隐藏输入区，仅显示译文结果
    - QUESTION: 完整交互形态（输入框 + 发送 + 追问建议）
    - EXPLANATION: 类似 QUESTION，自动填充解释 Prompt

    JavaScript 渲染管线 (markdown_template.html):
    1. protectFormulas(): 提取 \(...\) / $$...$$ / $...$ 为占位符
    2. marked.parse(): Markdown → HTML
    3. restoreFormulas(): 占位符 → katex.renderToString() 渲染的 HTML
    4. updateContent() 写入 #content DOM
    """
```

### 7.5 主题模块 (`src/ui/theme.py`)

```python
# 三套 QPalette 主题（通过 QApplication.setPalette() 全局应用）:
apply_theme("light")   # 素白（学术）
apply_theme("dark")    # 暗夜
apply_theme("sepia")   # 护眼羊皮纸

# SplitWidget 专用 QSS (SPLIT_WIDGET_STYLE):
# - 蓝紫渐变背景 (qlineargradient)
# - 圆角 12px
# - 定制按钮/输入框/结果区样式
# - 翻译模式下使用更简洁的蓝色系样式
```

---

## 8. 线程模型与并发安全

### 8.1 线程架构

| 线程 | 职责 | 数量 | 创建方式 |
|------|------|------|---------|
| 主线程 (GUI) | Qt 事件循环、Widget 绘制、信号/槽分发 | 1 | QApplication 自动 |
| _ParseThread | PDF 打开、文本提取、启发式分块 → MFD 精扫 | 1（可复用，上一线程等待 3s 超时后强制终止） | QThread 直接继承 |
| QThreadPool | 知识库构建（嵌入 + ChromaDB 写入） | 最多 2 | QThreadPool + QRunnable |
| _TranslationThread | LLM 流式翻译 | 每请求 1 个 | QThread 直接继承 |
| _QAThread | LLM 流式问答 | 每请求 1 个 | QThread 直接继承 |

### 8.2 线程安全规则

1. **所有 UI 更新必须在主线程**：工作线程通过 `Signal.emit()` 跨线程传递数据。Qt 信号/槽自动处理跨线程调度。
2. **ChromaDB 写入加锁**：`_BuildWorker` 使用 `QMutexLocker` 保护分批写入，防止 SQLite 并发锁死。
3. **共享数据不可变传递**：`list[DocumentBlock]` 构建完成后通过信号传递，工作线程不再修改。
4. **QThread 生命周期管理**：`AIEngine._active_threads` 保持引用防止 Python GC 回收导致信号断开。
5. **线程中断处理**：_ParseThread 支持 `requestInterruption()`；旧线程 3s 超时后 `terminate()`。

---

## 9. 信号连接总表

```
# === 文档打开与解析 ===
DocumentEngine.parse_finished(ParseResult)
  → MainWindow._on_document_loaded()
  → PdfViewer.load_document()

DocumentEngine.parse_progress(current, total)
  → MainWindow._on_parse_progress()

DocumentEngine.parse_error(msg)
  → MainWindow._on_parse_error()

DocumentEngine.formula_blocks_updated(list)
  → MainWindow._on_formula_blocks_updated()

# === 知识库构建 ===
KnowledgeEngine.build_progress(current, total)
  → MainWindow._on_kb_progress()

KnowledgeEngine.build_finished(doc_hash)
  → MainWindow._on_kb_finished()

KnowledgeEngine.build_error(msg)
  → MainWindow._on_kb_error()

# === 段落交互 ===
BlockOverlay.double_clicked(block_id)
  → PdfViewer.block_double_clicked
  → MainWindow._on_block_double_clicked()

BlockOverlay.translate_requested(block_id)
  → PdfViewer.block_translate_requested
  → MainWindow._on_block_translate()

BlockOverlay.question_requested(block_id)
  → MainWindow._on_block_question()

BlockOverlay.explain_requested(block_id)
  → MainWindow._on_block_explain()

# === 翻译 ===
AIEngine.translation_token(token, block_id)
  → MainWindow._on_translation_token()
  → SplitWidget.display_answer_stream(token)

AIEngine.translation_finished(text, block_id)
  → MainWindow._on_translation_finished()
  → SplitWidget.display_full_answer(text)

AIEngine.translation_error(msg, block_id)
  → MainWindow._on_translation_error()
  → SplitWidget.show_error(msg)

# === 问答 ===
AIEngine.answer_token(token, split_id)
  → MainWindow._on_answer_token()
  → SplitWidget.display_answer_stream(token)

AIEngine.answer_finished(answer, split_id)
  → MainWindow._on_answer_finished()
  → SplitWidget.display_full_answer(answer)

AIEngine.answer_error(msg, split_id)
  → MainWindow._on_answer_error()
  → SplitWidget.show_error(msg)

# === 导航 ===
Navigator.toc_ready(toc)
  → MainWindow._on_toc_ready()

QTreeWidget.itemClicked
  → MainWindow._on_toc_item_clicked()
  → PdfViewer.scroll_to_page(page_num)

# === 配置 ===
ConfigManager.config_changed(config)
  → MainWindow._on_config_changed()
  → 重新应用主题
```

---

## 10. 错误处理与降级

### 10.1 异常分类

| 异常 | 处理策略 |
|------|---------|
| PDF 文件损坏 | `_ParseThread` 捕获，发射 `parse_error`；`MainWindow` 弹错误对话框 |
| PDF 加密 | 检测 `doc.needs_pass`，发射错误"暂不支持密码保护" |
| Ollama 服务未启动 | `check_local_model_status()` 返回 `ollama_available=False`；状态栏提示 + 引导官网 |
| 本地模型未下载 | 同上，`qwen_available=False`；提示 `ollama pull` 命令 |
| 云端 API 连接失败 | 3 次重试（指数退避 1s/1.5s/2.25s）；失败抛异常 → 线程发射 error 信号 |
| ChromaDB 写入失败 | `_BuildWorker` 捕获，发射 `build_error` |
| 全局未捕获异常 | `sys.excepthook` → 写日志 + 弹简化错误对话框 |

### 10.2 优雅降级

- 本地模型不可用 → `MockLLMClient` 作为最终回退（模拟翻译/问答）
- 未配置 API Key → 系统自动使用 `MockLLMClient` 进入测试模式
- BGE-M3 不可用 → 知识库构建在 catch 块中静默跳过（文件照样可读）
- Pix2Text MFD 不可用 → 阶段二静默跳过，仅用启发式公式检测
- 单个句子翻译失败 → 返回 `"[翻译失败]"`，继续翻译剩余句子

---

## 11. 性能优化

| 优化项 | 实现 |
|--------|------|
| 虚拟视口懒加载 | `_LazyPageWidget` + `_update_visible_pages()` → 仅渲染可见 ±1 视口页面，离屏释放 pixmap |
| 滚动防抖 | QTimer 80ms singleShot → 避免高频计算 |
| Pixmap LRU 缓存 | DocumentEngine 最多保留 20 个页面的渲染结果 |
| 嵌入缓存 | EmbeddingService LRU 最多 5000 条 |
| ChromaDB 分批写入 | 每批 50 个块，QMutex 防并发 |
| 嵌入分批 | 每批 20 条文本 |
| 双栏页面数限制 | 仅候选页面（含 LaTeX 命令的页面）跑 MFD 模型 |
| 线程池限制 | QThreadPool 最多 2 线程 |
| Chromium 沙盒禁用 | Windows 上 `--no-sandbox --disable-gpu-sandbox` 避免 QtWebEngineProcess 崩溃 |
| telemetry 关闭 | `ANONYMIZED_TELEMETRY=False` 避免 posthog 报错 |

---

## 12. 配置管理

### config.yaml 结构

```yaml
model:
  local: qwen3.5:4b
  cloud: deepseek/deepseek-chat
  embed_local: bge-m3
  ollama_host: http://localhost:11434

routing:
  translation: local_first
  qa: local_first
  summarization: local_first
  embed: local_only
  auto_upgrade_threshold: 2000

ui:
  language: zh_CN
  theme: light           # light / dark / sepia
  split_position: below
  font_size: 12
  line_spacing: 1.5
  show_word_translation: false

api_keys: {}
```

### 启动脚本

`run_py314.bat`:
```batch
set PYTHONPATH=D:\程设大作业
conda run -n pdf_ai_reader_314 python src/main.py
```

---

## 13. 开发环境

### Conda 环境配置

```bash
conda create -n pdf_ai_reader_314 python=3.14.4 -y
conda activate pdf_ai_reader_314

pip install pyside6>=6.11.0 pymupdf>=1.27.2 chromadb>=0.5.0 \
            ollama>=0.6.1 litellm>=1.77.3 pydantic>=2.12.0 \
            pyyaml>=6.0.2 pytest>=8.0.0

# 可选：公式 ML 检测
pip install pix2text

# 本地模型（需先安装 Ollama 桌面应用）
ollama pull qwen3.5:4b
ollama pull bge-m3
```

### VS Code 配置 (`.vscode/settings.json`)

指向 conda 环境的 Python 解释器路径。

---

## 14. 当前实现状态

| 模块 | 状态 | 备注 |
|------|------|------|
| PDF 渲染 | ✅ 已实现 | 虚拟视口懒加载 + LRU 缓存 |
| 段落/公式分割 | ✅ 已实现 | 启发式规则 + Pix2Text MFD 精扫 |
| 裂缝式交互 | ✅ 已实现 | SplitWidget + QWebEngineView 渲染 |
| 翻译服务 | ✅ 已实现 | 公式保护 + 术语注入 + Few-shot |
| 问答服务 | ✅ 已实现 | 知识库检索 + 多轮对话 |
| 知识库构建 | ✅ 已实现 | ChromaDB + BGE-M3 嵌入 |
| 术语表管理 | ✅ 已实现 | 3 个内置学科包 + 导入功能 |
| 目录导航 | ✅ 已实现 | 原生大纲 + 标题推断 |
| 书签管理 | ✅ 已实现 | 手动添加 + AI 建议 |
| 混合路由 | ✅ 已实现 | 本地/云端/回退三级策略 |
| 配置管理 | ✅ 已实现 | YAML + 热加载 + .env |
| 主题系统 | ✅ 已实现 | QPalette 三套主题 |
| 取词翻译 | ⏳ 未实现 | 预留接口 |
| 扫描版 OCR | ⏳ 未实现 | 预留接口 |
| 图表理解 | ⏳ 未实现 | 预留接口 |
| 笔记系统 | ⏳ 未实现 | 数据模型已定义 |
| 设置对话框（完整） | ⏳ 简化版 | 仅云端 API 配置可用 |
| AI 工具集侧边栏 | ⏳ 占位 | 显示占位文字 |

---

## 15. 附录：术语表数据

内置三个学科术语包，存储在 `data/glossary/`：

- **math.json**: 73 条（代数、拓扑、分析、几何）— 含流形、同胚、张量、层、概形等
- **cs_ml.json**: 70 条（深度学习、NLP、CV、RL）— 含注意力机制、Transformer、RAG 等
- **physics.json**: 45 条（量子力学、相对论、统计物理）— 含薛定谔方程、规范场论等

每条术语支持 `force` 标志（是否强制使用此翻译）和 `aliases`（英文别名）。

---

*本文档与 `src/` 下的实际代码同步。最后更新: 2026-05-03。*

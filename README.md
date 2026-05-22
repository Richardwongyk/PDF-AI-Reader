<p align="center">
  <img src="https://img.shields.io/badge/Python-3.14.4-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/PySide-6.11-green?logo=qt" alt="PySide6">
  <img src="https://img.shields.io/badge/MuPDF-latest-orange?logo=adobeacrobatreader" alt="PyMuPDF">
  <img src="https://img.shields.io/badge/license-MIT-purple" alt="License">
</p>

# PDF AI Reader

面向专业学术论文的 **AI 辅助阅读与实时翻译工具**。基于 PySide6 + PyMuPDF，集成 LLM 翻译、OCR 公式识别、语义知识库检索，四层架构支持高性能大 PDF 渲染。

> *"Attention Is All You Need" — 不，你还需要一个更好的 PDF 阅读器。*

---

## 功能特性

| 模块 | 功能 |
|------|------|
| **PDF 渲染** | PyMuPDF DisplayList 重放、瓦片化渲染、屏幕物理 DPI 1:1 映射、方向感知预渲染 (Sioyek) |
| **AI 翻译** | 段落级 LLM 翻译 (LiteLLM 云端/Ollama 本地)、SQLite 持久化缓存、流式 token 渲染、KaTeX 公式排版 |
| **公式识别** | Pix2Text MFD 视觉检测 + MFR LaTeX 识别、数学字体/Unicode 启发式检测 |
| **语义问答** | ChromaDB 向量存储 + BGE-M3 嵌入、知识库构建与语义检索、多轮对话 |
| **交互设计** | 段落双击翻译/右键提问/右键解释、裂缝式翻译框 (折叠/展开/拖拽)、双栏文本次序重排 |
| **缩放** | 0.3×–5.0× 无极缩放、Sioyek 即时反馈、SumatraPDF fixPt 视口保持 |

## 架构

```
src/
├── core/        领域层: AIEngine, PDF引擎, 向量嵌入, 公式识别, ServiceContainer(DI)
├── infra/       基础设施层: PageCache, AICache, TileCache, TileRenderer
├── app/         应用层协调器: TranslationFlow, DocumentFlow, ExplainFlow, AskQuestionFlow
├── ui/          表示层: MainWindow, PdfViewer, SplitWidget, BlockOverlay
├── data/        数据层: ConfigManager, ChromaRepo
└── main.py      入口 + 懒加载编排
```

**性能指标**：404 页 PDF 加载 0.06s (0 widgets 预创建)、滚动更新 7–33ms、缩放响应 21–280ms、活跃 Widget ≤ 15 个。

## 快速开始

### 环境要求

- Python 3.14.4（请使用 `run_py314.bat` 或 conda 环境 `pdf_ai_reader_314`）
- PySide6 ≥ 6.11
- PyMuPDF ≥ 1.25
- Ollama (可选，本地翻译/嵌入)

### 安装

```bash
git clone https://github.com/Richardwongyk/PDF-AI-Reader.git
cd PDF-AI-Reader
pip install -r requirements.txt
```

### 配置

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入云端 API Key (DeepSeek/OpenAI 等)
```

### 启动

```bash
python src/main.py
```

### 使用

| 操作 | 方式 |
|------|------|
| 打开 PDF | `Ctrl+O` 或工具栏 |
| 翻译段落 | 双击紫色高亮段落 |
| 提问/解释 | 右键段落 |
| 放大/缩小 | `Ctrl+=` / `Ctrl+-` 或菜单 |
| 折叠翻译框 | 双击翻译框 或 `Esc` |
| 书签跳转 | 点击左侧目录树 |

## 开源借鉴

本项目设计借鉴了以下开源 PDF 阅读器：

| 项目 | 借鉴内容 |
|------|---------|
| [SumatraPDF](https://github.com/sumatrapdf/sumatrapdf) | DisplayList 重放、零 Widget 架构、fixPt 缩放视口保持 |
| [qpageview](https://github.com/frescobaldi/qpageview) | 瓦片缓存 Key 设计、QPainter 绘制模式、ImageCache LRU |
| [Sioyek](https://github.com/ahrm/sioyek) | 方向感知预渲染、closest-zoom 即时反馈 |
| [PDFCrop](https://github.com/inoueakimitsu/pdfcrop) | ServiceContainer DI 容器、ErrorHandler 四级严重度、PageCache |
| [Mad Professor](https://github.com/0xRar/mad-professor-public) | 信号驱动四层协调器模式 |

## 许可证

MIT License

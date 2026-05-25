# PDF AI Reader

面向学术论文的 AI 辅助 PDF 阅读器。项目使用 PySide6 + PyMuPDF 构建桌面阅读体验，并在后台逐步构建翻译、全文检索、公式候选、RAG/GraphRAG 和可审计知识系统。

> 当前项目处于活跃研发阶段。基础阅读、翻译、知识库、公式多轮流水线和大量审计工具已经落地；born-digital PDF 公式的最终高精度自动还原、产品级 accepted 审核和完整 GraphRAG 体验仍在推进中。

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.14.4-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/PySide-6.11-green?logo=qt" alt="PySide6">
  <img src="https://img.shields.io/badge/PyMuPDF-1.27+-orange" alt="PyMuPDF">
  <img src="https://img.shields.io/badge/LLM-LiteLLM%20%7C%20Ollama-purple" alt="LLM">
</p>

## 项目目标

PDF AI Reader 的目标不是简单把 PDF 截图后 OCR 一遍，而是做一个能长期维护、可验证、可追溯的论文阅读工具：

- 快速打开和流畅阅读长 PDF。
- 双击段落翻译，保留公式和数学表达。
- 基于全文证据做问答，而不是脱离文档自由聊天。
- 对 born-digital PDF 优先利用文本层、glyph、font、bbox、vector 等结构事实解析公式。
- 图片、扫描、乱码或低置信区域才进入 OCR/MFR 兜底。
- 所有重任务异步分批、结果落库、二次打开按 input hash 跳过。

## 当前能力概览

| 模块 | 当前状态 |
| --- | --- |
| PDF 阅读 | PyMuPDF 渲染、目录跳转、滚动、缩放、段落 overlay、翻译框交互 |
| 翻译 | LiteLLM 云端/Ollama 本地路由、SQLite 缓存、段落级翻译、公式保护 |
| 问答/RAG | 文档解析、知识库构建、检索问答、GraphRAG artifact 研发中 |
| 公式 r0 | born-digital PDF 结构快扫，抽取 glyph/font/bbox/vector/image evidence |
| 公式 r0.5 | 符号身份修复 MVP，支持 glyph name 映射、font+CID 锚点传播、资源自动发现 |
| 公式 r1/r2 | 缓存优先 OCR/MFR 补救、本地多工具候选 worker，默认不覆盖正文 |
| 公式 r3 | DeepSeek/分析模型语义复核候选，只写 JSON，不自动接受 |
| 公式 r4/r5 | candidate-only 公式图谱、accepted 公式增量写回知识库 service |
| TinyBDMath | born-digital 非 OCR 小模型路线已建数据、训练、候选服务主干 |
| 测试工具 | Attention/Napkin 源码对照、公式质量审计、E2E 工作流、日志审计、性能基准 |

## 公式解析路线

项目当前主攻 born-digital PDF 的非 OCR 公式解析。核心原则：

- born-digital PDF 默认不走 OCR。
- OCR/MFR 只用于图片、扫描、无文本层、乱码、低置信或用户显式精扫。
- 低置信结果只能作为候选，不能覆盖正文或污染 RAG/GraphRAG。
- LaTeX 源码只用于训练、测试和验收；真实用户路径不能假设有源码。

多轮流水线：

| 轮次 | 名称 | 作用 |
| --- | --- | --- |
| r0 | `r0_pdf_structure` | 快速抽取 PDF 文本层、glyph、font、bbox、vector、图片和候选区域 |
| r0.5 | `r0_5_symbol_identity_repair` | 非视觉符号身份修复，补救缺失/断裂的 PDF 字符映射 |
| r1 | `r1_cached_recognition` | 对图片/扫描/needs_ocr 候选做缓存优先识别 |
| r2 | `r2_local_high_precision` | Pix2Text、Paddle、MinerU、PEK/UniMERNet 等本地工具候选复核 |
| r2a | `r2a_tinybdmath_structural` | TinyBDMath born-digital 结构模型候选 |
| r3 | `r3_cloud_semantic_review` | DeepSeek 等分析模型基于上下文和候选做语义校对建议 |
| r4 | `r4_knowledge_graph` | 将公式、章节、定理、引用、概念写入 GraphRAG artifact |
| r5 | `r5_knowledge_incremental_update` | accepted 结果变化后增量写回全文知识库 |

命令行流水线入口：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools\formula_multiround_pipeline.py --case attention --max-pages 6
```

## TinyBDMath 与训练数据

TinyBDMath 是项目中的 born-digital 公式结构小模型路线。它不是视觉 OCR 模型，而是读取 r0/r0.5 之后的 glyph graph，学习符号之间的二维数学结构关系，例如：

- baseline / 水平相邻
- 上标、下标、上下标组合
- 分数线、根号线、overline、large operator 上下限
- alignment / matrix / cases 的行列关系
- 数学字体和 glyph identity evidence

当前最可靠的训练/评测资产来自源码插桩重编译：

| 资料 | 结果 |
| --- | --- |
| Attention is All You Need | 138 / 138 verified exact rows |
| Napkin | 29743 / 29743 verified exact rows |

这些数据通过临时 LaTeX 副本给每个公式染唯一颜色，重编译后从 born-digital PDF 结构层读取精确 bbox。该路线用于训练和验收，不进入真实用户生产解析路径。

下一阶段重点：

1. 将插桩训练集转换为 TinyBDMath graph rows、manifest 和 train/validation/test split。
2. 训练 MLP edge/quality baseline，按 inline/display、上下标、分数线、根号、align、数学字体分项评测。
3. 接入 r2a candidate-only 服务，和 r0/r2b/r3/fusion 同台比较。
4. 通过 accepted/rejected/revision 门禁后，才允许进入 r5 知识库增量写回。

## 安装与运行

### 环境要求

- Windows 为主要开发/验证环境。
- Python 3.14.4。
- 推荐 conda 环境名：`pdf_ai_reader_314`。
- 外部重工具不要混装进主环境；MinerU、Paddle、Pix2Text、PEK 等应使用独立 worker 环境。

### 安装依赖

```powershell
git clone git@github.com:Richardwongyk/PDF-AI-Reader.git
cd PDF-AI-Reader

conda create -n pdf_ai_reader_314 python=3.14.4
conda activate pdf_ai_reader_314
python -m pip install -r requirements.txt
```

### 配置模型

复制配置文件：

```powershell
Copy-Item config.example.yaml config.yaml
```

然后在 `config.yaml` 中填写 DeepSeek/OpenAI 等 LiteLLM 兼容 API Key。默认示例配置中：

- 翻译、问答、总结使用云端路由。
- embedding 默认保留本地路线。
- Ollama 可作为可选本地后端。

### 启动应用

```powershell
python src\main.py
```

本机开发环境也可以使用：

```powershell
.\run_py314.bat
```

## 常用测试与审计命令

轻量测试：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest tests/test_formula_multiround_pipeline.py tests/test_formula_index_flow.py tests/test_formula_semantic_review.py tests/test_graph_index_flow.py tests/test_graph_index_store.py tests/test_formula_tool_comparison.py tests/test_external_formula_tools.py tests/test_smoke.py -q
```

公式多轮流水线：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools\formula_multiround_pipeline.py --case attention --max-pages 6 --r1-limit 4 --r2-limit 1 --r3-limit 2 --r4-limit 12
```

公式索引性能：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools\formula_index_performance.py --case all
```

LaTeX 源码对照审计：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools\formula_latex_audit.py --case attention --quality-gate
```

E2E 工作流：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -X utf8 tools\e2e_pdf_workflow.py --case all
```

## 项目结构

```text
src/
  app/       应用层流程：翻译、文档解析、公式索引、GraphRAG、知识库更新
  core/      核心能力：PDF 引擎、AI 路由、公式识别、glyph graph、TinyBDMath
  data/      配置、缓存、知识库和持久化数据接口
  infra/     渲染缓存、后台任务和基础设施
  ui/        PySide6 主窗口、PDF viewer、overlay、split widget
tools/       公式审计、训练集构建、多轮 pipeline、E2E、benchmark、worker
tests/       单元测试、流水线测试、GraphRAG/公式/外部工具测试
docs/        设计文档、交接文档、公式路线、RAG/GraphRAG 计划
```

关键文档：

- `AGENTS.md`：新会话和协作约束入口。
- `TODO.md`：当前状态和路线图。
- `docs/next_session_handoff.md`：详细交接。
- `docs/current_goal_and_next_steps.md`：当前目标和下一步。
- `docs/async_formula_indexing_design.md`：异步多轮公式索引设计。
- `docs/formula_multitool_fusion_design.md`：多工具候选融合和 accepted 门禁。
- `docs/tiny_born_digital_math_model_engineering.md`：TinyBDMath 小模型工程方案。
- `docs/formula_quality_acceptance_plan.md`：质量门禁与验收计划。

## 数据、缓存与版本控制边界

不要提交：

- `测试资料/`
- `test_artifacts/`
- `logs/`
- `config.yaml`
- 模型缓存、PDF、截图、临时 benchmark 结果

`.gitignore` 已覆盖这些路径。测试资料只用于本地验收，公开仓库不包含 Attention/Napkin PDF 或 LaTeX 源码。

## 开源借鉴

本项目参考了多类开源 PDF 阅读器和文档处理工具的工程思路：

| 项目 | 借鉴内容 |
| --- | --- |
| SumatraPDF | 快速 PDF 阅读体验、视口保持、轻量交互 |
| qpageview | 页面缓存和按需渲染思路 |
| Sioyek | 学术 PDF 阅读交互和跳转体验 |
| PyMuPDF | PDF 文本层、glyph、bbox、vector 等结构事实抽取 |
| LiteLLM / LlamaIndex / Chroma | LLM 路由、RAG 与向量检索组件 |

## 许可证

MIT License。详见 `LICENSE`。

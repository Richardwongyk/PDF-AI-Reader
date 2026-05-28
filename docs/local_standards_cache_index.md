# 本地标准与参考资料缓存索引

日期：2026-05-26

本文件记录已经下载到本机的 PDF、MathML、Unicode、OpenType、glyph mapping 和 LaTeX 参考资料。资料存放在 `.local_references/standards/`，该目录已写入 `.git/info/exclude`，只作为本地研发缓存，不提交到仓库。完整文件清单、来源 URL、大小和 SHA-256 在 `.local_references/standards/standards_manifest.json`。

这些资料的作用是约束 born-digital PDF 公式路线，避免继续凭记忆写规则或把小模型误当成全规则解析器。

2026-05-28 状态补充：这些本地资料仍只作为研发对照。当前 r2a、fusion、审核 UI 和 r5 写回已接线，
但产品化资源仍必须先做许可证、hash、版本和分发范围审计；不能把 `.local_references/` 或外部标准全文提交进仓库。

同日性能复盘补充：Napkin 极大缩放黑底/空白页与标准资料缓存无关，不能通过增加标准/映射资源解决。
该问题记录在 `TODO.md` 顶部，修复点在 PDF viewer 渲染 fallback。

## 1. 缓存边界

- 本地缓存只服务研发、调研和实现对照。
- 不把外部标准全文、CTAN PDF、Unicode 数据、W3C HTML 或 Adobe/TeX glyph lists 提交进版本库。
- ISO 32000-2 只缓存 PDF Association sponsored standards 访问页；实际 ISO bundle 可能需要 $0 cart/download 流程，不能绕过。
- 若后续要把 glyph mapping 数据随项目分发，必须逐项确认许可证并做资源裁剪；当前缓存不等于可再分发资源。

## 2. 已缓存资料

### PDF / ISO

- `pdf/PDF32000_2008.pdf`：Adobe 公开 PDF 1.7 / ISO 32000-1:2008 参考副本，用于确认 PDF content stream、font、text extraction、ToUnicode CMap、graphics state 等事实层边界。
- `pdf/pdfa_sponsored_standards.html`：PDF Association sponsored standards 访问页，用于后续获取 ISO 32000-2 的正规入口。

### MathML / XML entities

- `mathml/mathml-core.html`：W3C MathML Core，作为浏览器/核心 Presentation MathML 目标。
- `mathml/mathml3.html`：W3C MathML 3.0 2nd Edition，作为广义 Presentation/Content MathML 参考。
- `mathml/mathml4-editor-draft.html`：MathML 4 editor draft，用于未来结构覆盖规划。
- `entities/xml-entity-names.html` 与 `entities/unicode.xml`：W3C XML/MathML entity 到 Unicode 的映射依据。

### Unicode mathematics

- `unicode/utr25-unicode-math.html`：Unicode UTR #25，数学字符与数学类别参考。
- `unicode/uax44-ucd.html`：Unicode UAX #44，UCD 文件格式与属性语义。
- `unicode/ucd/*.txt`：UnicodeData、Blocks、Scripts、DerivedCoreProperties、StandardizedVariants、PropertyAliases、PropertyValueAliases。
- `unicode/math/MathClass-15.txt` 与 `unicode/math/MathClassEx-15.txt`：UTR #25 对应数学类别数据。revision 16 链接当前返回 404，因此本地缓存 revision 15 数据并在后续实现中记录数据版本。
- `unicode/UTN28-PlainTextMath-v3.3.pdf`：UnicodeMath/plain-text math 参考，只用于对照，不作为主解析目标。

### Glyph mapping

- `glyph/adobe/glyphlist.txt`：Adobe Glyph List，glyph name 到 Unicode 的基础映射。
- `glyph/adobe/aglfn.txt`：Adobe Glyph List For New Fonts。
- `glyph/adobe/zapfdingbats.txt`：Zapf Dingbats glyph mapping。
- `glyph/adobe/agl-specification.md`：AGL glyph name 派生 Unicode 规则。
- `glyph/adobe/LICENSE.md`：Adobe AGL/AGLFN license。
- `glyph/tex/texglyphlist.txt`：LCDF/TeX glyph name 到 Unicode 映射，用于 TeX 字体补丁层。

### OpenType / fonts

- `opentype/math-table.html`：OpenType MATH table，公式字体 metrics、glyph variants、italic correction 等参考。
- `opentype/cmap-table.html`：OpenType cmap table，字体 codepoint 到 glyph mapping。
- `opentype/font-file.html`：OpenType font file 结构。

### LaTeX / packages

- `latex/latex2e-reference.pdf`：LaTeX2e unofficial reference manual，用于源码扫描、环境和数学模式行为对照。
- `latex/amsmath-amsldoc.pdf`：amsmath 用户指南，覆盖 align、gather、split、cases 等核心数学环境。
- `latex/unicode-math.pdf`：unicode-math 包文档，用于 Unicode/OpenType math font 和数学字母语义对照。

## 3. 对架构的硬约束

1. PDF 事实层以 PDF 标准和 MuPDF/Poppler 等事实抽取能力为准，不手写“猜 LaTeX”的热路径。
2. r0.5 符号身份修复优先使用 ToUnicode、embedded font cmap、AGL、texglyphlist、TeX encoding、文档内锚点传播和 outline/path 候选；不能因为 ToUnicode 失败就直接转 OCR。
3. TinyBDMath 的结构目标应对齐 SLT / Presentation MathML，而不是自造一套不可验证的公式语法。
4. LaTeX/amsmath/unicode-math 文档用于训练数据生成和 verifier 设计；真实用户生产路径不能依赖源 LaTeX。
5. “涵盖所有公式规则”不能靠固定正则或规则表承诺。工程目标应拆成结构类别 coverage、模型置信、verifier、candidate-only 兜底和 accepted precision 统计。

## 4. 下一步资源接入要求

- 把 `glyph/adobe/glyphlist.txt`、`glyph/adobe/aglfn.txt`、`glyph/tex/texglyphlist.txt` 转成项目可加载的资源包前，先确认许可证和分发范围。
- `GlyphNameMappingLoader` 后续应支持显式传入本地缓存路径，并在 r0.5 summary 中写入 mapping file hash 和数据版本。
- r0.5 font cmap 接入应引用 OpenType cmap/MATH table 资料，并把 font file hash、table presence、mapping confidence 写入证据。
- TinyBDMath dataset manifest 必须记录 MathML/Unicode/glyph resource version，避免模型训练与推理资源版本漂移。
- verifier 设计必须引用 MathML/Unicode/LaTeX 资料来定义结构合法性，但 verifier 只能拒绝或降置信，不得伪装成手写公式解析器。

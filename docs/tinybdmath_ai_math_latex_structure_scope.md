# TinyBDMath AI/Math LaTeX 结构范围初稿

最后更新：2026-07-05

本文限定第一阶段目标：读懂数学/AI 领域 born-digital PDF 论文中的数学排版
结构。第一阶段不做通用科学记号系统，不覆盖化学结构图、物理实验图示、
TikZ 图示或任意宏语义；这些对象以后由路由系统分流。

关键结论：数学公式数量无限，不能枚举公式类型；但主流 LaTeX 数学排版
由有限的二维结构组合器递归生成。TinyBDMath 第一阶段要恢复这些组合器，
而不是识别某个具体公式模板。

## 1. 调研依据

主流 LaTeX 数学能力来自 LaTeX kernel、AMS-LaTeX/amsmath、mathtools、
unicode-math、常用符号/字体包和算法/图示包。对 PDF 公式恢复有意义的不是
宏名字本身，而是这些宏最终产生的排版结构。

参考资料：

- LaTeX2e 数学章节列出上下标、符号、箭头、黑板粗体、花体、定界符、点、
  希腊字母、数学函数、重音、上下结构、数学间距、样式、`\frac`、`\sqrt`、
  `\stackrel` 等基础结构：
  https://latexref.xyz/dev/latex2e.html
- amsmath 是 AMS-LaTeX 的 principal package，支持 displayed equations、
  align/gather/multline/split、矩阵、operator names、math text、boxed、
  roots、over/under arrows、fractions、delimiters、integrals/sums、limits
  placement、math fonts 等：
  https://www.ctan.org/pkg/amsmath
  https://www.latex-project.org/help/documentation/amsldoc.pdf
- mathtools 扩展 amsmath，包含 `mathclap`、`multlined`、扩展 cases、
  paired delimiters、left scripts/prescript、over/under bracket 等：
  https://tug.ctan.org/macros/latex/contrib/mathtools/mathtools.pdf
- unicode-math 支持 XeTeX/LuaTeX 下的 Unicode math 和 OpenType math fonts，
  说明第一阶段必须保存 mathvariant/font/style 证据：
  https://ctan.org/pkg/unicode-math
- algorithm2e、algorithmicx 是算法/伪代码环境，应作为结构化文本/代码块，
  不是普通公式 CSLT：
  https://tug.ctan.org/macros/latex/contrib/algorithm2e/doc/algorithm2e.pdf
  https://tug.ctan.org/macros/latex/contrib/algorithmicx/algorithmicx.pdf
- TikZ/tikz-cd 是数学图示/图形系统，应路由到图示解析或 unsupported，不进入
  第一阶段普通公式树：
  https://ctan.org/pkg/tikz-cd

PDF primitive graph 和评估依据：

- MathSeer/ICDAR 2021 使用 born-digital PDF 字符信息、精确 bbox、公式检测和
  QD-GGA 图解析恢复 Symbol Layout Tree。这证明 PDF primitive graph 是合理
  主线，不需要默认 OCR：
  https://cs.rit.edu/~rlaz/files/ICDAR2021_MathSeer_Pipeline.pdf
  https://shahayush.com/publications/2021-09-01-mathseer-pipeline
- QD-GGA 把公式输入建成 Line-of-Sight graph，并联合预测节点/边分布，再抽取
  SLT。第一阶段应学习结构图，不应自由生成 LaTeX：
  https://openaccess.thecvf.com/content_CVPRW_2020/papers/w34/Mahdavi_Visual_Parsing_With_Query-Driven_Global_Graph_Attention_QD-GGA_Preliminary_Results_CVPRW_2020_paper.pdf
- UniMERNet、PP-FormulaNet、Pix2Text、Nougat 等图像公式模型适合作为
  scanned/image formula 或低 PDF 证据候选，不进入 born-digital 热路径：
  https://github.com/opendatalab/UniMERNet
  https://arxiv.org/abs/2404.15254
  https://www.paddleocr.ai/v2.10.0/algorithm/formula_recognition/algorithm_rec_ppformulanet.html
  https://github.com/facebookresearch/nougat
- CDM/render-style 指标用于减少 LaTeX 字符串 exact 的误判；后续 verifier 不能
  只看 decoded exact/near：
  https://arxiv.org/abs/2409.03643
  https://openaccess.thecvf.com/content/CVPR2025/papers/Wang_Image_Over_Text_Transforming_Formula_Recognition_Evaluation_with_Character_Detection_CVPR_2025_paper.pdf
  https://github.com/opendatalab/OmniDocBench

## 2. 设计原则

1. 不枚举公式内容。
   - 不写 “attention formula”、“loss formula”、“matrix formula” 这类类型。
   - 样本清单只用于验收覆盖，不作为功能边界。

2. 枚举的是递归排版组合器。
   - symbol、sequence、script、fraction、fence、matrix 等是有限组合器。
   - 这些组合器可以无限嵌套，足以覆盖开放数学表达式。

3. 宏名不是 CSLT 节点类型。
   - `\DeclarePairedDelimiter{\norm}{\lVert}{\rVert}` 最终应是 fence + body。
   - `\operatorname{softmax}` 最终应是 operator/text_run。
   - 自定义宏只在 target parser/manifest 中记录，不能污染生产推理。

4. LaTeX 源码只用于训练和审计。
   - 生产解析只能使用 PDF evidence、模型 artifact 和缓存。
   - 输出目标是渲染/结构等价，不是作者源码字符串 exact。

5. 低证据对象必须路由或 abstain。
   - 扫描图像公式走 OCR/MFR 候选。
   - TikZ、proof tree、化学结构图、复杂图示先标记 route/unsupported。
   - r2a TinyBDMath 不把低置信候选写 accepted。

## 3. CSLT M0 必须支持的结构

M0 是 AI/Math 第一阶段最小可用数学排版结构，不代表代码当前已经全部实现。
本节要作为结构范围清单使用；后续实现可以分批，但不能在设计上漏掉这些类。

### 3.0 结构总表

必须覆盖的 LaTeX/AI-Math 结构族：

1. 数学 atom/class：
   ordinary symbol、large operator、binary operator、relation、opening delimiter、
   closing delimiter、punctuation、inner expression、accented atom、operator name。
2. 基础符号内容：
   Latin/Greek 字母、数字、标点、关系符、二元运算符、箭头、大算子、点、
   省略号、集合符号、逻辑符号、概率/统计符号、几何/线代符号、特殊 Unicode
   math symbols。
3. 数学字母表和样式：
   italic、roman、bold、bold italic、sans、typewriter、calligraphic、script、
   blackboard bold、fraktur、normal、upright Greek、bold symbol、Unicode math
   alphanumeric variants。
4. 水平结构：
   sequence、implicit multiplication、function application、operator spacing、
   punctuation spacing、relation alignment、math spacing commands。
5. 上下与左右附着：
   subscript、superscript、subsup、prescript、sideset、substack、multiline script、
   limits/nolimits/displaylimits、under/over attachments。
6. 分隔/堆叠：
   frac、dfrac、tfrac、genfrac、binom/dbinom/tbinom、atop/choose/brack/brace、
   continued fractions、stackrel/overset/underset。
7. 根号/包围：
   sqrt、nth-root index、boxed/fbox、cancel/strike/enclosure evidence。
8. 重音/annotation：
   hat、widehat、bar、overline、underline、tilde、widetilde、vec、dot、ddot、
   breve、check、acute、grave、mathring、overbrace、underbrace、overbracket、
   underbracket、xleftarrow/xrightarrow/extensible arrows。
9. 定界符/fence：
   fixed delimiters、sized delimiters、left/right、middle、missing delimiter、
   paired delimiters、absolute value、norm、angle brackets、floor/ceil、set braces。
10. 网格/数组/多行：
   matrix、pmatrix、bmatrix、Bmatrix、vmatrix、Vmatrix、smallmatrix、array、
   cases/dcases、aligned、alignedat、gathered、split、multline、align、alignat、
   flalign、gather、row/column alignment、cell、row break、column separator、
   hline/cline/border evidence。
11. 文本与 operator：
   text、mbox、operatorname、DeclareMathOperator 展开结果、named functions、
   AI 术语函数名、units/text inside math。
12. 版式控制和 artifact：
   displaystyle/textstyle/scriptstyle/scriptscriptstyle、mathchoice、phantom、
   hphantom/vphantom、smash、llap/rlap/clap/mathclap、raise/lower、kern、
   thin/medium/thick/negative space、quad/qquad。
13. 编号与引用：
   equation number、tag、notag/nonumber、subequations、label/ref/eqref anchor、
   page/bbox evidence。
14. 文档容器内公式：
   table cell、caption、footnote、theorem/definition/proof block、algorithm block、
   list item、appendix equation。
15. 路由对象：
   image formula、scanned formula、TikZ/tikz-cd、proof tree、commutative diagram、
   neural network diagram、plot/figure notation、chemical/mhchem、molecular graph。

### 3.1 基础节点

- `symbol`：单个 glyph 或已证据合并的数学符号。
- `text_run`：`\text{...}`、`\operatorname{...}`、单位、函数名、AI 术语
  如 `softmax`、`LayerNorm`。
- `sequence`：水平数学序列，是最基础的递归组合器。
- `group`：逻辑分组，不要求对应可见括号。
- `spacing_artifact`：空白、`\quad`、thin space、negative space、phantom、
  smash、mathclap 等 layout 影响项；默认不进语义正文，但进入 verifier。
- `atom_class`：ordinary、operator、binary、relation、open、close、punctuation、
  inner 等 TeX math atom class。它影响 spacing 和 limits placement，不等于语义。
- `line_break`：多行 display、aligned、matrix、cases 中的换行，不当作普通空格。
- `alignment_anchor`：`&` 对齐点和 relation alignment 证据。

### 3.2 附着结构

- `script`：右上标、右下标、上下同时存在、多级嵌套。
- `prescript`：左上/左下标，mathtools `\prescript` 和数学/物理张量记号需要。
- `under_over`：`\sum`、`\prod`、`\lim`、`\arg\max` 等大算子或 operator 的
  上下附着。
- `accent_annotation`：hat、bar、tilde、dot、ddot、vec、overline、underline、
  overbrace、underbrace、overbracket、underbracket、over/under arrow。
- `substack`：`\substack{...}`、多行 limits、分段条件中的小型多行结构。
- `sideset`：大算子左右侧附着，不强行改写成普通左右序列。
- `stacked_relation`：`\stackrel`、`\overset`、`\underset` 这类上下堆叠关系。

### 3.3 分隔与包围

- `fraction`：普通分数、binomial、atop-like stacking、continued fraction。
  分数横线是 separator evidence，不是 PDF 侧硬编码规则。
- `radical`：根号主体和可选 index；根号线是 evidence。
- `fence`：`\left...\right`、`\middle`、固定大小 delimiters、范数、绝对值、
  角括号、集合括号，允许 missing open/close delimiter。
- `enclosure`：boxed、fbox、cancel/strike 类对象暂作为 M1/M2 扩展；M0 至少
  不能把它们误写成普通 symbol。
- `delimiter_size`：big、Big、bigg、Bigg、left/right 自动伸缩。它是 layout
  证据，不能丢失到普通括号。
- `paired_delimiter`：mathtools 风格 paired delimiter 展开后保存 open/body/close。
- `separator_evidence`：横线、竖线、斜线、brace/bracket extension piece 等
  vector/glyph 证据。它只说明 layout 角色，不直接决定语义。

### 3.4 网格与多行

- `matrix_grid`：matrix、pmatrix、bmatrix、vmatrix、array、cases、dcases。
  包含 row、cell、alignment、row/column separator、可选 fence。
- `aligned_display`：aligned、gathered、split、align、alignat、flalign、
  multline。包含 equation row、alignment anchor、relation alignment。
- `equation_tag`：公式编号、tag、subequation 编号；不是公式主体，但用于
  引用、RAG 和证据定位。
- `array_column_spec`：l/c/r/p/竖线等列对齐和边界证据，只在训练/审计中保存，
  生产输出可降级为 alignment metadata。
- `row_group`：cases、piecewise、aligned rows、multi-equation displays 的行组。
- `cell_content`：每个 matrix/array/cases cell 内部仍是递归 CSLT。
- `display_container`：equation、equation*、align、gather、multline 等外层容器，
  保存编号、页码、bbox、上下文，不混进公式正文节点。

### 3.5 身份与样式

- `operator`：普通 operator symbol 和 named operator，包含
  `\DeclareMathOperator` 展开的 operator name。
- `style_variant`：display/text/script/scriptscript style。
- `mathvariant`：bold、italic、roman、sans、monospace、calligraphic、
  blackboard、fraktur、bold italic 等。
- `font_identity`：字体名、glyph name、ToUnicode、ActualText、OpenType MATH、
  font cmap 和统一资源证据，供 identity repair/verifier 使用。
- `symbol_identity_aliases`：训练/审计阶段来自 MathML/AST、AGL、TeX glyph list、
  Unicode Math、font cmap 的统一别名证据。禁止在 TinyBDMath alignment/decoder
  中维护本地样本表。
- `color_style`：颜色/高亮只作为 layout/style evidence，不进入公式语义树。
- `source_macro_metadata`：训练/审计阶段保留自定义宏展开来源、parser backend、
  warnings 和版本 hash；生产推理不依赖源码宏。

## 4. AI/Math 内容覆盖清单

本节列的是验收样本应覆盖的内容，不是公式类型规则。

### 4.1 线性代数和张量

- vectors、matrices、tensors、bold symbols、transpose、inverse、trace、rank。
- norms、inner products、outer products、Hadamard product、Kronecker product。
- block matrices、diagonal matrices、identity matrices、eigendecomposition、SVD。

### 4.2 概率、统计和信息论

- expectation、variance、covariance、probability、conditional probability。
- distributions、density/mass functions、KL、entropy、mutual information。
- argmax/argmin、MLE/MAP、sampling notation、random variables。

### 4.3 优化和微积分

- gradients、partials、Jacobians、Hessians、integrals、limits、sup/inf。
- loss/objective、constraints、Lagrangian、regularization、big-O/little-o。
- convergence notation、min/max with conditions、piecewise definitions。

### 4.4 深度学习和 AI 常见记号

- softmax、LayerNorm、Attention、MLP、ReLU/GELU 等 operator/text runs。
- `Q/K/V`、weights/biases、hidden states、tokens、positions、heads、layers。
- sequence indices、batch/time dimensions、masking、logits/probabilities。
- algorithms/pseudocode 中嵌入的公式片段。

### 4.5 数学论文常见环境

- theorem、lemma、definition、proposition、corollary、proof 中的行内/行间公式。
- numbered equations、subequations、references、appendix equations。
- tables/captions/footnotes/list items 中的公式。

### 4.6 必须路由或暂不支持的内容

- TikZ/tikz-cd/commutative diagrams/proof trees。
- neural network architecture diagrams、plots、figures 中的视觉符号。
- algorithm2e/algorithmicx 伪代码整体语义。
- chemical/mhchem、molecular graph、reaction mechanism。
- scanned/image-only formula：走 image_mfr/cloud_review 候选，不进 born-digital
  PDF graph 主路径。

## 5. M1/M2 扩展边界

M1 强化数学排版，但仍属于普通公式：

- extensible arrows。
- over/under braces/brackets 的更细粒度角色。
- substack/multiline scripts。
- paired delimiters 规范化。
- boxed/cancel/enclosure。
- render/CDM-style verifier。
- 更完整的 operator spacing 和 limits placement。

M2 或智能路由后处理：

- algorithm2e/algorithmicx 伪代码语义解析。
- TikZ/tikz-cd、proof tree、commutative diagrams。
- chemical equations/mhchem。
- chemical structure diagrams/molecular graph。
- 物理实验图示、流程图、网络结构图。

## 6. 与当前代码的关系

当前已提交 Graph Parser 结构监督链路和节点头/范围收口改动只能作为审计链路
和历史对照，不代表上述结构已经全部完成。2026-07-05 当前工作树另有用户确认
保留的未提交 Graph Parser M5 改动：whole-formula graph context、结构化关系
冲突筛选、默认批量 torch eval 和 fast decode 可跳过 layout verifier。
M5 仍是 candidate-only 实验路径，不改变 accepted gate。

重要边界：

- `HORIZONTAL_RULE`、`VERTICAL_RULE` 可以作为 PDF 侧低层事实节点标签。
- PDF node label 不能编码“这是分数线”“这是根号线”这类公式语义。
- 分数 separator、根号 mark、上下标注、重音线、围栏、矩阵行列、enclosure
  和 equation tag 等只能表示 target tree 派生的训练结构证据；文本串和算子
  文本串组边界同样只作为训练监督。当前使用
  `TARGET_TEXT_RUN_EVIDENCE`、`TARGET_OPERATOR_TEXT_RUN_EVIDENCE`、
  `TARGET_FRACTION_SEPARATOR_EVIDENCE`、`TARGET_RADICAL_MARK_EVIDENCE`、
  `TARGET_UNDER_OVER_EVIDENCE`、`TARGET_ACCENT_ANNOTATION_EVIDENCE`、
  `TARGET_FENCE_EVIDENCE`、`TARGET_MATRIX_GRID_EVIDENCE`、
  `TARGET_MATRIX_ROW_EVIDENCE`、`TARGET_MATRIX_CELL_EVIDENCE`、
  `TARGET_ENCLOSURE_EVIDENCE`、`TARGET_EQUATION_TAG_EVIDENCE` 等中性 role。
- decoder 不得从 node label 直接合成分数、根号、矩阵等结构。
- relation/group/parser 输出必须承担结构恢复责任，verifier 负责拒绝低置信。
- `decode_latex_candidate(..., verify_layout=False)` 仅用于快速评估和模型迭代；
  任何质量结论、r2a 默认 artifact 决策或 accepted gate 讨论都必须跑 full verifier。
- whole-formula graph context 是模型特征，不是后处理规则。它可以帮助关系头判断
  候选边在整式图中的相对位置和密度，但不能引入公式内容枚举。

## 7. 第一阶段验证实验

不要直接继续长训。先做 AI/Math LaTeX 结构覆盖审计：

1. 从 Attention、Napkin 和数学/AI 论文中抽 100 个公式候选。
2. 按结构组合器覆盖分类，而不是按公式名字分类：
   sequence、script、prescript、under_over、fraction、radical、accent、
   fence、matrix_grid、aligned_display、operator、text_run、style_variant、
   spacing_artifact、equation_tag。
3. 对每个样本记录：
   - PDF evidence 是否足够。
   - CSLT M0 是否能表达。
   - target parser 是否能生成目标树。
   - PDF-to-CSLT alignment 是否能给 hard/soft/ignore 标签。
   - verifier 是否能拒绝明显错误。
4. 输出分桶：
   - `ready_for_model`
   - `needs_identity_repair`
   - `needs_schema_extension`
   - `needs_image_mfr`
   - `route_unsupported`
   - `abstain`
5. 只有 `ready_for_model` 和标签质量达到门槛后，才训练或改 decoder。

当前审计入口：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe tools\tinybdmath_audit_structure_scope.py `
  --graph-rows test_artifacts\tinybdmath_graph_unique_color_components_v3_20260601\tinybdmath_graph_rows.jsonl `
  --output test_artifacts\tinybdmath_structure_scope_audit_100.json `
  --limit 100
```

如果已经生成 target tree 和 alignment，可显式传入 `--target-trees` 和
`--alignment-rows`；否则工具会在训练/审计上下文中临时生成，不进入生产推理。

2026-06-03 已跑首个 100 行结构覆盖审计，并移除了按 LaTeX 字符串模式
推断 schema 缺口的口径；当前分桶只依据 parser summary、target tree warnings、
PDF-to-CSLT alignment、PDF glyph evidence 和显式 route metadata：

- 输出：`test_artifacts/tinybdmath_structure_scope_audit_100.json`，不提交。
- 分桶：`ready_for_model=84`、`needs_identity_repair=15`、
  `needs_schema_extension=1`。
- target tree：100 行中 99 行成功，1 行 KaTeX 解析失败。
- alignment：avg_hard_alignment_rate=0.951277，rows_with_hard_labels=76，
  rows_with_structure_labels=16。
- 审计报告记录 `parser_type_counts`、`mathml_tag_counts`、`blocker_counts`
  和 `blocked_rows`，用于定位下一步该补 parser/container、identity repair
  还是 PDF-to-target alignment。
- 已覆盖：`symbol`、`group`、`sequence`、`script`、`text_run`、
  `font_identity`、`mathvariant`、`fraction`、`radical`、`operator`、
  `matrix_grid`、`aligned_display`、`under_over`。
- 已补标准 KaTeX AST 结构：`cr` 行断点、large operator/operatorname
  limits、operatorname、overline/underline/horizBrace、boxed/fbox、left/right/
  middle/sized delimiters、equation tag、phantom/smash/lap/color layout/style
  evidence。未知命令产生的 KaTeX error color 节点会进入 schema blocker，
  不进入 ready 训练样本。
- 已补标准结构训练通道：Graph Parser relation labels 接住 `UNDER`、`OVER`、
  `ACCENT_BASE`、`TEXT_RUN_NEXT`、`OVERLINE`、`UNDERLINE`，并从 target-derived
  structure labels 生成文本串组边界、根号主体、围栏 open/body/close、矩阵行/
  单元格内容和 over/under line 的通用关系监督；简易 decoder 只序列化已支持关系，
  对暂未支持的 fence/matrix 等关系给 warning 和低置信。
- `fraction`、`radical`、`matrix_grid`、`aligned_display` 已出现，但本批没有进入
  `ready_for_model` 分桶；后续仍要先看 identity/alignment 失败，而不是改
  decoder。剩余 1 个 `needs_schema_extension` 是未包裹 display alignment 的
  KaTeX 解析失败，不允许在 decoder 里用输出字符串替换解决。
- 新增 `tests/test_tinybdmath_no_hardcoded_patterns.py`：扫描 TinyBDMath 主线
  和相关测试，并用 synthetic 反例确认可拦截宏注入、正则解析、LaTeX 命令
  分支和样本术语硬编码。

2026-07-05 当前验证补充：

- 新增批注/TOC/运行时检查：27 passed。
- 合并回归 + M5 定向组合：94 passed。
- 轻量接手测试：95 passed。
- M5 定向测试：
  `tests/test_tinybdmath_graph_parser.py tests/test_tinybdmath_eval_decoded_latex.py -q`
  为 32 passed。
- TinyBDMath 主线测试组合为 159 passed。
- 下一步结构实验报告必须同时列 fast decode 与 `--full-verifier` 指标，防止
  未跑 layout verifier 的 M5 快速路径掩盖 bbox/layout blocker。

## 8. 路由字段初稿

第一阶段先记录路由证据，不构建复杂智能路由：

- `domain`: `math_ai`、`unknown`、`possible_chemistry`、`diagram`
- `document_kind`: `born_digital`、`scanned`、`mixed`
- `candidate_kind`: `inline_math`、`display_math`、`image_formula`、
  `table_formula`、`algorithm_block`、`diagram`
- `evidence_quality`: `high`、`medium`、`low`
- `recommended_route`: `pdf_graph`、`image_mfr`、`cloud_review`、
  `unsupported`
- `accepted_policy`: `candidate_only`、`needs_review`、`abstain`

这些字段要写进 evidence JSON/manifest，而不是用隐式 if-else 决定后端。

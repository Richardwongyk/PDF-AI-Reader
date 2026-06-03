# 当前 TODO

最后更新：2026-06-02

本文件只记录当前要做的事。历史执行记录从 git、提交说明和旧测试产物中查，
不再写成长篇演化史。

## 入口

新会话按这个顺序读：

1. `AGENTS.md`
2. `docs/next_session_handoff.md`
3. `docs/current_goal_and_next_steps.md`
4. `docs/tiny_born_digital_math_model_engineering.md`
5. `docs/tinybdmath_ai_math_latex_structure_scope.md`
6. `docs/workspace_inventory.md`

## 当前主线

第一阶段目标是读懂 AI/Math 论文里的 born-digital 数学内容。公式恢复不靠
枚举公式类型，也不靠 OCR 处理可复制 PDF；主线是：

```text
PDF facts -> graph rows -> CSLT target tree -> PDF/CSLT alignment
          -> Graph Parser -> candidate-only LaTeX -> verifier/accepted gate
```

当前保留的 TinyBDMath 代码入口只服务这条线：插桩 graph rows、CSLT target
tree、alignment、Graph Parser、candidate-only r2a、LaTeX candidate decode 和
公式级评估。历史实验入口不再保留在当前文档和推荐命令里。

## 近期任务

1. 做 100 个 AI/Math 公式的结构覆盖审计。
   输出分桶：`ready_for_model`、`needs_identity_repair`、
   `needs_schema_extension`、`needs_image_mfr`、`route_unsupported`、`abstain`。

2. 按覆盖审计补 CSLT M0。
   重点结构：sequence、script、under/over、fraction、radical、accent、fence、
   matrix/aligned、operator、text run、style/mathvariant、equation tag。

3. 改 alignment 标签，不写局部符号表。
   alignment 只用 PDF facts、target tree、统一身份证据和 bbox/order；失败样例进
   identity repair、schema extension 或 abstain。

4. 训练 Graph Parser。
   2026-06-03 已完成当前结构标签的 2000 行 smoke：decoded exact=0.569500，
   near=0.710500，average_similarity=0.866203；后续先处理 TEXT precision、
   decoder warnings 和 verifier 阈值，再全量训练。报告必须同时列出节点头、
   关系头、公式级 exact/near、abstain rate 和失败分桶。

5. 做 constrained decoder + layout verifier。
   2026-06-03 已接入 layout verifier M0：同一 2000 行候选 gate 后
   pass=557、review=935、abstain=508，未拒绝子集 exact=0.726542、
   near=0.819035。2026-06-03 又新增 constrained decode M0，只验证模型结构图
   schema/缺失节点/cycle/coverage/blockers，不补 LaTeX；接入后 gate 为
   pass=557、review=859、abstain=584，未拒绝子集 exact=0.748588、
   near=0.831215。后续继续做 n-best、TEXT precision 和 verifier 校准。
   2026-06-03 已接入 n-best CSLT / LaTeX candidate evidence：Graph Parser
   relation alternatives 透传到 constrained decode；成环结构会额外给出按模型
   置信度保留的无环投影候选，但 rank-1 永远保持原始 selected graph。814 行
   重生成候选审计显示 n-best oracle near=0.787469，rank-1 near=0.687961；
   这是候选覆盖审计，不是自动 accepted。
   接续会话已修复重复 LaTeX 的替代结构证据合并：更干净的同输出结构进入
   `alternative_structure_evidence`，selected graph 不作为自身替代证据重复写入。
   2026-06-03 已继续补通用结构序列化：fence、text run、matrix row/cell、
   accent base 关系进入 decoder/verifier；TEXT_RUN 只在 node head 高置信
   `TEXT`/`OPERATOR` 证据下包 `\text{...}`。814 行 gated-text 审计：
   rank-1 exact=0.531941、near=0.680590，n-best oracle near=0.766585。
   接续又按 M0/M1 结构清单补齐了左附着/`prescript`、`radical_index`
   nth-root、operator text run 和 `matrix_grid` row/cell/content 三层语义；
   失败分桶只作为验收归因，不作为计划来源。focused 结构测试 82 passed，
   TinyBDMath 主线 129 passed。
   decoder 只序列化模型已给出的结构；结构缺失、冲突、低置信时拒绝或
   candidate-only，不在后处理中补模板。

6. 接入 r2a 验证。
   `TinyBDMathCandidateService` 只加载 Graph Parser artifact；缺模型就 abstain。
   结果不得写正文、FTS、向量库或 GraphRAG，只有 accepted/manual revision 可进知识库。

7. 保持阅读体验红线。
   后续若回到 UI 性能，先修 Napkin 极大缩放/快速滚动时中间页首帧空白问题；
   不允许用看不见页面换性能指标。

## 验证命令

轻量接手测试：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest `
  tests/test_external_formula_tools.py `
  tests/test_formula_index_flow.py `
  tests/test_born_digital_math.py `
  tests/test_formula_semantic_review.py `
  tests/test_smoke.py -q
```

TinyBDMath 当前主线测试：

```powershell
C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe -m pytest `
  tests/test_tinybdmath_symbol_equivalence.py `
  tests/test_tinybdmath_cslt_schema.py `
  tests/test_tinybdmath_target_tree.py `
  tests/test_tinybdmath_alignment.py `
  tests/test_tinybdmath_alignment_audit.py `
  tests/test_tinybdmath_graph_parser.py `
  tests/test_tinybdmath_latex_decoder.py `
  tests/test_tinybdmath_eval_decoded_latex.py `
  tests/test_tinybdmath_candidate_service.py `
  tests/test_formula_multiround_pipeline.py `
  tests/test_full_software_validation.py -q
```

## 禁止事项

- 不提交 `测试资料/`、`logs/`、`test_artifacts/`、缓存、模型权重或本地配置。
- 不把 OCR/MFR 当成 born-digital PDF 的默认公式路线。
- 不在 decoder/alignment/后处理中写样本特化规则、固定论文词表或局部符号表。
- 不把 candidate-only 公式写入正文、RAG 或 GraphRAG。
- 不把历史实验入口重新接回主线。

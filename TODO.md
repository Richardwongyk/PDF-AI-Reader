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
   先 2000 行 smoke，再全量；报告必须同时列出节点头、关系头、公式级 exact/near、
   abstain rate 和失败分桶。

5. 做 constrained decoder + layout verifier。
   decoder 只序列化模型已给出的结构；结构缺失、冲突、低置信时拒绝或 candidate-only，
   不在后处理中补模板。

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

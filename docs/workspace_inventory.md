# Workspace Inventory

Last updated: 2026-06-02.

This file records the practical workspace layout. It is about local cleanup and
handoff, not product design.

## Do Not Delete Casually

- `.tool_models/`: local external-tool model cache, controlled by
  `PDF_AI_READER_TOOL_MODELS_DIR`. Rebuilding this is expensive.
- `.local_references/`: local standards and reference documents. Do not commit
  or delete unless explicitly refreshing the reference cache.
- `测试资料/`: user-provided PDFs and LaTeX sources. Never delete or commit.
- `开源借鉴/`: local source checkouts used for implementation reference.
- `config.yaml`: local API/config secrets, ignored by git.

## Regenerable Runtime Data

- `logs/`: runtime and benchmark logs. Safe to clear after useful findings are
  written into docs.
- `data/knowledge_bases/` and `data/knowledge_bases_fts/`: Chroma/FTS indexes.
  Safe to delete when reclaiming space; the app will rebuild them, but the next
  open/query will be slower.
- `data/*cache*.db` and `data/*jobs*.db`: local caches/job stores. Usually keep
  while debugging skip/resume behavior; otherwise regenerable.
- `.cache/`, `.pytest_cache/`, `__pycache__/`: local Python/test cache.

## Current Large Test Artifacts

Kept after the 2026-06-02 cleanup:

- `test_artifacts/tinybdmath_graph_unique_color_components_v3_20260601`
  contains full graph rows for Attention + Napkin instrumented formulas.
- `test_artifacts/instrumented_full_unique_color_components_v3_20260601`
  contains final instrumented JSONL training/evidence rows. Its temporary
  `work/` directory was removed.
- `test_artifacts/tinybdmath_structure_scope_audit_100.json` records the first
  100-row AI/Math CSLT structure coverage audit. It is a regenerable audit
  output and must not be committed.

`.pytest_cache` currently has unreadable permissions and near-zero size, so it
was left in place instead of force-changing ACLs.

## Current TinyBDMath Entrypoints

Use these for new work:

- `tools/run_instrumented_dataset_background.ps1`
- `tools/run_tinybdmath_instrumented_graph_dataset.ps1`
- `tools/tinybdmath_build_target_trees.py`
- `tools/tinybdmath_align_targets.py`
- `tools/tinybdmath_audit_alignment.py`
- `tools/tinybdmath_audit_structure_scope.py`
- `tools/tinybdmath_train_graph_parser.py`
- `tools/tinybdmath_eval_decoded_latex.py`
- `tools/formula_multiround_pipeline.py --run-tinybdmath --tinybdmath-graph-parser-model <model>`

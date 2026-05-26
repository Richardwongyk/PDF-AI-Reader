param(
  [string]$AttentionRows = "test_artifacts\instrumented_attention_fast_delivery\instrumented_training_rows.jsonl",
  [string]$NapkinRows = "test_artifacts\instrumented_napkin_fast_delivery_v3\instrumented_training_rows.jsonl",
  [string]$OutputDir = "test_artifacts\tinybdmath_instrumented_graph_v1",
  [string]$Python = "C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe"
)

$ErrorActionPreference = "Stop"
$inputs = @()
if (Test-Path $AttentionRows) { $inputs += @("--input", $AttentionRows) }
if (Test-Path $NapkinRows) { $inputs += @("--input", $NapkinRows) }
if ($inputs.Count -eq 0) {
  throw "No instrumented_training_rows.jsonl inputs found. Pass -AttentionRows/-NapkinRows or build instrumented datasets first."
}

& $Python tools\tinybdmath_instrumented_graph_dataset.py @inputs --output-dir $OutputDir

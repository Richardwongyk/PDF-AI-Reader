param(
  [ValidateSet("attention", "napkin", "all")]
  [string]$Case = "all",
  [string]$OutputDir = "test_artifacts\tinybdmath_sharded_full_page_anchor_v2",
  [int]$Workers = 3,
  [int]$Epochs = 80,
  [int]$HiddenUnits = 24,
  [double]$MinSimilarity = 0.92,
  [switch]$SkipBuild,
  [switch]$TrainTorchScience
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe"
if (-not (Test-Path $python)) {
  throw "main python not found: $python"
}

Push-Location $repo
try {
  if (-not $SkipBuild) {
    & $python tools\tinybdmath_sharded_dataset.py `
      --case $Case `
      --workers $Workers `
      --output-dir $OutputDir
  }

  & $python tools\tinybdmath_shard_consolidate.py --output-dir $OutputDir

  $rows = Join-Path $OutputDir "training\tinybdmath_rows.jsonl"
  $modelDir = Join-Path $OutputDir "model"
  & $python tools\tinybdmath_train_baseline.py `
    --rows $rows `
    --output-dir $modelDir `
    --epochs $Epochs `
    --hidden-units $HiddenUnits `
    --min-similarity $MinSimilarity `
    --include-quality "strong_alignment,near_alignment"

  if ($TrainTorchScience) {
    conda run -n science python tools\tinybdmath_train_torch.py `
      --rows $rows `
      --output-dir (Join-Path $OutputDir "torch_model") `
      --epochs 40 `
      --hidden-units 64 `
      --min-similarity $MinSimilarity `
      --include-quality "strong_alignment,near_alignment" `
      --device cpu
  }
}
finally {
  Pop-Location
}

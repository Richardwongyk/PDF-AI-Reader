param(
  [string]$Rows = "test_artifacts\tinybdmath_realdata\training\tinybdmath_rows.jsonl",
  [string]$OutputDir = "test_artifacts\tinybdmath_realdata\torch_model",
  [int]$Epochs = 40,
  [int]$HiddenUnits = 64,
  [int]$BatchSize = 128
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$conda = Get-Command conda -ErrorAction SilentlyContinue
if (-not $conda) {
  throw "conda not found in PATH"
}

Push-Location $repo
try {
  conda run -n science python tools\tinybdmath_train_torch.py `
    --rows $Rows `
    --output-dir $OutputDir `
    --epochs $Epochs `
    --hidden-units $HiddenUnits `
    --batch-size $BatchSize `
    --device cpu
}
finally {
  Pop-Location
}

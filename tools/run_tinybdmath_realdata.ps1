param(
  [ValidateSet("attention", "napkin", "all")]
  [string]$Case = "all",
  [string]$OutputDir = "test_artifacts\tinybdmath_realdata",
  [int]$MaxPages = 0,
  [int]$Epochs = 120,
  [int]$HiddenUnits = 24,
  [switch]$SkipTrain
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe"
if (-not (Test-Path $python)) {
  throw "main python not found: $python"
}

Push-Location $repo
try {
  $args = @(
    "tools\tinybdmath_realdata_pipeline.py",
    "--case", $Case,
    "--output-dir", $OutputDir,
    "--max-pages", "$MaxPages",
    "--epochs", "$Epochs",
    "--hidden-units", "$HiddenUnits"
  )
  if ($SkipTrain) {
    $args += "--skip-train"
  }
  & $python @args
}
finally {
  Pop-Location
}

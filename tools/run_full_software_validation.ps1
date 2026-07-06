param(
  [ValidateSet("quick", "standard", "full", "nightly")]
  [string]$Profile = "standard",
  [ValidateSet("attention", "napkin", "all")]
  [string]$Case = "all",
  [string]$Python = "C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe",
  [string]$OutputDir = "",
  [int]$MaxPages = 0,
  [switch]$DryRun,
  [switch]$FailFast,
  [switch]$IncludeDesktopE2E,
  [switch]$IncludeCloud,
  [switch]$StrictLogs,
  [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $Python)) {
  throw "python not found: $Python"
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $OutputDir = "test_artifacts\full_software_validation_${Profile}_${stamp}"
}

$argsList = @(
  "tools\full_software_validation.py",
  "--profile", $Profile,
  "--case", $Case,
  "--output-dir", $OutputDir
)
if ($MaxPages -gt 0) { $argsList += @("--max-pages", "$MaxPages") }
if ($DryRun) { $argsList += "--dry-run" }
if ($FailFast) { $argsList += "--fail-fast" }
if ($IncludeDesktopE2E) { $argsList += "--include-desktop-e2e" }
if ($IncludeCloud) { $argsList += "--include-cloud" }
if ($StrictLogs) { $argsList += "--strict-logs" }

Push-Location $repo
try {
  New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
  if ($Foreground) {
    & $Python @argsList
    exit $LASTEXITCODE
  }
  $stdout = Join-Path $OutputDir "full_validation.stdout.log"
  $stderr = Join-Path $OutputDir "full_validation.stderr.log"
  $process = Start-Process `
    -FilePath $Python `
    -ArgumentList $argsList `
    -WorkingDirectory $repo `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr `
    -WindowStyle Hidden `
    -PassThru
  Write-Output ([PSCustomObject]@{
    pid = $process.Id
    profile = $Profile
    case = $Case
    output_dir = $OutputDir
    stdout = $stdout
    stderr = $stderr
    report = (Join-Path $OutputDir "full_software_validation_report.json")
  } | ConvertTo-Json -Depth 8)
}
finally {
  Pop-Location
}

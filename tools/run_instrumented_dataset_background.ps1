param(
  [Parameter(Mandatory=$true)][string]$Case,
  [Parameter(Mandatory=$true)][string]$OutputDir,
  [int]$Limit = 0,
  [string]$Pdf = "",
  [string]$LatexRoot = "",
  [ValidateSet("full", "fast-no-asy")][string]$BuildProfile = "full",
  [ValidateSet("latexmk", "pdflatex-once")][string]$CompileMode = "latexmk",
  [switch]$KeepWorkDir
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe"
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$safeCase = $Case -replace '[^A-Za-z0-9_.-]', '_'
$logPath = Join-Path $LogDir "instrumented_dataset_${safeCase}_${stamp}.log"

$toolArgs = @("tools\tinybdmath_instrumented_latex_dataset.py", "--case", $Case, "--output-dir", $OutputDir)
$toolArgs += @("--build-profile", $BuildProfile)
$toolArgs += @("--compile-mode", $CompileMode)
if ($Limit -gt 0) {
  $toolArgs += @("--limit", [string]$Limit)
}
if ($Pdf) {
  $toolArgs += @("--pdf", $Pdf)
}
if ($LatexRoot) {
  $toolArgs += @("--latex-root", $LatexRoot)
}
if ($KeepWorkDir) {
  $toolArgs += "--keep-work-dir"
}

$launch = [pscustomobject]@{
  started_at = (Get-Date).ToString("s")
  case = $Case
  output_dir = $OutputDir
  build_profile = $BuildProfile
  compile_mode = $CompileMode
  limit = $Limit
  tool_args = $toolArgs
}
$launch | ConvertTo-Json -Depth 5 | Set-Content -Path $logPath -Encoding UTF8

$encodedArgs = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($toolArgs | ConvertTo-Json -Compress)))
$script = @"
`$ErrorActionPreference = 'Stop'
Set-Location '$Root'
`$toolArgs = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('$encodedArgs')) | ConvertFrom-Json
& '$Python' -u @toolArgs *>&1 | ForEach-Object {
  `[string]`$line = `$_
  Add-Content -Path '$logPath' -Value `$line -Encoding UTF8
}
`$exitCode = `$LASTEXITCODE
"LASTEXITCODE=`$exitCode" | Add-Content -Path '$logPath' -Encoding UTF8
exit `$exitCode
"@
$encodedScript = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
$process = Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoLogo", "-NoProfile", "-EncodedCommand", $encodedScript) -WindowStyle Hidden -PassThru

[pscustomobject]@{
  process_id = $process.Id
  case = $Case
  output_dir = $OutputDir
  build_profile = $BuildProfile
  compile_mode = $CompileMode
  log = $logPath
} | ConvertTo-Json -Depth 3

param(
    [string]$Python = "C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe",
    [string]$GraphRows = "test_artifacts\tinybdmath_instrumented_graph_v1\tinybdmath_graph_rows.jsonl",
    [string]$OutputDir = "test_artifacts\tinybdmath_relation_pipeline_smoke",
    [int]$Limit = 2000,
    [int]$Epochs = 3,
    [double]$MinConfidence = 0.70,
    [switch]$UseTorchEdge,
    [switch]$CalibrateTorchEdge,
    [string]$TorchPython = "C:\Users\WYK\.conda\envs\science\python.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$out = New-Item -ItemType Directory -Force -Path $OutputDir
$labels = Join-Path $out.FullName "relation_labels"
$edgeModel = Join-Path $out.FullName "edge_model"
$scores = Join-Path $out.FullName "relation_scores"
$structural = Join-Path $out.FullName "structural_candidates"
$evalReport = Join-Path $out.FullName "structural_eval_report.json"
$mathml = Join-Path $out.FullName "mathml"
$summary = Join-Path $out.FullName "relation_pipeline_summary.json"

Write-Host "[TinyBDMath] relation pipeline start"
& $Python tools\tinybdmath_extract_mathml.py --rows $GraphRows --output-dir $mathml --limit $Limit
& $Python tools\tinybdmath_build_relation_labels.py --rows $GraphRows --output-dir $labels --limit $Limit --mathml-rows (Join-Path $mathml "latex_mathml_rows.jsonl")
if ($UseTorchEdge) {
    $torchArgs = @(
        "tools\tinybdmath_train_edge_torch.py",
        "--graph-rows", $GraphRows,
        "--relation-labels", (Join-Path $labels "tinybdmath_relation_label_rows.jsonl"),
        "--output-dir", $edgeModel,
        "--limit", $Limit,
        "--epochs", $Epochs
    )
    if ($CalibrateTorchEdge) {
        $torchArgs += "--calibrate-logit-scale"
    }
    & $TorchPython @torchArgs
} else {
    & $Python tools\tinybdmath_train_edge_baseline.py --graph-rows $GraphRows --relation-labels (Join-Path $labels "tinybdmath_relation_label_rows.jsonl") --output-dir $edgeModel --limit $Limit --epochs $Epochs
}
& $Python tools\tinybdmath_score_relations.py --rows $GraphRows --model (Join-Path $edgeModel "tinybdmath_edge_baseline_model.json") --output-dir $scores --limit $Limit --min-confidence 0.55 --stream
& $Python tools\tinybdmath_decode_structural_candidates.py --scores (Join-Path $scores "tinybdmath_relation_scores.jsonl") --output-dir $structural --limit $Limit --min-confidence $MinConfidence
& $Python tools\tinybdmath_eval_structural_candidates.py --candidates (Join-Path $structural "tinybdmath_structural_candidates.jsonl") --relation-labels (Join-Path $labels "tinybdmath_relation_label_rows.jsonl") --output $evalReport --limit $Limit

function Read-JsonObject([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    return [PSCustomObject]@{}
}

function Read-FirstJsonObject([string[]]$Paths) {
    foreach ($path in $Paths) {
        if (Test-Path -LiteralPath $path) {
            return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        }
    }
    return [PSCustomObject]@{}
}

$payload = [PSCustomObject]@{
    schema_version = "tinybdmath_relation_pipeline_summary_v1"
    limit = $Limit
    epochs = $Epochs
    min_confidence = $MinConfidence
    edge_training = $(if ($UseTorchEdge) { "torch" } else { "baseline" })
    edge_calibration = $(if ($CalibrateTorchEdge) { "logit_scale" } else { "" })
    mathml = Read-JsonObject (Join-Path $mathml "latex_mathml_manifest.json")
    relation_labels = Read-JsonObject (Join-Path $labels "tinybdmath_relation_label_manifest.json")
    edge_model = Read-FirstJsonObject @(
        (Join-Path $edgeModel "tinybdmath_edge_torch_report.json"),
        (Join-Path $edgeModel "tinybdmath_edge_baseline_report.json")
    )
    relation_scores = Read-JsonObject (Join-Path $scores "tinybdmath_relation_score_manifest.json")
    structural_candidates = Read-JsonObject (Join-Path $structural "tinybdmath_structural_candidate_manifest.json")
    structural_eval = Read-JsonObject $evalReport
    candidate_only = $true
    accepted_latex_emitted = $false
}
$payload | ConvertTo-Json -Depth 80 | Set-Content -LiteralPath $summary -Encoding UTF8
[PSCustomObject]@{
    schema_version = $payload.schema_version
    rows = $payload.structural_eval.rows
    micro = $payload.structural_eval.micro
    mathml_warnings = $payload.mathml.warnings
    accepted_latex_emitted = $false
} | ConvertTo-Json -Depth 20
Write-Host "[TinyBDMath] relation pipeline done: $OutputDir"

param(
    [string]$Python = "C:\Users\WYK\.conda\envs\pdf_ai_reader_314\python.exe",
    [string]$GraphRows = "test_artifacts\tinybdmath_instrumented_graph_v1\tinybdmath_graph_rows.jsonl",
    [string]$OutputDir = "test_artifacts\tinybdmath_relation_pipeline_smoke",
    [int]$Limit = 2000,
    [int]$Epochs = 3,
    [double]$MinConfidence = 0.70,
    [switch]$UseTorchEdge,
    [switch]$CalibrateTorchEdge,
    [ValidateSet("candidate_relation_f1", "accepted_precision")]
    [string]$TorchCalibrationObjective = "candidate_relation_f1",
    [double]$TorchCandidateThreshold = 0.70,
    [string]$TorchPython = "C:\Users\WYK\.conda\envs\science\python.exe",
    [switch]$FastTorchScoring,
    [switch]$CompactScores,
    [int]$ScoreBatchRows = 2048,
    [string]$ScoreTorchDevice = "cpu",
    [switch]$StreamStructuralDecode,
    [switch]$DirectStructuralDecode,
    [switch]$NoScoreJsonl,
    [switch]$StreamStructuralEval,
    [switch]$StreamDecodedLatexEval
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
$decodedEvalReport = Join-Path $out.FullName "decoded_latex_eval_report.json"
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
        $torchArgs += "--calibration-objective"
        $torchArgs += $TorchCalibrationObjective
        $torchArgs += "--candidate-threshold"
        $torchArgs += "$TorchCandidateThreshold"
    }
    & $TorchPython @torchArgs
} else {
    & $Python tools\tinybdmath_train_edge_baseline.py --graph-rows $GraphRows --relation-labels (Join-Path $labels "tinybdmath_relation_label_rows.jsonl") --output-dir $edgeModel --limit $Limit --epochs $Epochs
}
$scoreArgs = @(
    "tools\tinybdmath_score_relations.py",
    "--rows", $GraphRows,
    "--model", (Join-Path $edgeModel "tinybdmath_edge_baseline_model.json"),
    "--output-dir", $scores,
    "--limit", $Limit,
    "--min-confidence", "0.55",
    "--stream"
)
if ($FastTorchScoring) {
    $scoreArgs += "--fast-torch"
    $scoreArgs += "--batch-rows"
    $scoreArgs += "$ScoreBatchRows"
    $scoreArgs += "--torch-device"
    $scoreArgs += $ScoreTorchDevice
}
if ($CompactScores) {
    $scoreArgs += "--compact-output"
}
if ($DirectStructuralDecode) {
    $scoreArgs += "--direct-structural-output-dir"
    $scoreArgs += $structural
    $scoreArgs += "--structural-min-confidence"
    $scoreArgs += "$MinConfidence"
}
if ($NoScoreJsonl) {
    $scoreArgs += "--no-score-jsonl"
}
& $Python @scoreArgs

if (-not $DirectStructuralDecode) {
    $decodeArgs = @(
        "tools\tinybdmath_decode_structural_candidates.py",
        "--scores", (Join-Path $scores "tinybdmath_relation_scores.jsonl"),
        "--output-dir", $structural,
        "--limit", $Limit,
        "--min-confidence", $MinConfidence
    )
    if ($StreamStructuralDecode) {
        $decodeArgs += "--stream"
    }
    & $Python @decodeArgs
}
$structuralEvalArgs = @(
    "tools\tinybdmath_eval_structural_candidates.py",
    "--candidates", (Join-Path $structural "tinybdmath_structural_candidates.jsonl"),
    "--relation-labels", (Join-Path $labels "tinybdmath_relation_label_rows.jsonl"),
    "--output", $evalReport,
    "--limit", $Limit
)
if ($StreamStructuralEval) {
    $structuralEvalArgs += "--stream"
}
& $Python @structuralEvalArgs
$decodedEvalArgs = @(
    "tools\tinybdmath_eval_decoded_latex.py",
    "--graph-rows", $GraphRows,
    "--candidates", (Join-Path $structural "tinybdmath_structural_candidates.jsonl"),
    "--output", $decodedEvalReport,
    "--limit", $Limit
)
if ($StreamDecodedLatexEval) {
    $decodedEvalArgs += "--stream"
}
& $Python @decodedEvalArgs

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
    edge_calibration_objective = $(if ($CalibrateTorchEdge) { $TorchCalibrationObjective } else { "" })
    edge_candidate_threshold = $TorchCandidateThreshold
    scoring = [PSCustomObject]@{
        fast_torch = [bool]$FastTorchScoring
        compact_scores = [bool]$CompactScores
        batch_rows = $ScoreBatchRows
        torch_device = $ScoreTorchDevice
    }
    structural_decode = [PSCustomObject]@{
        streaming = [bool]$StreamStructuralDecode
        direct_from_fast_scoring = [bool]$DirectStructuralDecode
        score_jsonl_written = -not [bool]$NoScoreJsonl
    }
    decoded_latex_eval_config = [PSCustomObject]@{
        streaming = [bool]$StreamDecodedLatexEval
    }
    structural_eval_config = [PSCustomObject]@{
        streaming = [bool]$StreamStructuralEval
    }
    mathml = Read-JsonObject (Join-Path $mathml "latex_mathml_manifest.json")
    relation_labels = Read-JsonObject (Join-Path $labels "tinybdmath_relation_label_manifest.json")
    edge_model = Read-FirstJsonObject @(
        (Join-Path $edgeModel "tinybdmath_edge_torch_report.json"),
        (Join-Path $edgeModel "tinybdmath_edge_baseline_report.json")
    )
    relation_scores = Read-JsonObject (Join-Path $scores "tinybdmath_relation_score_manifest.json")
    structural_candidates = Read-JsonObject (Join-Path $structural "tinybdmath_structural_candidate_manifest.json")
    structural_eval = Read-JsonObject $evalReport
    decoded_latex_eval = Read-JsonObject $decodedEvalReport
    candidate_only = $true
    accepted_latex_emitted = $false
}
$payload | ConvertTo-Json -Depth 80 | Set-Content -LiteralPath $summary -Encoding UTF8
[PSCustomObject]@{
    schema_version = $payload.schema_version
    rows = $payload.structural_eval.rows
    micro = $payload.structural_eval.micro
    decoded_latex = $payload.decoded_latex_eval.metrics
    mathml_warnings = $payload.mathml.warnings
    accepted_latex_emitted = $false
} | ConvertTo-Json -Depth 20
Write-Host "[TinyBDMath] relation pipeline done: $OutputDir"

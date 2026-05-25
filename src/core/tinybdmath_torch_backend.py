"""Optional PyTorch backend for TinyBDMath quality models.

This file is safe to import without PyTorch installed.  Training imports torch
inside the function so the main application environment is not forced to carry
heavy ML dependencies.  A future isolated worker can call this module from a
dedicated conda env and export ONNX or TorchScript artifacts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from src.core.tinybdmath_baseline import (
    FEATURE_NAMES,
    LABELS,
    row_features,
    train_validation_split,
)


class TorchBackendUnavailable(RuntimeError):
    """Raised when the optional PyTorch backend is requested but unavailable."""


@dataclass(frozen=True)
class TinyBDTorchConfig:
    model_version: str = "tinybdmath_torch_mlp_quality_v0"
    epochs: int = 40
    hidden_units: int = 64
    batch_size: int = 128
    learning_rate: float = 0.001
    weight_decay: float = 0.0001
    validation_fraction: float = 0.20
    seed: int = 20260525
    device: str = "cpu"


def is_torch_available() -> bool:
    try:
        import torch  # noqa: F401
    except Exception:
        return False
    return True


def train_torch_quality_model(
    rows: list[dict[str, Any]],
    output_dir: Path,
    *,
    config: TinyBDTorchConfig | None = None,
) -> dict[str, Any]:
    """Train a PyTorch MLP on TinyBDMath row features and save artifacts."""

    config = config or TinyBDTorchConfig()
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, TensorDataset
    except Exception as exc:  # pragma: no cover - depends on optional env
        raise TorchBackendUnavailable(str(exc)) from exc

    torch.manual_seed(config.seed)
    train_rows, validation_rows = train_validation_split(
        rows,
        validation_fraction=config.validation_fraction,
        seed=config.seed,
    )
    train_x, train_y, means, scales = _tensorize(train_rows, torch=torch)
    val_x, val_y, _means, _scales = _tensorize(validation_rows, torch=torch, means=means, scales=scales)

    model = nn.Sequential(
        nn.Linear(len(FEATURE_NAMES), config.hidden_units),
        nn.ReLU(),
        nn.LayerNorm(config.hidden_units),
        nn.Linear(config.hidden_units, config.hidden_units),
        nn.ReLU(),
        nn.Linear(config.hidden_units, len(LABELS)),
    ).to(config.device)
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    loss_fn = nn.CrossEntropyLoss()
    loader = DataLoader(
        TensorDataset(train_x, train_y),
        batch_size=max(1, int(config.batch_size)),
        shuffle=True,
    )
    for _epoch in range(max(1, int(config.epochs))):
        model.train()
        for batch_x, batch_y in loader:
            batch_x = batch_x.to(config.device)
            batch_y = batch_y.to(config.device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(batch_x), batch_y)
            loss.backward()
            optimizer.step()

    train_metrics = _torch_metrics(model, train_x, train_y, torch=torch, device=config.device)
    validation_metrics = _torch_metrics(model, val_x, val_y, torch=torch, device=config.device)
    output_dir.mkdir(parents=True, exist_ok=True)
    weights_path = output_dir / "tinybdmath_torch_mlp_quality.pt"
    metadata_path = output_dir / "tinybdmath_torch_mlp_quality_metadata.json"
    torch.save(model.state_dict(), weights_path)
    metadata = {
        "schema_version": "tinybdmath_torch_quality_model_v1",
        "model_version": config.model_version,
        "weights_path": str(weights_path),
        "feature_names": list(FEATURE_NAMES),
        "labels": list(LABELS),
        "feature_mean": [round(float(value), 10) for value in means],
        "feature_scale": [round(float(value), 10) for value in scales],
        "config": asdict(config),
        "row_counts": {
            "all": len(rows),
            "train": len(train_rows),
            "validation": len(validation_rows),
        },
        "train": train_metrics,
        "validation": validation_metrics,
        "notes": [
            "Optional backend; keep it in an isolated ML environment if torch is heavy.",
            "LaTeX source labels are training/evaluation data only.",
            "Predictions are candidate evidence and must pass fusion/verifier gates before accepted write-back.",
        ],
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def _tensorize(
    rows: list[dict[str, Any]],
    *,
    torch: Any,
    means: list[float] | None = None,
    scales: list[float] | None = None,
) -> tuple[Any, Any, list[float], list[float]]:
    vectors = [
        [float(row_features(row).get(name, 0.0) or 0.0) for name in FEATURE_NAMES]
        for row in rows
    ]
    if means is None or scales is None:
        means, scales = _stats(vectors, len(FEATURE_NAMES))
    normalized = [
        [
            (value - means[index]) / max(scales[index], 1e-6)
            for index, value in enumerate(vector)
        ]
        for vector in vectors
    ]
    label_index = {label: index for index, label in enumerate(LABELS)}
    labels = [label_index.get(str(row.get("quality_label", "")), label_index["low_alignment"]) for row in rows]
    if not normalized:
        normalized = [[0.0 for _ in FEATURE_NAMES]]
        labels = [label_index["low_alignment"]]
    return (
        torch.tensor(normalized, dtype=torch.float32),
        torch.tensor(labels, dtype=torch.long),
        means,
        scales,
    )


def _stats(vectors: list[list[float]], width: int) -> tuple[list[float], list[float]]:
    if not vectors:
        return [0.0] * width, [1.0] * width
    means: list[float] = []
    scales: list[float] = []
    for index in range(width):
        values = [vector[index] for vector in vectors]
        mean = sum(values) / len(values)
        variance = sum((value - mean) ** 2 for value in values) / len(values)
        means.append(mean)
        scales.append(max(variance ** 0.5, 1e-6))
    return means, scales


def _torch_metrics(model: Any, features: Any, labels: Any, *, torch: Any, device: str) -> dict[str, Any]:
    model.eval()
    with torch.no_grad():
        logits = model(features.to(device))
        predictions = torch.argmax(logits, dim=1).cpu()
    labels_cpu = labels.cpu()
    total = int(labels_cpu.numel())
    correct = int((predictions == labels_cpu).sum().item())
    confusion: dict[str, dict[str, int]] = {}
    for truth_id, pred_id in zip(labels_cpu.tolist(), predictions.tolist(), strict=False):
        truth = LABELS[int(truth_id)]
        pred = LABELS[int(pred_id)]
        confusion.setdefault(truth, {})
        confusion[truth][pred] = confusion[truth].get(pred, 0) + 1
    return {
        "rows": total,
        "accuracy": round(correct / total, 4) if total else 1.0,
        "confusion": confusion,
    }

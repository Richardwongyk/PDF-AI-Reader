import json
import sys

from src.core import external_formula_tools as module
from src.core.external_formula_tools import ExternalFormulaToolRunner, ExternalFormulaToolSpec


def test_external_formula_runner_parses_worker_candidates(monkeypatch, tmp_path) -> None:
    worker = tmp_path / "worker.py"
    worker.write_text(
        """
import argparse, json
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--backend')
parser.add_argument('--input')
parser.add_argument('--output')
parser.add_argument('--model', default='')
args = parser.parse_args()
payload = json.loads(Path(args.input).read_text(encoding='utf-8'))
results = []
for item in payload['items']:
    results.append({
        'candidate_id': item['candidate_id'],
        'latex': r'\\alpha',
        'model': args.backend,
        'model_version': args.model,
        'preprocess_version': 'png-v1',
        'score': 0.9,
        'duration_ms': 12,
        'warnings': ['candidate_only'],
    })
Path(args.output).write_text(json.dumps({'results': results}), encoding='utf-8')
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "WORKER_SCRIPT", worker)

    candidates = ExternalFormulaToolRunner().recognize_images(
        [("p0_b1", b"png")],
        specs=[
            ExternalFormulaToolSpec(
                name="fake",
                backend="fake_backend",
                python=sys.executable,
                model="fake-model",
            )
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].candidate_id == "p0_b1"
    assert candidates[0].latex == r"\alpha"
    assert candidates[0].model == "fake_backend"
    assert candidates[0].model_version == "fake-model"
    assert candidates[0].score == 0.9
    assert candidates[0].warnings == ("candidate_only",)


def test_external_formula_runner_records_failed_tool_warning(monkeypatch, tmp_path) -> None:
    worker = tmp_path / "worker.py"
    worker.write_text("raise SystemExit('boom')\n", encoding="utf-8")
    monkeypatch.setattr(module, "WORKER_SCRIPT", worker)

    candidates = ExternalFormulaToolRunner().recognize_images(
        [("p0_b1", b"png")],
        specs=[
            ExternalFormulaToolSpec(
                name="fake",
                backend="fake_backend",
                python=sys.executable,
            )
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].candidate_id == "p0_b1"
    assert candidates[0].latex == ""
    assert candidates[0].warnings
    assert candidates[0].warnings[0].startswith("tool_failed:")


def test_external_formula_runner_loads_specs_from_env(monkeypatch, tmp_path) -> None:
    spec_path = tmp_path / "specs.json"
    spec_path.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "paddle_formula",
                        "backend": "paddle_formula",
                        "python": "python.exe",
                        "model": "PP-FormulaNet_plus-S",
                        "env": {"PADDLE_PDX_CACHE_HOME": "C:/models"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PDF_AI_READER_FORMULA_TOOL_SPECS", str(spec_path))

    specs = ExternalFormulaToolRunner.default_specs()

    assert len(specs) == 1
    assert specs[0].name == "paddle_formula"
    assert specs[0].backend == "paddle_formula"
    assert specs[0].model == "PP-FormulaNet_plus-S"
    assert specs[0].env == {"PADDLE_PDX_CACHE_HOME": "C:/models"}


def test_external_formula_runner_discovers_known_local_envs(tmp_path) -> None:
    env_root = tmp_path / "envs"
    paddle = env_root / "pdf_tool_paddle310" / "python.exe"
    pix2text = env_root / "pdf_tool_pix2text310" / "python.exe"
    paddle.parent.mkdir(parents=True)
    pix2text.parent.mkdir(parents=True)
    paddle.write_text("", encoding="utf-8")
    pix2text.write_text("", encoding="utf-8")

    specs = ExternalFormulaToolRunner.known_local_specs(env_root)

    names = {spec.name for spec in specs}
    assert names == {"paddle_formula", "pix2text_formula"}
    by_name = {spec.name: spec for spec in specs}
    assert by_name["paddle_formula"].backend == "paddle_formula"
    assert by_name["paddle_formula"].model == "PP-FormulaNet_plus-S"
    assert by_name["pix2text_formula"].backend == "pix2text_formula"

import json
import sys

from src.core import external_formula_tools as module
from src.core.external_formula_tools import ExternalFormulaToolRunner, ExternalFormulaToolSpec
from tools import formula_tool_worker


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
    mineru = env_root / "pdf_tool_mineru310" / "python.exe"
    pek = env_root / "pdf_tool_pek310" / "python.exe"
    paddle.parent.mkdir(parents=True)
    pix2text.parent.mkdir(parents=True)
    mineru.parent.mkdir(parents=True)
    pek.parent.mkdir(parents=True)
    paddle.write_text("", encoding="utf-8")
    pix2text.write_text("", encoding="utf-8")
    mineru.write_text("", encoding="utf-8")
    pek.write_text("", encoding="utf-8")

    specs = ExternalFormulaToolRunner.known_local_specs(env_root)

    names = {spec.name for spec in specs}
    assert names == {"paddle_formula", "pix2text_formula", "mineru_hybrid_formula", "pek_unimernet"}
    by_name = {spec.name: spec for spec in specs}
    assert by_name["paddle_formula"].backend == "paddle_formula"
    assert by_name["paddle_formula"].model == "PP-FormulaNet_plus-S"
    assert by_name["pix2text_formula"].backend == "pix2text_formula"
    assert by_name["mineru_hybrid_formula"].backend == "mineru_pdf_page"
    assert by_name["mineru_hybrid_formula"].model == "hybrid-auto-engine"
    assert by_name["pek_unimernet"].backend == "pek_unimernet"


def test_formula_tool_worker_extracts_mineru_formula_snippets(tmp_path) -> None:
    out_dir = tmp_path / "mineru"
    out_dir.mkdir()
    (out_dir / "page.md").write_text(
        r"""
        Text before
        $$\alpha+\beta$$
        and inline \(\gamma\).
        """,
        encoding="utf-8",
    )

    formulas = formula_tool_worker._extract_mineru_formulas(out_dir)

    assert formulas == [r"\alpha+\beta", r"\gamma"]


def test_formula_tool_worker_records_unavailable_pek_warning(monkeypatch) -> None:
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "unimernet":
            raise ModuleNotFoundError("No module named 'unimernet'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    result = formula_tool_worker._run_pek_unimernet(
        [{"candidate_id": "p0_b1", "image_path": "formula.png"}]
    )[0]

    assert result["candidate_id"] == "p0_b1"
    assert result["model"] == "pek_unimernet"
    assert result["latex"] == ""
    assert str(result["warnings"][0]).startswith("tool_failed:pek_unimernet_unavailable")


def test_formula_tool_worker_failed_items_keep_tool_identity() -> None:
    result = formula_tool_worker._failed_item(
        {"candidate_id": "p0_b1"},
        "mineru_failed",
        model="mineru_hybrid_formula",
        model_version="hybrid-auto-engine",
        preprocess_version="pdf-page-txt-v1",
    )

    assert result["candidate_id"] == "p0_b1"
    assert result["model"] == "mineru_hybrid_formula"
    assert result["model_version"] == "hybrid-auto-engine"
    assert result["preprocess_version"] == "pdf-page-txt-v1"


def test_formula_tool_worker_accepts_utf8_bom_input(tmp_path) -> None:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(
        '{"items":[{"candidate_id":"p0_b1","image_path":"missing.png"}]}',
        encoding="utf-8-sig",
    )

    code = formula_tool_worker.main_with_args_for_test([
        "--backend",
        "pek_unimernet",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--model",
        "UniMERNet",
    ])

    assert code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["results"][0]["candidate_id"] == "p0_b1"


def test_formula_tool_worker_keeps_pdf_page_context(monkeypatch, tmp_path) -> None:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    input_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "candidate_id": "p0_b1",
                        "image_path": "formula.png",
                        "pdf_path": "paper.pdf",
                        "page_num": 2,
                        "bbox": [10, 20, 30, 40],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    captured: list[dict[str, object]] = []

    def fake_mineru(items, model, output_root):
        captured.extend(items)
        return [
            {
                "candidate_id": items[0]["candidate_id"],
                "latex": r"x+y",
                "score": None,
                "model": "mineru_hybrid_formula",
                "model_version": model,
                "preprocess_version": "pdf-page-txt-v1",
                "duration_ms": 1,
                "warnings": [],
                "raw": {},
            }
        ]

    monkeypatch.setattr(formula_tool_worker, "_run_mineru_pdf_page", fake_mineru)

    code = formula_tool_worker.main_with_args_for_test([
        "--backend",
        "mineru_pdf_page",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--model",
        "hybrid-auto-engine",
    ])

    assert code == 0
    assert captured == [
        {
            "candidate_id": "p0_b1",
            "image_path": "formula.png",
            "pdf_path": "paper.pdf",
            "page_num": 2,
            "bbox": [10, 20, 30, 40],
        }
    ]

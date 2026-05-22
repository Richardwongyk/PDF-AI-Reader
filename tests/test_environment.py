import sys
import tomllib
from pathlib import Path


def test_python_version_matches_project_target() -> None:
    assert sys.version_info[:3] == (3, 14, 4)


def test_pyproject_requires_python_3144() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["requires-python"] == "==3.14.4"

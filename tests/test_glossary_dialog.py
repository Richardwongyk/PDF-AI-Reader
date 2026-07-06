import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.core.glossary_manager import GlossaryManager
from src.core.models import GlossaryEntry
from src.ui.glossary_dialog import GlossaryDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_glossary(path: Path, domain: str, terms: list[dict[str, object]]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{domain}.json").write_text(
        json.dumps({"domain": domain, "terms": terms}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_glossary_manager_replaces_domain_and_reads_import_file(tmp_path: Path) -> None:
    glossary_dir = tmp_path / "glossary"
    _write_glossary(
        glossary_dir,
        "math",
        [{"en": "manifold", "zh": "流形", "domain": "math", "force": True}],
    )
    import_file = tmp_path / "terms.csv"
    import_file.write_text(
        "en,zh,domain,force,aliases,notes\n"
        "attention,注意力,cs_ml,true,self-attention,Transformer term\n",
        encoding="utf-8",
    )

    manager = GlossaryManager(str(glossary_dir))
    imported = manager.read_glossary_file(str(import_file))
    assert imported[0].en == "attention"
    assert imported[0].aliases == ["self-attention"]

    manager.set_entries(
        "user",
        [GlossaryEntry(en="kernel", zh="核函数", domain="draft", force=True)],
    )
    manager.save()

    payload = json.loads((glossary_dir / "user.json").read_text(encoding="utf-8"))
    assert payload["terms"][0]["domain"] == "user"
    assert payload["terms"][0]["zh"] == "核函数"


def test_glossary_dialog_saves_user_terms(tmp_path: Path) -> None:
    _app()
    glossary_dir = tmp_path / "glossary"
    _write_glossary(
        glossary_dir,
        "math",
        [{"en": "gradient", "zh": "梯度", "domain": "math", "force": True}],
    )
    manager = GlossaryManager(str(glossary_dir))
    dialog = GlossaryDialog(manager)
    saved: list[bool] = []
    dialog.glossary_saved.connect(lambda: saved.append(True))

    dialog._set_active_domain("user")
    dialog._add_term()
    row = dialog._table.rowCount() - 1
    dialog._table.item(row, 0).setText("attention")
    dialog._table.item(row, 1).setText("注意力")
    dialog._table.item(row, 3).setCheckState(Qt.CheckState.Checked)
    dialog._table.item(row, 4).setText("self-attention, attention mechanism")
    dialog._table.item(row, 5).setText("论文翻译固定术语")

    assert dialog._save_changes(show_message=False) is True
    assert saved == [True]

    user_terms = manager.get_entries(["user"])
    assert len(user_terms) == 1
    assert user_terms[0].en == "attention"
    assert user_terms[0].zh == "注意力"
    assert user_terms[0].force is True
    assert user_terms[0].aliases == ["self-attention", "attention mechanism"]

    payload = json.loads((glossary_dir / "user.json").read_text(encoding="utf-8"))
    assert payload["terms"][0]["notes"] == "论文翻译固定术语"
    dialog.close()

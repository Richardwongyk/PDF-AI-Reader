"""Glossary editor dialog."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.glossary_manager import GlossaryManager
from src.core.models import GlossaryEntry


class GlossaryDialog(QDialog):
    """Editable glossary manager for built-in and user imported terms."""

    glossary_saved = Signal()

    HEADERS = ["英文术语", "中文译名", "领域", "强制", "别名", "备注"]
    DOMAIN_ORDER = ["math", "cs_ml", "physics", "imported", "user"]
    DOMAIN_LABELS = {
        "math": "数学",
        "cs_ml": "计算机 / ML",
        "physics": "物理",
        "imported": "导入",
        "user": "用户",
    }

    def __init__(self, manager: GlossaryManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._domain_entries: dict[str, list[GlossaryEntry]] = {}
        self._active_domain = ""
        self._dirty = False
        self._loading = False

        self.setObjectName("glossary_dialog")
        self.setWindowTitle("术语表管理器")
        self.resize(980, 640)
        self.setMinimumSize(820, 520)

        self._build_ui()
        self._load_from_manager()
        self._refresh_domain_list(select_domain=self._first_domain())
        self._apply_style()

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("glossary_header")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        title = QLabel("术语表管理器")
        title.setObjectName("glossary_title")
        subtitle = QLabel("维护翻译 Prompt 注入的专业术语；保存后立即刷新当前翻译服务。")
        subtitle.setObjectName("glossary_subtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        root.addWidget(self._splitter, 1)

        left_panel = QWidget()
        left_panel.setObjectName("glossary_left_panel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 10, 0)
        left_layout.setSpacing(8)

        domain_label = QLabel("领域")
        domain_label.setObjectName("glossary_section_label")
        left_layout.addWidget(domain_label)

        self._domain_list = QListWidget()
        self._domain_list.setObjectName("glossary_domain_list")
        self._domain_list.setMinimumWidth(180)
        self._domain_list.currentItemChanged.connect(self._on_domain_changed)
        left_layout.addWidget(self._domain_list, 1)

        self._count_label = QLabel("")
        self._count_label.setObjectName("glossary_count_label")
        left_layout.addWidget(self._count_label)
        self._splitter.addWidget(left_panel)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 0, 0, 0)
        right_layout.setSpacing(10)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        self._domain_title = QLabel("")
        self._domain_title.setObjectName("glossary_domain_title")
        top_bar.addWidget(self._domain_title)
        top_bar.addStretch(1)
        self._search_box = QLineEdit()
        self._search_box.setObjectName("glossary_search_box")
        self._search_box.setPlaceholderText("搜索英文、中文、别名或备注")
        self._search_box.setClearButtonEnabled(True)
        self._search_box.setMinimumWidth(260)
        self._search_box.textChanged.connect(self._apply_search_filter)
        top_bar.addWidget(self._search_box)
        right_layout.addLayout(top_bar)

        self._table = QTableWidget()
        self._table.setObjectName("glossary_terms_table")
        self._table.setColumnCount(len(self.HEADERS))
        self._table.setHorizontalHeaderLabels(self.HEADERS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._table.itemChanged.connect(self._on_item_changed)
        right_layout.addWidget(self._table, 1)

        action_bar = QHBoxLayout()
        action_bar.setSpacing(8)
        self._add_button = QPushButton("新增")
        self._delete_button = QPushButton("删除")
        self._import_button = QPushButton("导入 JSON/CSV")
        self._save_button = QPushButton("保存")
        self._close_button = QPushButton("关闭")
        self._save_button.setObjectName("primary_button")
        self._set_standard_icon(self._add_button, QStyle.StandardPixmap.SP_FileIcon)
        self._set_standard_icon(self._delete_button, QStyle.StandardPixmap.SP_DialogDiscardButton)
        self._set_standard_icon(self._import_button, QStyle.StandardPixmap.SP_DialogOpenButton)
        self._set_standard_icon(self._save_button, QStyle.StandardPixmap.SP_DialogSaveButton)
        self._add_button.clicked.connect(self._add_term)
        self._delete_button.clicked.connect(self._delete_selected_terms)
        self._import_button.clicked.connect(self._import_terms)
        self._save_button.clicked.connect(lambda: self._save_changes())
        self._close_button.clicked.connect(self._close_requested)
        action_bar.addWidget(self._add_button)
        action_bar.addWidget(self._delete_button)
        action_bar.addWidget(self._import_button)
        action_bar.addStretch(1)
        action_bar.addWidget(self._save_button)
        action_bar.addWidget(self._close_button)
        right_layout.addLayout(action_bar)
        self._splitter.addWidget(right_panel)
        self._splitter.setSizes([220, 760])

    def _set_standard_icon(self, button: QPushButton, pixmap: QStyle.StandardPixmap) -> None:
        icon = self.style().standardIcon(pixmap)
        if not icon.isNull():
            button.setIcon(icon)

    def _load_from_manager(self) -> None:
        self._domain_entries.clear()
        for domain in self._domains():
            entries = self._manager.get_entries([domain])
            self._domain_entries[domain] = [entry.model_copy(deep=True) for entry in entries]
        self._domain_entries.setdefault("imported", [])
        self._domain_entries.setdefault("user", [])

    def _domains(self) -> list[str]:
        known = set(self._manager.domains) | set(self._domain_entries) | {"imported", "user"}
        ordered = [domain for domain in self.DOMAIN_ORDER if domain in known]
        ordered.extend(sorted(known - set(ordered)))
        return ordered

    def _first_domain(self) -> str:
        for domain in self.DOMAIN_ORDER:
            if domain in self._domain_entries:
                return domain
        return next(iter(self._domain_entries), "user")

    def _refresh_domain_list(self, select_domain: str | None = None) -> None:
        select_domain = select_domain or self._active_domain or self._first_domain()
        self._domain_list.blockSignals(True)
        self._domain_list.clear()
        for domain in self._domains():
            count = len(self._domain_entries.get(domain, []))
            item = QListWidgetItem(f"{self._domain_label(domain)}  ·  {count}")
            item.setData(Qt.ItemDataRole.UserRole, domain)
            self._domain_list.addItem(item)
            if domain == select_domain:
                item.setSelected(True)
                self._domain_list.setCurrentItem(item)
        self._domain_list.blockSignals(False)
        current = self._domain_list.currentItem()
        if current is not None:
            self._set_active_domain(str(current.data(Qt.ItemDataRole.UserRole)))

    def _domain_label(self, domain: str) -> str:
        return self.DOMAIN_LABELS.get(domain, domain)

    def _on_domain_changed(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if current is None or self._loading:
            return
        self._store_current_table(strict=False)
        self._set_active_domain(str(current.data(Qt.ItemDataRole.UserRole)))

    def _set_active_domain(self, domain: str) -> None:
        self._active_domain = domain
        self._domain_entries.setdefault(domain, [])
        self._domain_title.setText(f"{self._domain_label(domain)}  ({domain})")
        self._load_domain_table(domain)
        self._apply_search_filter()

    def _load_domain_table(self, domain: str) -> None:
        self._loading = True
        try:
            entries = self._domain_entries.get(domain, [])
            self._table.setRowCount(len(entries))
            for row, entry in enumerate(entries):
                self._set_row(row, entry)
            self._count_label.setText(f"{self._domain_label(domain)}：{len(entries)} 条术语")
        finally:
            self._loading = False

    def _set_row(self, row: int, entry: GlossaryEntry) -> None:
        values = [
            entry.en,
            entry.zh,
            entry.domain or self._active_domain,
            "",
            ", ".join(entry.aliases),
            entry.notes,
        ]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 3:
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsUserCheckable
                )
                item.setCheckState(Qt.CheckState.Checked if entry.force else Qt.CheckState.Unchecked)
                item.setText("")
            elif column == 2:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            else:
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | Qt.ItemFlag.ItemIsEditable
                )
            self._table.setItem(row, column, item)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        self._dirty = True
        self._apply_search_filter()

    def _add_term(self) -> None:
        domain = self._active_domain or "user"
        self._store_current_table(strict=False)
        self._domain_entries.setdefault(domain, []).append(
            GlossaryEntry(en="new_term", zh="新术语", domain=domain, force=False)
        )
        self._dirty = True
        self._refresh_domain_list(select_domain=domain)
        row = self._table.rowCount() - 1
        if row >= 0:
            self._table.selectRow(row)
            self._table.editItem(self._table.item(row, 0))

    def _delete_selected_terms(self) -> None:
        rows = sorted({index.row() for index in self._table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            QMessageBox.information(self, "删除术语", "请先选择要删除的术语行。")
            return
        for row in rows:
            self._table.removeRow(row)
        self._dirty = True
        self._store_current_table(strict=False)
        self._refresh_domain_list(select_domain=self._active_domain)

    def _import_terms(self) -> None:
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "导入术语表",
            "",
            "术语表 (*.json *.csv);;JSON 文件 (*.json);;CSV 文件 (*.csv);;所有文件 (*.*)",
        )
        if not filepath:
            return
        try:
            imported = self._manager.read_glossary_file(filepath)
        except Exception as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        if not imported:
            QMessageBox.information(self, "导入术语表", "没有读到可导入的术语。")
            return
        self._store_current_table(strict=False)
        target_domain = "imported"
        self._domain_entries.setdefault(target_domain, [])
        self._domain_entries[target_domain].extend(
            entry.model_copy(update={"domain": target_domain})
            for entry in imported
        )
        self._dirty = True
        self._refresh_domain_list(select_domain=target_domain)
        QMessageBox.information(
            self,
            "导入完成",
            f"已从 {Path(filepath).name} 导入 {len(imported)} 条术语，保存后生效。",
        )

    def _save_changes(self, show_message: bool = True) -> bool:
        try:
            self._store_current_table(strict=True)
        except ValueError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return False
        for domain, entries in self._domain_entries.items():
            self._manager.set_entries(domain, entries)
        self._manager.save()
        self._dirty = False
        self._refresh_domain_list(select_domain=self._active_domain)
        self.glossary_saved.emit()
        if show_message:
            QMessageBox.information(self, "术语表管理器", "术语表已保存并刷新翻译服务。")
        return True

    def _store_current_table(self, *, strict: bool) -> None:
        if not self._active_domain:
            return
        self._domain_entries[self._active_domain] = self._entries_from_table(strict=strict)

    def _entries_from_table(self, *, strict: bool) -> list[GlossaryEntry]:
        entries: list[GlossaryEntry] = []
        domain = self._active_domain or "user"
        for row in range(self._table.rowCount()):
            en = self._cell_text(row, 0).strip()
            zh = self._cell_text(row, 1).strip()
            aliases_text = self._cell_text(row, 4).strip()
            notes = self._cell_text(row, 5).strip()
            force = self._table.item(row, 3).checkState() == Qt.CheckState.Checked
            if not any([en, zh, aliases_text, notes, force]):
                continue
            if strict and (not en or not zh):
                raise ValueError(f"第 {row + 1} 行需要同时填写英文术语和中文译名。")
            aliases = [
                item.strip()
                for item in aliases_text.replace("，", ",").split(",")
                if item.strip()
            ]
            entries.append(
                GlossaryEntry(
                    en=en,
                    zh=zh,
                    domain=domain,
                    force=force,
                    aliases=aliases,
                    notes=notes,
                )
            )
        return entries

    def _cell_text(self, row: int, column: int) -> str:
        item = self._table.item(row, column)
        return item.text() if item is not None else ""

    def _apply_search_filter(self) -> None:
        query = self._search_box.text().strip().lower()
        visible = 0
        for row in range(self._table.rowCount()):
            haystack = " ".join(
                self._cell_text(row, column)
                for column in (0, 1, 2, 4, 5)
            ).lower()
            matched = not query or query in haystack
            self._table.setRowHidden(row, not matched)
            if matched:
                visible += 1
        total = self._table.rowCount()
        if query:
            self._count_label.setText(
                f"{self._domain_label(self._active_domain)}：显示 {visible} / {total}"
            )
        else:
            self._count_label.setText(
                f"{self._domain_label(self._active_domain)}：{total} 条术语"
            )

    def _close_requested(self) -> None:
        if self._maybe_save_before_close():
            self.reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._maybe_save_before_close():
            event.accept()
        else:
            event.ignore()

    def _maybe_save_before_close(self) -> bool:
        if not self._dirty:
            return True
        choice = QMessageBox.question(
            self,
            "保存术语表",
            "术语表还有未保存的修改。是否保存后关闭？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            return self._save_changes()
        if choice == QMessageBox.StandardButton.Discard:
            return True
        return False

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog#glossary_dialog {
                background: palette(window);
            }
            QLabel#glossary_title {
                font-size: 20px;
                font-weight: 700;
            }
            QLabel#glossary_subtitle,
            QLabel#glossary_count_label {
                color: palette(mid);
            }
            QLabel#glossary_section_label,
            QLabel#glossary_domain_title {
                font-weight: 700;
            }
            QListWidget#glossary_domain_list,
            QTableWidget#glossary_terms_table,
            QLineEdit#glossary_search_box {
                border: 1px solid palette(mid);
                border-radius: 6px;
                padding: 4px;
            }
            QListWidget#glossary_domain_list::item {
                padding: 8px 10px;
                border-radius: 5px;
            }
            QListWidget#glossary_domain_list::item:selected {
                background: palette(highlight);
                color: palette(highlighted-text);
            }
            QPushButton {
                padding: 6px 12px;
                border-radius: 5px;
            }
            QPushButton#primary_button {
                font-weight: 700;
            }
            """
        )

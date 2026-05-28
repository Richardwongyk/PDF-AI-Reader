"""Formula acceptance review dialog."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.app.formula_acceptance_review import FormulaAcceptanceReviewService


class FormulaAcceptanceDialog(QDialog):
    """Review persisted formula candidates through the shared service API."""

    def __init__(
        self,
        service: FormulaAcceptanceReviewService,
        doc_hash: str,
        filepath: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._doc_hash = doc_hash
        self._filepath = filepath
        self.setWindowTitle("公式审核")
        self.resize(980, 620)

        layout = QVBoxLayout(self)
        self._status = QLabel()
        layout.addWidget(self._status)

        self._tabs = QTabWidget()
        self._ready_table = _table(["candidate", "decision", "similarity", "fusion_id", "latex"])
        self._result_table = _table(["candidate", "stage", "score", "accepted", "result_id", "latex"])
        self._decision_table = _table(["time", "action", "candidate", "source", "reason", "result_id"])
        self._tabs.addTab(self._ready_table, "待接受")
        self._tabs.addTab(self._result_table, "候选结果")
        self._tabs.addTab(self._decision_table, "审核记录")
        layout.addWidget(self._tabs, 1)

        self._reason = QTextEdit()
        self._reason.setPlaceholderText("审核原因")
        self._reason.setFixedHeight(68)
        layout.addWidget(self._reason)

        buttons = QHBoxLayout()
        self._refresh_button = QPushButton("刷新")
        self._accept_fusion_button = QPushButton("接受选中融合")
        self._accept_result_button = QPushButton("接受选中结果")
        self._reject_result_button = QPushButton("拒绝选中结果")
        self._close_button = QPushButton("关闭")
        buttons.addWidget(self._refresh_button)
        buttons.addStretch(1)
        buttons.addWidget(self._accept_fusion_button)
        buttons.addWidget(self._accept_result_button)
        buttons.addWidget(self._reject_result_button)
        buttons.addWidget(self._close_button)
        layout.addLayout(buttons)

        self._refresh_button.clicked.connect(self.refresh)
        self._accept_fusion_button.clicked.connect(self._accept_selected_fusion)
        self._accept_result_button.clicked.connect(self._accept_selected_result)
        self._reject_result_button.clicked.connect(self._reject_selected_result)
        self._close_button.clicked.connect(self.accept)
        self.refresh()

    def refresh(self) -> None:
        ready = self._service.list_ready_fusion(self._doc_hash, limit=100)
        results = self._service.list_results(self._doc_hash, limit=200)
        decisions = self._service.list_decisions(self._doc_hash, limit=100)
        _fill_ready_table(self._ready_table, ready.get("fusion_records", []))
        _fill_result_table(self._result_table, results.get("results", []))
        _fill_decision_table(self._decision_table, decisions.get("decisions", []))
        self._status.setText(
            f"待接受 {ready.get('count', 0)}，候选 {results.get('count', 0)}，审核记录 {decisions.get('count', 0)}"
        )

    def _accept_selected_fusion(self) -> None:
        fusion_id = _selected_id(self._ready_table, 3)
        if not fusion_id:
            QMessageBox.information(self, "公式审核", "请先选择一条待接受融合记录。")
            return
        try:
            self._service.accept_fusion(
                self._doc_hash,
                fusion_id=fusion_id,
                filepath=self._filepath,
                source="manual_ui_fusion",
                reason=self._reason.toPlainText().strip(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "公式审核失败", str(exc))
            return
        self.refresh()

    def _accept_selected_result(self) -> None:
        result_id = _selected_id(self._result_table, 4)
        if not result_id:
            QMessageBox.information(self, "公式审核", "请先选择一条候选结果。")
            return
        try:
            self._service.accept_result(
                self._doc_hash,
                result_id=result_id,
                filepath=self._filepath,
                source="manual_ui",
                reason=self._reason.toPlainText().strip(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "公式审核失败", str(exc))
            return
        self.refresh()

    def _reject_selected_result(self) -> None:
        result_id = _selected_id(self._result_table, 4)
        if not result_id:
            QMessageBox.information(self, "公式审核", "请先选择一条候选结果。")
            return
        try:
            self._service.reject_result(
                self._doc_hash,
                result_id=result_id,
                source="manual_ui",
                reason=self._reason.toPlainText().strip(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "公式审核失败", str(exc))
            return
        self.refresh()


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _fill_ready_table(table: QTableWidget, rows: list[dict[str, Any]]) -> None:
    table.setRowCount(len(rows))
    for index, row in enumerate(rows):
        result = row.get("result_json", {})
        latex = result.get("best_latex", "") if isinstance(result, dict) else ""
        _set_row(
            table,
            index,
            [
                row.get("candidate_id", ""),
                row.get("decision", ""),
                _fmt_float(row.get("source_similarity")),
                row.get("fusion_id", ""),
                latex,
            ],
        )


def _fill_result_table(table: QTableWidget, rows: list[dict[str, Any]]) -> None:
    table.setRowCount(len(rows))
    for index, row in enumerate(rows):
        _set_row(
            table,
            index,
            [
                row.get("candidate_id", ""),
                row.get("stage", ""),
                _fmt_float(row.get("score")),
                "yes" if row.get("accepted") else "",
                row.get("result_id", ""),
                row.get("latex", ""),
            ],
        )


def _fill_decision_table(table: QTableWidget, rows: list[dict[str, Any]]) -> None:
    table.setRowCount(len(rows))
    for index, row in enumerate(rows):
        _set_row(
            table,
            index,
            [
                row.get("created_at", ""),
                row.get("action", ""),
                row.get("candidate_id", ""),
                row.get("decision_source", ""),
                row.get("reason", ""),
                row.get("result_id", ""),
            ],
        )


def _set_row(table: QTableWidget, row_index: int, values: list[object]) -> None:
    for column, value in enumerate(values):
        item = QTableWidgetItem(str(value or ""))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        table.setItem(row_index, column, item)


def _selected_id(table: QTableWidget, column: int) -> str:
    selected = table.selectionModel().selectedRows()
    if not selected:
        return ""
    item = table.item(selected[0].row(), column)
    return item.text().strip() if item else ""


def _fmt_float(value: object) -> str:
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return ""

"""
Centralized error handling with severity levels — adapted from PDFCrop.

Provides:
  - ErrorSeverity enum (INFO / WARNING / ERROR / CRITICAL)
  - ErrorHandler with logging, optional dialogs, and per-type callbacks
  - Global exception hook setup (replaces the ad-hoc hook in main.py)
"""

from __future__ import annotations

import logging
import sys
import traceback
from collections.abc import Callable
from enum import Enum
from typing import Any

_logger = logging.getLogger(__name__)


class ErrorSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ErrorHandler:
    """Centralized application error handler.

    Usage:
        handler = ErrorHandler(main_window)
        handler.setup_global_exception_hook()

        try:
            ...
        except SomeError as e:
            handler.handle(e, "loading PDF", ErrorSeverity.ERROR)
    """

    def __init__(self, parent_widget: Any | None = None) -> None:
        self._parent_widget = parent_widget
        self._error_callbacks: dict[type, Callable] = {}
        self._show_dialogs: bool = True

    def set_parent_widget(self, widget: Any) -> None:
        self._parent_widget = widget

    def set_show_dialogs(self, show: bool) -> None:
        self._show_dialogs = show

    def register_callback(self, error_type: type, callback: Callable) -> None:
        """Register a type-specific error callback."""
        self._error_callbacks[error_type] = callback

    def handle(
        self,
        error: Exception,
        context: str = "",
        severity: ErrorSeverity = ErrorSeverity.ERROR,
        show_dialog: bool | None = None,
    ) -> None:
        """Handle an error with appropriate logging and optional UI feedback."""
        msg = str(error)
        full = f"{context}: {msg}" if context else msg

        if severity == ErrorSeverity.INFO:
            _logger.info(full)
        elif severity == ErrorSeverity.WARNING:
            _logger.warning(full)
        elif severity == ErrorSeverity.ERROR:
            _logger.error(full, exc_info=error)
        elif severity == ErrorSeverity.CRITICAL:
            _logger.critical(full, exc_info=error)

        # Type-specific callback
        cb = self._error_callbacks.get(type(error))
        if cb:
            try:
                cb(error, context)
            except Exception as e:
                _logger.error("Error in callback for %s: %s", type(error).__name__, e)

        # Dialog
        should_show = show_dialog if show_dialog is not None else self._show_dialogs
        if should_show and severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL):
            self._show_error_dialog(full, severity)

    def handle_exception(
        self,
        exc_type: type,
        exc_value: Exception,
        exc_tb: Any,
        context: str = "",
    ) -> None:
        """Handle an uncaught exception (sys.excepthook callback)."""
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        _logger.critical("Uncaught exception in %s:\n%s", context, tb_text)
        self.handle(exc_value, f"Uncaught exception in {context}", ErrorSeverity.CRITICAL)

    def create_exception_hook(self, context: str = "application") -> Callable:
        """Create a sys.excepthook-compatible function."""

        def _hook(exc_type: type, exc_value: BaseException, exc_tb: Any) -> None:
            if issubclass(exc_type, KeyboardInterrupt):
                sys.__excepthook__(exc_type, exc_value, exc_tb)
                return
            self.handle_exception(exc_type, exc_value, exc_tb, context)

        return _hook

    def setup_global_exception_hook(self, context: str = "application") -> None:
        """Install this handler as the global sys.excepthook."""
        sys.excepthook = self.create_exception_hook(context)
        _logger.info("Global exception hook installed (context=%s)", context)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _show_error_dialog(self, message: str, severity: ErrorSeverity) -> None:
        if not self._parent_widget:
            return

        try:
            from PySide6.QtWidgets import QMessageBox

            icon_map = {
                ErrorSeverity.WARNING: QMessageBox.Icon.Warning,
                ErrorSeverity.CRITICAL: QMessageBox.Icon.Critical,
                ErrorSeverity.ERROR: QMessageBox.Icon.Critical,
            }
            title_map = {
                ErrorSeverity.WARNING: "Warning",
                ErrorSeverity.CRITICAL: "Critical Error",
                ErrorSeverity.ERROR: "Error",
            }

            box = QMessageBox(self._parent_widget)
            box.setIcon(icon_map.get(severity, QMessageBox.Icon.Critical))
            box.setWindowTitle(title_map.get(severity, "Error"))
            box.setText(message)
            box.setStandardButtons(QMessageBox.StandardButton.Ok)
            box.exec()
        except Exception:
            _logger.warning("Failed to show error dialog", exc_info=True)

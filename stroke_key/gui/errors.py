"""Consistent friendly errors, with development exception logging."""
import logging
from PySide6.QtWidgets import QMessageBox, QWidget


def show_error(parent: QWidget, message: str) -> None:
    logging.getLogger(__name__).exception(message)
    QMessageBox.warning(parent, "StrokeKey", message)

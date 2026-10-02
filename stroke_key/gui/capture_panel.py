"""Reusable canvas, clear action, counters and capture validation."""
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from stroke_key.gui.signature_canvas import SignatureCanvas
from stroke_key.models.signature import SignatureSample, validity_errors


class CapturePanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas = SignatureCanvas()
        self.status = QLabel()
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.canvas.clear)
        self.canvas.changed.connect(self.update_status)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sign in the white area. Lift the pointer between strokes."))
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.clear_button)
        self.update_status()

    def update_status(self) -> None:
        sample = self.canvas.sample()
        state = "Signing…" if self.canvas.recorder.active_device else "Ready"
        if self.canvas.recorder.interrupted:
            state = "Interrupted — clear and retry"
        self.status.setText(f"{state} | {len(sample.points)} points | "
                            f"{sample.number_of_strokes} strokes | {sample.total_duration:.3f} s")

    def validated_sample(self) -> SignatureSample | None:
        if self.canvas.recorder.active_device:
            QMessageBox.warning(self, "Finish signing", "Release the pointer before continuing.")
            return None
        sample = self.canvas.sample()
        errors = validity_errors(sample)
        if errors:
            QMessageBox.warning(self, "Signature needs more data", "\n".join(errors))
            return None
        return sample

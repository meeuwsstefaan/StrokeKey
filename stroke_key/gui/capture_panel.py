"""Reusable canvas, clear action, counters and capture validation."""
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from stroke_key.gui.signature_canvas import SignatureCanvas
from stroke_key.models.signature import SignatureSample, validity_errors
from stroke_key.processing.quality import assess_quality


class CapturePanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.canvas = SignatureCanvas()
        self.status = QLabel()
        self.status.setMinimumHeight(self.status.fontMetrics().height() + 4)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.canvas.clear)
        self.canvas.changed.connect(self.update_status)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Sign in the white area. Lift the pointer between strokes."))
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.status)
        self.quality = QPlainTextEdit("Finish a sample to see capture quality.")
        self.quality.setReadOnly(True)
        self.quality.setFixedHeight(75)
        self.quality.setAccessibleName("Capture quality report")
        layout.addWidget(self.quality)
        session_row = QHBoxLayout()
        self.session_tag = QLineEdit()
        self.session_tag.setMaxLength(120)
        self.session_tag.setPlaceholderText("Optional session note (e.g. morning, desk tablet)")
        session_row.addWidget(self.session_tag, 1)
        self.new_session_button = QPushButton("New session")
        self.new_session_button.setToolTip("Start a separate collection session after clearing the current capture.")
        self.new_session_button.clicked.connect(self.new_session)
        session_row.addWidget(self.new_session_button)
        layout.addLayout(session_row)
        self.session = QLabel()
        self.session.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.session)
        label_row = QHBoxLayout()
        label_row.addWidget(QLabel("Research label (self-reported):"))
        self.research_label = QComboBox()
        for label, value in (("Unlabelled", "unlabelled"), ("Genuine", "genuine"),
                             ("Attempted imitation", "imitation"), ("Synthetic / demonstration", "synthetic")):
            self.research_label.addItem(label, value)
        label_row.addWidget(self.research_label)
        layout.addLayout(label_row)
        layout.addWidget(self.clear_button)
        self.update_status()

    def update_status(self) -> None:
        sample = self.canvas.sample()
        state = "Signing…" if self.canvas.recorder.active_device else "Ready"
        if self.canvas.recorder.interrupted:
            state = "Interrupted — clear and retry"
        self.status.setText(f"{state} | {len(sample.points)} points | "
                             f"{sample.number_of_strokes} strokes | {sample.total_duration:.3f} s")
        self.session.setText(f"Session: {self.canvas.session_id}")
        self.new_session_button.setEnabled(not sample.points and not self.canvas.recorder.active_device)
        if self.canvas.recorder.active_device:
            self.quality.setPlainText("Signing… quality will be assessed after release.")
        elif sample.points:
            self.quality.setPlainText(assess_quality(sample).summary())
        else:
            self.quality.setPlainText("Finish a sample to see capture quality.")

    def new_session(self) -> None:
        if not self.canvas.recorder.points and not self.canvas.recorder.active_device:
            self.canvas.session_id = str(uuid4())
            self.session_tag.clear()
            self.update_status()

    def validated_sample(self) -> SignatureSample | None:
        if self.canvas.recorder.active_device:
            QMessageBox.warning(self, "Finish signing", "Release the pointer before continuing.")
            return None
        sample = self.canvas.sample()
        errors = validity_errors(sample)
        if errors:
            QMessageBox.warning(self, "Signature needs more data", "\n".join(errors))
            return None
        sample.metadata.update({"session_tag": self.session_tag.text().strip(),
                                "research_label": self.research_label.currentData()})
        return sample

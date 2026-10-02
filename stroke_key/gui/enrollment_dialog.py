"""Five persisted research samples, retry and atomic final enrollment."""
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout

from stroke_key.config import CAPTURE_CONFIG, RESEARCH_NOTICE
from stroke_key.gui.capture_panel import CapturePanel
from stroke_key.gui.errors import show_error
from stroke_key.services.enrollment import EnrollmentService


class EnrollmentDialog(QDialog):
    def __init__(self, service: EnrollmentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.sample_ids: list[str] = []
        self.setWindowTitle("StrokeKey — Enroll User")
        self.resize(850, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(RESEARCH_NOTICE))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Research participant name")
        self.name.setMaxLength(120)
        layout.addWidget(self.name)
        self.progress = QLabel("Capture sample 1 of 5")
        layout.addWidget(self.progress)
        self.capture = CapturePanel()
        layout.addWidget(self.capture, 1)
        self.samples_list = QListWidget()
        self.samples_list.setMaximumHeight(100)
        layout.addWidget(self.samples_list)
        self.save_button = QPushButton("Save Sample")
        self.save_button.clicked.connect(self.save_sample)
        layout.addWidget(self.save_button)
        self.retry_button = QPushButton("Retry selected saved sample")
        self.retry_button.clicked.connect(self.retry_sample)
        layout.addWidget(self.retry_button)
        self.finish_button = QPushButton("Complete Enrollment")
        self.finish_button.setEnabled(False)
        self.finish_button.clicked.connect(self.complete)
        layout.addWidget(self.finish_button)
        close_button = QPushButton("Close (unfinished samples remain local drafts)")
        close_button.clicked.connect(self.reject)
        layout.addWidget(close_button)

    def refresh(self) -> None:
        complete = len(self.sample_ids) == CAPTURE_CONFIG.enrollment_samples
        self.finish_button.setEnabled(complete)
        self.save_button.setEnabled(not complete)
        self.capture.setEnabled(not complete)
        self.progress.setText("Five samples saved. Complete enrollment." if complete else
                              f"Capture sample {len(self.sample_ids) + 1} of 5")

    def save_sample(self) -> None:
        sample = self.capture.validated_sample()
        if sample is None:
            return
        try:
            self.service.stage(sample)
            self.sample_ids.append(sample.sample_id)
            self.samples_list.addItem(f"Saved — {len(sample.points)} points, {sample.total_duration:.2f} s, "
                                      f"{sample.number_of_strokes} strokes")
            self.capture.canvas.clear()
            self.refresh()
        except Exception:
            show_error(self, "Could not save enrollment sample. Check the local database and retry.")

    def retry_sample(self) -> None:
        index = self.samples_list.currentRow()
        if index < 0:
            return
        try:
            self.service.samples.delete_unassigned(self.sample_ids[index])
            self.sample_ids.pop(index)
            self.samples_list.takeItem(index)
            self.capture.canvas.clear()
            self.refresh()
        except Exception:
            show_error(self, "Could not remove the draft sample. Retry after checking the database.")

    def complete(self) -> None:
        try:
            self.service.complete(self.name.text(), self.sample_ids)
            self.accept()
        except ValueError as exc:
            show_error(self, str(exc))
        except Exception:
            show_error(self, "Could not complete enrollment. Your saved drafts remain local.")

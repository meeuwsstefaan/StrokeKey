"""Simple local research workspace."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QMainWindow, QMessageBox, QPushButton, QVBoxLayout, QWidget

from stroke_key.config import RESEARCH_NOTICE
from stroke_key.gui.capture_panel import CapturePanel
from stroke_key.gui.enrollment_dialog import EnrollmentDialog
from stroke_key.gui.errors import show_error
from stroke_key.gui.sample_viewer import SampleViewer
from stroke_key.gui.verification_dialog import VerificationDialog
from stroke_key.services.enrollment import EnrollmentService
from stroke_key.services.verification import VerificationService
from stroke_key.storage.database import Database
from stroke_key.storage.repositories import SampleRepository, UserRepository


class CaptureDialog(QDialog):
    def __init__(self, samples: SampleRepository, parent=None) -> None:
        super().__init__(parent)
        self.samples = samples
        self.setWindowTitle("StrokeKey — Capture Sample")
        self.resize(850, 550)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(RESEARCH_NOTICE))
        self.capture = CapturePanel()
        layout.addWidget(self.capture, 1)
        save = QPushButton("Save Sample")
        save.clicked.connect(self.save)
        layout.addWidget(save)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        layout.addWidget(close)

    def save(self) -> None:
        sample = self.capture.validated_sample()
        if sample is None:
            return
        try:
            sample.metadata["purpose"] = "capture"
            self.samples.save(sample)
            self.capture.canvas.clear()
            QMessageBox.information(self, "Sample saved", "Raw signature measurements saved locally.")
        except Exception:
            show_error(self, "Could not save sample. Check the local database and retry.")


class MainWindow(QMainWindow):
    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database
        self.samples = SampleRepository(database)
        self.users = UserRepository(database)
        self.enrollment = EnrollmentService(self.users, self.samples)
        self.verification = VerificationService(self.samples)
        self.setWindowTitle("StrokeKey")
        self.resize(620, 480)
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(45, 35, 45, 35)
        title = QLabel("StrokeKey")
        title.setStyleSheet("font-size: 36px; font-weight: bold;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        subtitle = QLabel("Dynamic Signature Research Prototype")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)
        notice = QLabel(RESEARCH_NOTICE)
        notice.setWordWrap(True)
        notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(notice)
        layout.addSpacing(20)
        for text, action in (("Capture Sample", self.capture_sample), ("Enroll User", self.enroll_user),
                             ("Verify Signature", self.verify_signature), ("View Samples", self.view_samples),
                             ("Exit", self.close)):
            button = QPushButton(text)
            button.setMinimumHeight(40)
            button.clicked.connect(action)
            layout.addWidget(button)
        layout.addWidget(QLabel("Local data only · No telemetry · No password created"))
        self.setCentralWidget(widget)

    def capture_sample(self) -> None:
        CaptureDialog(self.samples, self).exec()

    def enroll_user(self) -> None:
        EnrollmentDialog(self.enrollment, self).exec()

    def verify_signature(self) -> None:
        try:
            VerificationDialog(self.verification, self.users.list_users(), self).exec()
        except Exception:
            show_error(self, "Could not load enrolled users. Check the local database.")

    def view_samples(self) -> None:
        try:
            SampleViewer(self.samples, self).exec()
        except Exception:
            show_error(self, "Could not open stored samples. Check the local database.")

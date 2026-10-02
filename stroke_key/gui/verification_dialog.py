"""Experimental comparison with transparent per-reference diagnostics."""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QPushButton, QTextEdit, QVBoxLayout

from stroke_key.config import RESEARCH_NOTICE
from stroke_key.gui.capture_panel import CapturePanel
from stroke_key.gui.errors import show_error
from stroke_key.models.user import User
from stroke_key.models.signature import SignatureSample
from stroke_key.services.verification import VerificationReport, VerificationService, compare_references


class ComparisonWorker(QThread):
    completed = Signal(object)
    failed = Signal()

    def __init__(self, candidate: SignatureSample, references: list[SignatureSample], parent=None) -> None:
        super().__init__(parent)
        self.candidate, self.references = candidate, references

    def run(self) -> None:
        try:
            self.completed.emit(compare_references(self.candidate, self.references))
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Comparison failed")
            self.failed.emit()


class VerificationDialog(QDialog):
    def __init__(self, service: VerificationService, users: list[User], parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.worker: ComparisonWorker | None = None
        self.setWindowTitle("StrokeKey — Verify Signature")
        self.resize(850, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(RESEARCH_NOTICE))
        self.users = QComboBox()
        for user in users:
            self.users.addItem(f"{user.name} ({user.user_id[:8]})", user.user_id)
        layout.addWidget(self.users)
        self.capture = CapturePanel()
        layout.addWidget(self.capture, 1)
        self.verify_button = QPushButton("Verify")
        self.verify_button.setEnabled(bool(users))
        self.verify_button.clicked.connect(self.verify)
        layout.addWidget(self.verify_button)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMaximumHeight(220)
        if not users:
            self.output.setPlainText("No enrolled users. Complete an enrollment first.")
        layout.addWidget(self.output)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)

    def verify(self) -> None:
        sample = self.capture.validated_sample()
        if sample is None:
            return
        try:
            references = self.service.prepare(self.users.currentData(), sample)
            self.output.setPlainText("Comparing with enrolled references…")
            self.set_busy(True)
            worker = ComparisonWorker(sample, references, self)
            self.worker = worker
            worker.completed.connect(self.show_report)
            worker.failed.connect(self.comparison_failed)
            worker.finished.connect(self.comparison_finished)
            worker.start()
        except ValueError as exc:
            show_error(self, str(exc))
        except Exception:
            show_error(self, "Could not read enrollment references. Check the local database.")

    def set_busy(self, busy: bool) -> None:
        self.verify_button.setEnabled(not busy)
        self.close_button.setEnabled(not busy)
        self.capture.setEnabled(not busy)
        self.users.setEnabled(not busy)

    def comparison_finished(self) -> None:
        self.set_busy(False)
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

    def reject(self) -> None:
        if self.worker is None or not self.worker.isRunning():
            super().reject()

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)

    def comparison_failed(self) -> None:
        self.output.setPlainText("Comparison failed. Consult the development log and retry.")

    def show_report(self, report: VerificationReport) -> None:
        result = report.result
        lines = [f"{'ACCEPTED' if result.accepted else 'REJECTED'} — experimental result",
                 f"Overall median similarity: {result.overall_score:.1%}",
                 f"DTW: {result.dtw_score:.1%} | Duration: {result.duration_score:.1%} | "
                 f"Geometry: {result.geometry_score:.1%} | Strokes: {result.stroke_score:.1%}", ""]
        for index, (_, comparison) in enumerate(report.comparisons, 1):
            d = comparison.diagnostics
            lines.append(f"Reference {index}: {comparison.overall_score:.1%}; DTW {comparison.dtw_score:.1%}; "
                         f"duration {d['candidate_duration']:.2f}/{d['reference_duration']:.2f} s; "
                         f"strokes {d['candidate_strokes']}/{d['reference_strokes']}")
        lines.append("\nVerification candidates are not saved automatically.")
        self.output.setPlainText("\n".join(lines))

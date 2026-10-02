"""Declare, compare, review and explicitly save a local research trial."""
import logging

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from stroke_key.config import RESEARCH_NOTICE
from stroke_key.gui.capture_panel import CapturePanel
from stroke_key.gui.errors import show_error
from stroke_key.models.user import User
from stroke_key.services.trials import PreparedTrial, TrialCollectionService, TrialDeclaration, compare_trial


class TrialWorker(QThread):
    completed = Signal(object)
    failed = Signal()

    def __init__(self, prepared: PreparedTrial, parent=None) -> None:
        super().__init__(parent)
        self.prepared = prepared

    def run(self) -> None:
        try:
            self.completed.emit(compare_trial(self.prepared))
        except Exception:
            logging.getLogger(__name__).exception("Trial comparison failed")
            self.failed.emit()


class TrialDialog(QDialog):
    def __init__(self, service: TrialCollectionService, users: list[User], parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.worker = self.prepared = self.trial = None
        self._pending_close = None
        self._closed = False
        self._has_users = bool(users)
        self.setWindowTitle("StrokeKey - Collect Research Trial")
        self.resize(1150, 790)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(RESEARCH_NOTICE))
        body = QHBoxLayout()
        self.capture = CapturePanel()
        # This workflow has one authoritative declaration rather than a second label.
        self.capture.research_label.setEnabled(False)
        self.capture.research_label.setToolTip("Set by the declared attempt type in the trial form.")
        body.addWidget(self.capture, 2)
        side = QVBoxLayout()
        self.declarations = QWidget()
        form = QFormLayout(self.declarations)
        self.claimed = QComboBox()
        self.signer = QComboBox()
        self.signer.addItem("Unknown / not recorded", None)
        for user in users:
            label = f"{user.name} ({user.user_id[:8]})"
            self.claimed.addItem(label, user.user_id)
            self.signer.addItem(label, user.user_id)
        form.addRow("Claimed participant:", self.claimed)
        form.addRow("Reported signer:", self.signer)
        self.attempt = QComboBox()
        for label, value in (("Unlabelled", "unlabelled"), ("Genuine", "genuine"),
                             ("Other participant", "other_participant"), ("Attempted imitation", "imitation")):
            self.attempt.addItem(label, value)
        form.addRow("Declared attempt:", self.attempt)
        self.synthetic = QCheckBox("Synthetic / demonstration capture")
        self.synthetic.setToolTip("Records a separate metadata flag; demonstration data must not establish real biometric performance.")
        form.addRow(self.synthetic)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Optional trial notes; avoid unnecessary identifying details.")
        self.notes.setMaximumHeight(90)
        form.addRow("Trial notes:", self.notes)
        self.consent = QCheckBox("Informed consent confirmed for this research capture")
        self.consent.setToolTip("Confirm consent from the people whose biometric data is collected or used.")
        form.addRow(self.consent)
        side.addWidget(self.declarations)
        help_text = QLabel("Declare the attempt before comparing. Labels are self-reported, not inferred. "
                           "Review the experimental result, then explicitly save the trial locally. "
                           "Saving never adds the candidate to enrollment.")
        help_text.setWordWrap(True)
        side.addWidget(help_text)
        self.compare_button = QPushButton("Compare trial (without saving)")
        self.compare_button.clicked.connect(self.compare)
        side.addWidget(self.compare_button)
        self.save_button = QPushButton("Save reviewed trial locally")
        self.save_button.clicked.connect(self.save)
        side.addWidget(self.save_button)
        self.discard_button = QPushButton("Discard unsaved trial / Clear")
        self.discard_button.clicked.connect(self.discard)
        side.addWidget(self.discard_button)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setAccessibleName("Research trial result")
        self.output.setPlainText("No enrolled participants. Complete enrollment first." if not users
                                 else "No trial saved. Capture a signature and complete the declaration.")
        side.addWidget(self.output, 1)
        body.addLayout(side, 1)
        layout.addLayout(body, 1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)
        self.attempt.currentIndexChanged.connect(self.sync_label)
        self.sync_label()
        self.refresh()

    def sync_label(self, *_args) -> None:
        value = self.attempt.currentData()
        label = self.capture.research_label
        if label.findData("other_participant") < 0:
            label.addItem("Other participant", "other_participant")
        label.setCurrentIndex(label.findData(value))

    def refresh(self) -> None:
        busy = self.worker is not None
        pending = self.prepared is not None
        self.capture.setEnabled(not busy and not pending and self._has_users)
        self.capture.research_label.setEnabled(False)
        self.declarations.setEnabled(not busy and not pending and self._has_users)
        self.compare_button.setEnabled(not busy and not pending and self._has_users)
        self.save_button.setEnabled(not busy and self.trial is not None)
        self.discard_button.setEnabled(not busy)

    def compare(self) -> None:
        if self.worker is not None or self.prepared is not None or not self._has_users:
            return
        sample = self.capture.validated_sample()
        if sample is None:
            return
        try:
            sample.metadata["synthetic"] = self.synthetic.isChecked()
            declaration = TrialDeclaration(self.claimed.currentData(), self.attempt.currentData(),
                                           self.consent.isChecked(), self.signer.currentData(),
                                           self.notes.toPlainText().strip())
            self.prepared = self.service.prepare(sample, declaration)
            worker = TrialWorker(self.prepared, self)
            self.worker = worker
            worker.completed.connect(self.compared)
            worker.failed.connect(self.comparison_failed)
            worker.finished.connect(self.comparison_finished)
            self.output.setPlainText("Comparing frozen enrollment references. Nothing has been saved.")
            self.refresh()
            worker.start()
        except ValueError as exc:
            self.prepared = None
            show_error(self, str(exc))
        except Exception:
            self.prepared = None
            show_error(self, "Could not prepare the trial. Check the local enrollment records.")

    def compared(self, trial) -> None:
        if self._closed or self._pending_close is not None:
            return
        self.trial = trial
        reference = self.prepared.reference_set
        result = trial.result
        lines = [f"{'ACCEPTED' if result['accepted'] else 'REJECTED'} - experimental result",
                 f"Overall median similarity: {result['overall_score']:.1%}",
                 f"DTW: {result['dtw_score']:.1%} | Duration: {result['duration_score']:.1%}",
                 f"Geometry: {result['geometry_score']:.1%} | Strokes: {result['stroke_score']:.1%}",
                 f"Declared attempt: {trial.attempt_type}",
                 f"Reference revision: {reference.revision} ({reference.reference_set_id})",
                 f"Matcher: {trial.matcher_version}; threshold: {trial.matcher_config['threshold']:.2f}",
                 f"Session: {trial.session_id}", ""]
        for sample_id, comparison in trial.comparisons.items():
            lines.append(f"Reference {sample_id[:8]}: {comparison['overall_score']:.1%}; DTW {comparison['dtw_score']:.1%}")
        lines.append("\nUNSAVED. Save this reviewed trial or discard it to capture again.")
        self.output.setPlainText("\n".join(lines))

    def comparison_failed(self) -> None:
        self.prepared = self.trial = None
        self.output.setPlainText("Comparison failed. Nothing saved. Consult the development log and retry.")

    def comparison_finished(self) -> None:
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        if self._pending_close is not None:
            self._closed = True
            super().done(self._pending_close)
        elif not self._closed:
            self.refresh()

    def save(self) -> None:
        if self.worker is not None or self.trial is None or self.prepared is None:
            return
        try:
            trial = self.service.save(self.prepared, self.trial)
        except ValueError as exc:
            show_error(self, str(exc))
            return
        except Exception:
            show_error(self, "Could not save the trial. The unsaved capture and reviewed result remain available for retry.")
            return
        self.prepared = self.trial = None
        self.capture.canvas.clear()
        self.consent.setChecked(False)
        self.synthetic.setChecked(False)
        self.notes.clear()
        self.output.setPlainText(f"Saved locally: {trial.trial_id}\nCandidate: {trial.candidate_sample_id}\n"
                                 "Raw measurements and result snapshots are immutable. Enrollment is unchanged.\n"
                                 "Consent must be confirmed again for the next trial.")
        self.refresh()

    def discard(self) -> None:
        if self.worker is not None:
            return
        self.prepared = self.trial = None
        self.capture.canvas.clear()
        self.consent.setChecked(False)
        self.synthetic.setChecked(False)
        self.output.setPlainText("Unsaved capture discarded. No trial written.")
        self.refresh()

    def done(self, result) -> None:
        if self.worker is not None and self.worker.isRunning():
            self._pending_close = result
            self.setEnabled(False)
            self.output.setPlainText("Closing after comparison finishes. Nothing will be saved.")
            return
        self._closed = True
        super().done(result)

    def reject(self) -> None:
        self.done(QDialog.DialogCode.Rejected)

    def accept(self) -> None:
        self.done(QDialog.DialogCode.Accepted)

    def closeEvent(self, event) -> None:
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
            self.reject()
        else:
            self._closed = True
            super().closeEvent(event)

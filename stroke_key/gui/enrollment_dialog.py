"""Guided enrollment, explicit keep/retry, and later-session extensions."""
import logging

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QPlainTextEdit, QPushButton, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from stroke_key.config import RESEARCH_NOTICE
from stroke_key.gui.capture_panel import CapturePanel
from stroke_key.gui.errors import show_error
from stroke_key.processing.enrollment_guidance import assess_enrollment
from stroke_key.services.enrollment import EnrollmentService


class GuidanceWorker(QThread):
    completed = Signal(int, object)
    failed = Signal(int)

    def __init__(self, generation, jobs, references, parent=None):
        super().__init__(parent)
        self.generation, self.jobs, self.references = generation, jobs, references

    def run(self):
        try:
            results = {kind: assess_enrollment(candidate, self.references)
                       for kind, candidate in self.jobs.items()}
            self.completed.emit(self.generation, results)
        except Exception:
            logging.getLogger(__name__).exception("Enrollment guidance failed")
            self.failed.emit(self.generation)


class EnrollmentDialog(QDialog):
    def __init__(self, service: EnrollmentService, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.sample_ids: list[str] = []
        self.worker = None
        self._generation = 0
        self._target_id = None
        self._pending_result = None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.start_guidance)
        self.setWindowTitle("StrokeKey — Guided Enrollment")
        self.resize(1280, 860)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(RESEARCH_NOTICE))
        participant_row = QHBoxLayout()
        participant_row.addWidget(QLabel("Participant:"))
        self.target_selector = QComboBox()
        self.target_selector.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.target_selector.setMinimumContentsLength(22)
        self.target_selector.addItem("New participant", None)
        for user in service.users.list_users():
            self.target_selector.addItem(f"{user.name} ({user.user_id[:8]})", user.user_id)
        participant_row.addWidget(self.target_selector, 1)
        self.name = QLineEdit()
        self.name.setPlaceholderText("New research participant name")
        self.name.setMaxLength(120)
        participant_row.addWidget(self.name, 1)
        layout.addLayout(participant_row)
        splitter = QSplitter()
        self.capture = CapturePanel()
        self.capture.research_label.removeItem(self.capture.research_label.findData("imitation"))
        splitter.addWidget(self.capture)
        sidebar = QWidget()
        sidebar.setMinimumWidth(380)
        sidebar_layout = QVBoxLayout(sidebar)
        self.progress = QLabel()
        self.progress.setWordWrap(True)
        self.progress.setTextFormat(Qt.TextFormat.PlainText)
        sidebar_layout.addWidget(self.progress)
        hint = QLabel("Sign naturally. Review capture problems without trying to reproduce a fixed signature. "
                      "Saved drafts stay local until you complete enrollment.")
        hint.setWordWrap(True)
        sidebar_layout.addWidget(hint)
        sidebar_layout.addWidget(QLabel("New drafts in this enrollment:"))
        self.samples_list = QListWidget()
        self.samples_list.setMinimumHeight(110)
        self.samples_list.setMaximumHeight(180)
        sidebar_layout.addWidget(self.samples_list)
        self.guidance_tabs = QTabWidget()
        self.current_guidance = QPlainTextEdit()
        self.current_guidance.setReadOnly(True)
        self.current_guidance.setPlainText("Finish a capture to see guidance before keeping or retrying it.")
        self.saved_guidance = QPlainTextEdit()
        self.saved_guidance.setReadOnly(True)
        self.saved_guidance.setPlainText("Select a saved draft for a comparison with the other references.")
        self.guidance_tabs.addTab(self.current_guidance, "Current capture")
        self.guidance_tabs.addTab(self.saved_guidance, "Selected saved sample")
        sidebar_layout.addWidget(self.guidance_tabs, 1)
        splitter.addWidget(sidebar)
        splitter.setSizes([800, 440])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        self.save_button = QPushButton("Keep and save sample")
        self.save_button.clicked.connect(self.save_sample)
        actions.addWidget(self.save_button)
        self.retry_button = QPushButton("Retry selected saved sample")
        self.retry_button.clicked.connect(self.retry_sample)
        actions.addWidget(self.retry_button)
        self.finish_button = QPushButton("Complete Enrollment")
        self.finish_button.clicked.connect(self.complete)
        actions.addWidget(self.finish_button)
        self.close_button = QPushButton("Close")
        self.close_button.setToolTip("Unfinished samples remain local drafts.")
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.target_selector.currentIndexChanged.connect(self.target_changed)
        self.name.textChanged.connect(self.refresh)
        self.samples_list.currentRowChanged.connect(self.selected_saved_changed)
        self.capture.canvas.changed.connect(self.schedule_guidance)
        self.refresh()

    def target_changed(self, *_args):
        if self.sample_ids:
            self.target_selector.blockSignals(True)
            self.target_selector.setCurrentIndex(self.target_selector.findData(self._target_id))
            self.target_selector.blockSignals(False)
            return
        self._target_id = self.target_selector.currentData()
        self.name.blockSignals(True)
        self.name.setText(self.service.users.get(self._target_id).name if self._target_id else "")
        self.name.blockSignals(False)
        self.name.setEnabled(self._target_id is None)
        self.finish_button.setText("Add to Enrollment" if self._target_id else "Complete Enrollment")
        self.refresh()

    def refresh(self, *_args):
        try:
            progress = self.service.progress(self.sample_ids, self._target_id)
            self.progress.setText(progress.summary())
            named = self._target_id is not None or bool(self.name.text().strip())
            self.finish_button.setEnabled(progress.ready and named)
        except ValueError as exc:
            self.progress.setText(str(exc))
            self.finish_button.setEnabled(False)
        self.target_selector.setEnabled(not self.sample_ids)
        self.retry_button.setEnabled(0 <= self.samples_list.currentRow() < len(self.sample_ids))
        for index in range(self.samples_list.count()):
            item = self.samples_list.item(index)
            item.setText(f"{index + 1}. {item.text().partition('. ')[2]}")
        self.schedule_guidance()

    def schedule_guidance(self, *_args):
        if self._closed or self._pending_result is not None:
            return
        self._generation += 1
        self._timer.start(180)
        if self.capture.canvas.recorder.active_device:
            self.current_guidance.setPlainText("Signing… consistency guidance will appear after release.")

    def selected_saved_changed(self, *_args):
        self.retry_button.setEnabled(0 <= self.samples_list.currentRow() < len(self.sample_ids))
        self.saved_guidance.setPlainText("Reviewing selected sample…" if self.samples_list.currentRow() >= 0
                                        else "Select a saved draft for a leave-one-out comparison.")
        self.schedule_guidance()

    def start_guidance(self):
        self._timer.stop()
        if self._closed or self.worker is not None:
            return
        try:
            references = self.service.guidance_references(self.sample_ids, self._target_id)
            jobs = {}
            candidate = self.capture.canvas.sample()
            if candidate.points and not self.capture.canvas.recorder.active_device:
                jobs["current"] = candidate
                self.current_guidance.setPlainText("Analyzing the current capture…")
            elif not candidate.points:
                self.current_guidance.setPlainText("Finish a capture to see guidance before keeping or retrying it.")
            index = self.samples_list.currentRow()
            if 0 <= index < len(self.sample_ids):
                jobs["saved"] = self.service.samples.get(self.sample_ids[index])
            if not jobs:
                return
            worker = GuidanceWorker(self._generation, jobs, references, self)
            self.worker = worker
            worker.completed.connect(self.show_guidance)
            worker.failed.connect(self.guidance_failed)
            worker.finished.connect(self.guidance_finished)
            worker.start()
        except Exception:
            logging.getLogger(__name__).exception("Could not load enrollment guidance references")
            self.current_guidance.setPlainText("Guidance could not be loaded. Your saved drafts remain available.")

    def show_guidance(self, generation, results):
        if generation != self._generation or self._closed:
            return
        if "current" in results:
            self.current_guidance.setPlainText(results["current"].summary())
        if "saved" in results:
            report = results["saved"]
            self.saved_guidance.setPlainText(report.summary())
            index = self.samples_list.currentRow()
            if 0 <= index < len(self.sample_ids):
                item = self.samples_list.item(index)
                sample_id = self.sample_ids[index]
                status = "Review suggested" if report.needs_review else "No review warnings"
                item.setText(f"{index + 1}. {sample_id[:8]} · {status}")

    def guidance_failed(self, generation):
        if generation == self._generation and not self._closed:
            message = "Guidance unavailable. Valid captures can still be kept; your saved drafts remain available."
            self.current_guidance.setPlainText(message)
            self.saved_guidance.setPlainText(message)

    def guidance_finished(self):
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        if self._pending_result is not None:
            self._closed = True
            super().done(self._pending_result)
        elif not self._closed and worker is not None and worker.generation != self._generation:
            self._timer.start(0)

    def save_sample(self) -> None:
        sample = self.capture.validated_sample()
        if sample is None:
            return
        try:
            self.service.stage(sample)
            self.sample_ids.append(sample.sample_id)
            self.samples_list.addItem(f"{len(self.sample_ids)}. {sample.sample_id[:8]} · "
                                      f"{sample.total_duration:.2f} s · {sample.number_of_strokes} strokes")
            self.capture.canvas.clear()
            self.samples_list.setCurrentRow(len(self.sample_ids) - 1)
            self.guidance_tabs.setCurrentIndex(1)
            self.refresh()
        except ValueError as exc:
            show_error(self, str(exc))
        except Exception:
            show_error(self, "Could not save enrollment sample. Check the local database and retry.")

    def retry_sample(self) -> None:
        index = self.samples_list.currentRow()
        if index < 0 or index >= len(self.sample_ids):
            return
        try:
            self.service.samples.delete_unassigned(self.sample_ids[index])
            self.sample_ids.pop(index)
            self.samples_list.takeItem(index)
            self.capture.canvas.clear()
            self.guidance_tabs.setCurrentIndex(0)
            self.refresh()
        except Exception:
            show_error(self, "Could not remove the draft sample. Retry after checking the database.")

    def complete(self) -> None:
        if self.capture.canvas.recorder.points or self.capture.canvas.recorder.active_device:
            show_error(self, "Keep and save the current capture, or clear it, before completing enrollment.")
            return
        try:
            if self._target_id is None:
                self.service.complete(self.name.text(), self.sample_ids)
            else:
                self.service.extend(self._target_id, self.sample_ids)
            self.accept()
        except ValueError as exc:
            show_error(self, str(exc))
        except Exception:
            show_error(self, "Could not complete enrollment. Your saved drafts remain local.")

    def done(self, result):
        self._timer.stop()
        self._generation += 1
        if self.worker is not None and self.worker.isRunning():
            self._pending_result = result
            self.setEnabled(False)
            return
        self._closed = True
        super().done(result)

    def accept(self):
        self.done(QDialog.DialogCode.Accepted)

    def reject(self):
        self.done(QDialog.DialogCode.Rejected)

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
            self.reject()
        else:
            self._closed = True
            self._timer.stop()
            super().closeEvent(event)

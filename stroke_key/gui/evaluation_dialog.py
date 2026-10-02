"""Local evaluation dashboard and immutable saved-run inspection."""
from dataclasses import asdict
import json
import logging

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import numpy as np
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QGridLayout, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from stroke_key.config import RESEARCH_NOTICE
from stroke_key.gui.errors import show_error
from stroke_key.models.user import User
from stroke_key.processing.evaluation import EVALUATION_VERSION, EvaluationSelection, evaluate_trials
from stroke_key.services.evaluation import EvaluationService, PreparedEvaluation


def rate(value) -> str:
    return "Unavailable" if value is None else f"{value:.2%}"


class EvaluationView(QWidget):
    def __init__(self, users: list[User], parent=None) -> None:
        super().__init__(parent)
        self.names = {user.user_id: user.name for user in users}
        layout = QVBoxLayout(self)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(170)
        layout.addWidget(self.summary)
        self.tabs = QTabWidget()
        self.figure = Figure(figsize=(9, 6), constrained_layout=True)
        self.plot = FigureCanvasQTAgg(self.figure)
        self.tabs.addTab(self.plot, "Distributions and error curves")
        self.participants = QTableWidget(0, 8)
        self.participants.setHorizontalHeaderLabels(["Claimed participant", "Genuine", "Impostor", "False rejects", "False accepts", "FRR", "FAR", "Sessions"])
        self.participants.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.participants.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.participants.horizontalHeader().setStretchLastSection(True)
        self.tabs.addTab(self.participants, "Per-participant counts")
        self.audit = QPlainTextEdit()
        self.audit.setReadOnly(True)
        self.tabs.addTab(self.audit, "Selection and trial audit")
        layout.addWidget(self.tabs, 1)
        self.clear("Select a device/matcher group and evaluate saved trials.")

    def clear(self, message: str) -> None:
        self.summary.setPlainText(message)
        self.figure.clear()
        self.plot.draw_idle()
        self.participants.setRowCount(0)
        self.audit.clear()

    def display(self, results: dict, snapshot: dict, heading: str) -> None:
        self.clear(heading)
        self.audit.setPlainText(json.dumps(snapshot, indent=2, ensure_ascii=False, allow_nan=False))
        if results.get("evaluation_version") != EVALUATION_VERSION:
            self.summary.setPlainText(heading + "\nLegacy or unsupported evaluator snapshot. Stored JSON is available in the audit tab.")
            return
        point = results["operating_point"]
        eer = results["approximate_eer"]
        demo_count = sum(row["synthetic"] for row in results["included_trials"])
        lines = [heading,
                 *([f"DEMONSTRATION DATA: {demo_count} trials use synthetic candidates or references; these rates do not establish biometric performance."] if demo_count else []),
                 f"Included: {results['included_count']} / {results['available_count']} available trials; excluded: {results['excluded_count']}",
                 f"Evaluation threshold: {point['threshold']:.4f} (score >= threshold accepts)",
                 f"Genuine: {point['genuine_count']}; false rejections: {point['false_rejections']}; FRR: {rate(point['frr'])}",
                 f"Impostor: {point['impostor_count']}; false acceptances: {point['false_acceptances']}; FAR: {rate(point['far'])}",
                 f"Approximate empirical EER: {rate(eer['rate']) if eer else 'Unavailable'}" +
                 (f" ({eer['method']}; not a calibrated threshold)" if eer else ""),
                 f"Known trial sessions: {results['session_count']}; frozen reference sets: {results['reference_set_count']}",
                 "Exclusions: " + (", ".join(f"{reason}: {count}" for reason, count in results['excluded_by_reason'].items()) or "none"),
                 *results["warnings"]]
        self.summary.setPlainText("\n".join(lines))
        grid = self.figure.add_gridspec(2, 2)
        distribution = self.figure.add_subplot(grid[0, :])
        roc = self.figure.add_subplot(grid[1, 0])
        sweep = self.figure.add_subplot(grid[1, 1])
        colors = {"genuine": "#247a89", "other_participant": "#d08c30", "imitation": "#b65369"}
        for attempt, values in results["distributions"].items():
            if values["scores"]:
                distribution.hist(values["scores"], bins=np.linspace(0, 1, 21), alpha=0.55,
                                  color=colors[attempt], label=f"{attempt.replace('_', ' ')} (n={values['count']})")
        distribution.axvline(point["threshold"], color="#8a7bbb", linestyle="--", label="Evaluation threshold")
        distribution.set(xlim=(0, 1), xlabel="Saved overall similarity", ylabel="Trial count",
                         title="Declared attempt score distributions" + (" (demonstration-only)" if demo_count else ""))
        distribution.legend(fontsize="small")
        curve = results["threshold_curve"]
        if point["genuine_count"] and point["impostor_count"]:
            roc.plot([row["far"] for row in curve], [1 - row["frr"] for row in curve], ".-", color="#247a89", label="Empirical ROC")
            roc.scatter([point["far"]], [1 - point["frr"]], color="#d08c30", label="Evaluation threshold", zorder=3)
            roc.legend(fontsize="small")
        else:
            roc.text(0.5, 0.5, "Both classes required\nROC unavailable", ha="center", transform=roc.transAxes)
        roc.set(xlim=(0, 1), ylim=(0, 1), xlabel="False acceptance rate", ylabel="True acceptance rate", title="ROC (includes reject-all endpoint)")
        thresholds = [row["threshold"] for row in curve if row["threshold"] is not None]
        for name, color in (("far", "#b65369"), ("frr", "#247a89")):
            if point[name] is not None:
                values = [row[name] for row in curve if row["threshold"] is not None]
                # Between adjacent observed scores, rates equal the next point's
                # value: acceptance is inclusive at the left score, then drops.
                # Include threshold 1 only if it has not already been sampled.
                plot_thresholds = thresholds + ([1.0] if thresholds[-1] < 1 else [])
                plot_values = values + ([curve[-1][name]] if thresholds[-1] < 1 else [])
                sweep.step(plot_thresholds, plot_values, where="pre", marker=".", color=color, label=name.upper())
                sweep.scatter([point["threshold"]], [point[name]], color=color, zorder=3)
        sweep.axvline(point["threshold"], color="#8a7bbb", linestyle="--", label="Evaluation threshold")
        sweep.set(xlim=(0, 1), ylim=(0, 1), xlabel="Sampled score threshold", ylabel="Empirical error rate", title="Threshold sweep (>= accepts ties)")
        sweep.legend(fontsize="small")
        self.plot.draw_idle()
        participants = results["participants"]
        self.participants.setRowCount(len(participants))
        for row_index, participant in enumerate(participants):
            user_id = participant["claimed_user_id"]
            cells = [f"{self.names.get(user_id, 'Participant')} ({user_id[:8]})",
                     str(participant["genuine_count"]), str(participant["impostor_count"]),
                     str(participant["false_rejections"]), str(participant["false_acceptances"]),
                     rate(participant["frr"]), rate(participant["far"]), str(participant["session_count"])]
            for column, value in enumerate(cells):
                self.participants.setItem(row_index, column, QTableWidgetItem(value))


class EvaluationWorker(QThread):
    completed = Signal(object)
    failed = Signal()

    def __init__(self, prepared: PreparedEvaluation, parent=None) -> None:
        super().__init__(parent)
        self.prepared = prepared

    def run(self) -> None:
        try:
            catalogue = self.prepared.catalogue
            self.completed.emit(evaluate_trials(catalogue.observations, self.prepared.selection, catalogue.unreadable_trial_ids))
        except Exception:
            logging.getLogger(__name__).exception("Evaluation failed")
            self.failed.emit()


class EvaluationDialog(QDialog):
    def __init__(self, service: EvaluationService, users: list[User], parent=None) -> None:
        super().__init__(parent)
        self.service, self.users = service, users
        self.worker = self.report = None
        self.cohorts = []
        self._saved = self._closed = False
        self._pending_close = None
        self.run_ids = []
        self.setWindowTitle("StrokeKey - Evaluate Research Trials")
        self.resize(1250, 940)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(RESEARCH_NOTICE + " Empirical evaluation of saved scores; no live threshold changes."))
        self.pages = QTabWidget()
        current = QWidget()
        current_layout = QVBoxLayout(current)
        self.controls = QWidget()
        grid = QGridLayout(self.controls)
        self.cohort = QComboBox()
        self.cohort.setMinimumContentsLength(55)
        self.cohort.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        grid.addWidget(QLabel("Exact device / matcher configuration:"), 0, 0)
        grid.addWidget(self.cohort, 0, 1, 1, 5)
        self.claimed = QComboBox()
        self.claimed.addItem("All claimed participants", None)
        for user in users:
            self.claimed.addItem(f"{user.name} ({user.user_id[:8]})", user.user_id)
        self.session = QComboBox()
        self.impostor = QComboBox()
        for label, value in (("Other participant + imitation", "all"), ("Other participant only", "other_participant"), ("Imitation only", "imitation")):
            self.impostor.addItem(label, value)
        grid.addWidget(QLabel("Claimed participant:"), 1, 0)
        grid.addWidget(self.claimed, 1, 1)
        grid.addWidget(QLabel("Trial session:"), 1, 2)
        grid.addWidget(self.session, 1, 3)
        grid.addWidget(QLabel("Impostor group:"), 1, 4)
        grid.addWidget(self.impostor, 1, 5)
        self.strict = QCheckBox("Require known, separate trial/reference sessions")
        self.strict.setChecked(True)
        self.synthetic = QCheckBox("Include synthetic / demonstration candidates or references")
        grid.addWidget(self.strict, 2, 0, 1, 3)
        grid.addWidget(self.synthetic, 2, 3, 1, 3)
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0, 1)
        self.threshold.setDecimals(4)
        self.threshold.setSingleStep(0.01)
        self.threshold.setValue(0.75)
        grid.addWidget(QLabel("Evaluation threshold:"), 3, 0)
        grid.addWidget(self.threshold, 3, 1)
        self.refresh_button = QPushButton("Refresh saved trials")
        self.refresh_button.clicked.connect(self.reload)
        self.evaluate_button = QPushButton("Evaluate selection (without saving)")
        self.evaluate_button.clicked.connect(self.evaluate)
        grid.addWidget(self.refresh_button, 3, 2, 1, 2)
        grid.addWidget(self.evaluate_button, 3, 4, 1, 2)
        current_layout.addWidget(self.controls)
        self.view = EvaluationView(users)
        current_layout.addWidget(self.view, 1)
        save_row = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name this evaluation before saving it locally")
        self.name.setMaxLength(160)
        self.save_button = QPushButton("Save evaluation run locally")
        self.save_button.clicked.connect(self.save)
        save_row.addWidget(self.name, 1)
        save_row.addWidget(self.save_button)
        current_layout.addLayout(save_row)
        self.pages.addTab(current, "Evaluate trials")
        saved = QWidget()
        saved_layout = QVBoxLayout(saved)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.runs = QListWidget()
        self.saved_view = EvaluationView(users)
        splitter.addWidget(self.runs)
        splitter.addWidget(self.saved_view)
        splitter.setSizes([220, 1000])
        splitter.setStretchFactor(1, 1)
        saved_layout.addWidget(splitter)
        self.pages.addTab(saved, "Saved evaluation runs")
        self.runs.currentRowChanged.connect(self.show_run)
        layout.addWidget(self.pages, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.cohort.currentIndexChanged.connect(self.cohort_changed)
        for selector in (self.claimed, self.session, self.impostor):
            selector.currentIndexChanged.connect(self.invalidate)
        self.strict.toggled.connect(self.invalidate)
        self.synthetic.toggled.connect(self.invalidate)
        self.threshold.valueChanged.connect(self.invalidate)
        self.reload()
        self.reload_runs()

    def invalidate(self, *_args) -> None:
        self.report = None
        self._saved = False
        self.view.clear("Selection changed. Evaluate to see results; nothing has been saved.")
        self.refresh_state()

    def refresh_state(self) -> None:
        busy = self.worker is not None
        self.controls.setEnabled(not busy)
        self.name.setEnabled(not busy)
        self.evaluate_button.setEnabled(not busy and bool(self.cohorts))
        self.save_button.setEnabled(not busy and self.report is not None and bool(self.report.trial_ids) and not self._saved)

    def reload(self) -> None:
        if self.worker is not None:
            return
        old_key, old_session = self.cohort.currentData(), self.session.currentData()
        self.invalidate()
        try:
            catalogue = self.service.catalogue()
        except Exception:
            self.cohorts = []
            self.cohort.clear()
            self.view.clear("Could not read the saved trial catalogue. Retry after checking the local database.")
            self.refresh_state()
            show_error(self, "Could not refresh saved research trials. No records were changed.")
            return
        self.cohorts = catalogue.cohorts
        self.cohort.blockSignals(True)
        self.cohort.clear()
        for index, cohort in enumerate(self.cohorts, 1):
            config = cohort.matcher_config
            ids = ", ".join(cohort.device_ids) or "unknown device ID"
            self.cohort.addItem(f"Group {index}: {cohort.device_type} [{ids}] | {cohort.matcher_version} | "
                                f"saved threshold {config['threshold']:g} | weights "
                                f"{config['dtw_weight']:g}/{config['duration_weight']:g}/{config['geometry_weight']:g}/{config['stroke_weight']:g} | "
                                f"scale {config['dtw_scale']:g}, limit {config['trajectory_limit']}", cohort.key)
        self.cohort.setCurrentIndex(max(0, self.cohort.findData(old_key)))
        self.cohort.blockSignals(False)
        self.session.blockSignals(True)
        self.session.clear()
        self.session.addItem("All trial sessions", None)
        for session in sorted({row.session_id for row in catalogue.observations if row.session_id}):
            self.session.addItem(session, session)
        self.session.setCurrentIndex(max(0, self.session.findData(old_session)))
        self.session.blockSignals(False)
        self.cohort_changed()
        if not self.cohorts:
            self.view.clear(f"No readable saved trials. Collect research trials first. Unreadable trials: {len(catalogue.unreadable_trial_ids)}.")
        self.refresh_state()

    def cohort_changed(self, *_args) -> None:
        index = self.cohort.currentIndex()
        if 0 <= index < len(self.cohorts):
            self.threshold.setValue(self.cohorts[index].matcher_config["threshold"])
        self.invalidate()

    def evaluate(self) -> None:
        if self.worker is not None or not self.cohorts:
            return
        self.report = None
        self._saved = False
        try:
            selection = EvaluationSelection(self.cohorts[self.cohort.currentIndex()], self.threshold.value(),
                                            self.claimed.currentData(), self.session.currentData(), self.impostor.currentData(),
                                            self.synthetic.isChecked(), self.strict.isChecked())
            prepared = self.service.prepare(selection)
            worker = EvaluationWorker(prepared, self)
            self.worker = worker
            worker.completed.connect(self.evaluated)
            worker.failed.connect(self.failed)
            worker.finished.connect(self.evaluation_finished)
            self.view.clear("Evaluating immutable saved scores. Nothing is written until you save the run.")
            self.refresh_state()
            worker.start()
        except ValueError as exc:
            show_error(self, str(exc))
            self.refresh_state()
        except Exception:
            show_error(self, "Could not load trial evidence for evaluation. Check the local database.")
            self.refresh_state()

    def evaluated(self, report) -> None:
        if self._closed or self._pending_close is not None:
            return
        self.report = report
        self.view.display(report.results, {"selection": asdict(report.selection), "trial_ids": report.trial_ids, "results": report.results}, "UNSAVED evaluation of frozen trial scores")

    def failed(self) -> None:
        self.report = None
        self.view.clear("Evaluation failed. Nothing was saved. Consult the development log and retry.")

    def evaluation_finished(self) -> None:
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        if self._pending_close is not None:
            self._closed = True
            super().done(self._pending_close)
        elif not self._closed:
            self.refresh_state()

    def save(self) -> None:
        if self.worker is not None or self.report is None or self._saved:
            return
        try:
            run = self.service.save(self.report, self.name.text())
        except ValueError as exc:
            show_error(self, str(exc))
            return
        except Exception:
            show_error(self, "Could not save the evaluation. The reviewed result remains available for retry.")
            return
        self._saved = True
        self.refresh_state()
        self.reload_runs()
        if run.run_id in self.run_ids:
            self.runs.setCurrentRow(self.run_ids.index(run.run_id))
        self.pages.setCurrentIndex(1)

    def reload_runs(self) -> None:
        self.runs.clear()
        self.run_ids = []
        self.saved_view.clear("No saved evaluation runs yet.")
        try:
            summaries = self.service.runs.list_summaries()
        except Exception:
            self.saved_view.clear("Could not load saved run identifiers. Retry after checking the local database.")
            show_error(self, "Could not load saved evaluation runs. No records were changed.")
            return
        for row in reversed(summaries):
            self.run_ids.append(row["run_id"])
            self.runs.addItem(f"{row['name']}\n{row['created_at'][:19]} | {row['run_id'][:8]}")

    def show_run(self, index: int) -> None:
        if not 0 <= index < len(self.run_ids):
            return
        try:
            run = self.service.runs.get(self.run_ids[index])
            self.saved_view.display(run.results, asdict(run), f"Saved: {run.name} | {run.created_at} | {run.run_id}")
        except Exception:
            self.saved_view.clear("This saved run cannot be displayed. Other saved runs remain available.")
            show_error(self, "Could not display this evaluation snapshot. No records were changed.")

    def done(self, result) -> None:
        if self.worker is not None and self.worker.isRunning():
            self._pending_close = result
            self.setEnabled(False)
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

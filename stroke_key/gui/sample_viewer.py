"""Raw samples and descriptive analysis against personal enrollment profiles."""
import json

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from stroke_key.gui.errors import show_error
from stroke_key.models.signature import SignatureSample
from stroke_key.models.user import User
from stroke_key.processing.analysis import analyze_sample
from stroke_key.processing.features import extract_features, velocity_series
from stroke_key.processing.normalize import normalize_signature
from stroke_key.storage.repositories import SampleRepository, StoredDataError


def stroke_groups(sample):
    groups = {}
    for point in sample.points:
        groups.setdefault(point.stroke_number, []).append(point)
    return list(groups.values())


def text_output():
    widget = QTextEdit()
    widget.setReadOnly(True)
    return widget


class SampleViewer(QDialog):
    def __init__(self, samples: SampleRepository, parent=None, *, users: list[User] | None = None) -> None:
        super().__init__(parent)
        self.samples = samples
        self.current_sample = self.report = None
        self.references = []
        self.setWindowTitle("StrokeKey — Stored Samples & Analysis")
        self.resize(1250, 900)
        layout = QVBoxLayout(self)
        splitter = QSplitter()
        self.list = QListWidget()
        self.list.setMinimumWidth(230)
        self.rows = samples.list_summaries()
        for row in self.rows:
            purpose = "research trial" if row["is_research_trial"] else "enrolled" if row["user_id"] else "unassigned / draft"
            self.list.addItem(f"{row['created_at'][:19]}\n{row['sample_id'][:8]} · {purpose}")
        splitter.addWidget(self.list)
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        self.metadata = QLabel("Select a sample." if self.rows else "No stored samples yet.")
        self.metadata.setWordWrap(True)
        self.metadata.setTextFormat(Qt.TextFormat.PlainText)
        self.metadata.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        panel_layout.addWidget(self.metadata)
        self.tabs = QTabWidget()
        raw_tab = QWidget()
        raw_layout = QVBoxLayout(raw_tab)
        self.figure = Figure(figsize=(7, 6), constrained_layout=True)
        self.plot = FigureCanvasQTAgg(self.figure)
        raw_layout.addWidget(self.plot, 1)
        self.tabs.addTab(raw_tab, "Raw sample")
        analysis_tab = QWidget()
        analysis_layout = QVBoxLayout(analysis_tab)
        selectors = QHBoxLayout()
        selectors.addWidget(QLabel("Enrollment profile:"))
        self.profile_selector = QComboBox()
        self.profile_selector.addItem("No enrollment profile", None)
        names = {u.user_id: u.name for u in users or []}
        for user_id in dict.fromkeys(row["user_id"] for row in self.rows if row["user_id"]):
            self.profile_selector.addItem(f"{names.get(user_id, 'Participant')} ({user_id[:8]})", user_id)
        selectors.addWidget(self.profile_selector, 1)
        selectors.addWidget(QLabel("Overlay:"))
        self.reference_selector = QComboBox()
        self.reference_selector.addItem("No overlay", None)
        selectors.addWidget(self.reference_selector, 1)
        analysis_layout.addLayout(selectors)
        self.profile_summary = QLabel("Select a sample to analyze.")
        self.profile_summary.setWordWrap(True)
        self.profile_summary.setTextFormat(Qt.TextFormat.PlainText)
        analysis_layout.addWidget(self.profile_summary)
        analysis_splitter = QSplitter(Qt.Orientation.Vertical)
        self.analysis_figure = Figure(figsize=(7, 4), constrained_layout=True)
        self.analysis_plot = FigureCanvasQTAgg(self.analysis_figure)
        analysis_splitter.addWidget(self.analysis_plot)
        self.detail_tabs = QTabWidget()
        self.feature_table = QTableWidget(0, 6)
        self.feature_table.setHorizontalHeaderLabels(["Feature", "Sample", "Median", "Observed range", "Refs", "Comparison"])
        self.feature_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.feature_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.feature_table.verticalHeader().hide()
        self.feature_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.feature_table.horizontalHeader().setStretchLastSection(True)
        self.detail_tabs.addTab(self.feature_table, "Feature comparisons")
        self.explanation = text_output()
        self.detail_tabs.addTab(self.explanation, "Measured explanations")
        self.quality_output = text_output()
        self.detail_tabs.addTab(self.quality_output, "Capture quality")
        analysis_splitter.addWidget(self.detail_tabs)
        analysis_splitter.setSizes([360, 260])
        analysis_layout.addWidget(analysis_splitter, 1)
        self.tabs.addTab(analysis_tab, "Analyze Sample")
        self.record_details = text_output()
        self.tabs.addTab(self.record_details, "Record metadata")
        panel_layout.addWidget(self.tabs, 1)
        splitter.addWidget(panel)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.profile_selector.currentIndexChanged.connect(self.update_analysis)
        self.reference_selector.currentIndexChanged.connect(self.draw_analysis)
        self.list.currentRowChanged.connect(self.show_sample)
        if self.rows:
            self.list.setCurrentRow(0)

    def show_sample(self, index: int) -> None:
        if index < 0 or index >= len(self.rows):
            return
        try:
            sample = self.samples.get(self.rows[index]["sample_id"])
            features = extract_features(sample)
            self.current_sample = sample
            self.metadata.setText(
                f"ID: {sample.sample_id}\nCreated: {sample.created_at}\n"
                f"User: {sample.user_id or 'unassigned'} | Device: {sample.device_type}\n"
                f"{len(sample.points)} points | {sample.number_of_strokes} strokes | "
                f"{sample.total_duration:.3f} s | Path: {features.total_path_length:.2f} px")
            self.record_details.setPlainText(json.dumps(sample.metadata, ensure_ascii=False, indent=2))
            self.draw_raw(sample)
            self.profile_selector.blockSignals(True)
            self.profile_selector.setCurrentIndex(max(0, self.profile_selector.findData(sample.user_id)))
            self.profile_selector.blockSignals(False)
            self.profile_selector.setEnabled(sample.user_id is None)
            self.update_analysis()
        except Exception:
            self.current_sample = self.report = None
            self.references = []
            self.figure.clear()
            self.analysis_figure.clear()
            self.plot.draw_idle()
            self.analysis_plot.draw_idle()
            self.feature_table.setRowCount(0)
            self.reference_selector.blockSignals(True)
            self.reference_selector.clear()
            self.reference_selector.addItem("No overlay", None)
            self.reference_selector.blockSignals(False)
            self.profile_summary.setText("Analysis unavailable for this record.")
            for widget in (self.explanation, self.quality_output, self.record_details):
                widget.clear()
            self.metadata.setText("This sample could not be loaded.")
            show_error(self, "Stored sample is unreadable or malformed. Other samples remain available.")

    def draw_raw(self, sample):
        self.figure.clear()
        signature, velocity, pressure = self.figure.subplots(3, 1)
        for points in stroke_groups(sample):
            signature.plot([p.x for p in points], [p.y for p in points], linewidth=1.5)
            times, speeds = velocity_series(SignatureSample(points=points))
            velocity.plot(times, speeds)
            valid = [p for p in points if p.pressure is not None]
            if valid:
                pressure.plot([p.elapsed_time for p in valid], [p.pressure for p in valid], ".-")
        signature.invert_yaxis()
        signature.set_aspect("equal", adjustable="datalim")
        signature.set_title("Raw signature (canvas pixels)")
        velocity.set_ylabel("Velocity (px/s)")
        velocity.set_xlabel("Elapsed seconds")
        pressure.set_ylabel("Qt pressure")
        pressure.set_xlabel("Elapsed seconds")
        if not any(p.pressure is not None for p in sample.points):
            pressure.text(0.5, 0.5, "Pressure unavailable", ha="center", transform=pressure.transAxes)
        self.plot.draw_idle()

    def update_analysis(self, *_args):
        if self.current_sample is None:
            return
        user_id = self.profile_selector.currentData()
        references, unreadable = [], 0
        for row in self.rows:
            if user_id is not None and row["user_id"] == user_id:
                try:
                    references.append(self.samples.get(row["sample_id"]))
                except StoredDataError:
                    unreadable += 1
        self.report = analyze_sample(self.current_sample, references)
        profile = self.report.profile
        ids = set(profile.reference_ids)
        self.references = [sample for sample in references if sample.sample_id in ids]
        self.reference_selector.blockSignals(True)
        self.reference_selector.clear()
        self.reference_selector.addItem("No overlay", None)
        for reference in self.references:
            self.reference_selector.addItem(f"{reference.sample_id[:8]} · {reference.total_duration:.2f} s", reference.sample_id)
        if self.references:
            self.reference_selector.setCurrentIndex(1)
        self.reference_selector.blockSignals(False)
        skipped = f"\n{unreadable} unreadable references skipped." if unreadable else ""
        self.profile_summary.setText(
            f"Descriptive profile v{profile.version} · {profile.device_type} · {profile.sample_count} references · "
            f"{profile.session_count} known sessions\n"
            "Preliminary observed ranges; selected sample excluded. No trained model or authenticity probability." + skipped)
        self.quality_output.setPlainText(self.report.quality.summary())
        self.explanation.setPlainText("\n\n".join(self.report.explanations) + skipped)
        self.feature_table.setRowCount(len(self.report.comparisons))
        for row, comparison in enumerate(self.report.comparisons):
            def formatted(value):
                return "Unavailable" if value is None else f"{value:.3g}"
            span = "—" if comparison.minimum is None else f"{comparison.minimum:.3g}–{comparison.maximum:.3g}"
            cells = [f"{comparison.label} ({comparison.unit})", formatted(comparison.value),
                     formatted(comparison.reference_median), span, str(comparison.reference_count), comparison.status]
            for column, value in enumerate(cells):
                self.feature_table.setItem(row, column, QTableWidgetItem(value))
        self.draw_analysis()

    def draw_analysis(self, *_args):
        if self.current_sample is None or self.report is None:
            return
        self.analysis_figure.clear()
        signature, velocity = self.analysis_figure.subplots(2, 1)
        if self.report.quality.errors:
            signature.text(0.5, 0.5, "Capture invalid — see Capture quality", ha="center", transform=signature.transAxes)
            self.analysis_plot.draw_idle()
            return
        candidate = normalize_signature(self.current_sample)
        segments, speeds = [], []
        for points in stroke_groups(candidate):
            signature.plot([p.x for p in points], [p.y for p in points], color="#697586", linewidth=1, alpha=0.5)
            for a, b in zip(points, points[1:]):
                dt = b.elapsed_time - a.elapsed_time
                if dt > 0:
                    segments.append([(a.x, a.y), (b.x, b.y)])
                    speeds.append(((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5 / dt)
            times, values = velocity_series(SignatureSample(points=points))
            velocity.plot(times, values, color="#247a89")
        if segments:
            collection = LineCollection(segments, cmap="viridis", norm=Normalize(0, max(speeds) or 1), linewidths=2)
            collection.set_array(speeds)
            signature.add_collection(collection)
            self.analysis_figure.colorbar(collection, ax=signature, label="Normalized speed (units/s)", pad=0.02)
        reference_id = self.reference_selector.currentData()
        reference = next((sample for sample in self.references if sample.sample_id == reference_id), None)
        if reference:
            normalized = normalize_signature(reference)
            for index, points in enumerate(stroke_groups(normalized)):
                signature.plot([p.x for p in points], [p.y for p in points], "--", color="#df7132", alpha=0.8,
                               linewidth=1.5, label="Enrollment reference" if index == 0 else None)
                times, values = velocity_series(SignatureSample(points=points))
                velocity.plot(times, values, "--", color="#df7132", alpha=0.8)
        start_time = self.current_sample.points[0].elapsed_time
        for index, pause in enumerate(self.report.quality.pauses):
            velocity.axvspan(pause.start - start_time, pause.end - start_time, color="#dca63f", alpha=0.25)
            point = min(candidate.points, key=lambda p: abs(p.elapsed_time - (pause.start - start_time)))
            signature.scatter([point.x], [point.y], facecolors="none", edgecolors="#b77d00", s=65,
                              label="Pause start" if index == 0 else None)
        if reference or self.report.quality.pauses:
            signature.legend(loc="upper right", fontsize="small")
        signature.autoscale_view()
        signature.invert_yaxis()
        signature.set_aspect("equal", adjustable="datalim")
        signature.set_title("Normalized signature · speed colours · circles mark pause starts", fontsize=10)
        velocity.set_ylabel("Speed (units/s)")
        velocity.set_xlabel("Seconds from first point · shaded intervals are detected pauses")
        self.analysis_plot.draw_idle()

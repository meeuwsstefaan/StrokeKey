"""Stored raw signature and dynamic plots in a compact viewer."""
import json

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QListWidget, QPushButton, QSplitter, QVBoxLayout, QWidget

from stroke_key.gui.errors import show_error
from stroke_key.processing.features import extract_features, velocity_series
from stroke_key.storage.repositories import SampleRepository


class SampleViewer(QDialog):
    def __init__(self, samples: SampleRepository, parent=None) -> None:
        super().__init__(parent)
        self.samples = samples
        self.setWindowTitle("StrokeKey — Stored Samples")
        self.resize(1100, 750)
        layout = QVBoxLayout(self)
        splitter = QSplitter()
        self.list = QListWidget()
        self.rows = samples.list_summaries()
        for row in self.rows:
            purpose = "enrolled" if row["user_id"] else "unassigned / draft"
            self.list.addItem(f"{row['created_at'][:19]}\n{row['sample_id'][:8]} · {purpose}")
        splitter.addWidget(self.list)
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        self.metadata = QLabel("Select a sample." if self.rows else "No stored samples yet.")
        self.metadata.setWordWrap(True)
        self.metadata.setTextFormat(Qt.TextFormat.PlainText)
        self.metadata.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        panel_layout.addWidget(self.metadata)
        self.figure = Figure(figsize=(7, 6), constrained_layout=True)
        self.plot = FigureCanvasQTAgg(self.figure)
        panel_layout.addWidget(self.plot, 1)
        splitter.addWidget(panel)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
        self.list.currentRowChanged.connect(self.show_sample)
        if self.rows:
            self.list.setCurrentRow(0)

    def show_sample(self, index: int) -> None:
        if index < 0:
            return
        try:
            sample = self.samples.get(self.rows[index]["sample_id"])
            features = extract_features(sample)
            self.metadata.setText(
                f"ID: {sample.sample_id}\nCreated: {sample.created_at}\n"
                f"User: {sample.user_id or 'unassigned'} | Device: {sample.device_type}\n"
                f"{len(sample.points)} points | {sample.number_of_strokes} strokes | "
                f"{sample.total_duration:.3f} s | Path: {features.total_path_length:.2f} px\n"
                f"Metadata: {json.dumps(sample.metadata, ensure_ascii=False)}")
            self.figure.clear()
            signature, velocity, pressure = self.figure.subplots(3, 1)
            for stroke in sorted({p.stroke_number for p in sample.points}):
                points = [p for p in sample.points if p.stroke_number == stroke]
                signature.plot([p.x for p in points], [p.y for p in points], linewidth=1.5)
                valid_pressure = [p for p in points if p.pressure is not None]
                if valid_pressure:
                    pressure.plot([p.elapsed_time for p in valid_pressure], [p.pressure for p in valid_pressure], ".-")
            signature.invert_yaxis()
            signature.set_aspect("equal", adjustable="datalim")
            signature.set_title("Raw signature (canvas pixels)")
            times, speeds = velocity_series(sample)
            velocity.plot(times, speeds)
            velocity.set_ylabel("Velocity (px/s)")
            velocity.set_xlabel("Elapsed seconds")
            pressure.set_ylabel("Qt pressure")
            pressure.set_xlabel("Elapsed seconds")
            if features.average_pressure is None:
                pressure.text(0.5, 0.5, "Pressure unavailable", ha="center", transform=pressure.transAxes)
            self.plot.draw_idle()
        except Exception:
            self.figure.clear()
            self.plot.draw_idle()
            self.metadata.setText("This sample could not be loaded.")
            show_error(self, "Stored sample is unreadable or malformed. Other samples remain available.")

"""Read saved trial evidence without rerunning the matcher or changing records."""
from dataclasses import asdict
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QLabel, QListWidget, QPlainTextEdit,
                               QPushButton, QSplitter, QVBoxLayout)

from stroke_key.gui.errors import show_error
from stroke_key.gui.sample_viewer import SampleViewer
from stroke_key.models.user import User
from stroke_key.storage.research_repositories import ResearchTrialRepository


class TrialViewer(QDialog):
    def __init__(self, trials: ResearchTrialRepository, users: list[User], parent=None) -> None:
        super().__init__(parent)
        self.trials, self.users = trials, users
        self.ids = []
        self.current = None
        self.setWindowTitle("StrokeKey - Saved Research Trials")
        self.resize(1100, 750)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Immutable local snapshots; declared labels are self-reported. Scores are experimental."))
        self.claimed = QComboBox()
        self.claimed.addItem("All claimed participants", None)
        for user in users:
            self.claimed.addItem(f"{user.name} ({user.user_id[:8]})", user.user_id)
        layout.addWidget(self.claimed)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        splitter.addWidget(self.list)
        splitter.addWidget(self.details)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.view_button = QPushButton("View candidate measurements")
        self.view_button.clicked.connect(self.view_candidate)
        layout.addWidget(self.view_button)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        layout.addWidget(close)
        self.claimed.currentIndexChanged.connect(self.reload)
        self.list.currentRowChanged.connect(self.show_trial)
        self.reload()

    def reload(self, *_args) -> None:
        self.list.clear()
        self.ids = []
        self.current = None
        self.view_button.setEnabled(False)
        self.details.setPlainText("No saved research trials for this selection.")
        # Load identifiers separately so one unreadable snapshot does not hide the others.
        claimed = self.claimed.currentData()
        rows = list(reversed(self.trials.list_summaries(claimed)))
        self.ids = [row["trial_id"] for row in rows]
        for row in rows:
            self.list.addItem(f"{row['created_at'][:19]} | {row['attempt_type']}\n{row['trial_id'][:8]}")
        if rows:
            self.list.setCurrentRow(0)

    def show_trial(self, index: int) -> None:
        self.current = None
        self.view_button.setEnabled(False)
        if not 0 <= index < len(self.ids):
            return
        try:
            self.current = self.trials.get(self.ids[index])
            reference = self.trials.references.get(self.current.reference_set_id)
            names = {user.user_id: user.name for user in self.users}
            snapshot = {"claimed_participant_name": names.get(self.current.claimed_user_id),
                        "reported_signer_name": names.get(self.current.actual_signer_id),
                        "trial": asdict(self.current), "frozen_reference_set": asdict(reference)}
            self.details.setPlainText(json.dumps(snapshot, indent=2, ensure_ascii=False, allow_nan=False))
            self.view_button.setEnabled(True)
        except Exception:
            self.current = None
            self.details.setPlainText("This saved trial is unreadable. Other trials remain available.")
            show_error(self, "Could not read this saved trial snapshot. No records were changed.")

    def view_candidate(self) -> None:
        if self.current is None:
            return
        try:
            viewer = SampleViewer(self.trials.samples, self, users=self.users)
            index = next(i for i, row in enumerate(viewer.rows) if row["sample_id"] == self.current.candidate_sample_id)
            viewer.list.setCurrentRow(index)
            viewer.exec()
        except Exception:
            show_error(self, "Could not display the trial candidate measurements.")

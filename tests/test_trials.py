"""Synthetic collection evidence, atomic saves and reproducible frozen inputs."""
from copy import deepcopy
from dataclasses import asdict, replace
from pathlib import Path
import os
import sqlite3
from threading import Event
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from stroke_key.gui.trial_dialog import TrialDialog
from stroke_key.gui.trial_viewer import TrialViewer

from stroke_key.config import MATCH_CONFIG
from stroke_key.models.research import EvaluationRun
from stroke_key.processing.matcher import MATCHER_VERSION
from stroke_key.services.enrollment import EnrollmentService
from stroke_key.services.trials import TrialCollectionService, TrialDeclaration, compare_trial
from stroke_key.services.verification import VerificationService
from stroke_key.storage.database import Database
from stroke_key.storage.repositories import SampleRepository, UserRepository
from stroke_key.storage.research_repositories import EvaluationRunRepository


@pytest.fixture
def collection(tmp_path, sample_factory):
    database = Database(tmp_path / "trials.db")
    samples, users = SampleRepository(database), UserRepository(database)
    enrollment = EnrollmentService(users, samples)
    participants = []
    for name in ("Synthetic claimant", "Synthetic signer"):
        drafts = [sample_factory(duration=1 + i / 10) for i in range(5)]
        for draft in drafts:
            draft.metadata.update(session_id="enrollment-session", research_label="synthetic")
            enrollment.stage(draft)
        participants.append(enrollment.complete(name, [s.sample_id for s in drafts]))
    candidate = sample_factory(duration=1.2)
    candidate.metadata.update(session_id="trial-session", research_label="unlabelled")
    service = TrialCollectionService(database)
    declaration = TrialDeclaration(participants[0].user_id, "genuine", True, participants[0].user_id, "Synthetic trial")
    yield SimpleNamespace(db=database, samples=samples, users=users, enrollment=enrollment,
                          service=service, declaration=declaration, candidate=candidate, participants=participants)
    database.close()


def rows(connection):
    return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("users", "signature_samples", "signature_points", "reference_sets", "reference_set_samples",
                          "research_trials", "evaluation_runs", "evaluation_run_trials")}


def test_compare_does_not_write_and_save_preserves_existing_records(collection):
    c = collection
    before = rows(c.db.connection)
    original = deepcopy(c.candidate)
    prepared = c.service.prepare(c.candidate, c.declaration)
    trial = compare_trial(prepared)
    assert rows(c.db.connection) == before
    assert c.candidate == original
    assert prepared.candidate.metadata["research_label"] == "genuine"
    assert c.service.save(prepared, trial) == trial
    after = rows(c.db.connection)
    for table in ("users", "reference_sets", "reference_set_samples", "evaluation_runs", "evaluation_run_trials"):
        assert after[table] == before[table]
    assert after["signature_samples"][:-1] == before["signature_samples"]
    assert after["signature_points"][:-60] == before["signature_points"]
    candidate = c.samples.get(trial.candidate_sample_id)
    assert candidate.points == original.points
    assert candidate.user_id is None
    assert c.samples.is_trial(candidate.sample_id)
    assert len(VerificationService(c.samples).prepare(c.declaration.claimed_user_id, original)) == 5
    with pytest.raises(sqlite3.IntegrityError):
        c.service.save(prepared, trial)
    assert c.service.trials.list_trials() == [trial]


@pytest.mark.parametrize("attempt,signer", [("genuine", "same"), ("genuine", None),
                                          ("other_participant", "other"), ("imitation", "other"),
                                          ("imitation", None), ("unlabelled", None)])
def test_supported_declarations(collection, attempt, signer):
    c = collection
    signer_id = {"same": c.participants[0].user_id, "other": c.participants[1].user_id, None: None}[signer]
    declaration = replace(c.declaration, attempt_type=attempt, actual_signer_id=signer_id)
    prepared = c.service.prepare(c.candidate, declaration)
    trial = compare_trial(prepared)
    assert c.service.save(prepared, trial).attempt_type == attempt
    assert c.service.trials.get(trial.trial_id).actual_signer_id == signer_id


@pytest.mark.parametrize("change", [dict(consent_confirmed=False), dict(attempt_type="synthetic"),
                                    dict(attempt_type="other_participant", actual_signer_id=None),
                                    dict(attempt_type="imitation"), dict(actual_signer_id="missing"),
                                    dict(claimed_user_id="missing"), dict(notes=None)])
def test_bad_declaration_does_not_write(collection, change):
    c = collection
    before = rows(c.db.connection)
    with pytest.raises(ValueError):
        c.service.prepare(c.candidate, replace(c.declaration, **change))
    assert rows(c.db.connection) == before


def test_conflicting_genuine_signer_rejected(collection):
    c = collection
    with pytest.raises(ValueError, match="genuine"):
        c.service.prepare(c.candidate, replace(c.declaration, actual_signer_id=c.participants[1].user_id))


@pytest.mark.parametrize("change", ["no_session", "assigned", "invalid", "other_device", "mixed", "stored", "incomplete"])
def test_ineligible_candidates_do_not_write(collection, change):
    c = collection
    candidate = deepcopy(c.candidate)
    if change == "no_session":
        candidate.metadata.pop("session_id")
    elif change == "assigned":
        candidate.user_id = c.declaration.claimed_user_id
    elif change == "invalid":
        candidate.points = candidate.points[:2]
    elif change in {"other_device", "mixed"}:
        candidate.device_type = "stylus" if change == "other_device" else "mixed"
    elif change == "stored":
        c.samples.save(candidate)
    else:
        reference = c.service.references.latest_for_user(c.declaration.claimed_user_id)[0]
        c.service.references.create(c.declaration.claimed_user_id, reference.sample_ids[:4])
    before = rows(c.db.connection)
    with pytest.raises(ValueError):
        c.service.prepare(candidate, c.declaration)
    assert rows(c.db.connection) == before


def test_frozen_capture_config_and_reference_survive_later_enrollment(collection, sample_factory):
    c = collection
    c.service.config = replace(MATCH_CONFIG, threshold=0.99, dtw_weight=0.5, duration_weight=0.2)
    prepared = c.service.prepare(c.candidate, c.declaration)
    expected = compare_trial(prepared)
    c.candidate.points.clear()
    c.candidate.metadata["session_id"] = "changed"
    c.service.config = MATCH_CONFIG
    draft = sample_factory()
    draft.metadata.update(session_id="later-enrollment", research_label="synthetic")
    c.enrollment.stage(draft)
    c.enrollment.extend(c.declaration.claimed_user_id, [draft.sample_id])
    assert c.service.references.latest_for_user(c.declaration.claimed_user_id)[0].revision == 2
    trial = compare_trial(prepared)
    assert trial.result == expected.result
    assert trial.comparisons == expected.comparisons
    assert len(trial.comparisons) == 5
    assert trial.matcher_config == asdict(prepared.config)
    assert c.service.save(prepared, trial) == trial
    assert c.service.trials.get(trial.trial_id).session_id == "trial-session"
    other = Database(Path(c.db.connection.execute("PRAGMA database_list").fetchone()[2]))
    try:
        assert TrialCollectionService(other).trials.get(trial.trial_id) == trial
    finally:
        other.close()


def test_save_failure_rolls_back_candidate_and_allows_retry(collection):
    c = collection
    prepared = c.service.prepare(c.candidate, c.declaration)
    trial = compare_trial(prepared)
    before = rows(c.db.connection)
    c.db.connection.execute("CREATE TEMP TRIGGER fail_trial BEFORE INSERT ON research_trials "
                            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        c.service.save(prepared, trial)
    assert rows(c.db.connection) == before
    c.db.connection.execute("DROP TRIGGER fail_trial")
    assert c.service.save(prepared, trial) == trial


def test_existing_trial_and_evaluation_unchanged_by_collection(collection, sample_factory):
    c = collection
    prepared = c.service.prepare(c.candidate, c.declaration)
    first = c.service.save(prepared, compare_trial(prepared))
    runs = EvaluationRunRepository(c.db)
    run = runs.save(EvaluationRun("Synthetic baseline", (first.trial_id,), {}, MATCHER_VERSION,
                                  asdict(MATCH_CONFIG), {"count": 1}))
    second = sample_factory()
    second.metadata["session_id"] = "another-session"
    prepared = c.service.prepare(second, c.declaration)
    c.service.save(prepared, compare_trial(prepared))
    assert c.service.trials.get(first.trial_id) == first
    assert runs.get(run.run_id) == run


def test_save_rejects_changed_declaration_or_matcher(collection):
    c = collection
    prepared = c.service.prepare(c.candidate, c.declaration)
    trial = compare_trial(prepared)
    for bad in (replace(trial, notes="changed"), replace(trial, attempt_type="unlabelled"),
                replace(trial, matcher_version="changed"), replace(trial, matcher_config=asdict(replace(MATCH_CONFIG, threshold=0.5)))):
        with pytest.raises(ValueError, match="snapshot"):
            c.service.save(prepared, bad)
    assert not c.service.trials.list_trials()


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def setup_dialog(collection):
    c = collection
    dialog = TrialDialog(c.service, c.users.list_users())
    dialog.claimed.setCurrentIndex(dialog.claimed.findData(c.declaration.claimed_user_id))
    dialog.signer.setCurrentIndex(dialog.signer.findData(c.declaration.actual_signer_id))
    dialog.attempt.setCurrentIndex(dialog.attempt.findData("genuine"))
    dialog.consent.setChecked(True)
    dialog.synthetic.setChecked(True)
    dialog.capture.canvas.recorder.points = [replace(point, device_type="synthetic") for point in c.candidate.points]
    dialog.capture.canvas.update()
    dialog.capture.update_status()
    return dialog


def finish_comparison(dialog, app):
    assert dialog.worker is not None
    assert dialog.worker.wait(15_000)
    app.processEvents()
    assert dialog.worker is None


def test_gui_review_save_retry_and_viewer(app, collection, monkeypatch):
    c = collection
    errors = []
    monkeypatch.setattr("stroke_key.gui.trial_dialog.show_error", lambda _, message: errors.append(message))
    dialog = setup_dialog(c)
    dialog.show()
    app.processEvents()
    dialog.compare_button.click()
    assert not dialog.capture.isEnabled()
    assert not dialog.declarations.isEnabled()
    finish_comparison(dialog, app)
    assert "UNSAVED" in dialog.output.toPlainText()
    assert dialog.save_button.isEnabled()
    assert not dialog.compare_button.isEnabled()
    assert not c.service.trials.list_trials()
    frozen_trial = dialog.trial
    c.db.connection.execute("CREATE TEMP TRIGGER fail_trial BEFORE INSERT ON research_trials "
                            "BEGIN SELECT RAISE(ABORT, 'injected'); END")
    dialog.save_button.click()
    assert errors and "retry" in errors[-1]
    assert dialog.trial == frozen_trial
    assert dialog.save_button.isEnabled()
    assert not c.service.trials.list_trials()
    c.db.connection.execute("DROP TRIGGER fail_trial")
    session = dialog.capture.canvas.session_id
    dialog.save_button.click()
    assert c.service.trials.list_trials() == [frozen_trial]
    assert c.samples.get(frozen_trial.candidate_sample_id).metadata["synthetic"] is True
    assert dialog.trial is None and not dialog.save_button.isEnabled()
    assert dialog.capture.isEnabled()
    assert not dialog.capture.canvas.recorder.points
    assert not dialog.consent.isChecked()
    assert not dialog.synthetic.isChecked()
    assert dialog.capture.canvas.session_id == session
    viewer = TrialViewer(c.service.trials, c.users.list_users())
    assert viewer.current == frozen_trial
    assert frozen_trial.reference_set_id in viewer.details.toPlainText()
    assert '"matcher_config"' in viewer.details.toPlainText()
    assert '"consent_confirmed": true' in viewer.details.toPlainText()
    viewer.claimed.setCurrentIndex(viewer.claimed.findData(c.participants[1].user_id))
    assert viewer.list.count() == 0
    assert not viewer.view_button.isEnabled()
    assert "No saved" in viewer.details.toPlainText()
    dialog.close()
    viewer.close()


def test_gui_consent_required_and_discard_leaves_no_records(app, collection, monkeypatch):
    c = collection
    errors = []
    monkeypatch.setattr("stroke_key.gui.trial_dialog.show_error", lambda _, message: errors.append(message))
    dialog = setup_dialog(c)
    dialog.consent.setChecked(False)
    dialog.compare()
    assert dialog.worker is None
    assert errors and "consent" in errors[-1]
    dialog.consent.setChecked(True)
    dialog.compare()
    finish_comparison(dialog, app)
    session = dialog.capture.canvas.session_id
    dialog.discard_button.click()
    assert dialog.prepared is None and dialog.trial is None
    assert not dialog.consent.isChecked()
    assert dialog.capture.canvas.session_id == session
    assert not c.service.trials.list_trials()
    dialog.capture.new_session_button.click()
    assert dialog.capture.canvas.session_id != session
    dialog.close()


def test_gui_close_during_worker_waits_without_saving(app, collection, monkeypatch):
    import stroke_key.gui.trial_dialog as gui
    entered, release = Event(), Event()
    original = gui.compare_trial

    def delayed(prepared):
        entered.set()
        assert release.wait(15)
        return original(prepared)

    monkeypatch.setattr(gui, "compare_trial", delayed)
    dialog = setup_dialog(collection)
    dialog.show()
    dialog.compare()
    assert entered.wait(5)
    try:
        dialog.close()
        assert dialog._pending_close is not None
        assert dialog.isVisible()
    finally:
        release.set()
        finish_comparison(dialog, app)
    assert not dialog.isVisible()
    assert not collection.service.trials.list_trials()


def test_gui_unreadable_trial_does_not_hide_other_records(app, collection, monkeypatch):
    c = collection
    prepared = c.service.prepare(c.candidate, c.declaration)
    first = c.service.save(prepared, compare_trial(prepared))
    second_candidate = replace(c.candidate, sample_id="second-synthetic-candidate")
    prepared = c.service.prepare(second_candidate, c.declaration)
    second = c.service.save(prepared, compare_trial(prepared))
    original = c.service.trials.get

    def get(trial_id):
        if trial_id == second.trial_id:
            raise ValueError("Injected unreadable snapshot")
        return original(trial_id)

    monkeypatch.setattr(c.service.trials, "get", get)
    monkeypatch.setattr("stroke_key.gui.trial_viewer.show_error", lambda *args: None)
    viewer = TrialViewer(c.service.trials, c.users.list_users())
    assert viewer.list.count() == 2
    assert viewer.current is None and not viewer.view_button.isEnabled()
    viewer.list.setCurrentRow(viewer.ids.index(first.trial_id))
    assert viewer.current == first and viewer.view_button.isEnabled()
    # The raw viewer opens at the selected trial, not whichever sample is newest.
    def inspect_candidate(sample_viewer):
        assert sample_viewer.current_sample.sample_id == first.candidate_sample_id
        sample_viewer.close()
        return 0

    monkeypatch.setattr("stroke_key.gui.trial_viewer.SampleViewer.exec", inspect_candidate)
    viewer.view_button.click()
    viewer.close()


def test_gui_worker_failure_can_retry(app, collection, monkeypatch):
    import stroke_key.gui.trial_dialog as gui
    original = gui.compare_trial

    def fail(prepared):
        raise RuntimeError("Injected worker failure")

    monkeypatch.setattr(gui, "compare_trial", fail)
    dialog = setup_dialog(collection)
    dialog.compare()
    finish_comparison(dialog, app)
    assert "Nothing saved" in dialog.output.toPlainText()
    assert dialog.capture.isEnabled() and dialog.compare_button.isEnabled()
    assert not dialog.save_button.isEnabled()
    monkeypatch.setattr(gui, "compare_trial", original)
    dialog.compare()
    finish_comparison(dialog, app)
    assert dialog.save_button.isEnabled()
    dialog.close()
    assert not collection.service.trials.list_trials()


def test_gui_empty_collection_and_trial_viewer(app, tmp_path):
    database = Database(tmp_path / "empty.db")
    try:
        service = TrialCollectionService(database)
        dialog = TrialDialog(service, [])
        assert not dialog.compare_button.isEnabled()
        assert not dialog.save_button.isEnabled()
        assert "No enrolled" in dialog.output.toPlainText()
        viewer = TrialViewer(service.trials, [])
        assert "No saved" in viewer.details.toPlainText()
        assert not viewer.view_button.isEnabled()
        dialog.close()
        viewer.close()
    finally:
        database.close()

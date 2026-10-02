"""Known-score statistics and synthetic local persistence/Qt evaluation workflows."""
from copy import deepcopy
from dataclasses import asdict, replace
import os
from pathlib import Path
import sqlite3
from threading import Event
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from stroke_key.config import MATCH_CONFIG
from stroke_key.gui.evaluation_dialog import EvaluationDialog
from stroke_key.models.research import EvaluationRun
from stroke_key.processing.evaluation import (EvaluationCohort, EvaluationSelection, TrialObservation,
                                               evaluate_trials)
from stroke_key.processing.matcher import MATCHER_VERSION
from stroke_key.services.enrollment import EnrollmentService
from stroke_key.services.evaluation import EvaluationService
from stroke_key.services.trials import TrialCollectionService, TrialDeclaration, compare_trial
from stroke_key.storage.database import Database
from stroke_key.storage.repositories import SampleRepository, UserRepository


def cohort(**config):
    return EvaluationCohort(MATCHER_VERSION, asdict(replace(MATCH_CONFIG, **config)), "mouse", ())


def observation(identifier, score, attempt="genuine", **changes):
    row = TrialObservation(identifier, "candidate-" + identifier, "claimant", None, attempt,
                           "trial-session", "frozen-reference-set", 1, cohort(), score,
                           score >= MATCH_CONFIG.threshold, False, ("enrollment-session",), False)
    return replace(row, **changes)


def selection(**changes):
    return EvaluationSelection(cohort(), 0.75, **changes)


def test_known_rates_counts_distributions_and_empirical_eer():
    rows = (observation("g1", 0.6), observation("g2", 0.9),
            observation("i1", 0.4, "other_participant"), observation("i2", 0.8, "imitation"))
    report = evaluate_trials(rows, selection())
    result = report.results
    point = result["operating_point"]
    assert point["genuine_count"] == point["impostor_count"] == 2
    assert point["false_acceptances"] == point["false_rejections"] == 1
    assert point["true_acceptances"] == point["true_rejections"] == 1
    assert point["far"] == point["frr"] == 0.5
    assert result["approximate_eer"] == {"rate": 0.5, "method": "observed_equal_rates",
                                         "lower_threshold": 0.8, "upper_threshold": 0.8}
    assert result["distributions"]["genuine"]["median"] == 0.75
    assert result["distributions"]["other_participant"]["accepted"] == 0
    assert result["distributions"]["imitation"]["accepted"] == 1
    assert result["threshold_curve"][0]["far"] == 1
    assert result["threshold_curve"][0]["frr"] == 0
    assert result["threshold_curve"][-1]["far"] == 0
    assert result["threshold_curve"][-1]["frr"] == 1
    assert result["included_trials"][0]["saved_accepted"] is False
    assert report.trial_ids == tuple(row.trial_id for row in rows)


def test_ties_and_extreme_scores_preserve_inclusive_threshold_and_reject_all():
    rows = (observation("g", 1.0), observation("i", 1.0, "imitation"))
    report = evaluate_trials(rows, replace(selection(), threshold=1))
    assert report.results["operating_point"]["far"] == 1
    assert report.results["operating_point"]["frr"] == 0
    curve = report.results["threshold_curve"]
    assert curve[-2]["threshold"] == 1 and curve[-2]["far"] == 1
    assert curve[-1]["threshold"] is None and curve[-1]["accept_none"]
    assert report.results["approximate_eer"]["rate"] == 0.5
    assert report.results["approximate_eer"]["method"] == "linear_interpolation"
    assert report.results["approximate_eer"]["upper_threshold"] is None
    zero = evaluate_trials((observation("g0", 0), observation("i0", 0, "imitation")), replace(selection(), threshold=0))
    assert zero.results["operating_point"]["far"] == 1
    assert zero.results["operating_point"]["frr"] == 0


def test_separated_and_reversed_distributions():
    separated = evaluate_trials((observation("g", 0.9), observation("i", 0.1, "imitation")), selection())
    assert separated.results["approximate_eer"]["rate"] == 0
    assert separated.results["operating_point"]["far"] == separated.results["operating_point"]["frr"] == 0
    reversed_report = evaluate_trials((observation("g", 0.1), observation("i", 0.9, "imitation")), selection())
    assert reversed_report.results["approximate_eer"]["rate"] == 1


@pytest.mark.parametrize("rows", [(), (observation("g", 0.8),), (observation("i", 0.8, "imitation"),)])
def test_missing_classes_are_unavailable_not_zero(rows):
    report = evaluate_trials(rows, selection())
    point = report.results["operating_point"]
    assert report.results["approximate_eer"] is None
    if not any(row.attempt_type == "genuine" for row in rows):
        assert point["frr"] is None
    if not any(row.attempt_type != "genuine" for row in rows):
        assert point["far"] is None
    assert any("Both genuine and impostor" in message for message in report.results["warnings"])


@pytest.mark.parametrize("threshold", [-0.1, 1.1, float("nan"), float("inf"), True, "0.75"])
def test_bad_thresholds_rejected(threshold):
    with pytest.raises(ValueError, match="threshold"):
        EvaluationSelection(cohort(), threshold)


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), float("inf"), True])
def test_bad_included_scores_rejected(score):
    with pytest.raises(ValueError, match="finite"):
        evaluate_trials((observation("bad", score),), selection())


def test_all_exclusions_and_filter_policies_are_recorded():
    rows = (observation("good", 0.9), observation("unknown", 0.9, session_id=None),
            observation("unknown_ref", 0.9, unknown_reference_session=True),
            observation("overlap", 0.9, reference_sessions=("trial-session",)),
            observation("demo", 0.9, synthetic=True), observation("unlabelled", 0.9, "unlabelled"),
            observation("other_config", 0.9, cohort=cohort(dtw_scale=0.3)),
            observation("other_version", 0.9, cohort=replace(cohort(), matcher_version="another-version")),
            observation("other_device", 0.9, cohort=replace(cohort(), device_ids=("pen-123",))))
    report = evaluate_trials(rows, selection(), ("unreadable",))
    assert report.trial_ids == ("good",)
    assert report.results["available_count"] == 10
    assert report.results["excluded_by_reason"] == {
        "unreadable": 1, "unknown_session_separation": 2, "reference_session_overlap": 1,
        "synthetic_candidate_or_reference": 1, "unlabelled": 1, "different_device_or_matcher": 3}
    exploratory = evaluate_trials(rows, selection(include_synthetic=True, strict_sessions=False))
    assert set(exploratory.trial_ids) == {"good", "unknown", "unknown_ref", "overlap", "demo"}
    assert any("demonstration-only" in message for message in exploratory.results["warnings"])
    assert any("Session separation is disabled" in message for message in exploratory.results["warnings"])


def test_claim_session_and_impostor_filters_and_per_participant_denominators():
    rows = (observation("g1", 0.9), observation("i1", 0.8, "other_participant"),
            observation("g2", 0.4, claimed_user_id="second", session_id="later"),
            observation("i2", 0.2, "imitation", claimed_user_id="second", session_id="later"))
    filtered = evaluate_trials(rows, selection(claimed_user_id="second", session_id="later", impostor_type="imitation"))
    assert filtered.trial_ids == ("g2", "i2")
    result = evaluate_trials(rows, selection()).results
    first, second = result["participants"]
    assert first["far"] == 1 and first["frr"] == 0
    assert second["far"] == 0 and second["frr"] == 1
    assert result["session_count"] == 2
    assert evaluate_trials(rows, selection(impostor_type="imitation")).trial_ids == ("g1", "g2", "i2")


def test_duplicate_ids_rejected_and_evaluation_does_not_mutate_inputs():
    row = observation("same", 0.9)
    original = deepcopy(row)
    evaluate_trials((row,), selection())
    assert row == original
    for rows, unreadable in (((row, row), ()), ((row,), ("same",))):
        with pytest.raises(ValueError, match="duplicate"):
            evaluate_trials(rows, selection(), unreadable)


def test_sweep_matches_independent_counts_at_every_boundary():
    rows = tuple(observation(f"g{i}", score) for i, score in enumerate([0, 0.25, 0.25, 0.75, 1])) + \
           tuple(observation(f"i{i}", score, "imitation") for i, score in enumerate([0.1, 0.25, 0.9, 1]))
    report = evaluate_trials(rows, selection())
    for point in report.results["threshold_curve"]:
        threshold = point["threshold"]
        accepted = lambda row: threshold is not None and row.score >= threshold
        expected_rejections = sum(not accepted(row) for row in rows if row.attempt_type == "genuine")
        expected_acceptances = sum(accepted(row) for row in rows if row.attempt_type == "imitation")
        assert point["false_rejections"] == expected_rejections
        assert point["false_acceptances"] == expected_acceptances


@pytest.fixture
def research(tmp_path, sample_factory):
    database = Database(tmp_path / "evaluation.db")
    samples, users = SampleRepository(database), UserRepository(database)
    enrollment = EnrollmentService(users, samples)
    participants = []
    for name in ("Synthetic evaluation claimant", "Synthetic evaluation signer"):
        drafts = [sample_factory() for _ in range(5)]
        for draft in drafts:
            # The underlying measurements are generated test data. These declarations
            # exercise the real-data filter in a disposable database only.
            draft.device_type = "mouse"
            draft.metadata.update(session_id="enrollment-session", research_label="genuine", synthetic=False)
            enrollment.stage(draft)
        participants.append(enrollment.complete(name, [draft.sample_id for draft in drafts]))
    collection = TrialCollectionService(database)

    def add(attempt="genuine", session="trial-session", synthetic=False, config=MATCH_CONFIG):
        candidate = sample_factory(shape="wave" if attempt == "genuine" else "line")
        candidate.device_type = "mouse"
        candidate.metadata.update(session_id=session, synthetic=synthetic)
        signer = participants[0].user_id if attempt == "genuine" else participants[1].user_id if attempt == "other_participant" else None
        collection.config = config
        prepared = collection.prepare(candidate, TrialDeclaration(participants[0].user_id, attempt, True, signer))
        return collection.save(prepared, compare_trial(prepared))

    service = EvaluationService(database)
    yield SimpleNamespace(db=database, samples=samples, users=users, participants=participants,
                          enrollment=enrollment, collection=collection, service=service, add=add)
    database.close()


def db_rows(connection):
    return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in ("users", "signature_samples", "signature_points", "reference_sets", "reference_set_samples",
                          "research_trials", "evaluation_runs", "evaluation_run_trials")}


def evaluated(research, **changes):
    catalogue = research.service.catalogue()
    selected = EvaluationSelection(catalogue.cohorts[0], 0.75, **changes)
    prepared = research.service.prepare(selected)
    return evaluate_trials(prepared.catalogue.observations, prepared.selection, prepared.catalogue.unreadable_trial_ids)


def test_service_reads_archived_scores_and_save_only_adds_run(research, monkeypatch):
    r = research
    genuine, impostor = r.add(), r.add("other_participant")
    before = db_rows(r.db.connection)
    def no_match(*args, **kwargs):
        raise AssertionError("Evaluation must not rerun matching")
    monkeypatch.setattr("stroke_key.processing.matcher.compare_signatures", no_match)
    report = evaluated(r)
    assert db_rows(r.db.connection) == before
    assert report.trial_ids == (genuine.trial_id, impostor.trial_id)
    assert report.results["included_trials"][0]["score"] == genuine.result["overall_score"]
    run = r.service.save(report, "Saved local baseline")
    after = db_rows(r.db.connection)
    for table in before:
        if table not in {"evaluation_runs", "evaluation_run_trials"}:
            assert after[table] == before[table]
    assert run.matcher_config == {**genuine.matcher_config, "threshold": 0.75}
    assert run.selection["cohort"]["matcher_config"] == genuine.matcher_config
    assert run.results == report.results
    assert len(after["evaluation_run_trials"]) == 2
    assert not r.db.connection.execute("PRAGMA foreign_key_check").fetchall()
    assert r.db.connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_old_reference_sessions_and_saved_runs_survive_extensions_and_new_trials(research, sample_factory):
    r = research
    trial = r.add()
    report = evaluated(r)
    run = r.service.save(report, "Before later enrollment")
    draft = sample_factory()
    draft.device_type = "mouse"
    draft.metadata.update(session_id="trial-session", research_label="genuine", synthetic=False)
    r.enrollment.stage(draft)
    r.enrollment.extend(r.participants[0].user_id, [draft.sample_id])
    r.add("imitation", session="later-session")
    fresh = evaluated(r)
    assert trial.trial_id in fresh.trial_ids  # Original revision does not overlap.
    old = next(row for row in fresh.results["included_trials"] if row["trial_id"] == trial.trial_id)
    assert old["reference_revision"] == 1
    assert old["reference_sessions"] == ["enrollment-session"]
    assert r.service.runs.get(run.run_id) == run
    other = Database(Path(r.db.connection.execute("PRAGMA database_list").fetchone()[2]))
    try:
        reopened = EvaluationService(other).runs.get(run.run_id)
        # JSON arrays are reconstructed as lists; snapshots retain the same JSON values.
        assert reopened == run
    finally:
        other.close()


def test_synthetic_candidate_and_reference_exclusions_and_exact_matcher_cohorts(research, sample_factory):
    r = research
    clean = r.add()
    demo = r.add(synthetic=True)
    changed = r.add(config=replace(MATCH_CONFIG, threshold=0.8))
    catalogue = r.service.catalogue()
    assert len(catalogue.cohorts) == 2
    selected = next(c for c in catalogue.cohorts if c.matcher_config["threshold"] == 0.75)
    report = evaluate_trials(catalogue.observations, EvaluationSelection(selected, 0.75))
    assert report.trial_ids == (clean.trial_id,)
    assert {row["trial_id"]: row["reason"] for row in report.results["excluded_trials"]} == {
        demo.trial_id: "synthetic_candidate_or_reference", changed.trial_id: "different_device_or_matcher"}
    draft = sample_factory()
    draft.device_type = "mouse"
    draft.metadata.update(session_id="later-enrollment", research_label="synthetic")
    r.enrollment.stage(draft)
    r.enrollment.extend(r.participants[0].user_id, [draft.sample_id])
    r.add(session="third-session")
    assert evaluated(r).results["excluded_by_reason"]["synthetic_candidate_or_reference"] == 2


def test_unreadable_trial_counted_without_hiding_valid_trials(research, monkeypatch):
    r = research
    good, bad = r.add(), r.add("imitation")
    original = r.service.trials.get
    def get(identifier):
        if identifier == bad.trial_id:
            raise ValueError("Injected unreadable record")
        return original(identifier)
    monkeypatch.setattr(r.service.trials, "get", get)
    report = evaluated(r)
    assert report.trial_ids == (good.trial_id,)
    assert report.results["excluded_by_reason"]["unreadable"] == 1


def test_atomic_run_failure_can_retry_and_existing_run_is_immutable(research):
    r = research
    r.add()
    report = evaluated(r)
    with pytest.raises(ValueError, match="name"):
        r.service.save(report, "  ")
    before = db_rows(r.db.connection)
    r.db.connection.execute("CREATE TEMP TRIGGER fail_evaluation BEFORE INSERT ON evaluation_run_trials "
                            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        r.service.save(report, "Retry baseline")
    assert db_rows(r.db.connection) == before
    r.db.connection.execute("DROP TRIGGER fail_evaluation")
    run = r.service.save(report, "Retry baseline")
    for statement in ("UPDATE evaluation_runs SET name = 'changed' WHERE run_id = ?",
                      "DELETE FROM evaluation_runs WHERE run_id = ?", "DELETE FROM evaluation_run_trials WHERE run_id = ?"):
        with pytest.raises(sqlite3.IntegrityError):
            r.db.connection.execute(statement, (run.run_id,))
    assert r.service.runs.get(run.run_id) == run


def test_no_eligible_trials_cannot_save_and_threshold_exploration_never_changes_trials(research):
    r = research
    r.add(session="enrollment-session")
    empty = evaluated(r)
    assert not empty.trial_ids
    with pytest.raises(ValueError, match="eligible"):
        r.service.save(empty, "Empty run")
    trial = r.add(session="different-session")
    before = r.collection.trials.get(trial.trial_id)
    catalogue = r.service.catalogue()
    for threshold in (0, 1):
        report = evaluate_trials(catalogue.observations, EvaluationSelection(catalogue.cohorts[0], threshold))
        r.service.save(report, f"Threshold {threshold}")
    assert r.collection.trials.get(trial.trial_id) == before
    assert MATCH_CONFIG.threshold == 0.75


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def finish(dialog, app):
    assert dialog.worker is not None and dialog.worker.wait(15_000)
    app.processEvents()
    assert dialog.worker is None


def test_gui_evaluation_save_reload_and_selection_invalidates_report(app, research, monkeypatch):
    r = research
    r.add()
    r.add("imitation")
    errors = []
    monkeypatch.setattr("stroke_key.gui.evaluation_dialog.show_error", lambda _, message: errors.append(message))
    dialog = EvaluationDialog(r.service, r.users.list_users())
    dialog.show()
    dialog.evaluate_button.click()
    assert not dialog.controls.isEnabled()
    finish(dialog, app)
    assert dialog.report is not None
    assert len(dialog.view.figure.axes) == 3
    assert "FRR" in dialog.view.summary.toPlainText() and "FAR" in dialog.view.summary.toPlainText()
    assert dialog.view.participants.rowCount() == 1
    sweep = dialog.view.figure.axes[2]
    assert {line.get_drawstyle() for line in sweep.lines if line.get_label() in {"FAR", "FRR"}} == {"steps-pre"}
    for name, marker in zip(("far", "frr"), sweep.collections):
        assert marker.get_offsets()[0][1] == dialog.report.results["operating_point"][name]
    assert dialog.save_button.isEnabled()
    dialog.save_button.click()
    assert errors and "name" in errors[-1]
    dialog.name.setText("Qt baseline")
    dialog.save_button.click()
    assert len(r.service.runs.list_runs()) == 1
    assert not dialog.save_button.isEnabled()
    assert dialog.pages.currentIndex() == 1
    assert "Qt baseline" in dialog.saved_view.summary.toPlainText()
    assert len(dialog.saved_view.figure.axes) == 3
    saved_run = r.service.runs.list_runs()[0]
    dialog.pages.setCurrentIndex(0)
    dialog.threshold.setValue(0.99)
    assert dialog.report is None and not dialog.save_button.isEnabled()
    assert len(dialog.view.figure.axes) == 0
    dialog.evaluate()
    finish(dialog, app)
    assert dialog.report.selection.threshold == 0.99
    assert r.service.runs.get(saved_run.run_id) == saved_run
    dialog.close()


def test_gui_save_failure_keeps_review_and_empty_states(app, research, tmp_path, monkeypatch):
    r = research
    r.add(synthetic=True)
    monkeypatch.setattr("stroke_key.gui.evaluation_dialog.show_error", lambda *args: None)
    dialog = EvaluationDialog(r.service, r.users.list_users())
    dialog.evaluate()
    finish(dialog, app)
    assert not dialog.report.trial_ids and not dialog.save_button.isEnabled()
    dialog.synthetic.setChecked(True)
    dialog.evaluate()
    finish(dialog, app)
    assert dialog.save_button.isEnabled()
    r.db.connection.execute("CREATE TEMP TRIGGER fail_evaluation BEFORE INSERT ON evaluation_runs "
                            "BEGIN SELECT RAISE(ABORT, 'injected'); END")
    dialog.name.setText("Retry demo")
    report = dialog.report
    dialog.save()
    assert dialog.report == report and dialog.save_button.isEnabled()
    assert not r.service.runs.list_runs()
    r.db.connection.execute("DROP TRIGGER fail_evaluation")
    dialog.save()
    assert len(r.service.runs.list_runs()) == 1
    assert "DEMONSTRATION DATA" in dialog.saved_view.summary.toPlainText().splitlines()[1]
    dialog.close()
    database = Database(tmp_path / "empty.db")
    try:
        empty = EvaluationDialog(EvaluationService(database), [])
        assert not empty.evaluate_button.isEnabled() and not empty.save_button.isEnabled()
        assert "No readable saved trials" in empty.view.summary.toPlainText()
        empty.close()
    finally:
        database.close()


def test_gui_close_waits_for_worker_without_saving(app, research, monkeypatch):
    import stroke_key.gui.evaluation_dialog as gui
    research.add()
    entered, release = Event(), Event()
    original = gui.evaluate_trials
    def delayed(*args):
        entered.set()
        assert release.wait(15)
        return original(*args)
    monkeypatch.setattr(gui, "evaluate_trials", delayed)
    dialog = EvaluationDialog(research.service, research.users.list_users())
    dialog.show()
    dialog.evaluate()
    assert entered.wait(5)
    try:
        dialog.close()
        assert dialog._pending_close is not None and dialog.isVisible()
    finally:
        release.set()
        finish(dialog, app)
    assert not dialog.isVisible() and not research.service.runs.list_runs()


def test_gui_worker_and_refresh_failures_keep_records_unchanged(app, research, monkeypatch):
    import stroke_key.gui.evaluation_dialog as gui
    r = research
    r.add()
    before = db_rows(r.db.connection)
    dialog = EvaluationDialog(r.service, r.users.list_users())
    original = gui.evaluate_trials
    def fail(*args):
        raise RuntimeError("Injected worker failure")
    monkeypatch.setattr(gui, "evaluate_trials", fail)
    dialog.evaluate()
    finish(dialog, app)
    assert "Evaluation failed" in dialog.view.summary.toPlainText()
    assert dialog.controls.isEnabled() and not dialog.save_button.isEnabled()
    monkeypatch.setattr(gui, "evaluate_trials", original)
    dialog.evaluate()
    finish(dialog, app)
    assert dialog.save_button.isEnabled()
    monkeypatch.setattr(r.service, "catalogue", fail)
    monkeypatch.setattr(gui, "show_error", lambda *args: None)
    dialog.refresh_button.click()
    assert "Could not read" in dialog.view.summary.toPlainText()
    assert not dialog.evaluate_button.isEnabled() and not dialog.save_button.isEnabled()
    assert db_rows(r.db.connection) == before
    dialog.close()


def test_gui_legacy_and_unreadable_saved_runs_leave_other_runs_available(app, research, monkeypatch):
    r = research
    trial = r.add()
    good = r.service.save(evaluated(r), "Current snapshot")
    legacy = r.service.runs.save(EvaluationRun("Legacy snapshot", (trial.trial_id,), {}, MATCHER_VERSION, asdict(MATCH_CONFIG), {"count": 1}))
    dialog = EvaluationDialog(r.service, r.users.list_users())
    dialog.runs.setCurrentRow(dialog.run_ids.index(legacy.run_id))
    assert "Legacy or unsupported" in dialog.saved_view.summary.toPlainText()
    assert '"count": 1' in dialog.saved_view.audit.toPlainText()
    original = r.service.runs.get
    def get(identifier):
        if identifier == legacy.run_id:
            raise ValueError("Injected unreadable run")
        return original(identifier)
    monkeypatch.setattr(r.service.runs, "get", get)
    monkeypatch.setattr("stroke_key.gui.evaluation_dialog.show_error", lambda *args: None)
    dialog.show_run(dialog.run_ids.index(legacy.run_id))
    assert "cannot be displayed" in dialog.saved_view.summary.toPlainText()
    dialog.runs.setCurrentRow(dialog.run_ids.index(good.run_id))
    assert "Current snapshot" in dialog.saved_view.summary.toPlainText()
    dialog.close()

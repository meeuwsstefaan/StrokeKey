"""Migration preservation, atomic publication, frozen evidence and trial isolation."""
from dataclasses import asdict, replace
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from stroke_key.config import MATCH_CONFIG
from stroke_key.models.research import EvaluationRun, ResearchTrial
from stroke_key.models.user import User
from stroke_key.processing.matcher import MATCHER_VERSION
from stroke_key.services.enrollment import EnrollmentService
from stroke_key.services.verification import VerificationService, compare_references
from stroke_key.storage.database import Database, SCHEMA_V1
from stroke_key.storage.research_repositories import (EvaluationRunRepository, ReferenceSetRepository,
                                                       ResearchTrialRepository)
from stroke_key.storage.repositories import SampleRepository, StoredDataError, UserRepository
from stroke_key.storage.transactions import atomic


LEGACY_TABLES = ("users", "signature_samples", "signature_points")


def raw_rows(connection):
    return {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
            for table in LEGACY_TABLES}


def create_legacy(path, sample_factory):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA_V1)
    connection.execute("INSERT INTO users VALUES ('legacy-user', 'Synthetic legacy', 'legacy-time', '{}')")
    samples = SampleRepository(SimpleNamespace(connection=connection))
    references = []
    for index in range(5):
        sample = sample_factory(duration=1 + index / 10)
        sample.user_id = "legacy-user"
        if index > 2:
            sample.device_type = "stylus"
            sample.points = [replace(p, device_type="stylus", device_id="legacy-pen") for p in sample.points]
        samples.save(sample)
        references.append(sample)
    draft = sample_factory()
    samples.save(draft)
    # Migration preserves malformed metadata rather than inventing history or deleting data.
    connection.execute("UPDATE signature_samples SET metadata = 'legacy-invalid-json' WHERE sample_id = ?", (draft.sample_id,))
    connection.commit()
    snapshot = raw_rows(connection)
    connection.close()
    return snapshot, references


def test_migrate_v1_preserves_all_raw_rows_backfills_groups_and_backup(tmp_path, sample_factory):
    path = tmp_path / "legacy.db"
    snapshot, references = create_legacy(path, sample_factory)
    database = Database(path)
    try:
        assert database.connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert raw_rows(database.connection) == snapshot
        assert not database.connection.execute("PRAGMA foreign_key_check").fetchall()
        sets = ReferenceSetRepository(database).list_for_user("legacy-user")
        assert sorted(len(s.sample_ids) for s in sets) == [2, 3]
        assert all(s.revision == 1 for s in sets)
        assert {sample_id for s in sets for sample_id in s.sample_ids} == {s.sample_id for s in references}
        assert {s.device_ids for s in sets} == {(), ("legacy-pen",)}
        assert all("session_id" not in s.metadata for s in SampleRepository(database).for_user("legacy-user"))
        backup_path = database.migration_backup
        assert backup_path and backup_path.exists()
        with sqlite3.connect(backup_path) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
            assert raw_rows(backup) == snapshot
    finally:
        database.close()
    database = Database(path)
    try:
        assert database.migration_backup is None
        assert ReferenceSetRepository(database).list_for_user("legacy-user") == sets
    finally:
        database.close()


def test_migration_failure_rolls_back_ddl_backfill_and_version(tmp_path, sample_factory, monkeypatch):
    import stroke_key.storage.migrations as migrations
    path = tmp_path / "failed.db"
    snapshot, _ = create_legacy(path, sample_factory)
    original = migrations.backfill_reference_sets

    def fail_after_backfill(connection):
        original(connection)
        raise RuntimeError("Injected failure after publishing migrated reference sets")

    monkeypatch.setattr(migrations, "backfill_reference_sets", fail_after_backfill)
    with pytest.raises(RuntimeError, match="Injected"):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert raw_rows(connection) == snapshot
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'reference_sets'").fetchone() is None
        assert not connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
    assert len(list(tmp_path.glob("failed.db-v1-backup-*"))) == 1
    monkeypatch.setattr(migrations, "backfill_reference_sets", original)
    database = Database(path)
    database.close()


def test_new_database_failure_rolls_back_initial_schema(tmp_path, monkeypatch):
    def fail(connection):
        connection.execute("CREATE TABLE partial_migration (value TEXT)")
        raise RuntimeError("Injected migration failure")

    monkeypatch.setattr("stroke_key.storage.database.migrate_v1_to_v2", fail)
    path = tmp_path / "new.db"
    with pytest.raises(RuntimeError):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert not connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()


def test_unknown_schema_version_is_not_downgraded(tmp_path):
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="Unsupported"):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_legacy_foreign_key_corruption_blocks_migration_without_changes(tmp_path, sample_factory):
    path = tmp_path / "orphan.db"
    create_legacy(path, sample_factory)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE signature_samples SET user_id = 'missing' WHERE user_id IS NOT NULL")
    with pytest.raises(ValueError, match="foreign keys"):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'reference_sets'").fetchone() is None


@pytest.fixture
def research(tmp_path, sample_factory):
    database = Database(tmp_path / "research.db")
    samples, users = SampleRepository(database), UserRepository(database)
    service = EnrollmentService(users, samples)
    references = [sample_factory(duration=1 + index / 10) for index in range(5)]
    for sample in references:
        sample.metadata.update(session_id="enrollment-session", research_label="synthetic")
        service.stage(sample)
    user = service.complete("Synthetic participant", [s.sample_id for s in references])
    sets = ReferenceSetRepository(database)
    reference = sets.latest_for_user(user.user_id)[0]
    yield SimpleNamespace(db=database, samples=samples, users=users, service=service, user=user, sets=sets,
                          reference=reference, trials=ResearchTrialRepository(database), runs=EvaluationRunRepository(database))
    database.close()


def make_trial(research, sample_factory):
    candidate = sample_factory(duration=1.2)
    candidate.metadata.update(session_id="trial-session", research_label="genuine")
    report = compare_references(candidate, research.sets.samples_for(research.reference.reference_set_id))
    trial = ResearchTrial(candidate_sample_id=candidate.sample_id, claimed_user_id=research.user.user_id,
                          reference_set_id=research.reference.reference_set_id, attempt_type="genuine",
                          session_id="trial-session", matcher_version=MATCHER_VERSION, matcher_config=asdict(MATCH_CONFIG),
                          result=asdict(report.result), comparisons={sid: asdict(result) for sid, result in report.comparisons},
                          consent_confirmed=True, actual_signer_id=research.user.user_id)
    return candidate, trial


def test_new_enrollment_publishes_references_and_latest_revision_is_explicit(research, sample_factory):
    assert len(research.reference.sample_ids) == 5
    assert research.reference.revision == 1
    new_sample = sample_factory()
    new_sample.user_id = research.user.user_id
    research.samples.save(new_sample)
    assert len(research.samples.for_user(research.user.user_id)) == 6
    assert len(VerificationService(research.samples).prepare(research.user.user_id, sample_factory())) == 5
    new_set = research.sets.create(research.user.user_id, [*research.reference.sample_ids, new_sample.sample_id])
    assert new_set.revision == 2
    assert len(VerificationService(research.samples).prepare(research.user.user_id, sample_factory())) == 6
    assert research.sets.get(research.reference.reference_set_id) == research.reference


def test_trial_and_evaluation_remain_stable_after_new_reference_version(research, sample_factory):
    candidate, trial = make_trial(research, sample_factory)
    assert research.trials.save(candidate, trial) == trial
    assert research.samples.get(candidate.sample_id).metadata["purpose"] == "research_trial"
    assert "purpose" not in candidate.metadata  # Saving does not mutate the caller's capture.
    assert len(research.samples.enrollment_references(research.user.user_id)) == 5
    run = EvaluationRun(name="Synthetic baseline", trial_ids=(trial.trial_id,), selection={"session": "trial-session"},
                        matcher_version=MATCHER_VERSION, matcher_config=asdict(MATCH_CONFIG),
                        results={"false_rejections": 0, "genuine_trials": 1, "false_acceptance_rate": None})
    assert research.runs.save(run) == run
    research.sets.create(research.user.user_id, list(reversed(research.reference.sample_ids)))
    assert research.trials.get(trial.trial_id) == trial
    assert research.runs.get(run.run_id) == run
    assert research.trials.list_trials(research.user.user_id) == [trial]
    assert research.runs.list_runs() == [run]


def test_trial_candidate_cannot_be_retried_enrolled_or_mutated(research, sample_factory):
    candidate, trial = make_trial(research, sample_factory)
    research.trials.save(candidate, trial)
    assert research.samples.is_trial(candidate.sample_id)
    with pytest.raises(ValueError, match="Research trials"):
        research.samples.delete_unassigned(candidate.sample_id)
    with pytest.raises(ValueError, match="invalid or already assigned"):
        research.service.complete("Another participant", [candidate.sample_id, *research.reference.sample_ids[:4]])
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        research.db.connection.execute("UPDATE signature_samples SET user_id = ? WHERE sample_id = ?",
                                        (research.user.user_id, candidate.sample_id))
    assert research.samples.get(candidate.sample_id).user_id is None
    assert any(row["is_research_trial"] for row in research.samples.list_summaries())


@pytest.mark.parametrize("statement", [
    "UPDATE reference_sets SET revision = 99 WHERE reference_set_id = ?",
    "DELETE FROM reference_sets WHERE reference_set_id = ?",
    "DELETE FROM reference_set_samples WHERE reference_set_id = ?",
    "UPDATE reference_set_samples SET ordinal = ordinal + 10 WHERE reference_set_id = ?",
])
def test_published_reference_sets_reject_sql_mutation(research, statement):
    with pytest.raises(sqlite3.IntegrityError):
        research.db.connection.execute(statement, (research.reference.reference_set_id,))
    assert research.sets.get(research.reference.reference_set_id) == research.reference


@pytest.mark.parametrize("statement", [
    "UPDATE signature_samples SET metadata = '{}' WHERE sample_id = ?",
    "DELETE FROM signature_samples WHERE sample_id = ?",
    "UPDATE signature_points SET x = x + 100 WHERE sample_id = ?",
    "DELETE FROM signature_points WHERE sample_id = ?",
    "INSERT INTO signature_points SELECT sample_id, 1000, x, y, timestamp, elapsed_time, pressure, stroke_number, pointer_state, device_type, tilt_x, tilt_y, orientation, stylus_buttons, device_id FROM signature_points WHERE sample_id = ? LIMIT 1",
    "INSERT OR REPLACE INTO signature_samples SELECT sample_id, user_id, created_at, device_type, total_duration, number_of_strokes, '{}' FROM signature_samples WHERE sample_id = ?",
])
def test_published_raw_reference_measurements_are_immutable(research, statement):
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        research.db.connection.execute(statement, (research.reference.sample_ids[0],))


@pytest.mark.parametrize("bad_field", ["consent", "session", "signer", "comparisons", "threshold", "nonfinite", "config", "aggregate"])
def test_invalid_trials_leave_no_candidate_or_trial(research, sample_factory, bad_field):
    candidate, trial = make_trial(research, sample_factory)
    if bad_field == "consent":
        trial = replace(trial, consent_confirmed=False)
    elif bad_field == "session":
        trial = replace(trial, session_id="another-session")
    elif bad_field == "signer":
        trial = replace(trial, attempt_type="imitation")
    elif bad_field == "comparisons":
        trial = replace(trial, comparisons={})
    elif bad_field == "threshold":
        trial = replace(trial, result={**trial.result, "accepted": not trial.result["accepted"]})
    elif bad_field == "nonfinite":
        trial = replace(trial, result={**trial.result, "overall_score": float("nan")})
    elif bad_field == "config":
        trial = replace(trial, matcher_config={"threshold": 0.75})
    else:
        trial = replace(trial, result={**trial.result, "geometry_score": 0.01})
    count = len(research.samples.list_summaries())
    with pytest.raises(ValueError):
        research.trials.save(candidate, trial)
    assert len(research.samples.list_summaries()) == count
    assert not research.trials.list_trials()


def test_trial_save_sql_failure_after_candidate_insert_rolls_back(research, sample_factory):
    candidate, trial = make_trial(research, sample_factory)
    # Missing signer is rejected by a foreign key after the raw candidate has been inserted.
    trial = replace(trial, attempt_type="other_participant", actual_signer_id="missing-participant")
    with pytest.raises(sqlite3.IntegrityError):
        research.trials.save(candidate, trial)
    with pytest.raises(StoredDataError):
        research.samples.get(candidate.sample_id)
    assert not research.trials.list_trials()


def test_duplicate_trial_save_keeps_original_evidence(research, sample_factory):
    candidate, trial = make_trial(research, sample_factory)
    research.trials.save(candidate, trial)
    with pytest.raises(sqlite3.IntegrityError):
        research.trials.save(candidate, trial)
    assert research.trials.list_trials() == [trial]
    assert len(research.samples.list_summaries()) == 6


def test_trial_and_run_reopen_without_recomputing_or_changing_snapshots(research, sample_factory):
    candidate, trial = make_trial(research, sample_factory)
    research.trials.save(candidate, trial)
    run = EvaluationRun(name="Synthetic saved run", trial_ids=(trial.trial_id,), selection={"strict_sessions": True},
                        matcher_version=MATCHER_VERSION, matcher_config=asdict(MATCH_CONFIG), results={"count": 1})
    research.runs.save(run)
    path = research.db.connection.execute("PRAGMA database_list").fetchone()[2]
    other = Database(Path(path))
    try:
        assert ResearchTrialRepository(other).get(trial.trial_id) == trial
        assert EvaluationRunRepository(other).get(run.run_id) == run
        assert other.migration_backup is None
    finally:
        other.close()


def test_saved_trials_remain_readable_after_capture_policy_changes(research, sample_factory, monkeypatch):
    from stroke_key.config import CAPTURE_CONFIG
    candidate, trial = make_trial(research, sample_factory)
    research.trials.save(candidate, trial)
    monkeypatch.setattr("stroke_key.storage.research_repositories.CAPTURE_CONFIG",
                        replace(CAPTURE_CONFIG, enrollment_samples=10))
    assert research.trials.get(trial.trial_id) == trial


def test_reference_creation_rejects_duplicates_foreign_owner_and_device(research, sample_factory):
    base = research.reference.sample_ids
    other = sample_factory()
    other.user_id = research.user.user_id
    other.device_type = "stylus"
    research.samples.save(other)
    for user_id, ids in ((research.user.user_id, [base[0], base[0]]),
                         ("foreign-user", list(base)), (research.user.user_id, [base[0], other.sample_id])):
        with pytest.raises(ValueError):
            research.sets.create(user_id, ids)
    assert len(research.sets.list_for_user(research.user.user_id)) == 1


def test_published_membership_cannot_be_extended(research, sample_factory):
    sample = sample_factory()
    sample.user_id = research.user.user_id
    research.samples.save(sample)
    with pytest.raises(sqlite3.IntegrityError):
        research.db.connection.execute("INSERT INTO reference_set_samples VALUES (?, ?, 5)",
                                        (research.reference.reference_set_id, sample.sample_id))
    assert len(research.sets.get(research.reference.reference_set_id).sample_ids) == 5


@pytest.mark.parametrize("statement", [
    "UPDATE signature_points SET pressure = 1 WHERE sample_id = ?",
    "DELETE FROM signature_points WHERE sample_id = ?",
    "DELETE FROM signature_samples WHERE sample_id = ?",
])
def test_trial_measurements_cannot_be_changed_or_deleted(research, sample_factory, statement):
    candidate, trial = make_trial(research, sample_factory)
    research.trials.save(candidate, trial)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        research.db.connection.execute(statement, (candidate.sample_id,))
    assert research.trials.get(trial.trial_id) == trial


def test_enrollment_publication_failure_rolls_back_identity_and_associations(research, sample_factory, monkeypatch):
    drafts = [sample_factory() for _ in range(5)]
    for draft in drafts:
        research.service.stage(draft)
    original = ReferenceSetRepository.create_groups

    def fail_after_publication(self, user_id, sample_ids):
        original(self, user_id, sample_ids)
        raise RuntimeError("Injected publication failure")

    monkeypatch.setattr(ReferenceSetRepository, "create_groups", fail_after_publication)
    with pytest.raises(RuntimeError, match="publication"):
        research.service.complete("Synthetic failure", [s.sample_id for s in drafts])
    assert len(research.users.list_users()) == 1
    assert all(research.samples.get(s.sample_id).user_id is None for s in drafts)
    assert research.db.connection.execute("SELECT COUNT(*) FROM reference_sets").fetchone()[0] == 1


def test_nested_repository_save_does_not_commit_outer_transaction(research, sample_factory):
    candidate = sample_factory()
    with pytest.raises(RuntimeError):
        with atomic(research.db.connection):
            research.samples.save(candidate)
            raise RuntimeError("Outer workflow failed")
    with pytest.raises(StoredDataError):
        research.samples.get(candidate.sample_id)


def test_evaluation_selection_and_payload_failures_roll_back(research, sample_factory):
    candidate, trial = make_trial(research, sample_factory)
    research.trials.save(candidate, trial)
    run = EvaluationRun(name="Synthetic", trial_ids=(trial.trial_id,), selection={},
                        matcher_version=MATCHER_VERSION, matcher_config=asdict(MATCH_CONFIG), results={})
    for bad in (replace(run, trial_ids=(trial.trial_id, trial.trial_id)),
                replace(run, trial_ids=("missing",)), replace(run, results={"rate": float("inf")})):
        with pytest.raises(ValueError):
            research.runs.save(bad)
    assert not research.runs.list_runs()
    research.runs.save(run)
    for statement in ("UPDATE evaluation_runs SET results = '{}' WHERE run_id = ?",
                      "DELETE FROM evaluation_runs WHERE run_id = ?",
                      "DELETE FROM evaluation_run_trials WHERE run_id = ?"):
        with pytest.raises(sqlite3.IntegrityError):
            research.db.connection.execute(statement, (run.run_id,))
    for statement in ("UPDATE research_trials SET result = '{}' WHERE trial_id = ?",
                      "DELETE FROM research_trials WHERE trial_id = ?"):
        with pytest.raises(sqlite3.IntegrityError):
            research.db.connection.execute(statement, (trial.trial_id,))
    assert research.runs.get(run.run_id) == run

"""Additive schema and immutable, published research snapshots."""
import json
import sqlite3

from stroke_key.models.research import new_id, utc_now

SCHEMA_VERSION = 2

SCHEMA_V2 = """
CREATE TABLE reference_sets (
    reference_set_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    device_type TEXT NOT NULL,
    device_ids TEXT NOT NULL,
    device_group TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sealed INTEGER NOT NULL DEFAULT 0 CHECK(sealed IN (0, 1)),
    UNIQUE(user_id, device_group, revision)
);
CREATE TABLE reference_set_samples (
    reference_set_id TEXT NOT NULL REFERENCES reference_sets(reference_set_id),
    sample_id TEXT NOT NULL REFERENCES signature_samples(sample_id),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    PRIMARY KEY(reference_set_id, sample_id),
    UNIQUE(reference_set_id, ordinal)
);
CREATE INDEX references_by_sample ON reference_set_samples(sample_id);
CREATE TABLE research_trials (
    trial_id TEXT PRIMARY KEY,
    candidate_sample_id TEXT NOT NULL UNIQUE REFERENCES signature_samples(sample_id),
    claimed_user_id TEXT NOT NULL REFERENCES users(user_id),
    actual_signer_id TEXT REFERENCES users(user_id),
    reference_set_id TEXT NOT NULL REFERENCES reference_sets(reference_set_id),
    attempt_type TEXT NOT NULL CHECK(attempt_type IN ('genuine', 'other_participant', 'imitation', 'unlabelled')),
    session_id TEXT CHECK(session_id IS NULL OR length(trim(session_id)) > 0),
    consent_confirmed INTEGER NOT NULL CHECK(consent_confirmed = 1),
    notes TEXT NOT NULL,
    matcher_version TEXT NOT NULL CHECK(length(trim(matcher_version)) > 0),
    matcher_config TEXT NOT NULL,
    result TEXT NOT NULL,
    comparisons TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX trials_by_claim ON research_trials(claimed_user_id, created_at);
CREATE INDEX trials_by_session ON research_trials(session_id);
CREATE TABLE evaluation_runs (
    run_id TEXT PRIMARY KEY,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    selection TEXT NOT NULL,
    matcher_version TEXT NOT NULL CHECK(length(trim(matcher_version)) > 0),
    matcher_config TEXT NOT NULL,
    results TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sealed INTEGER NOT NULL DEFAULT 0 CHECK(sealed IN (0, 1))
);
CREATE TABLE evaluation_run_trials (
    run_id TEXT NOT NULL REFERENCES evaluation_runs(run_id),
    trial_id TEXT NOT NULL REFERENCES research_trials(trial_id),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    PRIMARY KEY(run_id, trial_id),
    UNIQUE(run_id, ordinal)
);

CREATE TRIGGER protect_reference_set_update BEFORE UPDATE ON reference_sets
WHEN OLD.sealed = 1
BEGIN SELECT RAISE(ABORT, 'Published reference sets are immutable'); END;
CREATE TRIGGER validate_reference_set_insert BEFORE INSERT ON reference_sets
WHEN NEW.sealed != 0
BEGIN SELECT RAISE(ABORT, 'Build reference membership before publishing'); END;
CREATE TRIGGER protect_reference_set_delete BEFORE DELETE ON reference_sets
WHEN OLD.sealed = 1
BEGIN SELECT RAISE(ABORT, 'Published reference sets are immutable'); END;
CREATE TRIGGER validate_reference_seal BEFORE UPDATE OF sealed ON reference_sets
WHEN NEW.sealed = 1 AND (
    NOT EXISTS (SELECT 1 FROM reference_set_samples WHERE reference_set_id = OLD.reference_set_id)
    OR (SELECT MIN(ordinal) FROM reference_set_samples WHERE reference_set_id = OLD.reference_set_id) != 0
    OR (SELECT MAX(ordinal) + 1 FROM reference_set_samples WHERE reference_set_id = OLD.reference_set_id)
       != (SELECT COUNT(*) FROM reference_set_samples WHERE reference_set_id = OLD.reference_set_id)
    OR EXISTS (SELECT 1 FROM reference_set_samples m JOIN signature_samples s ON m.sample_id = s.sample_id
               WHERE m.reference_set_id = OLD.reference_set_id
               AND (s.user_id IS NULL OR s.user_id != NEW.user_id OR s.device_type != NEW.device_type)))
BEGIN SELECT RAISE(ABORT, 'Reference sets must have complete, valid membership'); END;
CREATE TRIGGER validate_reference_member BEFORE INSERT ON reference_set_samples
WHEN (SELECT sealed FROM reference_sets WHERE reference_set_id = NEW.reference_set_id) != 0
 OR NOT EXISTS (
    SELECT 1 FROM signature_samples s JOIN reference_sets r ON r.reference_set_id = NEW.reference_set_id
    WHERE s.sample_id = NEW.sample_id AND s.user_id = r.user_id AND s.device_type = r.device_type)
 OR EXISTS (SELECT 1 FROM research_trials WHERE candidate_sample_id = NEW.sample_id)
BEGIN SELECT RAISE(ABORT, 'Invalid or published reference membership'); END;
CREATE TRIGGER protect_reference_member_update BEFORE UPDATE ON reference_set_samples
BEGIN SELECT RAISE(ABORT, 'Reference membership cannot be updated'); END;
CREATE TRIGGER protect_reference_member_delete BEFORE DELETE ON reference_set_samples
WHEN (SELECT sealed FROM reference_sets WHERE reference_set_id = OLD.reference_set_id) = 1
BEGIN SELECT RAISE(ABORT, 'Published reference membership is immutable'); END;

CREATE TRIGGER validate_trial BEFORE INSERT ON research_trials
WHEN NOT EXISTS (
    SELECT 1 FROM signature_samples s WHERE s.sample_id = NEW.candidate_sample_id AND s.user_id IS NULL)
 OR EXISTS (SELECT 1 FROM reference_set_samples WHERE sample_id = NEW.candidate_sample_id)
 OR NOT EXISTS (
    SELECT 1 FROM reference_sets r WHERE r.reference_set_id = NEW.reference_set_id
    AND r.sealed = 1 AND r.user_id = NEW.claimed_user_id)
 OR (NEW.attempt_type = 'genuine' AND NEW.actual_signer_id IS NOT NULL AND NEW.actual_signer_id != NEW.claimed_user_id)
 OR (NEW.attempt_type IN ('other_participant', 'imitation') AND NEW.actual_signer_id = NEW.claimed_user_id)
 OR (NEW.attempt_type = 'other_participant' AND NEW.actual_signer_id IS NULL)
BEGIN SELECT RAISE(ABORT, 'Invalid research trial relationships'); END;
CREATE TRIGGER protect_trial_update BEFORE UPDATE ON research_trials
BEGIN SELECT RAISE(ABORT, 'Saved research trials are immutable'); END;
CREATE TRIGGER protect_trial_delete BEFORE DELETE ON research_trials
BEGIN SELECT RAISE(ABORT, 'Saved research trials are immutable'); END;

CREATE TRIGGER protect_evaluation_update BEFORE UPDATE ON evaluation_runs
WHEN OLD.sealed = 1
BEGIN SELECT RAISE(ABORT, 'Saved evaluation runs are immutable'); END;
CREATE TRIGGER validate_evaluation_insert BEFORE INSERT ON evaluation_runs
WHEN NEW.sealed != 0
BEGIN SELECT RAISE(ABORT, 'Build evaluation selection before publishing'); END;
CREATE TRIGGER protect_evaluation_delete BEFORE DELETE ON evaluation_runs
WHEN OLD.sealed = 1
BEGIN SELECT RAISE(ABORT, 'Saved evaluation runs are immutable'); END;
CREATE TRIGGER protect_evaluation_member_insert BEFORE INSERT ON evaluation_run_trials
WHEN (SELECT sealed FROM evaluation_runs WHERE run_id = NEW.run_id) != 0
BEGIN SELECT RAISE(ABORT, 'Saved evaluation selections are immutable'); END;
CREATE TRIGGER protect_evaluation_member_update BEFORE UPDATE ON evaluation_run_trials
BEGIN SELECT RAISE(ABORT, 'Evaluation selections cannot be updated'); END;
CREATE TRIGGER protect_evaluation_member_delete BEFORE DELETE ON evaluation_run_trials
WHEN (SELECT sealed FROM evaluation_runs WHERE run_id = OLD.run_id) = 1
BEGIN SELECT RAISE(ABORT, 'Saved evaluation selections are immutable'); END;
"""


def execute_statements(connection: sqlite3.Connection, script: str) -> None:
    """Unlike executescript(), do not implicitly commit the migration transaction."""
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise ValueError("Incomplete schema statement")


def device_group(device_type: str, ids: tuple[str, ...]) -> str:
    return json.dumps([device_type, list(ids)], separators=(",", ":"), allow_nan=False)


def stored_device_ids(connection: sqlite3.Connection, sample_id: str) -> tuple[str, ...]:
    return tuple(row[0] for row in connection.execute(
        "SELECT DISTINCT device_id FROM signature_points WHERE sample_id = ? AND device_id IS NOT NULL ORDER BY device_id",
        (sample_id,)))


def backfill_reference_sets(connection: sqlite3.Connection) -> None:
    """Preserve raw legacy data, including absent or malformed session metadata."""
    groups = {}
    for row in connection.execute(
            "SELECT sample_id, user_id, device_type FROM signature_samples WHERE user_id IS NOT NULL ORDER BY created_at, sample_id"):
        key = (row["user_id"], row["device_type"], stored_device_ids(connection, row["sample_id"]))
        groups.setdefault(key, []).append(row["sample_id"])
    for (user_id, input_type, ids), sample_ids in groups.items():
        set_id = new_id()
        connection.execute(
            "INSERT INTO reference_sets VALUES (?, ?, 1, ?, ?, ?, ?, 0)",
            (set_id, user_id, input_type, json.dumps(ids), device_group(input_type, ids), utc_now()))
        connection.executemany("INSERT INTO reference_set_samples VALUES (?, ?, ?)",
                               [(set_id, sample_id, ordinal) for ordinal, sample_id in enumerate(sample_ids)])
        connection.execute("UPDATE reference_sets SET sealed = 1 WHERE reference_set_id = ?", (set_id,))


def protect_measurements(connection: sqlite3.Connection) -> None:
    # Protect raw measurements and session/label metadata behind every frozen result.
    for table in ("signature_samples", "signature_points"):
        actions = ("UPDATE", "DELETE") if table == "signature_samples" else ("INSERT", "UPDATE", "DELETE")
        for action in actions:
            aliases = ("OLD", "NEW") if action == "UPDATE" else ("NEW",) if action == "INSERT" else ("OLD",)
            clauses = []
            for alias in aliases:
                clauses.append(f"""EXISTS (SELECT 1 FROM reference_set_samples m JOIN reference_sets r
                    ON m.reference_set_id = r.reference_set_id
                    WHERE m.sample_id = {alias}.sample_id AND r.sealed = 1)
                    OR EXISTS (SELECT 1 FROM research_trials WHERE candidate_sample_id = {alias}.sample_id)""")
            connection.execute(f"""CREATE TRIGGER protect_{table}_{action.lower()} BEFORE {action} ON {table}
                WHEN {' OR '.join(clauses)}
                BEGIN SELECT RAISE(ABORT, 'Published research measurements are immutable'); END""")


def migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    execute_statements(connection, SCHEMA_V2)
    backfill_reference_sets(connection)
    protect_measurements(connection)

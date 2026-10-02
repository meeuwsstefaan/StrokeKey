"""Atomic persistence and strict reconstruction of frozen research records."""
from dataclasses import fields, replace
import json
from math import isfinite
from statistics import median

from stroke_key.config import CAPTURE_CONFIG, MatchConfig
from stroke_key.models.research import EvaluationRun, ReferenceSet, ResearchTrial, new_id, utc_now
from stroke_key.models.signature import SignatureSample, validity_errors
from stroke_key.processing.compatibility import device_ids, input_group, matches_group
from stroke_key.storage.database import Database
from stroke_key.storage.migrations import device_group
from stroke_key.storage.repositories import SampleRepository, StoredDataError, decode_object
from stroke_key.storage.transactions import atomic


def encode_object(value: dict) -> str:
    if not isinstance(value, dict):
        raise ValueError("Research snapshot must be a JSON object")
    # Freeze through JSON and reject NaN/Infinity rather than storing invalid metrics.
    return json.dumps(value, allow_nan=False, sort_keys=True, ensure_ascii=False)


def require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty text")


class ReferenceSetRepository:
    def __init__(self, database: Database) -> None:
        self.connection = database.connection
        self.samples = SampleRepository(database)

    def create(self, user_id: str, sample_ids: list[str] | tuple[str, ...]) -> ReferenceSet:
        """Publish a new revision for an exact input-type / reported-device group."""
        require_text(user_id, "Participant")
        if not sample_ids or len(set(sample_ids)) != len(sample_ids):
            raise ValueError("Reference sets require distinct samples")
        with atomic(self.connection):
            samples = [self.samples.get(sample_id) for sample_id in sample_ids]
            input_type, ids = samples[0].device_type, device_ids(samples[0])
            if any(s.user_id != user_id or not matches_group(s, input_type, ids)
                   or validity_errors(s) or self.samples.is_trial(s.sample_id) for s in samples):
                raise ValueError("References must be valid enrollment samples from one participant and device group")
            group = device_group(input_type, ids)
            revision = self.connection.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 FROM reference_sets WHERE user_id = ? AND device_group = ?",
                (user_id, group)).fetchone()[0]
            set_id = new_id()
            self.connection.execute("INSERT INTO reference_sets VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                                    (set_id, user_id, revision, input_type, json.dumps(ids), group, utc_now()))
            self.connection.executemany("INSERT INTO reference_set_samples VALUES (?, ?, ?)",
                                        [(set_id, sample_id, i) for i, sample_id in enumerate(sample_ids)])
            self.connection.execute("UPDATE reference_sets SET sealed = 1 WHERE reference_set_id = ?", (set_id,))
            return self.get(set_id)

    def create_groups(self, user_id: str, sample_ids: list[str]) -> list[ReferenceSet]:
        with atomic(self.connection):
            groups = {}
            for sample_id in sample_ids:
                sample = self.samples.get(sample_id)
                groups.setdefault((sample.device_type, device_ids(sample)), []).append(sample_id)
            return [self.create(user_id, ids) for ids in groups.values()]

    def get(self, reference_set_id: str) -> ReferenceSet:
        row = self.connection.execute(
            "SELECT * FROM reference_sets WHERE reference_set_id = ? AND sealed = 1", (reference_set_id,)).fetchone()
        if row is None:
            raise StoredDataError("Published reference set does not exist")
        members = self.connection.execute(
            "SELECT sample_id, ordinal FROM reference_set_samples WHERE reference_set_id = ? ORDER BY ordinal",
            (reference_set_id,)).fetchall()
        try:
            ids = json.loads(row["device_ids"])
            if not isinstance(ids, list) or any(not isinstance(v, str) for v in ids) or sorted(set(ids)) != ids:
                raise ValueError("Invalid reported device identifiers")
            if not members or any(member["ordinal"] != i for i, member in enumerate(members)):
                raise ValueError("Incomplete reference membership")
            if row["device_group"] != device_group(row["device_type"], tuple(ids)):
                raise ValueError("Inconsistent device group")
            return ReferenceSet(row["reference_set_id"], row["user_id"], row["revision"], row["device_type"],
                                tuple(ids), tuple(m["sample_id"] for m in members), row["created_at"])
        except (TypeError, ValueError) as exc:
            raise StoredDataError("Reference set is malformed") from exc

    def list_for_user(self, user_id: str) -> list[ReferenceSet]:
        return [self.get(row[0]) for row in self.connection.execute(
            "SELECT reference_set_id FROM reference_sets WHERE user_id = ? AND sealed = 1 ORDER BY device_group, revision",
            (user_id,))]

    def latest_for_user(self, user_id: str) -> list[ReferenceSet]:
        rows = self.connection.execute(
            "SELECT r.reference_set_id FROM reference_sets r WHERE r.user_id = ? AND r.sealed = 1 "
            "AND r.revision = (SELECT MAX(s.revision) FROM reference_sets s "
            "WHERE s.user_id = r.user_id AND s.device_group = r.device_group AND s.sealed = 1) ORDER BY r.device_group",
            (user_id,)).fetchall()
        return [self.get(row[0]) for row in rows]

    def samples_for(self, reference_set_id: str) -> list[SignatureSample]:
        return [self.samples.get(sample_id) for sample_id in self.get(reference_set_id).sample_ids]


SCORE_FIELDS = ("overall_score", "dtw_score", "duration_score", "geometry_score", "stroke_score")


def validate_result(result: dict, threshold: float) -> None:
    encode_object(result)
    for name in SCORE_FIELDS:
        value = result.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not 0 <= value <= 1:
            raise ValueError("Match snapshots require finite similarity components in [0, 1]")
    if not isinstance(result.get("accepted"), bool) or result["accepted"] != (result["overall_score"] >= threshold):
        raise ValueError("Saved decision must agree with the saved threshold")
    if not isinstance(result.get("diagnostics"), dict):
        raise ValueError("Match diagnostics must be a JSON object")


class ResearchTrialRepository:
    def __init__(self, database: Database) -> None:
        self.connection = database.connection
        self.samples = SampleRepository(database)
        self.references = ReferenceSetRepository(database)

    def _validate(self, candidate: SignatureSample, trial: ResearchTrial, *, for_save: bool = True) -> None:
        for value, label in ((trial.trial_id, "Trial ID"), (trial.created_at, "Creation time"),
                             (trial.matcher_version, "Matcher version")):
            require_text(value, label)
        if trial.consent_confirmed is not True:
            raise ValueError("Research collection requires consent confirmation")
        if trial.attempt_type not in {"genuine", "other_participant", "imitation", "unlabelled"}:
            raise ValueError("Unknown declared attempt type")
        if not isinstance(trial.notes, str):
            raise ValueError("Trial notes must be text")
        if trial.session_id is not None:
            require_text(trial.session_id, "Session ID")
        if (candidate.user_id is not None or candidate.sample_id != trial.candidate_sample_id
                or for_save and validity_errors(candidate)):
            raise ValueError("Trial candidates must be valid, separate samples")
        if candidate.metadata.get("session_id") != trial.session_id:
            raise ValueError("Trial session must match the captured sample session")
        if (trial.attempt_type == "genuine" and trial.actual_signer_id is not None
                and trial.actual_signer_id != trial.claimed_user_id):
            raise ValueError("A genuine declaration must agree with the reported signer")
        if (trial.attempt_type in {"other_participant", "imitation"}
                and trial.actual_signer_id == trial.claimed_user_id):
            raise ValueError("An impostor declaration requires a different reported signer")
        if trial.attempt_type == "other_participant" and trial.actual_signer_id is None:
            raise ValueError("Other-participant trials require a reported signer")
        reference = self.references.get(trial.reference_set_id)
        compatible = (matches_group(candidate, reference.device_type, reference.device_ids) if for_save
                      else input_group(candidate) == (reference.device_type, reference.device_ids))
        if reference.user_id != trial.claimed_user_id or not compatible:
            raise ValueError("Claim and candidate device must match the frozen reference set")
        if for_save and len(reference.sample_ids) < CAPTURE_CONFIG.enrollment_samples:
            raise ValueError("Trial requires a complete compatible enrollment reference set")
        if candidate.sample_id in reference.sample_ids:
            raise ValueError("Trial candidate cannot be its own reference")
        if for_save and any(validity_errors(s) for s in self.references.samples_for(reference.reference_set_id)):
            raise ValueError("Frozen enrollment contains invalid references")
        encode_object(trial.matcher_config)
        if set(trial.matcher_config) != {f.name for f in fields(MatchConfig)}:
            raise ValueError("Store the complete matcher configuration")
        config = MatchConfig(**trial.matcher_config)
        validate_result(trial.result, config.threshold)
        encode_object(trial.comparisons)
        if set(trial.comparisons) != set(reference.sample_ids):
            raise ValueError("Per-reference results must cover exactly the frozen reference set")
        for result in trial.comparisons.values():
            validate_result(result, config.threshold)
        for name in SCORE_FIELDS:
            expected = median(result[name] for result in trial.comparisons.values())
            if abs(trial.result[name] - expected) > 1e-9:
                raise ValueError("Aggregate components must match the stored median comparisons")

    def save(self, candidate: SignatureSample, trial: ResearchTrial) -> ResearchTrial:
        """Atomically insert a fresh candidate and its immutable, explicit trial."""
        with atomic(self.connection):
            self._validate(candidate, trial)
            stored_candidate = replace(candidate, metadata={**candidate.metadata, "purpose": "research_trial"})
            self.samples.save(stored_candidate)
            self.connection.execute(
                "INSERT INTO research_trials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (trial.trial_id, trial.candidate_sample_id, trial.claimed_user_id, trial.actual_signer_id,
                 trial.reference_set_id, trial.attempt_type, trial.session_id, int(trial.consent_confirmed),
                 trial.notes, trial.matcher_version, encode_object(trial.matcher_config), encode_object(trial.result),
                 encode_object(trial.comparisons), trial.created_at))
            return self.get(trial.trial_id)

    def get(self, trial_id: str) -> ResearchTrial:
        row = self.connection.execute("SELECT * FROM research_trials WHERE trial_id = ?", (trial_id,)).fetchone()
        if row is None:
            raise StoredDataError("Research trial does not exist")
        trial = ResearchTrial(
            candidate_sample_id=row["candidate_sample_id"], claimed_user_id=row["claimed_user_id"],
            reference_set_id=row["reference_set_id"], attempt_type=row["attempt_type"], session_id=row["session_id"],
            matcher_version=row["matcher_version"], matcher_config=decode_object(row["matcher_config"]),
            result=decode_object(row["result"]), comparisons=decode_object(row["comparisons"]),
            consent_confirmed=bool(row["consent_confirmed"]), actual_signer_id=row["actual_signer_id"],
            notes=row["notes"], trial_id=row["trial_id"], created_at=row["created_at"])
        try:
            # Capture-policy changes must not invalidate already recorded evidence.
            self._validate(self.samples.get(trial.candidate_sample_id), trial, for_save=False)
            return trial
        except (TypeError, ValueError, OverflowError) as exc:
            raise StoredDataError("Research trial is malformed") from exc

    def list_trials(self, claimed_user_id: str | None = None) -> list[ResearchTrial]:
        return [self.get(row["trial_id"]) for row in self.list_summaries(claimed_user_id)]

    def list_summaries(self, claimed_user_id: str | None = None) -> list:
        """List selection metadata without decoding every result snapshot."""
        return self.connection.execute(
            "SELECT trial_id, created_at, attempt_type FROM research_trials "
            "WHERE (? IS NULL OR claimed_user_id = ?) ORDER BY created_at, trial_id",
            (claimed_user_id, claimed_user_id)).fetchall()


class EvaluationRunRepository:
    def __init__(self, database: Database) -> None:
        self.connection = database.connection
        self.trials = ResearchTrialRepository(database)

    def save(self, run: EvaluationRun) -> EvaluationRun:
        for value, label in ((run.run_id, "Run ID"), (run.name, "Run name"),
                             (run.matcher_version, "Matcher version"), (run.created_at, "Creation time")):
            require_text(value, label)
        if len(set(run.trial_ids)) != len(run.trial_ids):
            raise ValueError("An evaluation selection cannot repeat a trial")
        with atomic(self.connection):
            for trial_id in run.trial_ids:
                self.trials.get(trial_id)
            self.connection.execute("INSERT INTO evaluation_runs VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                                    (run.run_id, run.name, encode_object(run.selection), run.matcher_version,
                                     encode_object(run.matcher_config), encode_object(run.results), run.created_at))
            self.connection.executemany("INSERT INTO evaluation_run_trials VALUES (?, ?, ?)",
                                        [(run.run_id, trial_id, i) for i, trial_id in enumerate(run.trial_ids)])
            self.connection.execute("UPDATE evaluation_runs SET sealed = 1 WHERE run_id = ?", (run.run_id,))
            return self.get(run.run_id)

    def get(self, run_id: str) -> EvaluationRun:
        row = self.connection.execute("SELECT * FROM evaluation_runs WHERE run_id = ? AND sealed = 1", (run_id,)).fetchone()
        if row is None:
            raise StoredDataError("Evaluation run does not exist")
        members = self.connection.execute(
            "SELECT trial_id, ordinal FROM evaluation_run_trials WHERE run_id = ? ORDER BY ordinal", (run_id,)).fetchall()
        if any(member["ordinal"] != i for i, member in enumerate(members)):
            raise StoredDataError("Evaluation selection is incomplete")
        run = EvaluationRun(name=row["name"], trial_ids=tuple(m["trial_id"] for m in members),
                            selection=decode_object(row["selection"]), matcher_version=row["matcher_version"],
                            matcher_config=decode_object(row["matcher_config"]), results=decode_object(row["results"]),
                            run_id=row["run_id"], created_at=row["created_at"])
        try:
            for value in (run.selection, run.matcher_config, run.results):
                encode_object(value)
            return run
        except (TypeError, ValueError) as exc:
            raise StoredDataError("Evaluation snapshot is malformed") from exc

    def list_runs(self) -> list[EvaluationRun]:
        return [self.get(row["run_id"]) for row in self.list_summaries()]

    def list_summaries(self) -> list:
        return self.connection.execute(
            "SELECT run_id, name, created_at FROM evaluation_runs WHERE sealed = 1 ORDER BY created_at, run_id").fetchall()

"""Parameterized SQL and strict reconstruction of raw records."""
from dataclasses import astuple, fields
import json
import sqlite3

from stroke_key.models.signature import SignaturePoint, SignatureSample, measurement_errors
from stroke_key.models.user import User
from stroke_key.storage.database import Database
from stroke_key.storage.transactions import atomic


class StoredDataError(ValueError):
    """Stored record cannot safely be interpreted."""


def decode_object(value: str) -> dict:
    try:
        result = json.loads(value)
        if not isinstance(result, dict):
            raise ValueError("Expected JSON object")
        return result
    except (TypeError, ValueError) as exc:
        raise StoredDataError("Stored metadata is malformed") from exc


class SampleRepository:
    def __init__(self, database: Database) -> None:
        self.connection = database.connection

    def _insert(self, sample: SignatureSample) -> None:
        if measurement_errors(sample):
            raise ValueError("Invalid signature measurements")
        self.connection.execute(
            "INSERT INTO signature_samples VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sample.sample_id, sample.user_id, sample.created_at, sample.device_type,
             sample.total_duration, sample.number_of_strokes,
             json.dumps(sample.metadata, allow_nan=False)))
        self.connection.executemany(
            "INSERT INTO signature_points VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(sample.sample_id, index, *astuple(point)) for index, point in enumerate(sample.points)])

    def save(self, sample: SignatureSample) -> None:
        with atomic(self.connection):
            self._insert(sample)

    def list_summaries(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT s.*, COUNT(p.point_index) AS point_count, "
            "EXISTS (SELECT 1 FROM research_trials t WHERE t.candidate_sample_id = s.sample_id) AS is_research_trial "
            "FROM signature_samples s "
            "LEFT JOIN signature_points p ON s.sample_id = p.sample_id "
            "GROUP BY s.sample_id ORDER BY s.created_at DESC").fetchall()

    def get(self, sample_id: str) -> SignatureSample:
        row = self.connection.execute("SELECT * FROM signature_samples WHERE sample_id = ?", (sample_id,)).fetchone()
        if row is None:
            raise StoredDataError("Sample no longer exists")
        rows = self.connection.execute(
            "SELECT * FROM signature_points WHERE sample_id = ? ORDER BY point_index", (sample_id,)).fetchall()
        try:
            if any(p["point_index"] != i for i, p in enumerate(rows)):
                raise ValueError("Point ordering is incomplete")
            points = [SignaturePoint(**{field.name: p[field.name] for field in fields(SignaturePoint)}) for p in rows]
            sample = SignatureSample(points, row["sample_id"], row["user_id"], row["created_at"],
                                     row["device_type"], decode_object(row["metadata"]))
            if (measurement_errors(sample) or abs(sample.total_duration - row["total_duration"]) > 1e-6
                    or sample.number_of_strokes != row["number_of_strokes"]):
                raise ValueError("Inconsistent measurements")
            return sample
        except (TypeError, ValueError, OverflowError) as exc:
            raise StoredDataError("Sample measurements are malformed") from exc

    def for_user(self, user_id: str) -> list[SignatureSample]:
        rows = self.connection.execute(
            "SELECT sample_id FROM signature_samples WHERE user_id = ? ORDER BY created_at", (user_id,)).fetchall()
        return [self.get(row[0]) for row in rows]

    def enrollment_references(self, user_id: str) -> list[SignatureSample]:
        """Only published latest reference revisions contribute to live verification."""
        rows = self.connection.execute(
            "SELECT m.sample_id FROM reference_set_samples m JOIN reference_sets r "
            "ON r.reference_set_id = m.reference_set_id WHERE r.user_id = ? AND r.sealed = 1 "
            "AND r.revision = (SELECT MAX(s.revision) FROM reference_sets s "
            "WHERE s.user_id = r.user_id AND s.device_group = r.device_group AND s.sealed = 1) "
            "ORDER BY r.device_group, m.ordinal", (user_id,)).fetchall()
        return [self.get(row[0]) for row in rows]

    def is_trial(self, sample_id: str) -> bool:
        return self.connection.execute("SELECT 1 FROM research_trials WHERE candidate_sample_id = ?",
                                       (sample_id,)).fetchone() is not None

    def delete_unassigned(self, sample_id: str) -> None:
        with atomic(self.connection):
            if self.is_trial(sample_id):
                raise ValueError("Research trials cannot be removed through enrollment draft retry")
            self.connection.execute("DELETE FROM signature_samples WHERE sample_id = ? AND user_id IS NULL", (sample_id,))


class UserRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.connection = database.connection

    def list_users(self) -> list[User]:
        return [User(r["name"], r["user_id"], r["created_at"], decode_object(r["enrollment_statistics"]))
                for r in self.connection.execute("SELECT * FROM users ORDER BY name")]

    def get(self, user_id: str) -> User:
        row = self.connection.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            raise StoredDataError("Participant no longer exists")
        return User(row["name"], row["user_id"], row["created_at"], decode_object(row["enrollment_statistics"]))

    def attach_drafts(self, user_id: str, sample_ids: list[str]) -> None:
        """Attach explicit drafts; the caller publishes versions in the same transaction."""
        with atomic(self.connection):
            self.get(user_id)
            for sample_id in sample_ids:
                cursor = self.connection.execute(
                    "UPDATE signature_samples SET user_id = ? WHERE sample_id = ? AND user_id IS NULL "
                    "AND NOT EXISTS (SELECT 1 FROM research_trials WHERE candidate_sample_id = ?)",
                    (user_id, sample_id, sample_id))
                if cursor.rowcount != 1:
                    raise ValueError("An enrollment draft is missing or already assigned")

    def update_statistics(self, user_id: str, statistics: dict) -> None:
        with atomic(self.connection):
            cursor = self.connection.execute("UPDATE users SET enrollment_statistics = ? WHERE user_id = ?",
                                             (json.dumps(statistics, allow_nan=False), user_id))
            if cursor.rowcount != 1:
                raise StoredDataError("Participant no longer exists")

    def enroll(self, user: User, samples: list[SignatureSample]) -> None:
        """Associate staged samples and identity atomically; failure leaves drafts intact."""
        from stroke_key.storage.research_repositories import ReferenceSetRepository

        with atomic(self.connection):
            self.connection.execute("INSERT INTO users VALUES (?, ?, ?, ?)",
                                    (user.user_id, user.name, user.created_at,
                                     json.dumps(user.enrollment_statistics, allow_nan=False)))
            for sample in samples:
                cursor = self.connection.execute(
                    "UPDATE signature_samples SET user_id = ? WHERE sample_id = ? AND user_id IS NULL",
                    (user.user_id, sample.sample_id))
                if cursor.rowcount != 1:
                    raise ValueError("An enrollment draft is missing or already assigned")
            ReferenceSetRepository(self.database).create_groups(user.user_id, [s.sample_id for s in samples])

from dataclasses import replace
import sqlite3
import pytest

from stroke_key.models.user import User
from stroke_key.services.enrollment import EnrollmentService
from stroke_key.services.verification import VerificationService
from stroke_key.storage.database import Database
from stroke_key.storage.repositories import SampleRepository, StoredDataError, UserRepository


def test_round_trip_and_reopen(tmp_path, sample_factory):
    path = tmp_path / "test.db"
    database = Database(path)
    repository = SampleRepository(database)
    sample = sample_factory(strokes=2, pressure=0.5)
    sample.points[0] = replace(sample.points[0], tilt_x=12, tilt_y=-8, orientation=4,
                               stylus_buttons=1, device_id="synthetic-device")
    repository.save(sample)
    database.close()
    database = Database(path)
    try:
        repository = SampleRepository(database)
        assert repository.get(sample.sample_id) == sample
        assert repository.list_summaries()[0]["point_count"] == 60
    finally:
        database.close()


def test_enroll_verify_and_retry(tmp_path, sample_factory):
    database = Database(tmp_path / "test.db")
    try:
        samples, users = SampleRepository(database), UserRepository(database)
        service = EnrollmentService(users, samples)
        draft = sample_factory()
        service.stage(draft)
        samples.delete_unassigned(draft.sample_id)
        assert not samples.list_summaries()
        references = [sample_factory(offset=(i * 10, 0)) for i in range(5)]
        for sample in references:
            service.stage(sample)
        user = service.complete("Synthetic test participant", [r.sample_id for r in references])
        assert len(users.list_users()) == 1
        assert user.enrollment_statistics["sample_count"] == 5
        assert len(samples.for_user(user.user_id)) == 5
        report = VerificationService(samples).verify(user.user_id, sample_factory())
        assert report.result.accepted
        assert len(report.comparisons) == 5
        samples.delete_unassigned(references[0].sample_id)
        assert len(samples.for_user(user.user_id)) == 5
    finally:
        database.close()


def test_foreign_keys_and_transaction_rollback(tmp_path, sample_factory):
    database = Database(tmp_path / "test.db")
    try:
        samples, users = SampleRepository(database), UserRepository(database)
        bad = sample_factory()
        bad.user_id = "missing"
        with pytest.raises(sqlite3.IntegrityError):
            samples.save(bad)
        assert not samples.list_summaries()
        sample = sample_factory()
        samples.save(sample)
        with pytest.raises(ValueError):
            users.enroll(User("Synthetic"), [sample, sample_factory()])
        assert not users.list_users()
        assert samples.get(sample.sample_id).user_id is None
    finally:
        database.close()


def test_malformed_storage(tmp_path, sample_factory):
    database = Database(tmp_path / "test.db")
    try:
        repository = SampleRepository(database)
        sample = sample_factory()
        repository.save(sample)
        database.connection.execute("UPDATE signature_samples SET metadata = ?", ("[]",))
        with pytest.raises(StoredDataError):
            repository.get(sample.sample_id)
    finally:
        database.close()


def test_incomplete_enrollment(tmp_path, sample_factory):
    database = Database(tmp_path / "test.db")
    try:
        service = EnrollmentService(UserRepository(database), SampleRepository(database))
        with pytest.raises(ValueError):
            service.complete("Synthetic", [])
        with pytest.raises(ValueError):
            VerificationService(service.samples).verify("missing", sample_factory())
    finally:
        database.close()

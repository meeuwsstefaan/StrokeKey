"""Enrollment drafts are persisted after each accepted sample."""
from statistics import mean, pstdev

from stroke_key.config import CAPTURE_CONFIG
from stroke_key.models.signature import SignatureSample, validity_errors
from stroke_key.models.user import User
from stroke_key.processing.features import extract_features
from stroke_key.storage.repositories import SampleRepository, UserRepository


class EnrollmentService:
    def __init__(self, users: UserRepository, samples: SampleRepository) -> None:
        self.users, self.samples = users, samples

    def stage(self, sample: SignatureSample) -> None:
        errors = validity_errors(sample)
        if errors:
            raise ValueError("\n".join(errors))
        sample.metadata["purpose"] = "enrollment_draft"
        self.samples.save(sample)

    def complete(self, name: str, sample_ids: list[str]) -> User:
        if not name.strip() or len(name.strip()) > 120:
            raise ValueError("Enter a name between 1 and 120 characters.")
        if len(sample_ids) != CAPTURE_CONFIG.enrollment_samples or len(set(sample_ids)) != len(sample_ids):
            raise ValueError("Enrollment requires five distinct samples.")
        samples = [self.samples.get(sample_id) for sample_id in sample_ids]
        if any(validity_errors(sample) or sample.user_id is not None for sample in samples):
            raise ValueError("Enrollment references are invalid or already assigned.")
        features = [extract_features(sample) for sample in samples]
        durations = [f.total_duration for f in features]
        user = User(name.strip(), enrollment_statistics={
            "sample_count": len(samples), "duration_mean": mean(durations),
            "duration_std": pstdev(durations),
            "path_length_mean": mean(f.total_path_length for f in features),
            "stroke_count_mean": mean(f.number_of_strokes for f in features)})
        self.users.enroll(user, samples)
        return user

"""Guided initial enrollment and atomic later-session reference extensions."""
from dataclasses import asdict, dataclass
from statistics import mean, pstdev

from stroke_key.config import CAPTURE_CONFIG
from stroke_key.models.signature import SignatureSample, validity_errors
from stroke_key.models.user import User
from stroke_key.processing.compatibility import input_group, matches_group
from stroke_key.processing.features import extract_features
from stroke_key.processing.profiles import PROFILE_VERSION, summarize_profile
from stroke_key.storage.research_repositories import ReferenceSetRepository
from stroke_key.storage.repositories import SampleRepository, UserRepository
from stroke_key.storage.transactions import atomic


@dataclass(frozen=True)
class GroupProgress:
    device_type: str
    device_ids: tuple[str, ...]
    existing_count: int
    added_count: int

    @property
    def total_count(self) -> int:
        return self.existing_count + self.added_count


@dataclass(frozen=True)
class EnrollmentProgress:
    groups: tuple[GroupProgress, ...]
    added_count: int

    @property
    def ready(self) -> bool:
        return self.added_count > 0 and all(
            group.total_count >= CAPTURE_CONFIG.enrollment_samples
            for group in self.groups if group.added_count)

    def summary(self) -> str:
        lines = [f"{self.added_count} new drafts saved. Minimum {CAPTURE_CONFIG.enrollment_samples} references per compatible device group."]
        for group in self.groups:
            device = ", ".join(group.device_ids) or "no reported device ID"
            lines.append(f"{group.device_type} ({device}): {group.existing_count} existing + "
                         f"{group.added_count} new = {group.total_count}")
        if self.ready:
            lines.append("Ready to complete. You may keep capturing additional samples.")
        elif self.added_count:
            lines.append("Capture more samples for each new or incomplete device group.")
        else:
            lines.append("Capture at least one new sample to begin.")
        return "\n".join(lines)


def enrollment_statistics(samples: list[SignatureSample]) -> dict:
    features = [extract_features(sample) for sample in samples]
    durations = [f.total_duration for f in features]
    groups = sorted({input_group(s) for s in samples})
    profiles = [asdict(summarize_profile(samples, input_type, ids)) for input_type, ids in groups]
    return {
        "sample_count": len(samples), "duration_mean": mean(durations), "duration_std": pstdev(durations),
        "path_length_mean": mean(f.total_path_length for f in features),
        "stroke_count_mean": mean(f.number_of_strokes for f in features),
        "analysis_profile_version": PROFILE_VERSION, "signing_profiles": profiles,
    }


class EnrollmentService:
    def __init__(self, users: UserRepository, samples: SampleRepository) -> None:
        self.users, self.samples = users, samples
        self.references = ReferenceSetRepository(users.database)

    def stage(self, sample: SignatureSample) -> None:
        errors = validity_errors(sample)
        if errors:
            raise ValueError("\n".join(errors))
        if sample.user_id is not None or self.samples.is_trial(sample.sample_id):
            raise ValueError("Only new unassigned captures can be enrollment drafts.")
        if sample.device_type == "mixed":
            raise ValueError("Use one input type for an enrollment capture; this sample mixes input types.")
        if sample.metadata.get("research_label") in {"imitation", "other_participant"}:
            raise ValueError("Samples declared as impostor attempts cannot be enrollment references.")
        sample.metadata["purpose"] = "enrollment_draft"
        self.samples.save(sample)

    def _drafts(self, sample_ids: list[str]) -> list[SignatureSample]:
        if len(set(sample_ids)) != len(sample_ids):
            raise ValueError("Enrollment requires distinct samples.")
        samples = [self.samples.get(sample_id) for sample_id in sample_ids]
        if any(validity_errors(s) or s.user_id is not None or self.samples.is_trial(s.sample_id)
               or s.metadata.get("purpose") != "enrollment_draft" or s.device_type == "mixed"
               or s.metadata.get("research_label") in {"imitation", "other_participant"} for s in samples):
            raise ValueError("Enrollment references are invalid or already assigned.")
        return samples

    def guidance_references(self, sample_ids: list[str], user_id: str | None = None) -> list[SignatureSample]:
        drafts = self._drafts(sample_ids)
        if user_id is None:
            return drafts
        self.users.get(user_id)
        return self.samples.enrollment_references(user_id) + drafts

    def progress(self, sample_ids: list[str], user_id: str | None = None) -> EnrollmentProgress:
        drafts = self._drafts(sample_ids)
        existing = []
        if user_id is not None:
            self.users.get(user_id)
            existing = self.samples.enrollment_references(user_id)
        counts = {}
        for sample in existing:
            if sample.device_type != "mixed" and not validity_errors(sample):
                counts.setdefault(input_group(sample), [0, 0])[0] += 1
        for sample in drafts:
            counts.setdefault(input_group(sample), [0, 0])[1] += 1
        groups = tuple(GroupProgress(kind, ids, values[0], values[1])
                       for (kind, ids), values in sorted(counts.items()))
        return EnrollmentProgress(groups, len(drafts))

    def complete(self, name: str, sample_ids: list[str]) -> User:
        if not name.strip() or len(name.strip()) > 120:
            raise ValueError("Enter a name between 1 and 120 characters.")
        with atomic(self.users.connection):
            if not self.progress(sample_ids).ready:
                raise ValueError(f"Enrollment requires at least {CAPTURE_CONFIG.enrollment_samples} distinct valid samples per compatible device group.")
            samples = self._drafts(sample_ids)
            user = User(name.strip(), enrollment_statistics=enrollment_statistics(samples))
            self.users.enroll(user, samples)
            return user

    def extend(self, user_id: str, sample_ids: list[str]) -> User:
        """Append explicit drafts to affected latest versions; archive earlier versions."""
        with atomic(self.users.connection):
            user = self.users.get(user_id)
            if not self.progress(sample_ids, user_id).ready:
                raise ValueError(f"Add samples until every affected device group has at least {CAPTURE_CONFIG.enrollment_samples} valid references.")
            drafts = self._drafts(sample_ids)
            previous = self.samples.enrollment_references(user_id)
            groups = sorted({input_group(s) for s in drafts})
            # Do not silently discard invalid historical references in an affected group.
            for kind, ids in groups:
                if any(validity_errors(s) for s in previous if matches_group(s, kind, ids)):
                    raise ValueError("Existing references in this device group are invalid. Review the stored data; your drafts remain available.")
            self.users.attach_drafts(user_id, sample_ids)
            for kind, ids in groups:
                combined = [s.sample_id for s in previous if matches_group(s, kind, ids)]
                combined.extend(s.sample_id for s in drafts if matches_group(s, kind, ids))
                self.references.create(user_id, combined)
            statistics = {**user.enrollment_statistics,
                          **enrollment_statistics(self.samples.enrollment_references(user_id))}
            self.users.update_statistics(user_id, statistics)
            return self.users.get(user_id)

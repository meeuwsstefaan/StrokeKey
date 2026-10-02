"""Guidance and explicit, version-preserving enrollment across sessions/devices."""
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from stroke_key.config import MATCH_CONFIG
from stroke_key.models.research import EvaluationRun, ResearchTrial
from stroke_key.processing.compatibility import compatible_inputs, input_group
from stroke_key.processing.enrollment_guidance import assess_enrollment
from stroke_key.processing.matcher import MATCHER_VERSION
from stroke_key.processing.profiles import build_profile
from stroke_key.services.enrollment import EnrollmentService
from stroke_key.services.verification import VerificationService, compare_references
from stroke_key.storage.database import Database
from stroke_key.storage.repositories import SampleRepository, UserRepository
from stroke_key.storage.research_repositories import ReferenceSetRepository, ResearchTrialRepository, EvaluationRunRepository


@pytest.fixture
def enrollment(tmp_path):
    database = Database(tmp_path / "enrollment.db")
    samples, users = SampleRepository(database), UserRepository(database)
    yield SimpleNamespace(db=database, samples=samples, users=users, service=EnrollmentService(users, samples),
                          references=ReferenceSetRepository(database))
    database.close()


def stage_batch(enrollment, sample_factory, count=5, session="session-1", device_type="synthetic", device_id=None):
    samples = [sample_factory(duration=1 + index / 10) for index in range(count)]
    for sample in samples:
        sample.device_type = device_type
        sample.points = [replace(p, device_type=device_type, device_id=device_id) for p in sample.points]
        sample.metadata.update(session_id=session, research_label="synthetic")
        enrollment.service.stage(sample)
    return samples


def test_initial_enrollment_allows_more_than_five_and_keeps_advisory_samples(enrollment, sample_factory):
    drafts = stage_batch(enrollment, sample_factory, count=6)
    unusual = sample_factory(duration=10)
    unusual.metadata.update(session_id="session-1", research_label="genuine")
    guidance = assess_enrollment(unusual, drafts)
    assert guidance.needs_review
    assert any(c.name == "total_duration" for c in guidance.differences)
    enrollment.service.stage(unusual)
    drafts.append(unusual)
    assert enrollment.service.progress([s.sample_id for s in drafts]).ready
    user = enrollment.service.complete("Synthetic varied signer", [s.sample_id for s in drafts])
    assert user.enrollment_statistics["sample_count"] == 7
    assert unusual.sample_id in enrollment.references.latest_for_user(user.user_id)[0].sample_ids
    assert enrollment.samples.get(unusual.sample_id).user_id == user.user_id


def test_each_device_group_needs_five_references(enrollment, sample_factory):
    a = stage_batch(enrollment, sample_factory, count=4)
    b = stage_batch(enrollment, sample_factory, count=1, device_type="stylus", device_id="pen-1")
    ids = [s.sample_id for s in a + b]
    progress = enrollment.service.progress(ids)
    assert not progress.ready
    with pytest.raises(ValueError, match="per compatible device group"):
        enrollment.service.complete("Synthetic", ids)
    assert not enrollment.users.list_users()
    a += stage_batch(enrollment, sample_factory, count=1)
    b += stage_batch(enrollment, sample_factory, count=4, device_type="stylus", device_id="pen-1")
    user = enrollment.service.complete("Synthetic two devices", [s.sample_id for s in a + b])
    assert len(enrollment.references.latest_for_user(user.user_id)) == 2
    refs = VerificationService(enrollment.samples).prepare(user.user_id, sample_factory())
    assert len(refs) == 5
    assert all(s.device_type == "synthetic" for s in refs)


def test_later_session_extension_appends_and_refreshes_statistics(enrollment, sample_factory):
    original = stage_batch(enrollment, sample_factory)
    user = enrollment.service.complete("Synthetic later session", [s.sample_id for s in original])
    first = enrollment.references.latest_for_user(user.user_id)[0]
    enrollment.users.update_statistics(user.user_id, {**user.enrollment_statistics, "custom_note": "preserve"})
    additions = stage_batch(enrollment, sample_factory, count=1, session="session-2")
    progress = enrollment.service.progress([s.sample_id for s in additions], user.user_id)
    assert progress.ready
    assert progress.groups[0].existing_count == 5
    assert progress.groups[0].added_count == 1
    updated = enrollment.service.extend(user.user_id, [s.sample_id for s in additions])
    current = enrollment.references.latest_for_user(user.user_id)[0]
    assert current.revision == 2
    assert current.sample_ids == first.sample_ids + (additions[0].sample_id,)
    assert enrollment.references.get(first.reference_set_id) == first
    assert updated.name == user.name and updated.created_at == user.created_at
    assert updated.enrollment_statistics["sample_count"] == 6
    assert updated.enrollment_statistics["custom_note"] == "preserve"
    assert updated.enrollment_statistics["signing_profiles"][0]["session_count"] == 2
    assert len(VerificationService(enrollment.samples).prepare(user.user_id, sample_factory())) == 6


def test_new_device_extension_waits_for_five_and_leaves_other_group_unchanged(enrollment, sample_factory):
    drafts = stage_batch(enrollment, sample_factory)
    user = enrollment.service.complete("Synthetic", [s.sample_id for s in drafts])
    original = enrollment.references.latest_for_user(user.user_id)[0]
    additions = stage_batch(enrollment, sample_factory, count=1, device_type="stylus", device_id="pen-2")
    with pytest.raises(ValueError, match="affected device group"):
        enrollment.service.extend(user.user_id, [s.sample_id for s in additions])
    assert enrollment.samples.get(additions[0].sample_id).user_id is None
    additions += stage_batch(enrollment, sample_factory, count=4, device_type="stylus", device_id="pen-2")
    enrollment.service.extend(user.user_id, [s.sample_id for s in additions])
    sets = enrollment.references.latest_for_user(user.user_id)
    assert len(sets) == 2
    assert original in sets
    assert all(s.revision == 1 for s in sets)


def test_unknown_device_does_not_count_as_known_device(enrollment, sample_factory):
    drafts = stage_batch(enrollment, sample_factory, device_type="stylus", device_id=None)
    user = enrollment.service.complete("Synthetic unknown device", [s.sample_id for s in drafts])
    additions = stage_batch(enrollment, sample_factory, count=1, device_type="stylus", device_id="pen-1")
    assert not compatible_inputs(drafts[0], additions[0])
    assert build_profile(additions[0], drafts).sample_count == 0
    assert not enrollment.service.progress([s.sample_id for s in additions], user.user_id).ready
    with pytest.raises(ValueError, match="reported device"):
        VerificationService(enrollment.samples).prepare(user.user_id, additions[0])


def test_guidance_is_leave_one_out_and_missing_sensors_stay_unavailable(sample_factory):
    samples = [sample_factory(duration=1 + index / 10) for index in range(5)]
    samples[-1] = sample_factory(duration=8)
    guidance = assess_enrollment(samples[-1], samples)
    assert guidance.analysis.profile.sample_count == 4
    assert samples[-1].sample_id not in guidance.analysis.profile.reference_ids
    duration = next(c for c in guidance.analysis.comparisons if c.name == "total_duration")
    assert duration.maximum == pytest.approx(1.3)
    assert duration.status == "Above observed range"
    assert next(c for c in guidance.analysis.comparisons if c.name == "average_pressure").status == "Unavailable"
    assert "Sign naturally" in guidance.summary()
    assert "advisory" in guidance.summary()
    assert "too few" in assess_enrollment(samples[0], samples[:2]).summary().lower()


def test_invalid_guidance_does_not_claim_features_match(sample_factory):
    sample = sample_factory()
    sample.metadata["interrupted"] = True
    report = assess_enrollment(sample, [sample_factory(), sample_factory()])
    assert "Resolve capture errors" in report.summary()
    assert "fall within" not in report.summary()


@pytest.mark.parametrize("invalid", ["mixed", "imitation", "duplicate", "assigned"])
def test_invalid_drafts_are_not_published(enrollment, sample_factory, invalid):
    sample = sample_factory()
    if invalid == "mixed":
        sample.device_type = "mixed"
    elif invalid == "imitation":
        sample.metadata["research_label"] = "imitation"
    elif invalid == "assigned":
        sample.user_id = "some-user"
    else:
        enrollment.service.stage(sample)
        with pytest.raises(ValueError, match="distinct"):
            enrollment.service.complete("Synthetic", [sample.sample_id] * 5)
        return
    with pytest.raises(ValueError):
        enrollment.service.stage(sample)
    assert not enrollment.samples.list_summaries()


def test_extension_failure_rolls_back_association_versions_and_statistics(enrollment, sample_factory, monkeypatch):
    original = stage_batch(enrollment, sample_factory)
    user = enrollment.service.complete("Synthetic rollback", [s.sample_id for s in original])
    first = enrollment.references.latest_for_user(user.user_id)[0]
    addition = stage_batch(enrollment, sample_factory, count=1, session="session-2")[0]
    old = enrollment.users.get(user.user_id)
    update = enrollment.users.update_statistics

    def fail_after_statistics(*args):
        update(*args)
        raise RuntimeError("Injected post-publication failure")

    monkeypatch.setattr(enrollment.users, "update_statistics", fail_after_statistics)
    with pytest.raises(RuntimeError, match="post-publication"):
        enrollment.service.extend(user.user_id, [addition.sample_id])
    assert enrollment.samples.get(addition.sample_id).user_id is None
    assert enrollment.references.list_for_user(user.user_id) == [first]
    assert enrollment.users.get(user.user_id) == old


def test_extension_preserves_trial_and_evaluation_snapshots(enrollment, sample_factory):
    original = stage_batch(enrollment, sample_factory)
    user = enrollment.service.complete("Synthetic frozen results", [s.sample_id for s in original])
    first = enrollment.references.latest_for_user(user.user_id)[0]
    candidate = sample_factory()
    candidate.metadata["session_id"] = "trial-session"
    report = compare_references(candidate, enrollment.references.samples_for(first.reference_set_id))
    trial = ResearchTrial(candidate_sample_id=candidate.sample_id, claimed_user_id=user.user_id,
                          reference_set_id=first.reference_set_id, attempt_type="genuine", session_id="trial-session",
                          matcher_version=MATCHER_VERSION, matcher_config=asdict(MATCH_CONFIG), result=asdict(report.result),
                          comparisons={sid: asdict(result) for sid, result in report.comparisons}, consent_confirmed=True)
    trials = ResearchTrialRepository(enrollment.db)
    trials.save(candidate, trial)
    runs = EvaluationRunRepository(enrollment.db)
    run = EvaluationRun(name="Frozen synthetic run", trial_ids=(trial.trial_id,), selection={},
                        matcher_version=MATCHER_VERSION, matcher_config=asdict(MATCH_CONFIG), results={"count": 1})
    runs.save(run)
    additions = stage_batch(enrollment, sample_factory, count=2, session="session-2")
    enrollment.service.extend(user.user_id, [s.sample_id for s in additions])
    assert trials.get(trial.trial_id) == trial
    assert runs.get(run.run_id) == run
    with pytest.raises(ValueError):
        enrollment.service.extend(user.user_id, [candidate.sample_id])

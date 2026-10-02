"""Synthetic evidence for capture diagnostics and descriptive personal profiles."""
from copy import deepcopy
from dataclasses import replace

import pytest

from stroke_key.models.signature import SignatureSample
from stroke_key.processing.analysis import analyze_sample
from stroke_key.processing.features import extract_features
from stroke_key.processing.profiles import build_profile, profile_features
from stroke_key.processing.quality import assess_quality
from stroke_key.services.enrollment import EnrollmentService
from stroke_key.storage.database import Database
from stroke_key.storage.repositories import SampleRepository, UserRepository


def test_quality_missing_sensors_is_not_invalid(sample_factory):
    report = assess_quality(sample_factory())
    assert not report.errors
    assert not report.warnings
    assert report.pressure_coverage == report.tilt_coverage == 0
    assert report.effective_rate_hz == pytest.approx(59)
    assert report.maximum_gap == pytest.approx(1 / 59)


def test_pen_up_gap_is_pause_not_sampling_loss(sample_factory):
    sample = sample_factory(strokes=2)
    sample.points = [replace(p, elapsed_time=p.elapsed_time + (0.6 if p.stroke_number == 2 else 0))
                     for p in sample.points]
    quality = assess_quality(sample)
    features = extract_features(sample)
    assert quality.gap_count == 0
    assert quality.maximum_gap == pytest.approx(1 / 59)
    assert len(quality.pauses) == features.pause_count == 1
    assert quality.pauses[0].end - quality.pauses[0].start == pytest.approx(features.total_pause_duration)


def test_sparse_gaps_duplicates_and_partial_pressure(sample_factory):
    sample = sample_factory(duration=10)
    sample.points[1] = replace(sample.points[1], elapsed_time=sample.points[0].elapsed_time, pressure=0.5)
    report = assess_quality(sample)
    assert not report.errors
    assert report.gap_count > 0
    assert report.duplicate_time_count == 1
    assert report.pressure_coverage == pytest.approx(1 / 60)
    assert any("Sparse" in warning for warning in report.warnings)
    assert any("partially missing" in warning for warning in report.warnings)


@pytest.mark.parametrize("kind", ["nan", "backward", "empty", "interrupted", "duplicate_time"])
def test_invalid_analysis_is_explicit_and_safe(sample_factory, kind):
    sample = sample_factory()
    if kind == "nan":
        sample.points[0] = replace(sample.points[0], x=float("nan"))
    elif kind == "backward":
        sample.points[4] = replace(sample.points[4], elapsed_time=-1)
    elif kind == "empty":
        sample = SignatureSample()
    elif kind == "duplicate_time":
        sample.points = [replace(p, elapsed_time=0) for p in sample.points]
    else:
        sample.metadata["interrupted"] = True
    report = analyze_sample(sample, [sample_factory(), sample_factory()])
    assert report.quality.errors
    assert not report.comparisons


def test_personal_profile_excludes_self_other_users_input_and_invalid(sample_factory):
    candidate = sample_factory(duration=8)
    candidate.user_id = "alice"
    references = [candidate]
    for duration in (1, 2):
        sample = sample_factory(duration=duration)
        sample.user_id = "alice"
        sample.metadata["session_id"] = f"session-{duration}"
        references.append(sample)
    foreign = replace(sample_factory(), user_id="bob")
    device = replace(sample_factory(), user_id="alice", device_type="stylus")
    invalid = replace(sample_factory(), user_id="alice", points=[])
    references.extend([foreign, device, invalid, references[1]])
    report = analyze_sample(candidate, references)
    assert report.profile.sample_count == 2
    assert report.profile.skipped_count == 5
    assert report.profile.session_count == 2
    duration = next(c for c in report.comparisons if c.name == "total_duration")
    assert duration.maximum == 2
    assert duration.reference_median == 1.5
    assert duration.status == "Above observed range"
    assert candidate.sample_id not in report.profile.reference_ids
    assert any("Duration: 8 s" in line for line in report.explanations)


def test_one_reference_and_legacy_sessions_do_not_claim_confidence(sample_factory):
    report = analyze_sample(sample_factory(), [sample_factory()])
    assert all(c.status in {"Insufficient references", "Unavailable"} for c in report.comparisons)
    assert report.profile.unknown_session_count == 1
    assert any("legacy" in line for line in report.explanations)
    assert any("two comparable" in line for line in report.explanations)


def test_profile_features_preserve_raw_and_normalize_spatial_scale(sample_factory):
    a, b = sample_factory(), sample_factory(scale=5, offset=(400, 300))
    original = deepcopy(b)
    report = analyze_sample(b, [a, sample_factory(scale=2)])
    assert b == original
    fa, fb = profile_features(a), profile_features(b)
    for name in fa:
        if fa[name] is not None:
            assert fb[name] == pytest.approx(fa[name], abs=1e-10)
    assert report.profile.sample_count == 2


def pressure_sample(sample_factory, device_id="pen-1"):
    sample = sample_factory()
    sample.device_type = "stylus"
    sample.points = [replace(p, pressure=0.2 + index / 100, device_id=device_id, device_type="stylus")
                     for index, p in enumerate(sample.points)]
    return sample


def test_pressure_requires_varying_complete_signal_and_matching_device(sample_factory):
    candidate = pressure_sample(sample_factory)
    references = [pressure_sample(sample_factory), pressure_sample(sample_factory)]
    report = analyze_sample(candidate, references)
    pressure = next(c for c in report.comparisons if c.name == "average_pressure")
    assert pressure.status == "Within observed range"
    assert pressure.reference_count == 2
    candidate.points[0] = replace(candidate.points[0], pressure=None)
    report = analyze_sample(candidate, references)
    assert next(c for c in report.comparisons if c.name == "average_pressure").status == "Unavailable"
    constant = pressure_sample(sample_factory)
    constant.points = [replace(p, pressure=0.5) for p in constant.points]
    assert profile_features(constant)["average_pressure"] is None
    assert any("constant" in w for w in assess_quality(constant).warnings)
    foreign = pressure_sample(sample_factory, "pen-2")
    assert build_profile(foreign, references).sample_count == 0
    unidentified = pressure_sample(sample_factory, None)
    assert profile_features(unidentified)["average_pressure"] is None


def test_enrollment_persists_versioned_profiles_and_session_metadata(tmp_path, sample_factory):
    database = Database(tmp_path / "analysis.db")
    try:
        samples, users = SampleRepository(database), UserRepository(database)
        service = EnrollmentService(users, samples)
        references = [sample_factory(duration=1 + index / 10) for index in range(5)]
        for sample in references:
            sample.metadata.update(session_id="session-1", research_label="genuine")
            service.stage(sample)
        user = service.complete("Synthetic participant", [s.sample_id for s in references])
        stored = users.list_users()[0]
        assert stored.user_id == user.user_id
        assert stored.enrollment_statistics["analysis_profile_version"] == 2
        profile = stored.enrollment_statistics["signing_profiles"][0]
        assert len(profile["reference_ids"]) == 5
        assert profile["session_count"] == 1
        assert profile["ranges"]["total_duration"]["maximum"] == pytest.approx(1.4)
        assert samples.get(references[0].sample_id).metadata["research_label"] == "genuine"
    finally:
        database.close()

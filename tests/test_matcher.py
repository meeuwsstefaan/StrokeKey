import pytest

from stroke_key.models.signature import SignatureSample
from stroke_key.processing.matcher import aggregate_matches, compare_signatures


def test_identical_and_translated(sample_factory):
    reference = sample_factory()
    identical = compare_signatures(reference, reference)
    assert identical.overall_score == pytest.approx(1)
    assert identical.accepted
    moved = compare_signatures(sample_factory(offset=(300, 150), scale=4), reference)
    assert moved.overall_score > 0.99


def test_different_shape_and_timing(sample_factory):
    reference = sample_factory()
    same = compare_signatures(reference, reference)
    different = compare_signatures(sample_factory(shape="line", duration=4, strokes=3), reference)
    assert different.overall_score < same.overall_score
    assert not different.accepted
    for result in (same, different):
        for field in ("overall_score", "dtw_score", "duration_score", "geometry_score", "stroke_score"):
            assert 0 <= getattr(result, field) <= 1


def test_invalid_rejected_even_against_itself():
    result = compare_signatures(SignatureSample(), SignatureSample())
    assert not result.accepted
    assert result.overall_score == 0


def test_median_aggregation(sample_factory):
    reference = sample_factory()
    good = compare_signatures(reference, reference)
    bad = compare_signatures(sample_factory(shape="line", duration=8), reference)
    assert aggregate_matches([good, good, bad]).overall_score == pytest.approx(1)
    assert not aggregate_matches([]).accepted

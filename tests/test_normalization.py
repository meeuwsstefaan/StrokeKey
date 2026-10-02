from dataclasses import replace
import numpy as np
import pytest

from stroke_key.models.signature import SignatureSample
from stroke_key.processing.normalize import normalize_signature


def test_translation_scale_and_raw_immutability(sample_factory):
    raw = sample_factory(offset=(230, 470), scale=3, strokes=2)
    before = list(raw.points)
    normalized = normalize_signature(raw)
    reference = normalize_signature(sample_factory(strokes=2))
    np.testing.assert_allclose([(p.x, p.y) for p in normalized.points],
                               [(p.x, p.y) for p in reference.points], atol=1e-12)
    assert raw.points == before
    assert normalized.number_of_strokes == 2
    assert [p.stroke_number for p in normalized.points] == [p.stroke_number for p in raw.points]
    assert normalized.total_duration == raw.total_duration
    assert [p.timestamp for p in normalized.points] == [p.timestamp for p in raw.points]


def test_custom_dimensions_and_time_origin(sample_factory):
    sample = sample_factory()
    sample.points = [replace(p, elapsed_time=p.elapsed_time + 4) for p in sample.points]
    result = normalize_signature(sample, 200, 100)
    assert result.points[0].elapsed_time == 0
    assert max(p.x for p in result.points) <= 200
    assert max(p.y for p in result.points) <= 100
    assert result.total_duration == pytest.approx(1)


def test_empty_and_single_point(sample_factory):
    assert normalize_signature(SignatureSample()).points == []
    sample = sample_factory()
    sample.points = sample.points[:1]
    assert normalize_signature(sample).points[0].x == 0


@pytest.mark.parametrize("width", [0, -1, float("nan")])
def test_bad_dimensions(width):
    with pytest.raises(ValueError):
        normalize_signature(SignatureSample(), width=width)

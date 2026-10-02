from dataclasses import asdict, replace
import math
import pytest

from stroke_key.models.signature import SignatureSample, validity_errors
from stroke_key.processing.features import extract_features


def test_linear_motion(sample_factory):
    sample = sample_factory(shape="line", duration=2)
    f = extract_features(sample)
    assert f.total_path_length == pytest.approx(math.sqrt(20000))
    assert f.average_velocity == pytest.approx(f.total_path_length / 2)
    assert f.maximum_velocity == pytest.approx(f.average_velocity)
    assert f.maximum_acceleration < 1e-8
    assert f.bounding_box_width == 100
    assert f.bounding_box_height == 100
    assert f.aspect_ratio == 1
    assert f.number_of_strokes == 1
    assert f.average_stroke_duration == 2
    assert f.average_pressure is None


def test_pen_up_jump_excluded_and_pause_count(sample_factory):
    sample = sample_factory(strokes=2)
    sample.points = [replace(p, x=p.x + (1000 if p.stroke_number == 2 else 0),
                             elapsed_time=p.elapsed_time + (0.5 if p.stroke_number == 2 else 0)) for p in sample.points]
    f = extract_features(sample)
    assert f.total_path_length < 400
    assert f.pause_count == 1
    assert f.total_pause_duration == pytest.approx(0.5 + 1 / 59)
    assert f.average_stroke_length == f.total_path_length / 2


def test_pressure(sample_factory):
    f = extract_features(sample_factory(pressure=0.4))
    assert f.average_pressure == pytest.approx(0.4)
    assert f.minimum_pressure == f.maximum_pressure == 0.4
    assert f.pressure_std == pytest.approx(0)


@pytest.mark.parametrize("kind", ["empty", "single", "duplicate_time", "nan", "backward", "bad_pressure"])
def test_edge_cases_remain_safe(sample_factory, kind):
    sample = sample_factory()
    if kind == "empty":
        sample = SignatureSample()
    elif kind == "single":
        sample.points = sample.points[:1]
    elif kind == "duplicate_time":
        sample.points = [replace(p, elapsed_time=0) for p in sample.points]
    elif kind == "nan":
        sample.points[0] = replace(sample.points[0], x=float("nan"))
    elif kind == "bad_pressure":
        sample.points[0] = replace(sample.points[0], pressure="corrupt")
    else:
        sample.points[10] = replace(sample.points[10], elapsed_time=-1)
    assert validity_errors(sample)
    assert all(math.isfinite(value) for value in asdict(extract_features(sample)).values() if value is not None)

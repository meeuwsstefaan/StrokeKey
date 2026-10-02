import numpy as np
import pytest

from stroke_key.processing.dtw import dtw_distance


def test_identical():
    points = [[0, 0], [1, 2], [2, 0]]
    assert dtw_distance(points, points) == 0


def test_different_lengths_and_similarity():
    a = np.column_stack((np.linspace(0, 1, 20), np.zeros(20)))
    b = np.column_stack((np.linspace(0, 1, 35), np.zeros(35)))
    assert dtw_distance(a, b) < 0.03
    assert dtw_distance(a, a + 5) > 5
    assert dtw_distance(a, b) == pytest.approx(dtw_distance(b, a))


def test_empty():
    assert np.isinf(dtw_distance([], [[1, 2]]))


@pytest.mark.parametrize("a,b", [([[1, 2]], [[1, 2, 3]]), ([1, 2], [[1, 2]]), ([[float('nan'), 1]], [[1, 2]])])
def test_invalid(a, b):
    with pytest.raises(ValueError):
        dtw_distance(a, b)

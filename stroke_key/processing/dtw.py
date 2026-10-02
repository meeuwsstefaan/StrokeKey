"""Euclidean DTW with O(m) memory and O(n*m) time."""
import numpy as np
from numpy.typing import ArrayLike


def dtw_distance(first: ArrayLike, second: ArrayLike) -> float:
    """Mean local cost on the minimum-total-cost warping path.

    Empty input returns infinity. Non-finite or incompatible arrays are rejected.
    Ties favor the shorter alignment; arbitrary feature dimensions are supported.
    """
    a, b = np.asarray(first, dtype=float), np.asarray(second, dtype=float)
    if a.size == 0 or b.size == 0:
        return float("inf")
    if a.ndim != 2 or b.ndim != 2 or a.shape[1] != b.shape[1]:
        raise ValueError("DTW requires two arrays with equal feature dimensions")
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("DTW requires finite values")
    costs = np.full(len(b) + 1, np.inf)
    steps = np.zeros(len(b) + 1, dtype=int)
    costs[0] = 0.0
    for point in a:
        new_costs = np.full(len(b) + 1, np.inf)
        new_steps = np.zeros(len(b) + 1, dtype=int)
        local = np.linalg.norm(b - point, axis=1)
        for j, distance in enumerate(local, 1):
            cost, count = min((costs[j - 1], steps[j - 1]),
                              (costs[j], steps[j]), (new_costs[j - 1], new_steps[j - 1]))
            new_costs[j], new_steps[j] = cost + distance, count + 1
        costs, steps = new_costs, new_steps
    return float(costs[-1] / steps[-1])

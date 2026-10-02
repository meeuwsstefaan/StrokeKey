"""Derive aspect-preserving normalization without mutating raw records."""
from dataclasses import replace
from math import isfinite

from stroke_key.models.signature import SignatureSample, measurement_errors


def normalize_signature(sample: SignatureSample, width: float = 1.0,
                        height: float = 1.0) -> SignatureSample:
    if not all(isfinite(v) and v > 0 for v in (width, height)):
        raise ValueError("Normalization dimensions must be positive and finite")
    if measurement_errors(sample):
        raise ValueError("Invalid signature measurements")
    if not sample.points:
        return replace(sample, points=[], metadata=dict(sample.metadata))
    min_x, min_y = min(p.x for p in sample.points), min(p.y for p in sample.points)
    span_x = max(p.x for p in sample.points) - min_x
    span_y = max(p.y for p in sample.points) - min_y
    scales = [size / span for size, span in ((width, span_x), (height, span_y)) if span > 0]
    scale = min(scales) if scales else 1.0
    start = sample.points[0].elapsed_time
    points = [replace(p, x=(p.x - min_x) * scale, y=(p.y - min_y) * scale,
                      elapsed_time=p.elapsed_time - start) for p in sample.points]
    return replace(sample, points=points, metadata={**sample.metadata, "normalized": True,
                                                   "normalization_scale": scale})

"""Synthetic measurements only; never use a person's real biometric data."""
import math
import pytest

from stroke_key.models.signature import SignaturePoint, SignatureSample


@pytest.fixture
def sample_factory():
    def make(offset=(0.0, 0.0), scale=1.0, duration=1.0, shape="wave", strokes=1, pressure=None):
        points = []
        for i in range(60):
            t = i / 59
            y = math.sin(t * math.pi * 4) * 30 if shape == "wave" else t * 100
            stroke = min(strokes, i * strokes // 60 + 1)
            state = "down" if i == 0 or stroke != points[-1].stroke_number else "move"
            if i == 59 or (i + 1) * strokes // 60 + 1 > stroke:
                state = "up"
            points.append(SignaturePoint(100 * t * scale + offset[0], y * scale + offset[1],
                                         1_700_000_000 + t * duration, t * duration,
                                         pressure, stroke, state))
        return SignatureSample(points=points, device_type="synthetic", metadata={"synthetic": True})
    return make

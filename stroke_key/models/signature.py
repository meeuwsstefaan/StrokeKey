"""Raw measurements use canvas pixels, UTC epoch seconds and elapsed seconds."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import isfinite
from typing import Any
from uuid import uuid4

from stroke_key.config import CAPTURE_CONFIG, CaptureConfig


@dataclass(frozen=True)
class SignaturePoint:
    x: float
    y: float
    timestamp: float
    elapsed_time: float
    pressure: float | None = None
    stroke_number: int = 1
    pointer_state: str = "move"
    device_type: str = "mouse"
    tilt_x: float | None = None
    tilt_y: float | None = None
    orientation: float | None = None
    stylus_buttons: int | None = None
    device_id: str | None = None


@dataclass
class SignatureSample:
    points: list[SignaturePoint] = field(default_factory=list)
    sample_id: str = field(default_factory=lambda: str(uuid4()))
    user_id: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    device_type: str = "unknown"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_duration(self) -> float:
        return max(0.0, self.points[-1].elapsed_time - self.points[0].elapsed_time) if self.points else 0.0

    @property
    def number_of_strokes(self) -> int:
        return len({p.stroke_number for p in self.points})


def measurement_errors(sample: SignatureSample) -> list[str]:
    """Validate finite, ordered measurements before processing or persistence."""
    previous_time, previous_stroke = -1.0, 0
    states = {"down", "move", "up", "cancel"}
    for point in sample.points:
        required = (point.x, point.y, point.timestamp, point.elapsed_time)
        optional = (point.pressure, point.tilt_x, point.tilt_y, point.orientation)
        try:
            finite = all(isfinite(v) for v in required + tuple(v for v in optional if v is not None))
            ordered = (isinstance(point.stroke_number, int) and point.stroke_number >= 1
                       and point.stroke_number >= previous_stroke and point.elapsed_time >= previous_time
                       and point.elapsed_time >= 0)
        except (TypeError, ValueError, OverflowError):
            return ["Measurements contain invalid numeric values."]
        if not finite:
            return ["Measurements contain non-finite values."]
        if not ordered:
            return ["Point timing or stroke numbers are not ordered."]
        if point.pointer_state not in states:
            return ["Unknown pointer state."]
        previous_time, previous_stroke = point.elapsed_time, point.stroke_number
    return []


def validity_errors(sample: SignatureSample, config: CaptureConfig = CAPTURE_CONFIG) -> list[str]:
    errors = measurement_errors(sample)
    if errors:
        return errors
    if sample.metadata.get("interrupted"):
        errors.append("Capture was interrupted. Clear and sign again.")
    if len(sample.points) < config.min_points:
        errors.append(f"Capture at least {config.min_points} points; draw a longer signature.")
    if len(sample.points) > config.max_points:
        errors.append("Signature exceeds the capture limit.")
    if sample.total_duration < config.min_duration:
        errors.append(f"Sign for at least {config.min_duration:.2f} seconds.")
    if not sample.points or sample.number_of_strokes < 1:
        errors.append("Draw at least one stroke.")
    elif max(max(p.x for p in sample.points) - min(p.x for p in sample.points),
             max(p.y for p in sample.points) - min(p.y for p in sample.points)) < config.min_extent:
        errors.append("Signature must contain visible motion.")
    return errors

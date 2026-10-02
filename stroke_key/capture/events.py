"""Measurements supplied by input adapters; absent sensors remain None."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PointerMeasurement:
    x: float
    y: float
    device_type: str
    pressure: float | None = None
    tilt_x: float | None = None
    tilt_y: float | None = None
    orientation: float | None = None
    stylus_buttons: int | None = None
    device_id: str | None = None

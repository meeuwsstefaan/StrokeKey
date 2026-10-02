"""Monotonic capture timing independent of GUI event types."""
from dataclasses import asdict
import time

from stroke_key.capture.events import PointerMeasurement
from stroke_key.config import CAPTURE_CONFIG
from stroke_key.models.signature import SignaturePoint, SignatureSample


class SignatureRecorder:
    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.points: list[SignaturePoint] = []
        self.active_device: str | None = None
        self.stroke_number = 0
        self._start: float | None = None
        self._epoch = 0.0
        self.interrupted = False

    def record(self, measurement: PointerMeasurement, state: str) -> bool:
        """Reject overlapping devices and bound memory usage."""
        if len(self.points) >= CAPTURE_CONFIG.max_points:
            self.cancel()
            return False
        if state == "down":
            if self.active_device is not None:
                return False
            self.active_device = measurement.device_type
            self.stroke_number += 1
            if self._start is None:
                self._start, self._epoch = time.perf_counter(), time.time()
        elif self.active_device != measurement.device_type:
            return False
        elapsed = time.perf_counter() - self._start
        self.points.append(SignaturePoint(
            **asdict(measurement), timestamp=self._epoch + elapsed,
            elapsed_time=elapsed, stroke_number=self.stroke_number, pointer_state=state))
        if state == "up":
            self.active_device = None
        return True

    def cancel(self) -> None:
        if self.active_device is not None:
            self.interrupted = True
            self.active_device = None

    def sample(self) -> SignatureSample:
        devices = {p.device_type for p in self.points}
        return SignatureSample(points=list(self.points),
                               device_type=next(iter(devices)) if len(devices) == 1 else "mixed",
                               metadata={"interrupted": self.interrupted, "timing_unit": "seconds",
                                         "coordinate_unit": "canvas_pixels"})

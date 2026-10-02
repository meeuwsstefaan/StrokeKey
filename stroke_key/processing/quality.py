"""Descriptive capture diagnostics, never an authenticity or liveness score."""
from dataclasses import dataclass
from math import hypot
from statistics import median

from stroke_key.config import CAPTURE_CONFIG
from stroke_key.models.signature import SignatureSample, validity_errors


@dataclass(frozen=True)
class PauseInterval:
    start: float
    end: float


@dataclass(frozen=True)
class CaptureQuality:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    median_interval: float | None
    effective_rate_hz: float | None
    maximum_gap: float | None
    gap_count: int
    duplicate_time_count: int
    pressure_coverage: float
    tilt_coverage: float
    pauses: tuple[PauseInterval, ...]

    @property
    def status(self) -> str:
        return "Invalid capture" if self.errors else "Review capture" if self.warnings else "No quality warnings"

    def summary(self) -> str:
        if self.errors:
            return "\n".join((self.status, *self.errors))
        rate = f"{self.effective_rate_hz:.1f} Hz" if self.effective_rate_hz is not None else "unavailable"
        gap = f"{self.maximum_gap:.3f} s" if self.maximum_gap is not None else "unavailable"
        lines = [self.status, f"Within-stroke rate: {rate} (1 / median interval); largest gap: {gap}",
                 f"Pressure coverage: {self.pressure_coverage:.0%}; tilt coverage: {self.tilt_coverage:.0%}"]
        lines.extend(self.errors)
        lines.extend(self.warnings)
        return "\n".join(lines)


def pause_intervals(sample: SignatureSample) -> tuple[PauseInterval, ...]:
    """Match the feature extractor's contiguous low-speed / pen-up pause rule."""
    intervals = []
    start = end = None
    for a, b in zip(sample.points, sample.points[1:]):
        dt = b.elapsed_time - a.elapsed_time
        same_stroke = a.stroke_number == b.stroke_number
        speed = hypot(b.x - a.x, b.y - a.y) / dt if same_stroke and dt > 0 else None
        paused = dt > 0 and (not same_stroke or speed is not None and speed <= CAPTURE_CONFIG.pause_speed)
        if paused:
            if start is None:
                start = a.elapsed_time
            end = b.elapsed_time
        else:
            if start is not None and end - start >= CAPTURE_CONFIG.pause_seconds:
                intervals.append(PauseInterval(start, end))
            start = end = None
    if start is not None and end - start >= CAPTURE_CONFIG.pause_seconds:
        intervals.append(PauseInterval(start, end))
    return tuple(intervals)


def assess_quality(sample: SignatureSample) -> CaptureQuality:
    errors = tuple(validity_errors(sample))
    # Invalid measurements must not enter numeric processing.
    if errors:
        return CaptureQuality(errors, (), None, None, None, 0, 0, 0.0, 0.0, ())
    intervals = [b.elapsed_time - a.elapsed_time for a, b in zip(sample.points, sample.points[1:])
                 if a.stroke_number == b.stroke_number]
    positive = [dt for dt in intervals if dt > 0]
    typical = median(positive) if positive else None
    rate = 1 / typical if typical else None
    maximum = max(positive) if positive else None
    gaps = sum(dt > CAPTURE_CONFIG.quality_gap_seconds for dt in positive)
    duplicates = intervals.count(0)
    count = len(sample.points)
    pressures = [p.pressure for p in sample.points if p.pressure is not None]
    pressure_coverage = len(pressures) / count
    tilt_coverage = sum(p.tilt_x is not None and p.tilt_y is not None for p in sample.points) / count
    warnings = []
    if rate is None or rate < CAPTURE_CONFIG.quality_min_rate_hz:
        warnings.append("Sparse within-stroke sampling; fast movements may be missed.")
    if gaps:
        warnings.append(f"{gaps} within-stroke gaps exceed {CAPTURE_CONFIG.quality_gap_seconds:.3f} s; these may be pauses or missed events.")
    if duplicates:
        warnings.append(f"{duplicates} equal-time segments are excluded from derivatives.")
    if 0 < pressure_coverage < 1:
        warnings.append("Pressure is partially missing; pressure comparison is unavailable.")
    if pressures and max(pressures) - min(pressures) < 1e-6:
        warnings.append("Pressure is constant; it may carry no useful dynamic information.")
    if sample.device_type == "mixed":
        warnings.append("Multiple input types were used; capture with one device for a comparable profile.")
    return CaptureQuality(errors, tuple(warnings), typical, rate, maximum, gaps, duplicates,
                          pressure_coverage, tilt_coverage, pause_intervals(sample))

"""Dynamic features exclude pen-up jumps from path length and derivatives."""
from dataclasses import dataclass
from math import hypot

import numpy as np

from stroke_key.config import CAPTURE_CONFIG
from stroke_key.models.signature import SignatureSample, measurement_errors


@dataclass(frozen=True)
class SignatureFeatures:
    total_duration: float = 0.0
    total_path_length: float = 0.0
    bounding_box_width: float = 0.0
    bounding_box_height: float = 0.0
    aspect_ratio: float = 0.0
    number_of_strokes: int = 0
    average_velocity: float = 0.0
    maximum_velocity: float = 0.0
    median_velocity: float = 0.0
    velocity_std: float = 0.0
    average_acceleration: float = 0.0
    maximum_acceleration: float = 0.0
    pause_count: int = 0
    total_pause_duration: float = 0.0
    average_stroke_duration: float = 0.0
    average_stroke_length: float = 0.0
    average_pressure: float | None = None
    pressure_std: float | None = None
    minimum_pressure: float | None = None
    maximum_pressure: float | None = None


def velocity_series(sample: SignatureSample) -> tuple[list[float], list[float]]:
    times, speeds = [], []
    if measurement_errors(sample):
        return times, speeds
    for a, b in zip(sample.points, sample.points[1:]):
        dt = b.elapsed_time - a.elapsed_time
        if a.stroke_number == b.stroke_number and dt > 0:
            times.append(b.elapsed_time)
            speeds.append(hypot(b.x - a.x, b.y - a.y) / dt)
    return times, speeds


def extract_features(sample: SignatureSample) -> SignatureFeatures:
    """Malformed samples produce finite empty features; services reject them."""
    if not sample.points or measurement_errors(sample):
        return SignatureFeatures()
    points = sample.points
    width = max(p.x for p in points) - min(p.x for p in points)
    height = max(p.y for p in points) - min(p.y for p in points)
    lengths: dict[int, float] = {p.stroke_number: 0.0 for p in points}
    stroke_times: dict[int, tuple[float, float]] = {}
    for point in points:
        start = stroke_times.get(point.stroke_number, (point.elapsed_time, point.elapsed_time))[0]
        stroke_times[point.stroke_number] = (start, point.elapsed_time)
    durations = []
    accelerations, speeds = [], []
    pause_count, pause_duration, pause_run = 0, 0.0, 0.0
    previous_speed: float | None = None
    moving_time = 0.0
    for a, b in zip(points, points[1:]):
        dt = b.elapsed_time - a.elapsed_time
        same_stroke = a.stroke_number == b.stroke_number
        distance = hypot(b.x - a.x, b.y - a.y) if same_stroke else 0.0
        lengths[b.stroke_number] += distance
        speed = distance / dt if dt > 0 and same_stroke else None
        if speed is not None:
            speeds.append(speed)
            moving_time += dt
            if previous_speed is not None:
                accelerations.append(abs(speed - previous_speed) / dt)
        previous_speed = speed
        paused = dt > 0 and (not same_stroke or speed is not None and speed <= CAPTURE_CONFIG.pause_speed)
        if paused:
            pause_run += dt
        else:
            if pause_run >= CAPTURE_CONFIG.pause_seconds:
                pause_count += 1
                pause_duration += pause_run
            pause_run = 0.0
    if pause_run >= CAPTURE_CONFIG.pause_seconds:
        pause_count += 1
        pause_duration += pause_run
    durations = [end - start for start, end in stroke_times.values()]
    pressures = [p.pressure for p in points if p.pressure is not None]
    length = sum(lengths.values())
    return SignatureFeatures(
        total_duration=sample.total_duration, total_path_length=length,
        bounding_box_width=width, bounding_box_height=height,
        aspect_ratio=width / height if height else 0.0,
        number_of_strokes=len(lengths), average_velocity=length / moving_time if moving_time else 0.0,
        maximum_velocity=max(speeds, default=0.0), median_velocity=float(np.median(speeds)) if speeds else 0.0,
        velocity_std=float(np.std(speeds)) if speeds else 0.0,
        average_acceleration=float(np.mean(accelerations)) if accelerations else 0.0,
        maximum_acceleration=max(accelerations, default=0.0), pause_count=pause_count,
        total_pause_duration=pause_duration, average_stroke_duration=float(np.mean(durations)),
        average_stroke_length=length / len(lengths),
        average_pressure=float(np.mean(pressures)) if pressures else None,
        pressure_std=float(np.std(pressures)) if pressures else None,
        minimum_pressure=min(pressures) if pressures else None,
        maximum_pressure=max(pressures) if pressures else None)

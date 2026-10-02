"""Versioned descriptive signing profiles; ranges are observed, not calibrated."""
from dataclasses import dataclass
from statistics import mean, median, pstdev

from stroke_key.models.signature import SignatureSample, validity_errors
from stroke_key.processing.compatibility import device_ids, matches_group
from stroke_key.processing.features import extract_features
from stroke_key.processing.normalize import normalize_signature
from stroke_key.processing.quality import assess_quality

PROFILE_VERSION = 2

# label, unit; spatial dynamics use aspect-preserving unit-box coordinates.
FEATURE_SPECS = {
    "total_duration": ("Duration", "s"),
    "number_of_strokes": ("Strokes", "count"),
    "total_path_length": ("Normalized path length", "units"),
    "bounding_box_width": ("Normalized width", "units"),
    "bounding_box_height": ("Normalized height", "units"),
    "average_velocity": ("Mean normalized speed", "units/s"),
    "velocity_std": ("Normalized speed variation", "units/s"),
    "average_acceleration": ("Mean normalized acceleration", "units/s²"),
    "pause_count": ("Pauses", "count"),
    "total_pause_duration": ("Pause duration", "s"),
    "average_pressure": ("Mean pressure", "Qt units"),
    "pressure_std": ("Pressure variation", "Qt units"),
}


def profile_features(sample: SignatureSample) -> dict[str, float | None]:
    raw = extract_features(sample)
    normalized = extract_features(normalize_signature(sample))
    quality = assess_quality(sample)
    raw_names = {"total_duration", "number_of_strokes", "pause_count", "total_pause_duration",
                 "average_pressure", "pressure_std"}
    result = {}
    for name in FEATURE_SPECS:
        value = getattr(raw if name in raw_names else normalized, name)
        result[name] = float(value) if value is not None else None
    # Missing, constant or device-unidentified pressure is still plotted, but not compared.
    if (quality.pressure_coverage < 1 or not device_ids(sample)
            or raw.pressure_std is None or raw.pressure_std < 1e-6):
        result["average_pressure"] = result["pressure_std"] = None
    return result


@dataclass(frozen=True)
class FeatureRange:
    count: int
    mean: float
    median: float
    minimum: float
    maximum: float
    standard_deviation: float


@dataclass(frozen=True)
class SigningProfile:
    version: int
    device_type: str
    device_ids: tuple[str, ...]
    reference_ids: tuple[str, ...]
    session_count: int
    unknown_session_count: int
    skipped_count: int
    ranges: dict[str, FeatureRange]

    @property
    def sample_count(self) -> int:
        return len(self.reference_ids)


def summarize_profile(references: list[SignatureSample], device_type: str,
                      target_device_ids: tuple[str, ...] = (), *,
                      exclude_sample_id: str | None = None, user_id: str | None = None) -> SigningProfile:
    eligible = []
    seen = set()
    for sample in references:
        if (sample.sample_id == exclude_sample_id or sample.sample_id in seen
                or not matches_group(sample, device_type, target_device_ids)
                or user_id is not None and sample.user_id != user_id
                or validity_errors(sample)):
            continue
        eligible.append(sample)
        seen.add(sample.sample_id)
    features = [profile_features(sample) for sample in eligible]
    # Require explicit matching device IDs for sensor comparisons.
    for values, sample in zip(features, eligible):
        if not target_device_ids or device_ids(sample) != target_device_ids:
            values["average_pressure"] = values["pressure_std"] = None
    ranges = {}
    for name in FEATURE_SPECS:
        values = [f[name] for f in features if f[name] is not None]
        if values:
            ranges[name] = FeatureRange(len(values), mean(values), median(values), min(values), max(values), pstdev(values))
    sessions = {s.metadata.get("session_id") for s in eligible
                if isinstance(s.metadata.get("session_id"), str) and s.metadata["session_id"]}
    unknown = sum(not isinstance(s.metadata.get("session_id"), str) or not s.metadata["session_id"] for s in eligible)
    return SigningProfile(PROFILE_VERSION, device_type, target_device_ids,
                          tuple(s.sample_id for s in eligible), len(sessions), unknown,
                          len(references) - len(eligible), ranges)


def build_profile(candidate: SignatureSample, references: list[SignatureSample]) -> SigningProfile:
    return summarize_profile(references, candidate.device_type, device_ids(candidate),
                             exclude_sample_id=candidate.sample_id, user_id=candidate.user_id)

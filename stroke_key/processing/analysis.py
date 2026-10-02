"""Measured sample differences, independent of GUI, persistence or trained models."""
from dataclasses import dataclass

from stroke_key.models.signature import SignatureSample
from stroke_key.processing.profiles import FEATURE_SPECS, SigningProfile, build_profile, profile_features
from stroke_key.processing.quality import CaptureQuality, assess_quality


@dataclass(frozen=True)
class FeatureComparison:
    name: str
    label: str
    unit: str
    value: float | None
    reference_median: float | None
    minimum: float | None
    maximum: float | None
    reference_count: int
    status: str


@dataclass(frozen=True)
class SampleAnalysis:
    quality: CaptureQuality
    profile: SigningProfile
    comparisons: tuple[FeatureComparison, ...]
    explanations: tuple[str, ...]


def analyze_sample(candidate: SignatureSample, references: list[SignatureSample]) -> SampleAnalysis:
    quality = assess_quality(candidate)
    profile = build_profile(candidate, references)
    explanations = ["Descriptive analysis only; no trained model or authenticity probability."]
    if quality.errors:
        explanations.append("Resolve the capture errors before comparing this sample.")
        return SampleAnalysis(quality, profile, (), tuple(explanations))
    if profile.sample_count < 2:
        explanations.append("At least two comparable enrollment references are needed for range comparisons.")
    else:
        explanations.append(f"Compared with {profile.sample_count} references; the selected sample is excluded.")
        explanations.append("Observed enrollment ranges are preliminary; falling outside a range does not establish imitation.")
    if profile.session_count < 2:
        explanations.append("References cover fewer than two known sessions; between-session variation is not established.")
    if profile.unknown_session_count:
        explanations.append(f"{profile.unknown_session_count} references have no session metadata (legacy records remain usable).")
    if profile.skipped_count:
        explanations.append(f"{profile.skipped_count} references excluded: selected sample, duplicates, invalid data, another user or incompatible input.")
    values = profile_features(candidate)
    comparisons = []
    for name, (label, unit) in FEATURE_SPECS.items():
        value, span = values[name], profile.ranges.get(name)
        usable = value is not None and span is not None and span.count >= 2
        status = "Unavailable" if value is None else "Insufficient references" if not usable else "Within observed range"
        if usable:
            tolerance = max(1e-9, abs(span.maximum) * 1e-9)
            if value < span.minimum - tolerance:
                status = "Below observed range"
            elif value > span.maximum + tolerance:
                status = "Above observed range"
            if status != "Within observed range":
                explanations.append(f"{label}: {value:.3g} {unit}, versus an observed range of "
                                    f"{span.minimum:.3g}–{span.maximum:.3g} {unit}.")
        comparisons.append(FeatureComparison(name, label, unit, value, span.median if span else None,
                                            span.minimum if span else None, span.maximum if span else None,
                                            span.count if span else 0, status))
    explanations.append("Pressure comparisons require a complete, varying signal and matching reported device IDs; missing sensors are never inferred.")
    explanations.append("Pause detection uses the existing raw-pixel speed rule and needs device calibration.")
    return SampleAnalysis(quality, profile, tuple(comparisons), tuple(explanations))

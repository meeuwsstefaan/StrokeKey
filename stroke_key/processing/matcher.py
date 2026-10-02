"""Experimental similarity; no claim of authentication security."""
from dataclasses import dataclass, field
from math import exp
from statistics import median
from typing import Any

import numpy as np

from stroke_key.config import MATCH_CONFIG, MatchConfig
from stroke_key.models.signature import SignatureSample, validity_errors
from stroke_key.processing.dtw import dtw_distance
from stroke_key.processing.features import extract_features
from stroke_key.processing.normalize import normalize_signature


@dataclass(frozen=True)
class MatchResult:
    overall_score: float
    accepted: bool
    dtw_score: float
    duration_score: float
    geometry_score: float
    stroke_score: float
    diagnostics: dict[str, Any] = field(default_factory=dict)


def ratio_similarity(a: float, b: float) -> float:
    return min(a, b) / max(a, b) if max(a, b) > 0 else 1.0


def trajectory(sample: SignatureSample, limit: int) -> np.ndarray:
    """Bound DTW work by deterministic index subsampling, without changing raw data."""
    points = normalize_signature(sample).points
    indices = np.linspace(0, len(points) - 1, min(len(points), limit), dtype=int)
    return np.array([(points[i].x, points[i].y) for i in indices])


def compare_signatures(candidate: SignatureSample, reference: SignatureSample,
                       config: MatchConfig = MATCH_CONFIG) -> MatchResult:
    errors = validity_errors(candidate) + validity_errors(reference)
    if errors:
        return MatchResult(0, False, 0, 0, 0, 0, {"errors": errors})
    a, b = extract_features(candidate), extract_features(reference)
    na, nb = extract_features(normalize_signature(candidate)), extract_features(normalize_signature(reference))
    distance = dtw_distance(trajectory(candidate, config.trajectory_limit), trajectory(reference, config.trajectory_limit))
    dtw = exp(-distance / config.dtw_scale)
    duration = ratio_similarity(a.total_duration, b.total_duration)
    # Normalized extents avoid singular aspect ratios for horizontal/vertical strokes.
    geometry = float(np.mean([ratio_similarity(na.total_path_length, nb.total_path_length),
                              ratio_similarity(na.bounding_box_width, nb.bounding_box_width),
                              ratio_similarity(na.bounding_box_height, nb.bounding_box_height)]))
    strokes = ratio_similarity(a.number_of_strokes, b.number_of_strokes)
    score = dtw * config.dtw_weight + duration * config.duration_weight + geometry * config.geometry_weight + strokes * config.stroke_weight
    score = min(1.0, max(0.0, score))
    return MatchResult(score, score >= config.threshold, dtw, duration, geometry, strokes,
                       {"dtw_distance": distance, "candidate_duration": a.total_duration,
                        "reference_duration": b.total_duration, "candidate_strokes": a.number_of_strokes,
                        "reference_strokes": b.number_of_strokes})


def aggregate_matches(results: list[MatchResult], config: MatchConfig = MATCH_CONFIG) -> MatchResult:
    if not results:
        return MatchResult(0, False, 0, 0, 0, 0, {"errors": ["No valid enrollment references."]})
    scores = [median(getattr(r, name) for r in results) for name in
              ("overall_score", "dtw_score", "duration_score", "geometry_score", "stroke_score")]
    return MatchResult(scores[0], scores[0] >= config.threshold, *scores[1:],
                       {"aggregation": "median", "reference_count": len(results)})

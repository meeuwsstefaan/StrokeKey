"""Empirical evaluation of frozen trial scores; no authentication calibration."""
from bisect import bisect_left
from dataclasses import asdict, dataclass, field
import json
from math import isfinite
from statistics import mean, median
from typing import Any

EVALUATION_VERSION = "saved-scores-v1"
IMPOSTOR_TYPES = {"other_participant", "imitation"}


@dataclass(frozen=True)
class EvaluationCohort:
    matcher_version: str
    matcher_config: dict[str, Any]
    device_type: str
    device_ids: tuple[str, ...]

    @property
    def key(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, allow_nan=False)


@dataclass(frozen=True)
class TrialObservation:
    trial_id: str
    candidate_sample_id: str
    claimed_user_id: str
    actual_signer_id: str | None
    attempt_type: str
    session_id: str | None
    reference_set_id: str
    reference_revision: int
    cohort: EvaluationCohort
    score: float
    saved_accepted: bool
    synthetic: bool
    reference_sessions: tuple[str, ...]
    unknown_reference_session: bool


@dataclass(frozen=True)
class EvaluationSelection:
    cohort: EvaluationCohort
    threshold: float
    claimed_user_id: str | None = None
    session_id: str | None = None
    impostor_type: str = "all"
    include_synthetic: bool = False
    strict_sessions: bool = True

    def __post_init__(self) -> None:
        if (isinstance(self.threshold, bool) or not isinstance(self.threshold, (int, float))
                or not isfinite(self.threshold) or not 0 <= self.threshold <= 1):
            raise ValueError("Evaluation threshold must be a finite number between 0 and 1.")
        if self.impostor_type not in {"all", *IMPOSTOR_TYPES}:
            raise ValueError("Choose a supported impostor group.")
        if not isinstance(self.include_synthetic, bool) or not isinstance(self.strict_sessions, bool):
            raise ValueError("Evaluation policies must be explicit booleans.")


@dataclass(frozen=True)
class EvaluationReport:
    selection: EvaluationSelection
    trial_ids: tuple[str, ...]
    results: dict[str, Any] = field(default_factory=dict)


def operating_point(genuine: list[float], impostor: list[float], threshold: float | None) -> dict:
    """Sorted scores; >= accepts ties together. None is the reject-all endpoint."""
    false_rejections = bisect_left(genuine, threshold) if threshold is not None else len(genuine)
    false_acceptances = len(impostor) - bisect_left(impostor, threshold) if threshold is not None else 0
    return {"threshold": threshold, "accept_none": threshold is None,
            "genuine_count": len(genuine), "impostor_count": len(impostor),
            "false_rejections": false_rejections, "false_acceptances": false_acceptances,
            "true_acceptances": len(genuine) - false_rejections,
            "true_rejections": len(impostor) - false_acceptances,
            "frr": false_rejections / len(genuine) if genuine else None,
            "far": false_acceptances / len(impostor) if impostor else None}


def approximate_eer(curve: list[dict]) -> dict | None:
    """Linear crossing between empirical points; no deployable threshold is inferred."""
    if not curve or curve[0]["far"] is None or curve[0]["frr"] is None:
        return None
    for index, point in enumerate(curve):
        difference = point["far"] - point["frr"]
        if abs(difference) <= 1e-12:
            return {"rate": (point["far"] + point["frr"]) / 2, "method": "observed_equal_rates",
                    "lower_threshold": point["threshold"], "upper_threshold": point["threshold"]}
        if difference < 0 and index:
            previous = curve[index - 1]
            prior_difference = previous["far"] - previous["frr"]
            fraction = prior_difference / (prior_difference - difference)
            return {"rate": previous["far"] + fraction * (point["far"] - previous["far"]),
                    "method": "linear_interpolation", "lower_threshold": previous["threshold"],
                    "upper_threshold": point["threshold"]}
    return None


def score_summary(scores: list[float]) -> dict:
    return {"count": len(scores), "minimum": min(scores) if scores else None,
            "maximum": max(scores) if scores else None, "mean": mean(scores) if scores else None,
            "median": median(scores) if scores else None}


def evaluate_trials(observations: tuple[TrialObservation, ...], selection: EvaluationSelection,
                    unreadable_trial_ids: tuple[str, ...] = ()) -> EvaluationReport:
    """Pure evaluation of a detached catalogue; every exclusion has a recorded reason."""
    ids = [row.trial_id for row in observations] + list(unreadable_trial_ids)
    if len(set(ids)) != len(ids):
        raise ValueError("Evaluation input cannot contain duplicate trial IDs.")
    included, excluded = [], [{"trial_id": trial_id, "reason": "unreadable"} for trial_id in unreadable_trial_ids]
    for row in observations:
        reason = None
        if row.cohort.key != selection.cohort.key:
            reason = "different_device_or_matcher"
        elif selection.claimed_user_id is not None and row.claimed_user_id != selection.claimed_user_id:
            reason = "claimed_participant_filter"
        elif selection.session_id is not None and row.session_id != selection.session_id:
            reason = "trial_session_filter"
        elif row.attempt_type not in {"genuine", *IMPOSTOR_TYPES}:
            reason = "unlabelled"
        elif row.attempt_type in IMPOSTOR_TYPES and selection.impostor_type not in {"all", row.attempt_type}:
            reason = "impostor_type_filter"
        elif row.synthetic and not selection.include_synthetic:
            reason = "synthetic_candidate_or_reference"
        elif selection.strict_sessions and (not row.session_id or row.unknown_reference_session or not row.reference_sessions):
            reason = "unknown_session_separation"
        elif selection.strict_sessions and row.session_id in row.reference_sessions:
            reason = "reference_session_overlap"
        if reason:
            excluded.append({"trial_id": row.trial_id, "reason": reason})
            continue
        if isinstance(row.score, bool) or not isinstance(row.score, (int, float)) or not isfinite(row.score) or not 0 <= row.score <= 1:
            raise ValueError("Evaluation requires finite saved similarity scores in [0, 1].")
        included.append(row)

    genuine = sorted(row.score for row in included if row.attempt_type == "genuine")
    impostor = sorted(row.score for row in included if row.attempt_type in IMPOSTOR_TYPES)
    curve = [operating_point(genuine, impostor, threshold) for threshold in sorted({0.0, *genuine, *impostor})]
    curve.append(operating_point(genuine, impostor, None))
    warnings = ["Empirical trial rates use self-reported labels; they do not establish identity assurance or spoof resistance.",
                "Session UUID separation does not prove between-day collection or independent observations.",
                "Repeated trials share participants/references; pooled rates are trial-weighted, not population estimates.",
                "Threshold exploration and interpolated EER on this selection are descriptive, not held-out calibration."]
    if not genuine or not impostor:
        warnings.append("Both genuine and impostor trials are required for ROC and EER; missing-class rates are unavailable.")
    if any(row.synthetic for row in included):
        warnings.append("Synthetic/demonstration candidates or enrollment references are included. These rates are demonstration-only.")
    if not selection.strict_sessions:
        warnings.append("Session separation is disabled; unknown or overlapping collection sessions may be included.")
    if len({row.claimed_user_id for row in included}) < 2:
        warnings.append("Fewer than two claimed participants are represented; this selection cannot establish multi-user performance.")
    summaries = {}
    for attempt in ("genuine", "other_participant", "imitation"):
        scores = [row.score for row in included if row.attempt_type == attempt]
        summaries[attempt] = {**score_summary(scores), "scores": scores,
                              "accepted": sum(score >= selection.threshold for score in scores)}
    participants = []
    for user_id in sorted({row.claimed_user_id for row in included}):
        group = [row for row in included if row.claimed_user_id == user_id]
        participants.append({"claimed_user_id": user_id,
                             "session_count": len({row.session_id for row in group if row.session_id}),
                             **operating_point(sorted(row.score for row in group if row.attempt_type == "genuine"),
                                               sorted(row.score for row in group if row.attempt_type in IMPOSTOR_TYPES), selection.threshold)})
    reasons = {}
    for row in excluded:
        reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    audit = [{**asdict(row), "evaluation_accepted": row.score >= selection.threshold} for row in included]
    results = {"evaluation_version": EVALUATION_VERSION, "available_count": len(ids),
               "included_count": len(included), "excluded_count": len(excluded),
               "operating_point": operating_point(genuine, impostor, selection.threshold),
               "distributions": summaries, "threshold_curve": curve, "approximate_eer": approximate_eer(curve),
               "participants": participants, "session_count": len({row.session_id for row in included if row.session_id}),
               "reference_set_count": len({row.reference_set_id for row in included}),
               "excluded_by_reason": reasons, "excluded_trials": excluded,
               "included_trials": audit, "warnings": warnings}
    # Use the same value types in the reviewed and reconstructed JSON snapshots.
    results = json.loads(json.dumps(results, allow_nan=False))
    return EvaluationReport(selection, tuple(row.trial_id for row in included), results)

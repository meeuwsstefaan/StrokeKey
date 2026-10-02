"""Central research parameters; thresholds require future FAR/FRR calibration."""
from dataclasses import dataclass


@dataclass(frozen=True)
class CaptureConfig:
    min_points: int = 20
    min_duration: float = 0.250
    min_extent: float = 2.0
    max_points: int = 50_000
    pause_seconds: float = 0.150
    pause_speed: float = 5.0  # raw canvas pixels / second
    enrollment_samples: int = 5


@dataclass(frozen=True)
class MatchConfig:
    threshold: float = 0.75
    dtw_scale: float = 0.20
    trajectory_limit: int = 256
    dtw_weight: float = 0.55
    duration_weight: float = 0.15
    geometry_weight: float = 0.20
    stroke_weight: float = 0.10

    def __post_init__(self) -> None:
        weights = (self.dtw_weight, self.duration_weight,
                   self.geometry_weight, self.stroke_weight)
        if (not 0 <= self.threshold <= 1 or self.dtw_scale <= 0
                or self.trajectory_limit < 2 or min(weights) < 0
                or abs(sum(weights) - 1) > 1e-9):
            raise ValueError("Invalid matching configuration")


CAPTURE_CONFIG = CaptureConfig()
MATCH_CONFIG = MatchConfig()
RESEARCH_NOTICE = "Research prototype - not for production authentication."

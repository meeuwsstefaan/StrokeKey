"""Leave-one-out enrollment feedback, never an authenticity decision."""
from dataclasses import dataclass

from stroke_key.models.signature import SignatureSample
from stroke_key.processing.analysis import SampleAnalysis, analyze_sample


@dataclass(frozen=True)
class EnrollmentGuidance:
    analysis: SampleAnalysis

    @property
    def differences(self):
        return tuple(c for c in self.analysis.comparisons
                     if c.status in {"Above observed range", "Below observed range"})

    @property
    def needs_review(self) -> bool:
        return bool(self.analysis.quality.errors or self.analysis.quality.warnings or self.differences)

    def summary(self) -> str:
        report = self.analysis
        lines = ["Sign naturally. Differences can reflect normal variation; do not try to copy a fixed pattern.",
                 "Quality warnings and measured differences are advisory. Keep a valid capture or retry it explicitly.",
                 "", report.quality.summary(), "",
                 f"Compared with {report.profile.sample_count} compatible references; this sample is excluded."]
        if report.quality.errors:
            lines.append("Resolve capture errors before comparing or saving this sample.")
        elif report.profile.sample_count < 2:
            lines.append("Too few other references for useful consistency guidance. Capture more natural samples.")
        elif self.differences:
            lines.append("Measured differences worth reviewing:")
            for feature in self.differences:
                lines.append(f"• {feature.label}: {feature.value:.3g} {feature.unit}; "
                             f"other references {feature.minimum:.3g}–{feature.maximum:.3g} {feature.unit}.")
        else:
            lines.append("Available features fall within the other references' observed ranges.")
        lines.extend(["", f"Other references: {report.profile.session_count} known collection sessions; "
                      f"{report.profile.unknown_session_count} samples with unknown sessions."])
        if report.profile.session_count < 2:
            lines.append("Collect later sessions to study between-session variation; a new session ID alone does not establish it.")
        lines.extend(["", "Pressure requires a complete varying signal and matching reported device IDs.",
                      "Pause detection uses a raw-pixel heuristic. No trained model or calibrated confidence is used."])
        return "\n".join(lines)


def assess_enrollment(candidate: SignatureSample, references: list[SignatureSample]) -> EnrollmentGuidance:
    return EnrollmentGuidance(analyze_sample(candidate, references))

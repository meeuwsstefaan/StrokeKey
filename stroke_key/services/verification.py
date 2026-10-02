"""Compare a candidate to every enrolled reference, then aggregate medians."""
from dataclasses import dataclass

from stroke_key.models.signature import SignatureSample, validity_errors
from stroke_key.processing.matcher import MatchResult, aggregate_matches, compare_signatures
from stroke_key.storage.repositories import SampleRepository


@dataclass(frozen=True)
class VerificationReport:
    result: MatchResult
    comparisons: list[tuple[str, MatchResult]]


def compare_references(candidate: SignatureSample, references: list[SignatureSample]) -> VerificationReport:
    """Pure comparison, safe to run in a worker without SQLite access."""
    comparisons = [(r.sample_id, compare_signatures(candidate, r)) for r in references]
    return VerificationReport(aggregate_matches([result for _, result in comparisons]), comparisons)


class VerificationService:
    def __init__(self, samples: SampleRepository) -> None:
        self.samples = samples

    def prepare(self, user_id: str, candidate: SignatureSample) -> list[SignatureSample]:
        errors = validity_errors(candidate)
        if errors:
            raise ValueError("\n".join(errors))
        references = self.samples.for_user(user_id)
        if len(references) < 5 or any(validity_errors(reference) for reference in references):
            raise ValueError("This user does not have a complete valid enrollment.")
        return references

    def verify(self, user_id: str, candidate: SignatureSample) -> VerificationReport:
        return compare_references(candidate, self.prepare(user_id, candidate))

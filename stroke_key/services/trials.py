"""Explicit, local trial collection using frozen references and matcher settings."""
from copy import deepcopy
from dataclasses import asdict, dataclass

from stroke_key.config import CAPTURE_CONFIG, MATCH_CONFIG, MatchConfig
from stroke_key.models.research import ReferenceSet, ResearchTrial
from stroke_key.models.signature import SignatureSample, validity_errors
from stroke_key.processing.compatibility import matches_group
from stroke_key.processing.matcher import MATCHER_VERSION
from stroke_key.services.verification import compare_references
from stroke_key.storage.database import Database
from stroke_key.storage.repositories import UserRepository
from stroke_key.storage.research_repositories import ReferenceSetRepository, ResearchTrialRepository, require_text


@dataclass(frozen=True)
class TrialDeclaration:
    claimed_user_id: str
    attempt_type: str
    consent_confirmed: bool
    actual_signer_id: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class PreparedTrial:
    candidate: SignatureSample
    declaration: TrialDeclaration
    reference_set: ReferenceSet
    references: tuple[SignatureSample, ...]
    config: MatchConfig
    matcher_version: str


def compare_trial(prepared: PreparedTrial) -> ResearchTrial:
    """Pure worker operation; nothing is written until explicitly saved."""
    report = compare_references(prepared.candidate, list(prepared.references), prepared.config)
    declaration = prepared.declaration
    return ResearchTrial(
        candidate_sample_id=prepared.candidate.sample_id,
        claimed_user_id=declaration.claimed_user_id,
        reference_set_id=prepared.reference_set.reference_set_id,
        attempt_type=declaration.attempt_type,
        session_id=prepared.candidate.metadata["session_id"],
        matcher_version=prepared.matcher_version, matcher_config=asdict(prepared.config),
        result=asdict(report.result),
        comparisons={sample_id: asdict(result) for sample_id, result in report.comparisons},
        consent_confirmed=declaration.consent_confirmed,
        actual_signer_id=declaration.actual_signer_id, notes=declaration.notes)


class TrialCollectionService:
    def __init__(self, database: Database, config: MatchConfig = MATCH_CONFIG) -> None:
        self.users = UserRepository(database)
        self.references = ReferenceSetRepository(database)
        self.trials = ResearchTrialRepository(database)
        self.config = config

    def prepare(self, candidate: SignatureSample, declaration: TrialDeclaration) -> PreparedTrial:
        """Read/validate on the owning thread, detach the complete comparison input."""
        if declaration.consent_confirmed is not True:
            raise ValueError("Confirm informed consent for this research capture before comparing.")
        if declaration.attempt_type not in {"genuine", "other_participant", "imitation", "unlabelled"}:
            raise ValueError("Choose a supported declared attempt type.")
        self.users.get(declaration.claimed_user_id)
        if declaration.actual_signer_id is not None:
            self.users.get(declaration.actual_signer_id)
        if declaration.attempt_type == "genuine" and declaration.actual_signer_id not in (None, declaration.claimed_user_id):
            raise ValueError("A genuine attempt must agree with the reported signer.")
        if declaration.attempt_type in {"other_participant", "imitation"} and declaration.actual_signer_id == declaration.claimed_user_id:
            raise ValueError("An other-participant or imitation attempt requires a different reported signer.")
        if declaration.attempt_type == "other_participant" and declaration.actual_signer_id is None:
            raise ValueError("Select the reported signer for an other-participant attempt.")
        if not isinstance(declaration.notes, str):
            raise ValueError("Trial notes must be text.")
        require_text(candidate.metadata.get("session_id"), "Collection session")
        if candidate.user_id is not None:
            raise ValueError("Trial candidates must be separate, unassigned captures.")
        if self.trials.samples.exists(candidate.sample_id):
            raise ValueError("Capture a fresh trial candidate; existing samples cannot be reused.")
        errors = validity_errors(candidate)
        if errors:
            raise ValueError("\n".join(errors))
        matching = [reference for reference in self.references.latest_for_user(declaration.claimed_user_id)
                    if matches_group(candidate, reference.device_type, reference.device_ids)]
        if len(matching) != 1:
            raise ValueError("No compatible published enrollment for this input type and reported device.")
        reference = matching[0]
        references = self.references.samples_for(reference.reference_set_id)
        if len(references) < CAPTURE_CONFIG.enrollment_samples or any(validity_errors(s) for s in references):
            raise ValueError("Trial collection requires a complete valid compatible enrollment.")
        # Trial declaration is authoritative; ordinary capture labels cannot contradict it.
        captured = deepcopy(candidate)
        captured.metadata.update(research_label=declaration.attempt_type, purpose="research_trial")
        return PreparedTrial(captured, declaration, reference, tuple(deepcopy(references)), self.config, MATCHER_VERSION)

    def save(self, prepared: PreparedTrial, trial: ResearchTrial) -> ResearchTrial:
        """Persist the reviewed snapshot atomically, without selecting newer references."""
        declaration = prepared.declaration
        if (trial.candidate_sample_id != prepared.candidate.sample_id
                or trial.reference_set_id != prepared.reference_set.reference_set_id
                or trial.claimed_user_id != declaration.claimed_user_id
                or trial.actual_signer_id != declaration.actual_signer_id
                or trial.attempt_type != declaration.attempt_type
                or trial.consent_confirmed != declaration.consent_confirmed
                or trial.notes != declaration.notes
                or trial.matcher_version != prepared.matcher_version
                or trial.matcher_config != asdict(prepared.config)):
            raise ValueError("Trial snapshot does not match the prepared collection.")
        return self.trials.save(prepared.candidate, trial)

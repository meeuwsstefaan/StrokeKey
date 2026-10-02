"""Load immutable trial evidence on the owning thread and save explicit evaluations."""
from copy import deepcopy
from dataclasses import asdict, dataclass

from stroke_key.models.research import EvaluationRun
from stroke_key.models.signature import SignatureSample
from stroke_key.processing.evaluation import (EVALUATION_VERSION, EvaluationCohort, EvaluationReport,
                                               EvaluationSelection, TrialObservation)
from stroke_key.storage.database import Database
from stroke_key.storage.research_repositories import EvaluationRunRepository, ResearchTrialRepository, require_text


def synthetic_sample(sample: SignatureSample) -> bool:
    return (sample.metadata.get("synthetic") is True or sample.metadata.get("research_label") == "synthetic"
            or sample.device_type == "synthetic")


def session_id(sample: SignatureSample) -> str | None:
    value = sample.metadata.get("session_id")
    return value if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class EvaluationCatalogue:
    observations: tuple[TrialObservation, ...]
    unreadable_trial_ids: tuple[str, ...]

    @property
    def cohorts(self) -> list[EvaluationCohort]:
        unique = {row.cohort.key: row.cohort for row in self.observations}
        return [unique[key] for key in sorted(unique)]


@dataclass(frozen=True)
class PreparedEvaluation:
    catalogue: EvaluationCatalogue
    selection: EvaluationSelection


class EvaluationService:
    def __init__(self, database: Database) -> None:
        self.trials = ResearchTrialRepository(database)
        self.runs = EvaluationRunRepository(database)

    def catalogue(self) -> EvaluationCatalogue:
        observations, unreadable, references = [], [], {}
        for row in self.trials.list_summaries():
            trial_id = row["trial_id"]
            try:
                trial = self.trials.get(trial_id)
                candidate = self.trials.samples.get(trial.candidate_sample_id)
                if trial.reference_set_id not in references:
                    reference = self.trials.references.get(trial.reference_set_id)
                    samples = self.trials.references.samples_for(reference.reference_set_id)
                    sessions = tuple(sorted({session_id(sample) for sample in samples if session_id(sample)}))
                    references[trial.reference_set_id] = (reference, sessions, any(session_id(sample) is None for sample in samples),
                                                          any(synthetic_sample(sample) for sample in samples))
                reference, sessions, unknown, synthetic_reference = references[trial.reference_set_id]
                cohort = EvaluationCohort(trial.matcher_version, deepcopy(trial.matcher_config), reference.device_type, reference.device_ids)
                observations.append(TrialObservation(trial.trial_id, trial.candidate_sample_id, trial.claimed_user_id,
                                                     trial.actual_signer_id, trial.attempt_type, trial.session_id,
                                                     reference.reference_set_id, reference.revision, cohort,
                                                     trial.result["overall_score"], trial.result["accepted"],
                                                     synthetic_sample(candidate) or synthetic_reference, sessions, unknown))
            except (ValueError, TypeError, KeyError, OverflowError):
                unreadable.append(trial_id)
        return EvaluationCatalogue(tuple(observations), tuple(unreadable))

    def prepare(self, selection: EvaluationSelection) -> PreparedEvaluation:
        catalogue = self.catalogue()
        if selection.cohort.key not in {cohort.key for cohort in catalogue.cohorts}:
            raise ValueError("No readable saved trials match the selected device and matcher configuration.")
        return PreparedEvaluation(deepcopy(catalogue), deepcopy(selection))

    def save(self, report: EvaluationReport, name: str) -> EvaluationRun:
        require_text(name, "Evaluation name")
        if not report.trial_ids:
            raise ValueError("There are no eligible trials to save. Review the selection and exclusion reasons.")
        if report.results.get("evaluation_version") != EVALUATION_VERSION:
            raise ValueError("Unsupported evaluation report version.")
        selection = asdict(report.selection)
        selection.update(evaluation_version=EVALUATION_VERSION, score_source="immutable_saved_trial_scores")
        config = {**report.selection.cohort.matcher_config, "threshold": report.selection.threshold}
        run = EvaluationRun(name.strip(), report.trial_ids, selection,
                            report.selection.cohort.matcher_version, config, deepcopy(report.results))
        return self.runs.save(run)

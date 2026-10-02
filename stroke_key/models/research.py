"""Identifiers and snapshots for reproducible local research records."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ReferenceSet:
    reference_set_id: str
    user_id: str
    revision: int
    device_type: str
    device_ids: tuple[str, ...]
    sample_ids: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class ResearchTrial:
    candidate_sample_id: str
    claimed_user_id: str
    reference_set_id: str
    attempt_type: str
    session_id: str | None
    matcher_version: str
    matcher_config: dict[str, Any]
    result: dict[str, Any]
    comparisons: dict[str, dict[str, Any]]
    consent_confirmed: bool
    actual_signer_id: str | None = None
    notes: str = ""
    trial_id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=utc_now)


@dataclass(frozen=True)
class EvaluationRun:
    name: str
    trial_ids: tuple[str, ...]
    selection: dict[str, Any]
    matcher_version: str
    matcher_config: dict[str, Any]
    results: dict[str, Any]
    run_id: str = field(default_factory=new_id)
    created_at: str = field(default_factory=utc_now)

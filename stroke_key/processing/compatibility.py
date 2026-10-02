"""Shared, conservative grouping of captures by input type and reported devices."""
from stroke_key.models.signature import SignatureSample


def device_ids(sample: SignatureSample) -> tuple[str, ...]:
    return tuple(sorted({p.device_id for p in sample.points if p.device_id is not None}))


def input_group(sample: SignatureSample) -> tuple[str, tuple[str, ...]]:
    return sample.device_type, device_ids(sample)


def matches_group(sample: SignatureSample, device_type: str, ids: tuple[str, ...]) -> bool:
    # Unknown device IDs only match other unknown IDs; no sensor equivalence is inferred.
    return sample.device_type != "mixed" and input_group(sample) == (device_type, ids)


def compatible_inputs(a: SignatureSample, b: SignatureSample) -> bool:
    return matches_group(a, *input_group(b))

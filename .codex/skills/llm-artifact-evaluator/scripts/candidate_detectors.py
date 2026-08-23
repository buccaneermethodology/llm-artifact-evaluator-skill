"""Portable candidate-only risk detectors.

Detectors in this module are deliberately advisory: they inspect a candidate
and its declared context, but never turn a warning into approval.  Callers can
select checks through ``contract["candidate_risk_checks"]``.  The evaluator's
legacy names (``unrecorded_state_write`` and
``missing_evidence_reference``) remain supported.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable


Detector = Callable[[dict[str, Any], dict[str, Any]], list[dict[str, Any]]]


def _registry_entries(registry: Any) -> dict[str, Any]:
    if not isinstance(registry, dict):
        return {}
    entries = registry.get("sources", registry)
    return entries if isinstance(entries, dict) else {}


def _risk(code: str, message: str, **details: Any) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "code": code,
        "status": "warning",
        "message": message,
    }
    if details:
        finding["details"] = details
    return finding


def _units(candidate: dict[str, Any]) -> Iterable[dict[str, Any]]:
    return (unit for unit in candidate.get("units", []) if isinstance(unit, dict))


def unrecorded_state_write(candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for unit in _units(candidate):
        writes = set(unit.get("writes", []))
        recorded = set(unit.get("state_change_records", []))
        unrecorded = sorted(writes - recorded)
        if unrecorded:
            risks.append(
                _risk(
                    "EVAL-RISK-UNPAIRED-SIDE-EFFECT",
                    "Candidate writes state without a matching state-change record.",
                    unit_id=unit.get("id"),
                    writes=unrecorded,
                )
            )
    return risks


def phantom_evidence_reference(candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    declared = {item for item in candidate.get("source_ids", []) if isinstance(item, str)}
    referenced = {
        evidence_id
        for unit in _units(candidate)
        for evidence_id in unit.get("evidence_ids", [])
        if isinstance(evidence_id, str)
    }
    registry = _registry_entries(context.get("source_registry"))
    resolvable = set(registry)
    risks: list[dict[str, Any]] = []
    for unit in _units(candidate):
        missing = sorted(
            evidence_id
            for evidence_id in unit.get("evidence_ids", [])
            if isinstance(evidence_id, str)
            and evidence_id not in declared
        )
        unresolved = sorted(
            evidence_id
            for evidence_id in unit.get("evidence_ids", [])
            if isinstance(evidence_id, str)
            and resolvable
            and evidence_id in declared
            and evidence_id not in resolvable
        )
        if missing or unresolved:
            risks.append(
                _risk(
                    "EVAL-RISK-PHANTOM-EVIDENCE",
                    "Candidate references evidence that is undeclared or absent from the configured source registry.",
                    unit_id=unit.get("id"),
                    undeclared_evidence_ids=missing,
                    unresolvable_source_ids=unresolved,
                )
            )
    # Keep the local variable useful to static readers and future detectors.
    _ = referenced
    return risks


def authority_mismatch(candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = set(context.get("allowed_authorities", []))
    if not allowed:
        allowed = {"human", "human_review", "external_authority", "approved_authority"}
    claims = candidate.get("claims", [])
    if isinstance(claims, str):
        claims = [claims]
    authority = candidate.get("authority")
    claim_text = " ".join(str(item).lower() for item in claims)
    elevated = ("canonical", "approved", "production", "complete", "semantic truth")
    if not any(token in claim_text for token in elevated):
        return []
    if authority in allowed:
        return []
    return [
        _risk(
            "EVAL-RISK-AUTHORITY-MISMATCH",
            "Candidate makes an elevated claim without an authority accepted by the evaluation contract.",
            claims=claims,
            authority=authority,
            allowed_authorities=sorted(allowed),
        )
    ]


def claim_ceiling_breach(candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    ceiling = context.get("claim_ceiling")
    forbidden = context.get("forbidden_claims", [])
    if isinstance(ceiling, dict):
        forbidden = list(forbidden) + list(ceiling.get("forbidden", []))
    claims = candidate.get("claims", [])
    if isinstance(claims, str):
        claims = [claims]
    found = [claim for claim in claims if any(token.lower() in str(claim).lower() for token in forbidden)]
    if not found:
        return []
    return [
        _risk(
            "EVAL-RISK-CLAIM-CEILING-BREACH",
            "Candidate claim exceeds the configured evidence or project claim ceiling.",
            claims=found,
            claim_ceiling=ceiling,
        )
    ]


def scope_or_partition_collapse(candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    required = set(context.get("required_partitions", []))
    actual = {item for item in candidate.get("unit_partitions", []) if isinstance(item, str)}
    missing = sorted(required - actual)
    if not missing:
        return []
    return [
        _risk(
            "EVAL-RISK-PARTITION-COLLAPSE",
            "Candidate omits required partitions, which may collapse independently reviewable scope.",
            required_partitions=sorted(required),
            observed_partitions=sorted(actual),
            missing_partitions=missing,
        )
    ]


def missing_counterexample(candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    required = context.get("required_counterexamples", [])
    supplied = candidate.get("counterexamples", [])
    missing = sorted(set(required) - set(supplied))
    if not missing:
        return []
    return [
        _risk(
            "EVAL-RISK-MISSING-COUNTEREXAMPLE",
            "Candidate does not provide the configured semantic boundary or failure counterexamples.",
            missing_counterexamples=missing,
        )
    ]


def stale_or_superseded_input(candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    registry = _registry_entries(context.get("source_registry"))
    stale: list[str] = []
    for source_id in candidate.get("source_ids", []):
        entry = registry.get(source_id)
        if isinstance(entry, dict) and (entry.get("stale") or entry.get("superseded_by")):
            stale.append(source_id)
    if not stale:
        return []
    return [_risk("EVAL-RISK-STALE-INPUT", "Candidate uses a stale or superseded source snapshot.", source_ids=sorted(stale))]


def unverified_external_dependency(candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    registry = _registry_entries(context.get("source_registry"))
    unresolved = []
    resolved_results = {item.get("source_id"): item for item in context.get("resolved_sources", []) if isinstance(item, dict)}
    for source_id in candidate.get("source_ids", []):
        entry = registry.get(source_id)
        if isinstance(entry, dict) and entry.get("kind") in {"url", "external", "external_url", "repo_symbol"}:
            receipt = entry.get("receipt") if isinstance(entry.get("receipt"), dict) else {}
            resolved = resolved_results.get(source_id, {})
            if resolved.get("status") not in {"resolved"} or not resolved.get("content_verified"):
                unresolved.append(source_id)
    if not unresolved:
        return []
    return [_risk("EVAL-RISK-UNVERIFIED-EXTERNAL", "External source lacks a saved resolution receipt or content digest.", source_ids=sorted(unresolved))]


def recomputed_expected_as_validation(candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    golden = context.get("golden") or {}
    if not isinstance(golden, dict):
        return []
    candidate_id = candidate.get("artifact_id")
    golden_id = golden.get("artifact_id")
    if candidate_id and golden_id and candidate_id == golden_id:
        return [_risk("EVAL-RISK-RECOMPUTED-EXPECTED", "Golden and candidate share an identity; expected output may be self-generated.", artifact_id=candidate_id)]
    if golden.get("generated_by") == candidate_id or golden.get("derived_from") == candidate_id:
        return [_risk("EVAL-RISK-RECOMPUTED-EXPECTED", "Golden declares the candidate as its generator or source.", artifact_id=candidate_id)]
    return []


def source_identity_collision(candidate: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    registry = _registry_entries(context.get("source_registry"))
    by_digest: defaultdict[str, list[str]] = defaultdict(list)
    for source_id, entry in registry.items():
        if isinstance(entry, dict) and (entry.get("content_digest") or entry.get("digest")):
            by_digest[str(entry.get("content_digest") or entry.get("digest"))].append(str(source_id))
    collisions = [sorted(ids) for ids in by_digest.values() if len(ids) > 1]
    if not collisions:
        return []
    return [_risk("EVAL-RISK-SOURCE-ID-COLLISION", "Multiple source identities resolve to the same content digest.", collisions=collisions)]


DETECTORS: dict[str, Detector] = {
    "unrecorded_state_write": unrecorded_state_write,
    "unpaired_side_effect": unrecorded_state_write,
    "missing_evidence_reference": phantom_evidence_reference,
    "phantom_evidence_reference": phantom_evidence_reference,
    "authority_mismatch": authority_mismatch,
    "claim_ceiling_breach": claim_ceiling_breach,
    "scope_or_partition_collapse": scope_or_partition_collapse,
    "missing_counterexample": missing_counterexample,
    "stale_or_superseded_input": stale_or_superseded_input,
    "unverified_external_dependency": unverified_external_dependency,
    "recomputed_expected_as_validation": recomputed_expected_as_validation,
    "source_identity_collision": source_identity_collision,
}


def run_candidate_detectors(
    candidate: dict[str, Any],
    contract: dict[str, Any] | None = None,
    *,
    source_registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run explicitly configured candidate-only checks.

    A missing ``candidate_risk_checks`` list intentionally means no checks are
    run; this keeps the portable core conservative and avoids domain guesses.
    """

    contract = contract or {}
    context = dict(contract)
    if source_registry is not None:
        context["source_registry"] = source_registry
    selected = contract.get("candidate_risk_checks", [])
    results: list[dict[str, Any]] = []
    for name in selected:
        detector = DETECTORS.get(str(name))
        if detector is not None:
            results.extend(detector(candidate, context))
    return results

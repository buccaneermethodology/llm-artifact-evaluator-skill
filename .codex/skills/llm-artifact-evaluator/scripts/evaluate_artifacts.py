#!/usr/bin/env python3
"""Evaluate one structured LLM artifact with deterministic, bounded checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from candidate_detectors import run_candidate_detectors
from source_registry import load_registry, resolve_registry
from typed_comparators import compare_dimension

CORE_STATUSES = ("pass", "error", "warning", "not_run")
FORBIDDEN_PROFILE_KEYS = {
    "core_status_overrides",
    "status_overrides",
    "downgrade_errors",
    "ignore_error_codes",
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def finding(code: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "status": status, "message": message}
    if details:
        result["details"] = details
    return result


def type_matches(value: Any, expected: str) -> bool:
    checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    return expected in checks and checks[expected](value)


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type and not type_matches(value, expected_type):
        return [f"{path}: expected {expected_type}, got {type(value).__name__}"]

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, child in value.items():
            if key in properties:
                errors.extend(validate_schema(child, properties[key], f"{path}.{key}"))
    elif isinstance(value, list) and "items" in schema:
        for index, child in enumerate(value):
            errors.extend(validate_schema(child, schema["items"], f"{path}[{index}]"))

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not in enum")
    return errors


def get_path(value: Any, dotted_path: str) -> tuple[bool, Any]:
    current = value
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def semantic_findings(candidate: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Run portable, contract-driven semantic predicates.

    The evaluator does not infer domain meaning.  It only checks relationships
    explicitly supplied by the caller, keeping the public skill portable.
    """
    semantic = contract.get("semantic_contract")
    if not semantic:
        return []
    results: list[dict[str, Any]] = []
    roles = semantic.get("roles", {})
    for path, expected_role in roles.items():
        found, value = get_path(candidate, path.lstrip("$."))
        if not found:
            results.append(finding("EVAL-SEMANTIC-FAIL", "error", "Configured semantic role path is missing.", path=path, expected_role=expected_role))
        elif value in (None, [], {}, ""):
            results.append(finding("EVAL-SEMANTIC-INCONCLUSIVE", "warning", "Configured semantic role has no observable value.", path=path, role=expected_role, result="inconclusive"))
    for invariant in semantic.get("invariants", []):
        if isinstance(invariant, str):
            name, path = invariant, invariant
        else:
            name, path = invariant.get("name", "unnamed"), invariant.get("path", "")
        if path:
            found, value = get_path(candidate, path.lstrip("$."))
            if not found or value in (None, [], {}, ""):
                results.append(finding("EVAL-SEMANTIC-FAIL", "error", "Configured semantic invariant is not satisfied.", invariant=name, path=path))
        else:
            results.append(finding("EVAL-SEMANTIC-INCONCLUSIVE", "warning", "Semantic invariant has no executable predicate.", invariant=name, result="inconclusive"))
    forbidden = semantic.get("forbidden_collapses", [])
    text = json.dumps(candidate, ensure_ascii=False).lower()
    for item in forbidden:
        pattern = item if isinstance(item, str) else item.get("pattern", "")
        if pattern and pattern.lower() in text:
            results.append(finding("EVAL-SEMANTIC-FORBIDDEN-COLLAPSE", "error", "Candidate contains a forbidden semantic collapse.", pattern=pattern))
    if not results:
        results.append(finding("EVAL-SEMANTIC-VALID", "pass", "Configured semantic contract is satisfied."))
    return results


def aggregate_status(findings: list[dict[str, Any]]) -> str:
    statuses = {item["status"] for item in findings}
    if "error" in statuses:
        return "error"
    if "warning" in statuses:
        return "warning"
    if statuses == {"not_run"}:
        return "not_run"
    return "pass"


def evaluate(case_dir: Path, candidate_path: Path, profile_path: Path | None) -> dict[str, Any]:
    contract_path = case_dir / "contract.json"
    contract = load_json(contract_path)
    schema = load_json(case_dir / contract["schema"])
    candidate = load_json(candidate_path)
    findings: list[dict[str, Any]] = []

    schema_errors = validate_schema(candidate, schema)
    if schema_errors:
        findings.append(
            finding(
                "EVAL-SCHEMA-ERROR",
                "error",
                "Candidate does not satisfy the configured schema.",
                errors=schema_errors,
            )
        )
    else:
        findings.append(finding("EVAL-SCHEMA-VALID", "pass", "Candidate satisfies the configured schema."))

    required_sources = set(contract.get("required_source_ids", []))
    declared_sources = set(candidate.get("source_ids", []))
    referenced_sources = {
        evidence_id
        for unit in candidate.get("units", [])
        if isinstance(unit, dict)
        for evidence_id in unit.get("evidence_ids", [])
        if isinstance(evidence_id, str)
    }
    missing_required = sorted(required_sources - declared_sources)
    undeclared_references = sorted(referenced_sources - declared_sources)
    if missing_required or undeclared_references:
        findings.append(
            finding(
                "EVAL-LINEAGE-MISSING",
                "error",
                "Candidate lineage is incomplete or references undeclared evidence.",
                missing_required_source_ids=missing_required,
                undeclared_evidence_references=undeclared_references,
            )
        )
    else:
        findings.append(finding("EVAL-LINEAGE-VALID", "pass", "Candidate lineage covers required and referenced sources."))

    source_registry = None
    registry_ref = contract.get("source_registry")
    if registry_ref:
        try:
            source_registry = load_registry(case_dir / registry_ref)
            resolutions = resolve_registry(sorted(declared_sources), source_registry, base_dir=case_dir)
            # Pass resolver evidence to opt-in detectors so they do not infer
            # resolution from registry declarations alone.
            resolved_source_context = resolutions
            bad = [item for item in resolutions if item.get("status") in {"unresolved", "error"}]
            uncertain = [item for item in resolutions if item.get("status") == "inconclusive"]
            if bad:
                findings.append(finding("EVAL-LINEAGE-SOURCE-UNRESOLVED", "error", "One or more declared sources cannot be resolved or verified.", sources=bad))
            elif uncertain:
                findings.append(finding("EVAL-LINEAGE-SOURCE-INCONCLUSIVE", "warning", "One or more declared sources cannot be fully verified from the supplied registry.", sources=uncertain, result="inconclusive"))
            else:
                findings.append(finding("EVAL-LINEAGE-SOURCE-VERIFIED", "pass", "Declared sources resolve through the configured offline registry.", sources=resolutions))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            findings.append(finding("EVAL-LINEAGE-SOURCE-INCONCLUSIVE", "warning", "Source registry could not be evaluated.", reason=str(exc), result="inconclusive"))

    golden_config = contract.get("golden")
    if not golden_config:
        findings.append(finding("EVAL-GOLDEN-NOT-CONFIGURED", "not_run", "No golden comparison is configured."))
    else:
        authority = golden_config.get("authority")
        provenance = golden_config.get("provenance")
        golden_path = (case_dir / golden_config.get("path", "")).resolve()
        if not authority or not provenance or golden_path == candidate_path.resolve():
            findings.append(
                finding(
                    "EVAL-GOLDEN-MISMATCH",
                    "error",
                    "Golden authority/provenance is missing or the candidate was selected as its own golden.",
                )
            )
        else:
            golden = load_json(golden_path)
            mismatches: list[dict[str, Any]] = []
            inconclusive: list[dict[str, Any]] = []
            for dimension in golden_config.get("dimensions", []):
                if isinstance(dimension, str):
                    path, kind, options = dimension, "exact", {}
                else:
                    path, kind, options = dimension.get("path", ""), dimension.get("comparator", "exact"), dimension.get("options", {})
                golden_found, golden_value = get_path(golden, path)
                candidate_found, candidate_value = get_path(candidate, path)
                if not golden_found or not candidate_found:
                    inconclusive.append({"dimension": path, "comparator": kind, "category": "inconclusive", "golden": "<missing>" if not golden_found else golden_value, "candidate": "<missing>" if not candidate_found else candidate_value, "details": {"reason": "dimension path is missing"}})
                    continue
                comparison = compare_dimension(kind, golden_value if golden_found else None, candidate_value if candidate_found else None, options=options)
                if comparison.matched is False:
                    item = comparison.as_dict()
                    item["dimension"] = path
                    mismatches.append(item)
                elif comparison.matched is None:
                    item = comparison.as_dict()
                    item["dimension"] = path
                    inconclusive.append(item)
            if mismatches:
                findings.append(
                    finding(
                        "EVAL-GOLDEN-MISMATCH",
                        "error",
                        "Candidate differs from the selected golden on configured dimensions.",
                        mismatches=mismatches,
                    )
                )
            elif inconclusive:
                findings.append(finding("EVAL-GOLDEN-INCONCLUSIVE", "warning", "Some configured golden dimensions cannot be compared from the supplied values.", dimensions=inconclusive, result="inconclusive"))
            else:
                findings.append(finding("EVAL-GOLDEN-MATCH", "pass", "Configured golden dimensions match."))

    selected_risks = contract.get("candidate_risk_checks", [])
    legacy_only = set(selected_risks).issubset({"unrecorded_state_write", "missing_evidence_reference"})
    risk_details: list[dict[str, Any]] = []
    if legacy_only:
        for unit in candidate.get("units", []):
            if not isinstance(unit, dict):
                continue
            writes = set(unit.get("writes", []))
            recorded = set(unit.get("state_change_records", []))
            unrecorded = sorted(writes - recorded)
            if unrecorded:
                risk_details.append({"unit_id": unit.get("id"), "risk": "unrecorded_state_write", "writes": unrecorded})
            missing_evidence = sorted(set(unit.get("evidence_ids", [])) - declared_sources)
            if missing_evidence:
                risk_details.append({"unit_id": unit.get("id"), "risk": "missing_evidence_reference", "evidence_ids": missing_evidence})
    else:
        detector_contract = dict(contract)
        detector_contract["resolved_sources"] = locals().get("resolved_source_context", [])
        risk_details = run_candidate_detectors(candidate, detector_contract, source_registry=source_registry)
    if risk_details:
        findings.append(
            finding(
                "EVAL-CANDIDATE-RISK",
                "warning",
                "Candidate-only heuristic checks found risks requiring review.",
                risks=risk_details,
            )
        )
    else:
        findings.append(finding("EVAL-CANDIDATE-RISK-CLEAR", "pass", "Configured candidate-only risks were not detected."))

    expectation_ref = contract.get("negative_expectations")
    if expectation_ref:
        expectations = load_json(case_dir / expectation_ref)
        try:
            candidate_key = candidate_path.resolve().relative_to(case_dir.resolve()).as_posix()
        except ValueError:
            candidate_key = candidate_path.name
        expected_codes = expectations.get(candidate_key)
        if expected_codes:
            observed_codes = {item["code"] for item in findings}
            missing_codes = sorted(set(expected_codes) - observed_codes)
            if missing_codes:
                findings.append(
                    finding(
                        "EVAL-NEGATIVE-NOT-DETECTED",
                        "error",
                        "A negative fixture did not trigger every expected finding.",
                        missing_finding_codes=missing_codes,
                    )
                )
            else:
                findings.append(
                    finding("EVAL-NEGATIVE-DETECTED", "pass", "Negative fixture triggered every expected finding.")
                )
        else:
            findings.append(finding("EVAL-NEGATIVE-NOT-APPLICABLE", "not_run", "Candidate is not registered as a negative fixture."))
    else:
        findings.append(finding("EVAL-NEGATIVE-NOT-CONFIGURED", "not_run", "No negative expectations are configured."))

    configured_semantic = bool(contract.get("semantic_contract"))
    semantic_results = semantic_findings(candidate, contract) if configured_semantic else []
    findings.extend(semantic_results)

    core_status = aggregate_status(findings)
    profile_result: dict[str, Any] = {
        "profile_name": None,
        "project_status": core_status,
        "project_label": core_status,
        "strict_warning_codes_triggered": [],
    }
    if profile_path:
        profile = load_json(profile_path)
        forbidden = sorted(FORBIDDEN_PROFILE_KEYS.intersection(profile))
        if forbidden:
            findings.append(
                finding(
                    "EVAL-PROFILE-MASKS-CORE",
                    "error",
                    "Project profile contains fields that could replace or downgrade core results.",
                    forbidden_keys=forbidden,
                )
            )
            core_status = aggregate_status(findings)
        triggered = sorted(
            set(profile.get("strict_warning_codes", []))
            & {item["code"] for item in findings if item["status"] == "warning"}
        )
        project_status = "error" if core_status == "warning" and triggered else core_status
        labels = profile.get("labels", {})
        profile_result = {
            "profile_name": profile.get("profile_name"),
            "project_status": project_status,
            "project_label": labels.get(project_status, project_status),
            "strict_warning_codes_triggered": triggered,
        }

    try:
        candidate_ref = candidate_path.resolve().relative_to(case_dir.resolve()).as_posix()
    except ValueError:
        candidate_ref = candidate_path.name
    report = {
        "report_version": "1.1" if (configured_semantic or registry_ref or any(isinstance(item, dict) for item in (golden_config or {}).get("dimensions", []))) else "1.0",
        "case": case_dir.name,
        "candidate": candidate_ref,
        "core_vocabulary": list(CORE_STATUSES),
        "core_status": core_status,
        "profile": profile_result,
        "findings": findings,
        "evidence_ceiling": (
            "This report covers configured deterministic checks and heuristic risks only; "
            "it does not prove semantic truth or production readiness."
        ),
    }
    if configured_semantic:
        report["semantic"] = {
            "status": "error" if any(item["status"] == "error" for item in semantic_results) else ("warning" if any(item["status"] == "warning" for item in semantic_results) else "pass"),
            "finding_codes": [item["code"] for item in semantic_results],
        }
    if registry_ref:
        report["lineage"] = {"source_registry": registry_ref, "offline": True}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, type=Path, help="Evaluation case directory")
    parser.add_argument(
        "--candidate",
        type=Path,
        default=Path("candidates/candidate-pass.json"),
        help="Candidate path, relative to --case unless absolute",
    )
    parser.add_argument("--profile", type=Path, help="Optional project profile JSON")
    parser.add_argument("--output", type=Path, help="Optional report output path")
    args = parser.parse_args()

    case_dir = args.case.resolve()
    candidate_path = args.candidate if args.candidate.is_absolute() else case_dir / args.candidate
    profile_path = args.profile.resolve() if args.profile else None
    try:
        report = evaluate(case_dir, candidate_path.resolve(), profile_path)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "report_version": "1.0",
            "core_vocabulary": list(CORE_STATUSES),
            "core_status": "error",
            "findings": [finding("EVAL-INPUT-ERROR", "error", f"Evaluation inputs are invalid: {exc}")],
            "evidence_ceiling": "Input failure; no artifact acceptance claim is supported.",
        }

    rendered = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    project_status = report.get("profile", {}).get("project_status")
    return 1 if report["core_status"] == "error" or project_status == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())

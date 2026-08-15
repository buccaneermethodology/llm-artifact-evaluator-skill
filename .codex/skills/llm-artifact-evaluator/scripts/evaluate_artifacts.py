#!/usr/bin/env python3
"""Evaluate one structured LLM artifact with deterministic, bounded checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

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
            for dimension in golden_config.get("dimensions", []):
                golden_found, golden_value = get_path(golden, dimension)
                candidate_found, candidate_value = get_path(candidate, dimension)
                if not golden_found or not candidate_found or golden_value != candidate_value:
                    mismatches.append(
                        {
                            "dimension": dimension,
                            "golden": golden_value if golden_found else "<missing>",
                            "candidate": candidate_value if candidate_found else "<missing>",
                        }
                    )
            if mismatches:
                findings.append(
                    finding(
                        "EVAL-GOLDEN-MISMATCH",
                        "error",
                        "Candidate differs from the selected golden on configured dimensions.",
                        mismatches=mismatches,
                    )
                )
            else:
                findings.append(finding("EVAL-GOLDEN-MATCH", "pass", "Configured golden dimensions match."))

    risk_details: list[dict[str, Any]] = []
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
            risk_details.append(
                {"unit_id": unit.get("id"), "risk": "missing_evidence_reference", "evidence_ids": missing_evidence}
            )
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
    return {
        "report_version": "1.0",
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

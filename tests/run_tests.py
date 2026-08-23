#!/usr/bin/env python3
"""Regression tests for the bundled evaluator and fixtures."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate_artifacts.py"
FIXTURE = ROOT / "examples" / "minimal-evaluation"


def run(case_dir: Path, candidate: str, profile: Path | None = None) -> tuple[int, dict]:
    command = [sys.executable, str(SCRIPT), "--case", str(case_dir), "--candidate", candidate]
    if profile:
        command.extend(["--profile", str(profile)])
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"Evaluator did not emit JSON: {completed.stdout}\n{completed.stderr}") from exc
    return completed.returncode, report


def codes(report: dict) -> set[str]:
    return {item["code"] for item in report["findings"]}


def copy_fixture() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory()
    target = Path(temporary.name) / "case"
    shutil.copytree(FIXTURE, target)
    return temporary, target


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_positive() -> None:
    returncode, report = run(FIXTURE, "candidates/candidate-pass.json")
    assert returncode == 0
    assert report["core_status"] == "pass"
    assert report["core_vocabulary"] == ["pass", "error", "warning", "not_run"]


def test_expected_report_is_stable() -> None:
    returncode, report = run(FIXTURE, "candidates/candidate-pass.json")
    expected = json.loads((FIXTURE / "expected-report.json").read_text(encoding="utf-8"))
    assert returncode == 0
    assert report == expected


def test_schema_failure() -> None:
    temporary, case_dir = copy_fixture()
    try:
        candidate_path = case_dir / "candidates" / "candidate-pass.json"
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        del candidate["units"]
        write_json(candidate_path, candidate)
        returncode, report = run(case_dir, "candidates/candidate-pass.json")
        assert returncode == 1
        assert "EVAL-SCHEMA-ERROR" in codes(report)
    finally:
        temporary.cleanup()


def test_lineage_failure() -> None:
    temporary, case_dir = copy_fixture()
    try:
        candidate_path = case_dir / "candidates" / "candidate-pass.json"
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        candidate["source_ids"] = ["source-a"]
        write_json(candidate_path, candidate)
        returncode, report = run(case_dir, "candidates/candidate-pass.json")
        assert returncode == 1
        assert "EVAL-LINEAGE-MISSING" in codes(report)
    finally:
        temporary.cleanup()


def test_golden_mismatch() -> None:
    returncode, report = run(FIXTURE, "candidates/candidate-golden-mismatch.json")
    assert returncode == 1
    assert "EVAL-GOLDEN-MISMATCH" in codes(report)


def test_candidate_risk_warning() -> None:
    returncode, report = run(FIXTURE, "candidates/candidate-warning.json")
    assert returncode == 0
    assert report["core_status"] == "warning"
    assert "EVAL-CANDIDATE-RISK" in codes(report)


def test_negative_detection() -> None:
    returncode, report = run(FIXTURE, "candidates/candidate-negative.json")
    assert returncode == 1  # lineage is a deterministic error in this intentionally bad fixture
    assert "EVAL-CANDIDATE-RISK" in codes(report)
    assert "EVAL-NEGATIVE-DETECTED" in codes(report)


def test_negative_not_detected() -> None:
    temporary, case_dir = copy_fixture()
    try:
        write_json(
            case_dir / "negative-expectations.json",
            {"candidates/candidate-pass.json": ["EVAL-CANDIDATE-RISK"]},
        )
        returncode, report = run(case_dir, "candidates/candidate-pass.json")
        assert returncode == 1
        assert "EVAL-NEGATIVE-NOT-DETECTED" in codes(report)
    finally:
        temporary.cleanup()


def test_profile_can_tighten_without_rewriting_core() -> None:
    returncode, report = run(
        FIXTURE,
        "candidates/candidate-warning.json",
        FIXTURE / "evaluation-profile.json",
    )
    assert returncode == 1
    assert report["core_status"] == "warning"
    assert report["profile"]["project_status"] == "error"
    assert report["profile"]["strict_warning_codes_triggered"] == ["EVAL-CANDIDATE-RISK"]


def test_unique_codex_skill_root() -> None:
    skill_root = ROOT / ".codex" / "skills" / "llm-artifact-evaluator"
    assert (skill_root / "SKILL.md").is_file()
    assert (skill_root / "agents" / "openai.yaml").is_file()
    assert (skill_root / "references" / "REFERENCE.md").is_file()
    assert (skill_root / "scripts" / "evaluate_artifacts.py").is_file()
    assert not (ROOT / "skill").exists()


def test_profile_masking_is_rejected() -> None:
    temporary, case_dir = copy_fixture()
    try:
        profile_path = case_dir / "masking-profile.json"
        write_json(
            profile_path,
            {
                "profile_name": "invalid-mask",
                "core_status_overrides": {"error": "pass"},
                "labels": {"pass": "ok", "warning": "review", "error": "blocked", "not_run": "n/a"},
            },
        )
        returncode, report = run(case_dir, "candidates/candidate-pass.json", profile_path)
        assert returncode == 1
        assert report["core_status"] == "error"
        assert "EVAL-PROFILE-MASKS-CORE" in codes(report)
    finally:
        temporary.cleanup()


def test_typed_golden_set_comparator() -> None:
    temporary, case_dir = copy_fixture()
    try:
        contract = json.loads((case_dir / "contract.json").read_text(encoding="utf-8"))
        contract["golden"]["dimensions"] = [{"path": "decision_ids", "comparator": "set"}]
        write_json(case_dir / "contract.json", contract)
        candidate_path = case_dir / "candidates" / "candidate-pass.json"
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        candidate["decision_ids"] = ["decision-1"]
        write_json(candidate_path, candidate)
        returncode, report = run(case_dir, "candidates/candidate-pass.json")
        assert returncode == 0
        assert "EVAL-GOLDEN-MATCH" in codes(report)
    finally:
        temporary.cleanup()


def test_semantic_contract_failure() -> None:
    temporary, case_dir = copy_fixture()
    try:
        contract = json.loads((case_dir / "contract.json").read_text(encoding="utf-8"))
        contract["semantic_contract"] = {"roles": {"missing_field": "decision"}}
        write_json(case_dir / "contract.json", contract)
        returncode, report = run(case_dir, "candidates/candidate-pass.json")
        assert returncode == 1
        assert "EVAL-SEMANTIC-FAIL" in codes(report)
        assert report["semantic"]["status"] == "error"
    finally:
        temporary.cleanup()


def test_source_registry_resolution() -> None:
    temporary, case_dir = copy_fixture()
    try:
        source = case_dir / "source-a.txt"
        source.write_text("grounded source\n", encoding="utf-8")
        import hashlib
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        contract = json.loads((case_dir / "contract.json").read_text(encoding="utf-8"))
        contract["source_registry"] = "source-registry.json"
        write_json(case_dir / "source-registry.json", {"sources": {"source-a": {"kind": "local_file", "path": "source-a.txt", "sha256": digest}, "source-b": {"kind": "local_file", "path": "source-a.txt", "sha256": digest}}})
        write_json(case_dir / "contract.json", contract)
        returncode, report = run(case_dir, "candidates/candidate-pass.json")
        assert returncode == 0
        assert "EVAL-LINEAGE-SOURCE-VERIFIED" in codes(report)
    finally:
        temporary.cleanup()


def test_opt_in_candidate_detector() -> None:
    temporary, case_dir = copy_fixture()
    try:
        contract = json.loads((case_dir / "contract.json").read_text(encoding="utf-8"))
        contract["candidate_risk_checks"] = ["claim_ceiling_breach"]
        contract["claim_ceiling"] = {"forbidden": ["production-ready"]}
        write_json(case_dir / "contract.json", contract)
        candidate_path = case_dir / "candidates" / "candidate-pass.json"
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        candidate["claims"] = ["production-ready"]
        write_json(candidate_path, candidate)
        returncode, report = run(case_dir, "candidates/candidate-pass.json")
        assert returncode == 0
        assert any(item.get("code") == "EVAL-RISK-CLAIM-CEILING-BREACH" for item in report["findings"][3]["details"]["risks"])
        assert report["core_status"] == "warning"
    finally:
        temporary.cleanup()


def main() -> int:
    tests = [
        test_positive,
        test_expected_report_is_stable,
        test_schema_failure,
        test_lineage_failure,
        test_golden_mismatch,
        test_candidate_risk_warning,
        test_negative_detection,
        test_negative_not_detected,
        test_profile_can_tighten_without_rewriting_core,
        test_unique_codex_skill_root,
        test_profile_masking_is_rejected,
        test_typed_golden_set_comparator,
        test_semantic_contract_failure,
        test_source_registry_resolution,
        test_opt_in_candidate_detector,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS {len(tests)} tests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

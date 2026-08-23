# LLM Artifact Evaluator Skill

A portable Codex skill and standard-library evaluator for structured LLM outputs. It separates schema and lineage errors, typed Golden comparisons, contract-driven semantic checks, candidate-only risk detection, offline source verification, negative-fixture detection, and optional project policy.

## Install

Copy `.codex/skills/llm-artifact-evaluator` into your Codex skills directory:

```bash
cp -R .codex/skills/llm-artifact-evaluator "${CODEX_HOME:-$HOME/.codex}/skills/"
```

Invoke it as `$llm-artifact-evaluator`.

## Quick start

```bash
python3 scripts/evaluate_artifacts.py \
  --case examples/minimal-evaluation \
  --candidate candidates/candidate-pass.json

python3 tests/run_tests.py
```

Use `--profile examples/minimal-evaluation/evaluation-profile.json` for project labels and stricter warning handling. When a strict profile produces `project_status=error`, the CLI exits nonzero even if the preserved `core_status` is only `warning`. Use `--output report.json` to preserve the report.

For richer evaluation, add optional `semantic_contract` and `source_registry` entries to `contract.json`. Golden dimensions may be strings (backward-compatible `exact`) or objects such as `{ "path": "decision_ids", "comparator": "set" }`. Source registries are offline-first: local files are checked directly, while URLs require a saved receipt and are never fetched by deterministic CI.

The minimum useful input is a candidate plus `contract.json` and schema. Golden, semantic contract, and source registry are optional; when absent, the evaluator reports the corresponding checks as not configured rather than inventing authority or expected output.

The evaluator uses only the Python standard library. See [the full reference](.codex/skills/llm-artifact-evaluator/references/REFERENCE.md) for contracts, finding codes, negative tests, and adoption guidance.

## Evidence boundary

Passing the bundled checks proves only that the supplied artifact satisfies the configured deterministic checks and avoids the included heuristic smells. It does not prove that the artifact is semantically true, that its golden is authoritative outside the selecting project, or that the package is production-ready for every use case.

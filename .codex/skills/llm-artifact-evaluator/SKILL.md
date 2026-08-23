---
name: llm-artifact-evaluator
description: Evaluate and harden LLM-generated structured artifacts such as JSON, extraction outputs, plans, and code-derived facts. Use when correctness needs schema and lineage checks, repeated trials, golden comparison, semantic boundary audits, candidate-only risk detection, negative fixtures, acceptance policy, or bounded revision loops.
---

# LLM Artifact Evaluator

Use this workflow when an LLM can produce a plausible artifact but downstream use requires stronger evidence than valid-looking JSON.

## Workflow

1. Freeze the artifact contract, source identity, prompt/model/context metadata, and acceptance policy.
2. Validate at least one known-good example before evaluating candidates.
3. Select a manually reviewed or project-designated golden. Record its authority and provenance; never reconstruct it from the candidate being judged.
4. Preserve every raw response and parsed candidate. Run independent attempts with fixed inputs when measuring stability.
5. Validate schema and lineage before semantic comparison.
6. Compare configured golden dimensions with typed comparators (`exact`, `set`, `multiset`, `partition`, `mapping`, `coverage`, `graph`, `bounded_numeric`, `absence`), then run explicitly selected candidate-only risk checks.
7. If configured, resolve declared sources through an offline `source_registry`; external URLs require a saved receipt and are never fetched by deterministic CI.
8. If configured, run the portable `semantic_contract` predicates and report semantic failures separately from structural checks.
9. Keep deterministic contract failures as `error`, heuristic risks as `warning`, successful checks as `pass`, and unavailable checks as `not_run`.
10. Apply an optional project profile only after core evaluation. Permit stricter project policy or display labels, but never downgrade or hide a core error. Return a nonzero CLI exit when either core or project status is `error`.
11. Use bounded revision loops only after reports contain stable finding codes and exact repair evidence.

## Bundled evaluator

Run the public fixture from the repository root:

```bash
python3 scripts/evaluate_artifacts.py \
  --case examples/minimal-evaluation \
  --candidate candidates/candidate-pass.json
```

The evaluator emits a machine-readable report. Read [references/REFERENCE.md](references/REFERENCE.md) for the file contract, typed dimensions, source registry, finding codes, adoption guidance, and evidence ceiling.

## Boundaries

- Do not treat schema pass as semantic truth or production readiness.
- Do not hide failed trials, missing lineage, golden mismatch, or negative fixtures.
- Do not put remote LLM calls inside deterministic test commands.
- Do not let a project profile mask core blocking results.
- Do not tune a gate to one golden without checking another positive or negative fixture for false results.

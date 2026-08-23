# LLM Artifact Evaluator Reference

## 1. Problem and fit

Structured LLM output can be syntactically valid while silently changing partitions, omitting source lineage, inventing evidence, broadening side effects, or collapsing decisions. This skill supplies a repeatable evaluation loop and a small deterministic evaluator for exposing those failures before downstream use.

Use it for JSON extraction results, machine-readable plans, schemas, code-derived facts, migration candidates, or other artifacts whose contract can be checked. It is especially useful when you have repeated trials, a trusted comparison example, negative fixtures, or a downstream acceptance policy.

Do not use it as a substitute for a human domain oracle, live-system validation, security review, factual verification against authoritative sources, or production readiness testing. Natural-language output without an explicit machine-readable contract usually needs a different review method first.

## 2. Concepts and authority boundaries

### Contract

The contract fixes the schema path, required source identities, optional golden authority, semantic dimensions, candidate-only checks, and negative expectations. Change the contract deliberately and version the change; do not tune it invisibly after seeing a candidate fail.

### Candidate

A candidate is the parsed artifact being evaluated. Preserve the raw model response, prompt, model identifier, input identity, and context policy outside this minimal fixture when running real trials. A candidate never becomes its own oracle.

### Golden

A golden is a manually reviewed or project-designated comparison artifact. Its authority is local to the selecting project and purpose. The evaluator requires both `authority` and `provenance` and rejects self-comparison, but it cannot prove that a reviewer made the right semantic choice.

### Core result and project profile

Core checks use exactly four statuses:

- `pass`: the configured check succeeded;
- `error`: a deterministic contract or blocking check failed;
- `warning`: a heuristic risk requires review;
- `not_run`: the check was unavailable or not applicable.

A project profile may provide display labels and may make selected warnings project-blocking. It cannot replace core status, ignore an error, or downgrade a blocking finding. This separation lets projects add policy without redefining portable evaluator facts.

The CLI exits nonzero when either `core_status` or `profile.project_status` is `error`. This makes project blocking policy executable in shell and CI while preserving the original core finding history.

### Golden-assisted versus candidate-only checks

Golden-assisted checks compare configured dimensions to a selected oracle. Candidate-only checks look for high-confidence smells without requiring an oracle. Keep both because production inputs often have no matching golden.

## 3. End-to-end workflow

1. Freeze the input, prompt, model, context policy, artifact schema, and source identities.
2. Validate a known-good artifact to ensure the evaluator and schema agree.
3. Select and document a golden independently of the candidate.
4. Run at least three isolated generations when measuring stability; retain raw and parsed failures.
5. Run schema validation. Stop downstream acceptance if it emits `EVAL-SCHEMA-ERROR`.
6. Run lineage validation. Treat absent required sources or undeclared evidence references as `EVAL-LINEAGE-MISSING`.
7. Compare only explicitly named golden dimensions. A mismatch is evidence of boundary drift, not automatic proof that the golden is universally correct.
8. Run candidate-only risks such as unrecorded writes and missing evidence references.
9. Run registered negative fixtures and verify that expected finding codes appear.
10. Apply project policy after the core report; allow tightening, never masking.
11. Repair in a bounded loop using the original contract, prior candidate, and exact findings. Stop when accepted or when disagreement belongs to the contract, source, or golden.
12. Preserve the final report plus every failed trial needed to explain the result.

## 4. Repository and file contract

```text
.codex/skills/llm-artifact-evaluator/
  SKILL.md
  agents/openai.yaml
  references/REFERENCE.md
examples/minimal-evaluation/
  artifact.schema.json
  contract.json
  golden.json
  evaluation-profile.json
  negative-expectations.json
  expected-report.json
  candidates/
scripts/
  evaluate_artifacts.py
  validate_skill_layout.py
tests/run_tests.py
```

### `contract.json`

- `contract_version`: caller-owned contract revision.
- `schema`: schema path relative to the case directory.
- `required_source_ids`: lineage identifiers every candidate must declare.
- `golden.path`: comparison artifact relative to the case directory.
- `golden.authority`: project-defined authority class, such as `human_reviewed_example`.
- `golden.provenance`: explanation of who or what selected the golden independently.
- `golden.dimensions`: dotted object paths compared exactly.
- `candidate_risk_checks`: documented candidate-only checks expected by this case.
- `negative_expectations`: path to candidate-to-finding-code expectations.

### Candidate artifact

The bundled schema requires:

- `artifact_id`: stable trial/candidate identity;
- `source_ids`: declared lineage;
- `unit_partitions`: semantic grouping used by the golden comparison;
- `decision_ids`: decisions expected by the configured case;
- `units`: structured units with `id`, `summary`, `evidence_ids`, `writes`, and `state_change_records`.

This is a minimal teaching schema, not a universal artifact model. Replace it with a project schema while retaining the evaluation order and authority split.

### Project profile

- `profile_name`: project policy identity.
- `labels`: optional display labels keyed by core status.
- `strict_warning_codes`: warning codes that make `profile.project_status` equal `error` while leaving `core_status` unchanged.

Fields that attempt core status replacement, downgrade, or ignore behavior are rejected with `EVAL-PROFILE-MASKS-CORE`.

### Report

- `report_version`: report contract version.
- `case` and `candidate`: evaluated inputs.
- `core_vocabulary`: portable status vocabulary.
- `core_status`: aggregate core result.
- `profile`: separate project status and label.
- `findings`: stable code, core status, explanation, and optional details.
- `evidence_ceiling`: explicit limit on what the report proves.

## 5. Finding codes

| Code | Core status | Meaning |
| --- | --- | --- |
| `EVAL-SCHEMA-ERROR` | `error` | Candidate violates the configured schema. |
| `EVAL-LINEAGE-MISSING` | `error` | Required lineage is absent or evidence is undeclared. |
| `EVAL-GOLDEN-MISMATCH` | `error` | Configured dimensions differ, or golden authority is invalid. |
| `EVAL-CANDIDATE-RISK` | `warning` | Candidate-only heuristics found suspicious writes or evidence use. |
| `EVAL-NEGATIVE-NOT-DETECTED` | `error` | A registered negative fixture missed an expected finding. |
| `EVAL-PROFILE-MASKS-CORE` | `error` | Project policy attempts to replace or downgrade core results. |
| `EVAL-INPUT-ERROR` | `error` | Required JSON or contract input cannot be read safely. |

Companion success codes such as `EVAL-SCHEMA-VALID`, `EVAL-GOLDEN-MATCH`, and `EVAL-NEGATIVE-DETECTED` make the executed checks visible. `*-NOT-CONFIGURED` and `*-NOT-APPLICABLE` findings use `not_run`; they are not silently treated as proof.

## 6. Minimal example

From the repository root, evaluate the positive candidate:

```bash
python3 scripts/evaluate_artifacts.py \
  --case examples/minimal-evaluation \
  --candidate candidates/candidate-pass.json
```

The exit code is zero and `core_status` is `pass`. Preserve a report:

```bash
python3 scripts/evaluate_artifacts.py \
  --case examples/minimal-evaluation \
  --candidate candidates/candidate-pass.json \
  --output /tmp/evaluation-report.json
```

Run a candidate-only warning:

```bash
python3 scripts/evaluate_artifacts.py \
  --case examples/minimal-evaluation \
  --candidate candidates/candidate-warning.json
```

Its core result is `warning` and the process exits zero because the generic heuristic is advisory. Add the supplied project profile to make that warning project-blocking without rewriting core evidence:

```bash
python3 scripts/evaluate_artifacts.py \
  --case examples/minimal-evaluation \
  --candidate candidates/candidate-warning.json \
  --profile examples/minimal-evaluation/evaluation-profile.json
```

This strict-profile command reports `core_status=warning`, reports `profile.project_status=error`, and exits nonzero. The nonzero exit enforces the project policy; it does not rewrite the portable warning into a core error.

Run the full regression suite:

```bash
python3 tests/run_tests.py
```

## 7. Positive, negative, blocking, and advisory behavior

The positive candidate matches schema, lineage, selected golden dimensions, and candidate-only checks. `candidate-golden-mismatch.json` deliberately collapses partitions and decisions. `candidate-negative.json` combines an undeclared evidence reference with an unrecorded state write. The negative expectation file requires that the risk detector remain live.

Schema, lineage, configured golden mismatch, missed negative expectations, invalid inputs, and profile masking are blocking core errors. Candidate-only smell detection is advisory by default because heuristics can produce false positives. A project may list selected warning codes in `strict_warning_codes` to block its own downstream use.

Do not change a warning to a core error merely to satisfy one fixture. First test at least one positive and one negative shape and document the false-positive/false-negative tradeoff.

## 8. Common failures and repair route

### Schema passes but meaning drifts

Add or revise semantic dimensions, negative fixtures, or a human review rubric. Do not claim the schema establishes meaning.

### Golden oscillates with candidates

Stop the revision loop. Re-establish independent golden provenance or admit that the project has no stable oracle yet.

### Candidate-only warning is noisy

Keep it advisory, narrow the smell with more grounded structure, and add a false-positive fixture before promotion to blocking.

### Required source is present but unit evidence is invented

The lineage check compares unit references to declared candidate sources. Add an optional offline `source_registry` to verify local paths, repository symbols, or saved external receipts if stronger grounding is required. The resolver never fetches URLs during deterministic evaluation.

### Project profile says an error is acceptable

Reject the profile. Fix the candidate or create a new versioned core contract after explicit review; do not hide the old failure.

### Remote model call makes CI flaky

Separate generation from evaluation. Archive model output as a fixture and keep CI deterministic.

## 9. Progressive adoption and integration

Start with schema and lineage on one known-good candidate. Add a manually reviewed golden for only the highest-value semantic dimensions. Next, preserve three or more isolated trials and classify repeated drift. Add candidate-only warnings for high-confidence risks, then register negative fixtures that prove each detector remains active. Finally, introduce a project profile for local blocking policy.

Integrate the evaluator before downstream ingestion or promotion. Store raw response, parsed candidate, generation metadata, report, and policy version together. Make acceptance consume `core_status` plus the explicit project status; never consume only a friendly display label.

For existing pipelines, begin in report-only mode, measure false results, and promote individual warnings deliberately. Contract or golden changes should increment a version and cause baseline re-evaluation.

## 10. Provenance and evidence ceiling

This public package is generalized from bounded experience evaluating structured model artifacts and includes self-contained examples. The repository tests establish that the included evaluator detects the represented schema, lineage, golden, candidate-risk, negative, and profile-masking cases. They do not establish factual truth, exhaustiveness, universal portability, live-provider behavior, or production readiness.

A golden carries only the authority explicitly granted by the adopting project. A report is evaluation evidence, not approval. Downstream promotion remains a separate governance decision.

## 11. Maintenance, extension, and compatibility

Keep finding codes stable within a major version. Add fields compatibly and version report or contract shapes when consumers must change. Preserve the four core statuses even when projects use custom labels.

### Typed dimensions and semantic contracts

Golden dimensions may remain dotted strings for backward-compatible exact comparison, or use an object such as `{"path": "decision_ids", "comparator": "set"}`. Supported comparators are `exact`, `set`, `multiset`, `partition`, `mapping`, `coverage`, `graph`, `bounded_numeric`, and `absence`. Malformed or unobservable dimensions return an inconclusive finding rather than a false semantic failure.

An optional `semantic_contract` can define `roles`, path-based `invariants`, `forbidden_collapses`, and a project `claim_ceiling`. It supplies project meaning; the portable evaluator does not invent ontology or authority. Candidate-only detectors are opt-in through `candidate_risk_checks` and remain advisory warnings unless a project profile explicitly tightens them.

An optional `source_registry` maps source IDs to local files/directories, repository symbols, or external URL entries with saved receipts. Local content can be checked for existence and SHA-256. External receipts can be checked for URL identity, digest, and configured freshness without network access.

When adding a schema feature, extend the minimal validator and add positive and negative tests. For full JSON Schema support, replace the bundled minimal checker with a maintained validator library and document the dependency rather than pretending this standard-library subset is complete.

When adding a golden dimension, add a mismatch fixture. When adding a candidate-only heuristic, add both a detected negative and a plausible non-finding case. When changing blocking posture, document the policy delta and rerun all fixtures. Preserve failed attempts and prior reports when the change is part of a real evaluation record.

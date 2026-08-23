# Changelog

## 1.1.0 - 2026-08-22

- 增加 typed Golden comparators：exact、set、multiset、partition、mapping、coverage、graph、bounded_numeric、absence。
- 增加可选 `semantic_contract`，支持 roles、path-based invariants 和 forbidden collapses。
- 增加 opt-in candidate-only detectors：authority、claim ceiling、phantom evidence、partition、stale input、external dependency、self-golden 和 source identity collision。
- 增加 offline-first `source_registry`：本地路径、仓库符号、SHA-256、URL saved receipt 与 freshness 检查；确定性评估不联网。
- 保持 v1.0 contract、exact dimensions、四类核心状态和旧 fixture 输出兼容。

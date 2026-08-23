"""Portable, typed comparisons for Golden-assisted artifact evaluation.

The module deliberately contains no project-specific ontology.  Callers select a
comparator in their contract and receive a structured, auditable result.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
from typing import Any, Callable, Optional

COMPARATOR_VERSION = "1.0"


@dataclass(frozen=True)
class Comparison:
    """Result returned by :func:`compare_dimension`."""

    kind: str
    matched: Optional[bool]
    category: str
    golden: Any
    candidate: Any
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "comparator": self.kind,
            "comparator_version": COMPARATOR_VERSION,
            "matched": self.matched,
            "category": self.category,
            "golden": self.golden,
            "candidate": self.candidate,
            "details": self.details,
        }


def _result(kind: str, matched: bool | None, category: str, golden: Any, candidate: Any, **details: Any) -> Comparison:
    return Comparison(kind, matched, category, golden, candidate, details)


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def _items(value: Any) -> list[Any] | None:
    return value if isinstance(value, list) else None


def _partition(value: Any, anchor_key: str) -> dict[str, list[Any]] | None:
    items = _items(value)
    if items is None:
        return None
    result: dict[str, list[Any]] = {}
    for item in items:
        if not isinstance(item, dict) or anchor_key not in item:
            return None
        result.setdefault(str(item[anchor_key]), []).append(item)
    return {key: sorted((_canonical(item) for item in items), key=repr) for key, items in sorted(result.items())}


def _graph(value: Any, source_key: str = "source", target_key: str = "target") -> tuple[set[str], set[tuple[str, str, str]]] | None:
    if not isinstance(value, dict):
        return None
    nodes = value.get("nodes")
    edges = value.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return None
    node_ids = {str(node.get("id")) for node in nodes if isinstance(node, dict) and "id" in node}
    if len(node_ids) != len(nodes):
        return None
    edge_set: set[tuple[str, str, str]] = set()
    for edge in edges:
        if not isinstance(edge, dict) or not {source_key, target_key}.issubset(edge):
            return None
        edge_set.add((str(edge[source_key]), str(edge[target_key]), str(edge.get("role", ""))))
    return node_ids, edge_set


def compare_dimension(
    kind: str,
    golden: Any,
    candidate: Any,
    *,
    options: dict[str, Any] | None = None,
    custom: Optional[Callable[[Any, Any], Optional[bool]]] = None,
) -> Comparison:
    """Compare one typed dimension.

    ``matched=None`` means the dimension is not observable/configured rather
    than falsely treating malformed input as a semantic mismatch.
    """

    options = options or {}
    if kind == "exact":
        matched = _canonical(golden) == _canonical(candidate)
    elif kind == "set":
        left, right = _items(golden), _items(candidate)
        if left is None or right is None:
            return _result(kind, None, "inconclusive", golden, candidate, reason="both values must be arrays")
        matched = {_freeze(item) for item in left} == {_freeze(item) for item in right}
    elif kind == "multiset":
        left, right = _items(golden), _items(candidate)
        if left is None or right is None:
            return _result(kind, None, "inconclusive", golden, candidate, reason="both values must be arrays")
        matched = Counter(_freeze(item) for item in left) == Counter(_freeze(item) for item in right)
    elif kind == "partition":
        anchor_key = str(options.get("anchor_key", "anchor"))
        left, right = _partition(golden, anchor_key), _partition(candidate, anchor_key)
        if left is None or right is None:
            return _result(kind, None, "inconclusive", golden, candidate, anchor_key=anchor_key)
        matched = left == right
    elif kind == "mapping":
        key = str(options.get("key", "id"))
        value_key = str(options.get("value", "role"))
        if not isinstance(golden, list) or not isinstance(candidate, list):
            return _result(kind, None, "inconclusive", golden, candidate, reason="both values must be arrays")
        try:
            left_items = [item for item in golden if isinstance(item, dict)]
            right_items = [item for item in candidate if isinstance(item, dict)]
            if len({str(item.get(key)) for item in left_items}) != len(left_items) or len({str(item.get(key)) for item in right_items}) != len(right_items):
                return _result(kind, None, "inconclusive", golden, candidate, reason="mapping keys must be unique")
            left = {str(item[key]): item[value_key] for item in left_items}
            right = {str(item[key]): item[value_key] for item in right_items}
        except KeyError:
            return _result(kind, None, "inconclusive", golden, candidate, key=key, value=value_key)
        matched = _canonical(left) == _canonical(right)
    elif kind == "coverage":
        required = golden if isinstance(golden, list) else options.get("required", [])
        observed = candidate if isinstance(candidate, list) else []
        if not isinstance(required, list) or not isinstance(observed, list):
            return _result(kind, None, "inconclusive", golden, candidate, reason="coverage values must be arrays")
        missing = sorted({_freeze(item) for item in required} - {_freeze(item) for item in observed}, key=repr)
        matched = not missing
        return _result(kind, matched, "match" if matched else "coverage_gap", golden, candidate, missing=missing)
    elif kind == "graph":
        left, right = _graph(golden, str(options.get("source_key", "source")), str(options.get("target_key", "target"))), _graph(candidate, str(options.get("source_key", "source")), str(options.get("target_key", "target")))
        if left is None or right is None:
            return _result(kind, None, "inconclusive", golden, candidate, reason="graph requires nodes and edges")
        matched = left == right
    elif kind == "bounded_numeric":
        if not isinstance(golden, (int, float)) or isinstance(golden, bool) or not isinstance(candidate, (int, float)) or isinstance(candidate, bool):
            return _result(kind, None, "inconclusive", golden, candidate, reason="both values must be numeric")
        tolerance = float(options.get("tolerance", 0))
        matched = math.isclose(float(golden), float(candidate), abs_tol=tolerance, rel_tol=float(options.get("relative_tolerance", 0)))
    elif kind == "absence":
        forbidden = options.get("forbidden", golden if isinstance(golden, list) else [golden])
        present = [item for item in forbidden if _contains_path(candidate, item)]
        matched = not present
        return _result(kind, matched, "match" if matched else "forbidden_present", golden, candidate, present=present)
    elif kind == "custom" and custom is not None:
        matched = custom(golden, candidate)
    else:
        return _result(kind, None, "not_configured", golden, candidate, reason=f"unknown comparator: {kind}")
    return _result(kind, matched, "match" if matched else "mismatch", golden, candidate)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((str(key), _freeze(value[key])) for key in sorted(value, key=str))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _contains_path(value: Any, dotted_path: Any) -> bool:
    if not isinstance(dotted_path, str):
        return dotted_path in value if isinstance(value, list) else value == dotted_path
    current = value
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True

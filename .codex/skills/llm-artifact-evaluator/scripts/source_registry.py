"""Offline-first source registry and lineage resolver.

The resolver verifies declarations against saved local files or externally
fetched receipts. It never performs network access; a separate caller may
populate a receipt before invoking it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_registry(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("source registry must be an object")
    return value


def resolve_source(source_id: str, registry: dict[str, Any], *, base_dir: Optional[Path] = None) -> dict[str, Any]:
    entries = registry.get("sources", registry)
    entry = entries.get(source_id) if isinstance(entries, dict) else None
    if not isinstance(entry, dict):
        return {"source_id": source_id, "status": "unresolved", "reason": "source is not registered"}
    kind = entry.get("kind", entry.get("type", ""))
    result = {"source_id": source_id, "kind": kind, "status": "resolved", "authority": entry.get("authority")}
    if kind in {"local_file", "local_dir", "repo_symbol"}:
        raw_path = entry.get("path") or entry.get("file")
        if not raw_path:
            return {**result, "status": "inconclusive", "reason": "source path is missing"}
        path = Path(raw_path)
        if not path.is_absolute() and base_dir is not None:
            path = (base_dir / path).resolve()
        else:
            path = path.resolve()
        root = Path(entry["root"]).resolve() if entry.get("root") else None
        if root and path != root and root not in path.parents:
            return {**result, "status": "error", "reason": "source path escapes configured root", "path": str(path)}
        if not path.exists():
            return {**result, "status": "unresolved", "reason": "source path does not exist", "path": str(path)}
        if kind == "local_file" or path.is_file():
            result.update({"path": str(path), "content_verified": True, "digest": _sha256(path)})
        else:
            result.update({"path": str(path), "content_verified": True})
        expected = entry.get("sha256") or entry.get("digest")
        if expected and result.get("digest") != expected:
            result.update({"status": "error", "content_verified": False, "reason": "source digest mismatch", "expected_digest": expected})
        return result
    if kind in {"url", "external_url"}:
        parsed = urlparse(str(entry.get("url", "")))
        receipt = entry.get("receipt")
        if parsed.scheme not in {"http", "https"}:
            return {**result, "status": "error", "reason": "URL must use http or https"}
        if not isinstance(receipt, dict):
            return {**result, "status": "unresolved", "reason": "saved external receipt is missing", "url": entry.get("url")}
        if receipt.get("url") and receipt.get("url") != entry.get("url"):
            return {**result, "status": "error", "reason": "receipt URL does not match registry URL"}
        digest = receipt.get("sha256") or receipt.get("digest")
        result.update({"url": entry.get("url"), "receipt": receipt, "content_verified": bool(digest), "digest": digest})
        expected = entry.get("sha256") or entry.get("digest")
        if expected and digest != expected:
            result.update({"status": "error", "content_verified": False, "reason": "receipt digest mismatch", "expected_digest": expected})
        max_age = entry.get("max_age_days")
        retrieved = receipt.get("retrieved_at")
        if max_age is not None and retrieved:
            try:
                timestamp = datetime.fromisoformat(str(retrieved).replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds() / 86400
                result["freshness_verified"] = age_days <= float(max_age)
                if not result["freshness_verified"]:
                    result.update({"status": "error", "reason": "saved receipt is stale", "age_days": age_days})
            except (TypeError, ValueError):
                result.update({"status": "inconclusive", "reason": "receipt retrieved_at is invalid", "freshness_verified": False})
        return result
    return {**result, "status": "inconclusive", "reason": "unsupported source kind"}


def resolve_registry(source_ids: list[str], registry: dict[str, Any], *, base_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    return [resolve_source(source_id, registry, base_dir=base_dir) for source_id in source_ids]

#!/usr/bin/env python3
"""Repository entrypoint for the skill-bundled evaluator."""

from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    bundled = (
        Path(__file__).resolve().parents[1]
        / ".codex"
        / "skills"
        / "llm-artifact-evaluator"
        / "scripts"
        / "evaluate_artifacts.py"
    )
    runpy.run_path(str(bundled), run_name="__main__")

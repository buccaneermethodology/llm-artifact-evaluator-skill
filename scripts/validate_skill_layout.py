#!/usr/bin/env python3
"""Offline compatibility check for the public skill layout."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_dir", type=Path)
    args = parser.parse_args()
    skill_dir = args.skill_dir
    required = [skill_dir / "SKILL.md", skill_dir / "agents/openai.yaml", skill_dir / "references/REFERENCE.md"]
    missing = [str(path) for path in required if not path.is_file()]
    skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8") if not missing else ""
    metadata_ok = skill_text.startswith("---\nname: llm-artifact-evaluator\ndescription:") and "\n---\n" in skill_text[4:]
    if missing or not metadata_ok:
        if missing:
            print("Missing required files:", ", ".join(missing))
        if not metadata_ok:
            print("SKILL.md frontmatter is invalid")
        return 1
    print("Skill layout compatibility check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

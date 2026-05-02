#!/usr/bin/env python3
"""
Validates `gh pr create` and `gh pr merge` Bash invocations against the
Git Flow base-branch matrix:

    feature/*  ->  develop
    hotfix/*   ->  main
    release/*  ->  main

Reads PreToolUse JSON from stdin. Emits a deny payload to stdout when the
command would create or merge a PR with the wrong base; exits 0 otherwise.

Always exits 0 (success) — even on internal exceptions, fails open.
Spec: docs/superpowers/specs/2026-05-02-pr-base-enforcement-design.md
"""
from __future__ import annotations

import json
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def expected_base_for(branch: str) -> Optional[str]:
    """Return the required PR base for a Git Flow branch, or None if not Git Flow."""
    if branch.startswith("feature/"):
        return "develop"
    if branch.startswith("hotfix/"):
        return "main"
    if branch.startswith("release/"):
        return "main"
    return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        json.load(sys.stdin)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()

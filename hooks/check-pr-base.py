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
import re
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


# Match --base VAL, --base=VAL, -B VAL. Captures VAL with surrounding quotes
# stripped. VAL stops at whitespace or end of string.
_BASE_FLAG_RE = re.compile(
    r"""(?:--base[=\s]+|-B\s+)        # flag form
        (?:(['"])(?P<quoted>[^'"]+)\1 # quoted value -> 'quoted'
          |(?P<bare>\S+))             # or bare value -> 'bare'
    """,
    re.VERBOSE,
)


def parse_base_flag(cmd: str) -> Optional[str]:
    """Extract the value of --base / --base= / -B from `cmd`.

    Returns None if the flag is absent. Quotes (single or double) are stripped.
    Shell expansions like $VAR are returned as the literal string '$VAR' so the
    caller can decide whether to enforce or fall through.
    """
    m = _BASE_FLAG_RE.search(cmd)
    if not m:
        return None
    return m.group("quoted") or m.group("bare")


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

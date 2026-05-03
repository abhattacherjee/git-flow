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
import subprocess
import sys
from dataclasses import dataclass
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


# Match "gh pr merge" followed by a PR number or GitHub PR URL. Skips flags.
_PR_NUM_RE = re.compile(r"/pull/(\d+)|(?<!\S)(\d+)(?!\S)")


def parse_pr_number(cmd: str) -> Optional[str]:
    """Extract the PR number from a `gh pr merge` command.

    Recognizes bare integers (`gh pr merge 42`) and PR URLs (`.../pull/42`).
    Returns None if no number is present (gh would resolve from current branch).
    """
    # Strip the "gh pr merge" prefix and look at the rest.
    idx = cmd.find("gh pr merge")
    if idx < 0:
        return None
    tail = cmd[idx + len("gh pr merge"):]
    # Filter out tokens that start with `-` (flags) by matching only standalone
    # numeric tokens or /pull/<num>.
    for m in _PR_NUM_RE.finditer(tail):
        return m.group(1) or m.group(2)
    return None


# Split on shell connectors. Naive (does not respect quoted strings) — that's
# acceptable here because the goal is best-effort layered defense, and the
# subcommand handlers re-validate each segment anyway.
_CHAIN_RE = re.compile(r"\s*(?:&&|\|\||;)\s*")


def split_command_chain(cmd: str) -> list[str]:
    """Split a command string on shell connectors (&&, ||, ;)."""
    return [part for part in _CHAIN_RE.split(cmd) if part]


# ---------------------------------------------------------------------------
# Shell I/O wrappers — tests can patch these or use temp_git_repo / gh_stub.
# All wrappers fail open: any non-zero exit or exception returns None / False.
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 5.0) -> Optional[str]:
    """Run a command; return stdout stripped, or None on any failure."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def current_branch() -> Optional[str]:
    """Return the current branch name, or None on detached HEAD / git failure."""
    return _run(["git", "symbolic-ref", "--short", "HEAD"])


def has_develop_branch() -> bool:
    """Return True if the local repo has a 'develop' branch."""
    return _run(["git", "rev-parse", "--verify", "--quiet", "develop"]) is not None


def pr_refs_for(pr_num: str) -> Optional[tuple[str, str]]:
    """Return (baseRefName, headRefName) for a PR number, or None on failure."""
    raw = _run(
        ["gh", "pr", "view", pr_num, "--json", "baseRefName,headRefName"],
        timeout=10.0,
    )
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return (data["baseRefName"], data["headRefName"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def pr_for_branch(branch: str) -> Optional[str]:
    """Resolve the open PR number for a branch name, or None if not found."""
    raw = _run(
        ["gh", "pr", "list", "--head", branch, "--json", "number"],
        timeout=10.0,
    )
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if not data:
            return None
        return str(data[0]["number"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Decision + diagnostic templates
# ---------------------------------------------------------------------------

@dataclass
class Decision:
    allow: bool
    reason: str = ""


def diag_wrong_base_pr(*, pr_num: str, actual: str, expected: str, branch_type: str) -> str:
    return (
        f'✗ ABORTING: PR #{pr_num} has base "{actual}", expected "{expected}".\n'
        f"{branch_type} branches must merge to {expected}, not {actual}.\n\n"
        f"To fix:\n"
        f"  gh pr edit {pr_num} --base {expected}\n\n"
        f"Then re-run."
    )


def diag_wrong_base_create(*, actual: str, expected: str, branch_type: str, rest_of_args: str) -> str:
    return (
        f'✗ BLOCKED: cannot create {branch_type} PR with --base "{actual}".\n'
        f"{branch_type} branches must merge to {expected} per Git Flow.\n\n"
        f"To fix, re-run with:\n"
        f"  gh pr create --base {expected} {rest_of_args}".rstrip()
    )


def diag_missing_base_create(*, expected: str, branch_type: str, rest_of_args: str) -> str:
    return (
        f"✗ BLOCKED: gh pr create on {branch_type} branch requires explicit --base {expected}.\n\n"
        f"The repo default branch is typically main, which would silently create a wrong-base PR.\n"
        f"Always pass --base explicitly per Git Flow.\n\n"
        f"To fix, re-run with:\n"
        f"  gh pr create --base {expected} {rest_of_args}".rstrip()
    )


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _branch_type_label(branch: str) -> str:
    """e.g. 'feature/foo' -> 'feature/*'. Used in diagnostic messages."""
    if "/" in branch:
        return branch.split("/", 1)[0] + "/*"
    return branch


def _strip_create_args_for_remediation(cmd: str) -> str:
    """Strip 'gh pr create' and any --base/-B flag from cmd, leaving the rest."""
    s = cmd
    idx = s.find("gh pr create")
    if idx >= 0:
        s = s[idx + len("gh pr create"):].strip()
    s = _BASE_FLAG_RE.sub("", s).strip()
    return s


def check_create(cmd: str) -> Decision:
    """Validate a single `gh pr create ...` command segment."""
    branch = current_branch()
    if branch is None:
        return Decision(allow=True)  # detached HEAD — pass through

    expected = expected_base_for(branch)
    if expected is None:
        return Decision(allow=True)  # not Git Flow — pass through

    # Single-trunk repo without 'develop' is not a Git Flow repo even if the
    # branch happens to start with 'feature/'. Pass-through.
    if expected == "develop" and not has_develop_branch():
        return Decision(allow=True)

    actual = parse_base_flag(cmd)
    rest = _strip_create_args_for_remediation(cmd)
    branch_type = _branch_type_label(branch)

    if actual is None:
        return Decision(
            allow=False,
            reason=diag_missing_base_create(
                expected=expected, branch_type=branch_type, rest_of_args=rest
            ),
        )
    if actual.startswith("$"):
        # Shell expansion — can't evaluate. Allow + warn.
        print(
            f"⚠️  check-pr-base: --base value is shell expansion ({actual}); "
            f"skipping enforcement. Verify the resolved base is '{expected}'.",
            file=sys.stderr,
        )
        return Decision(allow=True)
    if actual != expected:
        return Decision(
            allow=False,
            reason=diag_wrong_base_create(
                actual=actual, expected=expected, branch_type=branch_type, rest_of_args=rest
            ),
        )
    return Decision(allow=True)


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

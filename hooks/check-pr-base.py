#!/usr/bin/env python3
"""
Validates `gh pr create` and `gh pr merge` Bash invocations against the
Git Flow base-branch matrix:

    feature/*  ->  develop
    hotfix/*   ->  main
    release/*  ->  main

Reads PreToolUse JSON from stdin. Emits a deny payload to stdout when the
command would create or merge a PR with the wrong base; exits 0 otherwise.

Exit code is always 0 — the deny decision is conveyed via JSON on stdout
(PreToolUse's `permissionDecision` contract), not via exit code. Internal
exceptions fall through to a no-op (fail open) so a hook bug never blocks
legitimate work.
Spec: docs/superpowers/specs/2026-05-02-pr-base-enforcement-design.md
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def expected_bases_for(branch: str) -> Optional[frozenset]:
    """Return the set of allowed PR bases for a Git Flow branch, or None if not Git Flow.

    feature/* -> frozenset({"develop"})          (direct merge only)
    hotfix/*  -> frozenset({"main", "develop"})  (release PR + back-merge)
    release/* -> frozenset({"main", "develop"})  (release PR + back-merge)
    else       -> None  (sentinel: not Git Flow — pass through)
    """
    if branch.startswith("feature/"):
        return frozenset({"develop"})
    if branch.startswith("hotfix/"):
        return frozenset({"main", "develop"})
    if branch.startswith("release/"):
        return frozenset({"main", "develop"})
    return None


def canonical_base(bases: frozenset) -> str:
    """Return the primary/canonical base from an allowed-bases set.

    Used for remediation hints so release/* and hotfix/* still suggest
    --base main (the release PR target) rather than develop (the back-merge).
    """
    return "main" if "main" in bases else "develop"


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


# Match a whitespace-isolated integer (e.g. "42") or a /pull/<n> URL fragment.
# Whitespace lookarounds reject digits adjacent to non-whitespace, which
# happens to skip most flag tokens (--commit-id=abc123, etc.). Does NOT
# actively parse flags — see issue tracker for shlex-based hardening.
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


# Extract a leading `cd <path>` from a command chain so we can run git/gh in
# the right directory. Mirrors the pattern in .claude/hooks/prevent-direct-push.py.
# Handles quoted paths and ~/$VAR expansions via os.path.expanduser/expandvars.
_CD_PREFIX_RE = re.compile(
    r"""(?:^|[;&|]\s*)        # at start of command or after a connector
        cd\s+
        (?:'(?P<sq>[^']+)'    # single-quoted path
          |"(?P<dq>[^"]+)"    # double-quoted path
          |(?P<bare>\S+))     # or bare (no whitespace)
    """,
    re.VERBOSE,
)


def extract_cwd(cmd: str) -> Optional[str]:
    """Return the first `cd <path>` target in the command, or None.

    Performs ~ and $VAR expansion. Returns None if no cd prefix found, or if
    the expanded path doesn't exist (caller should fall back to default CWD).
    """
    m = _CD_PREFIX_RE.search(cmd)
    if not m:
        return None
    path = m.group("sq") or m.group("dq") or m.group("bare")
    path = os.path.expanduser(os.path.expandvars(path))
    if not os.path.isdir(path):
        return None
    return path


# ---------------------------------------------------------------------------
# Shell I/O wrappers — tests can patch these or use temp_git_repo / gh_stub.
# All wrappers fail open: any non-zero exit or exception returns None / False.
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 5.0, cwd: Optional[str] = None) -> Optional[str]:
    """Run a command; return stdout stripped, or None on any failure."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False, cwd=cwd
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, UnicodeDecodeError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def current_branch(cwd: Optional[str] = None) -> Optional[str]:
    """Return the current branch name, or None on detached HEAD / git failure."""
    return _run(["git", "symbolic-ref", "--short", "HEAD"], cwd=cwd)


def has_develop_branch(cwd: Optional[str] = None) -> bool:
    """Return True if the local repo has a 'develop' branch."""
    return _run(["git", "rev-parse", "--verify", "--quiet", "develop"], cwd=cwd) is not None


def pr_refs_for(pr_num: str, cwd: Optional[str] = None) -> Optional[tuple[str, str]]:
    """Return (baseRefName, headRefName) for a PR number, or None on failure."""
    raw = _run(
        ["gh", "pr", "view", pr_num, "--json", "baseRefName,headRefName"],
        timeout=10.0,
        cwd=cwd,
    )
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return (data["baseRefName"], data["headRefName"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def pr_for_branch(branch: str, cwd: Optional[str] = None) -> Optional[str]:
    """Resolve the open PR number for a branch name, or None if not found."""
    raw = _run(
        ["gh", "pr", "list", "--head", branch, "--json", "number"],
        timeout=10.0,
        cwd=cwd,
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


def check_create(cmd: str, cwd: Optional[str] = None) -> Decision:
    """Validate a single `gh pr create ...` command segment."""
    branch = current_branch(cwd=cwd)
    if branch is None:
        return Decision(allow=True)  # detached HEAD — pass through

    bases = expected_bases_for(branch)
    if bases is None:
        return Decision(allow=True)  # not Git Flow — pass through

    # Single-trunk repo without 'develop' is not a Git Flow repo even if the
    # branch happens to start with 'feature/'. Pass-through.
    if bases == frozenset({"develop"}) and not has_develop_branch(cwd=cwd):
        return Decision(allow=True)

    actual = parse_base_flag(cmd)
    rest = _strip_create_args_for_remediation(cmd)
    branch_type = _branch_type_label(branch)
    hint_base = canonical_base(bases)

    if actual is None:
        return Decision(
            allow=False,
            reason=diag_missing_base_create(
                expected=hint_base, branch_type=branch_type, rest_of_args=rest
            ),
        )
    if actual.startswith("$"):
        # Shell expansion — can't evaluate. Allow + warn.
        print(
            f"⚠️  check-pr-base: --base value is shell expansion ({actual}); "
            f"skipping enforcement. Verify the resolved base is one of {sorted(bases)}.",
            file=sys.stderr,
        )
        return Decision(allow=True)
    if actual not in bases:
        return Decision(
            allow=False,
            reason=diag_wrong_base_create(
                actual=actual, expected=hint_base, branch_type=branch_type, rest_of_args=rest
            ),
        )
    return Decision(allow=True)


def check_merge(cmd: str, cwd: Optional[str] = None) -> Decision:
    """Validate a single `gh pr merge ...` command segment."""
    pr_num = parse_pr_number(cmd)
    if pr_num is None:
        # gh resolves from current branch when the number is omitted.
        branch = current_branch(cwd=cwd)
        if branch is None:
            return Decision(allow=True)
        pr_num = pr_for_branch(branch, cwd=cwd)
        if pr_num is None:
            return Decision(allow=True)  # gh would fail naturally

    refs = pr_refs_for(pr_num, cwd=cwd)
    if refs is None:
        return Decision(allow=True)  # gh failure — fail open
    actual_base, head = refs

    bases = expected_bases_for(head)
    if bases is None:
        return Decision(allow=True)  # PR is not from a Git Flow branch

    if actual_base not in bases:
        return Decision(
            allow=False,
            reason=diag_wrong_base_pr(
                pr_num=pr_num,
                actual=actual_base,
                expected=canonical_base(bases),
                branch_type=_branch_type_label(head),
            ),
        )
    return Decision(allow=True)


# Anchor: 'gh pr create'/'gh pr merge' must be at segment start or preceded by
# a shell separator/whitespace. These legacy regexes are NOT quote-aware and are
# only used as a fallback inside _invokes_gh_pr when shlex raises ValueError
# (unbalanced quotes, heredoc bodies that shlex cannot parse).
_GH_PR_CREATE_RE = re.compile(r"(?:^|[\s;&|])gh\s+pr\s+create\b")
_GH_PR_MERGE_RE = re.compile(r"(?:^|[\s;&|])gh\s+pr\s+merge\b")


def _invokes_gh_pr(segment: str, subcommand: str) -> bool:
    """Return True iff *segment* invokes `gh pr <subcommand>` as a real command.

    Uses shlex with punctuation_chars=True so that shell operators (|, &&, ;,
    etc.) become their own tokens, while quoted strings remain single tokens
    (quotes stripped in posix mode).  This means `git commit -m "gh pr create"`
    tokenizes the message as ONE token, so the triple ["gh","pr","create"] never
    forms as three separate adjacent tokens, and the false positive is eliminated.

    Detection: return True iff the triple ["gh", "pr", subcommand] appears as
    three CONSECUTIVE tokens anywhere in the token list.  This correctly handles:
      - Bare invocations: gh pr create ...
      - Env-assignment prefixes: VAR=val gh pr create ...
      - Keyword/utility prefixes: sudo, time, env, nice, command, then, etc.
      - Operator-glued prefixes: foo|gh pr create (punctuation_chars splits |)
    and correctly REJECTS:
      - Quoted spans: git commit -m "gh pr create" (quoted string = 1 token)
      - Quoted body args: --body "... gh pr create ..." (same reason)

    On ValueError (unbalanced quotes / heredoc that shlex cannot parse), falls
    back to the pre-fix legacy regex on " " + segment, preserving the original
    detection power exactly.  This fallback is ONLY used on shlex parse failure —
    NOT when shlex succeeds but finds no triple, to avoid re-introducing the #18
    quoted-mention false positive.
    """
    _LEGACY_RE = _GH_PR_CREATE_RE if subcommand == "create" else _GH_PR_MERGE_RE
    try:
        lex = shlex.shlex(segment, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        # Unbalanced quotes / heredoc body — fall back to legacy regex.
        return bool(_LEGACY_RE.search(" " + segment))

    triple = ["gh", "pr", subcommand]
    # Search for the triple as three consecutive tokens anywhere in the list.
    for k in range(len(tokens) - 2):
        if tokens[k:k + 3] == triple:
            return True
    return False


def dispatch(cmd: str) -> Decision:
    """Validate every gh pr create/merge segment in a command chain."""
    cwd = extract_cwd(cmd)
    for segment in split_command_chain(cmd):
        if _invokes_gh_pr(segment, "create"):
            d = check_create(segment, cwd=cwd)
            if not d.allow:
                return d
        elif _invokes_gh_pr(segment, "merge"):
            d = check_merge(segment, cwd=cwd)
            if not d.allow:
                return d
    return Decision(allow=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    try:
        payload = json.load(sys.stdin)
        cmd = payload.get("tool_input", {}).get("command", "")
        decision = dispatch(cmd)
    except Exception:
        # Hook bug or malformed payload — fail open with traceback to stderr.
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(0)

    if decision.allow:
        sys.exit(0)

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": decision.reason,
                }
            }
        )
    )
    sys.exit(0)


if __name__ == "__main__":
    main()

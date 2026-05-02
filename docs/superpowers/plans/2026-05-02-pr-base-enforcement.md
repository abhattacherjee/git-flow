# PR Base-Branch Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Block wrong-base `gh pr create` and `gh pr merge` invocations on Git Flow branches via a plugin-bundled PreToolUse hook plus an in-script check in `scripts/git-flow-finish.sh`.

**Architecture:** Two enforcement layers, defense in depth. Layer 1: `hooks/check-pr-base.py` — a plugin-bundled PreToolUse Bash hook that validates `gh pr create`/`gh pr merge` invocations. Layer 2: `verify_pr_base` bash function added to `git-flow-finish.sh`, called before any `gh pr merge` invocation. Validation matrix: `feature/*`→develop, `hotfix/*`→main, `release/*`→main. Pass-through silently for non-Git-Flow branches and on any `gh`/`git` failure (fail open).

**Tech Stack:** Python 3 (stdlib only — `json`, `re`, `subprocess`, `dataclasses`); pytest 9 (already on system, no project dep); bash 4+ for the script function and tests; Claude Code plugin hook manifest format.

**Spec:** `docs/superpowers/specs/2026-05-02-pr-base-enforcement-design.md`
**Issue:** [git-flow#1](https://github.com/abhattacherjee/git-flow/issues/1)
**Branch:** `feature/issue-1-pr-base-enforcement` (already created)

**Conventions for every commit in this plan:**
- Stage specific files (never `git add -A`).
- Run `./scripts/commit-preflight.sh` before every `git commit` (the `require-preflight` hook enforces this — without preflight the commit is blocked).
- Commit message footer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`
- Use HEREDOC for multi-line commit messages.

---

## File Structure

**New files:**
```
hooks/
├── hooks.json                      # plugin hook manifest
└── check-pr-base.py                # the hook script (~200 lines)

tests/
├── __init__.py                     # makes tests/ a package
├── conftest.py                     # pytest fixtures
├── test_helpers.py                 # pure-function unit tests
├── test_hook_e2e.py                # end-to-end subprocess tests (17 cases)
├── test_verify_pr_base_sh.py       # bash function tests
├── run.sh                          # pytest entrypoint
└── fixtures/
    └── bin/
        └── gh                      # gh stub for tests
```

**Modified files:**
```
scripts/git-flow-finish.sh          # +verify_pr_base() function, +sourcing guard, +1 call site
commands/finish.md                  # document the safety check
README.md                           # "Quality gates" section (or new equivalent)
CHANGELOG.md                        # Unreleased entry
.claude-plugin/plugin.json          # version bump (via bump-version.sh)
.claude-plugin/marketplace.json     # lockstep version bump
```

**Responsibility map:**
- `hooks/check-pr-base.py` — single hook script. Top-level dispatcher + pure helpers + shell I/O wrappers + two subcommand handlers (`check_create`, `check_merge`) + `main()`.
- `tests/conftest.py` — fixtures: `temp_git_repo`, `gh_stub`, `run_hook(payload, cwd, env)`.
- `tests/test_helpers.py` — direct imports, pure-function tests (no I/O).
- `tests/test_hook_e2e.py` — 17 subprocess-driven scenarios.
- `tests/test_verify_pr_base_sh.py` — sources `git-flow-finish.sh` in subshell.
- `scripts/git-flow-finish.sh` — adds `verify_pr_base` near top, sourcing guard at bottom, call site update at the existing `merge_main_via_pr` block.

---

## Task 1: Plugin hook skeleton wired to a no-op script

Establish the plumbing first so we have something to test against.

**Files:**
- Create: `hooks/hooks.json`
- Create: `hooks/check-pr-base.py`

- [ ] **Step 1: Create the plugin hook manifest**

Create `hooks/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/check-pr-base.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: Create the no-op hook script**

Create `hooks/check-pr-base.py`:

```python
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


def main() -> None:
    try:
        json.load(sys.stdin)  # placeholder: parse only
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Make the script executable and verify it parses**

Run:
```bash
chmod +x hooks/check-pr-base.py
python3 -c "import ast; ast.parse(open('hooks/check-pr-base.py').read())" && echo "OK"
echo '{"tool_name":"Bash","tool_input":{"command":"echo hi"}}' | python3 hooks/check-pr-base.py; echo "exit=$?"
```
Expected:
```
OK
exit=0
```

- [ ] **Step 4: Verify the hook manifest is valid JSON**

Run:
```bash
python3 -c "import json; print(json.dumps(json.load(open('hooks/hooks.json')), indent=2))"
```
Expected: pretty-printed JSON identical in structure to what was written.

- [ ] **Step 5: Commit**

```bash
git add hooks/hooks.json hooks/check-pr-base.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): scaffold check-pr-base hook with no-op handler

Creates hooks/hooks.json (plugin-level PreToolUse Bash matcher) and
hooks/check-pr-base.py with a no-op main() that always exits 0. Subsequent
tasks fill in the validation logic against the Git Flow base-branch matrix.

Refs #1
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Test scaffolding — fixtures, gh stub, and runner

Build the harness once so every helper and handler test piggy-backs on the same fixtures.

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/fixtures/bin/gh`
- Create: `tests/run.sh`

- [ ] **Step 1: Create empty `tests/__init__.py`**

```bash
mkdir -p tests/fixtures/bin
: > tests/__init__.py
```

- [ ] **Step 2: Write the gh stub**

Create `tests/fixtures/bin/gh`:

```bash
#!/usr/bin/env bash
# Test stub for `gh`. Reads MOCK_GH_STDOUT and MOCK_GH_EXIT from env.
# Default: prints nothing, exits 0.

if [[ -n "${MOCK_GH_STDOUT:-}" ]]; then
  printf '%s' "$MOCK_GH_STDOUT"
fi
exit "${MOCK_GH_EXIT:-0}"
```

Make it executable:
```bash
chmod +x tests/fixtures/bin/gh
```

- [ ] **Step 3: Verify the stub works**

Run:
```bash
MOCK_GH_STDOUT='{"baseRefName":"main"}' tests/fixtures/bin/gh; echo "exit=$?"
MOCK_GH_EXIT=2 tests/fixtures/bin/gh; echo "exit=$?"
```
Expected:
```
{"baseRefName":"main"}exit=0
exit=2
```

- [ ] **Step 4: Write `tests/conftest.py` with fixtures**

Create `tests/conftest.py`:

```python
"""Shared fixtures for hook tests.

Three fixtures used across every test file:
    temp_git_repo  - isolated git repo with requested branches
    gh_stub        - prepends tests/fixtures/bin to PATH; per-call mocks via env
    run_hook       - invokes hooks/check-pr-base.py with stdin payload
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "hooks" / "check-pr-base.py"
GH_STUB_DIR = REPO_ROOT / "tests" / "fixtures" / "bin"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def temp_git_repo(tmp_path: Path):
    """Initialize a git repo with a customizable branch layout.

    Returns a callable: setup(branches=["main", "develop"], head="main") -> Path

    `branches` is created in order from an initial empty commit. `head` is
    the branch checked out at the end. Pass `branches` containing only "main"
    (no "develop") to simulate a single-trunk repo.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    # Initial empty commit so branches can be created.
    _git(repo, "commit", "--allow-empty", "-m", "init", "-q")

    def setup(branches=None, head="main") -> Path:
        if branches is None:
            branches = ["main", "develop"]
        for b in branches:
            if b == "main":
                continue
            # Create branch off main if it doesn't exist.
            existing = subprocess.run(
                ["git", "branch", "--list", b], cwd=repo, capture_output=True, text=True
            ).stdout.strip()
            if not existing:
                _git(repo, "branch", b)
        _git(repo, "checkout", head, "-q")
        return repo

    return setup


@pytest.fixture
def gh_stub(monkeypatch):
    """Prepend the gh stub dir to PATH so `gh` resolves to the stub.

    Returns a callable: set_response(stdout: str = "", exit_code: int = 0) -> None
    Sets MOCK_GH_STDOUT and MOCK_GH_EXIT on the test's env.
    """
    monkeypatch.setenv("PATH", f"{GH_STUB_DIR}:{os.environ['PATH']}")

    def set_response(stdout: str = "", exit_code: int = 0):
        monkeypatch.setenv("MOCK_GH_STDOUT", stdout)
        monkeypatch.setenv("MOCK_GH_EXIT", str(exit_code))

    set_response()  # default: empty stdout, exit 0
    return set_response


@pytest.fixture
def run_hook():
    """Invoke hooks/check-pr-base.py with a payload on stdin.

    Returns a callable that accepts:
        payload: dict   - the JSON to send on stdin
        cwd:     Path   - working directory for the subprocess
        env:     dict   - extra env vars (merged with os.environ)

    Returns (exit_code: int, stdout: str, stderr: str).
    """
    def run(payload: dict, cwd: Path, env: Optional[dict] = None) -> tuple[int, str, str]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(payload),
            cwd=str(cwd),
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode, proc.stdout, proc.stderr

    return run
```

- [ ] **Step 5: Write the test runner**

Create `tests/run.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
exec python3 -m pytest tests/ -v "$@"
```

Make it executable:
```bash
chmod +x tests/run.sh
```

- [ ] **Step 6: Sanity-check fixtures by writing a smoke test**

Create `tests/test_smoke.py` (deleted at end of this task — temporary):

```python
"""Temporary smoke test — deleted at the end of Task 2."""
from pathlib import Path

def test_temp_git_repo_default_layout(temp_git_repo):
    repo: Path = temp_git_repo()
    assert (repo / ".git").is_dir()


def test_gh_stub_prints_mocked_stdout(gh_stub, tmp_path):
    import subprocess
    gh_stub("hello")
    result = subprocess.run(["gh", "ignored"], capture_output=True, text=True, cwd=tmp_path)
    assert result.stdout == "hello"
    assert result.returncode == 0


def test_run_hook_invokes_script(run_hook, tmp_path):
    code, out, err = run_hook({"tool_name": "Bash", "tool_input": {"command": "echo hi"}}, tmp_path)
    assert code == 0
    assert out == ""
```

- [ ] **Step 7: Run smoke tests**

Run:
```bash
tests/run.sh tests/test_smoke.py
```
Expected: 3 passed.

- [ ] **Step 8: Delete the smoke test**

```bash
rm tests/test_smoke.py
```

- [ ] **Step 9: Commit**

```bash
git add tests/__init__.py tests/conftest.py tests/fixtures/bin/gh tests/run.sh
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
test(hook): add pytest fixtures and gh stub

Three reusable fixtures for hook tests: temp_git_repo (init-on-demand
isolated repo with configurable branch layout), gh_stub (prepends
tests/fixtures/bin to PATH; per-call MOCK_GH_STDOUT/MOCK_GH_EXIT), and
run_hook (subprocess wrapper for hooks/check-pr-base.py with json stdin).
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Pure helper — `expected_base_for(branch)`

**Files:**
- Modify: `hooks/check-pr-base.py`
- Create: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_helpers.py`:

```python
"""Pure-function unit tests for hooks/check-pr-base.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "hooks" / "check-pr-base.py"


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("check_pr_base", HOOK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_pr_base"] = module
    spec.loader.exec_module(module)
    return module


hook = _load_hook_module()


# expected_base_for ----------------------------------------------------------

def test_expected_base_for_feature():
    assert hook.expected_base_for("feature/foo") == "develop"
    assert hook.expected_base_for("feature/sub/path") == "develop"


def test_expected_base_for_hotfix():
    assert hook.expected_base_for("hotfix/v1.0.1") == "main"


def test_expected_base_for_release():
    assert hook.expected_base_for("release/v2.0.0") == "main"


def test_expected_base_for_non_git_flow_returns_none():
    assert hook.expected_base_for("main") is None
    assert hook.expected_base_for("develop") is None
    assert hook.expected_base_for("chore/xyz") is None
    assert hook.expected_base_for("") is None
    assert hook.expected_base_for("feature") is None  # no slash, not feature/*
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: collection error or 4 failures with `AttributeError: module 'check_pr_base' has no attribute 'expected_base_for'`.

- [ ] **Step 3: Implement `expected_base_for`**

Edit `hooks/check-pr-base.py` — replace the file contents with:

```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/check-pr-base.py tests/test_helpers.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): expected_base_for() resolves branch -> required base

Pure helper that returns "develop" for feature/*, "main" for hotfix/* and
release/*, and None for non-Git-Flow branches (main, develop, chore/*, etc.).
First validation primitive for the hook.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Pure helper — `parse_base_flag(cmd)`

Parse `--base`/`--base=`/`-B` flag values from a command string.

**Files:**
- Modify: `hooks/check-pr-base.py`
- Modify: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_helpers.py`:

```python
# parse_base_flag -----------------------------------------------------------

def test_parse_base_flag_long():
    assert hook.parse_base_flag("gh pr create --base develop --title t") == "develop"


def test_parse_base_flag_equals_form():
    assert hook.parse_base_flag("gh pr create --base=develop") == "develop"


def test_parse_base_flag_short():
    assert hook.parse_base_flag("gh pr create -B develop") == "develop"


def test_parse_base_flag_double_quoted():
    assert hook.parse_base_flag('gh pr create --base "develop"') == "develop"


def test_parse_base_flag_single_quoted():
    assert hook.parse_base_flag("gh pr create --base 'develop'") == "develop"


def test_parse_base_flag_missing_returns_none():
    assert hook.parse_base_flag("gh pr create --title t") is None


def test_parse_base_flag_shell_var_returns_literal():
    """Caller decides what to do with a $VAR value (currently: allow + warn)."""
    assert hook.parse_base_flag('gh pr create --base "$BASE"') == "$BASE"
    assert hook.parse_base_flag("gh pr create --base $BASE") == "$BASE"
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
tests/run.sh tests/test_helpers.py::test_parse_base_flag_long
```
Expected: FAIL with `AttributeError: ... 'parse_base_flag'`.

- [ ] **Step 3: Implement `parse_base_flag`**

Add to `hooks/check-pr-base.py` after `expected_base_for`:

```python
import re

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
```

- [ ] **Step 4: Run tests to verify pass**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: 11 passed (4 prior + 7 new).

- [ ] **Step 5: Commit**

```bash
git add hooks/check-pr-base.py tests/test_helpers.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): parse_base_flag() extracts --base value from gh command

Handles --base val, --base=val, -B val, single/double-quoted values, and
returns the literal '\$VAR' for shell expansions so check_create can fall
through (allow + warn) instead of trying to evaluate.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Pure helper — `parse_pr_number(cmd)`

**Files:**
- Modify: `hooks/check-pr-base.py`
- Modify: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_helpers.py`:

```python
# parse_pr_number -----------------------------------------------------------

def test_parse_pr_number_bare():
    assert hook.parse_pr_number("gh pr merge 42") == "42"


def test_parse_pr_number_with_flags_after():
    assert hook.parse_pr_number("gh pr merge 42 --squash --delete-branch") == "42"


def test_parse_pr_number_with_flags_before():
    assert hook.parse_pr_number("gh pr merge --squash 42") == "42"


def test_parse_pr_number_url_form():
    assert hook.parse_pr_number("gh pr merge https://github.com/o/r/pull/7") == "7"


def test_parse_pr_number_missing_returns_none():
    assert hook.parse_pr_number("gh pr merge") is None
    assert hook.parse_pr_number("gh pr merge --squash") is None
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
tests/run.sh tests/test_helpers.py::test_parse_pr_number_bare
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement `parse_pr_number`**

Add to `hooks/check-pr-base.py` after `parse_base_flag`:

```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: 16 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/check-pr-base.py tests/test_helpers.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): parse_pr_number() extracts PR number from gh pr merge

Handles bare integers (gh pr merge 42), flags-before-number forms
(gh pr merge --squash 42), and GitHub PR URLs (.../pull/42). Returns None
if no number — gh would resolve from current branch in that case and the
hook just looks up via pr_for_branch instead.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Pure helper — `split_command_chain(cmd)`

Split on `&&`, `;`, `||` so chained commands are validated independently.

**Files:**
- Modify: `hooks/check-pr-base.py`
- Modify: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_helpers.py`:

```python
# split_command_chain -------------------------------------------------------

def test_split_chain_bare():
    assert hook.split_command_chain("gh pr create --base develop") == ["gh pr create --base develop"]


def test_split_chain_and():
    parts = hook.split_command_chain("gh pr create --base develop && echo done")
    assert [p.strip() for p in parts] == ["gh pr create --base develop", "echo done"]


def test_split_chain_semicolon():
    parts = hook.split_command_chain("a ; b ; c")
    assert [p.strip() for p in parts] == ["a", "b", "c"]


def test_split_chain_or():
    parts = hook.split_command_chain("a || b")
    assert [p.strip() for p in parts] == ["a", "b"]


def test_split_chain_mixed():
    parts = hook.split_command_chain("a && b ; c || d")
    assert [p.strip() for p in parts] == ["a", "b", "c", "d"]
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
tests/run.sh tests/test_helpers.py::test_split_chain_bare
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement `split_command_chain`**

Add to `hooks/check-pr-base.py` after `parse_pr_number`:

```python
# Split on shell connectors. Naive (does not respect quoted strings) — that's
# acceptable here because the goal is best-effort layered defense, and the
# subcommand handlers re-validate each segment anyway.
_CHAIN_RE = re.compile(r"\s*(?:&&|\|\||;)\s*")


def split_command_chain(cmd: str) -> list[str]:
    """Split a command string on shell connectors (&&, ||, ;)."""
    return [part for part in _CHAIN_RE.split(cmd) if part]
```

- [ ] **Step 4: Run tests to verify pass**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: 21 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/check-pr-base.py tests/test_helpers.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): split_command_chain() handles && ; || connectors

So that 'gh pr create --base main && echo done' is validated as two
segments — the first one fails the create check, blocking the whole call.
Naive split (ignores quoted strings); subcommand handlers re-filter.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Shell I/O wrappers — `current_branch` and `has_develop_branch`

These wrap `git` so tests can patch them or use `temp_git_repo`. We test them via the temp git repo so we exercise the real subprocess path.

**Files:**
- Modify: `hooks/check-pr-base.py`
- Modify: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_helpers.py`:

```python
import subprocess


# current_branch / has_develop_branch ---------------------------------------

def test_current_branch_on_feature(temp_git_repo, monkeypatch):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    monkeypatch.chdir(repo)
    assert hook.current_branch() == "feature/foo"


def test_current_branch_detached_returns_none(temp_git_repo, monkeypatch):
    repo = temp_git_repo()
    # Detach HEAD by checking out the commit hash directly.
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    subprocess.check_call(["git", "-C", str(repo), "checkout", sha], stderr=subprocess.DEVNULL)
    monkeypatch.chdir(repo)
    assert hook.current_branch() is None


def test_has_develop_branch_true(temp_git_repo, monkeypatch):
    repo = temp_git_repo(branches=["main", "develop"], head="main")
    monkeypatch.chdir(repo)
    assert hook.has_develop_branch() is True


def test_has_develop_branch_false_single_trunk(temp_git_repo, monkeypatch):
    repo = temp_git_repo(branches=["main"], head="main")
    monkeypatch.chdir(repo)
    assert hook.has_develop_branch() is False
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
tests/run.sh tests/test_helpers.py::test_current_branch_on_feature
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement the wrappers**

Add to `hooks/check-pr-base.py` after `split_command_chain`:

```python
import subprocess


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
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
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
```

- [ ] **Step 4: Run tests to verify pass**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/check-pr-base.py tests/test_helpers.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): current_branch() and has_develop_branch() wrappers

Subprocess wrappers around 'git symbolic-ref --short HEAD' and
'git rev-parse --verify develop'. Both fail open — return None / False on
any error so the hook treats unknown environments as 'pass-through'.
Tested against real temp git repos.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Shell I/O wrappers — `pr_refs_for` and `pr_for_branch`

**Files:**
- Modify: `hooks/check-pr-base.py`
- Modify: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_helpers.py`:

```python
# pr_refs_for / pr_for_branch -----------------------------------------------

def test_pr_refs_for_returns_tuple(gh_stub, monkeypatch, tmp_path):
    gh_stub('{"baseRefName":"main","headRefName":"feature/foo"}')
    monkeypatch.chdir(tmp_path)
    assert hook.pr_refs_for("42") == ("main", "feature/foo")


def test_pr_refs_for_gh_failure_returns_none(gh_stub, monkeypatch, tmp_path):
    gh_stub("", exit_code=2)
    monkeypatch.chdir(tmp_path)
    assert hook.pr_refs_for("42") is None


def test_pr_refs_for_malformed_json_returns_none(gh_stub, monkeypatch, tmp_path):
    gh_stub("not json")
    monkeypatch.chdir(tmp_path)
    assert hook.pr_refs_for("42") is None


def test_pr_for_branch_returns_number(gh_stub, monkeypatch, tmp_path):
    gh_stub('[{"number":42}]')
    monkeypatch.chdir(tmp_path)
    assert hook.pr_for_branch("feature/foo") == "42"


def test_pr_for_branch_no_pr_returns_none(gh_stub, monkeypatch, tmp_path):
    gh_stub("[]")
    monkeypatch.chdir(tmp_path)
    assert hook.pr_for_branch("feature/foo") is None
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
tests/run.sh tests/test_helpers.py::test_pr_refs_for_returns_tuple
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement the wrappers**

Add to `hooks/check-pr-base.py` after `has_develop_branch`:

```python
def pr_refs_for(pr_num: str) -> Optional[tuple[str, str]]:
    """Return (baseRefName, headRefName) for a PR number, or None on failure."""
    raw = _run(
        ["gh", "pr", "view", pr_num, "--json", "baseRefName,headRefName"]
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
    raw = _run(["gh", "pr", "list", "--head", branch, "--json", "number"])
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        if not data:
            return None
        return str(data[0]["number"])
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None
```

- [ ] **Step 4: Run tests to verify pass**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: 30 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/check-pr-base.py tests/test_helpers.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): pr_refs_for() and pr_for_branch() wrap gh pr lookups

pr_refs_for returns (baseRefName, headRefName) from a single gh pr view
call so check_merge can validate both the PR's base and identify its
source branch type. pr_for_branch resolves the PR number for a branch
when 'gh pr merge' is invoked without an explicit number.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Diagnostic templates and `Decision` dataclass

**Files:**
- Modify: `hooks/check-pr-base.py`
- Modify: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_helpers.py`:

```python
# Decision dataclass --------------------------------------------------------

def test_decision_allow_default():
    d = hook.Decision(allow=True)
    assert d.allow is True
    assert d.reason == ""


def test_decision_deny_with_reason():
    d = hook.Decision(allow=False, reason="oops")
    assert d.allow is False
    assert d.reason == "oops"


# Diagnostic templates ------------------------------------------------------

def test_diagnostic_wrong_base_pr_exists():
    msg = hook.diag_wrong_base_pr(pr_num="42", actual="main", expected="develop", branch_type="feature")
    assert "PR #42" in msg
    assert "ABORTING" in msg
    assert 'has base "main"' in msg
    assert 'expected "develop"' in msg
    assert "gh pr edit 42 --base develop" in msg


def test_diagnostic_wrong_base_create():
    msg = hook.diag_wrong_base_create(actual="main", expected="develop", branch_type="feature", rest_of_args="--title t")
    assert "BLOCKED" in msg
    assert "feature" in msg
    assert "gh pr create --base develop --title t" in msg


def test_diagnostic_missing_base_create():
    msg = hook.diag_missing_base_create(expected="develop", branch_type="feature", rest_of_args="--title t")
    assert "BLOCKED" in msg
    assert "explicit --base develop" in msg
    assert "gh pr create --base develop --title t" in msg
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
tests/run.sh tests/test_helpers.py -k "decision or diagnostic"
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement Decision and diagnostic helpers**

Add to `hooks/check-pr-base.py` after `pr_for_branch`:

```python
from dataclasses import dataclass


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
```

- [ ] **Step 4: Run tests to verify pass**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: 35 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/check-pr-base.py tests/test_helpers.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): add Decision dataclass and diagnostic templates

Three single-source-of-truth diagnostic builders (WRONG_BASE on existing
PR, WRONG_BASE on create, MISSING_BASE on create) plus a small Decision
dataclass returned by check_create / check_merge. Templates match §8 of
the design spec verbatim so the bash script can reuse identical wording.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: `check_create(cmd)` handler

**Files:**
- Modify: `hooks/check-pr-base.py`
- Modify: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_helpers.py`:

```python
from unittest.mock import patch


# check_create --------------------------------------------------------------

def _patch_branch_state(*, branch, has_develop=True):
    """Convenience: patch both shell wrappers with a single context manager."""
    return patch.multiple(
        hook,
        current_branch=lambda: branch,
        has_develop_branch=lambda: has_develop,
    )


def test_check_create_feature_missing_base_denied():
    with _patch_branch_state(branch="feature/foo"):
        d = hook.check_create("gh pr create --title t")
    assert d.allow is False
    assert "MISSING_BASE" in d.reason or "explicit --base develop" in d.reason


def test_check_create_feature_wrong_base_denied():
    with _patch_branch_state(branch="feature/foo"):
        d = hook.check_create("gh pr create --base main --title t")
    assert d.allow is False
    assert "main" in d.reason
    assert "develop" in d.reason


def test_check_create_feature_correct_base_allowed():
    with _patch_branch_state(branch="feature/foo"):
        d = hook.check_create("gh pr create --base develop --title t")
    assert d.allow is True


def test_check_create_hotfix_main_allowed():
    with _patch_branch_state(branch="hotfix/v1.0.1"):
        d = hook.check_create("gh pr create --base main --title t")
    assert d.allow is True


def test_check_create_hotfix_develop_denied():
    with _patch_branch_state(branch="hotfix/v1.0.1"):
        d = hook.check_create("gh pr create --base develop --title t")
    assert d.allow is False


def test_check_create_release_develop_denied():
    with _patch_branch_state(branch="release/v1.0"):
        d = hook.check_create("gh pr create --base develop --title t")
    assert d.allow is False


def test_check_create_non_git_flow_branch_allowed():
    with _patch_branch_state(branch="chore/cleanup"):
        d = hook.check_create("gh pr create --base main --title t")
    assert d.allow is True


def test_check_create_detached_head_allowed():
    with _patch_branch_state(branch=None):
        d = hook.check_create("gh pr create --title t")
    assert d.allow is True


def test_check_create_single_trunk_repo_allowed():
    with _patch_branch_state(branch="feature/foo", has_develop=False):
        d = hook.check_create("gh pr create --title t")
    assert d.allow is True


def test_check_create_shell_var_base_allowed_with_warn(capsys):
    with _patch_branch_state(branch="feature/foo"):
        d = hook.check_create("gh pr create --base $BASE")
    assert d.allow is True
    captured = capsys.readouterr()
    assert "$BASE" in captured.err  # warning written to stderr


def test_check_create_draft_missing_base_denied():
    with _patch_branch_state(branch="feature/foo"):
        d = hook.check_create("gh pr create --draft --title t")
    assert d.allow is False
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
tests/run.sh tests/test_helpers.py::test_check_create_feature_missing_base_denied
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement `check_create`**

Add to `hooks/check-pr-base.py` after the diagnostic helpers:

```python
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
    # Drop the leading 'gh pr create'
    s = cmd
    idx = s.find("gh pr create")
    if idx >= 0:
        s = s[idx + len("gh pr create"):].strip()
    # Drop --base / --base= / -B and its value (regex same as parse_base_flag).
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
```

- [ ] **Step 4: Run tests to verify pass**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: 46 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/check-pr-base.py tests/test_helpers.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): check_create() validates gh pr create against base matrix

Pass-through for detached HEAD, non-Git-Flow branches, single-trunk repos
(no develop), and shell-expansion --base values (with stderr warning).
Denies missing --base, wrong --base, and uses diagnostic templates with
remediation hint that omits the bad --base from the suggested re-run.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: `check_merge(cmd)` handler

**Files:**
- Modify: `hooks/check-pr-base.py`
- Modify: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_helpers.py`:

```python
# check_merge ---------------------------------------------------------------

def _patch_pr_state(*, refs):
    """Patch pr_refs_for to return a fixed (base, head) tuple, or None."""
    return patch.object(hook, "pr_refs_for", lambda pr_num: refs)


def test_check_merge_wrong_base_denied():
    with _patch_pr_state(refs=("main", "feature/foo")):
        d = hook.check_merge("gh pr merge 42 --squash")
    assert d.allow is False
    assert "PR #42" in d.reason
    assert "main" in d.reason
    assert "develop" in d.reason
    assert "gh pr edit 42 --base develop" in d.reason


def test_check_merge_correct_base_allowed():
    with _patch_pr_state(refs=("develop", "feature/foo")):
        d = hook.check_merge("gh pr merge 42")
    assert d.allow is True


def test_check_merge_release_to_main_allowed():
    with _patch_pr_state(refs=("main", "release/v1.0")):
        d = hook.check_merge("gh pr merge 7")
    assert d.allow is True


def test_check_merge_gh_failure_fails_open():
    with _patch_pr_state(refs=None):
        d = hook.check_merge("gh pr merge 42")
    assert d.allow is True


def test_check_merge_no_pr_number_resolves_via_branch(monkeypatch):
    """When PR number is omitted, use pr_for_branch + current_branch."""
    monkeypatch.setattr(hook, "current_branch", lambda: "feature/foo")
    monkeypatch.setattr(hook, "pr_for_branch", lambda b: "99")
    monkeypatch.setattr(hook, "pr_refs_for", lambda n: ("main", "feature/foo"))
    d = hook.check_merge("gh pr merge --squash")
    assert d.allow is False
    assert "PR #99" in d.reason


def test_check_merge_non_git_flow_head_allowed():
    """If the PR's headRefName is not Git Flow, pass through."""
    with _patch_pr_state(refs=("main", "chore/foo")):
        d = hook.check_merge("gh pr merge 42")
    assert d.allow is True
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
tests/run.sh tests/test_helpers.py::test_check_merge_wrong_base_denied
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement `check_merge`**

Add to `hooks/check-pr-base.py` after `check_create`:

```python
def check_merge(cmd: str) -> Decision:
    """Validate a single `gh pr merge ...` command segment."""
    pr_num = parse_pr_number(cmd)
    if pr_num is None:
        # gh resolves from current branch when the number is omitted.
        branch = current_branch()
        if branch is None:
            return Decision(allow=True)
        pr_num = pr_for_branch(branch)
        if pr_num is None:
            return Decision(allow=True)  # gh would fail naturally

    refs = pr_refs_for(pr_num)
    if refs is None:
        return Decision(allow=True)  # gh failure — fail open
    actual_base, head = refs

    expected = expected_base_for(head)
    if expected is None:
        return Decision(allow=True)  # PR is not from a Git Flow branch

    if actual_base != expected:
        return Decision(
            allow=False,
            reason=diag_wrong_base_pr(
                pr_num=pr_num,
                actual=actual_base,
                expected=expected,
                branch_type=_branch_type_label(head),
            ),
        )
    return Decision(allow=True)
```

- [ ] **Step 4: Run tests to verify pass**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: 52 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/check-pr-base.py tests/test_helpers.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): check_merge() validates gh pr merge against base matrix

Resolves PR via parse_pr_number (explicit) or pr_for_branch (omitted) and
queries pr_refs_for to get baseRefName + headRefName. Validates the PR's
base against the expected base for headRefName's branch type. Fails open
on gh errors and pass-through for non-Git-Flow head branches.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: `dispatch()` and `main()` integration

Wire it all together. Replace the stub `main()` with the real dispatcher.

**Files:**
- Modify: `hooks/check-pr-base.py`
- Modify: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_helpers.py`:

```python
# dispatch ------------------------------------------------------------------

def test_dispatch_unrelated_command_allows():
    d = hook.dispatch("echo hello")
    assert d.allow is True


def test_dispatch_quoted_echo_does_not_match(monkeypatch):
    """Word-boundary regex: 'gh pr create' inside a quoted echo is not a match."""
    # Even if branch is feature/*, the command shouldn't be parsed as gh pr create.
    monkeypatch.setattr(hook, "current_branch", lambda: "feature/foo")
    monkeypatch.setattr(hook, "has_develop_branch", lambda: True)
    d = hook.dispatch('echo "gh pr create --base main"')
    assert d.allow is True


def test_dispatch_chained_validates_first_failing_segment(monkeypatch):
    monkeypatch.setattr(hook, "current_branch", lambda: "feature/foo")
    monkeypatch.setattr(hook, "has_develop_branch", lambda: True)
    d = hook.dispatch("gh pr create --base main --title t && echo done")
    assert d.allow is False


def test_dispatch_gh_pr_view_subcommand_passthrough():
    d = hook.dispatch("gh pr view 42")
    assert d.allow is True


def test_dispatch_gh_pr_edit_subcommand_passthrough():
    d = hook.dispatch("gh pr edit 42 --base develop")
    assert d.allow is True
```

- [ ] **Step 2: Run to verify failure**

Run:
```bash
tests/run.sh tests/test_helpers.py::test_dispatch_unrelated_command_allows
```
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement `dispatch` and replace `main`**

In `hooks/check-pr-base.py`, after `check_merge`, add `dispatch` and replace the stub `main`:

```python
# Word-boundary patterns: avoid matching 'gh pr create' inside quoted strings.
# We require 'gh pr create' / 'gh pr merge' to be at start-of-segment after
# trimming whitespace, OR preceded by typical shell separators.
_GH_PR_CREATE_RE = re.compile(r"(?:^|[\s;&|])gh\s+pr\s+create\b")
_GH_PR_MERGE_RE = re.compile(r"(?:^|[\s;&|])gh\s+pr\s+merge\b")


def dispatch(cmd: str) -> Decision:
    """Validate every gh pr create/merge segment in a command chain."""
    for segment in split_command_chain(cmd):
        seg = " " + segment  # prepend space so ^ regex still anchors at boundary
        if _GH_PR_CREATE_RE.search(seg):
            d = check_create(segment)
            if not d.allow:
                return d
        elif _GH_PR_MERGE_RE.search(seg):
            d = check_merge(segment)
            if not d.allow:
                return d
    return Decision(allow=True)


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
```

- [ ] **Step 4: Run tests to verify pass**

Run:
```bash
tests/run.sh tests/test_helpers.py
```
Expected: 57 passed.

- [ ] **Step 5: Commit**

```bash
git add hooks/check-pr-base.py tests/test_helpers.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(hook): dispatch() routes to check_create/check_merge per segment

Splits the bash command on && ; ||, then routes each segment to the right
handler using word-boundary regexes (so 'gh pr create' inside a quoted
echo does not trigger). main() reads the harness JSON, runs dispatch, and
emits a deny payload to stdout when blocked. Always exits 0 — fail-open
on internal exceptions with traceback to stderr.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: End-to-end hook tests via subprocess (17 cases)

Now the hook script is fully wired. Drive it via `subprocess.run` from `tests/test_hook_e2e.py` to verify every scenario from the spec end-to-end.

**Files:**
- Create: `tests/test_hook_e2e.py`

- [ ] **Step 1: Write all 17 e2e tests**

Create `tests/test_hook_e2e.py`:

```python
"""End-to-end hook tests — drive hooks/check-pr-base.py via subprocess.

Each test:
  1. Creates an isolated git repo with the requested branch layout.
  2. Stubs `gh` on PATH with canned stdout/exit code.
  3. Pipes the harness JSON payload to the hook on stdin.
  4. Asserts on (exit_code, stdout, stderr).
"""
from __future__ import annotations

import json


def _payload(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def _assert_deny(stdout: str, *needles: str) -> dict:
    parsed = json.loads(stdout)
    reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
    for n in needles:
        assert n in reason, f"missing {n!r} in {reason!r}"
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    return parsed


# 1
def test_create_missing_base_on_feature_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/test-block"], head="feature/test-block")
    code, out, err = run_hook(_payload("gh pr create --title test"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED", "feature/*", "explicit --base develop")


# 2
def test_create_wrong_base_on_feature_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/test-block"], head="feature/test-block")
    code, out, err = run_hook(_payload("gh pr create --base main --title test"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED", "main", "develop")


# 3
def test_create_correct_base_on_feature_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/test-block"], head="feature/test-block")
    code, out, err = run_hook(_payload("gh pr create --base develop --title test"), repo)
    assert code == 0
    assert out == ""


# 4
def test_merge_wrong_base_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    gh_stub('{"baseRefName":"main","headRefName":"feature/foo"}')
    code, out, err = run_hook(_payload("gh pr merge 42 --squash"), repo)
    assert code == 0
    _assert_deny(out, "PR #42", "main", "develop", "gh pr edit 42 --base develop")


# 5
def test_merge_correct_base_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    gh_stub('{"baseRefName":"develop","headRefName":"feature/foo"}')
    code, out, err = run_hook(_payload("gh pr merge 42"), repo)
    assert code == 0
    assert out == ""


# 6
def test_create_on_main_branch_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop"], head="main")
    code, out, err = run_hook(_payload("gh pr create --base main --title t"), repo)
    assert code == 0
    assert out == ""


# 7
def test_single_trunk_repo_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(_payload("gh pr create --title t"), repo)
    assert code == 0
    assert out == ""


# 8
def test_create_hotfix_correct_base_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "hotfix/v1.0.1"], head="hotfix/v1.0.1")
    code, out, err = run_hook(_payload("gh pr create --base main --title t"), repo)
    assert code == 0
    assert out == ""


# 9
def test_create_hotfix_wrong_base_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "hotfix/v1.0.1"], head="hotfix/v1.0.1")
    code, out, err = run_hook(_payload("gh pr create --base develop --title t"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED", "main")


# 10
def test_create_release_wrong_base_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "release/v1.0"], head="release/v1.0")
    code, out, err = run_hook(_payload("gh pr create --base develop --title t"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED", "main")


# 11
def test_unrelated_command_passthrough(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo()
    code, out, err = run_hook(_payload("echo hello"), repo)
    assert code == 0
    assert out == ""


# 12
def test_gh_failure_fails_open(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    gh_stub("", exit_code=2)
    code, out, err = run_hook(_payload("gh pr merge 42"), repo)
    assert code == 0
    assert out == ""


# 13
def test_chained_command_validates_each_segment(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload("gh pr create --base main --title t && echo done"), repo
    )
    assert code == 0
    _assert_deny(out, "BLOCKED")


# 14
def test_quoted_string_with_gh_pr_create_does_not_match(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload('echo "gh pr create --base main"'), repo
    )
    assert code == 0
    assert out == ""


# 15
def test_gh_pr_view_other_subcommand_passthrough(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo()
    code, out, err = run_hook(_payload("gh pr view 42"), repo)
    assert code == 0
    assert out == ""


# 16
def test_draft_create_missing_base_on_feature_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(_payload("gh pr create --draft --title t"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED")


# 17
def test_internal_exception_fails_open(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo()
    # Send malformed stdin: not valid JSON.
    import subprocess, sys
    from pathlib import Path
    REPO = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, str(REPO / "hooks" / "check-pr-base.py")],
        input="not json at all",
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert "Traceback" in proc.stderr or proc.stderr  # any stderr output is fine
```

- [ ] **Step 2: Run e2e tests to verify all pass**

Run:
```bash
tests/run.sh tests/test_hook_e2e.py
```
Expected: 17 passed.

- [ ] **Step 3: Run the full suite to confirm nothing regressed**

Run:
```bash
tests/run.sh
```
Expected: 74 passed (57 helpers + 17 e2e).

- [ ] **Step 4: Commit**

```bash
git add tests/test_hook_e2e.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
test(hook): 17 end-to-end scenarios driving check-pr-base.py via subprocess

Each test creates an isolated git repo, optionally stubs gh, pipes the
harness JSON payload to the real hook script, and asserts on exit code +
stdout/stderr. Covers the full §9.2 matrix from the design spec including
wrong-base creates and merges for feature/hotfix/release, single-trunk
repos, gh failures, chained commands, quoted echoes, draft PRs, and
malformed stdin.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: `verify_pr_base` bash function in `git-flow-finish.sh`

Add the in-script Layer 2 check.

**Files:**
- Modify: `scripts/git-flow-finish.sh`

- [ ] **Step 1: Read the current script's bottom to find the existing entrypoint**

Run:
```bash
tail -30 scripts/git-flow-finish.sh
```

Note whether the file already has a `if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main; fi` guard or runs `main` unconditionally. Note the line number of the `merge_main_via_pr` `gh pr merge` invocation (mentioned in the spec at ~line 215).

- [ ] **Step 2: Add `verify_pr_base` near the top (after the shebang and `set -e`)**

Insert immediately after the existing top-of-file boilerplate and before any other function definition:

```bash
# ---------------------------------------------------------------------------
# verify_pr_base — abort if a PR's baseRefName does not match the expected base.
# Layer 2 of the PR base-branch enforcement (Layer 1 is hooks/check-pr-base.py).
# Fails open on gh errors so the hook layer remains the source of truth.
# Spec: docs/superpowers/specs/2026-05-02-pr-base-enforcement-design.md
# ---------------------------------------------------------------------------
verify_pr_base() {
  local pr_num="$1" expected_base="$2"
  local actual_base
  actual_base=$(gh pr view "$pr_num" --json baseRefName --jq '.baseRefName' 2>/dev/null) \
    || return 0  # gh failure: trust the hook layer to enforce
  if [[ "$actual_base" != "$expected_base" ]]; then
    cat >&2 <<EOM
✗ ABORTING: PR #${pr_num} has base "${actual_base}", expected "${expected_base}".
Feature/hotfix/release branches must merge to ${expected_base} per Git Flow.

To fix:
  gh pr edit ${pr_num} --base ${expected_base}

Then re-run /finish.
EOM
    return 1
  fi
}
```

- [ ] **Step 3: Add a sourcing guard at the bottom (if not already present)**

If the script currently ends with a bare call to `main "$@"` or similar, replace it with:

```bash
# Allow the file to be sourced for testing without invoking the main flow.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
```

If the script already has an `if __name__`-style guard or some equivalent, leave it alone.

- [ ] **Step 4: Update the `merge_main_via_pr` block to call `verify_pr_base`**

Find the existing `gh pr merge "$PR_NUMBER" --squash` line inside `merge_main_via_pr` (near line 215). Insert immediately above it:

```bash
verify_pr_base "$PR_NUMBER" "main" || exit 2
```

So the sequence becomes:

```bash
verify_pr_base "$PR_NUMBER" "main" || exit 2
gh pr merge "$PR_NUMBER" --squash
```

- [ ] **Step 5: Sanity-check the script still parses**

Run:
```bash
bash -n scripts/git-flow-finish.sh && echo "OK"
```
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add scripts/git-flow-finish.sh
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
feat(finish): verify_pr_base() guards merge_main_via_pr

Layer 2 of the PR base-branch enforcement (Layer 1 is hooks/check-pr-base.py).
Validates the PR's baseRefName matches the expected base before invoking
gh pr merge. Fails open on gh errors so the hook layer remains the source
of truth. Adds a sourcing guard so tests can source the script without
triggering the main flow.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Bash function tests — `tests/test_verify_pr_base_sh.py`

**Files:**
- Create: `tests/test_verify_pr_base_sh.py`

- [ ] **Step 1: Write the bash function tests**

Create `tests/test_verify_pr_base_sh.py`:

```python
"""Test scripts/git-flow-finish.sh:verify_pr_base via subprocess.

Sources the script in a subshell so we don't trigger the main /finish flow.
Stubs `gh` via the same fixture used by the hook tests.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"


def _run_verify(pr_num: str, expected_base: str, gh_stdout: str, gh_exit: int = 0):
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        verify_pr_base "{pr_num}" "{expected_base}"
    """)
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    env["MOCK_GH_STDOUT"] = gh_stdout
    env["MOCK_GH_EXIT"] = str(gh_exit)
    return subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True, env=env, timeout=10
    )


def test_verify_pass_on_match():
    result = _run_verify("42", "develop", "develop")
    assert result.returncode == 0
    assert result.stderr == ""


def test_verify_fail_on_mismatch():
    result = _run_verify("42", "develop", "main")
    assert result.returncode == 1
    assert "ABORTING" in result.stderr
    assert "PR #42" in result.stderr
    assert "gh pr edit 42 --base develop" in result.stderr


def test_verify_fail_open_on_gh_error():
    result = _run_verify("42", "develop", "", gh_exit=2)
    assert result.returncode == 0  # fail open
```

- [ ] **Step 2: Run tests**

Run:
```bash
tests/run.sh tests/test_verify_pr_base_sh.py
```
Expected: 3 passed.

If `test_verify_pass_on_match` fails because sourcing the script triggered some side effect (e.g., `set -e` aborting because `main` ran), revisit the sourcing guard added in Task 14 Step 3.

- [ ] **Step 3: Run the full suite**

Run:
```bash
tests/run.sh
```
Expected: 77 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_verify_pr_base_sh.py
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
test(finish): three subprocess-driven cases for verify_pr_base

Sources git-flow-finish.sh in a subshell (sourcing guard added in the
prior commit prevents the full main flow from running) and calls
verify_pr_base with a stubbed gh on PATH. Covers pass-on-match,
fail-on-mismatch (asserts diagnostic includes 'ABORTING', 'PR #42', and
the gh pr edit remediation), and fail-open on gh error.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 16: Documentation updates

**Files:**
- Modify: `commands/finish.md`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a safety-check note to `commands/finish.md`**

Find the section that documents the feature-finish PR merge step. Add a paragraph (or note block) immediately after that step:

```markdown
> **Safety check:** Before invoking `gh pr merge`, `/finish` and the
> plugin-bundled `check-pr-base` hook both verify the PR's `baseRefName`
> matches the expected base for the branch type:
>
> | Branch | Required base |
> | --- | --- |
> | `feature/*` | `develop` |
> | `hotfix/*` | `main` |
> | `release/*` | `main` |
>
> A wrong-base PR is blocked with a `gh pr edit <N> --base <expected>`
> remediation hint. The same hook also blocks `gh pr create` invocations
> on Git Flow branches that omit `--base` or pass the wrong base.
```

- [ ] **Step 2: Add a "Quality gates installed" subsection to `README.md`**

If the README already has a section listing what the plugin installs, append:

```markdown
### Quality gates

The plugin ships a `PreToolUse` Bash hook (`hooks/check-pr-base.py`) that
auto-activates on install. It blocks two categories of mistake on Git
Flow branches:

1. `gh pr create` without `--base`, or with the wrong `--base`.
2. `gh pr merge` against a PR whose `baseRefName` does not match the
   branch-type matrix below.

| Branch | Required base |
| --- | --- |
| `feature/*` | `develop` |
| `hotfix/*` | `main` |
| `release/*` | `main` |

Pass-through silently for non-Git-Flow branches, single-trunk repos
(no `develop`), detached HEAD, and any `gh`/`git` failure (fail open —
the hook never blocks legitimate work due to its own bugs).

The same matrix is enforced in-script by `verify_pr_base()` in
`scripts/git-flow-finish.sh`, so `/finish` catches the failure even if
the hook is somehow disabled.
```

If the README has no quality-gates section yet, add one above the existing "Releasing" section.

- [ ] **Step 3: Add an `Unreleased` entry to `CHANGELOG.md`**

If `CHANGELOG.md` already has an `## [Unreleased]` section, append under `### Added`. Otherwise add the section at the top:

```markdown
## [Unreleased]

### Added

- PR base-branch enforcement (#1):
  - New `hooks/check-pr-base.py` PreToolUse hook blocks wrong-base
    `gh pr create` and `gh pr merge` invocations on Git Flow branches
    (`feature/*`→develop, `hotfix/*`→main, `release/*`→main).
  - New `verify_pr_base()` function in `scripts/git-flow-finish.sh`
    provides defense-in-depth before any in-script `gh pr merge` call.
  - Pass-through for non-Git-Flow branches, single-trunk repos, and any
    `gh`/`git` error (fail open).
```

- [ ] **Step 4: Verify no broken links and the README/CHANGELOG render**

Run:
```bash
python3 -c "import markdown" 2>/dev/null && python3 -c "import markdown; print('OK' if markdown.markdown(open('README.md').read()) else 'FAIL')" || echo "skip — markdown not installed"
grep -E "^## \[Unreleased\]" CHANGELOG.md
```
Expected: header present in CHANGELOG.

- [ ] **Step 5: Commit**

```bash
git add commands/finish.md README.md CHANGELOG.md
./scripts/commit-preflight.sh
git commit -m "$(cat <<'EOF'
docs: document PR base-branch enforcement

Adds a safety-check note to commands/finish.md, a Quality gates section
to README.md describing the new hook and the validation matrix, and an
Unreleased CHANGELOG entry referencing issue #1.
Refs #1

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 17: Final integration check and PR

- [ ] **Step 1: Run the full test suite one more time**

Run:
```bash
tests/run.sh
```
Expected: 77 passed.

- [ ] **Step 2: Confirm git-flow-finish.sh still parses**

Run:
```bash
bash -n scripts/git-flow-finish.sh && echo "OK"
python3 -c "import json; json.load(open('hooks/hooks.json'))" && echo "OK"
python3 -c "import ast; ast.parse(open('hooks/check-pr-base.py').read())" && echo "OK"
```
Expected: three "OK" lines.

- [ ] **Step 3: Smoke-test the hook manually against the real repo**

Run from inside this feature branch (replace `<N>` with any open PR you have access to, or skip if none):

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"gh pr create --title test"}}' \
  | python3 hooks/check-pr-base.py
echo "exit=$?"
```

If the current branch is `feature/issue-1-pr-base-enforcement` and `develop` exists, expected output is a JSON deny payload (because `--base` was omitted on a `feature/*` branch). `exit=0`.

- [ ] **Step 4: Confirm clean working tree, then push**

Run:
```bash
git status --short
git log --oneline develop..HEAD
git push -u origin feature/issue-1-pr-base-enforcement
```

Expected: clean tree, ~14 commits, push succeeds.

- [ ] **Step 5: Open the PR against `develop` (NEVER `main`)**

```bash
gh pr create --base develop --title "feat: enforce PR base-branch via hook + script (issue #1)" --body "$(cat <<'EOF'
## Summary

Two-layer enforcement for the recurring wrong-base merge bug (issue #1):

- **Layer 1** — plugin-bundled PreToolUse hook (`hooks/check-pr-base.py`)
  intercepts both `gh pr create` and `gh pr merge` Bash invocations and
  validates against the Git Flow matrix:
  - `feature/*` → develop
  - `hotfix/*` → main
  - `release/*` → main
- **Layer 2** — `verify_pr_base()` bash function in
  `scripts/git-flow-finish.sh`, called before the in-script `gh pr merge`
  in `merge_main_via_pr`.

Pass-through silently for non-Git-Flow branches, single-trunk repos,
detached HEAD, and any `gh`/`git` failure.

Closes #1.

## Test plan

- [ ] `tests/run.sh` — 77 passing tests
  - 30 pure-helper unit tests
  - 27 handler unit tests with mocked I/O
  - 17 end-to-end subprocess tests covering every §9.2 scenario
  - 3 bash-function tests sourcing the script in a subshell
- [ ] Manual smoke: `echo '...' | python3 hooks/check-pr-base.py` on
      this branch with no `--base` blocks the call as MISSING_BASE.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Verify the PR base is `develop`**

Run:
```bash
PR_NUMBER=$(gh pr list --head feature/issue-1-pr-base-enforcement --json number --jq '.[0].number')
gh pr view $PR_NUMBER --json baseRefName --jq '.baseRefName'
```
Expected: `develop`. If anything else, **STOP** and re-run `gh pr edit $PR_NUMBER --base develop`.

---

## Self-Review Notes

- All 17 e2e scenarios from spec §9.2 map to a numbered test in Task 13.
- All 3 bash-function scenarios from spec §9.3 map to a test in Task 15.
- Helper coverage in Tasks 3-9 covers spec §9.1 row-by-row.
- Diagnostic templates (spec §8) live in one place — Task 9 — and are reused by both layers via shared wording in Task 14.
- Validation matrix (spec §3) is encoded once in `expected_base_for` (Task 3) and referenced everywhere downstream.
- The implementation requirement noted in spec §9.3 (sourcing guard for the bash script) is added in Task 14 Step 3 before the test that depends on it lands in Task 15.
- File responsibilities (spec §5) align with the file map above; no file does more than one thing.
- No placeholders, TODOs, or "similar to Task N" references — every step has full code.
- Type/name consistency check: `Decision`, `expected_base_for`, `parse_base_flag`, `parse_pr_number`, `split_command_chain`, `pr_refs_for`, `pr_for_branch`, `current_branch`, `has_develop_branch`, `check_create`, `check_merge`, `dispatch`, `main`, `verify_pr_base` — all names used consistently across tasks.

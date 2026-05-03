# PR Base-Branch Enforcement Design

**Issue:** [git-flow#1](https://github.com/abhattacherjee/git-flow/issues/1) — Enforce baseRefName check before `gh pr merge` in `/finish` skill (and pre-tool hook)
**Status:** Approved (brainstorm 2026-05-02)
**Branch:** `feature/issue-1-pr-base-enforcement`

## 1. Background & motivation

The `/finish` Git Flow command (and its underlying `scripts/git-flow-finish.sh`) currently invokes `gh pr merge` without verifying the PR's `baseRefName`. A feature PR mistakenly created against `main` (e.g. by forgetting `--base develop` on `gh pr create`) gets squash-merged to main, bypassing the develop → release → main pipeline.

Documented recurrences (3+):

- obsidian-brain PR #30 (2026-04-12) — wrong-base merge to main; recovery via `git revert` later silently dropped 7 files in a future release merge.
- knowledge-base-ui PR abhattacherjee/harden-repo#10 (2026-05-02) — same wrong-base merge; recovered via forward-only release/v0.1.0 sync.
- One earlier undocumented case.

The mistake almost always originates at `gh pr create` time (missing or wrong `--base`), not at merge time. Enforcement at merge is a safety net; enforcement at create is the structural fix.

## 2. Goals & non-goals

**Goals:**

- Block wrong-base `gh pr merge` invocations from any caller (the `/finish` skill, manual user commands, other plugins/skills).
- Block wrong-base or missing-`--base` `gh pr create` invocations on `feature/*`, `hotfix/*`, and `release/*` branches.
- Provide remediation hints in every diagnostic so the user can fix and retry without context-switching.
- Defense in depth: enforcement survives the loss of any single layer (hook disabled, script bypassed).

**Non-goals:**

- Auto-injecting `--base` into the user's command (block-and-instruct is preferred over silent rewrite).
- Modifying any other plugin's behavior (e.g., harden-repo's project-local `.claude/hooks/`); harden-repo can backport the same hook in a separate change if desired.
- Enforcing in repos that don't follow Git Flow (single-trunk, no `develop` branch) — pass-through silently.

## 3. Validation matrix

| Branch pattern | Required `baseRefName` |
| --- | --- |
| `feature/*` | `develop` |
| `hotfix/*` | `main` |
| `release/*` | `main` |
| anything else (`main`, `develop`, `chore/*`, etc.) | not enforced — pass-through |

This matches the user's global CLAUDE.md hard rule and the existing behavior of `scripts/git-flow-finish.sh` (which merges hotfix and release locally to `main` and falls back to `gh pr create --base main` when `main` is protected).

## 4. Architecture

Two enforcement layers, defense in depth.

### 4.1 Layer 1 — Plugin-bundled PreToolUse hook

A new file `hooks/check-pr-base.py` intercepts both `gh pr create` and `gh pr merge` Bash invocations. Declared via a new `hooks/hooks.json` (the plugin-level hook manifest auto-loaded by the Claude Code harness; same schema as `~/.claude/settings.json` `hooks` object).

The hook:

1. Reads JSON payload from stdin: `{"tool_name": "Bash", "tool_input": {"command": "..."}}`.
2. Fast-filters on `gh pr create` / `gh pr merge` substring; exits 0 immediately for any other command.
3. Splits the command on `&&`/`;`/`||` and validates every `gh pr (create|merge)` segment found (so chained commands are caught).
4. Dispatches to `check_create` or `check_merge`.
5. Emits a JSON deny payload to stdout if blocked, then exits 0 (deny payload always written via stdout, never stderr; matches `prevent-direct-push.py`).
6. On any internal exception (json parse error, unexpected) writes a stack trace to stderr and exits 0 (fail open). Never exits non-zero — that would block legitimate work due to a hook bug.

### 4.2 Layer 2 — In-script verification

A new bash function `verify_pr_base` is added near the top of `scripts/git-flow-finish.sh`. It is called immediately before any `gh pr merge` invocation (currently the `merge_main_via_pr` path at ~line 215). On mismatch it prints the same diagnostic format as the hook and exits 2.

The script-level check produces faster feedback inside `/finish` (no extra harness round-trip) and survives a disabled hook.

## 5. Components & file layout

**New:**

```
hooks/
├── hooks.json              # Plugin hook declaration
└── check-pr-base.py        # ~150 lines, mirrors prevent-direct-push.py pattern
tests/
├── conftest.py                 # pytest fixtures: temp_git_repo, gh_stub, run_hook
├── test_helpers.py             # pure-function unit tests
├── test_hook_e2e.py            # end-to-end hook invocation tests (17 cases)
├── test_verify_pr_base_sh.py   # bash function tests via subprocess
├── fixtures/bin/gh             # stub: reads MOCK_GH_STDOUT / MOCK_GH_EXIT env
└── run.sh                      # pytest entrypoint
```

**Modified:**

```
scripts/git-flow-finish.sh        # +verify_pr_base(), +1 call site
commands/finish.md                # Document the safety check
README.md                         # "Quality gates installed" section
CHANGELOG.md                      # Unreleased entry
.claude-plugin/plugin.json        # Version bump (via bump-version.sh)
.claude-plugin/marketplace.json   # Lockstep version bump
```

### 5.1 `hooks/hooks.json`

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

The matcher is broad (any Bash); narrowing happens inside the script's first action. This matches the existing `prevent-direct-push.py` pattern and avoids spurious hooks for non-`gh` commands beyond a fast substring check.

### 5.2 `hooks/check-pr-base.py` internal structure

```python
@dataclass
class Decision:
    allow: bool
    reason: str = ""

def main() -> None:
    payload = json.load(sys.stdin)
    cmd = payload.get("tool_input", {}).get("command", "")
    decision = dispatch(cmd)
    if decision.allow:
        sys.exit(0)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.reason,
        }
    }))
    sys.exit(0)

def dispatch(cmd: str) -> Decision:
    # split on && ; || and validate every gh pr segment
    for segment in split_command_chain(cmd):
        if "gh pr create" in segment:
            d = check_create(segment)
            if not d.allow: return d
        elif "gh pr merge" in segment:
            d = check_merge(segment)
            if not d.allow: return d
    return Decision(allow=True)

# Pure helpers — testable in isolation, all I/O goes through these:
def expected_base_for(branch: str) -> Optional[str]: ...
def parse_base_flag(cmd: str) -> Optional[str]: ...      # handles --base, --base=, -B
def parse_pr_number(cmd: str) -> Optional[str]: ...      # handles bare number, URL
def split_command_chain(cmd: str) -> List[str]: ...      # split on && ; ||

# Shell/I-O wrappers — patched in tests:
def current_branch() -> Optional[str]: ...               # git symbolic-ref --short HEAD
def has_develop_branch() -> bool: ...                    # git rev-parse --verify develop
def pr_refs_for(pr_num: str) -> Optional[Tuple[str, str]]: ...  # gh pr view <N> --json baseRefName,headRefName → (base, head)
def pr_for_branch(branch: str) -> Optional[str]: ...     # gh pr list --head <branch>

# Subcommand handlers:
def check_create(cmd: str) -> Decision: ...
def check_merge(cmd: str)  -> Decision: ...
```

### 5.3 `scripts/git-flow-finish.sh` `verify_pr_base()`

```bash
verify_pr_base() {
  local pr_num="$1" expected_base="$2"
  local actual_base
  actual_base=$(gh pr view "$pr_num" --json baseRefName --jq '.baseRefName' 2>/dev/null) || return 0  # fail-open on gh error
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

Inserted at the existing `merge_main_via_pr()` call site:

```bash
verify_pr_base "$PR_NUMBER" "main" || exit 2
gh pr merge "$PR_NUMBER" --squash
```

## 6. Data flow

### 6.1 Flow A — `gh pr create` interception (primary bug catch)

```
User: gh pr create --title "..."           (forgot --base)
    ↓
PreToolUse → check-pr-base.py
    ↓
match "gh pr create" → check_create()
    ↓
current_branch() → "feature/foo"
expected_base_for("feature/foo") → "develop"
parse_base_flag(cmd) → None
    ↓
Decision(allow=False, reason=MISSING_BASE template)
    ↓
JSON deny → harness blocks Bash call
```

### 6.2 Flow B — `gh pr merge` interception (safety net)

```
User: gh pr merge 42 --squash --delete-branch
    ↓
PreToolUse → check-pr-base.py
    ↓
match "gh pr merge" → check_merge()
    ↓
parse_pr_number(cmd) → "42"
pr_refs_for("42") → ("main", "feature/foo")  (one gh pr view call: baseRefName,headRefName)
expected_base_for("feature/foo") → "develop"
"main" != "develop"
    ↓
Decision(allow=False, reason=WRONG_BASE template)
    ↓
JSON deny → harness blocks Bash call
```

### 6.3 Flow C — `/finish` happy path (correctly-based PR)

```
User: /finish on feature/foo with PR #42 → develop
    ↓
git-flow-finish.sh: verify_pr_base "42" "develop"
    → gh pr view → "develop" → return 0
    ↓
gh pr merge 42 --squash → harness PreToolUse fires
    ↓
check-pr-base.py: base=develop, expected=develop → allow
    ↓
Merge proceeds. Script continues with develop sync, version bump, push.
```

## 7. Error handling & edge cases

| Condition | Decision | Rationale |
| --- | --- | --- |
| Branch is `main`/`develop`/non-Git-Flow name | allow | Not Git Flow — pass-through |
| Detached HEAD | allow | Can't determine intent |
| `gh` not installed or unauthenticated | allow | Hook is not the gh-availability police; fail open |
| `gh pr view` returns non-zero | allow | Let `gh pr merge` produce its own error |
| `git rev-parse --verify develop` fails (single-trunk repo) | allow | A repo without `develop` isn't Git Flow |
| `gh pr create --draft` with no `--base`, branch is `feature/*` | deny | Same rule applies to drafts |
| `gh pr create` inside a quoted echo: `echo "gh pr create"` | allow | Use word-boundary regex, not naive substring |
| `--base "develop"` (quoted value) | parse correctly | Strip surrounding `"`/`'` |
| `--base "$BASE"` (shell expansion) | allow + stderr warning | Don't try to evaluate; trust user intent |
| Chained: `gh pr create ... && gh pr merge ...` | check each segment | Split on `&&`/`;`/`\|\|` |
| `gh pr view`, `gh pr edit`, `gh pr list` (other subcommands) | allow | Not our business |
| Hook internal exception (json parse, etc.) | allow + stderr trace | Fail open; never `sys.exit(1)` |

## 8. Diagnostic templates

Single source of truth — used by both layers.

```
[WRONG_BASE — PR exists]
✗ ABORTING: PR #{n} has base "{actual}", expected "{expected}".
{branch_type} branches must merge to {expected}, not {actual}.

To fix:
  gh pr edit {n} --base {expected}

Then re-run.
```

```
[WRONG_BASE — gh pr create]
✗ BLOCKED: cannot create {branch_type} PR with --base "{actual}".
{branch_type} branches must merge to {expected} per Git Flow.

To fix, re-run with:
  gh pr create --base {expected} {rest_of_args}
```

```
[MISSING_BASE — gh pr create]
✗ BLOCKED: gh pr create on {branch_type} branch requires explicit --base {expected}.

The repo default branch is typically main, which would silently create a wrong-base PR.
Always pass --base explicitly per Git Flow.

To fix, re-run with:
  gh pr create --base {expected} {rest_of_args}
```

## 9. Testing strategy

Three layers, all automated via pytest. No bats dependency (not installed in dev env); pytest drives both the Python hook and the bash function via `subprocess.run`. Every check from the original manual verification list is converted into a test case here — no reliance on developer memory.

Test layout:

```
tests/
├── conftest.py                 # Fixtures: temp git repo, gh stub, hook runner
├── test_helpers.py             # §9.1 — pure-function unit tests
├── test_hook_e2e.py            # §9.2 — end-to-end hook invocation tests
├── test_verify_pr_base_sh.py   # §9.3 — bash function tests
├── fixtures/
│   └── bin/
│       └── gh                  # stub: reads MOCK_GH_STDOUT / MOCK_GH_EXIT env
└── run.sh                      # entrypoint: cd repo root && pytest tests/ -v
```

### 9.1 Pure-function unit tests — `test_helpers.py`

Direct imports, no I/O, no mocks (other than `unittest.mock.patch` on the four shell wrappers when needed):

| Function | Coverage |
| --- | --- |
| `expected_base_for` | feature → develop; hotfix → main; release → main; main/develop/chore/empty → None |
| `parse_base_flag` | `--base develop`, `--base=develop`, `-B develop`, `--base "develop"`, `--base 'develop'`, no flag → None, `--base $VAR` returns literal |
| `parse_pr_number` | `gh pr merge 42`, `gh pr merge --squash 42`, URL form `gh pr merge https://github.com/o/r/pull/7`, no number → None |
| `split_command_chain` | bare cmd, `&&`, `;`, `\|\|`, mixed |
| `check_create` (with patched `current_branch` + `has_develop_branch`) | feature + missing-base → deny MISSING_BASE; feature + `--base main` → deny WRONG_BASE; feature + `--base develop` → allow; hotfix + `--base main` → allow; hotfix + `--base develop` → deny; non-Git-Flow branch → allow; detached HEAD → allow |
| `check_merge` (with patched `pr_refs_for`) | feature/x with develop base → allow; feature/x with main base → deny; gh CalledProcessError → allow; release/x with main base → allow |

### 9.2 End-to-end hook tests — `test_hook_e2e.py`

Each test invokes the real `hooks/check-pr-base.py` script via `subprocess.run`, with stdin = harness JSON payload, in a temp git repo, with a stubbed `gh` on `PATH`. Asserts on exit code (always 0), stdout (deny payload JSON or empty), and stderr.

**Fixtures (`conftest.py`):**

- `temp_git_repo`: pytest fixture that `git init`s a tmp dir, creates branches as requested by the test (`main`, `develop`, `feature/x`, etc.), checks out the requested HEAD branch, returns the path.
- `gh_stub`: pytest fixture that prepends `tests/fixtures/bin` to `$PATH`. The stub `gh` reads `MOCK_GH_STDOUT` (printed verbatim) and `MOCK_GH_EXIT` (numeric exit code, default 0) from env. Test sets these per-call.
- `run_hook(payload, cwd, env)`: helper that invokes `python3 hooks/check-pr-base.py` with the given JSON on stdin, returns `(exit_code, stdout, stderr)`.

**Scenarios covered (each maps to a `def test_*` case):**

| # | Test name | Setup | Assertion |
| --- | --- | --- | --- |
| 1 | `test_create_missing_base_on_feature_blocked` | branch=feature/x, develop exists, cmd=`gh pr create --title t` | exit=0, stdout JSON deny, reason contains "MISSING_BASE" and "feature/x" and `--base develop` |
| 2 | `test_create_wrong_base_on_feature_blocked` | branch=feature/x, cmd=`gh pr create --base main --title t` | exit=0, stdout JSON deny, reason contains "WRONG_BASE" and "main" and `--base develop` |
| 3 | `test_create_correct_base_on_feature_allowed` | branch=feature/x, cmd=`gh pr create --base develop --title t` | exit=0, stdout empty (allow) |
| 4 | `test_merge_wrong_base_blocked` | branch=feature/x, cmd=`gh pr merge 42 --squash`, gh stub → `{"baseRefName":"main","headRefName":"feature/x"}` | exit=0, stdout JSON deny, reason contains "PR #42", "main", "develop", `gh pr edit 42 --base develop` |
| 5 | `test_merge_correct_base_allowed` | branch=feature/x, cmd=`gh pr merge 42`, gh stub → `{"baseRefName":"develop","headRefName":"feature/x"}` | exit=0, stdout empty |
| 6 | `test_create_on_main_branch_allowed` | branch=main, cmd=`gh pr create --base main` | exit=0, stdout empty (not Git Flow) |
| 7 | `test_single_trunk_repo_allowed` | branch=feature/x, develop branch does NOT exist, cmd=`gh pr create --title t` | exit=0, stdout empty (no develop = not Git Flow) |
| 8 | `test_create_hotfix_correct_base_allowed` | branch=hotfix/v1.0.1, cmd=`gh pr create --base main --title t` | exit=0, stdout empty |
| 9 | `test_create_hotfix_wrong_base_blocked` | branch=hotfix/v1.0.1, cmd=`gh pr create --base develop --title t` | exit=0, stdout JSON deny, reason contains "WRONG_BASE" and "main" |
| 10 | `test_create_release_wrong_base_blocked` | branch=release/v1.0, cmd=`gh pr create --base develop --title t` | exit=0, stdout JSON deny |
| 11 | `test_unrelated_command_passthrough` | cmd=`echo hello` | exit=0, stdout empty |
| 12 | `test_gh_failure_fails_open` | branch=feature/x, cmd=`gh pr merge 42`, gh stub exit=2 | exit=0, stdout empty (fail-open) |
| 13 | `test_chained_command_validates_each_segment` | branch=feature/x, cmd=`gh pr create --base main --title t && echo done`, expects deny on first segment | exit=0, stdout JSON deny |
| 14 | `test_quoted_string_with_gh_pr_create_does_not_match` | cmd=`echo "gh pr create --base main"` | exit=0, stdout empty (word-boundary regex) |
| 15 | `test_gh_pr_view_other_subcommand_passthrough` | cmd=`gh pr view 42` | exit=0, stdout empty |
| 16 | `test_draft_create_missing_base_on_feature_blocked` | branch=feature/x, cmd=`gh pr create --draft --title t` | exit=0, stdout JSON deny |
| 17 | `test_internal_exception_fails_open` | malformed stdin (e.g., not JSON) | exit=0, stdout empty, stderr contains traceback |

### 9.3 Bash function tests — `test_verify_pr_base_sh.py`

Drives the bash function via subprocess. Sources the script in a subshell, calls `verify_pr_base "$@"`, captures exit code + stderr.

```python
def run_verify(pr_num, expected_base, gh_stdout, gh_exit=0, env=None):
    script = textwrap.dedent(f"""
        source scripts/git-flow-finish.sh   # sources function defs without invoking main flow
        verify_pr_base "{pr_num}" "{expected_base}"
    """)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=...)
```

(Side note for implementation: `git-flow-finish.sh` will need a small guard — `if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main; fi` — at the bottom so sourcing for tests doesn't trigger the full flow. If that guard isn't already present it's added as part of this change.)

| Test | Setup | Assertion |
| --- | --- | --- |
| `test_verify_pass_on_match` | gh stub → `develop`, expected=develop | exit=0, stderr empty |
| `test_verify_fail_on_mismatch` | gh stub → `main`, expected=develop | exit=1, stderr contains "ABORTING", `PR #42`, `gh pr edit 42 --base develop` |
| `test_verify_fail_open_on_gh_error` | gh stub exit=2 | exit=0 (fail open) |

### 9.4 What is NOT automated (and why)

**`/finish` end-to-end happy path against a real GitHub PR.** The original checklist item 5 ("`/finish` on a correctly-based feature PR completes normally") cannot be automated without a real GitHub remote and PR. It's explicitly *not* a regression risk for this change — `verify_pr_base` returning 0 simply lets `/finish` proceed unchanged. The Layer 1 hook test (#5 in §9.2) and the Layer 2 bash test (`test_verify_pass_on_match`) together cover the only new code paths involved. No manual smoke test required.

### 9.5 Test runner

`tests/run.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
exec python3 -m pytest tests/ -v "$@"
```

Optional follow-up (not part of this issue): add `.github/workflows/ci.yml` that runs `tests/run.sh` on every PR. Listed in §13 as out-of-scope but worth filing as a sub-issue.

### 9.6 Regression coverage for the historical incidents

- obsidian-brain PR #30 (wrong-base merge to main from feature branch) → covered by test #4 (`test_merge_wrong_base_blocked`) and test #2 (`test_create_wrong_base_on_feature_blocked`).
- knowledge-base-ui PR abhattacherjee/harden-repo#10 (same pattern) → same tests.
- Any future regression in either Layer 1 or Layer 2 fails the suite. CI hook-up is recommended but not gating.

## 10. Documentation

- `commands/finish.md`: short note that `/finish` verifies `baseRefName` before merging.
- `README.md` "Quality gates installed" subsection documenting the hook and the validation matrix.
- `CHANGELOG.md` entry under `## [Unreleased]` → `### Added`.

## 11. Release & distribution

- Bump patch version via `scripts/bump-version.sh` (auto-updates `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` in lockstep).
- Implementation lands on `feature/issue-1-pr-base-enforcement` → PR `--base develop` → `/release patch` → tag → push.
- No coordination with harden-repo required for this change — the hook is plugin-bundled and auto-activates on every `git-flow` install.

## 12. Acceptance criteria (from issue, mapped)

- [x] `/finish` verifies `baseRefName` before `gh pr merge` — Layer 2 `verify_pr_base()` in `git-flow-finish.sh`
- [x] Wrong-base detection emits clear `gh pr edit --base` remediation — both layers, shared diagnostic templates (§8)
- [x] Same-PR rule applies to release branches — validation matrix §3 covers `release/*` → `main`
- [x] Backport to all installed-on repos — plugin-bundled hook auto-applies on install; no per-repo work
- [x] Document in README "Quality gates installed" — §10
- [x] Companion: `gh pr create --base` enforcement — Layer 1 `check_create` (added during brainstorm)

## 13. Out of scope

- Auto-rewriting the user's command (block-and-instruct is conservative).
- Sub-issue for harden-repo to also install a project-local copy of the hook (covered by the plugin-bundled hook for any repo using git-flow).
- A dedicated GitHub Action that enforces base on the server side (defense beyond the local CLI; future work).
- Wiring `tests/run.sh` to GitHub Actions CI (this repo currently has no `.github/workflows/`). Tests are runnable locally and via the test-runner script; CI hook-up should be a separate change so this PR stays focused. File a follow-up if desired.
- Replacing the §9.4 end-to-end `/finish` smoke test with a recorded mock-server fixture. Possible but adds significant infrastructure for a path already covered by the unit + e2e tests on the only new code.

## 14. Cross-references

- knowledge-base-ui PR abhattacherjee/harden-repo#17/#18, v0.1.0 release
- knowledge-base-ui retro `claude-insights/2026-05-02-retro-a14f.md`
- obsidian-brain `claude-insights/2026-04-12-pr-merged-to-main-instead-of-develop-wrong-ba-2fd8-error.md`
- obsidian-brain `claude-insights/2026-04-12-git-revert-on-main-silently-poisons-future-release-b5dd-error.md`
- knowledge-base-ui project memory `feedback_never_merge_to_main.md`
- Global CLAUDE.md hard rule (added 2026-05-02)

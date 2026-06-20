# Plan: check-pr-base.py correctness fixes (#18 + #21)

Two independent P1 fixes in `hooks/check-pr-base.py`, both verified against the code. Closes #18 and #21. TDD per task: write the failing test first, watch it fail, then the minimal code.

Files in scope:
- `hooks/check-pr-base.py` (all logic changes)
- `tests/test_hook_e2e.py`, `tests/test_helpers.py` (tests)

Run the suite with: `cd /Users/abhishek/dev/claude_workspace/git-flow && python -m pytest tests/ -q`

---

## Task 1 — #21: allow release/* and hotfix/* to back-merge to develop

**Problem:** `expected_base_for()` (lines 34-42) returns a single base `"main"` for `release/*` and `hotfix/*`, so `gh pr create --base develop` on a release/hotfix branch is denied. Git Flow's back-merge of a release/hotfix into `develop` is therefore impossible via PR (catch-22, requires admin direct push).

**Change:**
1. Rename `expected_base_for` -> `expected_bases_for` and change its return type from `Optional[str]` to `Optional[frozenset[str]]`. The rename is deliberate: it forces every call site to update, so no caller silently compares a set against a string. Mapping:
   - `feature/*` -> `frozenset({"develop"})`
   - `hotfix/*` and `release/*` -> `frozenset({"main", "develop"})`  (main = the release, develop = the back-merge)
   - else -> `None` (sentinel preserved; "not Git Flow -> pass through")
2. Add a helper `canonical_base(bases: frozenset[str]) -> str` returning `"main" if "main" in bases else "develop"`. Use it for the missing-base / wrong-base remediation hints so release/hotfix still suggest `--base main` (the create target).
3. Update the two call sites:
   - `check_create`: the single-trunk passthrough guard (line ~269) `if expected == "develop" and not has_develop_branch(...)` becomes `if bases == frozenset({"develop"}) and not has_develop_branch(...)`. The wrong-base comparison (line ~291) `if actual != expected` becomes `if actual not in bases`. Pass `canonical_base(bases)` to the diag functions.
   - `check_merge`: comparison (line ~322) `if actual_base != expected` becomes `if actual_base not in bases`. Pass `canonical_base(bases)` to `diag_wrong_base_pr`.

**Tests (TDD — write/adjust these first, watch them fail):**
- INVERT (now allow): `tests/test_hook_e2e.py` test #9 `test_create_hotfix_wrong_base_blocked` and #10 `test_create_release_wrong_base_blocked` — rename to `*_develop_now_allowed`, assert allow (`out == ""`).
- INVERT (now allow): `tests/test_helpers.py` `test_check_create_hotfix_develop_denied` (line ~272) and `test_check_create_release_develop_denied` (line ~278) -> `*_allowed`, assert `d.allow is True`.
- UPDATE return-type assertions: `test_expected_base_for_feature` -> `frozenset({"develop"})`; `test_expected_base_for_hotfix` and `test_expected_base_for_release` -> `frozenset({"main","develop"})`; `test_expected_base_for_non_git_flow_returns_none` unchanged. (Rename the function references too.)
- ADD merge back-merge: a `release/*` PR with refs `(develop, release/v1.0)` -> allow.
- KEEP PASSING (regression — must still hold): feature/* `--base main` create still DENY; feature/* `--base develop` allow; release/* `--base main` allow; hotfix/* `--base main` allow; release->main merge allow; feature->main merge deny; hotfix/release missing-base still DENY with the `--base main` remediation hint (e2e #18/#19 and the `*_missing_base_denied` helper tests must pass unchanged).

**Acceptance:** `gh pr create --base develop --head release/vX.Y.Z` and `--head hotfix/...` are allowed; `release/* -> main` and `feature/* -> develop` unchanged; `feature/* -> main` still blocked.

---

## Task 2 — #18: detect gh pr create/merge only at a real command boundary (quote/heredoc-aware)

**Problem:** detection uses whitespace-anchored regexes (lines 340-341) that are NOT quote-aware, so a `git commit -m "...gh pr create..."`, a `gh issue create --body "...gh pr create..."`, or a heredoc body that merely mentions the workflow is wrongly treated as a PR-create/merge invocation and blocked — silently nuking bundled commit/push.

**Change:** add a helper `_invokes_gh_pr(segment: str, subcommand: str) -> bool` that tokenizes the segment with shell-operator-aware shlex and reports True only when `["gh", "pr", subcommand]` appears as consecutive real command tokens at a command boundary:
- Tokenize with `lex = shlex.shlex(segment, posix=True, punctuation_chars=True); lex.whitespace_split = True; tokens = list(lex)`. `punctuation_chars=True` emits shell operators (`| || & && ; ( ) < >`) as their own tokens while keeping quoted strings as single tokens (quotes stripped in posix mode).
- A command boundary = the triple starts at index 0, OR is immediately preceded by a control-operator token (one of `| || & && ; ;; ( ) { }` etc.). Also skip any leading `VAR=val` environment-assignment tokens before index 0.
- On `ValueError` (unbalanced quotes / heredoc shlex can't parse) FALL BACK to the existing legacy regex (`_GH_PR_CREATE_RE` / `_GH_PR_MERGE_RE`) on `" " + segment`. This preserves today's detection power exactly, so #18 can only REMOVE false positives, never introduce a new false negative beyond today's baseline.
- DO NOT extend `split_command_chain` / `_CHAIN_RE` to split single `|`. Splitting on `|` is not quote-aware and would split inside quoted `--body` spans, producing an unbalanced fragment that then hits the regex fallback and re-triggers the very false positive this task removes. Handle pipes inside `_invokes_gh_pr` via the operator-token boundary check instead.

Wire `dispatch()` to use `_invokes_gh_pr(segment, "create")` / `_invokes_gh_pr(segment, "merge")` as the sole gate (the legacy regexes remain only as the ValueError fallback inside the helper).

**Fail-open paths to preserve unchanged:** the `main()` top-level `except Exception -> exit 0`; all `check_create`/`check_merge` `Decision(allow=True)` fall-throughs (detached HEAD, non-Git-Flow, gh failure, shell-var base).

**Tests (TDD — write these first, watch them fail):**
- Unit on the helper: `_invokes_gh_pr('git commit -m "gh pr create"', "create") is False`; `_invokes_gh_pr('gh pr create --base develop', "create") is True`; `_invokes_gh_pr('VAR=1 gh pr create', "create") is True`; `_invokes_gh_pr('foo | gh pr create', "create") is True`.
- E2E false-positive guards (all expect ALLOW / `out == ""`, run on a `feature/foo` head where a false match would otherwise deny):
  1. `git commit -m "wip: do not run gh pr create --base main yet"`
  2. `gh issue create --title x --body "next: gh pr create --base main"`
  3. heredoc body mention (a `gh issue create --body "$(cat <<'EOF' ... gh pr create --base main ... EOF)"` form)
- E2E true-positive must-still-DENY (regression):
  4. `gh pr create --base main --title t` on feature/foo -> deny
  5. chained `... && gh pr create --base main` -> deny (existing #13)
  6. `cd /repo && gh pr create --title t` -> deny (existing cd-prefix test)
- KEEP existing passthrough tests: `gh pr view 42`, `gh pr edit 42 --base develop`, `echo "gh pr create --base main"` all allow.

**Acceptance:** mentions of the workflow inside commit messages, `--body` args, and heredoc bodies no longer block; real `gh pr create` / `gh pr merge` invocations (including chained and cd-prefixed) are still validated/blocked exactly as before.

---

## Sequencing & done criteria
- Do Task 1, then Task 2 (independent, but sequential to avoid edit conflicts in the same file).
- Each task: failing test first (watch it fail for the right reason — beware the vacuous-test trap where a fallback path shadows the asserted path), then minimal code, then full suite green.
- Whole-branch done: `python -m pytest tests/ -q` green; no behavior change to feature/* enforcement or any fail-open path beyond the two intended fixes.

# /finish from a linked worktree (#32) — design and plan

Issue: abhattacherjee/git-flow#32. Acceptance criteria live in the issue body.

## Problem

`/finish` checks out the target branch (`develop` or `main`) in the current
worktree. When the run starts in a linked worktree and the main checkout holds
that branch, git exits 128 with `fatal: '<branch>' is already used by worktree`.
The PR is merged remotely, but the main checkout never gets the merge. Plugins
loaded from a directory-source checkout then run stale code.

Sites:
- `scripts/git-flow-finish.sh` (release/hotfix): `merge_main_via_pr` (source checkout,
  then `git checkout main; git pull`), Phase 1 `git checkout main; git pull`,
  Phase 4 `git checkout develop; git pull`, then Phase 5/6/8 use the local `develop`/`main` refs.
- `commands/finish.md`: feature PR path (`gh pr merge --delete-branch` +
  `git checkout develop && git pull`), feature local fallback, release section
  (`checkout main`, `checkout develop`).

## Design

**Terms.** *Main checkout* = the first `worktree` entry of
`git worktree list --porcelain` (git always lists it first). *Linked mode* =
`git rev-parse --show-toplevel` differs from the main checkout path. Outside
linked mode every command stays byte-identical to today.

**Script, linked mode.**
- `enter_target <branch>` replaces each `git checkout <b>; git pull origin <b>` pair.
  Normal mode: runs exactly that pair, sets `TARGET_REF=<b>`.
  Linked mode: `git fetch origin <b>` then `git checkout --detach origin/<b>`, sets
  `TARGET_REF=HEAD`. The branch is never taken, so no worktree conflict can occur.
- Merges and the bump commit happen on HEAD as today. Pushes use the ref variable:
  `push_main_or_fallback` pushes `"$MAIN_REF:main"`; Phase 6 calls
  `push_ref "develop" "$DEVELOP_REF"`. In normal mode those are `main`/`develop`,
  so the command lines are unchanged.
- New phase **SYNC MAIN CHECKOUT** (linked mode only) after CLEANUP, before VERIFICATION:
  `sync_main_checkout main` then `sync_main_checkout develop`. Runs after every push,
  so a sync problem can never leave a half-release (#37).
- VERIFICATION in linked mode: "on develop" becomes "HEAD equals origin/develop";
  local-vs-remote checks use the refs after sync, and a branch left unsynced on
  purpose is `log_skip` (already warned), not `log_fail`.
- `print_finish_summary` prints each sync warning again at the end (they are
  the operator's to-do list), via a `SYNC_WARNINGS` array.

**`sync_main_checkout <branch>`** (never exits non-zero, never aborts):
1. `holder` = path of the worktree whose `branch` line is `refs/heads/<branch>`, else empty.
2. No holder: fast-forward the shared local ref with `git fetch origin <b>:<b>`
   (git refuses a non-fast-forward and a checked-out branch). Failure → warning.
   If `<b>` is `develop` and the main checkout is on some other branch, add a warning:
   `main checkout <path> is on <x>, not develop; to use the merge run: git -C <path> switch develop`.
3. Holder is the main checkout:
   - path missing or `prunable` → warning with `git worktree prune`.
   - `git -C <path> status --porcelain --untracked-files=no` non-empty → warning
     `main checkout <path> has uncommitted changes; left <b> as is. Run: git -C <path> pull --ff-only origin <b>`.
   - else `git -C <path> pull --ff-only origin <b>`; failure (stderr shown) → same warning.
4. Holder is another linked worktree → warning naming that path and the same command.
Untracked files do not count as dirty: a fast-forward that would clobber one
fails, and that failure becomes the warning.

Never moves a checked-out branch from outside its worktree (`update-ref`,
`branch -f`). Bash 3.2-safe parsing (`while read`, no `mapfile`, no `${x^}`).

**finish.md.**
- New step **2b. Worktree pre-check** defining `MAIN_CHECKOUT`, `LINKED`, and a
  `sync_main_checkout` shell function, byte-identical to the script's function
  body, between `<!-- sync-main-checkout:begin -->` / `<!-- sync-main-checkout:end -->` markers.
- Feature PR path: when `LINKED=false`, unchanged. When `LINKED=true`:
  `gh pr merge $PR_NUMBER --squash --delete-branch=false`, then
  `gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/$CURRENT_BRANCH"`,
  then `sync_main_checkout develop`, `git fetch --prune origin`, then print the
  cleanup commands (`git -C "$MAIN_CHECKOUT" worktree remove <this worktree>`,
  `git -C "$MAIN_CHECKOUT" branch -D $CURRENT_BRANCH`): the branch is checked out
  here, so it cannot be deleted from here.
- Feature local fallback and release/hotfix sections: in linked mode, detach at
  `origin/<b>` instead of checkout, push `HEAD:<b>`, then `sync_main_checkout <b>`.
  Release/hotfix in linked mode points at `scripts/git-flow-finish.sh` when the repo has it.
- `skills/git-flow/SKILL.md` "Stale worktrees" gotcha and `README.md:180`:
  split stale vs active worktree; active is handled by `/finish`.

## Failure modes (self-check classes)

- **Fail-open paths:** sync must never abort. Test: dirty main checkout → script
  exits 0, release fully on origin, warning printed. Test: `pull --ff-only` fails
  (main checkout's develop diverged locally) → exit 0 + warning. Prunable holder → warning.
- **Exit codes:** script exit 0 on all linked-mode sync outcomes; non-worktree
  exit codes unchanged (existing suite, unedited).
- **Date boundaries:** n/a — no dates computed.
- **Doc counts:** CHANGELOG entry names no counts; finish.md step numbers re-read after edit.
- **Broken Markdown:** finish.md fences balanced; test extracts the marker block and runs `bash -n` on it.
- **Unrelated changes:** none; non-worktree command lines byte-identical (test diffs the argv log / pinned commands).
- **Inputs:** reads `git worktree list --porcelain` (source of truth for holders; first entry is main checkout),
  `git status --porcelain`, `origin/<b>`. Stale input: a prunable worktree entry → test.
  Two copies of `sync_main_checkout` (script, finish.md): test asserts the bodies are byte-identical.
- **Bypass routes (the pre-check is a guard):** cheapest wrong pass = a sync that
  "succeeds" while pushing the stale local branch in detached mode (no-op push).
  Test asserts origin state: release commit on `origin/main`, back-merge + bump on
  `origin/develop`, tag points at `origin/main`'s merge commit; and main checkout's
  `develop` == `origin/develop`. Second: the new worktree test must fail on the
  unmodified script with `already used by worktree` (run before the fix, recorded).

## Task 1: script linked-mode support + tests

Delegate: codex — self-contained: one script plus one new test file, checked by pytest.

Files: `scripts/git-flow-finish.sh`, new `tests/test_finish_worktree_e2e.py`.

Functions to add above the sourcing guard: `main_checkout_path`, `worktree_holding <branch>`
(prints path or nothing), `is_linked_worktree` (exit 0 when linked), `enter_target <branch>`
(sets `TARGET_REF`), `sync_main_checkout <branch>` (appends to `SYNC_WARNINGS`, always returns 0).
Globals: `LINKED`, `MAIN_CHECKOUT`, `MAIN_REF`, `DEVELOP_REF`, `SYNC_WARNINGS`.

Tests (reuse the `_live_run` harness shape from `tests/test_finish_live_e2e.py`: bare
origin, `tests/fixtures/bin` gh stub, `GIT_FLOW_CHECKS_GRACE=0`): main checkout `repo/`
on develop, linked worktree `repo--rel/` on `release/v1.2.3`; run from the linked worktree.
- clean main checkout on develop → exit 0; no "already used by worktree" in output;
  `origin/main` contains the release commit; `origin/develop` contains back-merge and
  bump; main checkout `develop` == `origin/develop`; local `main` == `origin/main`.
- dirty main checkout (modified tracked file) → exit 0; main checkout HEAD and file unchanged;
  output names the path and `git -C <path> pull --ff-only origin develop`.
- main checkout on another branch (`feature/x`) → not switched, warning names `git -C <path> switch develop`; local `develop` == `origin/develop`.
- blocked push to main (pre-receive hook) in linked mode → PR fallback path works (gh argv log has `pr merge`), no checkout error.
- unit: source the script, `worktree_holding develop` returns the main checkout path; returns empty for an unheld branch; `main_checkout_path` from inside the linked worktree returns `repo/`.
Existing `tests/test_finish_live_e2e.py` and `tests/test_finish_dry_run_e2e.py` pass with no edits.

Check: `python3 -m pytest tests/ -q`

## Task 2: finish.md linked-mode steps + test

Delegate: codex — markdown template plus one test file; checked by pytest.

Files: `commands/finish.md`, new `tests/test_finish_md_worktree.py`.

- Add step 2b and the guarded paths described in Design.
- Test extracts the block between the markers, asserts its `sync_main_checkout`
  function body equals the one in `scripts/git-flow-finish.sh` (byte-identical after
  extracting both with the same regex), runs `bash -n` on it, and runs it against the same
  three main-checkout states (clean / dirty / other branch) for `develop`.
- Test asserts the non-linked feature PR command line is still
  `gh pr merge $PR_NUMBER --squash --delete-branch` followed by `git checkout develop && git pull origin develop`.

Check: `python3 -m pytest tests/ -q`

## Task 3: docs + CHANGELOG

Delegate: claude — three short prose edits; no test command beyond preflight.

Files: `skills/git-flow/SKILL.md` (Stale worktrees gotcha), `README.md:180`, `CHANGELOG.md` `[Unreleased] ### Fixed`.

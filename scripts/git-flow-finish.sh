#!/usr/bin/env bash
# git-flow-finish.sh — Complete Git Flow finish with merge, tag, and push
# Handles: merge to main + tag + GitHub Release + merge to develop + version bump + cleanup
#
# Installed by /harden-repo
#
# Usage:
#   ./scripts/git-flow-finish.sh <branch-type> <version> [--skip-changelog] [--dry-run]
#
# Examples:
#   ./scripts/git-flow-finish.sh hotfix v1.0.6
#   ./scripts/git-flow-finish.sh release v2.0.0
#   ./scripts/git-flow-finish.sh hotfix v1.0.6 --dry-run
#   ./scripts/git-flow-finish.sh hotfix v1.0.6 --skip-changelog

set -eu

# ── Constants ──────────────────────────────────────────────────────
REPO=""
BRANCH_TYPE=""
VERSION=""
VERSION_NUMBER=""
SOURCE_BRANCH=""
# Computed in the bump phase, below the sourcing guard. Initialised here so
# print_finish_summary is callable from a test under `set -u`. The empty string
# is not a valid version, and the summary would report it as "develop bumped
# to " with nothing after it — so the e2e runs assert the printed VALUE rather
# than leaving that to `set -u`. Deliberately not asserted inside
# print_finish_summary: that function runs after main is merged, tagged,
# pushed and released, so aborting there would fail a release that had already
# completed, and the EXIT handler would then warn that /finish "did not
# complete" about a run that did.
NEXT_VERSION=""
# Set from BRANCH_TYPE during argument parsing, below the sourcing guard, and
# read by create_github_release and merge_main_via_pr — both of which live
# ABOVE the guard and are called directly by tests. Declared here so the script
# owns it: a test that has to hand-set a global before calling a function is
# supplying something the script should supply, and every time that has been
# true in this file it hid a real gap.
BRANCH_TYPE_CAPITALIZED=""
DRY_RUN=false
SKIP_CHANGELOG=false
MERGE_VIA_PR=false
# Set true when the CI gate returned 3 (no checks ever registered, so nothing
# was gated). The ⚠️ two-liner wait_for_ci_checks prints scrolls hundreds of
# lines off the top by the time /finish ends, and the closing summary read as
# an unqualified success — so the warning is reprinted from this flag.
CI_GATE_SKIPPED=false
# Set true only once the squash merge to main is CONFIRMED — after gh returned
# success, not before it was called. The ungated-merge warning tells the
# operator what state main is in, and it may only say "main already has this
# release" when something actually observed that.
#
# Two aborts sit between the CI gate and that confirmation, and they are NOT
# equivalent. The wrong-base guard fires before gh is called at all, so main
# is untouched. A `gh pr merge` failure is not that: gh can fail on a lost or
# timed-out response to a merge the server already performed, so main may well
# have moved. False in that direction is the reason the warning never asserts
# main is clean either — see warn_if_ci_gate_skipped.
MAIN_MERGED=false

# ── Functions ──────────────────────────────────────────────────────

# ---------------------------------------------------------------------------
# verify_pr_base — abort if a PR's baseRefName does not match the expected base.
# Layer 2 of the PR base-branch enforcement (Layer 1 is hooks/check-pr-base.py).
# Fails open on gh errors so the hook layer remains the source of truth.
# Spec: docs/superpowers/specs/2026-05-02-pr-base-enforcement-design.md
# ---------------------------------------------------------------------------
# Path verify_pr_base captures gh's stderr into, empty when there is none. A
# global rather than a local so the EXIT trap can be armed before the file is
# created and still find the path once it exists.
VERIFY_PR_BASE_TMP=""

# rm_verify_pr_base_tmp — trap body. Removes the tempfile if one was created.
# Reads the path at fire time, so nothing has to be quoted into a trap string.
rm_verify_pr_base_tmp() {
  if [[ -n "${VERIFY_PR_BASE_TMP:-}" ]]; then
    rm -f -- "$VERIFY_PR_BASE_TMP"
  fi
}

# cleanup_gh_stderr — remove the tempfile on the normal path, so it is gone
# before the run ends rather than only at exit.
#
# It does NOT touch traps. The EXIT trap is armed once, globally, at the top of
# this script and stays armed for the whole run; the save/restore dance this
# function used to do was what made a warning handler hung off EXIT
# unrecoverable (see git_flow_finish_on_exit).
#
# If the file cannot be removed, VERIFY_PR_BASE_TMP is deliberately left set so
# the EXIT trap gets another attempt, and the reason is printed rather than
# swallowed. Always returns 0, so a cleanup problem cannot abort /finish under
# `set -e`.
cleanup_gh_stderr() {
  if [[ -n "${VERIFY_PR_BASE_TMP:-}" ]]; then
    if ! rm -f -- "$VERIFY_PR_BASE_TMP"; then
      echo "⚠️  verify_pr_base: could not remove ${VERIFY_PR_BASE_TMP}; leaving the exit trap armed." >&2
      return 0
    fi
    VERIFY_PR_BASE_TMP=""
  fi
  return 0
}

# warn_if_ci_gate_skipped — print the "nothing gated this" warning when /finish
# ends without reaching the closing summary.
#
# handle_ci_gate_result sets CI_GATE_SKIPPED at the PR-merge step, and the
# summary that reports it is ~360 lines later: two `|| die` calls plus a run of
# unguarded commands under `set -e` (tag, checkout develop, pull, merge --no-ff,
# fetch, bump-version.sh, add, commit). A back-merge conflict on develop is
# ordinary. Without this handler the only trace of an ungated release is the ⚠️
# from wait_for_ci_checks, hundreds of lines up the scrollback — which is the
# exact problem the flag was added to solve.
#
# It does NOT gate on the exit status. `$?` inside an EXIT trap is 0 when the
# script is killed by a signal (measured: SIGTERM and SIGHUP both arrive here
# as 0, though the script itself exits 143 and 129), and a dropped SSH session
# while /finish blocks in `gh pr checks --watch` is precisely the case this
# warning exists for. CI_GATE_SKIPPED alone decides whether to print; `rc` only
# supplies the "(exit N)" detail, which is omitted when there is none.
#
# The one thing that must stay true on both branches is what it says about
# main. MAIN_MERGED is the only evidence that the squash merge landed, so the
# not-merged wording claims nothing either way: a signal landing between gh's
# server-side merge and the log_ok that records it leaves the flag false while
# main has in fact moved.
#
# print_finish_summary clears CI_GATE_SKIPPED only when its own copy of the
# warning was actually WRITTEN, so a completed run warns exactly once and a run
# whose stdout is gone still warns here. stdout and stderr are separate
# descriptors: `finish | head` closes the first and leaves the second healthy,
# and clearing the flag on a message that reached nobody would leave an ungated
# merge to main entirely unreported.
warn_if_ci_gate_skipped() {
  local rc="$1"
  ${CI_GATE_SKIPPED:-false} || return 0
  local detail=""
  if [[ "$rc" -ne 0 ]]; then
    detail=" (exit ${rc})"
  fi
  echo "" >&2
  echo "⚠️  NO CI GATE RAN, and /finish did not complete${detail}." >&2
  echo "    No checks ever registered on the PR to main, so nothing gated" >&2
  echo "    ${VERSION:-this release}." >&2
  if ${MAIN_MERGED:-false}; then
    echo "    It WAS squash-merged to main: that merge is already on the remote" >&2
    echo "    and is NOT undone by this failure. Check the build on main." >&2
  else
    echo "    /finish stopped before it could confirm the merge to main. Check" >&2
    echo "    whether main already carries ${VERSION:-this release} before you" >&2
    echo "    retry, revert or force-push anything." >&2
  fi
  return 0
}

# git_flow_finish_on_exit — the script's ONE EXIT handler, armed below.
#
# It was previously verify_pr_base's job to arm and disarm an EXIT trap around
# its own tempfile, saving and restoring whatever the caller had. That made
# arming any other EXIT handler unsafe: cleanup_gh_stderr returns early when
# `rm` fails, without restoring, so a handler armed by the caller was silently
# dropped on that path (measured: normal path restored the caller's trap and
# the warning fired; the rm-failure path left `rm_verify_pr_base_tmp` armed and
# the warning never ran). Arming one handler here instead is strictly more
# coverage: only `set -eu`, the variable initialisations and the function
# definitions run ahead of it, none of which can create a tempfile or set
# CI_GATE_SKIPPED — so it is live before anything it has to clean up can exist,
# which is earlier than verify_pr_base could ever manage. It also removes the
# whole class of trap-composition bugs.
#
# `local rc=$?` must be the first statement: it is the status the script is
# exiting with, and any command run before it would overwrite it.
#
# `|| true` on BOTH calls is load-bearing. Errexit is live inside an EXIT trap,
# so a handler whose call fails never reaches its next line. Each call can fail
# for its own reason: rm_verify_pr_base_tmp ends in a bare `rm -f`, which fails
# when the path is a directory or its parent is read-only; and
# warn_if_ci_gate_skipped ends in `echo`s to stderr, which fail when stderr is
# closed — a `/finish 2>&-`, or a dropped pty. Either one, unguarded, both
# swallowed what came after it and rewrote the status the script was exiting
# with (measured: an abort that exits 2 came out as 1).
#
# Guarded at the call site rather than inside each callee, so the guarantee
# lives at the point that depends on it: whatever any callee does, the rest of
# this handler still runs and `return 0` still preserves `rc`.
git_flow_finish_on_exit() {
  local rc=$?
  rm_verify_pr_base_tmp || true
  warn_if_ci_gate_skipped "$rc" || true
  return 0
}

# EXIT is the whole mechanism and the only trap wanted here. Bash runs an EXIT
# trap on the way out, including when it dies from a fatal signal. The signal
# shows in the SCRIPT's exit status (143 for TERM, 129 for HUP) but NOT in the
# `$?` the handler sees, which is 0 — hence warn_if_ci_gate_skipped not gating
# on it. A RETURN trap would not cover this: under a signal verify_pr_base
# never returns.
#
# Do NOT add TERM or HUP. Trapping them without re-raising makes the shell
# survive the signal and resume, and the statement right after verify_pr_base
# is `gh pr merge --squash` against main — so a dropped SSH session would merge
# to main and exit 0. EXIT alone cleans up and still lets the signal kill.
#
# Armed at the top level, so it is also armed when this file is SOURCED by a
# test — which is how the tempfile-under-signal tests reach it at all.
trap 'git_flow_finish_on_exit' EXIT

verify_pr_base() {
  local pr_num="$1" expected_base="$2"
  local actual_base gh_stderr
  # Cleanup is already armed: git_flow_finish_on_exit is trapped on EXIT at the
  # top of this script, before any function here can run, so it is live before
  # mktemp rather than around it. Creating the file first and arming after
  # leaves a window where it exists with nothing guarding it, and a signal
  # landing there still leaks — which is the very bug this closes. This
  # function therefore installs no trap of its own, and nothing here may
  # install one: an EXIT trap set here would clobber the global handler.

  # Take the NAME first, then create the file. `VAR=$(mktemp ...)` looks atomic
  # but is not: mktemp's child creates the file and only then does the parent
  # finish the assignment, so a signal landing in between runs the trap while
  # VERIFY_PR_BASE_TMP is still empty and the file survives. Measured at roughly
  # 1 kill in 5, and 25 out of 25 once that gap is widened by 50ms.
  #
  # `mktemp -u` hands back an unused name without leaving a file, so the
  # variable is set before anything exists on disk and the trap can always
  # remove it. Creating it under `set -C` gives O_EXCL, which refuses to follow
  # a symlink planted at that name, and `umask 077` keeps gh's stderr — which
  # can carry token material — readable only by us. If that create loses the
  # race and fails, we fall back to /dev/null like any other mktemp failure.
  VERIFY_PR_BASE_TMP=$(mktemp -u "${TMPDIR:-/tmp}/verify_pr_base.XXXXXX" 2>/dev/null) || VERIFY_PR_BASE_TMP=""
  if [[ -n "$VERIFY_PR_BASE_TMP" ]] && ! ( umask 077; set -C; : > "$VERIFY_PR_BASE_TMP" ); then
    VERIFY_PR_BASE_TMP=""
  fi
  if [[ -n "$VERIFY_PR_BASE_TMP" ]]; then
    gh_stderr="$VERIFY_PR_BASE_TMP"
  else
    gh_stderr=/dev/null
  fi
  if ! actual_base=$(gh pr view "$pr_num" --json baseRefName --jq '.baseRefName' 2>"$gh_stderr"); then
    # gh failure: emit a diagnostic so post-hoc forensics survive, then
    # fail open and trust the hook layer to enforce. Header prints
    # unconditionally so the breadcrumb is visible even when mktemp failed.
    echo "⚠️  verify_pr_base: gh pr view #${pr_num} failed; relying on hook layer." >&2
    if [[ "$gh_stderr" != "/dev/null" && -s "$gh_stderr" ]]; then
      sed 's/^/    /' "$gh_stderr" >&2
    elif [[ "$gh_stderr" == "/dev/null" ]]; then
      echo "    (gh stderr unavailable: mktemp failed)" >&2
    fi
    cleanup_gh_stderr
    return 0
  fi
  cleanup_gh_stderr
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

usage() {
  cat <<'USAGE'
Usage: git-flow-finish.sh <branch-type> <version> [options]

Arguments:
  branch-type   "hotfix" or "release"
  version       Version with v prefix (e.g., v1.0.6)

Options:
  --skip-changelog  Skip changelog promotion (already done by sub-agents)
  --dry-run         Show what would be done without executing
  --help, -h        Show this help message

Examples:
  git-flow-finish.sh hotfix v1.0.6
  git-flow-finish.sh release v2.0.0
  git-flow-finish.sh hotfix v1.0.6 --skip-changelog --dry-run

What this script does:
  1. Merges the source branch to main (--no-ff)
  2. Creates annotated tag on main (local)
  3. Pushes main + creates GitHub Release (tag + release notes from CHANGELOG.md)
  4. Merges source branch back to develop (--no-ff)
  5. Bumps develop to next patch version
  6. Pushes develop to remote
  7. Deletes source branch (local + remote)
  8. Verifies final state (including GitHub Release)

Push Strategy:
  Direct git push. The prevent-direct-push.py hook detects Git Flow
  merge context (release/hotfix in commit history) and allows the push.

USAGE
  exit 0
}

log() { echo "  $1"; }
log_ok() { echo "  ✅ $1"; }
log_fail() { echo "  ❌ $1"; }
log_skip() { echo "  ⏭️  $1"; }
log_phase() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  $1"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

die() { log_fail "$1"; exit 1; }

log_repo_context() {
  local toplevel remote
  toplevel=$(git rev-parse --show-toplevel 2>/dev/null) || toplevel="(not a git repo)"
  # Strip any user[:pass]@ userinfo so embedded credentials are not logged.
  # [^/]* (not [^/@]*) consumes a literal '@' in the password up to the last
  # '@' before the path, so passwords containing '@' don't leak their tail.
  if remote=$(git config --get remote.origin.url 2>/dev/null); then
    remote=$(printf '%s' "$remote" | sed -E 's#://[^/]*@#://#')
  else
    remote="no remote"
  fi
  log_phase "REPO CONTEXT"
  log "Repo:   $(basename "$toplevel")"
  log "Path:   $toplevel"
  log "Remote: $remote"
}

prune_stale_refs() {
  if $DRY_RUN; then
    log_skip "DRY-RUN: would run git fetch --prune origin"
    return 0
  fi
  if git fetch --prune origin; then
    log_ok "Pruned stale remote-tracking refs"
  else
    log_fail "git fetch --prune origin failed; stale refs may remain"
  fi
}

# Push a local branch to a remote ref.
# The prevent-direct-push.py hook detects Git Flow merge context and allows these pushes.
# Args: $1 = target ref (e.g., "main" or "develop"), $2 = local branch to push from
push_ref() {
  local TARGET_REF="$1"
  local LOCAL_BRANCH="$2"
  local COMMIT_SHA
  COMMIT_SHA=$(git rev-parse "$LOCAL_BRANCH")

  if $DRY_RUN; then
    log "[dry-run] Would push $LOCAL_BRANCH ($COMMIT_SHA) to origin/$TARGET_REF"
    return 0
  fi

  git push origin "$LOCAL_BRANCH:$TARGET_REF" || die "Failed to push to origin/$TARGET_REF"
  git fetch origin "$TARGET_REF" 2>/dev/null
  log_ok "Pushed to origin/$TARGET_REF ($COMMIT_SHA)"
}

# Create a GitHub Release (which also creates the tag on the remote)
create_github_release() {
  local TAG_NAME="$1"
  local NOTES_FILE

  if $DRY_RUN; then
    log "[dry-run] Would create GitHub Release $TAG_NAME on main"
    return 0
  fi

  NOTES_FILE=$(mktemp)

  # Extract the version's section from CHANGELOG.md
  if [[ -f CHANGELOG.md ]]; then
    awk -v ver="## [$VERSION_NUMBER]" '{
      if (index($0, ver) == 1) { found = 1; next }
      if (found == 1 && $0 ~ /^## \[/) exit
      if (found == 1) print
    }' CHANGELOG.md > "$NOTES_FILE"

    # Strip empty leading lines
    if [[ -s "$NOTES_FILE" ]]; then
      sed -i.bak '/./,$!d' "$NOTES_FILE" && rm -f "${NOTES_FILE}.bak"
    fi
  fi

  # Log extraction result for debugging
  if [[ -s "$NOTES_FILE" ]]; then
    local NOTES_LINES
    NOTES_LINES=$(wc -l < "$NOTES_FILE" | tr -d ' ')
    log "Extracted $NOTES_LINES lines from CHANGELOG.md"
  fi

  # Fallback if CHANGELOG.md missing or extraction yielded nothing
  if [[ ! -s "$NOTES_FILE" ]]; then
    cat > "$NOTES_FILE" <<EOF
${BRANCH_TYPE_CAPITALIZED} ${TAG_NAME}

See CHANGELOG.md for full details.
EOF
    log "Using fallback release notes (CHANGELOG.md extraction empty)"
  fi

  # gh release create also creates the tag on the remote — no separate API call needed
  if gh release create "$TAG_NAME" \
    --repo "$REPO" \
    --target main \
    --title "${BRANCH_TYPE_CAPITALIZED} ${TAG_NAME}" \
    --notes-file "$NOTES_FILE" 2>&1; then
    log_ok "Created GitHub Release $TAG_NAME"
  else
    # If the release already exists (e.g., partial re-run), update it
    log "Release $TAG_NAME may already exist, attempting update..."
    if ! gh release edit "$TAG_NAME" \
      --repo "$REPO" \
      --title "${BRANCH_TYPE_CAPITALIZED} ${TAG_NAME}" \
      --notes-file "$NOTES_FILE" 2>&1; then
      rm -f "$NOTES_FILE"
      die "Failed to create or update GitHub Release for $TAG_NAME"
    fi
    log_ok "Updated existing GitHub Release $TAG_NAME"
  fi

  # Verify release body is populated (catches silent failures where notes weren't applied)
  local BODY_LENGTH
  BODY_LENGTH=$(gh release view "$TAG_NAME" --repo "$REPO" --json body --jq '.body | length' 2>/dev/null || echo "0")
  if [[ "$BODY_LENGTH" -lt 50 ]]; then
    log "Release body appears empty or minimal ($BODY_LENGTH chars), re-applying notes..."
    gh release edit "$TAG_NAME" \
      --repo "$REPO" \
      --notes-file "$NOTES_FILE" 2>&1 || log_fail "Failed to re-apply release notes"
    log_ok "Re-applied release notes to $TAG_NAME"
  fi

  rm -f "$NOTES_FILE"
}

# wait_for_ci_checks — block until a PR's checks finish.
#   0  every check passed
#   1  gh did not report all checks passing — a failing check, or gh itself
#      failing (auth, rate limit, an unknown flag)
#   3  no checks registered after a bounded wait — nothing to gate on
#
# GIT_FLOW_CHECKS_GRACE — seconds to wait for check runs to register before
# concluding a repo has no CI (default 60; set 0 to skip the wait on a repo
# you know has no CI).
# GIT_FLOW_CHECKS_POLL — seconds between polls inside that wait (default 5).
#
# Both are validated as whole numbers before use. Unvalidated, they were not
# merely sloppy: on bash 3.2 `GIT_FLOW_CHECKS_GRACE=1e9` makes every `[[ $waited
# -ge $grace ]]` fail with "value too great for base" and compare false, so
# /finish spins forever, holding the release branch open with the PR to main
# created but never merged (merge_main_via_pr resets local main to origin/main
# before it gets here, so neither local nor remote main carries the merge at
# that point); `abc` kills the script with "unbound variable" without ever
# naming the variable; and `-5` skips the gate outright. A zero poll is
# rejected too — it turns the wait into a busy-loop hammering the API.
#
# A leading zero is rejected as well, and that is not pedantry: bash reads
# `08` and `010` as octal. `GIT_FLOW_CHECKS_GRACE=08` errors "value too great
# for base" on every `[[ $waited -ge $grace ]]` — non-fatal inside `[[ ]]`, and
# it evaluates FALSE, so /finish hangs exactly like `1e9`. `POLL=08` is worse
# in a different way: the same error in `waited=$((waited + 08))` is FATAL, so
# the script dies after one sleep. `POLL=010` is valid octal 8, so the sleep is
# a true 10 seconds but `waited` advances by 8 — at the default grace of 60
# that is 80 seconds of waiting, not 60. And `POLL=00` slips past the `0)` arm
# below and busy-loops the API. `0?*` matches any value with a character after
# a leading zero, which leaves plain `0` accepted by this arm — legitimate for
# GRACE (gate immediately), and rejected two lines below for POLL, which needs
# its own `0)` arm because a zero poll busy-loops.
#
# The gate call's output is deliberately not redirected. This gate is the last
# thing before the squash merge to main, and hiding it is what kept a dead
# gate invisible for four months (#27): the flag passed here used to be
# `--fail-any`, which no gh has ever had, so every run died on `unknown flag`
# and was reported to the operator as "CI checks failed" against a perfectly
# green build.
#
# GitHub registers check runs a few seconds after a PR opens, and
# `gh pr checks --watch` does NOT wait for them to appear — it reports on the
# runs it can already see and returns immediately (measured: 0s, exit 1) when
# there are none. So "no checks reported" straight after opening a PR means
# "not registered yet" far more often than "this repo has no CI". Only a
# bounded wait tells those apart, and getting it wrong merges to main with no
# gate at all. The grace loop below runs the classification call (no
# `--watch`) on its own, discarding that call's own exit code — its stderr is
# what decides whether to keep waiting, not its exit status.
#
# The classification call captures STDERR ONLY (`2>&1 >/dev/null` — order
# matters: dup stderr onto the capture first, THEN send stdout to
# /dev/null). Without `--watch`, gh renders a full checks TABLE on stdout,
# and "no checks reported on ..." is a separate diagnostic gh prints on
# stderr. That table's rows come from whatever app posted the commit
# status — a check's name or description is third-party text. Capturing
# both channels together (plain `2>&1`) means a check merely named or
# described "no checks reported" makes this treat a genuinely red build as
# "nothing to gate on" and wave it into the squash merge to main.
#
# `--fail-fast` requires `--watch` (gh rejects it on its own) and stops waiting
# once something is already red. gh checks Failed before Pending, so with
# `--watch` this returns 1 for a red build and never 8; exit 8 cannot happen
# here — the gate call always runs with `--watch`.
wait_for_ci_checks() {
  local pr_num="$1" repo="$2" detail waited=0
  # `-` not `:-`: an explicitly empty GIT_FLOW_CHECKS_GRACE= is a mistake worth
  # naming, not something to silently paper over with the default.
  local grace="${GIT_FLOW_CHECKS_GRACE-60}" poll="${GIT_FLOW_CHECKS_POLL-5}"

  case "$grace" in
    ''|*[!0-9]*|0?*) die "GIT_FLOW_CHECKS_GRACE must be a whole number of seconds with no leading zeros, got '${grace}'." ;;
  esac
  case "$poll" in
    ''|*[!0-9]*|0?*) die "GIT_FLOW_CHECKS_POLL must be a whole number of seconds with no leading zeros, got '${poll}'." ;;
    0) die "GIT_FLOW_CHECKS_POLL must be at least 1 second; 0 would busy-loop." ;;
  esac

  while :; do
    detail=$(gh pr checks "$pr_num" --repo "$repo" 2>&1 >/dev/null) || true
    case "$detail" in
      *"no checks reported"*) ;;   # not registered yet — keep waiting
      *)
        # Checks exist, or gh itself failed. Either way do not swallow what gh
        # said — swallowing gh's diagnostic is the bug this whole change fixes.
        # A healthy `gh pr checks` says nothing on stderr, so this prints only
        # when gh actually spoke (e.g. "HTTP 502: Bad gateway").
        if [[ -n "$detail" ]]; then
          printf '%s\n' "$detail" >&2
        fi
        break
        ;;
    esac
    if [[ $waited -ge $grace ]]; then
      echo "⚠️  No checks reported on ${repo} for PR #${pr_num} after ${waited}s." >&2
      echo "    Nothing to gate on — continuing with no CI gate." >&2
      return 3
    fi
    # This sleep is the whole wait. `waited` advances by $poll whether or not
    # any time actually passed, so deleting or shortening this line leaves the
    # loop counting to $grace in milliseconds and merging to main ungated on a
    # repo whose checks simply had not registered yet.
    sleep "$poll"
    waited=$((waited + poll))
  done

  if gh pr checks "$pr_num" --repo "$repo" --watch --fail-fast; then
    return 0
  fi

  echo "✗ CI checks did not pass on PR #${pr_num}." >&2
  echo "  gh's output is above. If it shows an error rather than a failing check, that is the cause." >&2
  return 1
}

# resolve_pr_number — parse a PR number out of `gh pr create`'s stdout URL
# into PR_NUMBER (bash's dynamic scoping means this sets the caller's `local
# PR_NUMBER` when called from inside merge_main_via_pr), aborting immediately
# if none is found.
#
# `grep -oE '[0-9]+$'` silently returned empty on any URL/error format that
# doesn't end in digits, and under `set -e` that failure died with NO
# message — right after the script had already reported the PR created.
#
# Sets PR_NUMBER directly rather than echoing it back through `$(...)`: a
# `die` (which calls `exit`) reached through a command substitution only
# kills that subshell, so the guard would silently pass on empty output
# instead of aborting the real script.
#
# Split out of merge_main_via_pr so it can be unit tested directly, the same
# way wait_for_ci_checks is.
resolve_pr_number() {
  local pr_url="$1"
  PR_NUMBER=$(printf '%s\n' "$pr_url" | sed -n 's#.*/pull/\([0-9][0-9]*\).*#\1#p' | tail -1)
  [[ -n "$PR_NUMBER" ]] || die "Could not read a PR number from gh's output: '${pr_url}'"
}

# handle_ci_gate_result — turn wait_for_ci_checks' three-way return code into
# operator-facing logging, and abort the merge on anything but "gate passed"
# or "no gate to run" (3). Split out of merge_main_via_pr so it can be unit
# tested directly — this is the exact call site that used to run
# `wait_for_ci_checks ... || die ...` followed by an unconditional log_ok,
# which printed "CI checks passed" even on the no-checks-configured path.
handle_ci_gate_result() {
  local rc="$1" pr_num="$2"
  case "$rc" in
    0) log_ok "CI checks passed on PR #${pr_num}" ;;
    # Not a skip like "no version files changed": this one means main was
    # merged with nothing gating it. Record it so the closing summary can say
    # so too — by then the ⚠️ from wait_for_ci_checks is far off screen.
    3) CI_GATE_SKIPPED=true; log_skip "No CI gate ran on PR #${pr_num}" ;;
    *) die "Not merging PR #${pr_num}." ;;
  esac
}

# Fallback: merge source branch to main via PR when direct push is blocked by branch protection.
# Creates PR, waits for CI, merges, and syncs local main.
merge_main_via_pr() {
  local PR_URL PR_NUMBER CI_RC

  log "Direct push to main blocked (branch protection?). Falling back to PR merge..."

  # Go back to source branch (we were on main after failed push)
  git reset --hard origin/main
  git checkout "$SOURCE_BRANCH"

  # Create PR
  PR_URL=$(gh pr create \
    --base main \
    --head "$SOURCE_BRANCH" \
    --repo "$REPO" \
    --title "$BRANCH_TYPE_CAPITALIZED $VERSION" \
    --body "$(cat <<EOF
$BRANCH_TYPE_CAPITALIZED $VERSION

Merged via git-flow-finish.sh (branch protection PR path).
See CHANGELOG.md for full details.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)") || die "Failed to create PR for $SOURCE_BRANCH → main (gh's error is above)"

  resolve_pr_number "$PR_URL"
  log_ok "Created PR #$PR_NUMBER: $SOURCE_BRANCH → main"

  # Wait for CI checks
  log "Waiting for CI checks on PR #$PR_NUMBER..."
  # Under `set -e` a bare `wait_for_ci_checks ...` statement trips errexit the
  # instant it returns non-zero, before its result could be inspected — `||
  # CI_RC=$?` captures the status without the compound statement itself failing.
  CI_RC=0
  wait_for_ci_checks "$PR_NUMBER" "$REPO" || CI_RC=$?
  handle_ci_gate_result "$CI_RC" "$PR_NUMBER"

  # Squash merge PR — combines all commits into a single commit on main.
  # Release notes are captured via GitHub Release (from CHANGELOG.md),
  # not from the merge commit message, so squash is safe here.
  verify_pr_base "$PR_NUMBER" "main" || exit 2
  gh pr merge "$PR_NUMBER" \
    --repo "$REPO" \
    --squash \
    --delete-branch=false \
    || die "Failed to merge PR #$PR_NUMBER"

  log_ok "Squash-merged PR #$PR_NUMBER to main"
  # Only now is the merge a fact. Set immediately after the confirmation, and
  # never earlier: everything above this line can still abort with main
  # untouched, and the ungated-merge warning reads this flag to decide what to
  # tell the operator about main's state.
  MAIN_MERGED=true

  # Sync local main with remote
  git checkout main
  git pull origin main

  MERGE_VIA_PR=true
}

# push_main_or_fallback — push the merged main, and fall back to the PR path
# when the push is refused.
#
# git's own reason is captured, not discarded. `2>/dev/null` here was the same
# defect as #27: a push to main fails for plenty of reasons that are not branch
# protection (a stale ref, no network, a bad credential), and throwing the
# message away sends the operator into the PR fallback with the actual cause
# already gone.
#
# On the ordering: `2>&1 >/dev/null` dups stderr onto the capture first, THEN
# sends stdout to /dev/null, so only git's stderr is captured. Measured on
# git 2.x: `git push` writes NOTHING to stdout — the "To <remote> ... [new
# branch]" line, "Everything up-to-date", and every fatal all go to stderr, on
# success and on failure alike. So today plain `2>&1` would capture the same
# bytes. The ordering is kept because it states which stream is being captured
# rather than leaving it to chance, and because a porcelain/quiet flag added
# later could start writing to stdout. What must NOT come back is `2>/dev/null`
# — that is #27 on the push path, and it is what this function exists to stop.
#
# Split out of the main flow, above the sourcing guard, so the capture and the
# reprint are reachable from a test — the same move already made for
# wait_for_ci_checks, resolve_pr_number and handle_ci_gate_result.
push_main_or_fallback() {
  local PUSH_ERR=""
  if PUSH_ERR=$(git push origin main:main 2>&1 >/dev/null); then
    log_ok "Pushed to origin/main"
  else
    if [[ -n "$PUSH_ERR" ]]; then
      printf '%s\n' "$PUSH_ERR" >&2
    fi
    merge_main_via_pr
  fi
}

# print_finish_summary — the closing report, including the "nothing gated this
# release" warning.
#
# Lives above the sourcing guard so a test can CALL it. It used to sit inline
# at the bottom of the script, where nothing could reach it: the test on it
# regex-extracted the `if $CI_GATE_SKIPPED` block out of this file and ran that
# text standalone, which proves the block prints correctly if something runs it
# and cannot tell whether anything does. An `exit 0` above the block made the
# warning unreachable without disturbing that test at all. Same structural
# cause as the untested CI-gate wiring and the untested push capture: nothing
# below the sourcing guard can be reached by sourcing the file.
print_finish_summary() {
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Git Flow Finish Complete: $VERSION"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  echo "  $SOURCE_BRANCH → main (GitHub Release $VERSION) → develop"
  echo "  develop bumped to $NEXT_VERSION"
  echo "  Source branch deleted (local + remote)"
  echo ""
  if $CI_GATE_SKIPPED; then
    # The flag is cleared only if these writes SUCCEEDED, so the EXIT handler
    # is never told "already reported" about a message that went nowhere.
    #
    # `&&` rather than a group: the status of a `{ ...; }` is only its LAST
    # command, so a group would clear the flag whenever the final `echo`
    # happened to succeed. Chained, any failed write short-circuits.
    #
    # KNOWN: the false branch of this condition — writes fail, flag stays set —
    # is currently UNREACHABLE, and that is recorded here rather than left for
    # someone to rediscover. Both ways stdout can die take the shell out before
    # this line is reached:
    #
    #   `finish | head`  the reader closes the pipe, the first banner `echo`
    #                    takes SIGPIPE, and the shell dies (exit 141) long
    #                    before the warning block.
    #   `finish >&-`     the first banner `echo` fails EBADF and errexit aborts
    #                    print_finish_summary at that line.
    #
    # In BOTH cases the warning still reaches the operator, because the EXIT
    # handler runs on a fatal signal as well as on an abort and writes to
    # stderr, which is a different descriptor and still healthy. That is the
    # property that actually matters, and it is tested.
    #
    # The condition is kept, not simplified away, because its TRUE branch runs
    # on every normal release — it is what makes a completed run warn exactly
    # once — and because the false branch becomes live the moment the banner
    # above stops aborting. Anyone making that change must keep this clear
    # conditional: pairing a non-aborting banner with an unconditional clear
    # hands the handler a cleared flag for an undelivered warning, and an
    # ungated merge to main then goes entirely unreported.
    #
    # Do not try to prove the false branch by closing fd 1. A closed fd 1 is
    # not a sound model of a dead stdout: the next `$(...)` anywhere in the
    # shell allocates fd 1 for its pipe, so "stdout" silently becomes that
    # pipe and the measurement reports whatever it likes. Measured — a probe
    # reading /dev/fd/1 came back holding the shell's own pending stdout.
    if echo "  ⚠️  NO CI GATE RAN. No checks ever registered on the PR to main, so" &&
       echo "      $VERSION was merged and released with nothing gating it." &&
       echo "      Check the build on main before relying on this release." &&
       echo ""; then
      CI_GATE_SKIPPED=false
    fi
  fi
  if $DRY_RUN; then
    echo "  [DRY RUN — no changes were made]"
    echo ""
  fi
}

# Allow the file to be sourced for testing without invoking the main flow.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then

# ── Parse Arguments ────────────────────────────────────────────────

[[ $# -lt 1 ]] && usage
[[ "$1" == "--help" || "$1" == "-h" ]] && usage
[[ $# -lt 2 ]] && { echo "Error: Missing version argument"; usage; }

BRANCH_TYPE="$1"
VERSION="$2"
shift 2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true ;;
    --skip-changelog) SKIP_CHANGELOG=true ;;
    --help|-h) usage ;;
    *) die "Unknown option: $1" ;;
  esac
  shift
done

# ── Validate Inputs ────────────────────────────────────────────────

[[ "$BRANCH_TYPE" == "hotfix" || "$BRANCH_TYPE" == "release" ]] || die "branch-type must be 'hotfix' or 'release', got '$BRANCH_TYPE'"

echo "$VERSION" | grep -qE '^v[0-9]+\.[0-9]+\.[0-9]+' || die "Version must start with v and follow semver (e.g., v1.0.6), got '$VERSION'"

VERSION_NUMBER="${VERSION#v}"
SOURCE_BRANCH="${BRANCH_TYPE}/${VERSION}"

# Detect source branch variations (hotfix/deps-v1.0.6 vs hotfix/v1.0.6)
if ! git rev-parse --verify "$SOURCE_BRANCH" >/dev/null 2>&1; then
  # Try with deps- prefix (common for dependabot hotfixes)
  if git rev-parse --verify "${BRANCH_TYPE}/deps-${VERSION}" >/dev/null 2>&1; then
    SOURCE_BRANCH="${BRANCH_TYPE}/deps-${VERSION}"
  else
    die "Source branch not found. Tried: ${BRANCH_TYPE}/${VERSION}, ${BRANCH_TYPE}/deps-${VERSION}"
  fi
fi

REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null) || die "Failed to get repo name. Is gh authenticated?"

# ── Verify Prerequisites ───────────────────────────────────────────

log_phase "PRE-FLIGHT CHECKS"

log_repo_context

# Must be on the source branch
CURRENT=$(git branch --show-current)
if [[ "$CURRENT" != "$SOURCE_BRANCH" ]]; then
  die "Must be on $SOURCE_BRANCH, currently on $CURRENT"
fi

# Working directory must be clean
if [[ -n "$(git status --porcelain)" ]]; then
  die "Working directory is not clean. Commit or stash changes first."
fi

# All commits must be pushed
UNPUSHED=$(git log "@{u}..HEAD" --oneline 2>/dev/null | wc -l | tr -d ' ')
if [[ "$UNPUSHED" -gt 0 ]]; then
  die "$UNPUSHED unpushed commits. Push them first: git push"
fi

log_ok "On $SOURCE_BRANCH, clean, all pushed"

# ── Phase 1: Merge to Main ────────────────────────────────────────

log_phase "MERGE TO MAIN"

if $DRY_RUN; then
  log "[dry-run] Would merge $SOURCE_BRANCH into main"
else
  git checkout main
  git pull origin main

  # Capitalize first letter (POSIX-compatible; ${VAR^} requires bash 4+ and macOS ships bash 3.2)
  BRANCH_TYPE_CAPITALIZED="$(echo "$BRANCH_TYPE" | awk '{print toupper(substr($0,1,1)) substr($0,2)}')"

  git merge --no-ff "$SOURCE_BRANCH" -m "$(cat <<EOF
Merge $SOURCE_BRANCH into main

$BRANCH_TYPE_CAPITALIZED $VERSION

See CHANGELOG.md for full details.

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)" || die "Merge to main failed (conflict?)"

  log_ok "Merged $SOURCE_BRANCH into main (local)"

  push_main_or_fallback
fi

# ── Phase 2: Create Tag ───────────────────────────────────────────

log_phase "CREATE TAG"

if $DRY_RUN; then
  log "[dry-run] Would create tag $VERSION on main"
else
  git tag -a "$VERSION" -m "$BRANCH_TYPE_CAPITALIZED $VERSION" || die "Failed to create tag $VERSION"
  log_ok "Created annotated tag $VERSION"
fi

# ── Phase 3: Create GitHub Release ───────────────────────────────

log_phase "CREATE GITHUB RELEASE"

# Push tag to remote (gh release create uses --target but having the tag pushed ensures annotated tag)
if ! $DRY_RUN && ! $MERGE_VIA_PR; then
  git push origin "$VERSION" 2>/dev/null || true
fi
create_github_release "$VERSION"

# ── Phase 4: Merge to Develop ─────────────────────────────────────

log_phase "MERGE TO DEVELOP"

if $DRY_RUN; then
  log "[dry-run] Would merge $SOURCE_BRANCH into develop"
else
  git checkout develop
  git pull origin develop

  git merge --no-ff "$SOURCE_BRANCH" -m "$(cat <<EOF
Merge $SOURCE_BRANCH back into develop

Sync ${BRANCH_TYPE} artifacts from $VERSION:
- Changelog with versioned section
- Version bumps

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)" || die "Merge to develop failed (conflict?)"

  log_ok "Merged $SOURCE_BRANCH into develop"

  # Sync main's --no-ff merge commit into develop's ancestry.
  # Without this, GitHub shows develop as "1 commit behind main" because
  # the merge commit on main (e.g., "Merge hotfix/v1.0.9 into main") is
  # not an ancestor of develop — even though file content is identical.
  git fetch origin main
  if git log origin/main --not HEAD --oneline | grep -q .; then
    log "Syncing main merge commit into develop ancestry..."
    git merge origin/main -m "$(cat <<EOF
Merge main into develop (sync $VERSION merge commit)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)" || log "⚠️  Main sync merge had conflicts (non-fatal, develop content is correct)"
    log_ok "Develop ancestry now includes main's merge commit"
  else
    log_ok "Develop already includes all main commits"
  fi
fi

# ── Phase 5: Bump Develop Version ─────────────────────────────────

log_phase "BUMP DEVELOP VERSION"

# Calculate next patch version
MAJOR=$(echo "$VERSION_NUMBER" | cut -d. -f1)
MINOR=$(echo "$VERSION_NUMBER" | cut -d. -f2)
RAW_PATCH=$(echo "$VERSION_NUMBER" | cut -d. -f3)
PATCH=$(echo "$RAW_PATCH" | cut -d- -f1)
NEXT_PATCH=$((PATCH + 1))
NEXT_VERSION="${MAJOR}.${MINOR}.${NEXT_PATCH}"

log "Next development version: $NEXT_VERSION"

if $DRY_RUN; then
  log "[dry-run] Would bump to next patch version via bump-version.sh"
else
  if [[ -x "./scripts/bump-version.sh" ]]; then
    ./scripts/bump-version.sh patch
  else
    log_skip "bump-version.sh not found — skip version bump"
  fi

  # Stage and commit all version file changes
  git add -A
  if ! git diff --cached --quiet; then
    git commit -m "$(cat <<EOF
chore(develop): bump version for next development cycle

Previous ${BRANCH_TYPE}: $VERSION

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
    log_ok "Bumped to next patch and committed"
  else
    log_skip "No version files changed"
  fi
fi

# ── Phase 6: Push Develop ─────────────────────────────────────────

log_phase "PUSH DEVELOP"

push_ref "develop" "develop"
prune_stale_refs

# ── Phase 7: Cleanup ──────────────────────────────────────────────

log_phase "CLEANUP"

if $DRY_RUN; then
  log "[dry-run] Would delete $SOURCE_BRANCH (local + remote)"
else
  # Delete local branch (-D force because squash merge creates different SHA,
  # so git branch -d fails with "not fully merged")
  git branch -D "$SOURCE_BRANCH" 2>/dev/null && log_ok "Deleted local $SOURCE_BRANCH" || log_skip "Local $SOURCE_BRANCH already deleted"

  # Delete remote branch via API (avoids push hook)
  gh api "repos/$REPO/git/refs/heads/$SOURCE_BRANCH" -X DELETE 2>/dev/null && log_ok "Deleted remote $SOURCE_BRANCH" || log_skip "Remote $SOURCE_BRANCH already deleted"
fi

# ── Phase 8: Verify ───────────────────────────────────────────────

log_phase "VERIFICATION"

if $DRY_RUN; then
  log "[dry-run] Would verify main, develop, and tag are in sync"
else
  # Verify current branch
  FINAL_BRANCH=$(git branch --show-current)
  [[ "$FINAL_BRANCH" == "develop" ]] && log_ok "On develop branch" || log_fail "Expected develop, on $FINAL_BRANCH"

  # Verify local = remote for develop
  git fetch origin develop 2>/dev/null
  LOCAL_SHA=$(git rev-parse develop)
  REMOTE_SHA=$(git rev-parse origin/develop 2>/dev/null)
  [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]] && log_ok "develop in sync (local = remote)" || log_fail "develop out of sync: local=$LOCAL_SHA remote=$REMOTE_SHA"

  # Verify local = remote for main
  git fetch origin main 2>/dev/null
  LOCAL_MAIN=$(git rev-parse main)
  REMOTE_MAIN=$(git rev-parse origin/main 2>/dev/null)
  [[ "$LOCAL_MAIN" == "$REMOTE_MAIN" ]] && log_ok "main in sync (local = remote)" || log_fail "main out of sync"

  # Verify GitHub Release (and tag) exists on remote
  if gh release view "$VERSION" --repo "$REPO" --json tagName --jq '.tagName' >/dev/null 2>&1; then
    log_ok "GitHub Release $VERSION exists on remote"
  else
    log_fail "GitHub Release $VERSION not found on remote"
  fi

  log_ok "Develop ready for next development cycle"
fi

# ── Summary ────────────────────────────────────────────────────────

print_finish_summary

fi  # end: if [[ "${BASH_SOURCE[0]}" == "${0}" ]]

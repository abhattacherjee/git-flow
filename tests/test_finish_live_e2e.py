"""Run git-flow-finish.sh for real — no --dry-run — against a local bare origin.

The dry-run e2e proves the script walks its full length, but every phase it
walks is guarded by `if $DRY_RUN`, so the branches that DO things never
execute. That leaves the two call sites that matter most unasserted, and both
are single lines whose deletion is invisible to a suite that never reaches
them:

  push_main_or_fallback   is called once, at the end of the merge-to-main
                          phase. merge_main_via_pr is called only from inside
                          it, so deleting that one call turns the entire CI
                          gate — the whole point of this change-set — into
                          unreachable dead code.
  verify_pr_base          is called once, on the PR that merges to main, and
                          `|| exit 2` is what stops a release being squashed
                          onto the wrong base. Layer 1 is a hook; this is
                          Layer 2, and Layer 2 only exists at this call site.

Extracting a function fixes reachability for its BODY and moves the untested
boundary up to its call. These tests sit at that boundary.

The setup is a throwaway repo with a bare repo as `origin`, so the script
merges, pushes, tags and back-merges for real. `gh` is the usual stub, keyed
per subcommand so one run can answer `repo view`, `pr create`, `pr view` and
`release view` differently.

Blocking the push to main uses a `pre-receive` hook in the bare origin that
rejects `refs/heads/main`. The release branch carries a real commit, so the
push is a genuine ref update and actually reaches the hook — a no-op push is
negotiated away before any hook runs and would fall through as a success.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"

VERSION = "v1.2.3"
SOURCE_BRANCH = f"release/{VERSION}"
PR_URL = "https://github.com/owner/repo/pull/4242"

REJECT_MAIN_HOOK = """#!/usr/bin/env bash
while read -r _old _new ref; do
  if [ "$ref" = "refs/heads/main" ]; then
    echo "remote: protected branch hook declined to update refs/heads/main" >&2
    exit 1
  fi
done
exit 0
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args],
                   check=True, capture_output=True, text=True)


def _live_run(root: Path, block_main: bool = False, pr_base: str = "main",
              extra_env: dict | None = None):
    repo = root / "repo"
    origin = root / "origin.git"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare", str(origin)],
                   check=True, capture_output=True, text=True)

    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    # The script makes real commits here; a developer's global signing config
    # would otherwise decide whether this test passes.
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "branch", "develop")
    _git(repo, "push", "-q", "origin", "develop")

    _git(repo, "checkout", "-q", "-b", SOURCE_BRANCH)
    (repo / "g.txt").write_text("y\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "feat: something")
    _git(repo, "push", "-q", "origin", SOURCE_BRANCH)

    if block_main:
        hook = origin / "hooks" / "pre-receive"
        hook.write_text(REJECT_MAIN_HOOK)
        hook.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    env["MOCK_GH_STDOUT"] = "owner/repo"
    env["MOCK_GH_STDOUT_PR_CREATE"] = PR_URL
    env["MOCK_GH_STDOUT_PR_VIEW"] = pr_base
    # `gh release view --json body --jq '.body | length'` feeds a numeric
    # comparison, so it needs a number rather than the flat default.
    env["MOCK_GH_STDOUT_RELEASE_VIEW"] = "200"
    # No CI on this repo: the classification call reports "no checks reported"
    # and grace=0 ends the wait on the first pass, so the gate returns 3 and
    # the run continues ungated — the state the closing warning is about.
    env["GIT_FLOW_CHECKS_GRACE"] = "0"
    env["MOCK_GH_DETAIL"] = "no checks reported on the 'x' branch"
    env["MOCK_GH_DETAIL_EXIT"] = "1"
    for k in ("MOCK_GH_STDERR", "MOCK_GH_EXIT", "MOCK_GH_SLEEP",
              "MOCK_GH_DETAIL_SEQ"):
        env.pop(k, None)
    # Kept, not popped: the argv log is what pins the commands that CHANGE
    # something. The stub answers every call the same way regardless of argv,
    # so this file is the only place a wrong flag can show up.
    args_file = root / "gh_args.log"
    env["MOCK_GH_ARGS_FILE"] = str(args_file)
    env.update(extra_env or {})

    result = subprocess.run(
        ["bash", str(SCRIPT), "release", VERSION],
        cwd=repo, capture_output=True, text=True, env=env, timeout=120,
    )
    return result, repo, origin, args_file


@pytest.fixture(scope="module")
def pushed(tmp_path_factory):
    """A run where the push to main succeeds — no PR fallback."""
    return _live_run(tmp_path_factory.mktemp("pushed"))


@pytest.fixture(scope="module")
def blocked(tmp_path_factory):
    """A run where main is protected, so the PR fallback path runs."""
    return _live_run(tmp_path_factory.mktemp("blocked"), block_main=True)


@pytest.fixture(scope="module")
def wrong_base(tmp_path_factory):
    """The same fallback path, with the PR opened against the wrong base."""
    return _live_run(tmp_path_factory.mktemp("wrongbase"),
                     block_main=True, pr_base="develop")


def test_the_merge_phase_actually_pushes_main(pushed):
    """The merge-to-main phase must CALL push_main_or_fallback.

    Deleting that call leaves main merged locally and never pushed, and takes
    merge_main_via_pr — the only caller of the CI gate — out of the program
    with it. Nothing that stubs or sources a function can see the difference;
    only a run that reaches a real remote can.
    """
    result, _repo, _origin, _args = pushed
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("Pushed to origin/main") == 1, result.stdout


def test_the_pushed_main_carries_the_release(pushed):
    """Reporting the push is not the same as pushing. Read the remote back:
    origin/main must contain the release branch's commit.
    """
    _result, repo, origin, _args = pushed
    remote_main = subprocess.run(
        ["git", "-C", str(origin), "rev-parse", "main"],
        check=True, capture_output=True, text=True).stdout.strip()
    contains = subprocess.run(
        ["git", "-C", str(repo), "log", "--format=%s", remote_main],
        check=True, capture_output=True, text=True).stdout
    assert "feat: something" in contains, contains


def test_the_run_bumps_develop_to_the_next_patch(pushed):
    """The summary's bumped version must be the computed one, not a blank.

    NEXT_VERSION is initialised empty so the summary can be unit-tested. That
    initialiser also means a bump that never runs prints `develop bumped to `
    with nothing after it, inside a block headed "Git Flow Finish Complete".
    """
    result, _repo, _origin, _args = pushed
    assert "develop bumped to 1.2.4" in result.stdout, result.stdout


def test_a_blocked_push_falls_back_to_the_pr_merge(blocked):
    """With a correct base, the fallback path runs all the way through the
    squash merge.

    This is what makes an absent merge elsewhere meaningful. Without a run
    that reaches the merge, "no merge happened" is equally well explained by
    a harness that never got near it.
    """
    result, _repo, _origin, _args = blocked
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Direct push to main blocked" in result.stdout, result.stdout
    assert "Squash-merged PR #4242 to main" in result.stdout, result.stdout


def test_a_wrong_base_pr_aborts_before_the_merge(wrong_base):
    """`verify_pr_base ... || exit 2` is the last thing between a release and
    a squash merge onto the wrong base.

    Delete that call and the run merges anyway: a PR based on develop gets
    squashed, and the operator is told it went to main. The exit status is
    part of the contract — 2 is the base-guard's own code, distinct from the
    1 that every other failure exits with.
    """
    result, _repo, _origin, _args = wrong_base
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'has base "develop", expected "main"' in result.stderr, result.stderr
    assert "Squash-merged" not in result.stdout, result.stdout


def test_an_ungated_release_warns_exactly_once(blocked):
    """End to end, with nothing stubbed but gh: the run merged to main with no
    check ever registered, so it must say so — once.

    The summary prints the warning and the EXIT handler fires straight after.
    Two copies of the same ⚠️ in one run teaches the operator to skim it.
    """
    result, _repo, _origin, _args = blocked
    both = result.stdout + result.stderr
    assert both.count("NO CI GATE RAN") == 1, both


def test_the_wrong_base_abort_does_not_claim_main_was_merged(wrong_base):
    """The guard fired correctly and main never moved. Telling the operator
    the release is already on main is what sends them to revert or force-push
    a branch that is fine.
    """
    result, _repo, _origin, _args = wrong_base
    assert "NO CI GATE RAN" in result.stderr, result.stderr
    assert "already on the remote" not in result.stderr, result.stderr


@pytest.fixture(scope="module")
def merge_refused(tmp_path_factory):
    """The fallback path with `gh pr merge` itself failing."""
    return _live_run(tmp_path_factory.mktemp("mergerefused"), block_main=True,
                     extra_env={"MOCK_GH_EXIT_PR_MERGE": "1"})


def test_the_write_commands_are_invoked_exactly_as_intended(blocked):
    """Exact argv for every gh command that CHANGES something.

    The gate and classification calls are pinned by equality; the three
    commands that actually open the PR, merge it to main and publish the
    release were not, and the stub answers regardless of argv — so a wrong
    flag on any of them changed nothing observable. The one that matters most
    is an EXTRA flag rather than a missing one: `--admin` on `gh pr merge`
    bypasses branch protection and lands a red build on main, which is the
    protection this whole change-set exists to respect.

    `--base` is asserted here rather than left to verify_pr_base. The stub
    answers `pr view` from MOCK_GH_STDOUT_PR_VIEW no matter what `pr create`
    was asked for, so the guard whose job is to notice that disagreement is
    facing a stub that cannot disagree with itself.

    On the `pr create` line: `--body` is last and its heredoc is multi-line,
    and the stub logs one line per invocation, so the logged line ends at the
    body's first line. Every flag worth pinning sits before `--body`, so exact
    equality on that line covers all of them.
    """
    _result, _repo, _origin, args_file = blocked
    lines = args_file.read_text().splitlines()

    def one(prefix: str) -> str:
        found = [l for l in lines if l.startswith(prefix)]
        assert len(found) == 1, f"expected exactly one {prefix!r} call, got {found}"
        return found[0]

    assert one("pr create") == (
        "pr create --base main --head release/v1.2.3 --repo owner/repo "
        "--title Release v1.2.3 --body Release v1.2.3"
    )
    assert one("pr view") == "pr view 4242 --json baseRefName --jq .baseRefName"
    assert one("pr merge") == (
        "pr merge 4242 --repo owner/repo --squash --delete-branch=false"
    )
    # Everything but the notes tempfile path, which is generated per run.
    assert re.fullmatch(
        r"release create v1\.2\.3 --repo owner/repo --target main "
        r"--title Release v1\.2\.3 --notes-file \S+",
        one("release create"),
    ), one("release create")


def test_a_refused_merge_aborts_and_does_not_claim_main_moved(merge_refused):
    """`gh pr merge` failing is the second abort between the CI gate and the
    confirmed merge, and the only one reachable end to end.

    It is NOT the same as the wrong-base guard: gh can fail on a lost response
    to a merge the server already performed, so main may or may not have
    moved. The warning must therefore stay agnostic — it may not say the merge
    landed, and it may not say main is clean either.
    """
    result, _repo, _origin, _args = merge_refused
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Failed to merge PR #4242" in result.stdout, result.stdout
    assert "Squash-merged" not in result.stdout, result.stdout
    assert "NO CI GATE RAN" in result.stderr, result.stderr
    assert "already on the remote" not in result.stderr, result.stderr

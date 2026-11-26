"""Run git-flow-finish.sh for real from a LINKED worktree (#32).

The main checkout `repo/` holds `develop`; the release branch lives in a
linked worktree `repo--rel/`, and the script runs there. Before #32 the first
`git checkout main` (or `develop`) died with
`fatal: '<branch>' is already used by worktree`, after the merge to main had
already been pushed.

The assertions read origin and the main checkout back, never the script's
own log lines alone: a detached-HEAD run that pushed the stale local branch
would print "Pushed" and move nothing.
"""
from __future__ import annotations

import os
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


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _setup(root: Path, main_state: str = "clean", block_main: bool = False):
    """Build origin + main checkout + linked release worktree.

    main_state: "clean"  main checkout on develop, clean
                "dirty"  main checkout on develop, a tracked file modified
                "other"  main checkout on feature/x
    """
    # Git reports physical worktree paths (e.g. /private/var on macOS).
    root = root.resolve()
    origin = root / "origin.git"
    repo = root / "repo"
    wt = root / "repo--rel"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)],
                   check=True, capture_output=True, text=True)
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--no-verify", "-m", "init")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "branch", "develop")
    _git(repo, "push", "-q", "origin", "develop")
    _git(repo, "checkout", "-q", "develop")

    _git(repo, "worktree", "add", "-q", "-b", SOURCE_BRANCH, str(wt), "develop")
    (wt / "g.txt").write_text("y\n")
    bump = wt / "scripts" / "bump-version.sh"
    bump.parent.mkdir()
    bump.write_text('#!/bin/sh\n[ "$1" = patch ] || exit 1\necho 1.2.4 > VERSION\n')
    bump.chmod(0o755)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "--no-verify", "-m", "feat: something")
    _git(wt, "push", "-q", "origin", SOURCE_BRANCH)

    if main_state == "dirty":
        (repo / "f.txt").write_text("local edit\n")
    elif main_state == "other":
        _git(repo, "checkout", "-q", "-b", "feature/x")

    if block_main:
        hook = origin / "hooks" / "pre-receive"
        hook.write_text(REJECT_MAIN_HOOK)
        hook.chmod(0o755)
    return origin, repo, wt


def _run(wt: Path, root: Path):
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    env["MOCK_GH_STDOUT"] = "owner/repo"
    env["MOCK_GH_STDOUT_PR_CREATE"] = PR_URL
    env["MOCK_GH_STDOUT_PR_VIEW"] = "main"
    env["MOCK_GH_STDOUT_RELEASE_VIEW"] = "200"
    env["GIT_FLOW_CHECKS_GRACE"] = "0"
    env["MOCK_GH_DETAIL"] = "no checks reported on the 'x' branch"
    env["MOCK_GH_DETAIL_EXIT"] = "1"
    for k in ("MOCK_GH_STDERR", "MOCK_GH_EXIT", "MOCK_GH_SLEEP",
              "MOCK_GH_DETAIL_SEQ"):
        env.pop(k, None)
    env["MOCK_GH_ARGS_FILE"] = str(root / "gh_args.log")
    return subprocess.run(["bash", str(SCRIPT), "release", VERSION],
                          cwd=wt, capture_output=True, text=True, env=env,
                          timeout=120)


def _origin_log(origin: Path, ref: str) -> str:
    return _git(origin, "log", "--format=%s", ref)


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    root = tmp_path_factory.mktemp("wt_clean")
    origin, repo, wt = _setup(root, "clean")
    return _run(wt, root), origin, repo, wt


@pytest.fixture(scope="module")
def dirty(tmp_path_factory):
    root = tmp_path_factory.mktemp("wt_dirty")
    origin, repo, wt = _setup(root, "dirty")
    before = _git(repo, "rev-parse", "HEAD")
    return _run(wt, root), origin, repo, wt, before


@pytest.fixture(scope="module")
def other(tmp_path_factory):
    root = tmp_path_factory.mktemp("wt_other")
    origin, repo, wt = _setup(root, "other")
    return _run(wt, root), origin, repo, wt


@pytest.fixture(scope="module")
def blocked(tmp_path_factory):
    root = tmp_path_factory.mktemp("wt_blocked")
    origin, repo, wt = _setup(root, "clean", block_main=True)
    return _run(wt, root), origin, repo, wt, root


# ── clean main checkout on develop ────────────────────────────────


def test_a_linked_worktree_run_completes(clean):
    result, origin, _repo, wt = clean
    both = result.stdout + result.stderr
    assert "already used by worktree" not in both, both
    assert "already checked out" not in both, both
    assert result.returncode == 0, both
    assert _git(wt, "branch", "--show-current") == ""
    assert _git(wt, "rev-parse", "HEAD") == _git(origin, "rev-parse", "develop")
    assert "✅ HEAD equals origin/develop" in result.stdout


def test_origin_main_carries_the_release_and_the_tag(clean):
    result, origin, _repo, _wt = clean
    assert "feat: something" in _origin_log(origin, "main"), result.stdout
    tag_commit = _git(origin, "rev-parse", f"{VERSION}^{{commit}}")
    assert tag_commit == _git(origin, "rev-parse", "main"), result.stdout


def test_origin_develop_carries_the_back_merge_and_the_bump(clean):
    result, origin, _repo, _wt = clean
    log = _origin_log(origin, "develop")
    assert f"Merge {SOURCE_BRANCH} back into develop" in log, result.stdout
    assert "feat: something" in log, result.stdout
    assert _git(origin, "log", "-1", "--format=%s", "develop") == \
        "chore(develop): bump version for next development cycle"
    assert _git(origin, "show", "develop:VERSION") == "1.2.4"


def test_the_main_checkout_develop_is_fast_forwarded(clean):
    result, origin, repo, _wt = clean
    assert _git(repo, "branch", "--show-current") == "develop"
    assert _git(repo, "rev-parse", "HEAD") == _git(origin, "rev-parse", "develop"), \
        result.stdout + result.stderr
    assert (repo / "g.txt").exists(), "main checkout working tree not updated"


def test_the_local_main_ref_matches_origin(clean):
    result, origin, repo, _wt = clean
    assert _git(repo, "rev-parse", "main") == _git(origin, "rev-parse", "main"), \
        result.stdout + result.stderr


# ── dirty main checkout ───────────────────────────────────────────


def test_a_dirty_main_checkout_is_not_touched(dirty):
    result, origin, repo, _wt, before = dirty
    both = result.stdout + result.stderr
    assert result.returncode == 0, both
    assert _git(repo, "rev-parse", "HEAD") == before, both
    assert (repo / "f.txt").read_text() == "local edit\n"
    # The release still fully reached origin.
    assert "feat: something" in _origin_log(origin, "main"), both
    assert f"Merge {SOURCE_BRANCH} back into develop" in _origin_log(origin, "develop"), both


def test_a_dirty_main_checkout_gets_one_named_warning(dirty):
    result, _origin, repo, _wt, _before = dirty
    both = result.stdout + result.stderr
    cmd = f"git -C {repo} pull --ff-only origin develop"
    warning = f"main checkout {repo} has uncommitted changes; left develop as is. Run: {cmd}"
    sync, rest = result.stdout.split("VERIFICATION")
    verification, summary = rest.split("Git Flow Finish Complete")
    assert warning in sync, both
    assert f"⏭️  develop out of sync: {warning}" in verification, both
    assert summary.count(warning) == 1, both


# ── main checkout on another branch ───────────────────────────────


def test_a_main_checkout_on_another_branch_is_not_switched(other):
    result, origin, repo, _wt = other
    both = result.stdout + result.stderr
    assert result.returncode == 0, both
    assert _git(repo, "branch", "--show-current") == "feature/x", both
    # Nobody holds develop, so the shared local ref is fast-forwarded.
    assert _git(repo, "rev-parse", "develop") == _git(origin, "rev-parse", "develop"), both
    assert (f"main checkout {repo} is on feature/x, not develop; "
            f"to use the merge run: git -C {repo} switch develop") in both, both


@pytest.mark.parametrize("held", [True, False], ids=["pull-refused", "fetch-refused"])
def test_diverged_local_develop_survives_finish(tmp_path, held):
    origin, repo, wt = _setup(tmp_path)
    _git(repo, "commit", "--allow-empty", "-qm", "local develop work")
    before = _git(repo, "rev-parse", "develop")
    if not held:
        _git(repo, "checkout", "-qb", "feature/x")

    result = _run(wt, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(repo, "rev-parse", "develop") == before
    assert _git(origin, "show", "develop:VERSION") == "1.2.4"
    cmd = f"git -C {repo} pull --ff-only origin develop" if held else "git fetch origin develop:develop"
    skip = next(line for line in result.stdout.splitlines() if "⏭️  develop out of sync" in line)
    assert cmd in skip


@pytest.mark.parametrize("missing", [False, True], ids=["active", "prunable"])
def test_preserves_main_held_by_another_worktree(tmp_path, missing):
    origin, repo, wt = _setup(tmp_path)
    holder = repo.parent / "other worktree"
    _git(repo, "worktree", "add", "-q", str(holder), "main")
    before = _git(holder, "rev-parse", "HEAD")
    if missing:
        holder.rename(repo.parent / "moved worktree")

    result = _run(wt, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _git(repo, "rev-parse", "main") == before
    assert _git(origin, "rev-parse", "main") != before
    assert _git(repo, "rev-parse", "develop") == _git(origin, "rev-parse", "develop")
    assert f"git -C {holder} pull --ff-only origin main" in result.stdout
    assert "⏭️  main out of sync" in result.stdout
    if missing:
        assert "git worktree prune" in result.stdout


# ── protected main: PR fallback from a linked worktree ────────────


def test_the_pr_fallback_works_from_a_linked_worktree(blocked):
    result, _origin, repo, _wt, root = blocked
    both = result.stdout + result.stderr
    assert "already used by worktree" not in both, both
    assert result.returncode == 0, both
    assert "Squash-merged PR #4242 to main" in result.stdout, both
    args = (root / "gh_args.log").read_text()
    assert "pr merge 4242" in args, args


# ── helpers, sourced ──────────────────────────────────────────────


def _source(cwd: Path, snippet: str) -> str:
    return subprocess.run(
        ["bash", "-c", f'source "{SCRIPT}"; {snippet}'],
        cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_main_checkout_path_from_a_linked_worktree(tmp_path):
    _origin, repo, wt = _setup(tmp_path)
    assert _source(wt, "main_checkout_path") == str(repo.resolve())


def test_worktree_holding_names_the_holder_or_nothing(tmp_path):
    _origin, repo, wt = _setup(tmp_path)
    assert _source(wt, "worktree_holding develop") == str(repo.resolve())
    assert _source(wt, f"worktree_holding {SOURCE_BRANCH}") == str(wt.resolve())
    assert _source(wt, "worktree_holding main") == ""


def test_is_linked_worktree(tmp_path):
    _origin, repo, wt = _setup(tmp_path)
    linked = subprocess.run(["bash", "-c", f'source "{SCRIPT}"; is_linked_worktree'],
                            cwd=wt, capture_output=True, text=True)
    main = subprocess.run(["bash", "-c", f'source "{SCRIPT}"; is_linked_worktree'],
                          cwd=repo, capture_output=True, text=True)
    assert linked.returncode == 0, linked.stderr
    assert main.returncode == 1, main.stderr

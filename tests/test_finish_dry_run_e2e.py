"""Run the whole of git-flow-finish.sh, end to end, in --dry-run.

A test that sources the script and calls one function cannot see whether the
main flow still CALLS that function. Three separate defects of exactly that
shape were found in this change: the CI gate was unwired from the merge, the
push's stderr capture sat where nothing could reach it, and the closing summary
was tested as a regex-extracted copy of itself — a copy that keeps passing
after the original is made unreachable. Running the real script is what closes
that gap.

--dry-run guards every phase, so the script walks its full length — pre-flight
checks, merge to main, tag, release, merge to develop, version bump, push,
cleanup, verification, summary — without touching a remote. All it needs is a
throwaway repo on a release branch and the `gh` stub for `gh repo view`.

What this does NOT cover: the non-dry-run branches. merge_main_via_pr and
push_main_or_fallback never run here, so nothing here reaches the CI gate or
sets CI_GATE_SKIPPED. What it adds is the one thing a direct function call
cannot show — that the script runs from its first line to its last.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"

VERSION = "v1.2.3"
SOURCE_BRANCH = f"release/{VERSION}"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    )


def _finish_dry_run(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--no-verify", "-m", "init")
    # The push-develop phase resolves `develop` even in dry-run.
    _git(repo, "branch", "develop")
    _git(repo, "checkout", "-q", "-b", SOURCE_BRANCH)

    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    # `gh repo view --json nameWithOwner` is the only gh call the dry-run path
    # makes; the stub answers every subcommand from MOCK_GH_STDOUT.
    env["MOCK_GH_STDOUT"] = "owner/repo"
    for k in ("MOCK_GH_STDERR", "MOCK_GH_EXIT", "MOCK_GH_SLEEP", "MOCK_GH_ARGS_FILE"):
        env.pop(k, None)
    return subprocess.run(
        ["bash", str(SCRIPT), "release", VERSION, "--dry-run"],
        cwd=repo, capture_output=True, text=True, env=env, timeout=60,
    )


def test_dry_run_reaches_the_closing_summary(tmp_path):
    """The summary must be REACHED, not merely correct when run.

    This is the assertion a test on an extracted copy of the summary could not
    make: it asserts the script gets there. Delete the `print_finish_summary`
    call or exit above it and the run still succeeds with no summary at all,
    which is exactly how the ungated-release warning became unreachable while
    looking tested.

    The bumped version is asserted by VALUE. NEXT_VERSION is initialised empty
    so the summary can be called from a unit test, which means a bump phase
    that never computes it prints `develop bumped to ` — a blank where the
    version belongs, inside a block headed "Git Flow Finish Complete" and
    followed by exit 0. Checking the summary is present says nothing about
    whether what it reports is real.
    """
    result = _finish_dry_run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    # Exactly once, not merely present: print_finish_summary called twice
    # prints the whole closing report twice, which reads as two releases.
    assert result.stdout.count("Git Flow Finish Complete: v1.2.3") == 1, result.stdout
    assert "[DRY RUN — no changes were made]" in result.stdout, result.stdout
    assert "develop bumped to 1.2.4" in result.stdout, result.stdout


def test_dry_run_walks_every_phase(tmp_path):
    """Reaching the summary is not the same as running the script.

    A summary moved up, or a run that skipped straight to it, still prints
    "Git Flow Finish Complete". Pinning the phase banners in order is what
    makes "reached the end" mean it went through the middle.
    """
    result = _finish_dry_run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    phases = ["PRE-FLIGHT CHECKS", "REPO CONTEXT", "MERGE TO MAIN", "CREATE TAG",
              "CREATE GITHUB RELEASE", "MERGE TO DEVELOP", "BUMP DEVELOP VERSION",
              "PUSH DEVELOP", "CLEANUP", "VERIFICATION",
              "Git Flow Finish Complete"]
    at = -1
    for phase in phases:
        found = result.stdout.find(phase, at + 1)
        assert found > at, f"{phase!r} missing or out of order:\n{result.stdout}"
        at = found


def test_dry_run_does_not_warn_when_the_gate_was_not_skipped(tmp_path):
    """CI_GATE_SKIPPED is false on this path — merge_main_via_pr never runs in
    dry-run — so the summary must stay quiet.

    Without this, a summary that printed the ungated-release warning on every
    single run would still satisfy every assertion that the warning appears
    when the gate was skipped.
    """
    result = _finish_dry_run(tmp_path)
    assert "NO CI GATE RAN" not in result.stdout, result.stdout

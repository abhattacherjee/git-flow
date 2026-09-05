"""Test scripts/git-flow-finish.sh:wait_for_ci_checks via subprocess.

Sources the script in a subshell so we do not trigger the main /finish flow.
Stubs `gh` via the same fixture the hook tests use.

This gate is the last thing between a release branch and a squash merge to
main. It used to run with `2>/dev/null`, so an auth failure, a rate limit or an
unsupported flag was indistinguishable from a failing check (#27).
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"


def _run(gh_exit: int = 0, gh_stderr: str = "", gh_help: str | None = None):
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        wait_for_ci_checks "42" "owner/repo"
    """)
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    env["MOCK_GH_EXIT"] = str(gh_exit)
    env["MOCK_GH_STDERR"] = gh_stderr
    env.pop("MOCK_GH_STDOUT", None)
    env.pop("MOCK_GH_SLEEP", None)
    env.pop("MOCK_GH_HELP", None)
    if gh_help is not None:
        env["MOCK_GH_HELP"] = gh_help
    return subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True, env=env, timeout=15
    )


def test_passes_when_checks_pass():
    result = _run(gh_exit=0)
    assert result.returncode == 0
    assert result.stderr == ""


def test_fails_when_checks_fail():
    result = _run(gh_exit=1)
    assert result.returncode == 1
    assert "PR #42" in result.stderr


def test_gh_stderr_reaches_the_operator():
    """The point of #27: gh's own error must not be swallowed.

    A masked auth failure or rate limit reads as "CI checks failed" and sends
    the operator to look at a build that is fine. Re-adding `2>/dev/null` to
    the `gh pr checks` call turns this test red.
    """
    result = _run(gh_exit=1, gh_stderr="HTTP 401: Bad credentials")
    assert result.returncode == 1
    assert "HTTP 401: Bad credentials" in result.stderr


def test_unsupported_fail_any_is_named_not_reported_as_a_check_failure():
    """An older gh must be told apart from a red build."""
    result = _run(gh_exit=0, gh_help="")
    assert result.returncode == 2, "must not be confused with a check failure (1)"
    assert "--fail-any" in result.stderr
    assert "Upgrade gh" in result.stderr


def test_support_probe_does_not_run_the_gate():
    """If --fail-any is missing we must bail before watching anything.

    gh exits 0 here, so a version that ran the gate anyway would return 0 and
    let the merge proceed on an ungated PR.
    """
    result = _run(gh_exit=0, gh_help="")
    assert result.returncode == 2

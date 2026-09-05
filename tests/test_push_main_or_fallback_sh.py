"""Test that a failed `git push origin main` reprints git's own reason.

`2>/dev/null` on that push was #27 on the push path: a push to main fails for
plenty of reasons that are not branch protection (a stale ref, no network, a
bad credential), and swallowing the message sends the operator into the PR
fallback with the cause already gone. The capture that replaced it was
untested — the push sat below the `if [[ "${BASH_SOURCE[0]}" == "${0}" ]]`
guard, so sourcing could not reach it, and reverting to `2>/dev/null`, deleting
the reprint block, or swapping the redirection to `>/dev/null 2>&1` (so the
capture is always empty) all produced a run nothing looked at.

The push is now push_main_or_fallback(), above the guard. `git` is stubbed as
a shell function inside the test body, which overrides the real binary for the
sourced script without needing a fixture on PATH.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"

# What git writes on its own stderr. Measured on git 2.x: `git push` writes
# NOTHING to stdout — the "To <remote> ... [new branch]" line, "Everything
# up-to-date" and every fatal all go to stderr, on success and failure alike.
PUSH_STDERR = "remote: error: GH006: Protected branch update failed for refs/heads/main."
SUCCESS_STDERR = "To github.com:owner/repo.git\n   abc1234..def5678  main -> main"


def _drive(push_rc: int, push_stderr: str):
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        git() {{ printf '%s\\n' {push_stderr!r} >&2; return {push_rc}; }}
        merge_main_via_pr() {{ echo "FELL_BACK_TO_PR"; }}
        push_main_or_fallback
        echo "REACHED_END"
    """)
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True,
                          env=env, timeout=20)


def test_failed_push_reprints_gits_own_reason():
    """The whole point of the capture: git said why, so the operator sees why."""
    result = _drive(push_rc=1, push_stderr=PUSH_STDERR)
    assert result.returncode == 0, result.stdout + result.stderr
    assert PUSH_STDERR in result.stderr, (
        f"git's reason was swallowed; stderr was {result.stderr!r}"
    )
    assert "FELL_BACK_TO_PR" in result.stdout, result.stdout
    assert "REACHED_END" in result.stdout


def test_successful_push_reprints_nothing():
    """Negative control. The capture holds git's progress output on success too
    (it is on stderr), and reprinting it would quote a clean push back to the
    operator as if it were an error — so the reprint must be conditional on the
    push having actually failed, not unconditional.
    """
    result = _drive(push_rc=0, push_stderr=SUCCESS_STDERR)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Pushed to origin/main" in result.stdout, result.stdout
    assert "FELL_BACK_TO_PR" not in result.stdout, result.stdout
    assert SUCCESS_STDERR not in result.stderr, (
        f"a successful push was quoted back as an error: {result.stderr!r}"
    )


def test_failed_push_with_a_silent_git_still_falls_back():
    """Second negative control: an empty capture must not print a blank line,
    and must still reach the PR fallback. Guards the `-n "$PUSH_ERR"` test —
    without it, a silent failure prints an empty line and nothing else, which
    reads as a corrupted log rather than a diagnostic.
    """
    result = _drive(push_rc=1, push_stderr="")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FELL_BACK_TO_PR" in result.stdout, result.stdout
    assert result.stderr == "", f"printed something for an empty capture: {result.stderr!r}"

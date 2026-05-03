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

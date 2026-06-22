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


def _run_verify(
    pr_num: str,
    expected_base: str,
    gh_stdout: str,
    gh_exit: int = 0,
    tmpdir: Path | None = None,
):
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        verify_pr_base "{pr_num}" "{expected_base}"
    """)
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    env["MOCK_GH_STDOUT"] = gh_stdout
    env["MOCK_GH_EXIT"] = str(gh_exit)
    if tmpdir is not None:
        env["TMPDIR"] = str(tmpdir)
    env.pop("MOCK_GH_STDERR", None)  # explicit isolation: don't inherit from caller's shell
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


def test_verify_fail_on_mismatch_removes_tempfile(tmp_path):
    result = _run_verify("42", "develop", "main", tmpdir=tmp_path)
    assert result.returncode == 1
    assert list(tmp_path.glob("verify_pr_base.*")) == []


def test_verify_fail_open_on_gh_error():
    result = _run_verify("42", "develop", "", gh_exit=2)
    assert result.returncode == 0  # fail open
    # Visibility breadcrumb: gh failure should be observable in stderr.
    assert "verify_pr_base" in result.stderr
    assert "#42" in result.stderr


def test_verify_fail_open_includes_indented_gh_stderr():
    """When gh fails AND prints to stderr, the breadcrumb re-emits it indented."""
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        verify_pr_base "42" "develop"
    """)
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    env["MOCK_GH_STDOUT"] = ""
    env["MOCK_GH_STDERR"] = "HTTP 401: Bad credentials"
    env["MOCK_GH_EXIT"] = "2"
    result = subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True, env=env, timeout=10
    )
    assert result.returncode == 0  # fail open
    # Both the breadcrumb header AND the indented gh-stderr re-emit must be visible.
    assert "verify_pr_base" in result.stderr
    assert "    HTTP 401: Bad credentials" in result.stderr  # 4-space indent

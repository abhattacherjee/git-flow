"""Test scripts/git-flow-finish.sh:verify_pr_base via subprocess.

Sources the script in a subshell so we don't trigger the main /finish flow.
Stubs `gh` via the same fixture used by the hook tests.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"


def _verify_env(gh_stdout: str, gh_exit: int, tmpdir: Path | None):
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    env["MOCK_GH_STDOUT"] = gh_stdout
    env["MOCK_GH_EXIT"] = str(gh_exit)
    env.pop("MOCK_GH_STDERR", None)  # explicit isolation: don't inherit from caller's shell
    env.pop("MOCK_GH_SLEEP", None)
    if tmpdir is not None:
        env["TMPDIR"] = str(tmpdir)
    return env


def _verify_body(pr_num: str, expected_base: str) -> str:
    return textwrap.dedent(f"""
        source "{SCRIPT}"
        verify_pr_base "{pr_num}" "{expected_base}"
    """)


def _run_verify(
    pr_num: str,
    expected_base: str,
    gh_stdout: str,
    gh_exit: int = 0,
    tmpdir: Path | None = None,
):
    return subprocess.run(
        ["bash", "-c", _verify_body(pr_num, expected_base)],
        capture_output=True,
        text=True,
        env=_verify_env(gh_stdout, gh_exit, tmpdir),
        timeout=10,
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


# Tempfile lifetime ---------------------------------------------------------
#
# verify_pr_base captures gh's stderr to a tempfile. Normal returns delete it;
# the gap is a signal arriving while `gh pr view` is still blocking, which is
# the only window where the file exists with nothing yet to remove it. See
# issue #8.

def _residue(tmpdir: Path):
    return sorted(p.name for p in tmpdir.glob("verify_pr_base.*"))


def test_no_tempfile_residue_on_match(tmp_path):
    assert _run_verify("42", "develop", "develop", tmpdir=tmp_path).returncode == 0
    assert _residue(tmp_path) == []


def test_no_tempfile_residue_on_mismatch(tmp_path):
    assert _run_verify("42", "develop", "main", tmpdir=tmp_path).returncode == 1
    assert _residue(tmp_path) == []


def test_no_tempfile_residue_on_gh_error(tmp_path):
    assert _run_verify("42", "develop", "", gh_exit=2, tmpdir=tmp_path).returncode == 0
    assert _residue(tmp_path) == []


def test_no_tempfile_residue_when_killed_mid_gh_call(tmp_path):
    """SIGTERM while `gh pr view` blocks must still clean the tempfile.

    This is the case issue #8 reports and the one a RETURN trap does NOT
    cover: the function never returns, so only an EXIT-family trap runs.
    Reverting the trap in verify_pr_base turns this test red.

    Note bash defers a trap until the foreground child finishes, so cleanup
    lands once the stubbed `gh` returns rather than the instant the signal
    arrives. The wait below is sized for that, not for the signal.
    """
    env = _verify_env("develop", 0, tmp_path)
    env["MOCK_GH_SLEEP"] = "2"
    proc = subprocess.Popen(
        ["bash", "-c", _verify_body("42", "develop")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    # Wait for the tempfile to appear, so we are provably killing the shell
    # inside the window the leak lives in rather than before or after it.
    deadline = time.monotonic() + 5
    while not _residue(tmp_path):
        assert proc.poll() is None, "shell exited before creating the tempfile"
        assert time.monotonic() < deadline, "tempfile never appeared"
        time.sleep(0.01)

    proc.terminate()
    proc.wait(timeout=20)
    assert _residue(tmp_path) == []

"""Test scripts/git-flow-finish.sh:verify_pr_base via subprocess.

Sources the script in a subshell so we don't trigger the main /finish flow.
Stubs `gh` via the same fixture used by the hook tests.
"""
from __future__ import annotations

import os
import signal
import subprocess
import textwrap
import time

import pytest
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


RESUMED = "SCRIPT-RESUMED-PAST-VERIFY"


def _verify_body(pr_num: str, expected_base: str) -> str:
    """Body that echoes a sentinel after the call.

    At the real call site verify_pr_base is followed immediately by
    `gh pr merge --squash` against main, so "did the shell resume?" is the
    question that matters, not just "was the tempfile removed?".
    """
    return textwrap.dedent(f"""
        source "{SCRIPT}"
        verify_pr_base "{pr_num}" "{expected_base}"
        echo "{RESUMED}"
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
# verify_pr_base captures gh's stderr to a tempfile. Normal returns delete it.
# Issue #8 is about the file outliving a kill: the EXIT trap is armed, and the
# path known, before anything is created, so the whole function is covered
# rather than just the long `gh pr view` call that is easiest to hit.

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


@pytest.mark.parametrize(
    "sig", [signal.SIGTERM, signal.SIGHUP], ids=["sigterm", "sighup"]
)
def test_killed_mid_gh_call_cleans_up_and_still_dies(tmp_path, sig):
    """A signal during the blocking gh call must clean up AND kill the shell.

    Two independent regressions live here:

    * the tempfile leak of issue #8 — a RETURN trap does not fire, because
      under a signal the function never returns;
    * a trap on TERM/HUP that cleans up but does not re-raise, which returns
      the shell to where it was. At the call site that means resuming into
      `gh pr merge --squash` against main, exiting 0, after the operator's
      session is already gone. EXIT alone avoids this: bash runs it on the way
      out from a fatal signal without catching the signal.

    So this asserts all three: no residue, the shell died of the signal, and
    the sentinel after the call never printed.
    """
    env = _verify_env("develop", 0, tmp_path)
    env["MOCK_GH_SLEEP"] = "2"
    proc = subprocess.Popen(
        ["bash", "-c", _verify_body("42", "develop")],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        start_new_session=True,  # don't let job control mask the signal
    )
    # Wait for the tempfile to appear, so we are provably signalling inside the
    # window the leak lives in rather than before or after it.
    deadline = time.monotonic() + 5
    while not _residue(tmp_path):
        assert proc.poll() is None, "shell exited before creating the tempfile"
        assert time.monotonic() < deadline, "tempfile never appeared"
        time.sleep(0.01)

    proc.send_signal(sig)
    # Bash defers a trap until the foreground child finishes, so this waits out
    # the stubbed gh rather than the signal.
    out, _ = proc.communicate(timeout=30)

    assert _residue(tmp_path) == [], "tempfile left behind"
    assert proc.returncode == -sig, (
        f"expected death by {sig!r}, got rc={proc.returncode} — "
        "the signal was caught and swallowed instead of killing the shell"
    )
    assert RESUMED not in out, "shell resumed past verify_pr_base after a signal"


def test_caller_exit_trap_survives_verify(tmp_path):
    """verify_pr_base must put back an EXIT trap the caller already had."""
    marker = tmp_path / "caller-exit-ran"
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        trap 'touch {marker}' EXIT
        verify_pr_base "42" "develop"
    """)
    result = subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        env=_verify_env("develop", 0, tmp_path),
        timeout=10,
    )
    assert result.returncode == 0
    assert marker.exists(), "verify_pr_base clobbered the caller's EXIT trap"
    assert _residue(tmp_path) == []


def test_repeated_calls_leave_no_residue_or_stale_trap(tmp_path):
    """Calling twice must not leak, nor leave a trap pointing at a stale path."""
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        verify_pr_base "42" "develop"
        verify_pr_base "43" "develop"
        trap -p EXIT
    """)
    result = subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        env=_verify_env("develop", 0, tmp_path),
        timeout=10,
    )
    assert result.returncode == 0
    assert _residue(tmp_path) == []
    assert result.stdout.strip() == "", f"stale trap left armed: {result.stdout!r}"


def _start_blocking(tmpdir: Path):
    """Start verify_pr_base and return once its tempfile provably exists."""
    env = _verify_env("develop", 0, tmpdir)
    env["MOCK_GH_SLEEP"] = "2"
    proc = subprocess.Popen(
        ["bash", "-c", _verify_body("42", "develop")],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        start_new_session=True,
    )
    deadline = time.monotonic() + 5
    while not _residue(tmpdir):
        assert proc.poll() is None, "shell exited before creating the tempfile"
        assert time.monotonic() < deadline, "tempfile never appeared"
        time.sleep(0.01)
    return proc


def test_sigint_to_process_group_still_cleans_up(tmp_path):
    """SIGINT is what the EXIT trap alone has to cover.

    TERM and HUP have their own handlers, and every normal path removes the
    file directly, so this is the case that actually exercises the EXIT trap.
    Signalling the whole group kills the stubbed gh too, so bash exits from
    inside the call rather than running on to the normal cleanup.
    """
    proc = _start_blocking(tmp_path)
    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    out, _ = proc.communicate(timeout=30)

    assert _residue(tmp_path) == [], "EXIT trap did not remove the tempfile"
    assert RESUMED not in out


def test_tmpdir_with_spaces_and_quotes(tmp_path):
    """A hostile TMPDIR must survive the normal path."""
    nasty = tmp_path / "a dir 'with' \"quotes\" and $dollar"
    nasty.mkdir()
    result = _run_verify("42", "develop", "develop", tmpdir=nasty)
    assert result.returncode == 0
    assert _residue(nasty) == []


def test_hostile_tmpdir_survives_signal_path(tmp_path):
    """And must survive the trap path, which is where the quoting matters.

    The normal path removes the file with a properly quoted "$path", so it
    passes even if the trap string is built wrong. Only a signal actually runs
    the trap string, so that is where printf %q earns its place.
    """
    nasty = tmp_path / "a dir 'with' \"quotes\" and $dollar"
    nasty.mkdir()
    proc = _start_blocking(nasty)
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=30)

    assert _residue(nasty) == [], "trap string mangled the path"
    assert proc.returncode == -signal.SIGTERM
    assert RESUMED not in out


def test_tempfile_name_is_known_before_the_file_exists():
    """Structural guard on the ordering the kill tests can only catch by luck.

    `VAR=$(mktemp ...)` is not atomic: mktemp's child creates the file and only
    then does the parent finish the assignment. A signal in that gap runs the
    EXIT trap while the variable is still empty, so nothing is removed and the
    file survives. Measured at roughly 1 kill in 5, and 25 out of 25 once the
    gap is widened by 50ms.

    The kill tests above detect that ordering mistake about 20% of the time,
    which is too rare to rely on and would land as an intermittent failure
    rather than a clear one. So the ordering is asserted directly: arm the trap,
    take the name, then create the file.
    """
    body = SCRIPT.read_text()
    body = body[body.index("verify_pr_base() {"):]
    body = body[: body.index("\n}\n")]

    trap_at = body.index("trap 'rm_verify_pr_base_tmp' EXIT")
    name_at = body.index("mktemp -u")
    create_at = body.index("set -C")
    assert trap_at < name_at < create_at, (
        "order must be: arm EXIT trap, take the name, create the file"
    )
    assert '$(mktemp "' not in body, (
        "creating the file inside a command substitution reopens the race — "
        "take the name with `mktemp -u` and create it separately"
    )

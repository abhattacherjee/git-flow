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
    args_file = tmp_path / "gh-args.log"
    env["MOCK_GH_ARGS_FILE"] = str(args_file)
    proc = subprocess.Popen(
        ["bash", "-c", _verify_body("42", "develop")],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        start_new_session=True,  # don't let job control mask the signal
        preexec_fn=_reset_sigint,
    )
    _await_gh_running(proc, tmp_path, args_file)

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
    """An EXIT handler armed by the caller must still run after verify_pr_base.

    verify_pr_base used to arm and disarm its own EXIT trap around the
    tempfile, saving the caller's spec and restoring it in cleanup_gh_stderr.
    That restore was skipped on the `rm` failure path, so a caller's handler
    was silently dropped there. It now installs no trap at all, so nothing it
    does can drop one.
    """
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


# The `rm` FAILURE branch -----------------------------------------------
#
# Every test above reaches cleanup_gh_stderr with an `rm` that succeeds, so
# none of them says anything about what happens when it does not — a tempfile
# whose parent went read-only, or a path that is a directory. That branch has
# three separate jobs and each one is load-bearing on its own, so they are
# asserted separately. Pointing VERIFY_PR_BASE_TMP at a directory makes `rm -f`
# fail for real rather than through a stubbed `rm`.

def _cleanup_with_unremovable_path(tmp_path, tail: str = ""):
    target = tmp_path / "not-a-file"
    target.mkdir()
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        VERIFY_PR_BASE_TMP="{target}"
        cleanup_gh_stderr
        echo "{RESUMED}"
        echo "TMP_VAR=[${{VERIFY_PR_BASE_TMP}}]"
        {tail}
    """)
    return subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True,
        env=_verify_env("develop", 0, tmp_path), timeout=10,
    )


def test_a_failed_cleanup_does_not_abort_the_run(tmp_path):
    """cleanup_gh_stderr runs mid-flow under `set -e`, one step above the
    squash merge to main. A non-zero return there kills /finish over a
    tempfile that could not be deleted — a housekeeping problem aborting a
    release. It must return 0 and let the run continue.
    """
    result = _cleanup_with_unremovable_path(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert RESUMED in result.stdout, result.stdout


def test_a_failed_cleanup_says_why(tmp_path):
    """The reason must reach the operator rather than being swallowed. A
    tempfile that cannot be deleted holds gh's stderr, which can carry token
    material, so silence here leaves it on disk with nobody told.
    """
    result = _cleanup_with_unremovable_path(tmp_path)
    assert "could not remove" in result.stderr, result.stderr
    assert str(tmp_path / "not-a-file") in result.stderr, result.stderr


def test_a_failed_cleanup_leaves_the_path_set_for_the_exit_trap(tmp_path):
    """The EXIT trap removes whatever VERIFY_PR_BASE_TMP still points at, so
    clearing the variable on a failed `rm` would throw away the retry and
    guarantee the leak. The path has to survive the failure.
    """
    result = _cleanup_with_unremovable_path(tmp_path)
    assert f"TMP_VAR=[{tmp_path / 'not-a-file'}]" in result.stdout, result.stdout


def test_repeated_calls_leave_no_residue_and_keep_the_global_trap(tmp_path):
    """Calling twice must not leak, and must leave the ONE global EXIT handler
    armed and pointing at nothing stale.

    The old design disarmed EXIT on the way out of every call, so the check
    here was "no trap at all". A single handler armed for the script's whole
    life is the point of the redesign, so the check is now that it is still
    the global handler — `rm_verify_pr_base_tmp` reads VERIFY_PR_BASE_TMP at
    fire time, so a cleared variable, not a cleared trap, is what makes it
    stale-proof.
    """
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        verify_pr_base "42" "develop"
        verify_pr_base "43" "develop"
        trap -p EXIT
        echo "TMP_VAR=[${{VERIFY_PR_BASE_TMP}}]"
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
    assert "git_flow_finish_on_exit" in result.stdout, (
        f"global EXIT handler was disarmed: {result.stdout!r}"
    )
    assert "TMP_VAR=[]" in result.stdout, (
        f"tempfile path left set, so the trap would chase a stale path: {result.stdout!r}"
    )


# A shell that starts a job in the background sets SIGINT to SIG_IGN for it,
# and that disposition is inherited straight through pytest into the shells
# these tests drive. So `bash tests/run.sh &`, or any harness that backgrounds
# the suite, silently turns the SIGINT test into a shell that cannot receive
# SIGINT at all: it ignores the signal, waits out the stubbed gh and resumes —
# which looks exactly like the bug the test is there to catch. Measured: 5 of 5
# backgrounded runs failed that way while the same command in the foreground
# passed 10 of 10.
#
# Python does not undo this. `restore_signals` resets only the signals Python
# itself set to SIG_IGN, not one inherited from the parent shell. Resetting
# SIGINT to SIG_DFL in the child is what makes these tests mean the same thing
# whichever way the suite was launched.
def _reset_sigint():
    signal.signal(signal.SIGINT, signal.SIG_DFL)


# Signalling these tests at the right instant is the whole experiment, and
# "the tempfile exists" is NOT that instant: verify_pr_base creates the file
# and only then calls `gh`, so a shell that has not reached the gh call yet
# already satisfies it. Signalling there measured 4 failures in 20 runs on a
# loaded machine, in two different disguises — bash absorbing the signal and
# then completing the call normally (so the sentinel printed and the run
# looked like a shell that wrongly resumed), and bash absorbing it and then
# blocking in the stubbed gh until the test timed out.
#
# The `gh` stub appends its argv to MOCK_GH_ARGS_FILE as its very first
# action, before it sleeps. Waiting for that file means gh is running, which
# is what puts the shell inside the window these tests are about. The sliver
# between gh logging its argv and gh entering its sleep is safe: gh is in the
# process group either way, so the signal kills it and bash sees a child that
# died of the signal.
#
# The reason this is spelled out at such length is that it is the same defect
# the code under test has thrown three times already, wearing a fourth
# costume: a signal that diverges from the reality it stands for. The gate
# read an invented `--fail-any` flag and reported a green build as failing;
# the classification call read a checks table on the wrong stream and could be
# told "no checks reported" by third-party text; and here a readiness
# condition fired before the thing it claimed to be waiting for had started.
# In every case the observation looked right and referred to something else.
# Take "this proves we are in the window" as a claim to re-measure, not a fact.
def _await_gh_running(proc, tmpdir: Path, args_file: Path):
    deadline = time.monotonic() + 15
    while not (_residue(tmpdir) and args_file.exists()):
        assert proc.poll() is None, "shell exited before it reached the gh call"
        assert time.monotonic() < deadline, "gh never started"
        time.sleep(0.01)


def _start_blocking(tmpdir: Path):
    """Start verify_pr_base and return once it is provably inside the gh call."""
    env = _verify_env("develop", 0, tmpdir)
    env["MOCK_GH_SLEEP"] = "2"
    args_file = tmpdir / "gh-args.log"
    env["MOCK_GH_ARGS_FILE"] = str(args_file)
    proc = subprocess.Popen(
        ["bash", "-c", _verify_body("42", "develop")],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        start_new_session=True,
        preexec_fn=_reset_sigint,
    )
    _await_gh_running(proc, tmpdir, args_file)
    return proc


def test_sigint_to_process_group_still_cleans_up(tmp_path):
    """SIGINT is what the EXIT trap alone has to cover.

    TERM and HUP have their own handlers, and every normal path removes the
    file directly, so this is the case that actually exercises the EXIT trap.
    Signalling the whole group kills the stubbed gh too, so bash exits from
    inside the call rather than running on to the normal cleanup.

    IF THIS FAILS UNDER A PARALLEL TEST RUNNER, SUSPECT THE HARNESS FIRST.
    This test sends SIGINT to a whole process group and depends on that signal
    reaching the shell it started, so it is sensitive to how the suite was
    launched in a way ordinary tests are not. Two environment traps were
    measured here and are handled above — the shell must be provably inside
    the gh call before the signal is sent, and SIGINT must not already be
    SIG_IGN in the child. Both fixes are in the helpers, and the group is
    private (`start_new_session=True`), so signalling it cannot disturb a
    sibling run.

    That makes the suite safe to run several copies at once, which is how
    mutation batteries are run. It is NOT a promise about every runner:
    pytest-xdist is untested here. A failure of this one test under a runner,
    with the rest green, is far more likely to be that runner's process and
    signal handling than a regression in the EXIT trap — check it against a
    plain serial run before treating it as a real failure.
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

    The trap is no longer armed inside verify_pr_base — it is the script's one
    global EXIT handler, armed at top level — so "armed before mktemp" is now
    asserted against the whole file: the `trap ... EXIT` line must come before
    verify_pr_base is even defined, which is strictly before it can be called.
    """
    full = SCRIPT.read_text()

    trap_at = full.index("trap 'git_flow_finish_on_exit' EXIT")
    func_at = full.index("verify_pr_base() {")
    assert trap_at < func_at, (
        "the EXIT trap must be armed at top level, before verify_pr_base is "
        "defined — arming it later leaves a window where the tempfile exists "
        "unguarded"
    )

    body = full[func_at:]
    body = body[: body.index("\n}\n")]

    code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    assert "trap" not in code, (
        "verify_pr_base must install no trap of its own — an EXIT trap here "
        "clobbers the global handler, and disarming it on the way out is what "
        "used to drop a caller's handler"
    )
    name_at = body.index("mktemp -u")
    create_at = body.index("set -C")
    assert name_at < create_at, (
        "order must be: take the name, then create the file"
    )
    assert '$(mktemp "' not in body, (
        "creating the file inside a command substitution reopens the race — "
        "take the name with `mktemp -u` and create it separately"
    )

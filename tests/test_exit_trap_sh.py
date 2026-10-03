"""Test the script's one global EXIT handler.

It does two jobs: remove verify_pr_base's tempfile (issue #8) and print the
"nothing gated this" warning when /finish ends without reaching the closing
summary. The tests here are about the second job.

The second job is why the handler is global. CI_GATE_SKIPPED is set at the
PR-merge step and read by the summary ~350 lines later, with two `|| die`
calls and a run of unguarded commands under `set -e` in between (checkout
develop, pull, merge --no-ff, fetch, tag, bump-version.sh, add, commit). A
back-merge conflict on develop is ordinary — and by then main may already be
squash-merged on the remote with nothing having gated it, which the failure
does not undo. Without this handler the only trace is the ⚠️ from
wait_for_ci_checks, hundreds of lines up the scrollback.

The tests here exist because a handler that reconstructs facts is easy to get
wrong in ways nothing observes:

  - it must survive a failing cleanup call, because errexit is live inside an
    EXIT trap and an unguarded failure skips everything after it;
  - it must fire when the shell dies from a signal, where `$?` is 0 rather
    than the script's own 143 or 129;
  - it must never claim main was merged unless something confirmed the merge,
    because "main already carries this release" is what sends an operator to
    revert or force-push a main that may not have moved;
  - and a completed run must warn once, not twice.
"""
from __future__ import annotations

import os
import signal
import subprocess
import textwrap
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"

WARNING = "NO CI GATE RAN, and /finish did not complete"
MERGED_CLAIM = "already on the remote"


def _body(tail: str, flag: str = "true", merged: str = "true", extra: str = "") -> str:
    return textwrap.dedent(f"""
        source "{SCRIPT}"
        CI_GATE_SKIPPED={flag}
        MAIN_MERGED={merged}
        VERSION=v9.9.9
        NEXT_VERSION=9.9.10
        SOURCE_BRANCH=release/v9.9.9
        {extra}
        {tail}
    """)


def _env():
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    return env


def _drive(flag: str, tail: str, merged: str = "true", extra: str = ""):
    return subprocess.run(
        ["bash", "-c", _body(tail, flag, merged, extra)],
        capture_output=True, text=True, env=_env(), timeout=20,
    )


def _drive_signal(sig: int, tmp_path: Path, flag: str = "true", merged: str = "true"):
    """Run the handler to completion under a real fatal signal.

    A busy `while :; do :; done` rather than `sleep`: bash defers a signal
    until the foreground command finishes, so a script parked in `sleep 30`
    would not act on the signal for 30 seconds. The loop lets bash notice it
    between commands, which is what a shell blocked in `gh pr checks --watch`
    does when its terminal goes away.
    """
    ready = tmp_path / "ready"
    script = tmp_path / "sig.sh"
    script.write_text(_body('while :; do :; done', flag, merged,
                            extra=f': > "{ready}"'))
    proc = subprocess.Popen(
        ["bash", str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=_env(),
    )
    deadline = time.monotonic() + 15
    while not ready.exists():
        assert time.monotonic() < deadline, "script never reached the wait"
        assert proc.poll() is None, "script exited before it could be signalled"
        time.sleep(0.01)
    proc.send_signal(sig)
    out, err = proc.communicate(timeout=15)
    return proc.returncode, out, err


# Losing stdout is modelled with a BROKEN PIPE, not a closed descriptor,
# because that is both the real case (`finish | head`) and the only one that
# can be measured honestly. Closing fd 1 looks equivalent and is not: the next
# `$(...)` anywhere in the shell allocates fd 1 for its pipe, so "stdout"
# quietly becomes that pipe and the run reports whatever it likes. Measured
# under a closed fd 1 — a probe reading /dev/fd/1 came back holding the
# shell's own pending stdout, and the summary text turned up in the stderr
# capture. A reader that exits immediately gives EPIPE against a descriptor
# that stays real.
BROKEN_PIPE_WRAPPER = """#!/usr/bin/env bash
bash "$1" 2>"$2" | head -n 0
exit "${PIPESTATUS[0]}"
"""

# Closing stderr is a different matter and IS sound here: nothing in this
# handler runs a command substitution, so fd 2 cannot be reallocated under it.
# Order still matters — redirect stdout FIRST, then close stderr, or the
# capture file lands on fd 2.
CLOSE_STDERR_WRAPPER = """#!/usr/bin/env bash
exec 1>"$2"
exec 2>&-
exec bash "$1"
"""


def _run_with_closed_fd(tmp_path, wrapper: str, tail: str, flag: str = "true",
                        merged: str = "true", extra: str = ""):
    """Run a driver with one standard descriptor closed, capturing the other."""
    driver = tmp_path / "driver.sh"
    driver.write_text(_body(tail, flag, merged, extra))
    wrap = tmp_path / "wrap.sh"
    wrap.write_text(wrapper)
    capture = tmp_path / "capture.txt"
    result = subprocess.run(
        ["bash", str(wrap), str(driver), str(capture)],
        capture_output=True, text=True, env=_env(), timeout=20,
    )
    return result.returncode, capture.read_text()


def test_a_closed_stderr_does_not_rewrite_the_exit_status(tmp_path):
    """The handler's warning ends in `echo`s to stderr, and errexit is live
    inside an EXIT trap. Unguarded, a closed stderr — `/finish 2>&-`, a
    dropped pty — makes those echoes fail, aborts the handler before its
    `return 0`, and replaces the status the script was exiting with.

    2 is the wrong-base guard's own code. Reporting it as 1 turns "the base
    was wrong, nothing was merged" into an indistinguishable generic failure.
    """
    rc, _out = _run_with_closed_fd(
        tmp_path, CLOSE_STDERR_WRAPPER, "exit 2", merged="false")
    assert rc == 2, rc


def test_a_lost_stdout_still_gets_the_warning_on_stderr(tmp_path):
    """stdout and stderr are separate descriptors. `finish | head` takes the
    first away and leaves the second perfectly healthy.

    The reader exits, the first banner write takes SIGPIPE and the shell dies
    at 141 — well before the summary could report anything. The EXIT handler
    runs on a fatal signal too, and writes to stderr, so the one fact that
    must not be lost survives: this release reached main with nothing gating
    it. An ungated merge reported on stdout alone is reported nowhere.
    """
    rc, err = _run_with_closed_fd(
        tmp_path, BROKEN_PIPE_WRAPPER, "print_finish_summary\nexit 0")
    assert rc == 141, (rc, err)
    assert WARNING in err, (rc, err)


def test_warning_fires_when_finish_dies_after_an_ungated_merge():
    """`die` between the ungated merge and the summary must not lose the flag.

    This is the ordinary failure the handler was written for: main is already
    squash-merged with nothing gating it, and the run dies somewhere in the
    long unguarded stretch that follows.
    """
    result = _drive("true", 'die "Merge to develop failed"')
    assert result.returncode == 1, result.stdout + result.stderr
    assert WARNING in result.stderr, result.stderr
    assert "v9.9.9" in result.stderr, result.stderr
    assert "(exit 1)" in result.stderr, result.stderr


def test_warning_fires_when_errexit_kills_the_script():
    """Most of the run between the merge and the summary is unguarded commands
    under `set -e`, which exit without going through `die` at all."""
    result = _drive("true", "false")
    assert result.returncode == 1, result.stdout + result.stderr
    assert WARNING in result.stderr, result.stderr


def test_warning_survives_a_failing_cleanup(tmp_path):
    """Errexit is live inside an EXIT trap, so the cleanup call ahead of the
    warning must be guarded at the call site.

    Unguarded, one failing `rm` cost two things at once: the warning was never
    printed, and the handler's own failure replaced the status the script was
    exiting with. Pointing VERIFY_PR_BASE_TMP at a directory makes `rm -f`
    fail for real rather than simulating it.
    """
    adir = tmp_path / "adir"
    adir.mkdir()
    result = _drive("true", "exit 7", extra=f'VERIFY_PR_BASE_TMP="{adir}"')
    assert result.returncode == 7, result.stdout + result.stderr
    assert WARNING in result.stderr, result.stderr
    assert "(exit 7)" in result.stderr, result.stderr


def test_warning_fires_when_the_shell_is_killed_by_sigterm(tmp_path):
    """A dropped SSH session while /finish blocks in `gh pr checks --watch` is
    the case this warning exists for, and it is the one a status gate misses:
    `$?` inside the EXIT trap is 0 under a signal, not the 143 the script
    exits with. The warning must not depend on that status.
    """
    rc, _out, err = _drive_signal(signal.SIGTERM, tmp_path)
    assert rc == -signal.SIGTERM or rc == 143, rc
    assert WARNING in err, err
    # rc is 0 in the handler here, so there is no exit number to report and it
    # must not invent one.
    assert "(exit" not in err, err


def test_warning_fires_when_the_shell_is_killed_by_sighup(tmp_path):
    """The same for SIGHUP, which is what a closed terminal actually sends."""
    rc, _out, err = _drive_signal(signal.SIGHUP, tmp_path)
    assert rc == -signal.SIGHUP or rc == 129, rc
    assert WARNING in err, err


def test_an_abort_before_the_merge_does_not_claim_main_was_merged():
    """The wrong-base guard fires between the CI gate and the merge, and on
    that path main is untouched. Telling the operator main already carries the
    release is the exact input that prompts a revert or a force-push of main,
    so false information here is worse than no warning at all.

    The warning still has to appear — nothing was gated either way — but it
    may only ask the operator to check main's state, not assert it.
    """
    result = _drive("true", "exit 2", merged="false")
    assert result.returncode == 2, result.stdout + result.stderr
    assert WARNING in result.stderr, result.stderr
    assert MERGED_CLAIM not in result.stderr, result.stderr
    assert "Check" in result.stderr and "main" in result.stderr, result.stderr


def test_the_not_merged_wording_does_not_assert_main_is_untouched():
    """The flag can be stale in the other direction too: a signal landing
    between gh's server-side merge and the log_ok that records it leaves
    MAIN_MERGED false while main HAS moved. So the not-merged wording must
    not promise main is clean either — it can only say the merge was never
    confirmed.
    """
    result = _drive("true", "exit 2", merged="false")
    lowered = result.stderr.lower()
    for claim in ("main is untouched", "main was not merged", "nothing landed",
                  "main never moved"):
        assert claim not in lowered, result.stderr


def test_a_completed_run_warns_exactly_once():
    """The summary prints this warning on the success path, and the EXIT
    handler fires moments later on the way out. Two copies of the same ⚠️ in
    one run teaches the operator to skim past it, so the summary clears the
    flag once it has delivered the message.
    """
    result = _drive("true", "print_finish_summary\nexit 0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("NO CI GATE RAN") == 1, result.stdout
    assert WARNING not in result.stderr, result.stderr


def test_no_warning_when_the_gate_actually_ran():
    """Negative control. A failure with a green gate must not claim otherwise."""
    result = _drive("false", 'die "Merge to develop failed"')
    assert result.returncode == 1
    assert WARNING not in result.stderr, result.stderr


def test_the_handler_is_armed_before_anything_can_set_the_flag():
    """Structural: the trap line must precede handle_ci_gate_result, the only
    thing that sets CI_GATE_SKIPPED. Armed later, a die in between is silent.
    """
    full = SCRIPT.read_text()
    assert (full.index("trap 'git_flow_finish_on_exit' EXIT")
            < full.index("handle_ci_gate_result() {"))

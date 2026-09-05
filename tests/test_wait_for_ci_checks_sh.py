"""Test scripts/git-flow-finish.sh:wait_for_ci_checks and its call site,
resolve_pr_number and handle_ci_gate_result, via subprocess.

Sources the script in a subshell so we do not trigger the main /finish flow.
Stubs `gh` via the same fixture the hook tests use.

This gate is the last thing between a release branch and a squash merge to
main, and it has failed in five distinct ways. That history is recorded here
because each failure is a SHAPE the tests below are built against. It does not
record which tests catch which mutation: no docstring can know that set, and
every attempt to write it down here was stale within a round.

#27, round 1: the gate passed `--fail-any` to `gh pr checks`, a flag gh has
never had, so every run died on "unknown flag" — and with `2>/dev/null` on the
call, that death reached the operator as "CI checks failed" against a perfectly
green build. The gate uses the real `--fail-fast` flag now and never redirects
gh's output away from the operator.

Round 2: `gh pr checks --watch` does not wait for check runs to register. It
reports on whatever it can already see and returns immediately when there is
nothing there yet. Straight after `gh pr create`, "nothing yet" looks identical
to "this repo has no CI at all", and round 1 treated both the same: merge with
no gate. wait_for_ci_checks now waits up to GIT_FLOW_CHECKS_GRACE seconds for
checks to register before concluding there is nothing to gate on.

Round 2 also closed an injection hole in the classification call. Without
`--watch`, gh renders a full checks table on stdout — rows supplied by whatever
app posted each commit status, so third-party text — and puts its own "no
checks reported on ..." diagnostic on stderr. Capturing both with a plain
`2>&1` meant a check merely named or described "no checks reported" could make
a genuinely red build look like nothing to gate on. The classification call
captures stderr only (`2>&1 >/dev/null`).

Round 3 is the wait itself. `waited` advances by `$poll` whether or not any
time actually passed, so deleting the sleep leaves the loop racing 0 -> grace
in milliseconds and merging ungated exactly as before, with no difference in
output at all — only a clock can tell the two apart. Both interval variables
are validated as whole numbers: unvalidated, `1e9` hung /finish forever on bash
3.2 and `-5` skipped the gate outright. A gh failure inside the loop is echoed
rather than discarded, which was #27's own failure mode reappearing inside the
loop that fixes #27. The validators also carry a leading-zero rule:
`''|*[!0-9]*` checks characters only, so `08` and `010` passed and bash then
read them as octal.

Round 4 is about assertions that could not fail. `>/dev/null` on the gate call
was unobservable because the harness popped MOCK_GH_STDOUT, so the stub never
emitted gate stdout for anything to hide. The classification call's argv was
never pinned, so dropping `--repo` or aiming it at a literal PR number changed
nothing anyone looked at. `sleep 1` hardcoded and `waited + 1` were
indistinguishable because every timing test ran at poll=1, where the sleep
duration and the counter step are the same number. The harness also ran the
function as a bare statement, leaving errexit ACTIVE — production calls it as
`wait_for_ci_checks ... || CI_RC=$?`, which suppresses errexit for the whole
function body, so every assertion about an in-loop failure was made against a
shell mode production never uses. `_run` mirrors the real call site now.

Round 5 is the wiring, which nothing in this file can see. These tests source
the script and call one function, so they cannot tell whether the main flow
still calls it. The closing summary was the sharpest case: it was tested by
regex-extracting the `if $CI_GATE_SKIPPED` block out of the script and running
that COPY, which proves the block prints correctly if something runs it and
cannot tell whether anything does. The block is print_finish_summary() now and
the test on it calls that function rather than a copy.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"


def _run(gh_exit: int = 0, gh_stderr: str = "", gh_stdout: str = "", detail: str = "",
         detail_exit: int = 0,
         detail_seq: str | None = None, detail_stdout: str = "", grace: int | str | None = 0,
         poll: int | str | None = None, args_file: Path | None = None,
         pr_num: str = "42", repo: str = "owner/repo", timeout: int = 30):
    """Run wait_for_ci_checks in a subshell against the stubbed gh.

    `grace=None` leaves GIT_FLOW_CHECKS_GRACE unset so the function's own
    default applies; anything else (including a non-numeric string, for the
    validation tests) is exported verbatim. `poll` works the same way for
    GIT_FLOW_CHECKS_POLL.

    The body mirrors the PRODUCTION call site (merge_main_via_pr: `CI_RC=0;
    wait_for_ci_checks ... || CI_RC=$?`), not a bare statement. The two are
    different shell modes: a bare statement leaves errexit active, so the
    subshell aborts at the first non-zero command inside the function, while
    `|| rc=$?` suppresses errexit for the WHOLE function body. Measured on
    an in-loop gh failure, the bare form aborted at the failure and the
    production form ran on to "STILL RUNNING" and returned 7 — so testing
    the bare form asserted over a shell mode production never uses.
    """
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        rc=0
        wait_for_ci_checks "{pr_num}" "{repo}" || rc=$?
        exit "$rc"
    """)
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    env["MOCK_GH_EXIT"] = str(gh_exit)
    env["MOCK_GH_STDERR"] = gh_stderr
    env["MOCK_GH_DETAIL"] = detail
    env["MOCK_GH_DETAIL_EXIT"] = str(detail_exit)
    env["MOCK_GH_DETAIL_STDOUT"] = detail_stdout
    # Default grace=0, so a test that does not care about the wait costs
    # nothing. `grace=None` pops the variable instead — the only way from here
    # to exercise the production default rather than a value the test chose.
    if grace is None:
        env.pop("GIT_FLOW_CHECKS_GRACE", None)
    else:
        env["GIT_FLOW_CHECKS_GRACE"] = str(grace)
    if poll is None:
        env.pop("GIT_FLOW_CHECKS_POLL", None)
    else:
        env["GIT_FLOW_CHECKS_POLL"] = str(poll)
    # Set, not popped. While this was popped, the stub's `--watch` branch
    # emitted NOTHING on stdout, so there was no gate stdout for a
    # `>/dev/null` on the gate call to hide — and that redirection is what
    # takes away the checks table naming the failing check, one step above the
    # squash merge to main. The stub has to produce the output before any
    # assertion about suppressing it means anything.
    env["MOCK_GH_STDOUT"] = gh_stdout
    env.pop("MOCK_GH_SLEEP", None)
    if detail_seq is not None:
        env["MOCK_GH_DETAIL_SEQ"] = detail_seq
    else:
        env.pop("MOCK_GH_DETAIL_SEQ", None)
    if args_file is not None:
        env["MOCK_GH_ARGS_FILE"] = str(args_file)
    # The stub's per-call sequence counter must live at a path that is stable
    # across the separate `gh` processes of ONE run and unique between runs.
    # Deriving it inside the stub from $PPID did neither: every `gh` runs in
    # its own command-substitution subshell, so each call invented a fresh
    # counter and MOCK_GH_DETAIL_SEQ silently degraded to element 1 forever.
    # Always supplying it here means no test can regress into that.
    with tempfile.TemporaryDirectory() as counter_dir:
        env["MOCK_GH_DETAIL_SEQ_COUNTER"] = str(Path(counter_dir) / "seq")
        return subprocess.run(
            ["bash", "-c", body], capture_output=True, text=True, env=env, timeout=timeout
        )


def test_passes_when_all_checks_pass():
    result = _run(gh_exit=0)
    assert result.returncode == 0
    assert "did not pass" not in result.stderr
    assert "Nothing to gate on" not in result.stderr


def test_fails_when_a_check_fails():
    result = _run(gh_exit=1, detail="1 of 3 checks failed")
    assert result.returncode == 1
    assert "PR #42" in result.stderr


def test_gh_own_error_reaches_the_operator():
    """The point of #27: gh's own error must not be swallowed.

    A masked auth failure or rate limit reads as "CI checks failed" and sends
    the operator to look at a build that is fine. Re-adding `2>/dev/null` to
    the `gh pr checks --watch` GATE call turns this test red.

    The marker is deliberately unique and is emitted ONLY by the stub's
    `--watch` branch (MOCK_GH_STDERR). The function DOES echo `$detail`
    verbatim on the non-"no checks reported" path, but `$detail` here is the
    classification call's own stderr (MOCK_GH_DETAIL, "1 of 3 checks
    failed"), which never contains the marker. So this can only pass via the
    gate call's own stderr reaching the operator unredirected; there is no
    second path to green.
    """
    marker = "GH-GATE-STDERR-MARKER: HTTP 401: Bad credentials"
    result = _run(gh_exit=1, gh_stderr=marker, detail="1 of 3 checks failed", detail_exit=1)
    assert result.returncode == 1
    assert marker in result.stderr


def test_gives_up_after_grace_and_reports_no_gate():
    """Regression guard for the round-2 CRITICAL, no-wait side.

    With the grace window exhausted immediately (grace=0) and gh reporting
    "no checks reported" on every classification call, the function must
    give up and say plainly that nothing was gated — not silently continue
    as if checks had passed.
    """
    result = _run(gh_exit=1, detail="no checks reported on the 'x' branch", detail_exit=1, grace=0)
    assert result.returncode == 3
    assert "Nothing to gate on" in result.stderr
    assert "did not pass" not in result.stderr


def test_gate_invocation_passes_pr_repo_and_flags(tmp_path):
    """Asserts the FULL recorded argv of the GATE call by exact equality, not
    a handful of substring checks — a substring check still passes if an
    extra real flag rides along (e.g. `--required`, which narrows the gate
    to required checks only and would quietly weaken it).

    The classification call (no `--watch`) always runs first now, so the
    gate call is the LAST line recorded, not the first — this test derives
    which line to check rather than assuming a position, so it is not
    accidentally satisfied by the classification call's argv.

    Distinctive PR number and repo (not "42"/"owner/repo", which could
    coincidentally match a hardcoded literal elsewhere in the script) so this
    also catches: `--repo "$repo"` dropped, `"$pr_num"` replaced with a
    literal, or `--watch` dropped.
    """
    args_file = tmp_path / "gh_args.log"
    result = _run(gh_exit=0, args_file=args_file, pr_num="4242", repo="someowner/somerepo")
    assert result.returncode == 0
    lines = args_file.read_text().splitlines()
    assert lines, "gh was never invoked"
    # `next()` with no default raises StopIteration, which pytest surfaces as
    # a test ERROR rather than a clean failure — hence the default plus the
    # assertion below.
    gate_call = next((l for l in lines if "--watch" in l), None)
    assert gate_call is not None, "no --watch gate call was recorded"
    assert gate_call == "pr checks 4242 --repo someowner/somerepo --watch --fail-fast"


@pytest.mark.skipif(shutil.which("gh") is None, reason="real gh not installed")
def test_flags_passed_to_gh_pr_checks_are_real():
    """Contract test against the REAL installed gh, not the stub.

    This is the test that would have caught `--fail-any`: it never existed in
    any gh, so this test would have failed on day one instead of the gate
    silently no-op'ing for four months.
    """
    script_text = SCRIPT.read_text()
    flags: set[str] = set()
    for line in script_text.splitlines():
        if "gh pr checks" not in line:
            continue
        # Anchored to `--`, letters/hyphens only, so a trailing `;` or `then`
        # never rides along as part of the flag name.
        flags.update(re.findall(r"--[a-zA-Z][a-zA-Z-]*", line))
    # --repo takes a value we don't care about checking against --help; the
    # flags that matter are the boolean ones controlling the gate itself.
    flags.discard("--repo")
    assert flags, "no --flags found on a `gh pr checks` line; did the script change?"

    env = os.environ.copy()
    # Deliberately do NOT put the fixtures dir on PATH — this must hit the
    # real gh binary, not the stub.
    help_text = subprocess.run(
        ["gh", "pr", "checks", "--help"], capture_output=True, text=True, env=env, timeout=15
    ).stdout

    for flag in flags:
        assert flag in help_text, f"{flag!r} is not a real `gh pr checks` flag"


def test_waits_for_checks_to_register_then_gates_on_them(tmp_path):
    """THE regression test for the round-2 CRITICAL.

    `gh pr checks --watch` reports on whatever it can already see and
    returns immediately when there is nothing there yet — it does not wait
    for check runs to register. Without the grace loop, the very first
    classification call ("no checks reported...", made right after `gh pr
    create`) would be taken at face value and `wait_for_ci_checks` would
    return 3 ("nothing to gate on"), and the caller would proceed to merge to
    main completely ungated even though this repo does have CI.

    Here the classification call reports "no checks reported" once, then on
    the next poll reports real (failing) checks. A correct implementation
    waits through the first answer and gates on the second: rc == 1, and it
    must NOT report "Nothing to gate on" — checks WERE found and did fail.

    It leaves GIT_FLOW_CHECKS_POLL unset, so the one sleep it does runs at the
    production default. That sleep is spent either way, so the elapsed-time
    assertion below is free — measured, adding it did not change this test's
    runtime (~5.03s). What it buys is a check that the default is a real
    interval at RUNTIME: a default of 1 gates exactly as well as 5 and costs
    5x the API calls, and the value the shell actually reads is not something
    a source assertion can confirm.
    """
    args_file = tmp_path / "gh_args.log"
    started = time.monotonic()
    result = _run(
        gh_exit=1,
        detail_seq="no checks reported on the 'x' branch|1 of 2 checks passed",
        grace=10,
        args_file=args_file,
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 1
    assert "Nothing to gate on" not in result.stderr
    # One poll at the production default. 4.5s excludes a default of 1 (and
    # of 4); the upper bound excludes a default of 10. A clock cannot pin the
    # exact number — that is the source assertion's job, not this one's.
    assert 4.5 <= elapsed < 8.0, (
        f"one poll did not cost the production default of 5s (elapsed {elapsed:.2f}s)"
    )


def test_production_default_poll_literal_is_five():
    """Pins the poll default as a LITERAL, which a clock cannot do.

    A timing assertion cannot tell 5 from 4.9, and this cannot tell whether
    the variable is read at all — a source grep passes if the literal is right
    and the value goes unused. Both assertions are needed because each is
    blind to what the other sees.
    """
    body = SCRIPT.read_text()
    assert 'poll="${GIT_FLOW_CHECKS_POLL-5}"' in body, (
        "the production default for GIT_FLOW_CHECKS_POLL is no longer 5"
    )


def test_no_checks_path_does_not_claim_checks_passed():
    """The rc==3 (no gate ran) path must never say "CI checks passed" —
    that message belongs only to a genuine pass (rc==0), not to "there was
    nothing to check"."""
    result = _run(gh_exit=1, detail="no checks reported on the 'x' branch", detail_exit=1, grace=0)
    assert result.returncode == 3
    assert "CI checks passed" not in result.stdout
    assert "CI checks passed" not in result.stderr


@pytest.mark.parametrize(
    "detail",
    ["", "warning: API rate limit approaching"],
    ids=["silent", "with_warning"],
)
def test_check_description_cannot_fake_no_checks(detail):
    """Regression guard for the classification-call injection hole.

    Without `--watch`, gh renders a full checks TABLE on stdout, built from
    whatever text the app that posted each commit status supplied — a
    check's name or description is third-party, not gh's own. If the
    classification call captured stdout together with stderr (plain
    `2>&1`, no `>/dev/null`), a check merely named or described "no checks
    reported" would make wait_for_ci_checks treat a genuinely failing build
    as "nothing to gate on" and let it through to the squash merge on main.

    Here the (fake) table on stdout contains the literal phrase "no checks
    reported" and the real check genuinely fails (gate call exits 1).
    stderr (MOCK_GH_DETAIL) is parametrized over two realistic values:

    - "silent" (""): on a plain failing check, `gh pr checks` exits via
      cmdutil.SilentError (gh source: pkg/cmd/pr/checks/checks.go:249;
      SilentError is defined in pkg/cmdutil/errors.go:35) — gh's own
      convention for "exit non-zero, print nothing extra." Empty stderr
      is the realistic, correct case here, not a gap to "helpfully" fill
      in with a diagnostic message.
    - "with_warning" ("warning: API rate limit approaching"): a real gh
      diagnostic can still ride on stderr alongside a failing build. It
      must not change the verdict either.

    Correct behavior gates on the real failure in both cases: rc == 1.
    Dropping the `>/dev/null` (letting stdout back into $detail) turns
    both parametrized cases red by misclassifying them as rc == 3.
    """
    result = _run(
        gh_exit=1,
        detail=detail,
        detail_stdout="NAME              STATUS   no checks reported in the workflow log",
        grace=0,
    )
    assert result.returncode == 1
    assert "Nothing to gate on" not in result.stderr


def test_grace_loop_actually_sleeps_between_polls(tmp_path):
    """Asserts ELAPSED WALL TIME, because nothing else tells a waiting loop
    apart from a zero-delay one.

    `waited` advances by `$poll` whether or not any time actually passed. Delete
    `sleep "$poll"` (or shorten it to `sleep 0.01`) and the loop races 0 ->
    grace in milliseconds, prints "after 60s", returns 3, and the caller merges
    to main ungated on a repo whose checks had simply not registered yet — the
    round-2 bug, back in full. Every observable byte is identical either way:
    same output, same return code, same call sequence. Wall time is the only
    thing that differs, so this test measures it.

    Two "no checks reported" answers means two sleeps at poll=1, so a real
    loop cannot finish in under ~2s. 1.8s leaves slack for process startup
    without leaving room for a no-op sleep.
    """
    started = time.monotonic()
    result = _run(
        gh_exit=1,
        grace=10,
        poll=1,
        args_file=tmp_path / "gh_args.log",
        detail_seq="no checks reported on x|no checks reported on x|1 of 2 checks passed",
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 1
    assert elapsed >= 1.8, f"loop did not sleep between polls (elapsed {elapsed:.2f}s)"



def test_grace_loop_terminates_when_checks_never_register():
    """Pins that the loop ENDS. `waited` is what ends it.

    This test takes the one shape that has to count `waited` up to grace to get
    out at all: a non-zero grace, and gh saying "no checks reported" on every
    single call. A run with grace=0 returns on the first iteration before
    `waited` is added to, and a run whose stub eventually reports checks breaks
    out on the checks instead — neither exercises the counter. Freeze it
    (`waited=$((waited))`) and this loop never terminates: /finish hangs
    forever with a release branch half-merged.

    grace=3 at poll=1 is three real sleeps, ~3s; the 15s subprocess timeout is
    far above that and far below a hang.
    """
    started = time.monotonic()
    try:
        result = _run(
            gh_exit=1,
            grace=3,
            poll=1,
            timeout=15,
            detail="no checks reported on the 'x' branch",
            detail_exit=1,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "wait_for_ci_checks never returned with grace=3 and checks that "
            "never register — the grace loop hangs (is `waited` still "
            "advancing by $poll?)"
        )
    elapsed = time.monotonic() - started
    assert result.returncode == 3
    assert "Nothing to gate on" in result.stderr
    # The message must report the total the loop actually counted to, so a
    # counter that stops advancing cannot still print a plausible number.
    assert "after 3s" in result.stderr, result.stderr
    # Three `sleep 1`s cannot finish in under 3s. Guards against passing by
    # returning instantly.
    assert elapsed >= 3.0, f"loop returned before waiting out grace (elapsed {elapsed:.2f}s)"


def test_production_default_grace_is_a_real_wait():
    """Runs with GIT_FLOW_CHECKS_GRACE UNSET, so the production default is
    what the loop actually waits on.

    A grace default that is too short turns production into a merge to main
    ungated on the first "no checks reported" — precisely the state round 2
    was meant to end. A source assertion cannot see that: it reads the number
    without ever running the loop.

    gh reports "no checks reported" forever here, so a correct default keeps
    waiting and the call is still running when the timeout fires. With
    poll=1, a default grace of N returns 3 after ~N seconds, so TimeoutExpired
    at 8s proves the default is at least 8 — which is the whole value of this
    test, and also its ceiling: it cannot tell 8 from 60 without spending 60
    seconds per run. A default of 5 (plausibly shorter than GitHub takes to
    register checks on a busy repo) used to survive this test at timeout=3.

    Above 8, pinning the exact value is a job for a source assertion, not a
    clock. Behaviour and literal need separate assertions because neither can
    stand in for the other: this one proves the default makes the loop wait
    but cannot reach 60, and reading the source proves the number but no
    behaviour at all.
    """
    with pytest.raises(subprocess.TimeoutExpired):
        _run(gh_exit=1, grace=None, poll=1, timeout=8,
             detail="no checks reported on the 'x' branch", detail_exit=1)


def test_production_default_grace_literal_is_sixty():
    """Pins the DEFAULT ITSELF at 60, which no behavioural test can reach
    without a 60-second run.

    A test that waits on the real default can only prove a lower bound — it
    would have to spend 60 seconds to tell 60 from 8. This reads the source
    instead, which costs nothing and pins the exact number. `-` not `:-` is
    asserted too: with `:-`, an explicitly empty GIT_FLOW_CHECKS_GRACE= would
    silently take the default instead of being rejected as the mistake it is.
    """
    script_text = SCRIPT.read_text()
    assert 'local grace="${GIT_FLOW_CHECKS_GRACE-60}"' in script_text, (
        "the production grace default is no longer the literal 60 (or the "
        "`-` fallback became `:-`); see wait_for_ci_checks"
    )


@pytest.mark.parametrize(
    "grace",
    ["1e9", "abc", "-5", "", " ", "00", "08", "010"],
    ids=["scientific_notation", "not_a_number", "negative", "empty", "whitespace",
         "double_zero", "octal_08", "leading_zero_010"],
)
def test_bad_grace_value_dies_with_a_named_variable(grace):
    """Unvalidated, each of these was a distinct live failure on bash 3.2:

    - `1e9`: every `[[ $waited -ge $grace ]]` errors with "value too great for
      base" and compares FALSE, so /finish polls forever, holding the release
      branch open with a PR to main created but never merged. (Local main is
      NOT carrying the merge at that point — merge_main_via_pr resets it to
      origin/main before the gate runs.) An infinite hang, not a bad message.
    - `abc`: `bash: abc: unbound variable` kills the script without ever
      naming the variable the operator got wrong.
    - `-5`: compares true on the first pass, returns 3, gate silently skipped.
    - empty / whitespace: same silent-skip or hang, depending on the shell.
    - `00` / `08` / `010`: pass a character-class check ("all digits") but
      bash reads them as OCTAL. `08` errors "value too great for base" on
      every comparison and hangs exactly like `1e9`; `010` silently waits 8
      seconds where the operator asked for 10.

    All must die loudly and name GIT_FLOW_CHECKS_GRACE. die() writes through
    log_fail, which goes to stdout.
    """
    result = _run(gh_exit=1, grace=grace, detail="no checks reported on x", detail_exit=1, timeout=10)
    assert result.returncode == 1
    assert "GIT_FLOW_CHECKS_GRACE" in result.stdout


@pytest.mark.parametrize(
    "poll",
    ["abc", "-1", "", "0", "00", "08", "010"],
    ids=["not_a_number", "negative", "empty", "zero",
         "double_zero", "octal_08", "leading_zero_010"],
)
def test_bad_poll_value_dies_with_a_named_variable(poll):
    """Same validation for the poll interval, plus one case grace does not
    share: `0` is numeric and harmless-looking but turns the wait into a
    busy-loop hammering the API with no delay between calls, so it is
    rejected too.

    `00` is the reason the leading-zero rule matters more here than for
    grace: it is not caught by the `0)` arm (that arm matches the literal
    `0`), so a character-class-only validator lets it through and the wait
    becomes exactly the busy-loop that arm exists to prevent — measured at
    1914 gh calls in 8 seconds, never terminating.

    `08` and `010` break differently from the way they break grace, and both
    were measured on bash 3.2. `08` is not valid octal, so
    `waited=$((waited + 08))` is a FATAL expansion error: the shell dies right
    there with "value too great for base", and `|| rc=$?` at the call site does
    not catch it — so POLL=08 aborts after a single sleep rather than hanging.
    (The same error inside `[[ ]]` is non-fatal and evaluates false, which is
    why GRACE=08 hangs instead.) `010` IS valid octal: `sleep 010` is a true
    10.00s — sleep parses decimal, not octal — so the operator gets the
    interval they asked for, but `waited` advances by 8 per poll. At the
    default grace of 60 that is 8 sleeps of 10s where 6 were meant: 80 seconds
    of waiting, longer than asked, not shorter.
    """
    result = _run(gh_exit=1, grace=10, poll=poll, detail="no checks reported on x",
                  detail_exit=1, timeout=10)
    assert result.returncode == 1
    assert "GIT_FLOW_CHECKS_POLL" in result.stdout


def test_valid_grace_and_poll_are_accepted():
    """Negative control for the two validation tests above: a validator that
    rejected everything would pass both of them. Plain whole numbers, zero
    included, must still run the gate.
    """
    result = _run(gh_exit=0, grace=0, poll=5)
    assert result.returncode == 0


def test_transient_gh_error_during_the_wait_reaches_the_operator(tmp_path):
    """A gh failure inside the grace loop must not be swallowed.

    The loop's `case` treats anything that is not "no checks reported" as
    "checks exist, stop waiting" — and that includes gh itself failing. A
    transient `HTTP 502` on poll 2 ends the wait early, lands in `$detail`,
    and used to be DISCARDED. The operator then sees "CI checks did not pass"
    on a repo whose checks had never registered, with the real cause
    invisible: #27's exact failure mode, recreated inside the loop that fixes
    #27.

    The 502 text is emitted ONLY by the stub's classification branch
    (MOCK_GH_DETAIL_SEQ). The gate call's own stderr (MOCK_GH_STDERR) is empty
    here, so the assertion below can only go green via the loop echoing
    `$detail`; there is no second path.
    """
    result = _run(
        gh_exit=1,
        grace=10,
        poll=1,
        args_file=tmp_path / "gh_args.log",
        detail_seq="no checks reported on x|HTTP 502: Bad gateway",
    )
    assert result.returncode == 1
    assert "HTTP 502: Bad gateway" in result.stderr


def test_healthy_classification_call_prints_nothing_extra():
    """A healthy `gh pr checks` says nothing on stderr, so the echo that
    surfaces an in-loop gh failure must stay silent on a normal run rather
    than emit a blank line or a stray marker every time.

    `== ""`, not `.strip() == ""`: a blank line is exactly what dropping the
    `if [[ -n "$detail" ]]` guard produces, and `.strip()` swallows it. The
    stricter form is the only one that can see the difference this test is
    about.
    """
    result = _run(gh_exit=0, detail="", grace=0)
    assert result.returncode == 0
    assert result.stderr == ""


def test_gate_call_stdout_reaches_the_operator():
    """The gate call's STDOUT must not be redirected either.

    Without `2>/dev/null` the gate's stderr is covered, but `gh pr checks
    --watch` renders the checks TABLE on stdout — that table is what tells
    the operator WHICH check is red, one step above the squash merge to main.
    An assertion about suppressed stdout is worthless unless the stub emits
    stdout in the first place, so this test drives it with a marker rather
    than relying on the harness default.
    """
    marker = "GH-GATE-STDOUT-MARKER  build  fail"
    result = _run(gh_exit=1, gh_stdout=marker, detail="1 of 3 checks failed")
    assert result.returncode == 1
    assert marker in result.stdout


def test_classification_invocation_targets_the_same_pr_and_repo(tmp_path):
    """Asserts the FULL argv of the CLASSIFICATION call by exact equality.

    The classification call is what decides whether checks have registered.
    Point it at the wrong PR, or drop `--repo` and let gh infer one from the
    cwd, and the grace loop reasons about a different PR before waving the
    real one through. The stub ignores argv and answers every call the same
    way, so no assertion about behaviour can see either mistake — the argv
    log is the only place it shows.
    """
    args_file = tmp_path / "gh_args.log"
    result = _run(gh_exit=0, args_file=args_file, pr_num="4242", repo="someowner/somerepo")
    assert result.returncode == 0
    lines = args_file.read_text().splitlines()
    classify = next((l for l in lines if "--watch" not in l), None)
    assert classify is not None, "no classification call was recorded"
    assert classify == "pr checks 4242 --repo someowner/somerepo"


def test_wait_scales_with_the_poll_interval():
    """Pins that ONE poll costs `$poll` seconds AND advances `waited` by
    `$poll` — the two halves of the loop's clock.

    This runs at poll=2 for a reason. At poll=1 the sleep duration and the
    counter step are the same number, so a hardcoded `sleep 1` and a
    `waited=$((waited + 1))` counter both produce a run indistinguishable from
    a correct one. In production (poll=5, grace=60) the first cuts the real
    wait to 12s and merges to main ungated on a repo whose checks had not
    registered — the round-2 bug again; the second stretches a 60s wait to
    300s.

    grace=6 at poll=2 is three real sleeps: ~6s, and the message must say
    "after 6s". A hardcoded `sleep 1` finishes in ~3s; a `+ 1` counter needs
    six sleeps and takes ~12s. The window below excludes both.
    """
    started = time.monotonic()
    result = _run(gh_exit=1, grace=6, poll=2, timeout=25,
                  detail="no checks reported on the 'x' branch", detail_exit=1)
    elapsed = time.monotonic() - started
    assert result.returncode == 3
    assert "after 6s" in result.stderr, result.stderr
    assert 6.0 <= elapsed < 9.0, f"one poll did not cost $poll seconds (elapsed {elapsed:.2f}s)"


# ── resolve_pr_number ────────────────────────────────────────────────────

def _run_resolve_pr_number(pr_url: str):
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        resolve_pr_number {pr_url!r}
        echo "PR_NUMBER=$PR_NUMBER"
    """)
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True, timeout=15)


def test_resolve_pr_number_parses_pull_url():
    result = _run_resolve_pr_number("https://github.com/owner/repo/pull/4242")
    assert result.returncode == 0
    assert "PR_NUMBER=4242" in result.stdout


def test_resolve_pr_number_dies_loudly_on_unparseable_url():
    """Regression guard: `grep -oE '[0-9]+$'` used to return empty on any
    URL/error string that doesn't end in digits, and under `set -e` that
    died with no message at all, right after the script had already
    reported the PR created. This must instead die with a clear message
    naming the bad input."""
    result = _run_resolve_pr_number("gh: error: something went wrong")
    assert result.returncode == 1
    # die() (via log_fail) writes to stdout, not stderr.
    assert "Could not read a PR number" in result.stdout
    assert "gh: error: something went wrong" in result.stdout


# ── handle_ci_gate_result ────────────────────────────────────────────────

def _run_handle_ci_gate_result(rc: str, pr_num: str = "42"):
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        handle_ci_gate_result {rc!r} {pr_num!r}
        echo "CI_GATE_SKIPPED=$CI_GATE_SKIPPED"
        echo "REACHED_END"
    """)
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True, timeout=15)


def test_handle_ci_gate_result_pass_logs_passed():
    result = _run_handle_ci_gate_result("0")
    assert result.returncode == 0
    assert "CI checks passed on PR #42" in result.stdout
    assert "REACHED_END" in result.stdout
    # Negative control for the flag asserted below: a gate that actually ran
    # must not make the summary claim it was skipped.
    assert "CI_GATE_SKIPPED=false" in result.stdout


def test_handle_ci_gate_result_no_gate_skips_not_passes():
    """The regression this call site exists to prevent: rc==3 ("no gate
    ran") must log_skip, never log_ok "CI checks passed", and must not
    abort the merge. Restoring the old `wait_for_ci_checks || die` plus an
    unconditional `log_ok` collapses this case into "die" — killing this
    test twice over (wrong exit code, and losing "REACHED_END")."""
    result = _run_handle_ci_gate_result("3")
    assert result.returncode == 0
    assert "CI checks passed" not in result.stdout
    assert "No CI gate ran on PR #42" in result.stdout
    assert "REACHED_END" in result.stdout
    # rc==3 also has to survive to the END of the run. log_skip renders it at
    # the same weight as "No version files changed" and scrolls hundreds of
    # lines off the top, leaving the closing summary reading as an unqualified
    # success on a release that was merged to main with nothing gating it.
    # This flag is what the summary reprints the warning from.
    assert "CI_GATE_SKIPPED=true" in result.stdout


def test_summary_warns_when_no_ci_gate_ran():
    """The rc==3 flag must actually reach the operator at the end of the run.

    Calls the REAL print_finish_summary. This used to regex-extract the
    `if $CI_GATE_SKIPPED` block out of the script and run that text
    standalone, which proves the block prints correctly if something runs it
    and cannot tell whether anything does. The block is a function above the
    sourcing guard now, so there is no copy to drift.

    What this proves is the function's OUTPUT, with the flag both ways. It
    does NOT prove the main flow calls it: a call site can be deleted or
    short-circuited, and no test that invokes a function directly can see
    that. Only running the whole script can.
    """
    for flag, expected in (("true", True), ("false", False)):
        body = textwrap.dedent(f"""
            source "{SCRIPT}"
            CI_GATE_SKIPPED={flag}
            VERSION=v1.2.3
            SOURCE_BRANCH=release/v1.2.3
            NEXT_VERSION=1.2.4
            print_finish_summary
        """)
        result = subprocess.run(
            ["bash", "-c", body], capture_output=True, text=True, timeout=15
        )
        assert result.returncode == 0, result.stderr
        assert ("NO CI GATE RAN" in result.stdout) is expected, result.stdout
        if expected:
            assert "v1.2.3" in result.stdout


def test_summary_reports_the_bumped_version_it_was_given():
    """The summary must print the version it was handed.

    NEXT_VERSION is initialised empty so this function can be called under
    `set -u`, which means a summary that dropped the variable, or a bump that
    never computed one, prints `develop bumped to ` with nothing after it and
    still exits 0. Whether the printed value is real is asserted here and in
    the end-to-end runs, not by aborting inside the summary — that function
    runs after the release has already been merged, tagged and published.
    """
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        CI_GATE_SKIPPED=false
        VERSION=v1.2.3
        SOURCE_BRANCH=release/v1.2.3
        NEXT_VERSION=1.2.4
        print_finish_summary
    """)
    result = subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "develop bumped to 1.2.4" in result.stdout, result.stdout


def test_handle_ci_gate_result_failure_aborts():
    result = _run_handle_ci_gate_result("1")
    assert result.returncode == 1
    # die() (via log_fail) writes to stdout, not stderr.
    assert "Not merging PR #42" in result.stdout
    assert "REACHED_END" not in result.stdout

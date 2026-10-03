"""Test that merge_main_via_pr actually WIRES the CI gate to the merge.

Every piece of the gate is unit-tested in isolation — wait_for_ci_checks,
handle_ci_gate_result, resolve_pr_number — and none of that notices if the call
site stops calling them. This file exists because nothing used to EXECUTE
merge_main_via_pr at all, which left a set of edits that break the gate while
changing nothing a unit test looks at: deleting the wait_for_ci_checks call;
replacing `|| CI_RC=$?` with `|| true`, so a red build is reported as "CI
checks passed" one statement above the squash merge to main; deleting the
handle_ci_gate_result call; gating PR 999 instead of the one just created; and
making CI_GATE_SKIPPED a local of the function, which leaves every log line
byte-identical while the closing summary reads `false`. That is #27's own
shape: a dead gate, with the tests still green.

The tests below drive the call site with its side effects stubbed, and assert
on what reaches the handler and what survives to top level.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"


def _drive(gate_rc: int, real_handler: bool = False, overrides: str = ""):
    """Drive merge_main_via_pr with the gate and every side effect stubbed.

    With real_handler=False the two gate functions are replaced AFTER sourcing
    with recorders, so this asserts the call site's own behaviour: that it
    calls the gate, and that the gate's return code is what reaches
    handle_ci_gate_result.

    With real_handler=True only wait_for_ci_checks is stubbed, and the script's
    own handle_ci_gate_result runs — so the CI_GATE_SKIPPED assignment inside
    it has to land on the global the closing summary reads. The trailing
    echo reports the flag AT TOP LEVEL, after the function returned, which is
    the only place the summary can see it.

    `overrides` is bash appended after the stubs, for tests that need one of
    the merge steps to fail.
    """
    handler = "" if real_handler else 'handle_ci_gate_result() { echo "HANDLED rc=$1 pr=$2"; }'
    body = textwrap.dedent(f"""
        source "{SCRIPT}"
        REPO=owner/repo
        SOURCE_BRANCH=release/v1.2.3
        VERSION=v1.2.3
        git() {{ :; }}
        verify_pr_base() {{ :; }}
        wait_for_ci_checks() {{ echo "GATE_CALLED pr=$1 repo=$2"; return {gate_rc}; }}
        {handler}
        {overrides}
        merge_main_via_pr
        echo "TOPLEVEL_CI_GATE_SKIPPED=$CI_GATE_SKIPPED"
        echo "TOPLEVEL_MAIN_MERGED=$MAIN_MERGED"
        echo "REACHED_END"
    """)
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    env["MOCK_GH_STDOUT"] = "https://github.com/owner/repo/pull/4242"
    for k in ("MOCK_GH_STDERR", "MOCK_GH_EXIT", "MOCK_GH_SLEEP", "MOCK_GH_ARGS_FILE"):
        env.pop(k, None)
    return subprocess.run(["bash", "-c", body], capture_output=True, text=True,
                          env=env, timeout=20)


def test_call_site_runs_the_gate_on_the_pr_it_created():
    result = _drive(gate_rc=0)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GATE_CALLED pr=4242 repo=owner/repo" in result.stdout, result.stdout
    assert "REACHED_END" in result.stdout


def test_call_site_forwards_a_failing_gate_rc_verbatim():
    """`|| CI_RC=$?` is what carries rc to handle_ci_gate_result. Replacing
    it with `|| true` leaves CI_RC at 0, so a red build is logged as "CI
    checks passed" and squash-merged to main."""
    result = _drive(gate_rc=1)
    assert "HANDLED rc=1 pr=4242" in result.stdout, result.stdout


def test_call_site_forwards_the_no_gate_rc_verbatim():
    result = _drive(gate_rc=3)
    assert "HANDLED rc=3 pr=4242" in result.stdout, result.stdout


def test_call_site_forwards_a_passing_gate_rc():
    """Negative control: rc 0 must arrive as 0 too. Without it, a call site
    that hardcoded a non-zero rc would satisfy every assertion that a failing
    gate reaches the handler."""
    result = _drive(gate_rc=0)
    assert "HANDLED rc=0 pr=4242" in result.stdout, result.stdout


def test_no_gate_flag_survives_to_top_level():
    """The flag must be GLOBAL, not a local of merge_main_via_pr.

    Adding CI_GATE_SKIPPED to the function's own `local` line leaves every
    line of log output byte-identical — the ⏭️ still prints — while the
    closing summary hundreds of lines later reads `false` and reports an
    ungated release as a clean one. So assert the value at top level after
    the function returned, not what the function printed.
    """
    result = _drive(gate_rc=3, real_handler=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "⏭️" in result.stdout, result.stdout
    assert "TOPLEVEL_CI_GATE_SKIPPED=true" in result.stdout, result.stdout


def test_passing_gate_leaves_the_no_gate_flag_false():
    """A green gate must not raise the flag. Without this, the flag could be
    hardcoded true and still satisfy every assertion that a skipped gate is
    recorded."""
    result = _drive(gate_rc=0, real_handler=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TOPLEVEL_CI_GATE_SKIPPED=false" in result.stdout, result.stdout


def test_the_merge_flag_is_raised_once_the_merge_is_confirmed():
    """MAIN_MERGED is the only evidence the ungated-merge warning has that the
    squash merge landed. It has to be a global set by this call site: left
    unset, every abort after this point tells the operator main is clean when
    it is not.
    """
    result = _drive(gate_rc=3, real_handler=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TOPLEVEL_MAIN_MERGED=true" in result.stdout, result.stdout


def test_the_wrong_base_guard_aborts_without_raising_the_merge_flag():
    """The base guard fires between the CI gate and `gh pr merge`, so main is
    untouched when it aborts. If MAIN_MERGED were raised any earlier than the
    line that confirms the merge, this correctly-working guard would hand the
    operator "main already carries this release" — the one message that
    prompts a revert or force-push of a main that never moved.
    """
    result = _drive(gate_rc=3, real_handler=True,
                    overrides="verify_pr_base() { return 1; }")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "REACHED_END" not in result.stdout
    assert "NO CI GATE RAN" in result.stderr, result.stderr
    assert "already on the remote" not in result.stderr, result.stderr


def test_a_failed_gh_merge_aborts_without_raising_the_merge_flag():
    """The other abort in the same gap. `gh pr merge` failing means the merge
    did not happen, and the warning that follows must not say it did.
    """
    result = _drive(
        gate_rc=3, real_handler=True,
        overrides='gh() { if [[ "$2" == merge ]]; then return 1; fi; '
                  'echo "https://github.com/owner/repo/pull/4242"; }',
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "REACHED_END" not in result.stdout
    assert "NO CI GATE RAN" in result.stderr, result.stderr
    assert "already on the remote" not in result.stderr, result.stderr

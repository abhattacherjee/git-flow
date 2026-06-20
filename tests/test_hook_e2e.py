"""End-to-end hook tests — drive hooks/check-pr-base.py via subprocess.

Each test:
  1. Creates an isolated git repo with the requested branch layout.
  2. Stubs `gh` on PATH with canned stdout/exit code.
  3. Pipes the harness JSON payload to the hook on stdin.
  4. Asserts on (exit_code, stdout, stderr).
"""
from __future__ import annotations

import json


def _payload(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


def _assert_deny(stdout: str, *needles: str) -> dict:
    parsed = json.loads(stdout)
    reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
    for n in needles:
        assert n in reason, f"missing {n!r} in {reason!r}"
    assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
    return parsed


# 1
def test_create_missing_base_on_feature_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/test-block"], head="feature/test-block")
    code, out, err = run_hook(_payload("gh pr create --title test"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED", "feature/*", "explicit --base develop")


# 2
def test_create_wrong_base_on_feature_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/test-block"], head="feature/test-block")
    code, out, err = run_hook(_payload("gh pr create --base main --title test"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED", "main", "develop")


# 3
def test_create_correct_base_on_feature_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/test-block"], head="feature/test-block")
    code, out, err = run_hook(_payload("gh pr create --base develop --title test"), repo)
    assert code == 0
    assert out == ""


# 4
def test_merge_wrong_base_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    gh_stub('{"baseRefName":"main","headRefName":"feature/foo"}')
    code, out, err = run_hook(_payload("gh pr merge 42 --squash"), repo)
    assert code == 0
    _assert_deny(out, "PR #42", "main", "develop", "gh pr edit 42 --base develop")


# 5
def test_merge_correct_base_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    gh_stub('{"baseRefName":"develop","headRefName":"feature/foo"}')
    code, out, err = run_hook(_payload("gh pr merge 42"), repo)
    assert code == 0
    assert out == ""


# 6
def test_create_on_main_branch_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop"], head="main")
    code, out, err = run_hook(_payload("gh pr create --base main --title t"), repo)
    assert code == 0
    assert out == ""


# 7
def test_single_trunk_repo_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(_payload("gh pr create --title t"), repo)
    assert code == 0
    assert out == ""


# 8
def test_create_hotfix_correct_base_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "hotfix/v1.0.1"], head="hotfix/v1.0.1")
    code, out, err = run_hook(_payload("gh pr create --base main --title t"), repo)
    assert code == 0
    assert out == ""


# 9 — develop base is the back-merge target: must be allowed
def test_create_hotfix_develop_now_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "hotfix/v1.0.1"], head="hotfix/v1.0.1")
    code, out, err = run_hook(_payload("gh pr create --base develop --title t"), repo)
    assert code == 0
    assert out == ""


# 10 — develop base is the back-merge target: must be allowed
def test_create_release_develop_now_allowed(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "release/v1.0"], head="release/v1.0")
    code, out, err = run_hook(_payload("gh pr create --base develop --title t"), repo)
    assert code == 0
    assert out == ""


# 11
def test_unrelated_command_passthrough(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo()
    code, out, err = run_hook(_payload("echo hello"), repo)
    assert code == 0
    assert out == ""


# 12
def test_gh_failure_fails_open(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    gh_stub("", exit_code=2)
    code, out, err = run_hook(_payload("gh pr merge 42"), repo)
    assert code == 0
    assert out == ""


# 13
def test_chained_command_validates_each_segment(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload("gh pr create --base main --title t && echo done"), repo
    )
    assert code == 0
    _assert_deny(out, "BLOCKED")


# 14
def test_quoted_string_with_gh_pr_create_does_not_match(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload('echo "gh pr create --base main"'), repo
    )
    assert code == 0
    assert out == ""


# 15
def test_gh_pr_view_other_subcommand_passthrough(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo()
    code, out, err = run_hook(_payload("gh pr view 42"), repo)
    assert code == 0
    assert out == ""


# 16
def test_draft_create_missing_base_on_feature_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(_payload("gh pr create --draft --title t"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED")


# 17
def test_internal_exception_fails_open(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo()
    # Send malformed stdin: not valid JSON.
    import subprocess, sys
    from pathlib import Path
    REPO = Path(__file__).resolve().parent.parent
    proc = subprocess.run(
        [sys.executable, str(REPO / "hooks" / "check-pr-base.py")],
        input="not json at all",
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert proc.returncode == 0
    assert proc.stdout == ""
    assert "Traceback" in proc.stderr or proc.stderr  # any stderr output is fine


# 18 — Copilot review feedback: hotfix missing base
def test_create_hotfix_missing_base_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "hotfix/v1.0.1"], head="hotfix/v1.0.1")
    code, out, err = run_hook(_payload("gh pr create --title test"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED", "hotfix/*", "explicit --base main")


# 19 — Copilot review feedback: release missing base
def test_create_release_missing_base_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "release/v1.0"], head="release/v1.0")
    code, out, err = run_hook(_payload("gh pr create --title test"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED", "release/*", "explicit --base main")


# 20 — Issue #18: commit message mention must NOT be blocked
def test_commit_message_mention_allowed(temp_git_repo, gh_stub, run_hook):
    """git commit -m whose value mentions the PR subcommand must not be blocked.

    This runs on a feature/foo head so a false match WOULD deny — confirming the
    allow is due to quote-awareness, not a fall-through on a non-Git-Flow branch.
    """
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload('git commit -m "wip: do not run gh pr create --base main yet"'), repo
    )
    assert code == 0
    assert out == ""


# 21 — Issue #18: --body arg mention must NOT be blocked
def test_body_arg_mention_allowed(temp_git_repo, gh_stub, run_hook):
    """gh issue create --body whose value mentions the PR subcommand must not be blocked."""
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload('gh issue create --title x --body "next: gh pr create --base main"'),
        repo,
    )
    assert code == 0
    assert out == ""


# 22 — Issue #18: pipe-preceded 'gh pr create' must still be DENIED (true positive)
def test_pipe_real_create_denied(temp_git_repo, gh_stub, run_hook):
    """'gh pr create' following a pipe is a real invocation and must be denied."""
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload("echo args | gh pr create --base main --title t"), repo
    )
    assert code == 0
    _assert_deny(out, "BLOCKED")


# 23-26 — Issue #18 false-NEGATIVE regressions: prefix forms that old boundary
# model missed. All must be DENIED on a feature/* head with --base main.

# 23
def test_sudo_prefix_denied(temp_git_repo, gh_stub, run_hook):
    """'sudo gh pr create --base main' must be denied on feature/* head."""
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload("sudo gh pr create --base main --title t"), repo
    )
    assert code == 0
    _assert_deny(out, "BLOCKED")


# 24
def test_time_prefix_denied(temp_git_repo, gh_stub, run_hook):
    """'time gh pr create --base main' must be denied on feature/* head."""
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload("time gh pr create --base main --title t"), repo
    )
    assert code == 0
    _assert_deny(out, "BLOCKED")


# 25
def test_env_prefix_denied(temp_git_repo, gh_stub, run_hook):
    """'env GH_TOKEN=x gh pr create --base main' must be denied on feature/* head."""
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload("env GH_TOKEN=x gh pr create --base main --title t"), repo
    )
    assert code == 0
    _assert_deny(out, "BLOCKED")


# 26
def test_if_then_prefix_denied(temp_git_repo, gh_stub, run_hook):
    """'if true; then gh pr create --base main; fi' must be denied on feature/* head.

    The outer split_command_chain splits on ';', so 'then gh pr create --base main'
    becomes one segment. The _invokes_gh_pr helper must find the triple ['gh','pr',
    'create'] as consecutive tokens even though 'then' precedes it.
    """
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    code, out, err = run_hook(
        _payload("if true; then gh pr create --base main --title t; fi"), repo
    )
    assert code == 0
    _assert_deny(out, "BLOCKED")

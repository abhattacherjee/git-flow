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


# 9
def test_create_hotfix_wrong_base_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "hotfix/v1.0.1"], head="hotfix/v1.0.1")
    code, out, err = run_hook(_payload("gh pr create --base develop --title t"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED", "main")


# 10
def test_create_release_wrong_base_blocked(temp_git_repo, gh_stub, run_hook):
    repo = temp_git_repo(branches=["main", "develop", "release/v1.0"], head="release/v1.0")
    code, out, err = run_hook(_payload("gh pr create --base develop --title t"), repo)
    assert code == 0
    _assert_deny(out, "BLOCKED", "main")


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

"""Pure-function unit tests for hooks/check-pr-base.py."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "hooks" / "check-pr-base.py"


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("check_pr_base", HOOK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_pr_base"] = module
    spec.loader.exec_module(module)
    return module


hook = _load_hook_module()


# expected_base_for ----------------------------------------------------------

def test_expected_base_for_feature():
    assert hook.expected_base_for("feature/foo") == "develop"
    assert hook.expected_base_for("feature/sub/path") == "develop"


def test_expected_base_for_hotfix():
    assert hook.expected_base_for("hotfix/v1.0.1") == "main"


def test_expected_base_for_release():
    assert hook.expected_base_for("release/v2.0.0") == "main"


def test_expected_base_for_non_git_flow_returns_none():
    assert hook.expected_base_for("main") is None
    assert hook.expected_base_for("develop") is None
    assert hook.expected_base_for("chore/xyz") is None
    assert hook.expected_base_for("") is None
    assert hook.expected_base_for("feature") is None  # no slash, not feature/*


# parse_base_flag -----------------------------------------------------------

def test_parse_base_flag_long():
    assert hook.parse_base_flag("gh pr create --base develop --title t") == "develop"


def test_parse_base_flag_equals_form():
    assert hook.parse_base_flag("gh pr create --base=develop") == "develop"


def test_parse_base_flag_short():
    assert hook.parse_base_flag("gh pr create -B develop") == "develop"


def test_parse_base_flag_double_quoted():
    assert hook.parse_base_flag('gh pr create --base "develop"') == "develop"


def test_parse_base_flag_single_quoted():
    assert hook.parse_base_flag("gh pr create --base 'develop'") == "develop"


def test_parse_base_flag_missing_returns_none():
    assert hook.parse_base_flag("gh pr create --title t") is None


def test_parse_base_flag_shell_var_returns_literal():
    """Caller decides what to do with a $VAR value (currently: allow + warn)."""
    assert hook.parse_base_flag('gh pr create --base "$BASE"') == "$BASE"
    assert hook.parse_base_flag("gh pr create --base $BASE") == "$BASE"


# parse_pr_number -----------------------------------------------------------

def test_parse_pr_number_bare():
    assert hook.parse_pr_number("gh pr merge 42") == "42"


def test_parse_pr_number_with_flags_after():
    assert hook.parse_pr_number("gh pr merge 42 --squash --delete-branch") == "42"


def test_parse_pr_number_with_flags_before():
    assert hook.parse_pr_number("gh pr merge --squash 42") == "42"


def test_parse_pr_number_url_form():
    assert hook.parse_pr_number("gh pr merge https://github.com/o/r/pull/7") == "7"


def test_parse_pr_number_missing_returns_none():
    assert hook.parse_pr_number("gh pr merge") is None
    assert hook.parse_pr_number("gh pr merge --squash") is None


# split_command_chain -------------------------------------------------------

def test_split_chain_bare():
    assert hook.split_command_chain("gh pr create --base develop") == ["gh pr create --base develop"]


def test_split_chain_and():
    parts = hook.split_command_chain("gh pr create --base develop && echo done")
    assert [p.strip() for p in parts] == ["gh pr create --base develop", "echo done"]


def test_split_chain_semicolon():
    parts = hook.split_command_chain("a ; b ; c")
    assert [p.strip() for p in parts] == ["a", "b", "c"]


def test_split_chain_or():
    parts = hook.split_command_chain("a || b")
    assert [p.strip() for p in parts] == ["a", "b"]


def test_split_chain_mixed():
    parts = hook.split_command_chain("a && b ; c || d")
    assert [p.strip() for p in parts] == ["a", "b", "c", "d"]


import subprocess
import tempfile
import os


# current_branch / has_develop_branch ---------------------------------------

def test_current_branch_on_feature(temp_git_repo, monkeypatch):
    repo = temp_git_repo(branches=["main", "develop", "feature/foo"], head="feature/foo")
    monkeypatch.chdir(repo)
    assert hook.current_branch() == "feature/foo"


def test_current_branch_detached_returns_none(temp_git_repo, monkeypatch):
    repo = temp_git_repo()
    # Detach HEAD by checking out the commit hash directly.
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    subprocess.check_call(["git", "-C", str(repo), "checkout", sha], stderr=subprocess.DEVNULL)
    monkeypatch.chdir(repo)
    assert hook.current_branch() is None


def test_has_develop_branch_true(temp_git_repo, monkeypatch):
    repo = temp_git_repo(branches=["main", "develop"], head="main")
    monkeypatch.chdir(repo)
    assert hook.has_develop_branch() is True


def test_has_develop_branch_false_single_trunk(temp_git_repo, monkeypatch):
    repo = temp_git_repo(branches=["main"], head="main")
    monkeypatch.chdir(repo)
    assert hook.has_develop_branch() is False


# pr_refs_for / pr_for_branch -----------------------------------------------

def test_pr_refs_for_returns_tuple(gh_stub, monkeypatch, tmp_path):
    gh_stub('{"baseRefName":"main","headRefName":"feature/foo"}')
    monkeypatch.chdir(tmp_path)
    assert hook.pr_refs_for("42") == ("main", "feature/foo")


def test_pr_refs_for_gh_failure_returns_none(gh_stub, monkeypatch, tmp_path):
    gh_stub("", exit_code=2)
    monkeypatch.chdir(tmp_path)
    assert hook.pr_refs_for("42") is None


def test_pr_refs_for_malformed_json_returns_none(gh_stub, monkeypatch, tmp_path):
    gh_stub("not json")
    monkeypatch.chdir(tmp_path)
    assert hook.pr_refs_for("42") is None


def test_pr_for_branch_returns_number(gh_stub, monkeypatch, tmp_path):
    gh_stub('[{"number":42}]')
    monkeypatch.chdir(tmp_path)
    assert hook.pr_for_branch("feature/foo") == "42"


def test_pr_for_branch_no_pr_returns_none(gh_stub, monkeypatch, tmp_path):
    gh_stub("[]")
    monkeypatch.chdir(tmp_path)
    assert hook.pr_for_branch("feature/foo") is None


# Decision dataclass --------------------------------------------------------

def test_decision_allow_default():
    d = hook.Decision(allow=True)
    assert d.allow is True
    assert d.reason == ""


def test_decision_deny_with_reason():
    d = hook.Decision(allow=False, reason="oops")
    assert d.allow is False
    assert d.reason == "oops"


# Diagnostic templates ------------------------------------------------------

def test_diagnostic_wrong_base_pr_exists():
    msg = hook.diag_wrong_base_pr(pr_num="42", actual="main", expected="develop", branch_type="feature")
    assert "PR #42" in msg
    assert "ABORTING" in msg
    assert 'has base "main"' in msg
    assert 'expected "develop"' in msg
    assert "gh pr edit 42 --base develop" in msg


def test_diagnostic_wrong_base_create():
    msg = hook.diag_wrong_base_create(actual="main", expected="develop", branch_type="feature", rest_of_args="--title t")
    assert "BLOCKED" in msg
    assert "feature" in msg
    assert "gh pr create --base develop --title t" in msg


def test_diagnostic_missing_base_create():
    msg = hook.diag_missing_base_create(expected="develop", branch_type="feature", rest_of_args="--title t")
    assert "BLOCKED" in msg
    assert "explicit --base develop" in msg
    assert "gh pr create --base develop --title t" in msg

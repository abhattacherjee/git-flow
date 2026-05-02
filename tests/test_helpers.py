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

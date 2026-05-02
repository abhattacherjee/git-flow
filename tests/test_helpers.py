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

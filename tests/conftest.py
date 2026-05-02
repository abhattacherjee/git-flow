"""Shared fixtures for hook tests.

Three fixtures used across every test file:
    temp_git_repo  - isolated git repo with requested branches
    gh_stub        - prepends tests/fixtures/bin to PATH; per-call mocks via env
    run_hook       - invokes hooks/check-pr-base.py with stdin payload
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "hooks" / "check-pr-base.py"
GH_STUB_DIR = REPO_ROOT / "tests" / "fixtures" / "bin"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def temp_git_repo(tmp_path: Path):
    """Initialize a git repo with a customizable branch layout.

    Returns a callable: setup(branches=["main", "develop"], head="main") -> Path

    `branches` is created in order from an initial empty commit. `head` is
    the branch checked out at the end. Pass `branches` containing only "main"
    (no "develop") to simulate a single-trunk repo.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    # Initial empty commit so branches can be created.
    _git(repo, "commit", "--allow-empty", "-m", "init", "-q")

    def setup(branches=None, head="main") -> Path:
        if branches is None:
            branches = ["main", "develop"]
        for b in branches:
            if b == "main":
                continue
            # Create branch off main if it doesn't exist.
            existing = subprocess.run(
                ["git", "branch", "--list", b], cwd=repo, capture_output=True, text=True
            ).stdout.strip()
            if not existing:
                _git(repo, "branch", b)
        _git(repo, "checkout", head, "-q")
        return repo

    return setup


@pytest.fixture
def gh_stub(monkeypatch):
    """Prepend the gh stub dir to PATH so `gh` resolves to the stub.

    Returns a callable: set_response(stdout: str = "", exit_code: int = 0) -> None
    Sets MOCK_GH_STDOUT and MOCK_GH_EXIT on the test's env.
    """
    monkeypatch.setenv("PATH", f"{GH_STUB_DIR}:{os.environ['PATH']}")

    def set_response(stdout: str = "", exit_code: int = 0):
        monkeypatch.setenv("MOCK_GH_STDOUT", stdout)
        monkeypatch.setenv("MOCK_GH_EXIT", str(exit_code))

    set_response()  # default: empty stdout, exit 0
    return set_response


@pytest.fixture
def run_hook():
    """Invoke hooks/check-pr-base.py with a payload on stdin.

    Returns a callable that accepts:
        payload: dict   - the JSON to send on stdin
        cwd:     Path   - working directory for the subprocess
        env:     dict   - extra env vars (merged with os.environ)

    Returns (exit_code: int, stdout: str, stderr: str).
    """
    def run(payload: dict, cwd: Path, env: Optional[dict] = None) -> tuple[int, str, str]:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        proc = subprocess.run(
            [sys.executable, str(HOOK_SCRIPT)],
            input=json.dumps(payload),
            cwd=str(cwd),
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode, proc.stdout, proc.stderr

    return run

"""Test scripts/git-flow-finish.sh:log_repo_context via subprocess.

Sources the script via the BASH_SOURCE guard so the main flow is not invoked.
Tests that the function logs the repo name and remote URL (or "no remote").
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"


def _run_log_repo_context(repo_dir: Path, remote_url: str | None = None) -> subprocess.CompletedProcess:
    """Source the script and call log_repo_context in the given directory."""
    setup_remote = ""
    if remote_url is not None:
        setup_remote = f'git remote add origin "{remote_url}"'

    body = textwrap.dedent(f"""
        cd "{repo_dir}"
        {setup_remote}
        source "{SCRIPT}"
        log_repo_context
    """)
    env = os.environ.copy()
    # Ensure the gh stub doesn't interfere (functions don't call gh)
    env["PATH"] = f"{REPO_ROOT}/tests/fixtures/bin:{env['PATH']}"
    env["MOCK_GH_EXIT"] = "0"
    env["MOCK_GH_STDOUT"] = ""
    return subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True, env=env, timeout=10
    )


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo in tmp_path and return its path."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init", "-q"],
        cwd=repo, check=True, capture_output=True
    )
    return repo


def test_log_repo_context_no_remote(tmp_path):
    """With no remote configured, output should contain 'no remote' and the repo basename."""
    repo = _make_git_repo(tmp_path)
    result = _run_log_repo_context(repo)
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    combined = result.stdout + result.stderr
    assert "no remote" in combined, f"Expected 'no remote' in output:\n{combined}"
    assert "myrepo" in combined, f"Expected repo basename 'myrepo' in output:\n{combined}"


def test_log_repo_context_with_remote(tmp_path):
    """With a remote configured, output should contain the remote URL."""
    repo = _make_git_repo(tmp_path)
    remote_url = "https://github.com/example/myrepo.git"
    result = _run_log_repo_context(repo, remote_url=remote_url)
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    combined = result.stdout + result.stderr
    assert remote_url in combined, f"Expected remote URL in output:\n{combined}"
    assert "myrepo" in combined, f"Expected repo basename in output:\n{combined}"

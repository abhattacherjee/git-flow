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
    """Source the script and call log_repo_context in the given directory.

    log_repo_context never calls `gh`, so no gh stub is needed. The remote URL
    is passed via the TEST_REMOTE_URL env var (referenced quoted in the bash
    body) rather than f-string-interpolated, so shell metacharacters in the URL
    (e.g. '@' in a password) cannot break the command.
    """
    env = os.environ.copy()
    setup_remote = ""
    if remote_url is not None:
        env["TEST_REMOTE_URL"] = remote_url
        setup_remote = 'git remote add origin "$TEST_REMOTE_URL"'

    body = textwrap.dedent(f"""
        cd "{repo_dir}"
        {setup_remote}
        source "{SCRIPT}"
        log_repo_context
    """)
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
    """With no remote configured, output should contain 'no remote' and the Repo basename line."""
    repo = _make_git_repo(tmp_path)
    result = _run_log_repo_context(repo)
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    combined = result.stdout + result.stderr
    assert "no remote" in combined, f"Expected 'no remote' in output:\n{combined}"
    # Assert the exact Repo line (log() prepends two spaces, script uses 3-space gap).
    # This fails if basename is replaced by a constant — the Path: line alone is not enough.
    assert "Repo:   myrepo" in combined, f"Expected exact 'Repo:   myrepo' line in output:\n{combined}"


def test_log_repo_context_with_remote(tmp_path):
    """With a remote configured, output should contain the remote URL and the Repo basename line."""
    repo = _make_git_repo(tmp_path)
    remote_url = "https://github.com/example/myrepo.git"
    result = _run_log_repo_context(repo, remote_url=remote_url)
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    combined = result.stdout + result.stderr
    assert remote_url in combined, f"Expected remote URL in output:\n{combined}"
    assert "Repo:   myrepo" in combined, f"Expected exact 'Repo:   myrepo' line in output:\n{combined}"


def test_log_repo_context_not_a_git_repo(tmp_path):
    """In a directory with no git repo, output should show the '(not a git repo)' fallback."""
    non_repo = tmp_path / "plain"
    non_repo.mkdir()
    result = _run_log_repo_context(non_repo)
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    combined = result.stdout + result.stderr
    assert "(not a git repo)" in combined, f"Expected '(not a git repo)' fallback in output:\n{combined}"


def test_log_repo_context_redacts_credentials(tmp_path):
    """A credential-bearing remote URL must have its userinfo stripped before logging."""
    repo = _make_git_repo(tmp_path)
    remote_url = "https://user:s3cr3t@github.com/o/r.git"
    result = _run_log_repo_context(repo, remote_url=remote_url)
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    combined = result.stdout + result.stderr
    assert "s3cr3t" not in combined, f"Credential leaked in output:\n{combined}"
    assert "user:" not in combined, f"Userinfo leaked in output:\n{combined}"
    assert "github.com/o/r.git" in combined, f"Expected redacted host/path in output:\n{combined}"


def test_log_repo_context_redacts_at_in_password(tmp_path):
    """A password containing a literal '@' must be fully stripped (no tail leak).

    With a too-narrow sed ([^/@]*@) the redaction stops at the FIRST '@' and
    leaks the password tail ('ss-w0rd@'). The correct sed ([^/]*@) consumes to
    the LAST '@' before the path, removing the whole userinfo.
    """
    repo = _make_git_repo(tmp_path)
    remote_url = "https://bob:p@ss-w0rd@github.com/o/r.git"
    result = _run_log_repo_context(repo, remote_url=remote_url)
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"
    combined = result.stdout + result.stderr
    assert "p@ss-w0rd" not in combined, f"Full password leaked in output:\n{combined}"
    assert "ss-w0rd" not in combined, f"Password tail leaked in output:\n{combined}"
    assert "bob" not in combined, f"Username leaked in output:\n{combined}"
    assert "github.com/o/r.git" in combined, f"Expected redacted host/path in output:\n{combined}"

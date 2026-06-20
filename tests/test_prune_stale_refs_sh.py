"""Test scripts/git-flow-finish.sh:prune_stale_refs via subprocess.

Sources the script via the BASH_SOURCE guard so the main flow is not invoked.
Uses a PATH-shim stub for `git` that records arguments to a temp file so we
can assert the correct git subcommand was (or was not) invoked.

The stub only intercepts the `fetch` subcommand; all other git calls are
passed through to the real git binary so that sourcing the script itself
(which may call git rev-parse, etc.) works correctly.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"


def _run_prune(tmp_path: Path, dry_run: bool) -> tuple[subprocess.CompletedResult, Path]:
    """
    Run prune_stale_refs in a minimal git repo.

    Returns (completed_process, call_log_file).  The call_log_file records
    each `git fetch ...` invocation (one per line) written by the stub.
    """
    call_log = tmp_path / "git_fetch_calls.txt"

    # Build a git shim that records fetch calls and passes everything else
    # through to the real git.
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    git_real = subprocess.check_output(["which", "git"], text=True).strip()
    git_stub = stub_dir / "git"
    git_stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        if [[ "$1" == "fetch" ]]; then
          echo "git $*" >> "{call_log}"
          exit 0
        fi
        exec "{git_real}" "$@"
    """))
    git_stub.chmod(0o755)

    # We need a minimal real git repo for the script to source cleanly
    # (git rev-parse --show-toplevel is called by log_repo_context).
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init", "-q"], cwd=repo, check=True, capture_output=True)

    dry_run_val = "true" if dry_run else "false"
    body = textwrap.dedent(f"""
        cd "{repo}"
        source "{SCRIPT}"
        DRY_RUN={dry_run_val}
        prune_stale_refs
    """)
    env = os.environ.copy()
    env["PATH"] = f"{stub_dir}:{env['PATH']}"

    result = subprocess.run(
        ["bash", "-c", body], capture_output=True, text=True, env=env, timeout=10
    )
    return result, call_log


def test_prune_stale_refs_calls_git_fetch_when_not_dry_run(tmp_path):
    """With DRY_RUN=false, prune_stale_refs should call 'git fetch --prune origin'."""
    result, call_log = _run_prune(tmp_path, dry_run=False)
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"

    assert call_log.exists(), "git fetch was never called (call log not created)"
    calls = call_log.read_text()
    assert "fetch --prune origin" in calls, (
        f"Expected 'git fetch --prune origin' in recorded calls:\n{calls}"
    )

    combined = result.stdout + result.stderr
    assert "Pruned stale remote-tracking refs" in combined, (
        f"Expected success log line in output:\n{combined}"
    )


def test_prune_stale_refs_skips_git_fetch_when_dry_run(tmp_path):
    """With DRY_RUN=true, prune_stale_refs should NOT call git fetch."""
    result, call_log = _run_prune(tmp_path, dry_run=True)
    assert result.returncode == 0, f"Script failed:\n{result.stderr}"

    assert not call_log.exists(), (
        f"git fetch was called unexpectedly in dry-run mode:\n{call_log.read_text()}"
    )

    combined = result.stdout + result.stderr
    assert "DRY-RUN" in combined, f"Expected DRY-RUN skip line in output:\n{combined}"
    assert "would run git fetch --prune origin" in combined.lower() or "DRY-RUN" in combined, (
        f"Expected dry-run skip message:\n{combined}"
    )

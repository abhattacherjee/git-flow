"""The project's own .claude/hooks judge a worktree command where it runs (#54).

The harness runs each hook from the main checkout, with CLAUDE_PROJECT_DIR set
to it, and sends the Bash tool's real directory as `cwd` in the payload. A hook
that ignores that `cwd` judges a linked worktree's push or PR create against
the main checkout's branch. harden-repo 1.3.2 reads it (harden-repo#103).

Each test builds a main checkout on `develop` with a linked worktree on a
`feature/*` branch that has a CHANGELOG commit, then runs the hook the way the
harness does: process cwd and CLAUDE_PROJECT_DIR on the main checkout, payload
`cwd` on the worktree.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
FEATURE = "feature/x"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _registered_hooks() -> list[str]:
    settings = json.loads(SETTINGS.read_text())
    names = []
    for entry in settings["hooks"]["PreToolUse"]:
        for hook in entry["hooks"]:
            names.append(Path(hook["command"].split()[-1]).name)
    return names


@pytest.fixture
def worktree_layout(tmp_path: Path):
    """main checkout on develop + linked worktree on feature/x, with an origin."""
    origin = tmp_path / "origin.git"
    main = tmp_path / "main"
    wt = tmp_path / "wt"
    subprocess.run(["git", "init", "--bare", "-q", str(origin)], check=True)
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    _git(main, "config", "user.email", "t@example.com")
    _git(main, "config", "user.name", "t")
    _git(main, "config", "commit.gpgsign", "false")
    (main / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n")
    _git(main, "add", "CHANGELOG.md")
    _git(main, "commit", "-q", "-m", "init")
    _git(main, "branch", "develop")
    _git(main, "remote", "add", "origin", str(origin))
    _git(main, "push", "-q", "origin", "main", "develop")
    _git(main, "switch", "-q", "develop")
    _git(main, "worktree", "add", "-q", "-b", FEATURE, str(wt), "develop")
    (wt / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- a fix\n"
    )
    _git(wt, "commit", "-q", "-am", "fix: a fix")
    return main.resolve(), wt.resolve()


def _run(hook: str, command: str, main: Path, wt: Path) -> dict | None:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(wt),
    }
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["CLAUDE_PROJECT_DIR"] = str(main)
    proc = subprocess.run(
        [sys.executable, str(HOOKS_DIR / hook)],
        input=json.dumps(payload),
        cwd=main,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    return json.loads(out) if out else None


def _decision(result: dict | None) -> str:
    if result is None:
        return "allow"
    return result.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


@pytest.mark.parametrize("hook", _registered_hooks())
def test_every_registered_hook_reads_payload_cwd(hook):
    # The survey's "C": a hook copied from a template older than 1.3.2 has no
    # _payload_cwd, and judges worktree commands against the main checkout.
    assert "_payload_cwd" in (HOOKS_DIR / hook).read_text()


def test_bare_feature_push_from_worktree_is_allowed(worktree_layout):
    main, wt = worktree_layout
    result = _run("prevent-direct-push.py", f"git push --dry-run origin {FEATURE}", main, wt)
    assert _decision(result) != "deny", result


@pytest.mark.parametrize(
    "command",
    [
        "git push origin develop",
        "git push origin HEAD:develop",
        "git push origin main",
    ],
)
def test_push_to_protected_branch_from_worktree_is_denied(worktree_layout, command):
    main, wt = worktree_layout
    result = _run("prevent-direct-push.py", command, main, wt)
    assert _decision(result) == "deny", result


def test_bare_pr_create_from_worktree_passes_changelog_hook(worktree_layout):
    main, wt = worktree_layout
    result = _run(
        "update-changelog-before-pr.py",
        f"gh pr create --base develop --head {FEATURE} --title t --body b --dry-run",
        main,
        wt,
    )
    assert _decision(result) != "deny", result


def test_pr_create_without_changelog_entry_is_denied(worktree_layout):
    # Negative control: the allow above is not the hook failing open.
    main, wt = worktree_layout
    _git(wt, "reset", "-q", "--hard", "develop")
    (wt / "x.txt").write_text("x\n")
    _git(wt, "add", "x.txt")
    _git(wt, "commit", "-q", "-m", "feat: no changelog")
    result = _run(
        "update-changelog-before-pr.py",
        f"gh pr create --base develop --head {FEATURE} --title t --body b --dry-run",
        main,
        wt,
    )
    assert _decision(result) == "deny", result

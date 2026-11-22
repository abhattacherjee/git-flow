"""The project's own .claude/hooks judge a worktree command where it runs (#54).

The harness runs each hook from the main checkout, with CLAUDE_PROJECT_DIR set
to it, and sends the Bash tool's real directory as `cwd` in the payload. A hook
that ignores that `cwd` judges a linked worktree's push or PR create against
the main checkout's branch. harden-repo 1.3.2 reads it (harden-repo#103).

Each test builds a main checkout on `develop` with a linked worktree on a
`feature/*` branch that has a CHANGELOG commit, then runs the hook the way the
harness does: process cwd and CLAUDE_PROJECT_DIR on the main checkout, payload
`cwd` on the worktree. The mirror layout (main on `feature/x`, worktree on
`develop`) proves the hook reads the branch from the payload cwd.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"
FEATURE = "feature/x"
EXPECTED_HOOKS = {
    "prevent-direct-push.py",
    "require-preflight.py",
    "validate-branch-name.py",
    "update-changelog-before-pr.py",
}


def _clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=_clean_env(),
    ).stdout.strip()


def _registered_hooks() -> list[str]:
    settings = json.loads(SETTINGS.read_text())
    names = []
    for entry in settings["hooks"]["PreToolUse"]:
        for hook in entry["hooks"]:
            names.append(Path(hook["command"].split()[-1]).name)
    return names


def _build_layout(tmp_path: Path, main_branch: str, wt_branch: str):
    """main checkout on *main_branch* + linked worktree on *wt_branch*, with an origin.

    Whichever of the two is on the feature branch carries a CHANGELOG commit.
    """
    root = tmp_path / ("main-" + main_branch.replace("/", "-"))
    root.mkdir()
    origin = root / "origin.git"
    main = root / "main"
    wt = root / "wt"
    subprocess.run(
        ["git", "init", "--bare", "-q", str(origin)], check=True, env=_clean_env()
    )
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
    if main_branch == "develop":
        _git(main, "switch", "-q", "develop")
        _git(main, "worktree", "add", "-q", "-b", FEATURE, str(wt), "develop")
        feature_dir = wt
    else:
        _git(main, "switch", "-q", "-c", FEATURE, "develop")
        _git(main, "worktree", "add", "-q", str(wt), "develop")
        feature_dir = main
    assert _git(main, "branch", "--show-current") == main_branch
    assert _git(wt, "branch", "--show-current") == wt_branch
    (feature_dir / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n- a fix\n"
    )
    _git(feature_dir, "commit", "-q", "-am", "fix: a fix")
    return main.resolve(), wt.resolve()


@pytest.fixture
def worktree_layout(tmp_path: Path):
    """main checkout on develop + linked worktree on feature/x, with an origin."""
    return _build_layout(tmp_path, "develop", FEATURE)


@pytest.fixture
def mirror_layout(tmp_path: Path):
    """The reverse: main checkout on feature/x, linked worktree on develop."""
    return _build_layout(tmp_path, FEATURE, "develop")


def _run(hook: str, command: str, main: Path, wt: Path) -> dict | None:
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(wt),
    }
    env = _clean_env()
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


def _reason(result: dict | None) -> str:
    if result is None:
        return ""
    return result.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


def _calls_payload_cwd(path: Path) -> bool:
    """Whether the module contains a Call to a function named _payload_cwd."""
    tree = ast.parse(path.read_text())
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_payload_cwd"
        for node in ast.walk(tree)
    )


def test_registry_is_exactly_the_four_hooks():
    # Positive control: an empty PreToolUse list would collect zero cases below
    # and pass.
    registered = _registered_hooks()
    assert set(registered) == EXPECTED_HOOKS, registered
    assert len(registered) == len(EXPECTED_HOOKS), registered
    for hook in EXPECTED_HOOKS:
        assert (HOOKS_DIR / hook).is_file(), hook


@pytest.mark.parametrize("hook", sorted(EXPECTED_HOOKS))
def test_every_registered_hook_reads_payload_cwd(hook):
    # The survey's "C": a hook copied from a template older than 1.3.2 has no
    # call to _payload_cwd, and judges worktree commands against the main
    # checkout. A comment or an unused def does not count; a Call does.
    assert _calls_payload_cwd(HOOKS_DIR / hook), hook


def test_payload_cwd_check_ignores_comments_and_unused_defs(tmp_path):
    # Control for the check itself: it must not pass on a mention or a def.
    fake = tmp_path / "fake.py"
    fake.write_text("# _payload_cwd(x)\ndef _payload_cwd(d):\n    return None\n")
    assert not _calls_payload_cwd(fake)
    fake.write_text("def _payload_cwd(d):\n    return None\n_payload_cwd({})\n")
    assert _calls_payload_cwd(fake)


def test_bare_feature_push_from_worktree_is_allowed(worktree_layout):
    main, wt = worktree_layout
    result = _run("prevent-direct-push.py", f"git push --dry-run origin {FEATURE}", main, wt)
    assert _decision(result) != "deny", result


def test_dry_run_push_to_protected_branch_is_denied(worktree_layout):
    # --dry-run is not a silent skip: the same flag on a protected branch denies.
    main, wt = worktree_layout
    result = _run("prevent-direct-push.py", "git push --dry-run origin develop", main, wt)
    assert _decision(result) == "deny", result
    assert "develop" in _reason(result), result


@pytest.mark.parametrize(
    "command", ["git push origin HEAD", "git push", "git push --dry-run origin HEAD"]
)
def test_branch_dependent_push_reads_branch_from_payload_cwd(
    worktree_layout, mirror_layout, command
):
    # Same command, opposite layouts. Allowed when the payload cwd is on
    # feature/x (main checkout on develop); denied when the payload cwd is on
    # develop (main checkout on feature/x). Only a hook that reads the branch
    # from the payload cwd gets both right.
    main, wt = worktree_layout
    allowed = _run("prevent-direct-push.py", command, main, wt)
    assert _decision(allowed) != "deny", allowed
    main, wt = mirror_layout
    denied = _run("prevent-direct-push.py", command, main, wt)
    assert _decision(denied) == "deny", denied
    assert "develop" in _reason(denied), denied


@pytest.mark.parametrize(
    ("command", "branch"),
    [
        ("git push origin develop", "develop"),
        ("git push origin HEAD:develop", "develop"),
        ("git push origin main", "main"),
    ],
)
def test_push_to_protected_branch_from_worktree_is_denied(worktree_layout, command, branch):
    main, wt = worktree_layout
    result = _run("prevent-direct-push.py", command, main, wt)
    assert _decision(result) == "deny", result
    assert branch in _reason(result), result


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
    # Negative control: the allow above is not the hook failing open. The
    # reason must show the hook counted the WORKTREE's commit: read from the
    # main checkout (on develop) it would say 0 commit(s).
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
    reason = _reason(result)
    assert "CHANGELOG" in reason, reason
    assert "has 1 commit(s)" in reason, reason
    assert "feat: no changelog" in reason, reason


# --- require-preflight: the token is bound to a checkout -------------------


def _write_token(main: Path, bound_to: Path) -> Path:
    """Write the token commit-preflight.sh would write, bound to *bound_to*."""
    common = os.path.realpath(
        _git(main, "rev-parse", "--path-format=absolute", "--git-common-dir")
    )
    path = Path(f"/tmp/.preflight-token-{hashlib.md5(common.encode()).hexdigest()[:8]}")
    now = int(time.time())
    path.write_text(
        json.dumps(
            {
                "created": now,
                "expires": now + 300,
                "staged_files": 0,
                "checks_run": "lint",
                "head": _git(bound_to, "rev-parse", "HEAD"),
                "tree": _git(bound_to, "write-tree"),
                "worktree": os.path.realpath(bound_to),
            }
        )
    )
    return path


def test_preflight_token_for_the_worktree_passes_a_worktree_commit(worktree_layout):
    # Payload cwd = worktree, process cwd = main. The token is bound to the
    # worktree, so the commit is allowed only if the hook checks the worktree.
    main, wt = worktree_layout
    token = _write_token(main, wt)
    try:
        result = _run("require-preflight.py", "git commit -m msg", main, wt)
    finally:
        token.unlink(missing_ok=True)
    assert _decision(result) != "deny", result
    assert "Preflight verified" in json.dumps(result), result


def test_preflight_token_for_main_blocks_a_worktree_commit(worktree_layout):
    # Mirror: a token bound to the main checkout must not cover a commit made
    # in the worktree.
    main, wt = worktree_layout
    token = _write_token(main, main)
    try:
        result = _run("require-preflight.py", "git commit -m msg", main, wt)
    finally:
        token.unlink(missing_ok=True)
    assert _decision(result) == "deny", result
    assert "another checkout" in _reason(result), result


# --- validate-branch-name: a relative `git -C` walks from the payload cwd --


def test_branch_name_relative_git_dir_resolves_from_payload_cwd(worktree_layout, tmp_path):
    # `ext` exists only in the worktree, as a link to a directory outside the
    # project. Walked from the payload cwd it leaves the project, so the guard
    # skips; with no usable payload cwd the relative path cannot be pinned, and
    # "cannot tell" runs the guard.
    main, wt = worktree_layout
    outside = tmp_path / "outside"
    outside.mkdir()
    (wt / "ext").symlink_to(outside)
    bad = "git checkout -b badname"
    # Control: the same bad name, with no cd, is denied (not a silent skip).
    denied = _run("validate-branch-name.py", bad, main, wt)
    assert _decision(denied) == "deny", denied
    assert "badname" in _reason(denied), denied
    skipped = _run("validate-branch-name.py", "git -C ext checkout -b badname", main, wt)
    assert _decision(skipped) != "deny", skipped

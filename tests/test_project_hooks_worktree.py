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
import contextlib
import json
import os
import shlex
import shutil
import subprocess
import sys
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
    """The environment for fixture git calls and hook runs.

    Drops every GIT_* variable, then isolates from the developer's global and
    system git config (push.default=matching, hooksPath, ...) so a test result
    cannot depend on the machine it runs on.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


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
        assert entry["matcher"] == "Bash", entry
        for hook in entry["hooks"]:
            # Exactly `python3 <hook path>`: a wrapper such as
            # `true .claude/hooks/x.py` would register a hook that never runs.
            argv = shlex.split(hook["command"])
            assert len(argv) == 2 and argv[0] == "python3", hook["command"]
            assert argv[1].startswith(".claude/hooks/"), hook["command"]
            script = REPO_ROOT / argv[1]
            assert script.is_file(), hook["command"]
            assert script.parent == HOOKS_DIR, hook["command"]
            names.append(script.name)
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
    _git(main, "config", "push.default", "simple")
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
    # The hook names no destination: its reason lists both protected branches
    # as boilerplate. The only judged-state line is "Current branch: <name>".
    # The worktree is on feature/x, so a deny here can only come from the
    # destination.
    assert f"Current branch: {FEATURE}" in _reason(result), result


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
    # The branch-specific line, not the boilerplate that names main and develop.
    assert "Current branch: develop" in _reason(denied), denied


@pytest.mark.parametrize(
    "command",
    [
        "git push origin develop",
        "git push origin HEAD:develop",
        "git push origin main",
        f"git push --dry-run origin {FEATURE}:develop",
        f"git push --dry-run origin {FEATURE}:main",
        "git push --dry-run origin HEAD:develop",
    ],
)
def test_push_to_protected_branch_from_worktree_is_denied(worktree_layout, command):
    # Explicit destinations, including `feature/x:develop`, are judged on the
    # destination: a hook that ignores them and trusts the current branch
    # (feature/x here) would allow every one of these.
    main, wt = worktree_layout
    result = _run("prevent-direct-push.py", command, main, wt)
    assert _decision(result) == "deny", result
    # The reason names no destination (see the dry-run test above); this line
    # shows the deny came from the destination, not the current branch.
    assert f"Current branch: {FEATURE}" in _reason(result), result


@pytest.mark.parametrize(
    "command",
    [
        f"git push --dry-run origin {FEATURE}",
        f"git push --dry-run origin {FEATURE}:{FEATURE}",
        f"git push --dry-run origin HEAD:{FEATURE}",
    ],
)
def test_explicit_feature_destination_from_worktree_is_allowed(worktree_layout, command):
    # The mirror of the denials above: same shape, feature destination.
    main, wt = worktree_layout
    result = _run("prevent-direct-push.py", command, main, wt)
    assert _decision(result) != "deny", result


def test_bare_pr_create_from_worktree_passes_changelog_hook(worktree_layout):
    main, wt = worktree_layout
    result = _run(
        "update-changelog-before-pr.py",
        f"gh pr create --base develop --head {FEATURE} --title t --body b --dry-run",
        main,
        wt,
    )
    assert _decision(result) != "deny", result
    # The hook says why it allowed; silence would be an early exit.
    context = result["hookSpecificOutput"]["additionalContext"]
    assert "Changelog check: 1 entries found under [Unreleased]" in context, result


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


def _token_path(checkout: Path) -> Path:
    """The path require-preflight.py reads its token from (it hard-codes /tmp)."""
    loader = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('rp', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "print(m._get_token_path())\n"
    )
    env = _clean_env()
    env["CLAUDE_PROJECT_DIR"] = str(checkout)
    out = subprocess.run(
        [sys.executable, "-c", loader, str(HOOKS_DIR / "require-preflight.py")],
        env=env, capture_output=True, text=True, check=True, timeout=30,
    ).stdout.strip()
    return Path(out)


@contextlib.contextmanager
def _token_slot(main: Path):
    """Own the shared /tmp token path for one test, or fail without touching it.

    Hook and script both hard-code /tmp (no override). The path is keyed on
    this test's fixture repo, so a clash means a leftover or another
    checkout's file: refuse to overwrite it. Both the token and the
    `<token>.lock` require-preflight.py leaves are removed afterwards.
    """
    token = _token_path(main)
    lock = Path(str(token) + ".lock")
    for path in (token, lock):
        if path.exists():
            pytest.fail(f"{path} already exists; refusing to overwrite or remove it")
    try:
        yield token
    finally:
        token.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def _run_real_preflight(checkout: Path) -> None:
    """Run the real scripts/commit-preflight.sh in *checkout* with a staged file.

    `--skip-tests` stops after the token write: the secret scan, lint and the
    full suite are skipped, and the token comes from the script's own
    _preflight_write_token, so the test cannot drift from what it writes.
    The script takes its project dir from its own location, so it is copied in.
    """
    (checkout / "scripts").mkdir(exist_ok=True)
    shutil.copy(REPO_ROOT / "scripts" / "commit-preflight.sh", checkout / "scripts")
    (checkout / "staged.txt").write_text("staged\n")
    _git(checkout, "add", "staged.txt")
    proc = subprocess.run(
        ["bash", "scripts/commit-preflight.sh", "--skip-tests", "fixture"],
        cwd=checkout, env=_clean_env(), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    assert "PREFLIGHT PASSED" in proc.stdout, proc.stdout


def test_preflight_token_for_the_worktree_passes_a_worktree_commit(worktree_layout):
    # Payload cwd = worktree, process cwd = main. The token is bound to the
    # worktree, so the commit is allowed only if the hook checks the worktree.
    main, wt = worktree_layout
    with _token_slot(main) as token:
        _run_real_preflight(wt)
        written = json.loads(token.read_text())
        assert written["worktree"] == os.path.realpath(wt), written
        # The token goes to the hook exactly as the script wrote it.
        result = _run("require-preflight.py", "git commit -m msg", main, wt)
    assert _decision(result) != "deny", result
    assert "Preflight verified" in json.dumps(result), result


def test_preflight_token_for_main_blocks_a_worktree_commit(worktree_layout):
    # Mirror: a token bound to the main checkout must not cover a commit made
    # in the worktree.
    main, wt = worktree_layout
    with _token_slot(main):
        _run_real_preflight(main)
        result = _run("require-preflight.py", "git commit -m msg", main, wt)
    assert _decision(result) == "deny", result
    assert "another checkout" in _reason(result), result


def test_token_path_agrees_between_script_and_hook(worktree_layout):
    # This pins the token path to the two real formulas: the key computation lifted out of scripts/commit-preflight.sh,
    # and the hook's _get_token_path(). Run from the main checkout and from the
    # linked worktree, both must give the same path.
    main, wt = worktree_layout
    script = (REPO_ROOT / "scripts" / "commit-preflight.sh").read_text()
    start = script.index('PROJECT_KEY="$(git -C')
    end_marker = 'TOKEN_FILE="/tmp/.preflight-token-${PROJECT_HASH}"'
    end = script.index(end_marker) + len(end_marker)
    snippet = script[start:end]
    loader = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('rp', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "print(m._get_token_path())\n"
    )
    paths = set()
    for checkout in (main, wt):
        env = _clean_env()
        env["PROJECT_DIR"] = str(checkout)
        from_script = subprocess.run(
            ["bash", "-c", snippet + '\nprintf "%s" "$TOKEN_FILE"'],
            env=env, capture_output=True, text=True, check=True, timeout=30,
        ).stdout
        env = _clean_env()
        env["CLAUDE_PROJECT_DIR"] = str(checkout)
        from_hook = subprocess.run(
            [sys.executable, "-c", loader, str(HOOKS_DIR / "require-preflight.py")],
            env=env, capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
        assert from_script.startswith("/tmp/.preflight-token-"), from_script
        assert from_script == from_hook, (from_script, from_hook)
        paths.add(from_hook)
    assert len(paths) == 1, paths


# --- validate-branch-name: a relative `git -C` walks from the payload cwd --


def test_branch_name_relative_git_dir_resolves_from_payload_cwd(worktree_layout, tmp_path):
    # The same relative command both times; only where `ext` points changes.
    # Pointing outside the project, the guard skips. Pointing inside it, the
    # guard must still judge the name, so a hook that skips every redirected
    # checkout fails here.
    main, wt = worktree_layout
    outside = tmp_path / "outside"
    outside.mkdir()
    inside = main / "inside"
    inside.mkdir()
    cmd = "git -C ext checkout -b badname"
    # Control: the same bad name, with no -C, is denied (not a silent skip).
    denied = _run("validate-branch-name.py", "git checkout -b badname", main, wt)
    assert _decision(denied) == "deny", denied
    assert "badname" in _reason(denied), denied
    (wt / "ext").symlink_to(outside)
    skipped = _run("validate-branch-name.py", cmd, main, wt)
    assert _decision(skipped) != "deny", skipped
    (wt / "ext").unlink()
    (wt / "ext").symlink_to(inside)
    judged = _run("validate-branch-name.py", cmd, main, wt)
    assert _decision(judged) == "deny", judged
    assert "badname" in _reason(judged), judged

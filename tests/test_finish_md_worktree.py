"""Exercise the worktree pre-check embedded in the /finish template."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "commands" / "finish.md"
SCRIPT = ROOT / "scripts" / "git-flow-finish.sh"


@pytest.fixture(scope="module")
def precheck():
    template = TEMPLATE.read_bytes()
    begin = b"<!-- sync-main-checkout:begin -->\n"
    end = b"<!-- sync-main-checkout:end -->"
    assert template.count(begin) == template.count(end) == 1
    marked = template.split(begin)[1].split(end)[0]
    lines = marked.splitlines(keepends=True)
    assert lines[0] == b"```bash\n"
    assert lines[-1] == b"```\n"
    assert sum(line.startswith(b"```") for line in lines) == 2
    return b"".join(lines[1:-1])


@pytest.mark.parametrize(
    "name", [b"main_checkout_path", b"worktree_holding", b"warn_sync", b"sync_main_checkout"]
)
def test_precheck_function_matches_script(precheck, name):
    function = re.compile(rb"^" + name + rb"\(\) \{\n.*?^\}\n", re.MULTILINE | re.DOTALL)
    embedded = function.search(precheck)
    original = function.search(SCRIPT.read_bytes())
    assert embedded is not None
    assert original is not None
    assert embedded[0] == original[0]


def test_precheck_is_valid_bash(precheck):
    subprocess.run(["bash", "-n"], input=precheck, check=True, capture_output=True)


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def worktrees(tmp_path):
    root = tmp_path.resolve()  # Git reports physical paths, including /private/var on macOS.
    origin = root / "origin.git"
    repo = root / "repo"
    linked = root / "linked worktree"
    _git(root, "init", "--bare", str(origin))
    _git(root, "init", "-b", "develop", str(repo))
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "tracked.txt").write_text("original\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "Initial commit")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "origin", "develop")
    _git(repo, "worktree", "add", "-b", "feature/x", str(linked))
    (linked / "published.txt").write_text("new commit\n")
    _git(linked, "add", "published.txt")
    _git(linked, "commit", "-m", "Advance origin/develop")
    _git(linked, "push", "origin", "HEAD:develop")
    return origin, repo, linked


def _run_precheck(precheck, cwd, command):
    block = precheck.decode("utf-8")
    return subprocess.run(
        ["bash", "-c", block + command], cwd=cwd, capture_output=True, text=True
    )


@pytest.mark.parametrize("state", ["clean", "dirty", "other"])
def test_sync_develop_from_linked_worktree(precheck, worktrees, state):
    origin, repo, linked = worktrees
    if state == "dirty":
        (repo / "tracked.txt").write_text("local edit\n")
    elif state == "other":
        _git(repo, "switch", "-c", "other")

    before = _git(repo, "rev-parse", "HEAD")
    remote_develop = _git(origin, "rev-parse", "develop")
    assert before != remote_develop

    result = _run_precheck(precheck, linked, 'echo "$LINKED"; sync_main_checkout develop')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert result.stdout.splitlines()[0] == "true"

    branch = _git(repo, "branch", "--show-current")
    develop = _git(repo, "rev-parse", "develop")
    if state == "clean":
        assert branch == "develop"
        assert develop == remote_develop
        assert (repo / "published.txt").read_text() == "new commit\n"
    elif state == "dirty":
        assert branch == "develop"
        assert _git(repo, "rev-parse", "HEAD") == before
        assert (repo / "tracked.txt").read_text() == "local edit\n"
        assert (
            f"main checkout {repo} has uncommitted changes; left develop as is. "
            f"Run: git -C {repo} pull --ff-only origin develop"
        ) in output
    else:
        assert branch == "other"
        assert _git(repo, "rev-parse", "HEAD") == before
        assert develop == remote_develop
        assert f"git -C {repo} switch develop" in output


def test_main_checkout_is_not_linked(precheck, worktrees):
    _, repo, _ = worktrees
    result = _run_precheck(precheck, repo, 'echo "$LINKED"')
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "false"


@pytest.fixture(scope="module")
def feature_pr_block():
    template = TEMPLATE.read_text(encoding="utf-8")
    section = template.split("### 3. Feature Branch Finish\n")[1].split("#### Local fallback")[0]
    return section.split("```bash\n")[1].split("```")[0]


def test_feature_pr_commands(feature_pr_block):
    assert (
        "\ngh pr merge $PR_NUMBER --squash --delete-branch\n"
        "git checkout develop && git pull origin develop\n"
    ) in feature_pr_block
    assert "--delete-branch=false" in feature_pr_block
    assert "sync_main_checkout develop" in feature_pr_block


@pytest.mark.parametrize("merge_status, expected", [(7, 7), (0, 1)], ids=["failed", "queued"])
def test_linked_pr_stops_before_cleanup_if_unmerged(feature_pr_block, merge_status, expected):
    stubs = """
LINKED=true
CURRENT_BRANCH=feature/x
gh() {
  case "$1 $2" in
    'pr list') echo 42 ;;
    'pr merge') return "$MERGE_STATUS" ;;
    'pr view') echo OPEN ;;
    *) echo "unexpected gh $*" >&2 ;;
  esac
}
git() { echo "unexpected git $*" >&2; }
sync_main_checkout() { echo "unexpected sync $*" >&2; }
"""
    result = subprocess.run(
        ["bash", "-c", f"MERGE_STATUS=$1\n{stubs}{feature_pr_block}", "finish", str(merge_status)],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    assert result.returncode == expected, output
    assert "unexpected" not in output
    assert "worktree remove" not in output


def test_template_fences_are_balanced():
    assert TEMPLATE.read_bytes().count(b"```") % 2 == 0

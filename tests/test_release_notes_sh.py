"""Release-note source selection for git-flow-finish.sh."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "git-flow-finish.sh"
GH_STUB_DIR = REPO_ROOT / "tests" / "fixtures" / "bin"


def _extract(repo: Path) -> tuple[str, str]:
    output = repo / "release-body.md"
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"; cd "$2"; VERSION=v1.2.3; VERSION_NUMBER=1.2.3; '
                'extract_release_notes "$3"; printf "%s\\n" "$NOTES_SOURCE"'
            ),
            "bash",
            str(SCRIPT),
            str(repo),
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.rstrip("\n"), output.read_text()


def test_v_prefixed_authored_notes_take_priority(tmp_path):
    notes = tmp_path / "docs" / "release-notes"
    notes.mkdir(parents=True)
    (notes / "v1.2.3.md").write_text("Product-facing notes.\n")
    (notes / "1.2.3.md").write_text("Bare-version notes.\n")
    (tmp_path / "CHANGELOG.md").write_text("## [1.2.3]\nChangelog notes.\n")

    source, body = _extract(tmp_path)

    assert source == "docs/release-notes/v1.2.3.md"
    assert body == "Product-facing notes.\n"


def test_bare_version_authored_notes_are_supported(tmp_path):
    notes = tmp_path / "docs" / "release-notes"
    notes.mkdir(parents=True)
    (notes / "1.2.3.md").write_text("Bare-version notes.\n")

    source, body = _extract(tmp_path)

    assert source == "docs/release-notes/1.2.3.md"
    assert body == "Bare-version notes.\n"


def test_empty_authored_notes_fall_back_to_changelog(tmp_path):
    notes = tmp_path / "docs" / "release-notes"
    notes.mkdir(parents=True)
    (notes / "v1.2.3.md").touch()
    (tmp_path / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n## [1.2.3]\n\n### Added\n- Useful change.\n\n"
        "## [1.2.2]\n- Older change.\n"
    )

    source, body = _extract(tmp_path)

    assert source == "CHANGELOG.md"
    assert body == "### Added\n- Useful change.\n\n"


def test_missing_sources_leave_an_empty_body_for_the_existing_fallback(tmp_path):
    source, body = _extract(tmp_path)

    assert source == ""
    assert body == ""


def test_create_release_reports_the_authored_source(tmp_path):
    notes = tmp_path / "docs" / "release-notes"
    notes.mkdir(parents=True)
    (notes / "v1.2.3.md").write_text("Product-facing notes.\n")
    env = os.environ.copy()
    env["PATH"] = f"{GH_STUB_DIR}:{env['PATH']}"
    env["MOCK_GH_STDOUT_RELEASE_VIEW"] = "200"

    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"; cd "$2"; VERSION=v1.2.3; VERSION_NUMBER=1.2.3; '
                "REPO=owner/repo; BRANCH_TYPE_CAPITALIZED=Release; "
                'create_github_release "$VERSION"'
            ),
            "bash",
            str(SCRIPT),
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert "Release body: 1 lines from docs/release-notes/v1.2.3.md" in result.stdout

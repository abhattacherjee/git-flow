---
allowed-tools: Bash(git:*), Read, Edit, Write
argument-hint: <version|major|minor|patch>
description: Create a new Git Flow release branch from develop with version bumping and changelog generation
---

# Git Flow Release Branch

Create new release branch for: **$ARGUMENTS**

## Current Repository State

- Current branch: !`git branch --show-current`
- Git status: !`git status --porcelain`
- Latest tag: !`git describe --tags --abbrev=0 2>/dev/null || echo "No tags found"`
- Commits since last tag: !`git log $(git describe --tags --abbrev=0 2>/dev/null)..HEAD --oneline 2>/dev/null | wc -l | tr -d ' '`
- Package.json version: !`cat package.json 2>/dev/null | grep '"version"' | head -1 || echo "No package.json found"`
- Recent commits: !`git log --oneline -10`

## Task

Create a Git Flow release branch following these steps.

### 1. Resolve and Validate the Version

**Resolve `$ARGUMENTS` to a concrete, normalized version `vMAJOR.MINOR.PATCH` BEFORE running any branch/tag/push command.** Never interpolate the raw argument into a ref — an unvalidated arg (a multi-word phrase, an incomplete token) would create a space-containing or mangled branch. Store the result in `$VERSION` and use only `$VERSION` from step 2 onward.

Apply these rules in order:

1. **Empty** → emit the "No Version Provided" error (step 5) and abort.
2. **Semantic keyword** — `$ARGUMENTS` is `major`, `minor`, or `patch`, or a natural-language equivalent ("next major version", "next minor", "next patch version", …):
   - Determine the current highest released version with `git tag --sort=-v:refname | head -1` (strip a leading `v`); if there are no tags, treat it as `0.0.0`. Prefer this over the `Latest tag` shown in **Current Repository State** above — that value comes from `git describe`, which reflects topological ancestry, not the highest semver, so it can select a lower base and resolve a version that collides with an existing higher tag.
   - Increment the matching component: major → `(X+1).0.0`, minor → `X.(Y+1).0`, patch → `X.Y.(Z+1)`.
   - Cross-check against the commit history if useful (see the increment logic below), but the keyword the user gave governs.
   - Set `VERSION="v<computed>"` and **echo the resolution for confirmation**, e.g. `Resolved "next minor version" → v2.3.0`.
3. **Explicit semver token** — `$ARGUMENTS` matches `^v?[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$` (optional leading `v`, MAJOR.MINOR.PATCH, optional prerelease):
   - Normalize to a single leading `v`: `VERSION="v${ARGUMENTS#v}"`.
   - Confirm it is newer than the latest tag; if it is **not**, warn and ask the user to confirm before continuing — an older version would regress the project's version metadata.
   - Note: `scripts/bump-version.sh` accepts only a bare `X.Y.Z` (no prerelease). A prerelease version (e.g. `v2.0.0-beta.1`) is valid here but is bumped only on Node repos via `npm version`; on a non-Node repo the bump-version.sh delegation in step 2 will reject it and abort with its error surfaced.
4. **Anything else** (incomplete like `1.2`, non-semver text) → emit the "Invalid Version Format" error (step 5) and abort **before any mutation**.

**Version Increment Logic** (commit analysis since last tag, for the semantic-keyword cross-check):
- **MAJOR**: breaking changes (`BREAKING CHANGE:` in commits)
- **MINOR**: new features (`feat:` commits)
- **PATCH**: bug fixes only (`fix:` commits)

See the `git-flow` skill for the full semver selection guide.

### 2. Create Release Branch and Bump Version

**Precondition:** step 1 must have produced a valid, normalized `$VERSION` (a single leading `v`). If it did not, stop here — run none of the commands below.

Substitute the version you resolved in step 1 for `<vX.Y.Z>` on the first line so the block never runs with `$VERSION` unset, then run it as a **single** shell invocation so the abort paths take effect. **If any bump command exits non-zero, STOP** — the block returns to `develop` and deletes the partial `release/$VERSION` branch; do not edit the CHANGELOG, commit, or push. Surface the bumper's error and re-run `/release` after fixing the cause.

```bash
VERSION="<vX.Y.Z>"   # the validated version from step 1, e.g. v2.3.0

# Re-assert the value is a normalized vX.Y.Z before any mutation — guards
# against a transcription slip when copying the version out of step 1.
if [[ ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
  echo "ERROR: \$VERSION ('$VERSION') is not a normalized vX.Y.Z — re-run step 1." >&2
  exit 1
fi

# Require a clean working tree (enforces the "Uncommitted Changes" case in
# step 5). This makes the abort cleanup below safe: the forced checkout can
# then only discard the bump THIS block creates, never pre-existing user work.
if [[ -n "$(git status --porcelain)" ]]; then
  echo "ERROR: working tree is not clean — commit or stash your changes before running /release." >&2
  exit 1
fi

# Switch to develop and update (guard both — a failed switch or a non-ff
# pull must abort before we branch off the wrong base).
git checkout develop || { echo "ERROR: could not switch to develop." >&2; exit 1; }
git pull --ff-only origin develop \
  || { echo "ERROR: develop pull was not a clean fast-forward — resolve manually, then re-run /release." >&2; exit 1; }

# Create release branch from the VALIDATED version
git checkout -b "release/$VERSION"

# On any bump failure, undo this block's work (the partial bump + the new
# branch) and return to develop, so the "re-run /release" remediation works
# without manual cleanup. Safe because the clean-tree guard above guarantees
# the only tracked changes present are the ones this block created.
abort_release() {
  echo "$1" >&2
  git checkout -f develop
  git branch -D "release/$VERSION" \
    || echo "WARNING: could not delete release/$VERSION — delete it manually." >&2
  exit 1
}

# Bump version metadata. Prefer npm for Node projects; otherwise delegate to
# scripts/bump-version.sh (plugin.json / pyproject.toml / Cargo.toml /
# version.txt). This is the same bumper /finish step 4b uses for the
# post-release develop bump — except /finish passes the `patch` keyword,
# while here we pass the explicit release version. Both accept a bare X.Y.Z.
# A non-zero exit from either bumper ABORTS — never commit a partial or
# skipped bump under a "bump version" message.
if [[ -f package.json ]]; then
  npm version "${VERSION#v}" --no-git-tag-version \
    || abort_release "ERROR: 'npm version' failed. Ensure npm is installed and package.json is valid, then re-run /release $VERSION."
  git add package.json
  [[ -f package-lock.json ]] && git add package-lock.json
elif [[ -x scripts/bump-version.sh ]]; then
  ./scripts/bump-version.sh "${VERSION#v}" \
    || abort_release "ERROR: scripts/bump-version.sh failed. Fix the error above, then re-run /release $VERSION."
  git add -A
else
  abort_release "ERROR: no package.json and no scripts/bump-version.sh — cannot bump version metadata. Add scripts/bump-version.sh (see /harden-repo) or bump your version file manually, then re-run /release $VERSION."
fi
```

Now update the CHANGELOG per step 3 below — move the `## [Unreleased]` entries into a new `## [${VERSION#v}]` section. **Only then** stage it, commit the bump, and push as a single invocation:

```bash
git add CHANGELOG.md
git commit -m "chore(release): bump version to ${VERSION#v}

Co-Authored-By: Claude <noreply@anthropic.com>"

git push -u origin "release/$VERSION" \
  || { echo "ERROR: push failed. The branch and bump commit exist locally — fix the remote/auth issue, then run: git push -u origin release/$VERSION" >&2; exit 1; }
```

### 3. CHANGELOG Generation

CHANGELOG headers use the bare version number (no leading `v`), i.e. `${VERSION#v}`.

**If CHANGELOG.md has entries under `## [Unreleased]`:**

Move those entries into a new versioned section. Replace:
```markdown
## [Unreleased]

### Added
- existing entry from feature branches
...
```

With:
```markdown
## [Unreleased]

## [${VERSION#v}] - [Current Date]

### Added
- existing entry from feature branches
...
```

The `[Unreleased]` header stays but is now empty. The existing entries move under the new versioned header. Review the entries and supplement with any additional changes from commits since the last tag that aren't already listed.

**If CHANGELOG.md has no entries under `## [Unreleased]`:**

Generate changelog from commits since last tag, grouped by type:

```markdown
## [Unreleased]

## [${VERSION#v}] - [Current Date]

### Added
- [feat: commits]

### Fixed
- [fix: commits]

### Changed
- [refactor:/perf:/chore: commits]

### Documentation
- [docs: commits]
```

### 4. Success Response

```
Release Branch Ready: $VERSION

Branch: release/$VERSION
Base: develop
Target: main (after review)

Next Steps:
1. Review CHANGELOG.md for accuracy
2. Run final tests
3. Create PR to main: gh pr create --base main
4. Get team approvals
5. Run /finish to complete release (or /finalize-release if available)

Release Tips:
- No new features on release branch — bug fixes only
- Keep release branch short-lived
- Tag will be created when merged to main
```

### 5. Error Handling

**No Version Provided:**
```
Version is required

Usage: /release <version|major|minor|patch>

Examples:
  /release v1.2.0
  /release v2.0.0-beta.1
  /release minor

Current version: [from git describe]
Suggested version: [based on commit analysis]
```

**Invalid Version Format:**
```
Invalid version format: "$ARGUMENTS"

Expected one of:
  - vMAJOR.MINOR.PATCH  (e.g. v1.2.0, optional leading 'v', optional prerelease)
  - a semantic keyword: major | minor | patch
```

**Uncommitted Changes:**
```
Uncommitted changes detected. Commit or stash first.
```

## Related Commands

- `/finish` — Complete release (merge to main and develop, create tag)
- `/finalize-release` — Ship release with parallel artifact generation (project-specific)
- `/flow-status` — Check current Git Flow status
- `/feature <name>` — Create feature branch
- `/hotfix` — Create hotfix branch from main
- `git-flow` skill — Branching model reference, conventions, and diagnostic script

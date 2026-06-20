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

1. **Empty** → emit the "No Version Provided" error (§5) and abort.
2. **Semantic keyword** — `$ARGUMENTS` is `major`, `minor`, or `patch`, or a natural-language equivalent ("next major version", "next minor", "next patch version", …):
   - Read the current version from the latest tag: `git describe --tags --abbrev=0` (strip a leading `v`). With no tags, treat the current version as `0.0.0`.
   - Increment the matching component: major → `(X+1).0.0`, minor → `X.(Y+1).0`, patch → `X.Y.(Z+1)`.
   - Cross-check against the commit history if useful (see the increment logic below), but the keyword the user gave governs.
   - Set `VERSION="v<computed>"` and **echo the resolution for confirmation**, e.g. `Resolved "next minor version" → v2.3.0`.
3. **Explicit semver token** — `$ARGUMENTS` matches `^v?[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$` (optional leading `v`, MAJOR.MINOR.PATCH, optional prerelease):
   - Normalize to a single leading `v`: `VERSION="v${ARGUMENTS#v}"`.
   - Confirm it is newer than the latest tag; if it is not, warn before continuing.
4. **Anything else** (incomplete like `1.2`, non-semver text) → emit the "Invalid Version Format" error (§5) and abort **before any mutation**.

**Version Increment Logic** (commit analysis since last tag, for the semantic-keyword cross-check):
- **MAJOR**: breaking changes (`BREAKING CHANGE:` in commits)
- **MINOR**: new features (`feat:` commits)
- **PATCH**: bug fixes only (`fix:` commits)

See the `git-flow` skill for the full semver selection guide.

### 2. Create Release Branch

Use the validated `$VERSION` (always carrying a single leading `v`) — never `$ARGUMENTS`.

```bash
# Switch to develop and update
git checkout develop
git pull origin develop

# Create release branch from the VALIDATED version
git checkout -b "release/$VERSION"

# Bump version metadata. Prefer npm for Node projects; otherwise delegate
# to scripts/bump-version.sh (plugin.json / pyproject.toml / Cargo.toml /
# version.txt), mirroring /finish step 4b. Both bumpers take the X.Y.Z form
# (leading 'v' stripped).
if [[ -f package.json ]]; then
  npm version "${VERSION#v}" --no-git-tag-version
  git add package.json
elif [[ -x scripts/bump-version.sh ]]; then
  ./scripts/bump-version.sh "${VERSION#v}"
  git add -A
else
  echo "No package.json or scripts/bump-version.sh — skipping automated version bump"
fi

# Stage the changelog (edited per §3) and commit the bump
git add CHANGELOG.md
git commit -m "chore(release): bump version to ${VERSION#v}

Co-Authored-By: Claude <noreply@anthropic.com>"

# Push to remote with tracking
git push -u origin "release/$VERSION"
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

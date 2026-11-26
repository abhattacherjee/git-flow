---
allowed-tools: Bash(git:*), Bash(npm test:*), Read, Edit
argument-hint: [--no-delete] [--no-tag]
description: Complete and merge current Git Flow branch (feature/release/hotfix) with proper cleanup and tagging
---

# Git Flow Finish Branch

Complete current Git Flow branch: **$ARGUMENTS**

## Current Repository State

- Current branch: !`git branch --show-current`
- Repo root: !`git rev-parse --show-toplevel 2>/dev/null || pwd`
- Remote: !`git config --get remote.origin.url 2>/dev/null | sed -E 's#://[^/]*@#://#' | grep . || echo "no remote"`
- Branch type: !`git branch --show-current | grep -oE '^(feature|release|hotfix)' || echo "Not a Git Flow branch"`
- Git status: !`git status --porcelain`
- Unpushed commits: !`git log @{u}.. --oneline 2>/dev/null | wc -l | tr -d ' '`
- Latest tag: !`git describe --tags --abbrev=0 2>/dev/null || echo "No tags"`

## Task

Complete the current Git Flow branch by merging it to appropriate target branch(es).

### 1. Branch Type Detection

```bash
CURRENT_BRANCH=$(git branch --show-current)

if [[ $CURRENT_BRANCH == feature/* ]]; then
  BRANCH_TYPE="feature"
  MERGE_TO="develop"
  CREATE_TAG="no"
elif [[ $CURRENT_BRANCH == release/* ]]; then
  BRANCH_TYPE="release"
  MERGE_TO="main develop"
  CREATE_TAG="yes"
  TAG_NAME="${CURRENT_BRANCH#release/}"
elif [[ $CURRENT_BRANCH == hotfix/* ]]; then
  BRANCH_TYPE="hotfix"
  MERGE_TO="main develop"
  CREATE_TAG="yes"
  TAG_NAME="${CURRENT_BRANCH#hotfix/}"
else
  echo "Not on a Git Flow branch (feature/release/hotfix)"
  exit 1
fi
```

### 2. Pre-Merge Validation

- All changes committed (clean working directory)
- All commits pushed to remote
- No merge conflicts with target branch(es)

### 2b. Worktree pre-check

Linked mode means the current checkout is a Git worktree other than the main checkout (#32).
There, `git checkout develop` or `git checkout main` fails with "already used by worktree"
when the main checkout holds that branch, so the later steps detach at `origin/<branch>`
instead and then bring the main checkout up to date with `sync_main_checkout`.
Outside a linked worktree, every later command is unchanged.

Each Bash call starts a fresh shell, so these functions and variables do not carry over.
Put this whole block at the start of every later Bash call that uses `$LINKED`,
`$MAIN_CHECKOUT`, `$HERE` or `sync_main_checkout`.

<!-- sync-main-checkout:begin -->
```bash
# Keep in sync with scripts/git-flow-finish.sh; tests/test_finish_md_worktree.py checks it.
log() { echo "  $1"; }
SYNC_WARNINGS=()
MAIN_SYNC_WARNING=""
DEVELOP_SYNC_WARNING=""

main_checkout_path() {
  local entries first
  entries=$(git worktree list --porcelain) || return 1
  first=${entries%%$'\n'*}
  [[ "$first" == "worktree "* ]] || return 1
  printf '%s\n' "${first#worktree }"
}

worktree_holding() {
  local b="$1" entries line wt_path=""
  if ! entries=$(git worktree list --porcelain); then
    return 1
  fi
  while IFS= read -r line; do
    case "$line" in
      "worktree "*) wt_path=${line#worktree } ;;
      "branch refs/heads/$b") printf '%s\n' "$wt_path"; return 0 ;;
    esac
  done <<< "$entries"
  return 0
}

warn_sync() {
  local warning="$1"
  case "${2:-}" in
    main) MAIN_SYNC_WARNING="$warning" ;;
    develop) DEVELOP_SYNC_WARNING="$warning" ;;
  esac
  SYNC_WARNINGS+=("$warning")
  log "⚠️ $warning" || :
}

sync_main_checkout() {
  local b="$1" holder current changes pull q
  if ! holder=$(worktree_holding "$b"); then
    warn_sync "Could not determine which worktree holds $b. Run: git worktree list --porcelain" "$b"
  elif [[ -z "$holder" ]]; then
    if ! git fetch origin "$b:$b"; then
      warn_sync "Could not fast-forward local $b. Run: git fetch origin $b:$b" "$b"
    fi
    if [[ "$b" == develop ]]; then
      if ! current=$(git -C "$MAIN_CHECKOUT" branch --show-current); then
        warn_sync "Could not read the current branch in main checkout $MAIN_CHECKOUT. Run: git worktree list --porcelain"
      elif [[ -n "$current" && "$current" != develop ]]; then
        q=$(printf '%q' "$MAIN_CHECKOUT")
        warn_sync "main checkout $MAIN_CHECKOUT is on $current, not develop; to use the merge run: git -C $q switch develop"
      fi
    fi
  else
    q=$(printf '%q' "$holder")
    pull="git -C $q pull --ff-only origin $b"
    if [[ ! -d "$holder" ]]; then
      warn_sync "Worktree $holder holding $b is missing. If it was removed, run git worktree prune. If restored, run: $pull" "$b"
    elif [[ "$holder" != "$MAIN_CHECKOUT" ]]; then
      warn_sync "$b is checked out in another worktree at $holder. Run: $pull" "$b"
    elif ! changes=$(git -C "$holder" status --porcelain --untracked-files=no); then
      warn_sync "Could not check for changes in main checkout $holder; left $b as is. Run: $pull" "$b"
    elif [[ -n "$changes" ]]; then
      warn_sync "main checkout $holder has uncommitted changes; left $b as is. Run: $pull" "$b"
    elif ! git -C "$holder" pull --ff-only origin "$b"; then
      warn_sync "Could not fast-forward $b in main checkout $holder. Run: $pull" "$b"
    fi
  fi
  return 0
}

MAIN_CHECKOUT=$(main_checkout_path)
HERE=$(cd "$(git rev-parse --show-toplevel)" && pwd -P)
if [[ -z "$MAIN_CHECKOUT" || -z "$HERE" ]]; then
  echo "Could not determine the checkout paths" >&2
  exit 1
fi
LINKED=false; [ "$HERE" != "$(cd "$MAIN_CHECKOUT" && pwd -P)" ] && LINKED=true
: # A main-checkout result also completes the pre-check successfully.
```
<!-- sync-main-checkout:end -->

### 3. Feature Branch Finish

#### Via PR (preferred — always squash merge)

```bash
# Find the open PR for this branch
PR_NUMBER=$(gh pr list --head "$CURRENT_BRANCH" --json number --jq '.[0].number')

# Squash merge — combines all commits into a single commit on develop
if [ "$LINKED" = true ]; then
  if [[ ! "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
    echo "Could not determine the PR number for $CURRENT_BRANCH" >&2
    exit 1
  fi
  gh pr merge $PR_NUMBER --squash --delete-branch=false || exit $?
  PR_STATE=$(gh pr view "$PR_NUMBER" --json state --jq .state) || exit $?
  if [ "$PR_STATE" != MERGED ]; then
    printf 'PR #%s is %s. Resume cleanup and sync after it merges.\n' "$PR_NUMBER" "$PR_STATE" >&2
    exit 1
  fi
  gh api -X DELETE "repos/{owner}/{repo}/git/refs/heads/$CURRENT_BRANCH" ||
    printf 'Could not delete remote branch %s\n' "$CURRENT_BRANCH" >&2
  sync_main_checkout develop
  git fetch --prune origin
  printf 'git -C %q worktree remove %q\n' "$MAIN_CHECKOUT" "$HERE"
  printf 'git -C %q branch -D %q\n' "$MAIN_CHECKOUT" "$CURRENT_BRANCH"
else
  gh pr merge $PR_NUMBER --squash --delete-branch
  git checkout develop && git pull origin develop
  git fetch --prune origin   # drop stale remote-tracking refs for deleted branches
fi
```

In linked mode, the block prints two cleanup commands. Give them to the user to run
from outside this worktree because the feature branch is still checked out here.

> **Safety check:** Before invoking `gh pr merge`, `/finish` and the
> plugin-bundled `check-pr-base` hook both verify the PR's `baseRefName`
> matches the expected base for the branch type:
>
> | Branch | Required base |
> | --- | --- |
> | `feature/*` | `develop` |
> | `hotfix/*` | `main` |
> | `release/*` | `main` |
>
> A wrong-base PR is blocked with a `gh pr edit <N> --base <expected>`
> remediation hint. The same hook also blocks `gh pr create` invocations
> on Git Flow branches that omit `--base` or pass the wrong base.

#### Local fallback (when no PR exists)

For `LINKED=false`:

```bash
git push
git checkout develop
git pull origin develop
git merge --no-ff feature/$NAME -m "Merge feature/$NAME into develop

$(git log develop..feature/$NAME --oneline)

Co-Authored-By: Claude <noreply@anthropic.com>"
git push origin develop
git fetch --prune origin   # drop stale remote-tracking refs for deleted branches

# Use -D (force) because squash merge creates different SHA
git branch -D feature/$NAME 2>/dev/null || true
git push origin --delete feature/$NAME 2>/dev/null || true
```

For `LINKED=true`:

```bash
git push || exit $?
git fetch origin develop || exit $?
git checkout --detach origin/develop || exit $?
git merge --no-ff "$CURRENT_BRANCH" -m "Merge $CURRENT_BRANCH into develop

$(git log HEAD.."$CURRENT_BRANCH" --oneline)

Co-Authored-By: Claude <noreply@anthropic.com>" || exit $?
git push origin HEAD:develop || exit $?

# Cleanup (unless --no-delete)
git branch -D "$CURRENT_BRANCH" || true
git push origin --delete "$CURRENT_BRANCH" || true

sync_main_checkout develop
git fetch --prune origin
printf 'git -C %q worktree remove %q\n' "$MAIN_CHECKOUT" "$HERE"
```

### 4. Release Branch Finish

In linked mode, prefer `scripts/git-flow-finish.sh` for release and hotfix when the repo has it and neither `--no-tag` nor `--no-delete` is set. The script handles linked worktrees, the GitHub release, and the version bump.

For a manual finish with `LINKED=false`:

```bash
VERSION="${CURRENT_BRANCH#release/}"
git push

# Merge to main
git checkout main
git pull origin main
git merge --no-ff release/$VERSION -m "Merge release/$VERSION into main

Co-Authored-By: Claude <noreply@anthropic.com>"

# Tag (unless --no-tag)
git tag -a $VERSION -m "Release $VERSION"
git push origin main --tags

# Merge back to develop
git checkout develop
git pull origin develop
git merge --no-ff release/$VERSION -m "Merge release/$VERSION back into develop

Co-Authored-By: Claude <noreply@anthropic.com>"
git push origin develop
git fetch --prune origin   # drop stale remote-tracking refs for deleted branches

# Cleanup (unless --no-delete)
git branch -d release/$VERSION
git push origin --delete release/$VERSION
```

For a manual finish with `LINKED=true`:

```bash
VERSION="${CURRENT_BRANCH#*/}"
git push || exit $?

# Merge to main
git fetch origin main || exit $?
git checkout --detach origin/main || exit $?
git merge --no-ff "$CURRENT_BRANCH" -m "Merge $CURRENT_BRANCH into main

Co-Authored-By: Claude <noreply@anthropic.com>" || exit $?

# Tag (unless --no-tag)
git tag -a "$VERSION" -m "Release $VERSION" || exit $?
git push origin HEAD:main --tags || exit $?

# Merge back to develop
git fetch origin develop || exit $?
git checkout --detach origin/develop || exit $?
git merge --no-ff "$CURRENT_BRANCH" -m "Merge $CURRENT_BRANCH back into develop

Co-Authored-By: Claude <noreply@anthropic.com>" || exit $?
git push origin HEAD:develop || exit $?
git fetch --prune origin

# Cleanup (unless --no-delete)
git branch -d "$CURRENT_BRANCH" ||
  printf 'Could not delete local branch %s\n' "$CURRENT_BRANCH" >&2
git push origin --delete "$CURRENT_BRANCH" ||
  printf 'Could not delete remote branch %s\n' "$CURRENT_BRANCH" >&2
```

In linked mode, continue on detached `HEAD` through step 4b, which syncs both refs after the final push.

### 4a. Create GitHub Release (release and hotfix only)

After tagging and pushing, **always create a GitHub release** with user-friendly notes.

1. **Check previous release format** to match conventions:
   ```bash
   gh release view $(git tag --sort=-v:refname | sed -n '2p') --json body --jq '.body' | head -20
   ```

2. **Generate user-friendly release notes** from the CHANGELOG. Read the `[VERSION]` section
   from CHANGELOG.md, then rewrite each entry to be product/user-facing:
   - Remove internal implementation details (file paths, function names, config keys)
   - Describe what changed from the user's perspective, not what code was modified
   - Keep the same section headers (### Fixed, ### Added, ### Changed, etc.)

3. **Create the release** matching the format of previous releases:
   ```bash
   gh release create $VERSION --title "$VERSION" --notes "<user-friendly notes>"
   ```

### 4b. Post-release develop version bump (release and hotfix only)

After merging back to develop, bump the version to the next patch and ensure `[Unreleased]` is ready for new entries:

1. **Bump version** — run `./scripts/bump-version.sh patch` if the script exists, otherwise manually increment the patch version in the project's version file (package.json, plugin.json, etc.)

2. **Ensure `[Unreleased]` header exists** — check if CHANGELOG.md has an empty `## [Unreleased]` section at the top (above the just-released version). If not, add one:
   ```markdown
   ## [Unreleased]

   ## [x.y.z] - date
   ...
   ```

3. **Commit** on develop or on the detached `HEAD` from the linked back-merge:
   ```bash
   git add -A
   git commit -m "chore(develop): bump version for next development cycle

   Previous release: $VERSION

   Co-Authored-By: Claude <noreply@anthropic.com>"
   ```

4. **Push** after the commit succeeds:
   ```bash
   if [ "$LINKED" = true ]; then
     git push origin HEAD:develop || exit $?
     sync_main_checkout main
     sync_main_checkout develop
   else
     git push origin develop
   fi
   ```

### 5. Hotfix Branch Finish

Same as release finish but uses hotfix branch name. The tag is the hotfix version (already set on the branch). **Also creates a GitHub release** (step 4a) and **bumps develop version** (step 4b).

### 6. Error Handling

**Not on Git Flow Branch:** Guide user to correct branch.
**Uncommitted Changes:** Ask to commit/stash.
**Unpushed Commits:** Offer to push.
**Merge Conflicts:** Show conflicting files and resolution steps.

### 7. Arguments

- `--no-delete` — Keep branch after merging
- `--no-tag` — Skip tag creation (release/hotfix only)

## Related Commands

- `/feature <name>` — Start new feature branch
- `/release <version>` — Start new release branch
- `/hotfix` — Start new hotfix branch from main
- `/flow-status` — Check Git Flow status
- `git-flow` skill — Branching model reference, merge targets, and diagnostic script

# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Changed

- **Refreshed the harden-repo hooks and scripts to the released v1.2.0 templates.** The installed copies predated upstream #43, #73, #83 and #84, so this repo was running guards with two live bypasses: a `cd` to a path that does not exist turned all four guards off while the command still ran here, and the secret scan could not see `scripts/`. Also gains the command parser, worktree-stable preflight identity, and the advisory drift check. Two artifacts were missing and are now installed: `scripts/check-assertion-strength.sh` and `.github/workflows/ci.yml` — the CI file is the first thing in `.github/` here, ships with its `__HARDEN_LINT_JOB__` / `__HARDEN_TEST_JOB__` blocks still on the installer placeholder (comments, so the YAML is valid), and adds a secret-scan and a CHANGELOG gate to pull requests. Left alone: `scripts/bump-version.sh` and `scripts/git-flow-finish.sh` are UNRECOGNIZED (locally modified — this repo ships its own Git Flow tooling), and `scripts/commit-preflight.sh` is still the v1.0.0 generation with unfilled lint/test placeholders, which the doctor classifies UNCONFIGURED and therefore does not repair.

### Security

- **`scripts/bump-version.sh` no longer executes injected commands from the version source (harden-repo#55):** the script fed the parsed version components straight into bash arithmetic with only an is-it-empty check in front. `$(( ))` recursively expands the *contents* of the variables it evaluates, so an array-subscript payload in the version source — e.g. `x[$(rm -rf ~)].0.0` — ran as a command substitution during a bump, and the mangled result was then written back to the version file at exit 0. All three bump types were exploitable, each with the payload in the component that bump evaluates.

  The fix has two layers, because the first one alone was not enough:

  - `CURRENT_VERSION` is semver-validated before it reaches any arithmetic, with each core component bounded to 9 digits so a long component cannot overflow into a wrapped value.
  - The `-prerelease` / `+build` suffix is **stripped before the components are split**, so only digit runs ever reach `$(( ))`. This is the property that actually makes the arithmetic safe. Validating the string alone is not sufficient: arithmetic does not need a literal `$` or backtick to execute something, because a bare identifier inside `$(( ))` is looked up and its *value* re-evaluated as an arithmetic expression. A version of `1.2.3-zz` with `zz='x[$(cmd)]'` in the environment therefore still ran `cmd` and exited 0 reporting success. The same suffix path also silently **downgraded** `1.2.3-4` to `1.2.0` (`10#3-4 + 1` = 0), which is semver-shaped and so slipped past the write guard.

  Alongside those: the three arithmetic expansions force base 10 (`10#`), so a zero-padded component like `08` is no longer read as an invalid octal literal; carried-through components are normalized through `10#` as well, so `01.02.03` no longer yields `01.02.4`; and a symmetric guard refuses to write a `NEW_VERSION` that is not semver-shaped.

## [2.3.0] - 2026-06-20

### Added

- `/finish` now prints the resolved repository root and remote URL before any merge, tag, or push, so a wrong-repo invocation is visible at a glance (#10)
- `/finish` now runs `git fetch --prune origin` after each develop sync (feature squash-merge and release/hotfix back-merge), clearing stale remote-tracking refs for branches deleted during finish (#12)

### Fixed

- `/release` now resolves semantic version keywords (`major`/`minor`/`patch`, and natural-language phrases like "next minor version") to a concrete `vX.Y.Z` and validates the version argument before creating any branch, instead of interpolating the raw argument into the branch name (#14)

### Changed

- `/release` version-bump step now delegates to an executable `scripts/bump-version.sh` for non-Node projects (plugin.json / pyproject.toml / Cargo.toml / version.txt) when there is no `package.json`, mirroring `/finish` step 4b; if neither `package.json` nor an executable `scripts/bump-version.sh` is present it aborts with guidance — previously the bump was silently skipped on non-Node repos (#15)

## [2.2.1] - 2026-06-20

### Added

- **Merge & PR-base discipline reference (#16)** — `skills/git-flow/references/merge-discipline.md`: the branch→base matrix, explicit `--base` + verify-before-merge, the create-vs-merge-to-main distinction, and the `main`-poisoning failure mode (why a wrong-base merge can't be naively reverted). Linked from `SKILL.md`.

### Fixed

- check-pr-base hook: allow `release/*` and `hotfix/*` branches to back-merge to `develop` (not just `main`), unblocking the Git Flow release/hotfix back-merge PR (#21)
- check-pr-base hook: detect the PR create/merge subcommand only as real consecutive command tokens (quote/heredoc-aware), eliminating substring false-positives that wrongly blocked commit messages and `--body` arguments mentioning the workflow (#18)

## [2.2.0] - 2026-05-03

### Added

- PR base-branch enforcement (#1):
  - New `hooks/check-pr-base.py` PreToolUse hook blocks wrong-base
    `gh pr create` and `gh pr merge` invocations on Git Flow branches
    (`feature/*`→develop, `hotfix/*`→main, `release/*`→main).
  - New `verify_pr_base()` function in `scripts/git-flow-finish.sh`
    provides defense-in-depth before any in-script `gh pr merge` call.
  - Hook respects a leading `cd <path>` in the bash command so cross-repo
    invocations are validated against the cd'd repo, not the hook's CWD.
  - Diagnostic stderr breadcrumbs on gh-failure fail-open paths so
    wrong-base merges that slip through can be debugged post-hoc.
  - Fails open for non-Git-Flow branches, single-trunk repos (no
    `develop`), detached HEAD, and any `gh`/`git` error.

## [2.1.3] - 2026-05-02

### Removed

- **SKILL.md `metadata.version` frontmatter field** — informational only, not consumed by the plugin runtime, and prone to silently drifting from `plugin.json` (the actual source of truth). Removed rather than adding lockstep machinery to `bump-version.sh`.

## [2.1.2] - 2026-05-02

### Fixed

- **SKILL.md** — updated stale post-migration references. The skill description was inherited from the user-level install era and still pointed at `~/.claude/skills/git-flow/` (deleted) and `~/.claude/commands/` (deleted). Replaced with the slash-command form (`/git-flow:flow-status`) plus `${CLAUDE_PLUGIN_ROOT}` paths for direct script access. Two-Tier Architecture section updated to document the plugin-provided command resolution. Directory Layout section now describes the plugin's internal repo layout + the read-only cache install path.
- **SKILL.md frontmatter** — bumped `version: 2.0.0` → `2.1.2` to match `plugin.json`. (Note: not auto-tracked by `bump-version.sh`; future drift possible.)

## [2.1.1] - 2026-05-02

### Documentation

- **README.md** — rewritten for the standalone-marketplace install path. Now includes the branching diagram, merge-target matrix, walkthroughs for the 4 main flows (feature, planned release, hotfix, status check), project-level override rationale, configuration env vars, and a "gotchas worth knowing" pointer-list. The previous README was inherited from the `claude-code-skills` monorepo era and described an install path that no longer applies.

## [2.1.0] - 2026-05-02

### Changed

- **Distribution** — git-flow is now a standalone Claude Code marketplace at `abhattacherjee/git-flow`. No functional changes to commands or scripts.
- **`bump-version.sh`** — keeps `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` in lockstep with pre-bump validation (matches obsidian-brain pattern).

### Removed

- **Legacy `plugin-manifest.json`** — replaced by `.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` for standalone marketplace distribution.

### Migration notes

- Previously distributed via the `claude-code-skills` monorepo. The monorepo entry is being removed in a separate PR (see migration spec).
- Install: `/plugin marketplace add abhattacherjee/git-flow` then `/plugin install git-flow`.

## [2.0.0] - 2026-02-27

Two-tier architecture: generic Git Flow commands at user-level, project-specific overrides at project-level.

### Added

- **Two-tier command architecture** — generic `/feature`, `/release`, `/hotfix`, `/finish`, `/flow-status` commands at `~/.claude/commands/` with project-specific overrides in `.claude/commands/`
- **`scripts/git-flow-status.sh`** — comprehensive diagnostic script with `--json` output for agent consumption
- **`scripts/git-flow-finish.sh`** — hook-aware branch finish script that handles merge, tag, push, and cleanup
- **Plugin manifest** — `plugin-manifest.json` for plugin assembly and distribution via `skill-publishing`

### Changed

- **SKILL.md** — rewritten for two-tier architecture with generic commands, override system, and project-specific customization guide
- **Commands** — moved from project-level to user-level with dynamic context injection

## [1.0.0] - 2026-02-22

Initial release.

### Included

- **SKILL.md** — Git Flow branching model reference with merge targets, conventions, and branch naming
- **`scripts/git-flow-status.sh`** — repository state diagnostic (branch type, sync status, pending changes)
- **5 slash commands** — `/feature`, `/release`, `/hotfix`, `/finish`, `/flow-status`

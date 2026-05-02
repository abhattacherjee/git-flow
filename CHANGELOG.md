# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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

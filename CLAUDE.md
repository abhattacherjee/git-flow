# CLAUDE.md — git-flow plugin

This is the standalone repo for the `git-flow` Claude Code plugin. The plugin provides Git Flow branching commands (`/feature`, `/release`, `/hotfix`, `/finish`, `/flow-status`) and a status-diagnostic skill.

## Distribution

git-flow is distributed as a **standalone Claude Code marketplace** at `abhattacherjee/git-flow`. It is **not** synced to the `claude-code-skills` monorepo.

Install:
```
/plugin marketplace add abhattacherjee/git-flow
/plugin install git-flow
```

## Repo Layout

- `.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` — plugin + marketplace metadata. Both must move together; `scripts/bump-version.sh` keeps them in lockstep with pre-bump validation.
- `skills/git-flow/` — the diagnostic skill (`SKILL.md`, `scripts/git-flow-status.sh`, `references/override-guide.md`)
- `commands/` — the 5 slash commands (`feature`, `release`, `hotfix`, `finish`, `flow-status`)
- `scripts/` — dev/release scripts (installed by `/harden-repo`, `bump-version.sh` customized for plugin+marketplace lockstep)
- `.claude/` — local quality gates (PreToolUse hooks installed by `/harden-repo`)

## Git Flow Rules

- Never commit directly to `main` or `develop` — use feature branches
- Branch naming: `feature/*`, `release/*`, `hotfix/*`
- Features branch from and merge to `develop`
- Releases branch from `develop`, merge to both `main` and `develop`
- Hotfixes branch from `main`, merge to both `main` and `develop`
- Run `./scripts/commit-preflight.sh` before every commit (enforced by the `require-preflight` hook)
- The hook and `/finish` verify a PR's base immediately before merge, but GitHub
  does not make that lookup and merge atomic. A concurrent PR retarget can still
  win the interval; inspect the merged PR's recorded base when this matters.

## Releasing

1. On `develop`: `/release minor` (or `patch`/`major`) — cuts `release/X.Y.Z`, runs `bump-version.sh`, updates CHANGELOG
2. Edit CHANGELOG entries on the release branch, commit
3. `/finish` — merges to `main`, tags `vX.Y.Z`, merges back to `develop`, bumps develop to next patch (per `commands/finish.md` step 4b), pushes everything, creates a GitHub release

`bump-version.sh` updates **both** `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` so the marketplace listing stays in sync. It validates the marketplace entry exists *before* touching `plugin.json` so a sync failure can't leave the two files drifted.

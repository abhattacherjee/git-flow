# git-flow

A Claude Code plugin that brings the [Git Flow branching model](https://nvie.com/posts/a-successful-git-branching-model/) to the command palette. Five slash commands cover the full lifecycle (`/feature`, `/release`, `/hotfix`, `/finish`, `/flow-status`), backed by a skill that knows the conventions, gotchas, and override patterns. Works in any repo that uses Git Flow — Node, Python, Rust, monorepos, plugin repos, anything.

## What you get

**Five slash commands** that handle the routine moves so you stop thinking about which branch to cut from, what to merge to, and what to tag:

| Command | What it does |
|---|---|
| `/feature <name>` | Cuts `feature/<name>` from `develop`, pushes with tracking |
| `/release <version>` | Cuts `release/v<version>` from `develop`, bumps version files, updates `CHANGELOG.md`. A leading `v` is added if you omit it. |
| `/hotfix` | Cuts `hotfix/<auto-version>` from `main`, auto-increments the patch from the latest release tag |
| `/finish` | Merges the current branch to its target(s), tags releases/hotfixes, bumps `develop` to the next dev cycle, pushes everything, creates a GitHub release for tags |
| `/flow-status` | Shows current branch type, sync state, active branches, what `/finish` would do, and any drift from Git Flow conventions |

**One skill** (`git-flow`) loaded by the agent when the conversation touches branching decisions. The skill carries the branching diagram, the merge-target matrix, the semver-pick rules, and a list of real-world gotchas (squash-merge force-deletes, `--no-ff` discipline, push-hook workarounds, `grep -c` exit codes under `set -e`, and more).

**One status script** (`skills/git-flow/scripts/git-flow-status.sh`) that the `/flow-status` command shells out to. Also runnable directly with `--json` for piping into other tools.

## The branching model (ASCII version)

```
main ──────────────────────────────────────────────► (production, tagged releases)
  │                     ▲           ▲
  │                     │           │
  │              release/v1.3.0  hotfix/v1.2.1
  │                     ▲           │
  │                     │           │
  └──► develop ─────────┴───────────┴──────────────► (integration)
         │         ▲
         │         │
         └──► feature/my-feature
```

| Branch | From | To | Tag? |
|---|---|---|---|
| `feature/*` | `develop` | `develop` | No |
| `release/*` | `develop` | `main` + `develop` | Yes |
| `hotfix/*` | `main` | `main` + `develop` | Yes |

## Installation

```
/plugin marketplace add abhattacherjee/git-flow
/plugin install git-flow
```

Then run `/reload-plugins` (or restart Claude Code). The commands appear under the `git-flow:` namespace and — assuming nothing else in your install owns the same names — will also work unqualified (`/flow-status`, `/feature`, etc.).

That's it. There's nothing to clone, copy, or symlink. The plugin lives in Claude Code's plugin cache at `~/.claude/plugins/cache/git-flow-repo/git-flow/<version>/`.

### Quality gates

The plugin ships a `PreToolUse` Bash hook (`hooks/check-pr-base.py`) that
auto-activates on install. It blocks two categories of mistake on Git
Flow branches:

1. `gh pr create` without `--base`, or with the wrong `--base`.
2. `gh pr merge` against a PR whose `baseRefName` does not match the
   branch-type matrix below.

| Branch | Required base |
| --- | --- |
| `feature/*` | `develop` |
| `hotfix/*` | `main` |
| `release/*` | `main` |

The hook fails open for non-Git-Flow branches, single-trunk repos (no
`develop`), detached HEAD, and any `gh`/`git` failure — these conditions
never block legitimate work. Diagnostic breadcrumbs may be written to
stderr in security-relevant fail-open cases (gh failure, shell-expansion
`--base` value) so wrong-base merges that slip through can be debugged
post-hoc.

The same matrix is enforced in-script by `verify_pr_base()` in
`scripts/git-flow-finish.sh`, so `/finish` catches the failure even if
the hook is somehow disabled.

Both checks are pre-merge snapshots rather than an atomic server-side guard.
A user or automation with write access can retarget the PR after the final
`baseRefName` lookup and before GitHub processes `gh pr merge`. Review branch
protection and the merged PR's recorded base when this race is part of your
threat model; the plugin cannot guarantee the base remains unchanged during
that interval.

### Updating

```
/plugin marketplace update git-flow-repo
```

This pulls the latest release tag from GitHub. Pin to a specific version with `/plugin install git-flow@<version>` if you need stability over freshness.

### Uninstalling

```
/plugin uninstall git-flow
/plugin marketplace remove git-flow-repo   # if you don't want to receive updates anymore
```

## Walkthroughs

### Feature → develop

```
/feature user-authentication        # creates feature/user-authentication, pushes
… write code, commit, push …
/finish                             # merges to develop, deletes the branch
```

### Planned release

```
/release 1.3.0                      # creates release/v1.3.0, bumps versions, updates CHANGELOG
… edit CHANGELOG entries on the release branch …
/finish                             # merges to main + develop, tags v1.3.0, bumps develop to 1.3.1, creates GitHub release
```

### Emergency hotfix

```
/hotfix                             # auto-versions from latest main tag (1.2.0 → 1.2.1), branches off main
… minimal fix, commit, push …
/finish                             # merges to main + develop, tags, releases
```

### Status check

```
/flow-status
```

Prints which branch you're on, what type it is, sync status with the remote, what `/finish` would do, and any anomalies (no `develop` branch, dirty tree, branches diverged, etc.).

## Project-level overrides

The five commands ship as **generic** versions that work in any Git Flow repo. When a project needs custom behavior — monorepo version-bump fan-out, push-hook workarounds, ticket-prefixed branch naming, parallel release artifacts — drop a same-name file in `.claude/commands/` at the project root. Claude Code resolves project-level commands first, falling back to the plugin version.

Common reasons to override:

- **Monorepo** — bump multiple `package.json` (or `pyproject.toml`, `Cargo.toml`) files in lockstep on `/release`
- **Push hooks** — repo blocks direct pushes to `main`/`develop`, so `/finish` needs the temp-branch + `gh api` ref-patch workaround
- **Release pipeline** — `/finish` should trigger doc generation, deploy hooks, or recommend a project-specific `/finalize-release` instead
- **Naming conventions** — branches must be `feature/JIRA-123-description` instead of plain kebab-case
- **Non-npm projects** — version lives in `pyproject.toml` / `Cargo.toml` / `version.txt` rather than `package.json`

The `references/override-guide.md` file (shipped with this plugin, read it via the skill) documents the full override surface per command, with patterns and a checklist.

## Configuration

Two environment variables let you point the commands at non-default branch names:

| Variable | Default | Purpose |
|---|---|---|
| `GIT_FLOW_MAIN_BRANCH` | `main` | Production branch name |
| `GIT_FLOW_DEVELOP_BRANCH` | `develop` | Integration branch name |

Two more tune the CI gate `/finish` runs before it merges a release PR to `main`:

| Variable | Default | Purpose |
|---|---|---|
| `GIT_FLOW_CHECKS_GRACE` | `60` | Seconds to wait for check runs to register before deciding the repo has no CI. Set `0` to skip the wait. |
| `GIT_FLOW_CHECKS_POLL` | `5` | Seconds between polls during that wait. Must be 1 or more. |

GitHub registers check runs a few seconds after a PR opens, and `gh pr checks --watch` does not
wait for them — it reports on what it can already see. Straight after `gh pr create` that looks
identical to "this repo has no CI", so `/finish` waits `GIT_FLOW_CHECKS_GRACE` seconds before
believing it. On a repo you know has no CI, `GIT_FLOW_CHECKS_GRACE=0` skips the wait. If no checks
ever appear, `/finish` says so both at the time and again in the closing summary, and merges
without a CI gate.

Both variables must be whole numbers written without a leading zero — `10`, not `010`. Anything
else aborts with a message naming the variable. The leading-zero rule is not style: bash reads
`08` and `010` as octal, so `GIT_FLOW_CHECKS_GRACE=010` would wait 8 seconds and `08` would fail
every comparison and hang.

## Gotchas worth knowing

The skill includes detailed write-ups for these — quick summary so you know they exist:

- **Squash-merged branches need `git branch -D`** (force delete). `gh pr merge --squash` creates a new commit SHA, so `-d` thinks the branch isn't merged.
- **`--no-ff` always.** Use `git merge --no-ff` so branch history stays in the graph. Fast-forward merges erase the "this was a feature" signal.
- **Tag filtering for semver.** `git describe --tags --abbrev=0` picks any tag. Use `git tag -l 'v[0-9]*' --sort=-v:refname | head -1` to filter to semver tags only.
- **Push hooks may block direct pushes.** Some repos guard `main`/`develop` with pre-push hooks. The skill documents the temp-branch + `gh api` ref-patch workaround.
- **`grep -c` returns exit 1 on zero matches**, which kills bash scripts under `set -e`. The skill includes a `count_lines` helper that handles the empty case.
- **Stale worktrees block checkout.** `git checkout develop` failing with "already used by worktree" → `git worktree prune`.

Ask the agent about any of these in conversation; the skill auto-loads when relevant.

## What's in the box

```
git-flow/
├── .claude-plugin/
│   ├── plugin.json              # Plugin metadata (name, version, description)
│   └── marketplace.json         # Marketplace manifest (consumed by /plugin marketplace add)
├── commands/
│   ├── feature.md               # /feature
│   ├── release.md               # /release
│   ├── hotfix.md                # /hotfix
│   ├── finish.md                # /finish
│   └── flow-status.md           # /flow-status
├── skills/
│   └── git-flow/
│       ├── SKILL.md             # Reference + branching model + gotchas
│       ├── scripts/
│       │   └── git-flow-status.sh   # Diagnostic script (also: --json, --help)
│       └── references/
│           └── override-guide.md    # Project-level command override patterns
├── CLAUDE.md                    # Conventions for working IN this repo (not for consumers)
├── CHANGELOG.md                 # Keep-a-Changelog format
├── LICENSE
└── README.md                    # You are here
```

## See also

- Companion plugins worth pairing with this one:
  - `git-branch-cleanup` — audit and delete stale branches after merges
  - `changelog-keeper` — generate `CHANGELOG.md` entries from commit history
- [Original Git Flow post by Vincent Driessen](https://nvie.com/posts/a-successful-git-branching-model/)
- [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — the format this plugin's `/release` flow assumes for `CHANGELOG.md`
- [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

## License

[MIT](LICENSE)

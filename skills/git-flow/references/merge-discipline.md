# Merge & PR-Base Discipline

How to target pull requests and how to merge them safely under Git Flow. The
`check-pr-base.py` PreToolUse hook *enforces* the base for `feature/`,
`release/`, and `hotfix/` PRs (feature-branch enforcement requires a `develop`
branch; single-trunk repos pass through); this doc
explains the discipline around it — most importantly, why a wrong-base merge
to `main` is a structural incident rather than a cosmetic mistake.

## The core rule

**Feature work never merges to `main`.** `main` is release-only — it should
only ever advance through the release/hotfix flow.

| Branch | PR base | After merge |
| ------ | ------- | ----------- |
| `feature/*` | `develop` | delete branch |
| `release/*` | `main` | tag, then back-merge `main` → `develop` |
| `hotfix/*` | `main` | tag, then back-merge `main` → `develop` |

In single-trunk repos (no `develop`), `feature/*` targets `main` directly — but
only because `main` *is* the integration branch there.

## Always target the base explicitly

- Never rely on the repository's **default** branch when opening a PR. Pass
  `--base` explicitly: `gh pr create --base develop ...`.
- **Verify the base before merging:**
  `gh pr view <N> --json baseRefName --jq '.baseRefName'`.
  If a `feature/*` PR's base is not `develop`, **stop** — do not merge.

## Create vs. merge to `main`

These are different actions with different risk:

- **Creating** a `--base main` PR is legitimate and expected for `release/*` and
  `hotfix/*` — it is the documented release flow. Allowed.
- **Merging** to `main` (`gh pr merge`, `git push origin main`, or a local merge
  onto `main` followed by a push) is the gated action. Treat it as deliberate:
  confirm the branch type and the base first, every time.

The `check-pr-base.py` hook covers both `gh pr create` and `gh pr merge` — it
blocks a `gh pr merge` whose base is wrong for the head branch's type. What it
does *not* intercept is a direct `git push origin main` or a local merge onto
`main` followed by a push; those remain a discipline gate, optionally hardened
with a `Stop`/pre-push hook in your own setup.

## Why this matters — the failure mode

When a `feature/*` branch is merged into `main`, it poisons future release
merges. A later `git revert` on `main` does **not** cleanly undo it: the revert
records a tree state that diverges from `develop`, so when the next `release/*`
is merged back, Git can **silently drop files** that the revert "removed" —
without a conflict to warn you. The result is missing code on `main` that no
diff against `develop` will obviously explain.

The damage is not naively fixable (a second revert compounds the divergence).
So treat a wrong-base merge to `main` as a structural incident: stop, and
reconcile `main` and `develop` deliberately (often a fresh sync-merge plus
explicit cherry-picks), rather than reverting your way out.

## Checklist

- [ ] Branch named for its type (`feature/`, `release/`, `hotfix/`).
- [ ] PR opened with an explicit `--base`.
- [ ] Base verified immediately before merge.
- [ ] `main` merges are only `release/*` or `hotfix/*`, and are intentional.
- [ ] After a `main` merge: tag, then back-merge `main` → `develop`.

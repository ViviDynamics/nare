---
name: conventions
description: Use when starting work in the nare repository, when unsure which workflow skill applies, or when asked how nare ships code, watches CI, or merges.
license: Elastic-2.0
compatibility: Any agent that can run bash, gh, jq and git.
metadata:
  version: "1.0.0"
---

# How nare ships

Workflow skills live in `.agents/skills/` (also reachable as `.claude/skills/` and
`.opencode/skill/`). Repo-specific settings live in `repo.env`, copied from the committed
`repo.env.example`. Scripts do the mechanical work and print JSON; you make the judgment
calls the skill names, and you cite the script output for every claim.

**Where the scripts are.** `S` is a skill's `scripts/` directory, next to its SKILL.md.
Set it once per session, for example `S=.agents/skills/<name>/scripts`, then run every
script as `$S/<script>`.

## Which skill, when

| You want to | Use |
| --- | --- |
| Know the CI rules before touching a run | `ci-safety` (read first) |
| Wait for a PR's CI, retry safely, find out why it failed | `watch-ci` |
| Merge a PR, or decide whether it can merge | `merge-pr` |

## How nare is set up

- **One CI workflow.** `.github/workflows/ci.yml` runs `bin/build`: ruff format, ruff
  check, mypy strict, pytest. Run `bin/build` locally before pushing.
- **GitHub-hosted runners only.** nare is public. Never point a workflow at a
  self-hosted runner: a pull request's code would run on private hardware.
- **Squash merges only**, and every review thread must be resolved before `main`
  accepts a merge. No admin bypass is configured or needed.
- **Releases are automatic.** A push to `main` that passes CI is tagged `YYYY.M.N` by
  `.github/workflows/release.yml`. Never tag or release by hand.
- **Outside contributions** go through Discussions, not pull requests. See
  `CONTRIBUTING.md`.

## Ground rules

1. Every `gh` call in a skill goes through the skill's scripts: a purpose-built script,
   or `$S/vgh` for one-off reads. The scripts read the token from the file named by
   `VIVI_GH_TOKEN_FILE` in `repo.env`, falling back to `GH_TOKEN` or `GITHUB_TOKEN` in
   the environment.
2. A claim needs a read-back. "Merged" means `merge-verified` printed `merged`.
   "Retried" means `verified-rerun` exited 0.
3. State for a long chain lives in `.agents/state/<issue>.json` via `$S/state`.
   Read it before deciding, write it after acting. It is gitignored.
4. Never force-push except on your own unmerged branch, and then only with
   `--force-with-lease`. Never push to `main`.
5. Never disable, skip or delete a test to make CI green.
6. Prose we publish (PR bodies, comments, docs) uses no em dashes.

## Setting up a fresh clone

    cp repo.env.example repo.env
    .agents/skills/ci-safety/scripts/check-wiring

`check-wiring` prints `{"ok":true}` when everything is in place, or a `problems` list.
CI runs the same check on every push.

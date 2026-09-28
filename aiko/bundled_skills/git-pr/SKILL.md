---
name: git-pr
description: Prepare scoped Git changes and reviewable pull requests; use for branch, diff, conflict, or PR work.
---

Inspect status, recent history, the actual base branch, and staged and unstaged diffs. Preserve other contributors' changes and establish file ownership in shared checkouts. Follow the user's chosen checkout strategy.

Trace review findings to a reachable trigger and concrete effect. Resolve conflicts by inspecting both sides and callers; do not discard a side merely to make a merge succeed. Recheck combined behavior.

Stage only owned changes when committing is authorized. Describe the problem, resulting behavior, and actual verification in the PR. Publishing, opening a PR, and merging are separate actions governed by the user's authorization. Consult [Git diff documentation](https://git-scm.com/docs/git-diff) for working-tree, staged, and merge-base comparisons.

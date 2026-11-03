---
name: hk-merge-resolve
description: "Resolve the conflicts of an in-progress merge, rebase or cherry-pick, then run the affected suite with the verify gate the entrypoint names. Triggers on: 'merge conflict', 'resolve conflicts', 'conflict markers', 'rebase conflict', or /hk-merge-resolve."
allowed-tools: Bash, Read, Edit, Grep, Glob, AskUserQuestion
---

This skill lives in `docs/skills/git/hk-merge-resolve/`. Read `docs/skills/git/hk-merge-resolve/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/git/hk-merge-resolve/`.

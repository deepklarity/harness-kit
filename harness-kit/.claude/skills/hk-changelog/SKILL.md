---
name: hk-changelog
description: "Write a CHANGELOG.md entry from the branch's diff against main, then commit it on a changelog branch and open a PR. Triggers on: 'update the changelog', 'changelog entry', 'add to the changelog', or /hk-changelog."
argument-hint: "[--dry-run | --commit]"
allowed-tools: Bash, Read, Edit, Write, Agent
---

This skill lives in `docs/skills/git/hk-changelog/`. Read `docs/skills/git/hk-changelog/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/git/hk-changelog/`.

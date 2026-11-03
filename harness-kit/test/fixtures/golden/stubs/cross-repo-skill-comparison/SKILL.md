---
name: cross-repo-skill-comparison
description: "Compare the Claude skills across two or more repos (paths or GitHub links) and write one HTML page: which skills exist where, which copy is newest, what each fork changed, which are worth pulling into this repo. Triggers on: 'compare skills across repos', 'which repo has the latest skill', 'sync skills between projects', 'skill drift', or /cross-repo-skill-comparison."
argument-hint: "<repo path or URL> <repo path or URL> [more...]"
allowed-tools: Bash, Read, Write, Grep, Glob, Agent
---

This skill lives in `docs/skills/knowledge/cross-repo-skill-comparison/`. Read `docs/skills/knowledge/cross-repo-skill-comparison/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/knowledge/cross-repo-skill-comparison/`.

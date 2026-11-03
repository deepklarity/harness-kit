---
name: hk-skill-creator
description: "Create a skill in this repo's layout (the body under docs/skills plus the .claude/skills stub) or run evals on one: draft it, run it against a baseline on realistic prompts, judge the outputs, tighten the description. Triggers on: 'create a skill', 'new skill', 'make a skill for', 'skill eval', or /hk-skill-creator. An existing skill that reads badly is /skill-audit."
argument-hint: "[what the skill should do, or the path of the skill to eval]"
allowed-tools: Bash, Read, Edit, Write, Grep, Glob, Agent
---

This skill lives in `docs/skills/knowledge/hk-skill-creator/`. Read `docs/skills/knowledge/hk-skill-creator/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/knowledge/hk-skill-creator/`.

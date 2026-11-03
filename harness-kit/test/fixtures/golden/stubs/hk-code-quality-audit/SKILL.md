---
name: hk-code-quality-audit
description: "Find what nobody would miss and what reads as generated, and write it up as one HTML page scored out of 10: dead code, pasted templates, wrappers with one caller, scratch files, hardcoded hosts, unused packages. Triggers on: 'find slop', 'AI slop', 'hygiene check', 'dead code', 'dead endpoints', 'unused dependencies', 'backend slop', 'frontend slop', or /hk-code-quality-audit."
argument-hint: "[backend | frontend | a directory | one category name, default: whole repo]"
allowed-tools: Bash, Read, Grep, Glob, Write, Agent
---

This skill lives in `docs/skills/audits/hk-code-quality-audit/`. Read `docs/skills/audits/hk-code-quality-audit/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/audits/hk-code-quality-audit/`.

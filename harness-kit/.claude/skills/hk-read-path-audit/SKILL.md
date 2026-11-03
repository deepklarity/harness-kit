---
name: hk-read-path-audit
description: "Audit the documents an agent reads for a job, as one HTML page scored out of 10: their token cost, what they state twice, where they contradict each other or the code, what is read and never used, each with the exact edit; applies the edits when asked. Triggers on: 'what does a session read', 'token cost of the docs', 'find contradictions', 'simplify the read path', or /hk-read-path-audit."
argument-hint: "[a folder, files, or a vague reference like 'the mission control convention'; add 'apply' to make the edits from the last page]"
allowed-tools: Bash, Read, Grep, Glob, Write, Edit, Agent
---

This skill lives in `docs/skills/audits/hk-read-path-audit/`. Read `docs/skills/audits/hk-read-path-audit/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/audits/hk-read-path-audit/`.

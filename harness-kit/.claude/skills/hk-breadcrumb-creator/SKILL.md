---
name: hk-breadcrumb-creator
description: "Trace a flow through the code and write its breadcrumb (FLOW.md, DETAILS.md, DEBUG.md) under docs/breadcrumb_analysis/, with the index row. Triggers on: 'write a breadcrumb', 'add a breadcrumb', 'trace this flow', 'document this flow', 'write a debug guide for', or /hk-breadcrumb-creator."
argument-hint: "<flow name or description, e.g. 'quota failover' or 'proof upload from agent to task page'>"
---

This skill lives in `docs/skills/debugging/hk-breadcrumb-creator/`. Read `docs/skills/debugging/hk-breadcrumb-creator/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/debugging/hk-breadcrumb-creator/`.

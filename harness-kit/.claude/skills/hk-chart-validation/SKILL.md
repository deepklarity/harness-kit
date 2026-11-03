---
name: hk-chart-validation
description: "Check that the charts in a codebase tell the truth: chart type, axis baselines, aggregation, units, labels, tooltips and how they render in Chrome at each width; writes the findings as one HTML page with a score out of 10. Triggers on: 'validate the charts', 'is this chart misleading', 'chart audit', 'check the dashboard numbers', or /hk-chart-validation."
allowed-tools: Bash, Read, Grep, Glob, Write, mcp__chrome-devtools__*
generated: true
---

This skill lives in `docs/skills/audits/hk-chart-validation/`. Read `docs/skills/audits/hk-chart-validation/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/audits/hk-chart-validation/`.

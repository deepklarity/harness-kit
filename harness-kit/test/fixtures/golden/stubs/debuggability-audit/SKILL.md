---
name: debuggability-audit
description: "Find where a failure leaves no usable evidence, in the logs or on the screen: swallowed exceptions, context-free logs, silent fallbacks, errors caught and only logged, error fields no component reads; scored out of 10, guard mode checks the diff. Triggers on: 'is this debuggable', 'logging audit', 'silent error', 'check error surfacing', or /debuggability-audit."
argument-hint: "[guard | audit <path> | <path>]  - default: audit the whole repo; guard checks the current diff"
---

This skill lives in `docs/skills/audits/debuggability-audit/`. Read `docs/skills/audits/debuggability-audit/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/audits/debuggability-audit/`.

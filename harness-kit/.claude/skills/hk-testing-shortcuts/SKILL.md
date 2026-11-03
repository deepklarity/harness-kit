---
name: hk-testing-shortcuts
description: "Answer a testing question (a command, a path, how to get a test id) from the repo's own testing docs, and when the answer was not written down yet, save it as a guide in docs/testing_process/ or a script in the workspace's testing tools folder. Triggers on: 'how do I test', 'get me the ID', 'what's the command for', 'save this as a shortcut', or /hk-testing-shortcuts."
argument-hint: "[what you're trying to test or look up]"
---

This skill lives in `docs/skills/debugging/hk-testing-shortcuts/`. Read `docs/skills/debugging/hk-testing-shortcuts/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/debugging/hk-testing-shortcuts/`.

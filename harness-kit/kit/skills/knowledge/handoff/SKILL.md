---
name: "handoff"
description: "Compact the current conversation into a handoff document a fresh agent can resume from: where the work stands, what was decided and why, what is next, which files to open first. User-invoked only: /handoff."
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
imported: true
imported_from: "https://github.com/mattpocock/skills/tree/main/skills/productivity/handoff"
---

Write a handoff document so a fresh agent can continue the work without this conversation. It answers, in this order: where the work stands now, what was decided and why, what to do next, and which files to open first. A reader should not have to learn the history to learn where things stand.

Save it on disk where the repo keeps resumable work:

- If the work already has a tracker (`docs/mock-first/<slug>/tracker.md` from hk-mock-first, or a `loop.md` in the scratch directory beside the thing hk-refine is refining), write the handoff into that file by rewriting its Current state, Decisions (tracker.md), What we've learned and Next sections; the rest of the file stays as it is.
- Otherwise write `writeup/handoff-<slug>.md` (path from the repo root) with the four parts as headings. `writeup/` is gitignored, so a handoff from a debugging or audit session never becomes a committed file or a fake feature workspace, and it sits at a fixed path the next session finds with `ls writeup/`; the session scratchpad would vanish with this session.

Include a "suggested skills" section naming which skills the next agent should call the Skill tool for.

Content already captured elsewhere (specs, plans, issues, commits, diffs, pattern docs) is referenced by path or URL, not repeated. Redact API keys, passwords and personal information.

If the user passed arguments, they describe what the next session will focus on; shape the document around that.

Done when the file is on disk and its path has been printed in chat. Nothing is committed.

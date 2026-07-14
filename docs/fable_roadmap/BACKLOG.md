# BACKLOG

The queue. What we've agreed to do next, in order. The WHY for everything
here lives in the bucket files linked from [`fable_roadmap.md`](fable_roadmap.md), and
every task created from this list carries its bucket's WHY in the
description. Finished work gets deleted, git remembers it.

## Now

- **Quickstart follow-ups.** The Quickstart shipped (docs/Quickstart.md +
  docs/adoption/CHECKLIST.md) and works — live-tested on an outside
  workspace. What's left from that run: handle multi-repo workspaces,
  define scoring for them, inline the tone rules so the checklist works
  without harness-kit checked out, and cost awareness (user directive):
  the flow should approximate and log what the whole run cost — audit,
  report, adoption — so adopters see the price of the loop, not just the
  score. Bucket: [getting-started-ease](buckets/getting-started-ease.md).

Wave 12 draft, in order: (1) merge agent v2 — never splice two file
versions silently, compose-or-park, and a reply parser that accepts prose;
(2) fix agy's exec path (benched until then); (3) reviewer protocol for
host-side claims the sandbox can't see; (4) reflection-pass must fire from
any status; (5) the metrics script so audits stop hand-counting; (6) stage
one deliberate failure to prove 335/331/332 live. Then: the second instance
on the Linux box (a setup task, not a decision — msb runs on KVM there the
same way it runs on HVF here) and the second external project.

- **Known-red tests, on the record.** The verify gate reports 9 red tests
  honestly rather than masking them: 4 SettingsView reviewer tests describe
  the layered reviewer-config UI that hasn't been built yet (they merged
  ahead of the feature — build it or move the tests next to the work), and
  5 snapshot cost tests have a standing fix task. Everything else is green:
  odin 2004, backend 1583, the rest of frontend and snapshots.

- **Rethink the inbox (user finding, design before build).** Today's inbox
  is too many rows and says nothing the FAILED column doesn't. Before any
  more inbox code: sit with what a genuinely useful inbox means — probably
  "the five things that need a decision, each with the decision options
  inline", not a task list. Design first, with the user, then build.

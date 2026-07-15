# BACKLOG

The queue. What we've agreed to do next, in order. The WHY for everything
here lives in the bucket files linked from [`fable_roadmap.md`](fable_roadmap.md), and
every task created from this list carries its bucket's WHY in the
description. Finished work gets deleted, git remembers it.

## Now

- **Merge agent v2 — the next wave opens with this.** Two live failures in
  one shift: (a) a guided merge resolves per-file with one uniform action,
  so answering "task side" on one hunk silently wiped a sibling task's 122
  lines in the same file (caught by the verify gate, repaired by hand);
  (b) the reply parser rejected three reasonable answers including its own
  example phrase. Compose-or-park per hunk, and a parser that accepts
  prose. Bucket: [trust](buckets/trust.md).

- **Second instance on the Linux box.** A setup task, not a decision. The
  sleep pain proved the single-laptop cost again; the reap fix removes the
  killing, not the freezing. Bucket: [runs-anywhere](buckets/runs-anywhere.md).

- **Gitleaks as a skill and a preset.** Before tonight's public push, an
  independent gitleaks sweep of the full history caught a credential
  pattern every hand-rolled grep missed (it turned out to be Google's
  public Gemini CLI client, but the point stands: the tool sees what
  patterns don't). Wrap it twice: an hk- skill for operator sessions
  (scan history before any public push — make it a named step of the
  release checklist) and a board preset so agents can run it on any
  repo the kit works on. Bucket: [audits](buckets/audits.md).

- **Proof failures must be loud, and the tool must not lie.** A user's
  agent spent 14 minutes probing because (a) a failed screenshot upload is
  swallowed into a return-value footnote instead of a board comment, and
  (b) the taskit_add_comment description implies file_paths get uploaded
  when they are stored as metadata only (the host uploads .proof files at
  reflection). Fix both: failed uploads post a visible warning comment;
  the tool text says exactly what each parameter does. Also: planning dies
  at routing when the planner invents capability labels no agent declares
  ("No viable route", caps like html/file_writing) — unknown labels must
  warn and be ignored, not fail the plan (two user reports, diagnosis in
  orchestrator.py:3928). Bucket: [trust](buckets/trust.md).

- **Spec-branch initialization must not race.** When two tasks of a fresh
  spec dispatch together, worktree creation races and one fails
  missing_worktree — four times in one day, every requeue succeeded. Fix at
  the cause: the executor serializes (or lock-retries) the first worktree
  creation per spec instead of failing the loser. Until then the runbook
  staggers the first dispatch of a new spec. Bucket:
  [trust](buckets/trust.md).

- **Quickstart follow-ups.** The Quickstart shipped (docs/Quickstart.md +
  docs/adoption/CHECKLIST.md) and works — live-tested on an outside
  workspace. What's left from that run: handle multi-repo workspaces,
  define scoring for them, inline the tone rules so the checklist works
  without harness-kit checked out, and cost awareness (user directive):
  the flow should approximate and log what the whole run cost — audit,
  report, adoption — so adopters see the price of the loop, not just the
  score. Bucket: [getting-started-ease](buckets/getting-started-ease.md).

Wave 12 draft, in order: (1) merge agent v2 — never splice two file
versions silently, compose-or-park, and a reply parser that accepts prose
(live evidence: a parked merge rejected three reasonable replies including
the question's own example phrase "keep-both for everything" — the parser
matches "keep both" with a space, and the re-ask message hides the real
failure; "keep the spec side" finally worked);
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

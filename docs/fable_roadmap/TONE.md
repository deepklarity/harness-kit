# How we write here

Write the way you talk. Simple words, short sentences. If you would not say
it out loud to a teammate, don't write it.

Say what happened and why it matters. You were there, so put that in. We had
this rule:

> Refill rule, HARD TRIGGER (user directive, repeated twice): the moment any
> board-watcher heartbeat shows fewer than cap+2 live tasks, refill IN THAT
> SAME WORKING BEAT, before any other action.

Nobody talks like that. What we actually mean is:

> The queue ran empty twice while we were busy with other things. So now
> it's simple. If the watcher shows less than cap+2 tasks running, add work
> first. Everything else waits. An empty slot is lost time we never get back.

Same rule. But now you can hear a person saying it, and you know why the
rule exists.

A few habits that follow from this:

- Use everyday words. Not "utilize", "leverage", "facilitate". Just "use",
  "help", "do".
- Plain punctuation. Periods and commas. No semicolons, no long dashes.
  If a sentence needs them, it's two sentences.
- Give the real example, the real number, the real file name. "This broke
  twice" beats "this is a known risk area".
- Delete what's done. Old finished items sitting in a doc means nobody
  trusts the doc.
- If it sounds like a report or a contract, rewrite it.
- Don't give incidents nicknames or drama. "The trace-poisoning saga",
  "the epidemic", "the serial killer" — that's narration, not information.
  Say what happened: "the system killed every retry as soon as it
  started, because it judged them by an old log file left over from a
  dead run." No system slang either — "reaped", "materialized",
  "fingerprinted" mean nothing to a person who doesn't live in the code.
  A person who wasn't there should understand the failure from the
  sentence itself.
- Don't make the system a character. The kit does not lie, scream, survive,
  or fix itself. Say who did what: the counter reported a wrong number, the
  alert shows on the board, the fix merged. Character talk hides the facts
  and it spreads — one dramatic line in a doc becomes the house style.
- A verdict or headline is a plain spoken sentence, not styled writing of
  any flavor. We wrote "The kit stopped lying to us — now two faults, one
  decision, and a Quickstart stand between it and strangers" and the user
  called it fluff. We rewrote it as "Gate green, two tracks live. Two
  faults open" and that was slop too — clipped radio-speak is as much a
  costume as drama. Both are writing that knows it's a headline. Say it
  the way you'd say it across a desk: "Two things are broken. Nothing
  needs you. The tests pass on everything merged." Big type on a page
  does not change the writing rules, and it does not excuse shorthand —
  "gate green" means nothing to someone who doesn't know the gate.
- This file wins everywhere a person reads: docs, board comments, status
  updates, audit reports, and artifact pages (Mission Control included).
  If a design template or a styling guide pulls toward headline-writing,
  this file overrides it.
- Read it out loud once before you commit. If you trip on a sentence, fix it.
- A task title is something you could say to a teammate out loud. If it
  needs a system word to make sense — "reap", "materialize",
  "fingerprint" — it fails this test even if every other rule here is
  followed.
- Read it out loud once before you commit. If you trip on a sentence, fix it.

Anyone on the team, anywhere in the world, should read our docs and feel
like a person is talking to them. That's the test.

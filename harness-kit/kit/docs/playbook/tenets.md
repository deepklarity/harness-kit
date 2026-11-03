These are the eleven rules a workspace is checked against, no matter which
repo it lives in. Every row on a readiness checklist and every row on a
board names one of them by its heading; a row that names nothing gets rejected.

## 1. Progressive disclosure

Put one screen in front of the reader, with the detail behind a click. A
short entrypoint should point outward, not carry everything itself. Nobody
reads a wall before deciding whether the thing is relevant to them.

Looks wrong when: the entrypoint doc runs a thousand lines and the actual
setup steps are buried on line 400.

## 2. Spend tokens where judgment is

Checking whether something is present is a script or a listing. Checking
whether it's any good is a mid-tier model. Blending several judgments into
one verdict is the strong model. Every row and every check should say
which tier it ran at, and the cost of a run should be visible. This isn't
about squeezing every run to the cheapest possible model — a wrong cheap
answer costs more to clean up than a right expensive one would have cost
to produce.

Looks wrong when: a strong model is asked "does this file exist," or a
script is asked "is this test suite any good."

## 3. The disk is the memory

Work that spans more than one sitting needs a trail on disk — state,
decisions, what's next — because the conversation that produced it won't
be there next time. A session should be able to pick up cold from what's
on disk alone.

Looks wrong when: resuming a task means re-reading a chat transcript
because nothing survived to a file.

A question for a person is a decision row on the board with a proposed answer, not a chat message: the page is where a person and an agent meet, and an unanswered row stays visible instead of scrolling away.

## 4. One fact, one place

Write a piece of state once and render it everywhere else. Two documents
holding the same fact will disagree eventually, and then nobody knows
which one to trust.

Looks wrong when: a status shows up as "done" in one file and "in
progress" in another, both last touched today. Also: the same key pasted into forty file headers when one table would have held it.

## 5. Proof before done

A row on a board closes when its proof condition holds, not when someone
believes it's finished. "Should work" is not proof; a passing check,
a live run, a diff someone read is.

Looks wrong when: a task is marked done with no test run, no output, no
artifact — just the claim.

## 6. Read before write, never write what you weren't asked to

A scan of a workspace should execute nothing in it. Applying a change
should touch only what it was told to touch, never anything else the
workspace happens to own.

The kit knows nothing about a workspace until it looks. How many repos
there are, what they run on, what the gate and the stack script are
called, where the logs go: all of it is read from the workspace, never
written into the kit or told to the run. A kit file may show one of these
as a labelled example, never as a fact.

Looks wrong when: an audit script that's supposed to only look, quietly
edits a file, or a fix for one thing rewrites an unrelated config, or a
kit document names a count of repos because the workspace it was tested
on happened to have that many.

## 7. Default first, override by tier

Everything should work with no setup, using a sensible default. Someone
who wants something different overrides it — they shouldn't have to
configure anything just to get started.

Looks wrong when: a tool refuses to run until three config files are
filled in that could have shipped with reasonable values.

## 8. Worst first, scored honestly

When you're ranking problems, put the worst one at the top, and score it
by what's actually true, not by what makes the report look better. A
score that flatters nobody helps.

Looks wrong when: a checklist buries a real gap under ten passing items,
or rounds a 3 up to a 7 because the rest of the page looked fine.

## 9. Nothing learned twice

A pattern discovered once should be written down once, in a place the
next run will read before it hits the same wall. Breadcrumbs, patterns,
small tools — whatever form it takes, it should exist so the second
occurrence is faster than the first.

Looks wrong when: two sessions independently rediscover the same gotcha a
week apart because neither wrote it anywhere.

## 10. Written for the reader who was not there

Assume the reader opens the page with nothing else in front of them and no
memory of how it came to be. A tone document, where one exists, wins any
argument about wording — clarity to that stranger beats house style beats
personal preference.

No labels in place of words. A step, a phase, a row or a task is named by
what it does ("now the board gets its work rows"), never by its number or
id ("now step 6", "doing T3"). The number can follow in brackets for the
person who wants to find it; it never stands alone. This is enforced: a
checkpoint that says "step 6" is rewritten before it is sent.

Looks wrong when: a doc leans on "as discussed" or a bare ID with no
explanation of what it names, or a status update reads "step 6 done, step
7 next".

## 11. Self-healing

When something regresses, the tool that watches for it should reopen the
row itself, not wait for a person to notice by hand. A check that can
catch its own failure should also catch its own recovery.

Looks wrong when: a broken check sits red for weeks because nobody was
looking at that particular page.

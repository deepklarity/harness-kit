# Tone and taste

This is the canonical copy. The writeup skill
(`docs/skills/knowledge/hk-writeup/SKILL.md`) points here. It covers everything a
person reads from us: docs, pages, reports, chat messages, commit
messages, task titles.

## How we write

Write the way you talk. If you wouldn't say the sentence out loud to the
person sitting next to you, don't write it.

That's really the whole rule. Everything below is just what it looks like
in practice.

**Say what happened, plainly.** "The database was four migrations behind,
so nothing could finish. We ran the migrations and it works now." Not
"resolved a critical DB schema synchronization issue." The second one
sounds more professional and says less.

**Use the words you'd actually use.** Use, help, do, broke, fixed. Not
utilize, facilitate, leverage, remediate.

**Give the real thing.** The real file name, the real number, the real
error message. "This broke twice this week" tells someone more than "this
is a known risk area."

**Don't dress things up.** No nicknames for incidents, no drama, no
making the system a character that "lies" or "fights back." And no
squeezing either — "platform 22/23 ×2" is not clearer than "the platform
suite passed twice, 22 tests each time," it's just shorter. Someone
reading it has to stop and decode it. Both dressing up and squeezing down
are the same mistake: writing that's thinking about how it looks instead
of what it says.

**Say what the thing is, not its label.** "Now the board gets its work
rows, then the page tokens, then a fresh score" tells the person what is
about to happen. "Now step 6, then step 7" tells them nothing unless they
have the document open. Same for row ids, phase names and ticket numbers:
the words first, the label in brackets after if it helps someone find it.

**Skip the grading words.** Honest, robust, crucial, comprehensive,
seamless. If something matters, say why it matters. "This must pass from
a fresh database, because a dirty one can hide tests leaking into each
other" — no adjective needed.

**Assume the reader remembers nothing.** They read this a month from now
with nothing else open. So a reference carries its meaning with it:
"equipment-routing.spec.ts is failing (the mock AFM and the real one now
generate different scripts)" — the parenthesis is what saves them a
search.

**A task ID never travels alone.** `W2-01` is a filing key, not a name —
the reader should never need the board open to follow the sentence. Say
what the task does in the same breath: "W2-01 (labware allocation moves
onto the platform)", "the abort path now kills the run's SDK process
(ORPHAN-SDK-01)". First mention in a message carries the words; after
that the bare ID may stand in. This holds everywhere IDs appear — chat,
pages, log entries, commit messages.

**When something is stuck waiting on a person, say so unmissably.** One
short block: what's blocked, the one action needed, what it costs while
it waits. Save that shape for real blockers only, or people stop seeing
it.

**No timestamps on notes.** "Decided 2026-08-08", "(what worked,
2026-08-07)" — once a note is a note, the date is noise; git history has
it if anyone ever cares. Dates belong only where time is the content: a
log entry, a run result.

**Format chat for a tired human eye, and calibrate on the reader.**
Keep the detail — cut the effort of consuming it: short line reach,
visual anchors to skim by, complete plain sentences that explain
themselves. What shape that takes depends on the content and the
moment; there is no house layout for a chat message. Two failure
directions to steer between: the wall (all the detail, no way in) and
the overcorrection (headings, tables, fragments — structure that
replaces the meaning). When the reader says something is hard to
consume, that is formatting feedback, not a request for less — reshape
it and ask if it landed. Answer first, bookkeeping after.

**Read it out loud before you publish.** If you trip on a sentence, fix
it. If a fragment can't be spoken as a sentence, it doesn't belong on the
page.

## How pages look

The same idea, applied to layout: the page should feel like a person
walking you through something, not a control panel.

**One column.** People read top to bottom and build understanding as they
go, so put things in the order they need them. Columns force the eye to
jump around. Save side-by-side layout for actual tables.

**Complete sentences, fewer of them.** When a section feels long, cut
items, don't shorten them into labels. Six sentences someone actually
understands beat fifteen fragments they have to decode.

**A bold lead-in in front of a full sentence is welcome.** "**Today:**
the verb persists its own state. **Proposed:** it returns a plan and
the platform persists." — the reader can skim the labels and still
gets a complete sentence behind each one. The rule above is about
labels that replace the sentence. When a chat message walks through
three options, three labeled parts beat three plain paragraphs the
reader has to compare unaided.

**Decoration must carry the reading, not replace the sentence.** A
checklist, a table, and a collapsible section cover most needs. The
test for anything fancier: does the sentence still exist next to it? A
stat tile whose big number sits over a bold claim and an evidence
sentence helps the reader; a bare chip that replaces the sentence with
a code the reader has to learn does not. Layout (cards, tiles,
borders, shadows) is `visual_clarity.md`'s call, under "Decoration".

**One paragraph, one job.** A paragraph answers one question. When a
paragraph explains the goal, then the page's two views, then the label
legend, then the scope, it has become four paragraphs wearing one
coat — split it, and give each part its own bold lead-in if the parts
form a set. The tell: you added a sentence to an existing paragraph
instead of asking where that sentence's own home is. Intro paragraphs
are where this happens most, because every later edit appends one more
sentence to them.

**Bold is the focus map.** In each paragraph, bold only the few words a
scanning reader must not miss — the status, the problem, or the action.
Reading just the bold across a page should give its skeleton. If
everything is bold the map is gone. A bold lead-in heading a complete
sentence counts as part of the map.

**File references are typography, not prose.** A sentence with
`(oss-nodejs-custom/src/service/labware/labware-placement.service.ts:41)`
in the middle of it cannot be read aloud, so it fails the read-aloud
test by construction. Keep the sentence clean and set the references
apart in small mono type — at the end of the entry, in a meta line, or
as chips. Basename plus line number is enough; the full path belongs in
the linked page.

**When an entry repeats a shape, give the shape a layout.** Forty
entries that each say "what it is / what's wrong / what to change"
should not be forty undifferentiated paragraphs. Split the recurring
parts into typeset slots, each with a bold lead-in, so a scanning
reader can read just the bold labels of every entry and get the whole
page. The underlying shape is situation–complication–resolution (the
SCQA / Minto pyramid pattern): first what is, then why it can't stand,
then what to do. When an entry carries a before/after code example,
the after pane must show the named problem disappearing — a narrower
argument, removed boilerplate, a typed parameter — never the same call
with a renamed receiver; a pair with no visible difference either
needs redrawing or means the verdict was wrong. Pick the label words
from the content, not from a
fixed list — a method review reads "**Today:** … **Problem:** …
**Kit:** …", a bug report might read "**Symptom:** … **Cause:** …
**Fix:** …", a proposal "**Now:** … **Proposed:** …" — and drop the
middle slot when nothing is wrong. The labels must be the same words
across every entry on one page.

**The same voice everywhere.** If the page reads like a person and the
chat message announcing it reads like a press release, the drift has
already started. Chat counts. Everything counts.

## When we got this wrong

Keep this list growing — the examples are what keep the rules alive.

- **The diary.** The mission-control page's log carried the current
  state, so you had to read the whole history to learn where things
  stood. State now lives at the top; the log is only history.
- **The control panel.** We overcorrected into chips and three columns:
  "platform 22/23 ×2" in a pill. Shorter, and harder to understand.
  Rewritten as sentences.
- **The overcorrection the other way.** A three-option design
  walkthrough with bold lead-ins ("**Today:** … **Proposed:** …") read
  well. A tone pass flattened it into plain paragraphs, citing the
  no-labels rule, and it read worse; a reviewer asked for the structure
  back. The lead-in rule above came from this.
- **Grading words.** "Cannot be checked honestly," "fails loud,"
  "keystone." Each got replaced with the fact it was pointing at.
- **Counting instead of meaning.** "6 of 15 roadmap checkpoints done"
  with a progress bar. A count says how much, never what. The reader
  needs what the system can and cannot do now: "the platform half is
  built and passing; testing the real lab capabilities has not started."
  A count must also be able to name its members, or drop the number:
  "all seven decisions are ruled" and "both PAR tasks" both failed the
  owner because they couldn't say which seven, or which two — say what
  they are, or don't count them.
- **The squeeze grew back in the log.** After the control-panel lesson,
  new log entries quietly re-introduced it: "unit 14/14, smoke
  2/2." Log entries feel informal, so they are where
  the squeeze returns first. Rewritten as sentences; check new log
  entries for this specifically.
- **The epigram habit.** A session writing about test quality filled a
  page with X-not-Y flourishes: "proven, not asserted," "ownership, not
  first green," "a decision, not an accident," "the harness is wrong,
  not the test," "proof of life, not stability" — five in one screen.
  Each one sounds sharp and hides the plain claim behind rhetoric, and
  the shape multiplies once it appears because it feels quotable. The
  test: if two bullets on a page share the same rhetorical shape, both
  get rewritten as plain statements of what is true and what would show
  it.
- **Drift by imitation.** The mission-control page collected
  "Conformance — passing but shallow … (the tiles above carry the
  numbers)": a grading word, a fragment, and a positional reference in
  one line — because each session matched the style of the neighboring
  lines instead of this doc, and the neighbors were already wrong. Two
  rules from it: before editing a page, reread this doc, not the page;
  and when a new rule lands here, sweep the existing pages against it
  in the same pass — the epigrams the epigram rule was written about
  outlived the rule by days.
- **The layout that begs for fragments.** That same page headed each
  grade "Area — verdict." and duplicated its numbers in a tile row, so
  fragments and "see above" references were the path of least
  resistance. When a voice rule keeps breaking in one place, suspect
  the layout: a number lives once, and a grade is a sentence with its
  evidence, never a dash and a word.
- **The essay in the table cell.** A bugs-table cell grew into a
  ten-line story with a bolded decision and an upstream proposal inside
  it. The cell keeps the fact and the decision, one sentence each; the
  story moved to the log and the linked page.
- **The wall of judged entries.** A page judging 41 service methods
  rendered each judgment as one paragraph with full repo paths inline
  ("...returns the equipment/component pair, or null
  (oss-nodejs-custom/src/service/labware/labware-placement.service.ts:41).
  It does not change any state..."). Every sentence followed the rules
  one at a time, and the page was still heavy to read, because three
  recurring ideas (what it does, the problem, the change) and the
  references all shared one visual weight. The fix was structural: refs
  moved to mono chips, the decision got a bold "Kit:" lead-in, and the
  reader can now skim the bold lines alone. The two rules above came
  from this page.
- **The accreting intro.** While a page's entries were being fixed for
  readability over several rounds, its own intro paragraph grew a
  sentence per round — goal, then the view toggle, then the label
  legend, then scope, then cross-links — until it was nine lines doing
  five jobs, and nobody noticed because every fix went only where the
  complaint pointed. Two lessons: a fix applies to the whole page, not
  the quoted part (reread the page top to bottom after each change),
  and a sentence added to an existing paragraph needs the question
  "does this sentence belong in its own paragraph?" asked first. The
  one-paragraph-one-job rule above came from this.
- **The board-ID shorthand.** Status updates in chat read "W3-02 is
  re-running its gate; ORPHAN-SDK-01 rides the same suite" — every claim
  true, none of it readable without the task board open. The owner had
  to ask for context. The ID-never-travels-alone rule above came from
  this.
- **The good-night wall.** A sign-off message before an overnight run
  packed commitments, ordering, exclusions and deliverables into four
  long paragraphs — every sentence fine, the whole unreadable at
  bedtime. The reader said so. The glance rule above came from this.
- **Drift under momentum, in chat.** A session opened a chat reply with
  "the fix that matters is process, not pixels" — an X-not-Y flourish —
  minutes after re-reading a doc that bans the shape, because attention
  was on the task and the default register came back. Reading the rules
  once does not govern what gets written hours later. The working fix:
  reread your own last paragraph against the condensed rules before
  sending, every time, especially when deep in something else.

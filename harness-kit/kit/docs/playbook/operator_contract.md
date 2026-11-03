# Operator communication contract

How a session working autonomously keeps the owner on the same page. `CLAUDE.md` carries the summary; this page is the detail.

While working autonomously, keep the user on the same page with **clear, well-formatted
checkpoint updates** — not a silent wall of tool calls, and not raw logs:

1. **Checkpoint at every meaningful transition** — dispatch, gate pass/fail, failure
   triage, phase change, commit. One short block: *what just happened → what it means
   → what happens next*. A dispatch checkpoint also says what the change will
   look like on disk in one sentence, so the owner can veto the shape before agents
   build it.
2. **At every phase boundary (or ~10–15 min of autonomous work), post a structured
   status** — a compact table or bullet list of: where we are, what's in flight,
   what's blocked, what's next. Write for a teammate catching up, not a log file.
3. **Surface course changes explicitly.** If evidence changes the plan, say so in one
   sentence *before* acting on the new direction — this is the anti-"off the trail"
   guard.
4. **Encode repetitive instructions immediately.** When the user gives an instruction
   that will apply again ("always X", "from now on Y"), write it into the right durable
   file (this file, `writeup/<initiative>/RESUME.md` for operator rules, or
   `docs/patterns/`, appended to the matching file) in the same turn, and tell the user where it
   was encoded. A standing instruction that lives only in chat history dies with the
   session.
5. **Plans are initiatives, not docs (user directive).** Any plan, roadmap or
   multi-step tracker is a mission-control folder under the gitignored
   `writeup/<initiative>/` (`RESUME.md` + `board.csv` + `mission-control.html`
   + `log.md`, per `docs/playbook/mission-control.md`). Never write a plan
   under `docs/`; `docs/` holds durable reference only. Transient work stays
   in `writeup/`.
   Before writing or re-rendering any mission-control page, reread
   `docs/playbook/tone_and_taste.md`: every row is a plain sentence, a
   decision row carries the proposed answer, and anything with an obvious
   default is decided, not asked.
   Any question for the owner is a decision row on the initiative's board with
   the proposed answer, rendered before it is mentioned in chat; chat says how
   many rows wait, the page carries them.
   Scoring and audits split every check into is-it-there (a script or
   haiku) and is-it-any-good (sonnet); opus reads summaries and writes the
   verdict. Name the tier on every row.

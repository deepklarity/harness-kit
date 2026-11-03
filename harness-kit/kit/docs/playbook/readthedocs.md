# Readthedocs-style guides: a site a person can read to learn what the system does today

A system guide answers the questions a person asks when they arrive at a
part of the system cold: what exists, what works, what is half-working,
where things land on disk, what the data looks like, and which commands
move it from one stage to the next. It is a description of the present,
in the house voice (`tone_and_taste.md` in this folder), that reads
without its author in the room.

It is not a breadcrumb (those trace code paths for debugging, in
`docs/breadcrumb_analysis/`), not a mission-control page (that tracks
one initiative's open work, in `writeup/<initiative>/`), and not a
tutorial for one task (that is a plain `docs/guides/<topic>.md`). A
system guide sits above all three and links to them.

## Where things live

| Thing | Location | Why here |
|---|---|---|
| The guide (site + markdown + snapshots) | `docs/guides/<slug>/` — `<slug>_guide.html` at the root as the entry page, one folder per sidebar group (`generation/`, `data/`, `evaluation/`, `reference/`) holding one kebab-case page per entity, sub-entities one level down (`generation/document-types/<type>.html`), every page's markdown twin next to it with the same name, `README.md` as the markdown index, and `snapshot/*.json` for the data the pages were built from | Guides are committed reference, so they live with the other guides. The folder tree mirrors the sidebar, so a directory listing reads like the site map; a flat folder of 33 prefixed files did not. The entry page is named after the guide, never `index.html`, so an open tab says what it is. |
| The scripts that build it | `scripts/guides/<slug>/` at the workspace root | They read across the workspace's sibling repos (a generator, a dataset store, the app that serves them), so they belong to the workspace, not to one app repo. `scripts/guides/common.py` holds what every guide shares. |
| Screenshots, when a guide has them | `docs/guides/<slug>/img/` | Same folder as the page that shows them. Not done for any guide yet. |

When the user asks for a new guide, ask where it should go only if the
subject does not fit one folder under `docs/guides/`. Otherwise pick the
slug, say it, and build.

## The three parts of a guide

**1. Snapshot scripts.** Anything on the page that is a count, a list of
files, a field name or a flag comes from a script that reads the real
source: a config file, a Python module (import it, don't parse it), the
CLI's `--help`, or a walk over the data folders. Each script writes one
JSON file into `docs/guides/<slug>/snapshot/`. The page is rebuilt from those
files, so a stale guide is fixed by re-running the scripts, not by editing
HTML by hand.

**2. Written prose.** What a thing is for, what its limits are, what to
run and in what order, what the settled decisions are. This part is
written by a person or an agent, lives in `scripts/guides/<slug>/content.py`
(or a markdown file next to it), and is reviewed against the tone doc.
Prose never repeats a number the snapshot already carries; it points at
the table.

**3. The render script.** `build.py` reads the snapshots and the prose and
writes every page and `README.md`. The markdown twin carries the same
sections with plain tables, so the guide is readable in a git diff and
from a terminal.

## The site

A guide is a small documentation site, not a report page. The model is a
library's docs (https://docs.langchain.com/oss/python/langchain/overview):
a left sidebar that shows the whole site grouped by topic and marks the
current page, short pages, one entity per page, a right-hand "on this page"
list, previous and next links at the bottom. The first version of one
guide was a single many-screen page with a top anchor bar; a reviewer
could not find anything in it, and an audit scored it low with page
length at zero. The rules below came from rebuilding it (see the
example at the end of this file).

The look keeps the writeup shell's voice (light only, mono section labels
with the blue bar, flat bordered cards, no rounded corners) on top of
Tailwind and DaisyUI:

- **Sidebar groups by reader intent**, not by data source (example
  groups below). The front page has one door per audience ("I want to
  generate documents", "I want to know what data already exists", "I
  want to run an eval") so nobody reads the other audience's material
  to find their own.
- **One entity, one page, same layout as its siblings.** Every document
  type page has At a glance, How to generate it, Fields, Existing data.
  Every stage page has What happens, Commands, It worked when, the tools'
  own --help. A reader who has seen one knows where to look on the next.
- **No page longer than about three screens.** When a page grows past
  that, split it into pages or fold it, never scroll it.
- **Tables stay narrow; detail expands in place.** A table carries the
  columns a reader compares (name, status, counts) and nothing that wraps.
  Everything else about a row (files present, ground-truth keys, rendering
  limits, household members) expands in place under the row (`<details>`), never in a modal. Long lists get a
  filter box, click-to-sort headers and paging at 15 rows; the rows live
  in the page as JSON and a short inline script renders them.
- **Status words are coloured badges, and the word is always there.** One
  palette across the whole guide: green for works / yes / scored / accept,
  amber for limited / needs work / review / costs money, red for broken /
  reject, outlined grey for no / none / not scored, blue and teal for neutral
  kinds (from scratch, template overlay). The legend under a table shows the
  same badges, so the colours are learned once. Colour alone never carries a
  meaning.
- **Names that are enums live in one code module, with their label and
  sentence.** An Enum in the source of truth carries the plain label
  and the explaining sentence for each value. The snapshot imports it;
  the page shows the enum value as a badge with the label beside it and
  the sentence in the legend. A guide never spells such a set itself,
  because two spellings drift.
- **Column headings and cell values are plain words**, never the code's
  own names. A legend near the table repeats the meaning in a sentence
  each. A column whose heading is a code name is a guide's most common
  complaint, and the fastest one to trigger a rebuild.
- **Tailwind plus DaisyUI from a CDN** for layout and the stock parts
  (badges, selects, collapses, pager buttons). The cost is that the page
  needs a network connection to look right; the footer says so. If a
  guide must open offline, inline the writeup shell's CSS instead and
  keep the same structure.
- **Status words, never colour alone**: `works`, `limited`, `broken`,
  `no generator`. Each status word carries the sentence that explains it
  in the same row.
- **Commands are copyable blocks** with the working directory and venv
  activation on the line above them, every time, because each shell is
  fresh.

## Questions every system guide answers

Use these as section headings, in this order, dropping ones that do not
apply:

1. What exists, and in what state (works / limited / broken / missing),
   with the reason for anything that is not "works".
2. Where generated or stored things land on disk by default, and what
   decides the path.
3. What data exists right now, in what layout, and whether the layout is
   the standard one. Name the standard.
4. What is inside each item (fields, columns, keys) and how each part is
   treated downstream (scored, recorded but not scored, derived, ignored).
5. What happens next in the pipeline: stages in order, one paragraph per
   stage, the command, how to see it worked, where it breaks.
6. The decisions already made that a reader must not re-open.
7. Where to go deeper: breadcrumbs, trackers, source files.

## Auditing a guide

`docs/playbook/guide_audit_rubric.md` scores a guide on ten lines (navigation,
page length, one entity per page, long lists handled, plain words, two
doors, answer first, runnable commands, traceability, voice), each 0 to 5.
Run it before and after any restructuring, by handing the rubric and the
guide folder to a reviewer (a subagent works) that renders the pages with
headless Chrome and reads the HTML. Keep the reports in `writeup/`. A
guide rebuilt against this rubric commonly moves from a low score to a
high one; record the before-and-after on the guide's own "About this
guide" page.

## Building or refreshing a guide

```bash
cd /path/to/workspace
source <app>/.venv/bin/activate             # whatever env the build script needs
python scripts/guides/<slug>/snapshot.py    # rewrites docs/guides/<slug>/snapshot/*.json
python scripts/guides/<slug>/build.py       # rewrites index.html + README.md
open docs/guides/<slug>/<slug>_guide.html
```

Refresh when a generator, a config, a field contract or a dataset changes.
The guide's footer names the commit and date it was built from, so a
reader can tell how old it is.

## Rules that came from the first guide

- **Import the source of truth instead of describing it.** The field
  table imports the field-definitions module directly, rather than
  restating it by hand. A hand-copied field list drifted within a day
  in the README the guide replaced.
- **Say "broken" when it is broken, and say why in the same row.** A
  reader planning a dataset needs that in the table, not in a "known
  gaps" section at the bottom.
- **A dataset the guide counts must be one the pipeline can import.**
  The inventory reports whether each folder has the sidecar files that
  the standard format requires.
- **Do not put the eval numbers on a reference page.** Scores belong to
  a run record, the initiative's tracker. The guide says how to get a
  score, not what the last one was.

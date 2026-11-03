# Docs-site audit rubric for system guides

Score each line 0 to 5. 5 means a newcomer with nothing else open gets what
they need without effort; 0 means the item is absent. Report the score, the
evidence (page, section, what you saw), and the one change that would raise it.
The model to compare against is a library documentation site such as
https://docs.langchain.com/oss/python/langchain/overview: a left sidebar that
shows the whole site, short pages, one entity per page, a right-hand "on this
page" list, previous/next links.

1. **Navigation.** A persistent sidebar lists every page grouped by topic and
   marks the current page. Each page has an "on this page" list of its own
   headings. Previous and next links at the bottom.
2. **Page length.** No page needs more than about three screens of scrolling
   at 1280 wide. Anything longer is split into pages or folded.
3. **One entity, one page.** Each document type, use case, pipeline stage and
   similar thing has its own page with the same layout as its siblings, so a
   reader who has seen one knows where to look on the next.
4. **Long lists are handled.** Any list over about fifteen rows is a table
   with filter and paging, and any row with more detail than fits a cell opens
   an accordion in place rather than stretching the row; never a modal.
5. **Words a newcomer can read.** Every column heading, status word and
   filter value carries its meaning in plain words. No internal names
   (extract_fields, class, tier) without the plain-word version next to them.
6. **Two audiences, two doors.** Someone who wants to generate documents and
   someone who wants to know what data already exists reach their answers by
   different paths from the front page, without reading the other's material.
7. **Answer first.** Each page opens with the sentence that answers the page's
   question (what this is, whether it works, what to run), before any table.
8. **Commands are runnable.** Every command shows its working directory and
   venv activation, and its flags exist in the tool's real --help.
9. **Traceability.** Counts and lists say where they came from (a build stamp
   with commit and date; a footer naming the script) and nothing on the page
   is hand-edited.
10. **Voice.** docs/playbook/tone_and_taste.md holds: plain sentences, no
    grading words, no fragments in place of sentences, bold only as a focus map.

Report format: a table of the ten lines with score, evidence and fix; an
overall score out of 5 (the mean, rounded to one decimal); then the three
changes that would raise the score most, in order.

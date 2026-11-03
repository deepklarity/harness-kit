---
name: hk-chart-validation
description: "Check that the charts in a codebase tell the truth: chart type, axis baselines, aggregation, units, labels, tooltips and how they render in Chrome at each width; writes the findings as one HTML page with a score out of 10. Triggers on: 'validate the charts', 'is this chart misleading', 'chart audit', 'check the dashboard numbers', or /hk-chart-validation."
allowed-tools: Bash, Read, Grep, Glob, Write, mcp__chrome-devtools__*
generated: true
---

# Chart validation

Reads every chart's code for the decisions that can mislead (a y-axis that does not start at zero, a sum where a mean was meant, a pie for a trend) and then looks at the rendered chart in Chrome to see whether the reader can get the number out of it. Every finding shows the source lines and, where it helps, the rendered chart. It changes no source file.

Scope: charts and the numbers behind them. The rest of the screen is `hk-web-app-ui-validation`, which walks every route in Chrome at the same three widths; this skill opens only the routes with a chart. A chart with no loading or empty state is the state lens of `hk-web-app-ui-validation`. Making a chart is the `dataviz` skill; this one only judges.

The page is built as `docs/skills/knowledge/hk-writeup/SKILL.md` steps 3 to 5 say, in `docs/skills/knowledge/hk-writeup/SHELL.html`, in the voice of `docs/playbook/tone_and_taste.md`. Read the voice doc before writing a sentence.

## Does this apply?

Grep the project's source folders (skip `node_modules` and any state folder CLAUDE.md names) for `recharts`, `chart.js`, `d3`, `nivo`, `highcharts`, `plotly`, `victory`, `echarts`, or hand-drawn `<svg>` and `<canvas>` graphs. If nothing matches, say so in one chat line and stop; write no file.

## Workflow

**1. Inventory the charts.** One row per chart component: file, chart type, what it claims to show, where it renders. Done when every match from the gate has a row.

**2. Read each chart's code against the checklist** (below): the axis domain, the `dataKey`, the aggregation that produced the series, the formatter on ticks and tooltips, the container. Trace the series back to where it is computed; the wrong number usually comes from the reducer, not the chart.

**3. Look at each chart rendered.** Start the app with the workspace's dev command (CLAUDE.md) and open a record that has data. With the Chrome DevTools MCP, `mcp__chrome-devtools__navigate_page` to the route, `mcp__chrome-devtools__wait_for` the chart, and for each of 1440, 834 and 390 wide `mcp__chrome-devtools__resize_page` then `mcp__chrome-devtools__take_screenshot` with `filePath` set to `writeup/shots/hk-chart-validation/<chart>-<width>.png`; then `mcp__chrome-devtools__take_snapshot`, `mcp__chrome-devtools__hover` a data point by its uid, and screenshot the tooltip the same way. No app that starts, or no record with data: say so in one chat line and write no file. Done when every chart has three PNGs and a tooltip PNG on disk.

**4. Check one number by hand.** For each chart, pick a visible point and compute it from the source data (the API response via `mcp__chrome-devtools__get_network_request`, or the diagnostic script CLAUDE.md names for that entity). A point that does not match is a severity 5 finding.

**5. Verify, then cut.** Reopen each cited line and screenshot. Drop anything a maintainer would not change. Up to ten findings; fewer is fine, none means the page says the charts are truthful.

**6. Build the page** at `writeup/chart-validation-<subject>.html` at the repo root (`$(git rev-parse --show-toplevel)/writeup/`, gitignored; create the directory if missing), in the order under Page order below. Embed a PNG from `writeup/shots/hk-chart-validation/` as a base64 data URI where it shows the finding (`take_screenshot` with the chart's `uid` gives the crop), then delete `writeup/shots/hk-chart-validation/` and nothing else.

**7. Report back** in one chat line: where the page went, the score, and the count by severity.

## Page order

1. Kicker "Chart validation", the app as the title, chips for the chart count, the library and the model.
2. **Verdict**: one sentence with the score out of 10 and what set it (the band table in ../README.md), one naming the worst finding, then the inventory as a `.tbl` with a column for the hand-checked number and whether it matched.
3. **Findings**, one `section` per band: Wrong number or trend (severity 5 and 4), Right number, hard to get (3), Nits (2 and 1). Each finding is one `details.row`: `.row-name` is the severity in words ("severity four"), `.row-status` the title as a sentence; the `.dbody` says what the chart says and what the data says, quotes the source lines in a `pre`, shows the screenshot where it helps, and ends with `file:lines` in `.mono`.
4. `.foot`: replace the `.said` sentence with one saying how many charts were read and rendered on the named model, how many numbers were checked by hand, and that nothing was changed.

## The checklist

Per chart:

- The type fits the question: a trend over time is a line or bars, a share of a whole is a pie or stacked bar with few slices, a relationship is a scatter.
- A bar chart's value axis starts at zero; a line chart that does not says so on the axis.
- The aggregation matches the label: "average cost" is a mean, "cost" is a sum, and the grouping (per day, per model) is the one the title claims.
- Axes carry units and readable ticks at every width; the tooltip shows the value with its unit and the category it belongs to.
- Colour separates the series (no two adjacent series within a shade of each other), and the legend names every series in the order they appear.
- Missing data shows as a gap, not as zero.
- The container resizes with the viewport, and at 390 wide the chart is still readable or is replaced by a table.
- One point checked by hand matches the source data.

## Severity and score

Severity is 1 to 5, 5 worst: **5** when the hand-checked point does not match the data or the aggregation is not what the label says; **4** for a truncated baseline or a type that hides the shape; **3** when the right number is there but hard to get (missing units, unreadable ticks, a tooltip without its category); **2** for a legend out of order or a margin; **1** for palette.

Score 0 to 10 and the band for each set of findings: [../README.md](../README.md).

## Closing step

Then the closing step in [../README.md](../README.md#closing-step): board rows for severity 4 and 5, a pattern for a recurring cause, one ledger line.

## Example

- The state folders to skip are your orchestration and worktree folders.
- Name the chart library and the folder its components live in, and the route each chart renders on.
- The dev command is the one in your README; note the frontend and API ports.
- The diagnostic script for board totals is the one in your testing-tools folder that prints the aggregate the chart shows.

#!/usr/bin/env node
// Regenerate the board-driven sections of mission-control.html from board.csv.
//
// board.csv is the only place task, bug, decision, suggestion and learning
// state is written. Columns: id, kind, title, status, priority, depends_on,
// closes_when, how_proven, notes, started_at, done_at, owner_kind,
// owner_action, owner_reply (an older board without the last six renders too).
// Statuses: open, doing, blocked, owner, human-review, done, fixed, decided,
// dropped (suggestions: noted, taken; learnings: open, folded).
//
//   (stamp)     -> the "Rendered <time> by render-board.mjs from board.csv" line
//                  under the verdict, with the newest line of log.md.
//   owner,      -> "What needs you": one numbered table of every row that waits
//   human-review   on the owner (and every open decision): a kind pill (Decide,
//                  Review proof, Try it in the app, Run it yourself), the id,
//                  priority and "What you do"; chips per kind with counts; a
//                  row opens in place to the full ask and "To close it".
//   discussion.csv -> "Discussion notes", only when the file exists: one
//                  expanding row per question, chips open / decided / all.
//   task, bug,  -> "The work": one table of what is with the agent, ordered by
//   decision       priority then dependency; owner rows are not listed here;
//                  closed rows fold under "K closed rows, with their proof".
//                  Chips by group (open, done, decided, dropped), a totals line.
//   suggestion, -> one fold, "Noticed along the way", open items first and the
//   learning       closed ones in a nested fold.
//
// No modals: detail expands in place. Workspace paths become links. One inline
// script (chips, filter, copy, open the row a #hash names) keeps the page one
// offline file. "Where are we?" and "Instruments and references" are the
// agent's, outside the markers, and kept. Every render rewrites the HTML between
// <!-- BOARD:START --> and <!-- BOARD:END -->, the Rendered line, and the <head>
// (CSS) from the mission-control.html template beside this script, keeping the
// page's own <title>, so a template upgrade reaches every board on its next
// render. An unfilled {{slot}} is shown as a muted "<slot> not written yet" span
// that keeps the slot in data-slot; the next render turns it back into the token
// and again into the span, so filling it (token or span replaced by text) still
// works. A folder with board.csv and no mission-control.html gets the template
// copied in. HK_RENDER_TIME overrides the clock.
//
// Usage: node docs/harnesskit/templates/mission-control/render-board.mjs writeup/<initiative>
//        (no argument: the current folder)
import { copyFileSync, existsSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const START = "<!-- BOARD:START -->";
const END = "<!-- BOARD:END -->";
const PRIO_ORDER = /** @type {Record<string, number>} */ ({ P0: 0, P1: 1, P2: 2 });
const CLOSED = ["done", "fixed", "worked around", "decided", "dropped", "taken", "folded"];
const OWNER_STATES = ["owner", "human-review"];
const NUMBER_WORDS = [
  "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
  "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
  "nineteen", "twenty",
];
const FILTER_AT = 15; // open work rows past this get a text filter
const COLUMNS = [
  "id", "kind", "title", "status", "priority", "depends_on", "closes_when", "how_proven", "notes",
  "started_at", "done_at", "owner_kind", "owner_action", "owner_reply",
];
const OWNER_KINDS = /** @type {Record<string, string>} */ ({
  decide: "Decide",
  review: "Review proof",
  click: "Try it in the app",
  run: "Run it yourself",
});
const PILL = /** @type {Record<string, string>} */ ({ doing: "agent working", "human-review": "human review" });
/** Work-table chip groups, in chip order. */
const GROUPS = ["open", "done", "decided", "dropped"];

/** @typedef {Record<string, string> & { id: string, kind: string, title: string, status: string, priority: string, depends_on: string, closes_when: string, notes: string }} Row */

/** @param {number} n */
function inWords(n) {
  return n >= 0 && n < NUMBER_WORDS.length ? NUMBER_WORDS[n] : String(n);
}

/** @param {string} s */
function capitalize(s) {
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

/** Same escapes as Python's html.escape(s, quote=True). @param {string | undefined} s */
function esc(s) {
  return (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#x27;");
}

/**
 * Parse CSV the way Python's csv module does by default: double-quoted fields
 * may hold commas, newlines and "" for a quote; a quote inside an unquoted
 * field is literal; blank lines are skipped.
 * @param {string} text
 * @returns {string[][]}
 */
export function parseCsv(text) {
  /** @type {string[][]} */
  const records = [];
  let row = /** @type {string[]} */ ([]);
  let field = "";
  let inQuotes = false;
  let fieldStarted = false;
  const endRecord = () => {
    if (fieldStarted || row.length) {
      row.push(field);
      records.push(row);
    }
    row = [];
    field = "";
    fieldStarted = false;
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else inQuotes = false;
      } else field += c;
    } else if (c === '"' && field === "") {
      inQuotes = true;
      fieldStarted = true;
    } else if (c === ",") {
      row.push(field);
      field = "";
      fieldStarted = true;
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      endRecord();
    } else {
      field += c;
      fieldStarted = true;
    }
  }
  endRecord();
  return records;
}

/** A CSV with a header row as one object per row; short rows get empty fields. @param {string} text @returns {Record<string, string>[]} */
export function loadTable(text) {
  const [header, ...body] = parseCsv(text);
  return header ? body.map((cells) => Object.fromEntries(header.map((h, i) => [h, cells[i] ?? ""]))) : [];
}

/** @param {string} text @returns {Row[]} */
export function loadRows(text) {
  const [header, ...body] = parseCsv(text);
  if (!header) return [];
  return body.map((cells) => {
    // An unquoted comma shifts every later field; say so instead of rendering a garbled row.
    if (cells.length > header.length)
      process.stderr.write(`board.csv: row ${cells[0]} has ${cells.length} fields, expected ${header.length}; quote any field that contains a comma\n`);
    /** @type {Record<string, string>} */
    const r = {};
    for (const col of COLUMNS) r[col] = "";
    header.forEach((h, i) => {
      r[h] = cells[i] ?? "";
    });
    return /** @type {Row} */ (r);
  });
}

/** @param {Row} r */
const isClosed = (r) => CLOSED.includes(r.status);

/** A row the owner has to act on: status owner or human-review, or any open decision. @param {Row} r */
const waitsOnOwner = (r) => !isClosed(r) && ["task", "bug", "decision"].includes(r.kind) && (OWNER_STATES.includes(r.status) || (r.kind === "decision" && r.status !== "doing"));

/** depends_on is a ';' list of row ids. @param {Row} r */
const depIds = (r) => r.depends_on.split(";").map((d) => d.trim()).filter(Boolean);

/** Dependencies of r that are not closed (an unknown id counts as open). @param {Row} r @param {Map<string, Row>} byId */
const openDeps = (r, byId) => depIds(r).filter((d) => !byId.has(d) || !isClosed(/** @type {Row} */ (byId.get(d))));

/** Id plus the whole title. @param {string} id @param {Map<string, Row>} byId */
function label(id, byId) {
  const title = byId.get(id)?.title ?? "";
  return title ? `${id} ${title}` : id;
}

/** Status word: "blocked" while a dependency is open, else the row's own status. @param {Row} r @param {Map<string, Row>} byId */
const statusWord = (r, byId) => (!isClosed(r) && r.status !== "doing" && openDeps(r, byId).length ? "blocked" : r.status || "open");

/**
 * Open rows: priority, then dependency order (depth among open dependencies),
 * then board order.
 * @param {Row[]} open @param {Map<string, Row>} byId
 */
function ordered(open, byId) {
  /** @type {Map<string, number>} */
  const depth = new Map();
  /** @param {Row} r @param {Set<string>} seen @returns {number} */
  const depthOf = (r, seen) => {
    const known = depth.get(r.id);
    if (known !== undefined) return known;
    if (seen.has(r.id)) return 0;
    seen.add(r.id);
    let d = 0;
    for (const id of openDeps(r, byId)) {
      const dep = byId.get(id);
      if (dep) d = Math.max(d, 1 + depthOf(dep, seen));
    }
    depth.set(r.id, d);
    return d;
  };
  return open
    .map((r, i) => ({ r, i, d: depthOf(r, new Set()) }))
    .sort((a, b) => (PRIO_ORDER[a.r.priority] ?? 9) - (PRIO_ORDER[b.r.priority] ?? 9) || a.d - b.d || a.i - b.i)
    .map((x) => x.r);
}

/**
 * Turn workspace paths in already-escaped text into links. A path has a slash
 * and ends in a file extension or a slash; the page sits in writeup/<initiative>/,
 * so the link climbs two folders to the workspace root.
 * @param {string} escaped
 */
function linkPaths(escaped) {
  return escaped.replace(/(^|[\s(])((?:[\w.-]+\/)+(?:[\w-]+\.[A-Za-z]\w*)?)(?=$|[\s),;:]|\.(?:\s|$))/g, (m, pre, path) =>
    path.endsWith("/") || /\.[A-Za-z]\w*$/.test(path) ? `${pre}<a href="../../${path}">${path}</a>` : m,
  );
}

/** @param {string} s */
const prose = (s) => linkPaths(esc(s));

/** @param {string} s */
const sentence = (s) => s.charAt(0).toUpperCase() + s.slice(1);

/** @param {number} n @param {string} one */
const plural = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;

/**
 * Split notes at the answer a label introduces: the text after "Proposed:" (or
 * "Decided:") up to the end of its sentence or line. Without the label the answer
 * is the notes' first sentence. rest is everything else, for the Why fold.
 * A '.' ends a sentence only before whitespace, so paths and versions stay whole.
 * @param {string} notes @param {string} label
 */
function splitAnswer(notes, label) {
  const m = notes.match(new RegExp(`${label}(?: answer)?:\\s*`, "i"));
  // No label means no answer was written: a reason must never pose as one.
  if (!m) return { answer: "", rest: notes.trim() };
  const tail = notes.slice(m.index + m[0].length);
  const end = tail.search(/[.!?](?=\s|$)|\n/);
  const cut = end < 0 ? tail.length : tail[end] === "\n" ? end : end + 1;
  const before = notes.slice(0, m.index);
  return { answer: tail.slice(0, cut).trim(), rest: (before + " " + tail.slice(cut)).trim() };
}

/** @param {string} summary @param {string} inner @param {string} [cls] */
const fold = (summary, inner, cls = "fold") => `<details class="${cls}"><summary>${summary}</summary>${inner}</details>`;

/** @param {string} status */
const pill = (status) => `<span class="pill ${esc(status.replace(/\s+/g, "-"))}">${esc(PILL[status] ?? status)}</span>`;

/** First sentence of a text, for a table cell. @param {string} text */
const firstSentence = (text) => {
  const m = text.trim().match(/^([^]+?[.!?])(\s|$)/);
  return m ? m[1] : text.trim();
};

/** Text with **bold** marks, escaped and path-linked. @param {string} s */
const inline = (s) => prose(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

/** "after T-1, T-2": the open dependencies as links, or "". @param {Row} r @param {Map<string, Row>} byId */
function after(r, byId) {
  const deps = isClosed(r) ? [] : openDeps(r, byId);
  return deps.length ? `<div class="muted">after ${deps.map((d) => `<a href="#${esc(d)}">${esc(d)}</a>`).join(", ")}</div>` : "";
}

/**
 * The labelled blocks a row opens to: closes-when, how it is proven, what it
 * waits on by full title, notes.
 * @param {Row} r @param {Map<string, Row>} byId @param {string} [notes] @param {boolean} [skipCloses] already shown as the ask
 */
function detailBlocks(r, byId, notes = r.notes, skipCloses = false) {
  const wait = isClosed(r) ? [] : openDeps(r, byId);
  const block = (/** @type {string} */ name, /** @type {string} */ text) => (text.trim() ? `<p class="dl">${name}</p><p>${prose(text.trim())}</p>` : "");
  return (
    (skipCloses ? "" : block("Closed when", r.closes_when)) +
    block("Proven by", r.how_proven) +
    (wait.length ? `<p class="dl">Waits on</p><ul>${wait.map((d) => `<li><a href="#${esc(d)}">${esc(label(d, byId))}</a></li>`).join("")}</ul>` : "") +
    block("Notes", notes)
  );
}

/** A row's expanding pair: the visible line and the detail under it. @param {string} attrs @param {string} main @param {number} span @param {string} more */
const item = (attrs, main, span, more) =>
  `<tbody class="item" ${attrs}><tr class="main">${main}</tr><tr class="more"><td></td><td colspan="${span - 1}">${more}</td></tr></tbody>`;

/** Chips: "all N" first, then one per group; scope names which rows they filter. @param {string} scope @param {[string, string][]} chips @param {string} on */
const chipBar = (scope, chips, on) =>
  '<div class="filters">' +
  chips.map(([v, text]) => `<button type="button" data-scope="${scope}" data-v="${esc(v)}"${v === on ? ' class="on"' : ""}>${text}</button>`).join("") +
  "</div>";

/** Rows waiting on the owner as one numbered table with kind chips. @param {Row[]} waiting @param {Map<string, Row>} byId @param {Row[]} rows */
function ownerTable(waiting, byId, rows) {
  // owner_kind may be the code (decide) or its label (Decide, Review proof), in any case.
  const kindOf = (/** @type {Row} */ r) => {
    const k = (r.owner_kind || "").trim().toLowerCase();
    const code = Object.keys(OWNER_KINDS).find((c) => c === k || OWNER_KINDS[c].toLowerCase() === k);
    return code ?? (k || (r.kind === "decision" ? "decide" : "review"));
  };
  const order = Object.keys(OWNER_KINDS);
  const rank = (/** @type {Row} */ r) => (order.indexOf(kindOf(r)) < 0 ? order.length : order.indexOf(kindOf(r)));
  const sorted = waiting.map((r, i) => ({ r, i })).sort((a, b) => rank(a.r) - rank(b.r) || (PRIO_ORDER[a.r.priority] ?? 9) - (PRIO_ORDER[b.r.priority] ?? 9) || a.i - b.i).map((x) => x.r);
  const kindWord = (/** @type {string} */ k) => OWNER_KINDS[k] ?? sentence(k);
  const kindClass = (/** @type {string} */ k) => (OWNER_KINDS[k] ? k : "review");
  const kinds = [...new Set(sorted.map(kindOf))];
  const chips = /** @type {[string, string][]} */ ([["", `all ${sorted.length}`]]);
  for (const k of kinds) chips.push([k, `<span class="kind ${kindClass(k)}">${esc(kindWord(k))}</span> ${sorted.filter((r) => kindOf(r) === k).length}`]);
  const body = sorted.map((r, n) => {
    const k = kindOf(r);
    const proposal = r.kind === "decision" ? splitAnswer(r.notes, "Proposed") : { answer: "", rest: r.notes };
    const action = r.owner_action || (proposal.answer ? `Take or change the proposed answer: ${sentence(proposal.answer)}` : r.closes_when) || r.title;
    const reply =
      r.owner_reply ||
      (r.kind === "decision" ? `Reply "${r.id} yes" to take the proposed answer, or "${r.id}: your answer".` : `Reply "${r.id} done", or reopen it with the reason.`);
    const held = rows.filter((x) => !isClosed(x) && depIds(x).includes(r.id));
    const copy = r.kind === "decision" && proposal.answer ? ` <button type="button" class="copy" data-copy="${esc(r.id)} yes">Copy ${esc(r.id)} yes</button>` : "";
    return item(
      `id="${esc(r.id)}" data-owner="${esc(k)}"`,
      `<td class="num-cell">${n + 1}</td><td><span class="kind ${kindClass(k)}">${esc(kindWord(k))}</span></td><td class="id">${esc(r.id)}</td><td>${esc(r.priority)}</td><td>${inline(firstSentence(action))}</td><td class="chev">▸</td>`,
      6,
      `<p class="ask-title">${esc(r.title)}</p><p><strong>Do:</strong> ${inline(action)}</p><p><strong>To close it:</strong> ${inline(reply)}${copy}</p>` +
        detailBlocks(r, byId, proposal.rest, r.kind === "decision" || action === r.closes_when) +
        (held.length ? `<p class="dl">Holds up</p><ul>${held.map((x) => `<li><a href="#${esc(x.id)}">${esc(label(x.id, byId))}</a></li>`).join("")}</ul>` : ""),
    );
  });
  return (
    chipBar("owner", chips, "") +
    '<p class="muted">Click a row for the full ask and the reply that closes it.</p>' +
    '<table class="asks"><thead><tr><th>#</th><th>Action</th><th>Task</th><th>Priority</th><th>What you do</th><th></th></tr></thead>' +
    body.join("") +
    "</table>"
  );
}

/** The work table for a set of rows. @param {Row[]} rs @param {Map<string, Row>} byId */
function workTable(rs, byId) {
  const body = rs.map((r) => {
    const word = statusWord(r, byId);
    const decided = r.kind === "decision" && r.status === "decided" ? splitAnswer(r.notes, "Decided") : null;
    const closes = decided?.answer ? `Decided: ${sentence(decided.answer)}` : firstSentence(r.closes_when);
    return item(
      `id="${esc(r.id)}" data-work="${groupOf(r)}"`,
      `<td class="id">${esc(r.id)}</td><td>${esc(r.priority)}</td><td class="c-kind">${esc(r.kind)}</td><td>${esc(r.title)}</td>` +
        `<td>${inline(closes)}${after(r, byId)}</td><td>${pill(word)}</td><td class="c-time">${esc(r.started_at)}</td><td class="c-time">${esc(r.done_at)}</td>` +
        '<td><button type="button" class="detail" aria-expanded="false">detail</button></td>',
      9,
      detailBlocks(r, byId, decided ? decided.rest : r.notes) || "<p>No detail recorded.</p>",
    );
  });
  return (
    '<table class="work"><thead><tr><th>Task</th><th>Priority</th><th class="c-kind">Kind</th><th>What</th><th>Closed when</th><th>Status</th><th class="c-time">Started</th><th class="c-time">Done</th><th></th></tr></thead>' +
    body.join("") +
    "</table>"
  );
}

/** Chip group of a work row. @param {Row} r */
const groupOf = (r) => (["done", "fixed", "worked around"].includes(r.status) ? "done" : r.status === "decided" ? "decided" : r.status === "dropped" ? "dropped" : "open");

/** One expanding row per discussion note; null-safe for missing columns. @param {Record<string, string>[]} notes */
function discussionSection(notes) {
  const status = (/** @type {Record<string, string>} */ n) => (n.status || "open").trim();
  const counts = new Map();
  for (const n of notes) counts.set(status(n), (counts.get(status(n)) ?? 0) + 1);
  const names = [...counts.keys()].sort((a, b) => Number(b === "open") - Number(a === "open") || Number(b === "decided") - Number(a === "decided"));
  const on = counts.has("open") ? "open" : "";
  const chips = /** @type {[string, string][]} */ ([...names.map((k) => /** @type {[string, string]} */ ([k, `${esc(k)} ${counts.get(k)}`])), ["", `all ${notes.length}`]]);
  const body = notes.map((n) => {
    const opts = (n.options || "")
      .split("||")
      .filter((o) => o.trim())
      .map((o) => `<tr>${[0, 1, 2].map((i) => `<td>${esc((o.split("::")[i] ?? "").trim())}</td>`).join("")}</tr>`)
      .join("");
    const block = (/** @type {string} */ name, /** @type {string} */ html) => (html ? `<p class="dl">${name}</p>${html}` : "");
    return item(
      `data-note="${esc(status(n))}"${on && status(n) !== on ? " hidden" : ""}`,
      `<td class="id">${esc(n.id)}</td><td>${esc(n.question)}</td><td>${pill(status(n))}</td><td class="chev">▸</td>`,
      4,
      block("What happens today", n.today ? `<p>${inline(n.today)}</p>` : "") +
        block("Options", opts ? `<table class="opts"><thead><tr><th>Option</th><th>What it means</th><th>Cost or risk</th></tr></thead><tbody>${opts}</tbody></table>` : "") +
        block("My recommendation", n.recommend ? `<p>${inline(n.recommend)}</p>` : "") +
        ((n.raised || n.code) ? `<p class="muted">${n.raised ? `Raised ${esc(n.raised)}.` : ""}${n.code ? ` In the code: ${esc(n.code)}` : ""}</p>` : ""),
    );
  });
  return (
    '<section id="discussion"><p class="label">Discussion notes</p>' +
    chipBar("note", chips, on) +
    '<p class="muted">Questions to take to other stakeholders. Click a row for what happens today, the options, and my recommendation.</p>' +
    '<table class="asks"><thead><tr><th>#</th><th>Question</th><th>Status</th><th></th></tr></thead>' +
    body.join("") +
    "</table></section>"
  );
}

/** @param {Row} r */
const noteLine = (r) => `<li>${esc(r.title)}${isClosed(r) ? ` <span class="st">${esc(r.status)}</span>` : ""}${r.notes ? ` <span class="file">${prose(r.notes)}</span>` : ""}</li>`;
/** @param {Row[]} rows */
const list = (rows) => `<ul class="plain">${rows.map(noteLine).join("")}</ul>`;

/** Local ISO datetime (no zone), or HK_RENDER_TIME when set. */
function renderTime() {
  if (process.env.HK_RENDER_TIME) return process.env.HK_RENDER_TIME;
  const d = new Date();
  const p = (/** @type {number} */ n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

/** The newest "- " line of log.md, or "". @param {string} log */
export function lastLogLine(log) {
  const lines = log.split("\n").filter((l) => /^\s*[-*] /.test(l));
  return lines.length ? lines[lines.length - 1].replace(/^\s*[-*] /, "").trim() : "";
}

/** Chips, filters, copy buttons, expanding rows, and opening the row a #hash names. Inline so the page works offline. */
const SCRIPT =
  "<script>" +
  "(function(){var $=function(s){return Array.prototype.slice.call(document.querySelectorAll(s))};" +
  "function toggle(t,on){var o=on===undefined?!t.classList.contains('open'):on;t.classList.toggle('open',o);var b=t.querySelector('button.detail');if(b)b.setAttribute('aria-expanded',o)}" +
  "$('tbody.item>tr.main').forEach(function(tr){tr.onclick=function(e){if(e.target.closest('a'))return;toggle(tr.parentElement)}});" +
  "$('button.copy').forEach(function(b){b.onclick=function(){var t=b.getAttribute('data-copy'),l=b.textContent;" +
  "function done(){b.textContent='Copied';setTimeout(function(){b.textContent=l},1500)}" +
  "function old(){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove();done()}" +
  "if(navigator.clipboard){navigator.clipboard.writeText(t).then(done,old)}else old()}});" +
  "var v={owner:'',work:'',note:''},q='';" +
  "function show(s){$('tbody.item[data-'+s+']').forEach(function(t){var h=!!v[s]&&t.dataset[s]!==v[s];" +
  "if(s==='work'&&q&&t.textContent.toLowerCase().indexOf(q)<0)h=true;t.hidden=h;if(h)toggle(t,false)})}" +
  "$('.filters button').forEach(function(b){if(b.classList.contains('on'))v[b.dataset.scope]=b.dataset.v;b.onclick=function(){var s=b.dataset.scope;v[s]=b.dataset.v;" +
  "$('.filters button[data-scope=\"'+s+'\"]').forEach(function(x){x.classList.toggle('on',x===b)});show(s);" +
  "if(s==='work'&&v[s])$('#work details').forEach(function(d){d.open=true})}});" +
  "var f=document.getElementById('filter');if(f)f.oninput=function(){q=f.value.toLowerCase();show('work');if(q)$('#work details').forEach(function(d){d.open=true})};" +
  "function reveal(){var e=location.hash&&document.getElementById(location.hash.slice(1));if(!e)return;for(var p=e;p;p=p.parentElement){if(p.tagName==='DETAILS')p.open=true}" +
  "if(e.classList.contains('item')){e.hidden=false;toggle(e,true)}e.scrollIntoView({block:'center'})}" +
  "addEventListener('hashchange',reveal);reveal()})();" +
  "</script>";

/** @param {Row[]} rows @param { { log?: string, discussion?: Record<string, string>[] } } [opts] */
export function render(rows, opts = {}) {
  const byId = new Map(rows.map((r) => [r.id, r]));
  const out = [];

  const work = rows.filter((r) => ["task", "bug", "decision"].includes(r.kind));
  const waiting = work.filter(waitsOnOwner);
  const agentRows = ordered(work.filter((r) => !isClosed(r) && !waitsOnOwner(r)), byId);
  const closedRows = work.filter(isClosed);

  out.push("<!-- Generated from board.csv; refresh with: node docs/harnesskit/templates/mission-control/render-board.mjs writeup/<initiative> -->");

  // --- what needs you: every row that waits on the owner, one table ---
  out.push('<section id="owner"><p class="label">What needs you</p>');
  out.push(waiting.length ? ownerTable(waiting, byId, rows) : '<p class="muted">Nothing needs you right now.</p>');
  out.push("</section>");

  // --- discussion notes: only when discussion.csv exists ---
  if (opts.discussion) out.push(discussionSection(opts.discussion));

  // --- the work: what is with the agent, closed rows folded ---
  out.push('<section id="work"><p class="label">The work</p>');
  const all = agentRows.length + closedRows.length;
  if (all) {
    const counts = new Map();
    for (const r of [...agentRows, ...closedRows]) counts.set(groupOf(r), (counts.get(groupOf(r)) ?? 0) + 1);
    out.push(chipBar("work", [["", `all ${all}`], ...GROUPS.filter((g) => counts.has(g)).map((g) => /** @type {[string, string]} */ ([g, `${g} ${counts.get(g)}`]))], ""));
  }
  out.push(`<p class="muted">${agentRows.length} with the agent, ${waiting.length} waiting on you (listed above, not here), ${closedRows.length} closed</p>`);
  if (agentRows.length > FILTER_AT) out.push('<p class="filter"><input type="search" id="filter" placeholder="Filter by words or id" aria-label="Filter the work"></p>');
  if (agentRows.length) out.push(workTable(agentRows, byId));
  else if (all) out.push('<p class="muted">Nothing with the agent right now.</p>');
  if (closedRows.length) out.push(fold(`${plural(closedRows.length, "closed row")}, with their proof`, workTable(closedRows, byId)));
  out.push("</section>");

  // --- suggestions and learnings: one fold, open items first, closed ones nested ---
  const of = (/** @type {string} */ k) => rows.filter((r) => r.kind === k);
  const sugg = of("suggestion");
  const learn = of("learning");
  if (sugg.length || learn.length) {
    const kinds = /** @type {[string, Row[]][]} */ ([["Suggestions for the repo owners", sugg], ["Learnings from this deployment", learn]]);
    const openOf = (/** @type {Row[]} */ rs) => rs.filter((r) => !isClosed(r));
    const closedAll = [...sugg, ...learn].filter(isClosed);
    const counts = [
      openOf(sugg).length ? `${inWords(openOf(sugg).length)} ${openOf(sugg).length === 1 ? "suggestion" : "suggestions"} for the repo owners` : "",
      openOf(learn).length ? `${inWords(openOf(learn).length)} ${openOf(learn).length === 1 ? "learning" : "learnings"}` : "",
    ].filter(Boolean);
    const groups = (/** @type {(rs: Row[]) => Row[]} */ pick) =>
      kinds.map(([name, rs]) => (pick(rs).length ? `<p class="sub">${name}</p>${list(pick(rs))}` : "")).join("");
    out.push(
      '<section id="noticed">' +
        fold(
          `Noticed along the way: ${counts.join(", ") || "all acted on"}`,
          groups(openOf) + (closedAll.length ? fold(`Already acted on (${closedAll.length})`, groups((rs) => rs.filter(isClosed))) : ""),
        ) +
        "</section>",
    );
  }

  return [...out, SCRIPT].join("\n");
}

/** The Rendered line: when, by what, from what, and the newest log line. @param {string} log */
function stampLine(log) {
  const t = renderTime();
  const last = lastLogLine(log);
  return `<p class="muted" id="updated">Rendered <time datetime="${esc(t)}">${esc(t.replace("T", " ").slice(0, 16))}</time> by render-board.mjs from board.csv${last ? ` · Last: ${prose(last.length > 140 ? last.slice(0, 139).trimEnd() + "…" : last)}` : ""}</p>`;
}

/** Put the Rendered line in the page's top matter: replace the old one, else add it after the verdict or the h1. @param {string} head @param {string} stamp */
function withStamp(head, stamp) {
  if (/<p class="muted" id="updated">[^]*?<\/p>/.test(head)) return head.replace(/<p class="muted" id="updated">[^]*?<\/p>/, () => stamp);
  return head.replace(/(<p class="verdict">[^]*?<\/p>|<\/h1>)/, (m) => `${m}\n${stamp}`);
}

/** The page with its <head> swapped for the template's, keeping the page's own <title>. @param {string} page */
function withTemplateHead(page) {
  const templatePath = join(dirname(fileURLToPath(import.meta.url)), "mission-control.html");
  if (!existsSync(templatePath)) return page;
  const headOf = (/** @type {string} */ h) => h.match(/<head>[^]*?<\/head>/)?.[0];
  const fresh = headOf(readFileSync(templatePath, "utf8"));
  const old = headOf(page);
  if (!fresh || !old) return page;
  const title = old.match(/<title>[^]*?<\/title>/)?.[0];
  return page.replace(old, () => (title ? fresh.replace(/<title>[^]*?<\/title>/, () => title) : fresh));
}

/**
 * Show each unfilled {{slot}} outside HTML comments as a "<slot> not written yet"
 * span. Spans from an earlier render go back to tokens first, so a render is
 * idempotent and a token typed back in is recognised. @param {string} page
 */
function showUnfilled(page) {
  return page
    .split(/(<!--[^]*?-->)/)
    .map((part) =>
      part.startsWith("<!--")
        ? part
        : part
            .replace(/<span class="unfilled" data-slot="([\w.-]+)">[^<]*<\/span>/g, "{{$1}}")
            .replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (_, name) => `<span class="unfilled" data-slot="${name}">${name.replace(/[_.-]+/g, " ")} not written yet</span>`),
    )
    .join("");
}

function main() {
  const dir = resolve(process.argv[2] ?? process.cwd());
  const pagePath = join(dir, "mission-control.html");
  const created = !existsSync(pagePath);
  if (created) copyFileSync(join(dirname(fileURLToPath(import.meta.url)), "mission-control.html"), pagePath);
  const page = withTemplateHead(readFileSync(pagePath, "utf8"));
  if (!page.includes(START) || !page.includes(END)) {
    process.stderr.write("markers missing in mission-control.html\n");
    process.exit(1);
  }
  const rows = loadRows(readFileSync(join(dir, "board.csv"), "utf8"));
  const discPath = join(dir, "discussion.csv");
  const discussion = existsSync(discPath) ? loadTable(readFileSync(discPath, "utf8")) : undefined;
  const logPath = join(dir, "log.md");
  const log = existsSync(logPath) ? readFileSync(logPath, "utf8") : "";
  const head = page.slice(0, page.indexOf(START));
  const tail = page.slice(page.indexOf(END, page.indexOf(START)) + END.length);
  writeFileSync(pagePath, showUnfilled(withStamp(head, stampLine(log))) + START + "\n" + render(rows, { discussion }) + "\n" + END + showUnfilled(tail));
  console.log(created ? "created mission-control.html from the template and regenerated it from board.csv" : "mission-control.html regenerated from board.csv");
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();

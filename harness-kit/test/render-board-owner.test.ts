import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const SCRIPT = resolve("kit/templates/mission-control/render-board.mjs");
const TEMPLATE = resolve("kit/templates/mission-control/mission-control.html");
const STAMP = "2026-01-02T03:04:05";
const HEAD14 = "id,kind,title,status,priority,depends_on,closes_when,how_proven,notes,started_at,done_at,owner_kind,owner_action,owner_reply\n";
const HEAD8 = "id,kind,title,status,priority,depends_on,closes_when,notes\n";

/** Render a board (and optionally a discussion.csv) in a fresh folder; the page starts from the template. */
function renderBoard(csv: string, discussion?: string): string {
  const dir = mkdtempSync(join(tmpdir(), "render-owner-"));
  writeFileSync(join(dir, "board.csv"), csv);
  if (discussion !== undefined) writeFileSync(join(dir, "discussion.csv"), discussion);
  execFileSync("node", [SCRIPT, dir], { env: { ...process.env, HK_RENDER_TIME: STAMP } });
  return readFileSync(join(dir, "mission-control.html"), "utf8");
}

const section = (html: string, id: string) => {
  const i = html.indexOf(`<section id="${id}"`);
  if (i < 0) return "";
  const s = html.slice(i);
  return s.slice(0, s.indexOf("</section>"));
};

const BOARD =
  HEAD14 +
  "T-1,task,Pick the retry policy,owner,P1,,Owner picks one,,Because of cost,,,decide,Pick the retry policy: three tries or five.,Reply \"T-1 three\" or \"T-1 five\".\n" +
  "T-2,task,Proof page for sign-in,human-review,P0,,Page reads cold,Open proof/signin.html,,2026-01-01 09:00,2026-01-02 01:00,review,Open proof/signin.html and read the first screen.,\n" +
  "T-3,task,Click the new export,owner,P2,,export works,,,,,click,,\n" +
  "T-4,task,Run the importer,owner,P2,,rows land,,,,,run,Run the importer on your laptop.,Reply \"T-4 ok\".\n" +
  "T-5,task,Wire the gate,doing,P0,,gate runs,,,2026-01-01 10:00,,,,\n" +
  "T-6,task,Write the guide,open,P1,T-5,guide exists,,,,,,,\n" +
  "T-7,task,Old thing,done,P1,,shipped,tests by name,,2026-01-01 08:00,2026-01-01 09:00,,,\n" +
  "T-8,task,Cut idea,dropped,P2,,,,,,,,,\n" +
  "D-1,decision,Name the package,decided,P1,,,,Decided: node only.,,,,,\n";

describe("owner rows", () => {
  it("lists rows with status owner or human-review under What needs you and never in The work", () => {
    const html = renderBoard(BOARD);
    const owner = section(html, "owner");
    const work = section(html, "work");
    for (const id of ["T-1", "T-2", "T-3", "T-4"]) {
      expect(owner, id).toContain(`>${id}<`);
      expect(work, id).not.toContain(`>${id}<`);
    }
    for (const id of ["T-5", "T-6", "T-7", "T-8", "D-1"]) {
      expect(work, id).toContain(`>${id}<`);
      expect(owner, id).not.toContain(`>${id}<`);
    }
  });

  it("numbers the asks and shows the action kind as a pill with its word", () => {
    const owner = section(renderBoard(BOARD), "owner");
    expect(owner).toMatch(/<th>#<\/th><th>Action<\/th><th>Task<\/th><th>Priority<\/th><th>What you do<\/th>/);
    for (const [cls, word] of [["decide", "Decide"], ["review", "Review proof"], ["click", "Try it in the app"], ["run", "Run it yourself"]]) {
      expect(owner).toContain(`<td><span class="kind ${cls}">${word}</span></td>`);
    }
    expect(owner.match(/<td class="num-cell">(\d)<\/td>/g)).toEqual(['<td class="num-cell">1</td>', '<td class="num-cell">2</td>', '<td class="num-cell">3</td>', '<td class="num-cell">4</td>']);
  });

  it("orders asks by kind (decide first) then priority", () => {
    const owner = section(renderBoard(BOARD), "owner");
    const ids = [...owner.matchAll(/<td class="id">([^<]+)<\/td>/g)].map((m) => m[1]);
    expect(ids).toEqual(["T-1", "T-2", "T-3", "T-4"]);
  });

  it("filter chips say all N then one chip per kind with its count", () => {
    const owner = section(renderBoard(BOARD), "owner");
    const chips = [...owner.matchAll(/<button type="button" data-scope="owner" data-v="([^"]*)"[^>]*>([^]*?)<\/button>/g)].map((m) => [m[1], m[2].replace(/<[^>]*>/g, "")]);
    expect(chips).toEqual([["", "all 4"], ["decide", "Decide 1"], ["review", "Review proof 1"], ["click", "Try it in the app 1"], ["run", "Run it yourself 1"]]);
  });

  it("What you do comes from owner_action, else closes_when; To close it from owner_reply", () => {
    const owner = section(renderBoard(BOARD), "owner");
    expect(owner).toContain("Pick the retry policy: three tries or five.");
    expect(owner).toContain("<strong>To close it:</strong> Reply &quot;T-1 three&quot; or &quot;T-1 five&quot;.");
    expect(owner).toContain("export works");
    expect(owner).toContain("<strong>To close it:</strong> Reply &quot;T-3 done&quot;");
  });

  it("an open decision row waits on the owner and defaults to Decide, with the proposed answer", () => {
    const owner = section(renderBoard(HEAD14 + "D-2,decision,Which name?,open,P0,,owner answers,,Proposed: use acme. Cheaper.,,,,,\n"), "owner");
    expect(owner).toContain('<span class="kind decide">Decide</span>');
    expect(owner).toContain("Use acme.");
    expect(owner).toContain('data-copy="D-2 yes"');
  });

  it("says nothing needs you when no row waits", () => {
    expect(section(renderBoard(HEAD14 + "T-1,task,A,open,P0,,x,,,,,,,\n"), "owner")).toContain("Nothing needs you");
  });
});

describe("the work table", () => {
  it("states the totals: with the agent, waiting on you (listed above, not here), closed", () => {
    const work = section(renderBoard(BOARD), "work");
    expect(work).toContain("2 with the agent, 4 waiting on you (listed above, not here), 3 closed");
  });

  it("has one row per task with the columns of the reference table and the after-deps hint", () => {
    const work = section(renderBoard(BOARD), "work");
    expect(work).toMatch(/<th>Task<\/th><th>Priority<\/th><th class="c-kind">Kind<\/th><th>What<\/th><th>Closed when<\/th><th>Status<\/th><th class="c-time">Started<\/th><th class="c-time">Done<\/th>/);
    const t6 = work.slice(work.indexOf('id="T-6"'));
    expect(t6.slice(0, t6.indexOf("</tbody>"))).toContain('after <a href="#T-5">T-5</a>');
    expect(work).toContain('<span class="pill doing">agent working</span>');
    expect(work).toContain("2026-01-01 10:00");
  });

  it("folds closed rows under 'K closed rows, with their proof'", () => {
    const work = section(renderBoard(BOARD), "work");
    expect(work).toContain("<summary>3 closed rows, with their proof</summary>");
    expect(work.indexOf("Old thing")).toBeGreaterThan(work.indexOf("3 closed rows"));
    expect(work.indexOf("Write the guide")).toBeLessThan(work.indexOf("3 closed rows"));
  });

  it("filter chips: all N, then open, done, decided, dropped with counts", () => {
    const work = section(renderBoard(BOARD), "work");
    const chips = [...work.matchAll(/<button type="button" data-scope="work" data-v="([^"]*)"[^>]*>([^<]*)<\/button>/g)].map((m) => [m[1], m[2]]);
    expect(chips).toEqual([["", "all 5"], ["open", "open 2"], ["done", "done 1"], ["decided", "decided 1"], ["dropped", "dropped 1"]]);
  });

  it("a row's detail opens in place and holds closes-when and how it is proven", () => {
    const work = section(renderBoard(BOARD), "work");
    const t7 = work.slice(work.indexOf('id="T-7"'));
    const body = t7.slice(0, t7.indexOf("</tbody>"));
    expect(body).toContain('<button type="button" class="detail" aria-expanded="false">detail</button>');
    expect(body).toContain("tests by name");
  });
});

describe("discussion notes", () => {
  const DISC =
    "id,raised,status,question,today,options,recommend,code\n" +
    'Q-1,owner,open,Should stray files be read?,"No, they are parked.",Keep :: Only asked files :: Misses data||Always :: Read all :: Costs money,Keep as is: cheaper.,placement.ts\n' +
    "Q-2,owner,decided,Which cap?,Fifty.,Fifty :: Cap :: None,Fifty.,\n";

  it("renders a section with chips open, decided, all and one expanding row per question when discussion.csv exists", () => {
    const html = renderBoard(HEAD14, DISC);
    const s = section(html, "discussion");
    expect(s).toContain("Discussion notes");
    const chips = [...s.matchAll(/<button type="button" data-scope="note" data-v="([^"]*)"[^>]*>([^<]*)<\/button>/g)].map((m) => [m[1], m[2]]);
    expect(chips).toEqual([["open", "open 1"], ["decided", "decided 1"], ["", "all 2"]]);
    expect(s).toContain("Should stray files be read?");
    expect(s).toContain("What happens today");
    expect(s).toContain("<td>Only asked files</td>");
    expect(s).toContain("My recommendation");
    expect(html.indexOf('id="owner"')).toBeLessThan(html.indexOf('id="discussion"'));
    expect(html.indexOf('id="discussion"')).toBeLessThan(html.indexOf('id="work"'));
  });

  it("is omitted when there is no discussion.csv", () => {
    expect(renderBoard(HEAD14)).not.toContain("Discussion notes");
  });
});

describe("older boards", () => {
  it("an 8-column board still renders: missing columns are empty, dependencies and decisions keep working", () => {
    const html = renderBoard(
      HEAD8 + "D1,decision,Pick,open,P0,,owner answers,Proposed: yes. Because.\nT1,task,First,open,P0,,x,n\nT2,task,Second,open,P1,T1,y,n\nT3,task,Done one,done,P1,,z,n\n",
    );
    expect(section(html, "owner")).toContain('<span class="kind decide">Decide</span>');
    const work = section(html, "work");
    expect(work).toContain("2 with the agent, 1 waiting on you (listed above, not here), 1 closed");
    expect(work).toContain('after <a href="#T1">T1</a>');
    expect(work).not.toContain("undefined");
  });
});

describe("the look", () => {
  const tpl = readFileSync(TEMPLATE, "utf8");
  it("is always light: pinned theme, no dark palette", () => {
    expect(tpl).toContain('<html lang="en" data-theme="light">');
    expect(tpl).not.toMatch(/prefers-color-scheme|data-theme="dark"/);
  });
  it("puts the sections in order: where are we, needs you, work, references", () => {
    const html = renderBoard(BOARD);
    const order = ["Where are we?", "What needs you", "The work", "Instruments and references"].map((t) => html.indexOf(`<p class="label">${t}`));
    expect(order.every((i) => i > 0)).toBe(true);
    expect([...order].sort((a, b) => a - b)).toEqual(order);
  });
  it("opens with the title, a verdict line and the rendered stamp", () => {
    const html = renderBoard(BOARD).replace(/<!--[^]*?-->/g, "");
    expect(html).toMatch(/<h1>[^]*<\/h1>\s*<p class="verdict">[^]*<\/p>\s*<p class="muted" id="updated">Rendered <time datetime="2026-01-02T03:04:05">2026-01-02 03:04<\/time> by render-board\.mjs from board\.csv/);
  });
});

describe("owner kind spelling", () => {
  it("treats a code and its label, in any case, as the same kind", () => {
    const html = renderBoard(HEAD14 +
      "T1,task,A,owner,P0,,x,,,,,Decide,Pick one,yes\n" +
      "T2,task,B,owner,P0,,x,,,,,review proof,Open it,ok\n" +
      "T3,task,C,owner,P0,,x,,,,,decide,Pick two,yes\n");
    expect(html).toContain('<span class="kind decide">Decide</span> 2');
    expect(html).toContain('<span class="kind review">Review proof</span> 1');
    expect(html).not.toContain('<span class="kind review">Decide</span>');
  });
});
